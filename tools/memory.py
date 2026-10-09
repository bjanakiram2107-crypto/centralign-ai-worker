"""Company memory: durable facts the worker learns about how this company operates.

AgentState is the memory of ONE run and is gone when the run ends. Company memory survives across runs:
when the agent discovers something that will save time next time (for example "the invoice says 'Acme Corp'
but AcmeBooks lists the vendor as 'Acme Corporation', V-001"), it calls `remember`. At the start of every
run, all remembered facts are shown to the model, so a later run can skip a dead end it already learned about.

Memories are hints, not truth: the system prompt tells the agent to confirm them in the system of record
before acting, and every memory keeps the evidence it was based on and the run that learned it.
Stored as a small JSON file (memory/company_memory.json), so it is easy to read, edit or delete by hand.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.base import Tool, ToolContext, ToolError, schema

MAX_MEMORIES = 50
MAX_FACT_CHARS = 300


class CompanyMemory:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, memories: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(memories, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, topic: str, fact: str, evidence: str, run_id: str) -> str:
        existing = self.load()
        memories = [m for m in existing if m["topic"] != topic]  # a newer fact on the same topic replaces the old one
        replaced = len(memories) < len(existing)
        if len(memories) >= MAX_MEMORIES:
            raise ToolError(f"Company memory is full ({MAX_MEMORIES} facts). Only remember what will be reused.")
        memories.append({"topic": topic, "fact": fact, "evidence": evidence, "learned_in_run": run_id,
                         "learned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")})
        self.save(memories)
        return "updated" if replaced else "added"

    def as_prompt(self) -> str:
        memories = self.load()
        if not memories:
            return "COMPANY MEMORY: empty (nothing learned in earlier runs yet)."
        lines = [f"- [{m['topic']}] {m['fact']} (evidence: {m['evidence']}; learned {m['learned_at']})"
                 for m in memories]
        return ("COMPANY MEMORY (facts learned in earlier runs; use them as hints and confirm in the system of "
                "record before acting on them):\n" + "\n".join(lines))


def _memory(ctx: ToolContext) -> CompanyMemory:
    return CompanyMemory(ctx.config.memory_path)


def remember(ctx: ToolContext, topic: str, fact: str, evidence: str) -> str:
    if len(fact) > MAX_FACT_CHARS:
        raise ToolError(f"Keep a memory under {MAX_FACT_CHARS} characters: one durable fact, not a log.")
    action = _memory(ctx).add(topic.strip(), fact.strip(), evidence.strip(), ctx.state.run_dir.name)
    ctx.state.facts.setdefault("memories_written", []).append(topic)
    return f"Company memory {action}: [{topic}] {fact}"


def recall(ctx: ToolContext, query: str) -> str:
    words = [w for w in query.lower().split() if len(w) > 2]
    hits = [m for m in _memory(ctx).load()
            if any(w in (m["topic"] + " " + m["fact"]).lower() for w in words)]
    if not hits:
        return f"No company memory matches '{query}'."
    return "\n".join(f"- [{m['topic']}] {m['fact']} (evidence: {m['evidence']})" for m in hits)


TOOLS = [
    Tool("remember",
         "Save a durable fact about how this company operates, so future runs can reuse it (e.g. a vendor's legal "
         "name in AcmeBooks differs from the name on its invoices; where a required field's value comes from). "
         "Not for one-off invoice data or anything secret. topic: short key like 'vendor:Acme Corp'.",
         schema({"topic": {"type": "string"}, "fact": {"type": "string"}, "evidence": {"type": "string"}}),
         remember),
    Tool("recall",
         "Search company memory (facts learned in earlier runs) by keywords.",
         schema({"query": {"type": "string"}}), recall),
]
