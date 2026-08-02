from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from .domain import ProviderBinding


class SutProviderConfigurationError(RuntimeError):
    """A target-native provider cannot be started under its approved binding."""


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SutProviderEnvironment:
    """Target-declared names for a transient native-provider configuration.

    The returned mapping is deliberately process-local. Callers must pass it only
    to a child process or in-memory target adapter and must not persist or log it.
    """

    api_key_variable: str
    model_variable: str
    base_url_variable: str | None = None
    provider_variable: str | None = None
    provider_values: dict[str, str] = field(default_factory=dict)
    model_alias_variables: tuple[str, ...] = ()
    additional_environment: dict[str, str] = field(default_factory=dict)

    def resolve(
        self,
        binding: ProviderBinding,
        *,
        credential_reader: Callable[[str], str | None] = os.getenv,
    ) -> dict[str, str]:
        if binding.role != "sut_native":
            raise SutProviderConfigurationError("Target-native execution requires a sut_native ProviderBinding.")
        self._validate_binding_host(binding)
        api_key = credential_reader(binding.expected_environment_variable)
        if not api_key:
            raise SutProviderConfigurationError(
                f"{binding.expected_environment_variable} is required at runtime for target-native execution."
            )
        environment = {
            self._name(self.api_key_variable): api_key,
            self._name(self.model_variable): binding.model,
        }
        for name in self.model_alias_variables:
            environment[self._name(name)] = binding.model
        if self.base_url_variable:
            if not binding.base_url:
                raise SutProviderConfigurationError("Target-native provider requires ProviderBinding.base_url.")
            environment[self._name(self.base_url_variable)] = binding.base_url.rstrip("/")
        if self.provider_variable:
            provider_value = self.provider_values.get(binding.provider)
            if not provider_value:
                raise SutProviderConfigurationError(
                    f"No target-native provider value is declared for {binding.provider}."
                )
            environment[self._name(self.provider_variable)] = provider_value
        for name, value in self.additional_environment.items():
            if not value:
                raise SutProviderConfigurationError("Target-native additional environment values must be non-empty.")
            environment[self._name(name)] = value
        return environment

    @staticmethod
    def _name(name: str) -> str:
        if not _ENVIRONMENT_NAME.fullmatch(name):
            raise SutProviderConfigurationError("Target-native environment variable names must be valid identifiers.")
        return name

    @staticmethod
    def _validate_binding_host(binding: ProviderBinding) -> None:
        base_url = (binding.base_url or "").rstrip("/")
        host = urlparse(base_url).hostname
        if not base_url or not host or host not in binding.allowed_hosts:
            raise SutProviderConfigurationError(
                "Target-native provider base URL host is not present in ProviderBinding.allowed_hosts."
            )
