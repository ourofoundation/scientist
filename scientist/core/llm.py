"""Thin OpenRouter client: text, schema-validated JSON, and raw tool-call turns."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..utils.logging import get_logger

logger = get_logger("llm")

T = TypeVar("T", bound=BaseModel)

# JSON-schema keywords strict structured outputs reject.
_UNSUPPORTED_KEYS = {
    "default",
    "title",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
}


def strict_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Pydantic schema → strict form (all fields required, no extras)."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYS}
        for key in ("properties", "$defs"):
            # Keys in these maps are names (a field may be called "title").
            if isinstance(node.get(key), dict):
                out[key] = {name: walk(sub) for name, sub in node[key].items()}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return walk(model.model_json_schema())


def extract_json(text: str) -> Any:
    """Parse the outermost JSON object in a model reply (tolerates fences/prose)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start : end + 1])


def _get(obj: Any, *path: str) -> Any:
    """Nested attribute/key lookup that tolerates objects, dicts and gaps."""
    for key in path:
        if obj is None:
            return None
        obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    return obj


class LLM:
    """OpenRouter chat client that records every call it makes.

    ``shared`` on any call is context common to several calls (the search
    record). It is sent first, as its own cached block, so calls that share it
    within the cache window pay the cached-read price for it.
    """

    def __init__(self, config) -> None:
        from openai import OpenAI

        self.model = config.llm_model
        self.max_tokens = config.llm_max_tokens
        self.reasoning_effort = config.llm_reasoning_effort
        self.client = OpenAI(
            base_url=config.llm_base_url, api_key=config.llm_api_key, timeout=900
        )
        self.calls: List[Dict[str, Any]] = []  # one record per API call
        self._unsaved = 0

    # -- public calls -----------------------------------------------------

    def text(
        self,
        system: str,
        user: str,
        *,
        shared: Optional[str] = None,
        purpose: str = "text",
        web: bool = False,
        model: Optional[str] = None,
    ) -> str:
        """Free-form reply. ``web=True`` grounds it with OpenRouter web search."""
        extra = {"plugins": [{"id": "web", "max_results": 5}]} if web else {}
        message = self.complete(
            self.messages(system, user, shared),
            purpose=purpose,
            model=model,
            extra_body=extra,
        )
        return self._content(message)

    def structured(
        self,
        system: str,
        user: str,
        schema: Type[T],
        *,
        shared: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> T:
        """Reply validated against ``schema``; one repair retry on bad output."""
        purpose = purpose or schema.__name__
        spec = strict_schema(schema)
        user = (
            f"{user}\n\nReply with a single JSON object matching this schema:\n"
            f"{json.dumps(spec)}"
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": True, "schema": spec},
        }
        messages = self.messages(system, user, shared)

        try:
            reply = self._content(
                self.complete(messages, purpose=purpose, response_format=response_format)
            )
        except Exception as e:
            # Provider rejected the schema — the prompt carries it too, so retry plain.
            logger.warning(f"Structured-output request failed ({e}); retrying as text")
            reply = self._content(self.complete(messages, purpose=purpose))

        try:
            return schema.model_validate(extract_json(reply))
        except (ValueError, ValidationError) as e:
            logger.warning(f"{schema.__name__} reply invalid ({e}); asking for a fix")
            messages = messages + [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"That reply was rejected:\n{e}\n\n"
                    "Return only the corrected JSON object.",
                },
            ]
            fixed = self._content(self.complete(messages, purpose=f"{purpose}:repair"))
            return schema.model_validate(extract_json(fixed))

    def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        purpose: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        cache_conversation: bool = False,
    ) -> Any:
        """One chat completion; returns the assistant message object.

        ``cache_conversation`` caches the whole conversation up to its last
        block, so a multi-turn loop re-reads its own history at the cached
        price. Leave it off for one-shot calls: a cache write costs more than
        a plain read and is wasted if nothing reads it back.
        """
        body: Dict[str, Any] = {"usage": {"include": True}, **(extra_body or {})}
        if cache_conversation:
            body["cache_control"] = {"type": "ephemeral"}
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}

        kwargs: Dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "extra_body": body,
        }
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        started = time.time()
        response = self.client.chat.completions.create(**kwargs)
        self._record(purpose, kwargs["model"], response.usage, time.time() - started)
        if not response.choices:
            raise RuntimeError(f"LLM returned no choices: {_get(response, 'error')}")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            logger.warning(f"{purpose}: reply was cut off at max_tokens")
        return choice.message

    # -- usage ------------------------------------------------------------

    @property
    def usage(self) -> Dict[str, Any]:
        """Totals across every call so far, with a per-purpose breakdown."""
        return summarize_calls(self.calls)

    def drain(self) -> List[Dict[str, Any]]:
        """Call records not yet handed out (for persisting step by step)."""
        fresh = self.calls[self._unsaved :]
        self._unsaved = len(self.calls)
        return fresh

    def _record(self, purpose: str, model: str, usage: Any, seconds: float) -> None:
        record = {
            "purpose": purpose,
            "model": model,
            "input_tokens": _get(usage, "prompt_tokens") or 0,
            "cached_tokens": _get(usage, "prompt_tokens_details", "cached_tokens") or 0,
            "cache_write_tokens": _get(
                usage, "prompt_tokens_details", "cache_write_tokens"
            )
            or 0,
            "output_tokens": _get(usage, "completion_tokens") or 0,
            "reasoning_tokens": _get(
                usage, "completion_tokens_details", "reasoning_tokens"
            )
            or 0,
            "cost_usd": float(_get(usage, "cost") or 0),
            "seconds": round(seconds, 2),
        }
        self.calls.append(record)
        logger.debug(f"LLM call: {record}")

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def messages(
        system: str, user: str, shared: Optional[str]
    ) -> List[Dict[str, Any]]:
        if not shared:
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        return [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": shared,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": system},
                ],
            },
            {"role": "user", "content": user},
        ]

    @staticmethod
    def _content(message: Any) -> str:
        if not message.content:
            raise RuntimeError("LLM returned an empty reply")
        return message.content


def summarize_calls(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals plus a per-purpose breakdown for a list of call records."""
    keys = (
        "input_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost_usd",
        "seconds",
    )
    total: Dict[str, Any] = {"calls": len(calls), **{k: 0 for k in keys}}
    by_purpose: Dict[str, Dict[str, Any]] = {}
    for call in calls:
        group = by_purpose.setdefault(
            call["purpose"].split(":")[0], {"calls": 0, "cost_usd": 0.0}
        )
        group["calls"] += 1
        group["cost_usd"] += call["cost_usd"]
        for k in keys:
            total[k] += call[k]
    total["by_purpose"] = by_purpose
    return total
