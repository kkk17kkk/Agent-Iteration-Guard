from pathlib import Path

import pytest

from agentguard.domain import ProviderBinding
from agentguard.integrations.native_command import NativeCommandProfile, NativeCommandRunner
from agentguard.integrations.native_http import NativeHttpProcessRunner, NativeHttpProjectProfile
from agentguard.sut_provider import SutProviderConfigurationError, SutProviderEnvironment
from agentguard.targets import TargetInfrastructureError


def binding(**changes: object) -> ProviderBinding:
    payload: dict[str, object] = {
        "project_id": "project-stage7",
        "role": "sut_native",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "expected_environment_variable": "DEEPSEEK_API_KEY",
        "credential_source_ref": "user:.env",
        "batch_budget_usd": 0.5,
        "timeout_seconds": 30,
        "allowed_hosts": ["api.deepseek.com"],
        "data_retention_policy": "user-approved",
    }
    payload.update(changes)
    return ProviderBinding.model_validate(payload)


def test_sut_binding_is_resolved_only_at_runtime_into_target_declared_names() -> None:
    mapping = SutProviderEnvironment(
        api_key_variable="OPENROUTER_API_KEY",
        base_url_variable="OPENROUTER_BASE_URL",
        model_variable="OPENROUTER_MODEL",
        model_alias_variables=("OPENROUTER_RECIPE_MODEL",),
        additional_environment={"TARGET_LLM_REQUIRED": "1"},
    )

    environment = mapping.resolve(binding(), credential_reader=lambda _: "runtime-only-secret")

    assert environment == {
        "OPENROUTER_API_KEY": "runtime-only-secret",
        "OPENROUTER_BASE_URL": "https://api.deepseek.com/v1",
        "OPENROUTER_MODEL": "deepseek-chat",
        "OPENROUTER_RECIPE_MODEL": "deepseek-chat",
        "TARGET_LLM_REQUIRED": "1",
    }
    assert "runtime-only-secret" not in repr(mapping)


@pytest.mark.parametrize(
    "changed, message",
    [
        ({"role": "control_plane"}, "sut_native"),
        ({"allowed_hosts": ["other.example"]}, "allowed_hosts"),
    ],
)
def test_sut_binding_rejects_wrong_scope_or_host(changed: dict[str, object], message: str) -> None:
    mapping = SutProviderEnvironment("TARGET_KEY", "TARGET_MODEL", "TARGET_BASE_URL")

    with pytest.raises(SutProviderConfigurationError, match=message):
        mapping.resolve(binding(**changed), credential_reader=lambda _: "runtime-only-secret")


def test_sut_binding_rejects_missing_runtime_credential_and_unsupported_target_provider() -> None:
    mapping = SutProviderEnvironment(
        "TARGET_KEY", "TARGET_MODEL", "TARGET_BASE_URL", "TARGET_PROVIDER", {"deepseek": "openai"}
    )
    with pytest.raises(SutProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        mapping.resolve(binding(), credential_reader=lambda _: None)
    with pytest.raises(SutProviderConfigurationError, match="openai"):
        mapping.resolve(
            binding(provider="openai", base_url="https://api.openai.com/v1", allowed_hosts=["api.openai.com"]),
            credential_reader=lambda _: "runtime-only-secret",
        )


def test_native_http_runner_clears_then_allows_approved_target_injection() -> None:
    profile = NativeHttpProjectProfile(
        profile_id="test", application="main:app", readiness_path="/ready", required_source_files=(),
        cleared_secret_environment=("TARGET_KEY",),
    )
    runner = NativeHttpProcessRunner(Path("python"), profile)

    environment = runner.environment(Path("source"), Path("state.db"), environment_overrides={"TARGET_KEY": "runtime-only-secret"})

    assert environment["TARGET_KEY"] == "runtime-only-secret"
    with pytest.raises(TargetInfrastructureError, match="infrastructure"):
        runner.environment(Path("source"), Path("state.db"), environment_overrides={"PATH": "unsafe"})


def test_native_command_runner_has_the_same_injection_boundary() -> None:
    profile = NativeCommandProfile(
        profile_id="test", command_template=("{python}", "-c", "print(1)"), required_source_files=(),
        cleared_secret_environment=("TARGET_KEY",),
    )
    runner = NativeCommandRunner(Path("python"), profile)

    environment = runner.environment(Path("source"), Path("state.db"), environment_overrides={"TARGET_KEY": "runtime-only-secret"})

    assert environment["TARGET_KEY"] == "runtime-only-secret"
