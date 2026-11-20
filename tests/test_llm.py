import json

import httpx
import pytest

from promofinder.llm import (
    LLMError,
    LLMRouter,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderResponse,
)
from promofinder.schemas import EpisodeDiscovery


class FakeProvider:
    def __init__(self, response=None, error=None):
        self.response = response or '{"opportunities": []}'
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return ProviderResponse(self.response, {"total_tokens": 1})


def test_fallback_cache_and_invalidation(config, tmp_path):
    primary = FakeProvider(error=ProviderError("Missing key"))
    fallback = FakeProvider()
    router = LLMRouter(config, tmp_path, {"groq": primary, "gemini": fallback})
    first = router.call("candidate_discovery", "prompt v1", "source", EpisodeDiscovery)
    second = router.call("candidate_discovery", "prompt v1", "source", EpisodeDiscovery)
    assert first == second
    assert len(fallback.calls) == 1
    router.call("candidate_discovery", "prompt v1", "changed source", EpisodeDiscovery)
    assert len(fallback.calls) == 2
    router.call("candidate_discovery", "prompt v2", "changed source", EpisodeDiscovery)
    assert len(fallback.calls) == 3
    records = list((tmp_path / "candidate_discovery").glob("*.json"))
    assert json.loads(records[0].read_text())["request"]["provider"] == "gemini"


def test_invalid_output_gets_bounded_repair_then_fallback(config, tmp_path):
    primary, fallback = FakeProvider("not JSON"), FakeProvider()
    router = LLMRouter(config, tmp_path, {"groq": primary, "gemini": fallback})
    router.call("candidate_discovery", "prompt", "source", EpisodeDiscovery)
    assert len(primary.calls) == 2
    assert "Previous response was invalid" in primary.calls[-1]["user"]
    assert len(fallback.calls) == 1


def test_semantic_validation_falls_back(config, tmp_path):
    providers = {name: FakeProvider() for name in config.providers}
    router = LLMRouter(config, tmp_path, providers)

    def invalid(_):
        raise ValueError("Invented reference")

    with pytest.raises(LLMError):
        router.call("candidate_discovery", "prompt", "source", EpisodeDiscovery, invalid)
    assert all(len(p.calls) == 2 for p in providers.values())
    assert not list(tmp_path.glob("candidate_discovery/*.json"))


def test_http_adapter_structured_output_and_auth(config, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def respond(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["model"] == "openai/gpt-oss-120b"
        assert body["response_format"]["json_schema"]["strict"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"opportunities":[]}'}}
                ],
                "usage": {"total_tokens": 4},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAICompatibleProvider(config.providers["groq"], client)
        result = provider.generate(
            system="system",
            user="user",
            schema=EpisodeDiscovery.model_json_schema(),
            settings=config.llm,
        )
    assert result.usage["total_tokens"] == 4


@pytest.mark.parametrize(("status", "retryable"), [(401, False), (429, True), (503, True)])
def test_http_failure_classification(config, monkeypatch, status, retryable):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        provider = OpenAICompatibleProvider(config.providers["groq"], client)
        with pytest.raises(ProviderError) as error:
            provider.generate(system="s", user="u", schema={"title": "test"}, settings=config.llm)
    assert error.value.retryable == retryable


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"choices": []},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    ],
)
def test_malformed_or_truncated_http_response_is_retryable(config, monkeypatch, body):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=json.dumps(body)))
    ) as client:
        provider = OpenAICompatibleProvider(config.providers["groq"], client)
        with pytest.raises(ProviderError) as error:
            provider.generate(system="s", user="u", schema={"title": "test"}, settings=config.llm)
    assert error.value.retryable
