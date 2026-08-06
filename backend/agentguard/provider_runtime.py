from __future__ import annotations

import ast
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
                arguments = _decode_tool_arguments(item["function"].get("arguments", "{}"))
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


def _decode_tool_arguments(value: object) -> object:
    """Decode provider tool arguments while keeping malformed output fail-fast.

    Some OpenAI-compatible providers occasionally serialize a JSON-shaped tool
    object with Python literal quoting.  We accept only the standard JSON form
    first, then a non-executable ``ast.literal_eval`` representation; truncated
    or otherwise malformed arguments still raise the original parse error.
    """

    if not isinstance(value, str):
        raise TypeError("tool arguments must be a string")
    try:
        return json.loads(value)
    except json.JSONDecodeError as json_error:
        repaired = _close_unterminated_json(value)
        if repaired is not None:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError):
            raise json_error


def _close_unterminated_json(value: str) -> str | None:
    """Repair only a JSON object that ends with unmatched containers."""

    stack: list[str] = []
    repaired: list[str] = []
    in_string = False
    escaped = False
    matching = {"]": "[", "}": "{"
    }
    for char in value:
        previous = next((item for item in reversed(repaired) if not item.isspace()), "")
        if (
            char == "{"
            and stack
            and stack[-1] == "{"
            and "[" in stack[:-1]
            and previous == ","
        ):
            stack.pop()
            comma_index = len(repaired) - 1
            while comma_index >= 0 and repaired[comma_index].isspace():
                comma_index -= 1
            if comma_index < 0 or repaired[comma_index] != ",":
                return None
            repaired.insert(comma_index, "}")
        repaired.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack or stack[-1] != matching[char]:
                if char == "]" and "[" in stack:
                    while stack and stack[-1] == "{":
                        stack.pop()
                        repaired.insert(len(repaired) - 1, "}")
                    if not stack or stack[-1] != "[":
                        return None
                else:
                    return None
            stack.pop()
    if in_string:
        return None
    repaired.extend("]" if item == "[" else "}" for item in reversed(stack))
    return "".join(repaired) if stack or repaired != list(value) else None
