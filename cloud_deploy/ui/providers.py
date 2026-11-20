"""Dashboard providers: credentials are explicit and never read from the environment."""

import json

import httpx

from promofinder.config import STAGES, Config, ProviderConfig
from promofinder.llm import ProviderError, ProviderResponse

CATALOG = {
    "Anthropic": {"url": "https://api.anthropic.com/v1", "model": "claude-haiku-5-5"},
    "Groq": {"url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"},
    "Gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
    },
}
LABELS = dict(
    zip(
        STAGES,
        [
            "Episode analyst",
            "Series analyst",
            "Opportunity discovery",
            "Blind quality judge",
            "Spoiler judge",
        ],
    )
)


def preset(name):
    result = {}
    for stage in STAGES:
        provider = "Anthropic"
        if name == "Free-tier" or (
            name == "Mixed" and stage in ("episode_analysis", "candidate_discovery")
        ):
            provider = "Gemini" if stage in ("series_analysis", "blind_evaluation") else "Groq"
        result[stage] = {"provider": provider, "model": CATALOG[provider]["model"]}
    return result


def build_config(base, routes, keys, require_keys=True):
    if set(routes) != set(STAGES):
        raise ValueError("Configure all five agents in dashboard settings.")
    providers, routing = {}, {}
    for stage in STAGES:
        row = routes[stage]
        if row.get("batch") and (
            row["provider"] != "Anthropic"
            or "haiku" not in row["model"].lower()
            or stage
            not in (
                "episode_analysis",
                "candidate_discovery",
                "blind_evaluation",
                "spoiler_analysis",
            )
        ):
            raise ValueError(
                "Batch mode supports Haiku analysis, discovery, blind judging, and spoiler judging."
            )
        if row["provider"] not in CATALOG or not row["model"].strip():
            raise ValueError(f"Invalid provider or model for {LABELS[stage]}.")
        if (
            require_keys
            and not (stage == "spoiler_analysis" and not row.get("enabled", True))
            and not keys.get(stage, "").strip()
        ):
            raise ValueError(f"Enter a dashboard API key for {LABELS[stage]}.")
        providers[stage] = ProviderConfig(
            base_url=CATALOG[row["provider"]]["url"],
            model=row["model"].strip(),
            api_key_env="DASHBOARD_ONLY",
            structured_output="json_schema",
        )
        routing[stage] = [stage]
    pipeline = base.pipeline.model_copy(
        update={"check_spoilers": routes["spoiler_analysis"].get("enabled", True)}
    )
    return Config(providers=providers, routes=routing, llm=base.llm, pipeline=pipeline)


def anthropic_schema(schema):
    result = json.loads(json.dumps(schema))

    def visit(node):
        if isinstance(node, dict):
            # These bounds remain enforced by local Pydantic validation.
            for k in (
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "minItems",
                "maxItems",
                "minLength",
                "maxLength",
            ):
                node.pop(k, None)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(result)
    return result


class DashboardProvider:
    def __init__(self, provider, model, key, client=None):
        self.provider, self.model, self._key, self.client = provider, model, key, client
        if provider not in CATALOG:
            raise ValueError("Unsupported provider")

    def _request(self, method, path, body=None, timeout=120, jsonl=False):
        if not self._key.strip():
            raise ProviderError("Missing dashboard API key")
        headers = (
            {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
            if self.provider == "Anthropic"
            else {"Authorization": f"Bearer {self._key}"}
        )
        try:
            if self.client:
                response = self.client.request(
                    method,
                    CATALOG[self.provider]["url"] + path,
                    headers=headers,
                    json=body,
                    timeout=timeout,
                )
            else:
                with httpx.Client(timeout=timeout) as client:
                    response = client.request(
                        method, CATALOG[self.provider]["url"] + path, headers=headers, json=body
                    )
            if response.is_error:
                raise ProviderError(
                    f"HTTP {response.status_code}",
                    response.status_code == 429 or response.status_code >= 500,
                )
            return (
                [json.loads(line) for line in response.text.splitlines() if line.strip()]
                if jsonl
                else response.json()
            )
        except httpx.TransportError as exc:
            raise ProviderError(type(exc).__name__, True) from None
        except ValueError:
            raise ProviderError("Invalid provider response", True) from None

    def check_key(self):
        self._request("GET", "/models", timeout=30)

    def generate(self, *, system, user, schema, settings):
        body = {"model": self.model, "max_tokens": settings.max_tokens}
        if self.provider == "Anthropic":
            body.update(
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "format": {"type": "json_schema", "schema": anthropic_schema(schema)}
                },
            )
            data = self.message(body, settings.timeout_seconds)
            try:
                if data["stop_reason"] != "end_turn":
                    raise ProviderError("Incomplete or refused completion", True)
                content = "".join(b["text"] for b in data["content"] if b["type"] == "text")
            except (KeyError, TypeError):
                raise ProviderError("Malformed provider response", True) from None
        else:
            body.update(
                temperature=settings.temperature,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema["title"], "strict": True, "schema": schema},
                },
            )
            data = self._request("POST", "/chat/completions", body, settings.timeout_seconds)
            try:
                choice = data["choices"][0]
                if choice["finish_reason"] != "stop":
                    raise ProviderError("Incomplete or refused completion", True)
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise ProviderError("Malformed provider response", True) from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Empty completion", True)
        return ProviderResponse(content, data.get("usage", {}))

    def message(self, body, timeout):
        return self._request("POST", "/messages", body, timeout)
