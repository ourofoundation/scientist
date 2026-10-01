"""Bounded sub-agent: a tool loop with a budget that returns a typed result.

This is the one place the scientist lets a model run free, and only inside a
box: a fixed set of tools, a turn and cost budget, and a result schema. The
caller hands over a task and gets back a validated object — never a
transcript to parse. Nothing here knows about materials; research is one use.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..utils.logging import get_logger
from .llm import strict_schema

logger = get_logger("agent")

R = TypeVar("R", bound=BaseModel)

FINISH = "finish"
MAX_TOOL_OUTPUT = 20_000  # characters of a tool result shown to the model


@dataclass
class Tool:
    """A function the sub-agent may call. ``run`` gets validated arguments."""

    name: str
    description: str
    parameters: Type[BaseModel]
    run: Callable[[Any], str]

    def spec(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": strict_schema(self.parameters),
            },
        }


@dataclass
class AgentRun(Generic[R]):
    result: R
    turns: int
    cost_usd: float
    # What the agent did, for the record: [{tool, arguments, ok}]
    trace: List[Dict[str, Any]] = field(default_factory=list)
    finished: bool = True  # False when the budget ran out before `finish`


def run_agent(
    llm,
    *,
    system: str,
    task: str,
    tools: List[Tool],
    result: Type[R],
    purpose: str,
    shared: Optional[str] = None,
    max_turns: int = 10,
    max_cost_usd: Optional[float] = None,
) -> AgentRun[R]:
    """Let the model use ``tools`` on ``task`` until it calls ``finish``.

    The model ends the run by calling ``finish`` with arguments matching
    ``result``. When the turn or cost budget runs out it is told to finish with
    what it has; if it still does not, the result is extracted from the
    conversation with a plain structured call.
    """
    by_name = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    finish_spec = {
        "type": "function",
        "function": {
            "name": FINISH,
            "description": "End the task and return your result. Call this "
            "once, when you have what the task asks for.",
            "parameters": strict_schema(result),
        },
    }
    system = (
        f"{system}\n\nYou have {max_turns} turns. Use the tools to do the "
        f"work, then call `{FINISH}` with your result. Several tool calls in "
        "one turn run together and count as one turn."
    )
    messages = llm.messages(system, task, shared)
    trace: List[Dict[str, Any]] = []
    spent_before = llm.usage["cost_usd"]

    def spent() -> float:
        return llm.usage["cost_usd"] - spent_before

    def done(value: R, turns: int, finished: bool = True) -> AgentRun[R]:
        logger.info(
            f"{purpose}: {'finished' if finished else 'cut off'} after {turns} "
            f"turns, {len(trace)} tool calls, ${spent():.2f}"
        )
        return AgentRun(value, turns, spent(), trace, finished)

    for turn in range(1, max_turns + 1):
        last = turn == max_turns or (
            max_cost_usd is not None and spent() >= max_cost_usd
        )
        if last:
            messages.append(
                {
                    "role": "user",
                    "content": f"Your budget is spent. Call `{FINISH}` now "
                    "with the best result you can give from what you have.",
                }
            )
        # The tool list stays fixed for the whole run: changing it would
        # invalidate the cached conversation prefix.
        message = llm.complete(
            messages,
            purpose=purpose,
            tools=specs + [finish_spec],
            cache_conversation=True,
        )
        messages.append(_assistant_message(message))
        calls = message.tool_calls or []

        if not calls:
            if last:
                break
            messages.append(
                {
                    "role": "user",
                    "content": f"Continue with the tools, or call `{FINISH}` "
                    "if the task is complete.",
                }
            )
            continue

        finish = next((c for c in calls if c.function.name == FINISH), None)
        work = [c for c in calls if c is not finish]
        if last:
            outputs = [("Budget spent; this call was not run.", False)] * len(work)
        else:
            with ThreadPoolExecutor(max_workers=min(8, len(work) or 1)) as pool:
                outputs = list(
                    pool.map(
                        lambda c: _run_tool(
                            by_name.get(c.function.name),
                            c.function.name,
                            c.function.arguments,
                        ),
                        work,
                    )
                )
        for call, (output, ok) in zip(work, outputs):
            name, raw = call.function.name, call.function.arguments
            trace.append({"tool": name, "arguments": raw, "ok": ok})
            logger.info(f"{purpose}: {name}({raw[:200]}) → {'ok' if ok else 'error'}")
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )

        if finish is not None:
            try:
                value = result.model_validate(
                    json.loads(finish.function.arguments or "{}")
                )
                return done(value, turn)
            except (ValueError, ValidationError) as e:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": finish.id,
                        "content": f"finish rejected, fix and call it again: {e}",
                    }
                )
        if last:
            break

    # Budget spent without a valid `finish`: recover the result from the talk.
    transcript = "\n\n".join(
        f"[{m['role']}] {m['content']}"
        for m in messages[1:]
        if isinstance(m.get("content"), str) and m["content"]
    )
    value = llm.structured(
        "Extract the result of this unfinished task from its transcript. Use "
        "only what the transcript supports.",
        f"## Task\n{task}\n\n## Transcript\n{transcript[-60_000:]}",
        result,
        purpose=f"{purpose}:recover",
    )
    return done(value, max_turns, finished=False)


def _run_tool(tool: Optional[Tool], name: str, raw: str) -> tuple:
    """Run one tool call; errors go back to the model as text, not exceptions."""
    if tool is None:
        return f"Unknown tool '{name}'.", False
    try:
        arguments = tool.parameters.model_validate(json.loads(raw or "{}"))
    except (ValueError, ValidationError) as e:
        return f"Invalid arguments: {e}", False
    try:
        output = str(tool.run(arguments))
    except Exception as e:
        return f"Tool failed: {e}", False
    if len(output) > MAX_TOOL_OUTPUT:
        output = output[:MAX_TOOL_OUTPUT] + "\n… (truncated)"
    return output or "(empty result)", True


def _assistant_message(message: Any) -> Dict[str, Any]:
    """Assistant turn in wire form, keeping reasoning blocks for the next call."""
    out: Dict[str, Any] = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {
                    "name": c.function.name,
                    "arguments": c.function.arguments,
                },
            }
            for c in message.tool_calls
        ]
    reasoning = getattr(message, "reasoning_details", None)
    if reasoning:
        out["reasoning_details"] = reasoning
    return out
