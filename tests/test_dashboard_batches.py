import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from promofinder.config import LLMConfig  # noqa: E402
from promofinder.llm import ProviderError  # noqa: E402
from ui.batches import BatchProvider, prepare_tasks  # noqa: E402


def call(provider, text):
    return provider.generate(
        system="s", user=text, schema={"title": "Example", "type": "object"}, settings=LLMConfig()
    )


def test_grouping_polling_matching_and_restart(tmp_path):
    submitted, polls, states = [], [], []

    def respond(request):
        assert request.headers["x-api-key"] == "private-key"
        if request.method == "POST":
            assert request.url.path == "/v1/messages/batches"
            submitted.append(json.loads(request.content)["requests"])
            return httpx.Response(
                200, json={"id": "msgbatch_test", "processing_status": "in_progress"}
            )
        if request.url.path.endswith("/results"):
            rows = [
                {
                    "custom_id": r["custom_id"],
                    "result": {
                        "type": "succeeded",
                        "message": {
                            "stop_reason": "end_turn",
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        {"text": r["params"]["messages"][0]["content"]}
                                    ),
                                }
                            ],
                            "usage": {"input_tokens": 10},
                        },
                    },
                }
                for r in reversed(submitted[0])
            ]
            return httpx.Response(200, text="\n".join(json.dumps(r) for r in rows))
        polls.append(1)
        return httpx.Response(
            200,
            json={
                "processing_status": "in_progress" if len(polls) == 1 else "ended",
                "request_counts": {"succeeded": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = BatchProvider(
            "haiku", "private-key", tmp_path, states.append, client, sleep=lambda _: None
        )
        tasks = [lambda: call(provider, "episode1"), lambda: call(provider, "episode2")]
        prepare_tasks({"episode_analysis": provider}, tasks)
        assert len(submitted) == 1 and len(submitted[0]) == 2
        assert [json.loads(task().content)["text"] for task in tasks] == ["episode1", "episode2"]
        restored = BatchProvider("haiku", "private-key", tmp_path, client=client)
        assert json.loads(call(restored, "episode2").content)["text"] == "episode2"
        assert len(submitted) == 1
        assert states[-1]["batch_status"] == "ended"
    assert "private-key" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


def test_ambiguous_submission_never_resubmits(tmp_path):
    posts = []

    def respond(request):
        posts.append(request)
        raise httpx.ReadTimeout("secret", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        for _ in range(2):
            provider = BatchProvider("haiku", "key", tmp_path, client=client)
            with pytest.raises(ProviderError, match="[Uu]nconfirmed"):
                call(provider, "episode")
    assert len(posts) == 1


@pytest.mark.parametrize("outcome", ["errored", "canceled", "expired"])
def test_failed_requests_do_not_fall_back_or_resubmit(tmp_path, outcome):
    posts = []

    def respond(request):
        if request.method == "POST":
            posts.extend(json.loads(request.content)["requests"])
            return httpx.Response(200, json={"id": "batch", "processing_status": "in_progress"})
        if request.url.path.endswith("/results"):
            return httpx.Response(
                200,
                text=json.dumps({"custom_id": posts[0]["custom_id"], "result": {"type": outcome}}),
            )
        return httpx.Response(200, json={"processing_status": "ended"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = BatchProvider("haiku", "key", tmp_path, client=client)
        for _ in range(2):
            with pytest.raises(ProviderError, match=outcome):
                call(provider, "episode")
        assert len(posts) == 1


def test_resume_existing_batch_after_poll_failure(tmp_path):
    requests, fail = [], True

    def respond(request):
        nonlocal fail
        if request.method == "POST":
            requests.extend(json.loads(request.content)["requests"])
            return httpx.Response(200, json={"id": "batch", "processing_status": "in_progress"})
        if fail:
            fail = False
            return httpx.Response(503)
        if request.url.path.endswith("/results"):
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "custom_id": requests[0]["custom_id"],
                        "result": {
                            "type": "succeeded",
                            "message": {
                                "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "{}"}],
                            },
                        },
                    }
                ),
            )
        return httpx.Response(200, json={"processing_status": "ended"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="Reconnect"):
            call(BatchProvider("haiku", "key", tmp_path, client=client), "episode")
        assert (
            call(BatchProvider("haiku", "key", tmp_path, client=client), "episode").content == "{}"
        )
        assert len(requests) == 1


def test_pipeline_batches_independent_episodes_before_dependent_stages(config, tmp_path):
    from conftest import SCRIPT
    from test_pipeline import ScriptedProvider

    from promofinder.llm import LLMRouter
    from promofinder.pipeline import PromoPipeline
    from ui.providers import build_config, preset

    routes = preset("Haiku recommended")
    config = build_config(config, routes, {stage: "key" for stage in routes})
    fake = ScriptedProvider()
    submissions, batches = [], {}

    def respond(request):
        if request.method == "POST":
            rows = json.loads(request.content)["requests"]
            batch_id = f"batch_{len(submissions)}"
            submissions.append(rows)
            batches[batch_id] = rows
            return httpx.Response(200, json={"id": batch_id, "processing_status": "in_progress"})
        batch_id = request.url.path.split("/")[4]
        if request.url.path.endswith("/results"):
            results = []
            for row in batches[batch_id]:
                params = row["params"]
                output = fake.generate(
                    system=params["system"],
                    user=params["messages"][0]["content"],
                    schema=params["output_config"]["format"]["schema"],
                    settings=config.llm,
                )
                results.append(
                    {
                        "custom_id": row["custom_id"],
                        "result": {
                            "type": "succeeded",
                            "message": {
                                "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": output.content}],
                            },
                        },
                    }
                )
            return httpx.Response(200, text="\n".join(json.dumps(r) for r in results))
        return httpx.Response(200, json={"processing_status": "ended"})

    class Router(LLMRouter):
        def prepare(self, stage, tasks):
            if isinstance(self.providers.get(stage), BatchProvider):
                prepare_tasks(self.providers, tasks)

    data = tmp_path / "data"
    data.mkdir()
    for number in (1, 2):
        (data / f"{number}.txt").write_text(SCRIPT.format(number=number), encoding="utf-8")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        providers = {stage: fake for stage in routes}
        for stage in (
            "episode_analysis",
            "candidate_discovery",
            "blind_evaluation",
            "spoiler_analysis",
        ):
            providers[stage] = BatchProvider(
                "haiku", "key", tmp_path / "batches" / stage, client=client
            )
        router = Router(config, tmp_path / "cache", providers)
        run = PromoPipeline(config, tmp_path / "runs", router).run(data)
        assert (run / "outputs" / "episode_001_promos.json").exists()
        assert [len(batch) for batch in submissions[:2]] == [2, 2]
        assert len(submissions) == 4
        assert len(submissions[2]) == len(submissions[3]) > 0
        assert (
            submissions[0][0]["params"]["output_config"]["format"]["schema"]["title"]
            == "EpisodeAnalysis"
        )
        assert (
            submissions[1][0]["params"]["output_config"]["format"]["schema"]["title"]
            == "EpisodeDiscovery"
        )
        PromoPipeline(config, tmp_path / "runs", router).run(data)
        assert len(submissions) == 4
