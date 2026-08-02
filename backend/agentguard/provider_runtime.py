from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .domain import ProviderBinding


class ProviderRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        response_fingerprint: str | None = None,
        request_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_hit_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.response_fingerprint = response_fingerprint
        self.request_id = request_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_hit_tokens = cache_hit_tokens


@dataclass(frozen=True)
class ProviderToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ProviderTurn:
    request_id: str | None
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...]
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int
    request_fingerprint: str
    response_fingerprint: str


class OpenAICompatibleChatCompletionsClient:
    """One bounded control-plane turn for an approved OpenAI-compatible backend."""

    def __init__(self, binding: ProviderBinding, api_key: str) -> None:
        if binding.role != "control_plane":
            raise ValueError("Evolution control-plane runtime requires a control_plane ProviderBinding.")
        if not api_key:
            raise ValueError("A runtime API credential is required.")
        self.binding = binding
        self.api_key = api_key
        base_url = (binding.base_url or "").rstrip("/")
        host = urlparse(base_url).hostname
        if not host or host not in binding.allowed_hosts:
            raise ValueError("Provider base URL host is not present in ProviderBinding.allowed_hosts.")
        if not base_url:
            raise ValueError("ProviderBinding.base_url is required for an OpenAI-compatible control-plane backend.")
        self.endpoint = f"{base_url}/chat/completions"

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ProviderTurn:
        payload: dict[str, object] = {
            "model": self.binding.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "temperature": self.binding.temperature,
            "max_tokens": self.binding.max_output_tokens,
            "stream": False,
        }
        if self.binding.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_fingerprint = hashlib.sha256(encoded).hexdigest()
        request = Request(
            self.endpoint,
            data=encoded,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.binding.timeout_seconds) as response:  # noqa: S310 - approved host checked above
                raw = response.read()
        except HTTPError as error:
            raw_error = error.read()
            raise ProviderRuntimeError(
                f"Provider returned HTTP {error.code}.",
                response_fingerprint=hashlib.sha256(raw_error).hexdigest(),
            ) from error
        except URLError as error:
            raise ProviderRuntimeError(f"Provider request failed: {error.reason}") from error
        except TimeoutError as error:
            raise ProviderRuntimeError("Provider request timed out.") from error
        response_fingerprint = hashlib.sha256(raw).hexdigest()
        try:
            body = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            detail = f"{type(error).__name__}:{str(error)}"[:160]
            raise ProviderRuntimeError(
                f"Provider returned an invalid Chat Completions payload ({detail}).",
                response_fingerprint=response_fingerprint,
            ) from error
        try:
            choice = body["choices"][0]
            message = choice["message"]
            usage = body["usage"]
            request_id = str(body.get("id")) if body.get("id") else None
            details = usage.get("prompt_tokens_details") or {}
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)
            cache_hit_tokens = int(details.get("cached_tokens", 0) or 0)
            native_calls = message.get("tool_calls") or []
            calls: list[ProviderToolCall] = []
            for item in native_calls:
                arguments = json.loads(item["function"].get("arguments", "{}"))
                if not isinstance(arguments, dict):
                    raise TypeError("tool arguments must be an object")
                calls.append(ProviderToolCall(str(item["id"]), str(item["function"]["name"]), arguments))
            return ProviderTurn(
                request_id=request_id,
                finish_reason=str(choice.get("finish_reason") or ""),
                tool_calls=tuple(calls),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_hit_tokens=cache_hit_tokens,
                request_fingerprint=request_fingerprint,
                response_fingerprint=response_fingerprint,
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            detail = f"{type(error).__name__}:{str(error)}"[:160]
            raise ProviderRuntimeError(
                f"Provider returned an invalid Chat Completions payload ({detail}).",
                response_fingerprint=response_fingerprint,
                request_id=request_id if "request_id" in locals() else None,
                input_tokens=input_tokens if "input_tokens" in locals() else 0,
                output_tokens=output_tokens if "output_tokens" in locals() else 0,
                cache_hit_tokens=cache_hit_tokens if "cache_hit_tokens" in locals() else 0,
            ) from error


def build_control_plane_client(
    binding: ProviderBinding, api_key: str
) -> OpenAICompatibleChatCompletionsClient:
    """Select the configured backend without retrying or falling back to another provider."""
    return OpenAICompatibleChatCompletionsClient(binding, api_key)
