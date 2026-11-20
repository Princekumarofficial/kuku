import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from promofinder.config import Config, LLMConfig  # noqa: E402
from promofinder.llm import ProviderError  # noqa: E402
from ui.providers import DashboardProvider, build_config, preset  # noqa: E402


def test_dashboard_keys_required_even_with_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "local-key-must-not-be-used")
    routes = preset("Haiku recommended")
    with pytest.raises(ValueError, match="dashboard API key"):
        build_config(Config.load(Path("config.toml")), routes, {})
    keys = {stage: "dashboard-secret" for stage in routes}
    config = build_config(Config.load(Path("config.toml")), routes, keys)
    assert "dashboard-secret" not in config.model_dump_json()
    assert all(len(route) == 1 for route in config.routes.values())


def test_anthropic_native_transport_and_constraints():
    schema = {
        "title": "Rating",
        "type": "object",
        "properties": {"score": {"type": "number", "minimum": 0, "maximum": 10}},
        "required": ["score"],
        "additionalProperties": False,
    }

    def respond(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "dashboard-secret"
        body = json.loads(request.content)
        assert body["model"] == "claude-haiku-5-5"
        assert body["system"] == "instructions"
        assert body["messages"] == [{"role": "user", "content": "source"}]
        assert "minimum" not in body["output_config"]["format"]["schema"]["properties"]["score"]
        return httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"score": 8}'}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = DashboardProvider(
            "Anthropic", "claude-haiku-5-5", "dashboard-secret", client
        ).generate(system="instructions", user="source", schema=schema, settings=LLMConfig())
    assert json.loads(result.content)["score"] == 8
    assert schema["properties"]["score"]["minimum"] == 0


@pytest.mark.parametrize("provider", ["Groq", "Gemini"])
def test_explicit_keys_for_compatible_providers(provider):
    def respond(request):
        assert request.headers["authorization"] == "Bearer stage-specific"
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert (
            DashboardProvider(provider, "model", "stage-specific", client)
            .generate(system="s", user="u", schema={"title": "Test"}, settings=LLMConfig())
            .content
            == "{}"
        )


def test_errors_do_not_expose_credentials():
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret-key"))
    ) as client:
        with pytest.raises(ProviderError, match="HTTP 401") as exc:
            DashboardProvider("Anthropic", "model", "secret-key", client).check_key()
        assert "secret-key" not in str(exc.value)


def test_settings_session_keys_and_per_agent_override():
    st_test = pytest.importorskip("streamlit.testing.v1")
    app = st_test.AppTest.from_file(str(Path(__file__).resolve().parents[1] / "ui/app.py")).run(
        timeout=60
    )
    assert not app.exception
    app.text_input(key="shared_key_Anthropic").set_value("test-only-key").run()
    app.checkbox(key="use_shared_blind_evaluation").uncheck().run()
    app.text_input(key="key_blind_evaluation").set_value("different-test-key").run()
    assert app.session_state["shared_key_Anthropic"] == "test-only-key"
    assert app.session_state["key_blind_evaluation"] == "different-test-key"
    app.selectbox(key="agent_preset").select("Free-tier").run()
    assert app.session_state["provider_blind_evaluation"] == "Gemini"
    assert app.session_state["key_blind_evaluation"] == ""
    app.button(key="clear_keys").click().run()
    assert app.session_state["shared_key_Anthropic"] == ""
    assert not app.exception
