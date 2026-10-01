"""Sub-agent loop tests with a scripted model."""

import json
from types import SimpleNamespace

from pydantic import BaseModel

from scientist.core.agent import FINISH, Tool, run_agent
from scientist.core.llm import LLM
from scientist.core.store import Store
from scientist.core.tools import SqlArgs, query_memory_tool


class Answer(BaseModel):
    value: int


class AddArgs(BaseModel):
    a: int
    b: int


def call(name, arguments, call_id="c1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def reply(*calls, content=""):
    return SimpleNamespace(content=content, tool_calls=list(calls) or None)


class ScriptedLLM:
    """Plays back assistant turns; records what it was sent."""

    messages = staticmethod(LLM.messages)

    def __init__(self, turns, recovered=None):
        self.turns = list(turns)
        self.recovered = recovered
        self.sent = []
        self.cost = 0.0

    @property
    def usage(self):
        return {"cost_usd": self.cost}

    def complete(self, messages, **kwargs):
        self.sent.append((list(messages), kwargs))
        self.cost += 1.0
        return self.turns.pop(0)

    def structured(self, system, user, schema, **kwargs):
        return self.recovered


ADD = Tool("add", "add two ints", AddArgs, lambda args: str(args.a + args.b))


def run(llm, **kwargs):
    return run_agent(
        llm,
        system="sys",
        task="task",
        tools=[ADD],
        result=Answer,
        purpose="test",
        **kwargs,
    )


def test_tools_then_finish():
    llm = ScriptedLLM(
        [
            reply(call("add", {"a": 2, "b": 3}, "c1"), call("add", {"a": 1, "b": 1}, "c2")),
            reply(call(FINISH, {"value": 5})),
        ]
    )
    out = run(llm)
    assert out.result.value == 5 and out.finished and out.turns == 2
    assert [t["tool"] for t in out.trace] == ["add", "add"]

    second_request = llm.sent[1][0]
    tool_results = [m["content"] for m in second_request if m["role"] == "tool"]
    assert tool_results == ["5", "2"]
    # Same tool list every turn, and the conversation is cached.
    assert llm.sent[0][1]["tools"] == llm.sent[1][1]["tools"]
    assert llm.sent[0][1]["cache_conversation"] is True


def test_tool_errors_go_back_to_the_model():
    llm = ScriptedLLM(
        [
            reply(call("add", {"a": "x"}), call("missing", {}, "c2")),
            reply(call(FINISH, {"value": "nope"})),
            reply(call(FINISH, {"value": 1})),
        ]
    )
    out = run(llm)
    assert out.result.value == 1
    results = [m["content"] for m in llm.sent[1][0] if m["role"] == "tool"]
    assert "Invalid arguments" in results[0] and "Unknown tool" in results[1]
    assert "finish rejected" in llm.sent[2][0][-1]["content"]


def test_turn_budget_forces_recovery():
    llm = ScriptedLLM(
        [reply(call("add", {"a": 1, "b": 1})), reply(content="still thinking")],
        recovered=Answer(value=9),
    )
    out = run(llm, max_turns=2)
    assert out.result.value == 9 and not out.finished
    assert "budget is spent" in llm.sent[1][0][-1]["content"]


def test_cost_budget_stops_early():
    llm = ScriptedLLM(
        [reply(call("add", {"a": 1, "b": 1})), reply(call(FINISH, {"value": 2}))]
    )
    out = run(llm, max_turns=10, max_cost_usd=0.5)
    assert out.turns == 2 and out.result.value == 2
    assert "budget is spent" in llm.sent[1][0][-1]["content"]


def test_query_memory_is_read_only():
    store = Store(":memory:")
    campaign = store.create_campaign("k", "goal")
    tool = query_memory_tool(store, campaign["id"])
    assert "goal" in tool.run(SqlArgs(sql="SELECT goal FROM campaigns"))
    assert "Only a single SELECT" in tool.run(SqlArgs(sql="DELETE FROM campaigns"))
    assert "Only a single SELECT" in tool.run(
        SqlArgs(sql="SELECT 1; DELETE FROM campaigns")
    )
    assert "SQL error" in tool.run(
        SqlArgs(sql="WITH x AS (SELECT 1) DELETE FROM campaigns")
    )
    assert store.get_campaign("k") is not None
