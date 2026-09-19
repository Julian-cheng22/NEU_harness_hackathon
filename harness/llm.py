"""
Provider-agnostic LLM client.

Backends behind one interface, selected by HARNESS_LLM:

  local      llama.cpp's OpenAI-compatible server, running Qwen3.5-9B Q4_K_M
             on the RTX 4060. No network, no cost, no rate limit. This is the
             provider every committed eval result was measured on.
  anthropic  Claude Haiku 4.5. Cloud fallback when the GPU path will not come
             up; needs ANTHROPIC_API_KEY.
  gemini     FROZEN, see below. Kept in the tree, refuses to run.

The point of the abstraction is not elegance -- it is that at a hackathon the
local model WILL have a bad moment, and swapping providers must be one env var
and zero code edits. It also makes the strongest version of our claim testable:
run the same eval on two providers and show the harness lifts a 9B toward a
frontier model.

THINKING MODE
-------------
Qwen3.5 emits <think>...</think> before every answer by default. On stage that
looks like a hang, and it burns the context budget we need for schema cards.
LOCAL_ENABLE_THINKING=false (the default) disables it via the chat-template
kwarg AND strips any stray block that leaks through, because template support
varies by llama.cpp build and we cannot afford to find that out live.

GEMINI IS FROZEN (2026-09-18)
-----------------------------
A Google API policy change put the Gemini path out of scope for this project.
The adapter is deliberately NOT deleted -- the thought_signature handling and
the message/tool conversion are the hard-won part and we want them back if the
freeze lifts -- but constructing GeminiLLM now raises. See `gemini_frozen()`.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Opaque provider state echoed back on the next request. Gemini 3 returns a
    # `thought_signature` on every function-call part and REJECTS the following
    # request with 400 INVALID_ARGUMENT if it is not resent verbatim, so this
    # has to survive the round trip through the OpenAI-shaped history. No other
    # backend sets it, and none of them ever sees it.
    signature: bytes | None = None


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
# Gemini -- FROZEN 2026-09-18
# ---------------------------------------------------------------------------
GEMINI_FROZEN_ON = "2026-09-18"

GEMINI_FROZEN_MESSAGE = (
    f"The Gemini backend is FROZEN (since {GEMINI_FROZEN_ON}) following a Google "
    "API policy change; this project no longer runs against it. Use "
    "HARNESS_LLM=local (llama.cpp / Qwen3.5-9B -- what every committed eval "
    "result was measured on) or HARNESS_LLM=anthropic. The adapter is still in "
    "harness/llm.py: set GEMINI_UNFREEZE=true to thaw it deliberately."
)


def gemini_frozen() -> bool:
    """True unless the operator has explicitly thawed the backend.

    One env var, checked at construction rather than at import, so a run that
    has no business touching Gemini cannot reach it by accident -- including
    the web server's health probe and anything that constructs GeminiLLM
    directly instead of going through from_env().
    """
    return os.getenv("GEMINI_UNFREEZE", "").strip().lower() not in ("1", "true", "yes")


class GeminiLLM:
    """Gemini backend. FROZEN -- see `gemini_frozen()` above.

    Kept as documentation of a working adapter, not as a live path. Everything
    below this docstring is unchanged from when it last ran green; only the
    guard at the top of __init__ is new.

    Like the Anthropic one, this is a real ADAPTER. The rest of the harness
    speaks the OpenAI message/tool shape, and Gemini differs in three ways that
    all have to be handled or the harness arm silently degrades to the baseline:

      * `system` is config (`system_instruction`), not a message.
      * An assistant's tool call is a `function_call` PART on a role="model"
        turn -- not a separate `tool_calls` field.
      * A tool result is a `function_response` part on a role="user" turn, keyed
        by function NAME (Gemini has no tool-call ids). Consecutive results
        merge into one turn.

    The previous version flattened every non-user message to a plain text
    role="model" turn. That drops the model's own function calls from the
    history and feeds tool output back as if the model had said it, so the
    agent loop cannot see what it called or what came back -- it would burn
    steps and converge to a one-shot answer. Tool calling is the entire harness
    arm, so this path has to be right.

    KEY TYPE: since September 2026 the Gemini API rejects legacy "standard"
    keys. Create the key in AI Studio (aistudio.google.com/apikey), which issues
    a service-account-bound "auth key" and restricts it to the Gemini API by
    default. The Cloud console's Credentials page still makes standard keys.
    """

    name = "gemini"

    # Flash, because this project's premise is helping a SMALL model. Override
    # with GEMINI_MODEL; health() lists what the key can actually reach.
    DEFAULT_MODEL = "gemini-3.8-flash"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 max_retries: int | None = None):
        # Checked before the import so a frozen run reports the freeze, not a
        # confusing ModuleNotFoundError from an uninstalled google-genai.
        if gemini_frozen():
            raise RuntimeError(GEMINI_FROZEN_MESSAGE)

        from google import genai  # imported lazily: local-only runs need no key

        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Either export it or run with "
                "HARNESS_LLM=local."
            )
        self.model = model or os.getenv("GEMINI_MODEL", self.DEFAULT_MODEL)
        self.max_retries = max_retries or int(os.getenv("GEMINI_MAX_RETRIES", "4"))
        self._client = genai.Client(api_key=key)

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list:
        from google.genai import types
        if not tools:
            return []
        decls = []
        for t in tools:
            fn = t.get("function", t)
            params = fn.get("parameters") or {"type": "object", "properties": {}}
            # Gemini rejects an empty `properties` with no `type`; our no-arg
            # tools (get_schema, infer_joins) hit exactly that.
            params.setdefault("type", "object")
            params.setdefault("properties", {})
            decls.append(types.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=params,
            ))
        return [types.Tool(function_declarations=decls)]

    @staticmethod
    def _convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list]:
        """OpenAI-shaped history -> (system_instruction, gemini contents)."""
        from google.genai import types

        system_parts: list[str] = []
        contents: list = []
        names_by_id: dict[str, str] = {}
        pending: list = []      # function_response parts awaiting one user turn

        def flush() -> None:
            if pending:
                contents.append(types.Content(role="user", parts=list(pending)))
                pending.clear()

        for m in messages:
            role = m.get("role")
            if role == "system":
                flush()
                if m.get("content"):
                    system_parts.append(str(m["content"]))
            elif role == "user":
                flush()
                contents.append(types.Content(
                    role="user", parts=[types.Part(text=str(m.get("content") or ""))]))
            elif role == "assistant":
                flush()
                parts = []
                if m.get("content"):
                    parts.append(types.Part(text=str(m["content"])))
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {}) or {}
                    name = fn.get("name", "")
                    names_by_id[tc.get("id", "")] = name
                    # The signature rides on the PART, not the FunctionCall,
                    # and each parallel call carries its own.
                    parts.append(types.Part(
                        function_call=types.FunctionCall(
                            name=name, args=_safe_json(fn.get("arguments"))),
                        thought_signature=tc.get("signature")))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                # Gemini matches results to calls by name, not id.
                name = m.get("name") or names_by_id.get(m.get("tool_call_id", ""), "")
                pending.append(types.Part(function_response=types.FunctionResponse(
                    name=name, response={"result": str(m.get("content") or "")})))
        flush()
        return "\n\n".join(system_parts), contents

    # Transient by nature: 429 is the free tier's rate limit, 503 is Google
    # shedding load on a popular model. Both are worth waiting out; a 400 is
    # our bug and must surface immediately.
    _RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

    def _generate(self, **kwargs):
        """generate_content with backoff.

        An agent step that dies on a transient 503 loses every tool result the
        run has accumulated, so one blip ends the question. Measured: a Q01 run
        reached step 7 and threw away six successful tool calls.
        """
        from google.genai import errors

        delay = 2.0
        for attempt in range(self.max_retries):
            try:
                return self._client.models.generate_content(**kwargs)
            except errors.APIError as e:
                status = getattr(e, "code", None) or getattr(e, "status_code", None)
                if status not in self._RETRY_STATUS or attempt == self.max_retries - 1:
                    raise
                # A per-DAY quota does not refill on a backoff timescale. The
                # API still sends a short retryDelay, so backing off looks
                # reasonable and then fails anyway, ~30s later, per question.
                # Fail immediately and let the caller report the real problem.
                if status == 429 and "PerDay" in str(e):
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 30.0)

    def chat(self, messages, tools=None, temperature: float = 0.0) -> LLMResponse:
        from google.genai import types

        system, contents = self._convert_messages(messages)
        cfg: dict[str, Any] = {"temperature": temperature}
        if system:
            cfg["system_instruction"] = system
        converted = self._convert_tools(tools)
        if converted:
            cfg["tools"] = converted
            # The agent loop dispatches tools itself. Left on, the SDK tries to
            # call them for us, warns about it, and renames them `default_api:*`.
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                disable=True)

        resp = self._generate(model=self.model, contents=contents,
                              config=types.GenerateContentConfig(**cfg))

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for i, part in enumerate(_parts(resp)):
            fc = getattr(part, "function_call", None)
            if fc:
                calls.append(ToolCall(
                    id=fc.id or f"call_{i}", name=fc.name,
                    arguments=dict(fc.args or {}),
                    signature=getattr(part, "thought_signature", None)))
            elif getattr(part, "text", None):
                text_parts.append(part.text)

        # Read parts rather than resp.text: the convenience accessor warns or
        # raises when the turn is function calls with no prose.
        usage: dict[str, int] = {}
        um = getattr(resp, "usage_metadata", None)
        if um:
            usage = {"prompt_tokens": um.prompt_token_count or 0,
                     "completion_tokens": um.candidates_token_count or 0,
                     "total_tokens": um.total_token_count or 0}

        return LLMResponse(text="\n".join(text_parts).strip(),
                           tool_calls=calls, usage=usage)

    def health(self) -> tuple[bool, str]:
        """Pre-flight that costs no generation quota.

        models.list() is a metadata call, so this validates the key AND that
        the configured model is actually reachable by it without spending a
        single token. Model availability varies by tier, so a wrong id should
        fail here with the real options rather than mid-eval.
        """
        try:
            names = [(m.name or "").replace("models/", "", 1)
                     for m in self._client.models.list()]
        except Exception as e:
            return False, f"Gemini unreachable ({type(e).__name__}): {e}"

        if self.model not in names:
            flash = sorted(n for n in names if "flash" in n)
            return False, (f"key works, but model {self.model!r} is not available to it. "
                           f"Flash models offered: {', '.join(flash[:8]) or '(none)'}")
        return True, f"Gemini reachable; model={self.model}"


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
        # Still routed rather than dropped, so a stale HARNESS_LLM=gemini in
        # someone's .env gets the freeze notice instead of "Unknown provider".
        return GeminiLLM()
    if provider in ("anthropic", "claude"):
        return AnthropicLLM()
    raise ValueError(
        f"Unknown HARNESS_LLM={provider!r}; expected 'local' or 'anthropic' "
        f"('gemini' is frozen).")


def _main() -> int:
    """Pre-flight the configured provider:  python -m harness.llm

    Checks the credential and, where the provider offers a metadata call, that
    the configured model is actually reachable -- model ids change often enough
    that guessing one and discovering it mid-eval is a waste of a demo slot.
    Costs no generation quota. Never prints the key.
    """
    from pathlib import Path

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    provider = os.getenv("HARNESS_LLM", "local")
    print(f"HARNESS_LLM={provider}")
    try:
        client = from_env()
    except Exception as e:
        print(f"  could not initialise: {e}")
        return 1

    print(f"  model={getattr(client, 'model', '?')}")
    ok = True
    if hasattr(client, "health"):
        ok, msg = client.health()
        print(f"  {'OK ' if ok else 'FAIL'} {msg}")

    # List models even when health FAILED -- a wrong model id is exactly when
    # you need to see the options, so short-circuiting here would hide them.
    if isinstance(client, GeminiLLM):
        try:
            names = sorted((m.name or "").replace("models/", "", 1)
                           for m in client._client.models.list())
        except Exception as e:
            print(f"  could not list models: {e}")
            return 1
        flash = [n for n in names if "flash" in n]
        print(f"  {len(names)} models reachable; {len(flash)} with 'flash':")
        for n in flash:
            print(f"    {n}{'   <- configured' if n == client.model else ''}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
