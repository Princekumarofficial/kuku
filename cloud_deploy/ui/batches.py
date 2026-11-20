"""Durable Anthropic batches; request bodies and IDs persist, credentials never do."""

import hashlib
import json
import time

from promofinder.llm import LLMError, ProviderError
from ui.providers import DashboardProvider


class DeferredBatch(BaseException):
    """Internal collection signal, deliberately outside provider retry handling."""


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    for attempt in range(5):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1)


class BatchProvider(DashboardProvider):
    def __init__(
        self, model, key, directory, notify=lambda state: None, client=None, sleep=time.sleep
    ):
        super().__init__("Anthropic", model, key, client)
        self.directory, self.notify, self.sleep = directory, notify, sleep
        self.collecting = False
        self.pending = {}

    def message(self, body, timeout):
        request_id = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        for path in self.directory.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            result = record.get("results", {}).get(request_id)
            if result:
                if result["type"] != "succeeded":
                    raise ProviderError(
                        f"Batch request {result['type']}; no automatic paid resubmission"
                    )
                return result["message"]
        self.pending[request_id] = body
        if self.collecting:
            raise DeferredBatch()
        self.flush()
        for path in self.directory.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if request_id in record["requests"]:
                result = record.get("results", {}).get(request_id)
                if not result:
                    raise ProviderError("Batch result missing; reconnect to the saved job")
                if result["type"] != "succeeded":
                    raise ProviderError(
                        f"Batch request {result['type']}; no automatic paid resubmission"
                    )
                return result["message"]
        raise ProviderError("Batch request was not recorded")

    def flush(self):
        if not self.pending:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        records = []
        covered = set()
        for path in self.directory.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if self.pending.keys() & record["requests"].keys():
                records.append((path, record))
                covered.update(record["requests"])
        fresh = {key: body for key, body in self.pending.items() if key not in covered}
        if fresh:
            digest = hashlib.sha256("".join(sorted(fresh)).encode()).hexdigest()
            path = self.directory / f"{digest}.json"
            record = {"requests": fresh, "state": "submitting"}
            # Save intent first. An ambiguous POST failure must never silently submit twice.
            save(path, record)
            try:
                batch = self._request(
                    "POST",
                    "/messages/batches",
                    {
                        "requests": [
                            {"custom_id": key, "params": body} for key, body in fresh.items()
                        ]
                    },
                )
                record.update(id=batch["id"], state=batch["processing_status"])
                save(path, record)
            except (ProviderError, KeyError, TypeError):
                raise ProviderError(
                    "Batch submission unconfirmed. Check Anthropic Console before retrying; saved intent prevents duplicate charges."
                ) from None
            records.append((path, record))
        for path, record in records:
            if "id" not in record:
                raise ProviderError(
                    "Unconfirmed batch submission: reconcile its ID from Anthropic Console before resuming."
                )
            if "results" in record:
                continue
            while True:
                try:
                    batch = self._request("GET", f"/messages/batches/{record['id']}")
                except ProviderError:
                    raise ProviderError(
                        "Batch status unavailable. Reconnect to this saved job to continue polling without resubmitting."
                    ) from None
                record.update(
                    state=batch["processing_status"], counts=batch.get("request_counts", {})
                )
                save(path, record)
                self.notify(
                    {
                        "state": "waiting_for_batch",
                        "batch_id": record["id"],
                        "batch_status": record["state"],
                        "counts": record["counts"],
                    }
                )
                if record["state"] == "ended":
                    break
                self.sleep(30)
            # Use a fixed Anthropic endpoint, never forward credentials to a returned URL.
            results = self._request("GET", f"/messages/batches/{record['id']}/results", jsonl=True)
            record["results"] = {row["custom_id"]: row["result"] for row in results}
            save(path, record)
        self.pending.clear()


def prepare_tasks(providers, tasks):
    """Collect independent requests, submit once per agent, then run normal validation."""
    batched = [p for p in providers.values() if isinstance(p, BatchProvider)]
    if not batched:
        return
    # Initial submission, then one grouped repair round. Cached valid results
    # are saved during collection before waiting for any repairs.
    for _ in range(2):
        for provider in batched:
            provider.collecting = True
        try:
            for task in tasks:
                try:
                    task()
                except (DeferredBatch, LLMError):
                    pass
        finally:
            for provider in batched:
                provider.collecting = False
        for provider in batched:
            provider.flush()
