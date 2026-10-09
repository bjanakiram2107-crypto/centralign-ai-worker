"""A scripted stand-in for Claude, used ONLY in automated tests.

It replays a fixed list of tool calls so the runtime, guardrails, tools, browser and accounting app can be
tested end to end without an API key and without randomness. The real agent always uses agent/llm.py.
Each scripted step may be a callable that looks at the previous tool result, so tests can assert that the
runtime returned the expected error at the expected moment.
"""

from types import SimpleNamespace


def tool_call(name: str, **tool_input):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=f"toolu_{name}_{id(tool_input)}")


class FakeLLM:
    def __init__(self, script: list):
        self.script = list(script)
        self.seen_results: list[dict] = []
        self.first_messages: list[dict] | None = None
        self.seen_texts: list[str] = []

    def create(self, system, tools, messages):
        if self.first_messages is None:
            self.first_messages = [dict(m) for m in messages]
        last = messages[-1]["content"]
        if isinstance(last, list):
            self.seen_results.extend(b for b in last if isinstance(b, dict) and b.get("type") == "tool_result")
            self.seen_texts.extend(b["text"] for b in last if isinstance(b, dict) and b.get("type") == "text")
        if not self.script:
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="(script over)")])
        step = self.script.pop(0)
        if isinstance(step, Exception):  # simulate the API failing (e.g. a network timeout)
            raise step
        return SimpleNamespace(stop_reason="tool_use", content=[step])

    def last_result(self) -> dict:
        return self.seen_results[-1]


class ScriptedHuman:
    def __init__(self, decision="approved", answer=""):
        self.decision, self.answer = decision, answer
        self.approval_requests: list[str] = []

    def approve(self, summary):
        self.approval_requests.append(summary)
        return self.decision, "scripted test approver"

    def ask(self, question):
        return self.answer
