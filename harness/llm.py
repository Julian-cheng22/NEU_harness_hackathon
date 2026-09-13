"""
Provider-agnostic LLM client.

Two backends behind one interface, selected by HARNESS_LLM:

  local   llama.cpp's OpenAI-compatible server, running Qwen3.5-9B Q4_K_M
          on the RTX 4060. No network, no cost, no rate limit.
  gemini  Gemini 3 Flash. The demo-day insurance policy.

The point of the abstraction is not elegance -- it is that at a hackathon the
local model WILL have a bad moment, and swapping providers must be one env var
and zero code edits. It also makes the strongest version of our claim testable:
run the same eval on both and show the harness lifts a 9B toward Flash.

THINKING MODE
-------------
Qwen3.5 emits <think>...</think> before every answer by default. On stage that
looks like a hang, and it burns the context budget we need for schema cards.
LOCAL_ENABLE_THINKING=false (the default) disables it via the chat-template
kwarg AND strips any stray block that leaks through, because template support
varies by llama.cpp build and we cannot afford to find that out live.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


class LLM(Protocol):
    name: str

    def chat(self, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.0) -> LLMResponse: ...


def strip_thinking(text: str) -> str:
    """Remove <think> blocks.

    Belt and braces: we also pass enable_thinking=false to the template. An
    unterminated block (hit the token limit mid-thought) is handled too --
    otherwise the answer would be the reasoning trace, which scores as garbage.
    """
    text = _THINK_RE.sub("", text)
    if "<think>" in text and "</think>" not in text:
        text = text.split("<think>")[0]
    return text.strip()


# ---------------------------------------------------------------------------
# Local: llama.cpp server (OpenAI-compatible)
# ---------------------------------------------------------------------------
class LocalLLM:
    name = "local"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 enable_thinking: bool | None = None, timeout: float = 180.0):
        self.base_url = (base_url or os.getenv("LOCAL_BASE_URL",
                                               "http://127.0.0.1:8080/v1")).rstrip("/")
        self.model = model or os.getenv("LOCAL_MODEL", "qwen3.5-9b")
        if enable_thinking is None:
            enable_thinking = os.getenv("LOCAL_ENABLE_THINKING", "false").lower() == "true"
        self.enable_thinking = enable_thinking
        # Generous: a cold llama.cpp prompt-eval over a big schema card can take
        # a while, and a timeout mid-demo is worse than a slow answer.
        self._client = httpx.Client(timeout=timeout)

    def chat(self, messages, tools=None, temperature: float = 0.0) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if not self.enable_thinking:
            # llama.cpp --jinja forwards these into the chat template.
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        r = self._client.post(f"{self.base_url}/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        text = strip_thinking(msg.get("content") or "")

        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            calls.append(ToolCall(
                id=tc.get("id", f"call_{len(calls)}"),
                name=fn.get("name", ""),
                arguments=_safe_json(fn.get("arguments")),
            ))

        return LLMResponse(text=text, tool_calls=calls, raw=data,
                           usage=data.get("usage", {}) or {})

    def health(self) -> tuple[bool, str]:
        """Cheap pre-flight. Call this before a demo, not during one."""
        try:
            r = self._client.get(f"{self.base_url}/models", timeout=5.0)
            r.raise_for_status()
            names = [m.get("id") for m in (r.json().get("data") or [])]
            return True, f"llama.cpp up; models={names}"
        except Exception as e:
            return False, f"llama.cpp unreachable at {self.base_url}: {e}"


# ---------------------------------------------------------------------------
# Fallback: Gemini
# ---------------------------------------------------------------------------
class GeminiLLM:
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from google import genai  # imported lazily: local-only runs need no key

        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Either export it or run with "
                "HARNESS_LLM=local."
            )
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3-flash")
        self._client = genai.Client(api_key=key)

    def chat(self, messages, tools=None, temperature: float = 0.0) -> LLMResponse:
        from google.genai import types

        system = "\n\n".join(m["content"] for m in messages
                             if m.get("role") == "system" and m.get("content"))
        contents = [
            types.Content(role="user" if m["role"] == "user" else "model",
                          parts=[types.Part(text=str(m.get("content") or ""))])
            for m in messages if m.get("role") in ("user", "assistant", "tool")
        ]

        cfg: dict[str, Any] = {"temperature": temperature}
        if system:
            cfg["system_instruction"] = system
        if tools:
            cfg["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t["function"]["name"],
                    description=t["function"].get("description", ""),
                    parameters=t["function"].get("parameters", {}),
                ) for t in tools
            ])]

        resp = self._client.models.generate_content(
            model=self.model, contents=contents,
            config=types.GenerateContentConfig(**cfg),
        )

        calls: list[ToolCall] = []
        for i, part in enumerate(_parts(resp)):
            fc = getattr(part, "function_call", None)
            if fc:
                calls.append(ToolCall(id=f"call_{i}", name=fc.name,
                                      arguments=dict(fc.args or {})))

        return LLMResponse(text=(resp.text or "").strip(), tool_calls=calls)


def _parts(resp) -> list:
    try:
        return list(resp.candidates[0].content.parts or [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Anthropic (Claude) -- third backend, used to smoke-test the pipeline
# ---------------------------------------------------------------------------
class AnthropicLLM:
    """Claude backend.

    Added so the agent loop can be exercised end to end without downloading a
    5.7 GB local model first. Haiku 4.5 is the default: it is the cheapest and
    fastest option, and as a small model it is a reasonable stand-in for the
    Qwen3.5-9B we intend to ship on.

    This backend is a real ADAPTER, not a passthrough. The rest of the harness
    speaks the OpenAI message/tool shape (because llama.cpp's server does), and
    Anthropic's Messages API differs in three ways that all have to be handled:

      * `system` is a top-level parameter, not a message with role="system".
      * Tools are {name, description, input_schema}, not
        {type: "function", function: {...}}.
      * Tool results are `tool_result` content blocks inside a USER message,
        not messages with role="tool". Consecutive results must be merged into
        ONE user message -- splitting them teaches the model to stop making
        parallel tool calls.
    """

    name = "anthropic"

    # Small/fast first: this project's whole premise is helping a weak model.
    DEFAULT_MODEL = "claude-haiku-4-5"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 max_tokens: int = 8192):
        import anthropic  # imported lazily so local-only runs need no package

        self.model = model or os.getenv("ANTHROPIC_MODEL", self.DEFAULT_MODEL)
        self.max_tokens = max_tokens
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        # A bare Anthropic() also picks up ANTHROPIC_AUTH_TOKEN or an
        # `ant auth login` profile, so an unset API key is not fatal.
        self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not tools:
            return []
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    @staticmethod
    def _convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """OpenAI-shaped history -> (system_prompt, anthropic_messages)."""
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []

        def flush_results() -> None:
            # All tool results for one assistant turn go in a SINGLE user message.
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            role = m.get("role")
            if role == "system":
                flush_results()
                if m.get("content"):
                    system_parts.append(str(m["content"]))
            elif role == "user":
                flush_results()
                out.append({"role": "user", "content": str(m.get("content") or "")})
            elif role == "assistant":
                flush_results()
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": str(m["content"])})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {}) or {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", "call_0"),
                        "name": fn.get("name", ""),
                        "input": _safe_json(fn.get("arguments")),
                    })
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                pending_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", "call_0"),
                    "content": str(m.get("content") or ""),
                })
        flush_results()
        return "\n\n".join(system_parts), out

    def chat(self, messages, tools=None, temperature: float = 0.0) -> LLMResponse:
        system, msgs = self._convert_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": msgs,
        }
        if system:
            kwargs["system"] = system
        converted = self._convert_tools(tools)
        if converted:
            kwargs["tools"] = converted
        # Haiku 4.5 does no thinking unless budget_tokens is set, so omitting
        # `thinking` is already the fast path. Sonnet 5 runs adaptive thinking
        # by default, which we do not want for a latency-sensitive demo.
        if self.model.startswith("claude-sonnet-5"):
            kwargs["thinking"] = {"type": "disabled"}
            kwargs["temperature"] = temperature

        resp = self._client.messages.create(**kwargs)

        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name,
                                      arguments=dict(block.input or {})))

        usage = {}
        if getattr(resp, "usage", None):
            usage = {"prompt_tokens": resp.usage.input_tokens,
                     "completion_tokens": resp.usage.output_tokens}

        return LLMResponse(text="\n".join(text_parts).strip(),
                           tool_calls=calls, usage=usage)

    def health(self) -> tuple[bool, str]:
        try:
            self._client.messages.create(
                model=self.model, max_tokens=16,
                messages=[{"role": "user", "content": "ping"}])
            return True, f"Anthropic reachable; model={self.model}"
        except Exception as e:
            return False, f"Anthropic unreachable ({type(e).__name__}): {e}"


def _safe_json(s: Any) -> dict[str, Any]:
    """Tool arguments come back as a JSON *string*, and small models sometimes
    emit malformed JSON. Never raise here -- an empty dict lets the agent loop
    report the problem back to the model, which can then retry."""
    if isinstance(s, dict):
        return s
    if not s:
        return {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


def from_env() -> LLM:
    """Factory. The whole point of the abstraction lives in this one function."""
    provider = os.getenv("HARNESS_LLM", "local").strip().lower()
    if provider == "local":
        return LocalLLM()
    if provider == "gemini":
        return GeminiLLM()
    if provider in ("anthropic", "claude"):
        return AnthropicLLM()
    raise ValueError(
        f"Unknown HARNESS_LLM={provider!r}; expected 'local', 'gemini' or 'anthropic'.")
