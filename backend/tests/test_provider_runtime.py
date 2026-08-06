import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentguard.domain import ProviderBinding
from agentguard.provider_runtime import ProviderRuntimeError, ProviderToolCall, build_control_plane_client


def binding(provider: str, base_url: str) -> ProviderBinding:
    return ProviderBinding(
        project_id="project", role="control_plane", provider=provider, base_url=base_url,
        model="test-model", expected_environment_variable="TEST_API_KEY", credential_source_ref="runtime:test",
        batch_budget_usd=0.05, timeout_seconds=10, allowed_hosts=["localhost", "api.deepseek.com", "api.openai.com"],
        data_retention_policy="non-secret metadata only", input_price_per_million_usd=0.1,
        output_price_per_million_usd=0.2, pricing_source="test", pricing_verified_at="2026-08-01T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("provider", "base_url", "expects_thinking"),
    [
        ("deepseek", "https://api.deepseek.com", True),
        ("openai", "https://api.openai.com/v1", False),
        ("vllm", "http://localhost:8001/v1", False),
    ],
)
def test_control_plane_client_switches_openai_compatible_backends_without_fallback(
    monkeypatch, provider: str, base_url: str, expects_thinking: bool
) -> None:
    observed: dict[str, object] = {}

    @contextmanager
    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["payload"] = json.loads(request.data)
        observed["timeout"] = timeout
        class Response:
            def read(self):
                return json.dumps({
                    "id": "request-1", "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
                        "id": "call-1", "function": {"name": "read", "arguments": "{}"},
                    }]}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }).encode("utf-8")
        yield Response()

    monkeypatch.setattr("agentguard.provider_runtime.urlopen", fake_urlopen)
    turn = build_control_plane_client(binding(provider, base_url), "secret-at-runtime").complete(
        [{"role": "user", "content": "inspect"}],
        [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
    )
    assert observed["url"] == f"{base_url}/chat/completions"
    assert ("thinking" in observed["payload"]) is expects_thinking
    assert turn.request_id == "request-1"
    assert turn.tool_calls == (ProviderToolCall("call-1", "read", {}),)


def test_provider_binding_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="provider"):
        binding("unknown", "https://example.com")


def test_provider_templates_validate_after_cli_scope_injection() -> None:
    template_dir = Path(__file__).parents[2] / "examples" / "provider-bindings"
    for path in template_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        ProviderBinding.model_validate({"project_id": "project", **payload})


def test_control_plane_invalid_tool_arguments_preserve_nonsecret_usage(monkeypatch) -> None:
    @contextmanager
    def fake_urlopen(request, timeout):
        class Response:
            def read(self):
                return json.dumps({
                    "id": "request-parse-error",
                    "choices": [{"message": {"tool_calls": [{"id": "call-1", "function": {"name": "read", "arguments": "{\"cut\":"}}]}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 3}},
                }).encode("utf-8")
        yield Response()

    monkeypatch.setattr("agentguard.provider_runtime.urlopen", fake_urlopen)
    with pytest.raises(ProviderRuntimeError) as raised:
        build_control_plane_client(binding("deepseek", "https://api.deepseek.com"), "secret-at-runtime").complete(
            [{"role": "user", "content": "inspect"}],
            [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        )

    assert raised.value.request_id == "request-parse-error"
    assert (raised.value.input_tokens, raised.value.output_tokens, raised.value.cache_hit_tokens) == (11, 7, 3)


def test_control_plane_accepts_provider_python_literal_tool_arguments(monkeypatch) -> None:
    @contextmanager
    def fake_urlopen(request, timeout):
        class Response:
            def read(self):
                return json.dumps({
                    "id": "request-literal",
                    "choices": [{"message": {"tool_calls": [{
                        "id": "call-1", "function": {"name": "read", "arguments": "{'value': True}"},
                    }]}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }).encode("utf-8")
        yield Response()

    monkeypatch.setattr("agentguard.provider_runtime.urlopen", fake_urlopen)
    turn = build_control_plane_client(binding("deepseek", "https://api.deepseek.com"), "secret-at-runtime").complete(
        [{"role": "user", "content": "inspect"}],
        [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
    )

    assert turn.tool_calls[0].arguments == {"value": True}


def test_control_plane_closes_only_unterminated_json_tool_arguments(monkeypatch) -> None:
    @contextmanager
    def fake_urlopen(request, timeout):
        class Response:
            def read(self):
                return json.dumps({
                    "id": "request-truncated",
                    "choices": [{"message": {"tool_calls": [{
                        "id": "call-1", "function": {"name": "read", "arguments": '{"value": [1, 2'},
                    }]}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }).encode("utf-8")
        yield Response()

    monkeypatch.setattr("agentguard.provider_runtime.urlopen", fake_urlopen)
    turn = build_control_plane_client(binding("deepseek", "https://api.deepseek.com"), "secret-at-runtime").complete(
        [{"role": "user", "content": "inspect"}],
        [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
    )

    assert turn.tool_calls[0].arguments == {"value": [1, 2]}
