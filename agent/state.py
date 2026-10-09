"""Agent state: everything the worker knows about the current task.

The LLM is stateless between calls. This object is the agent's working memory.
The runtime (not the LLM) owns it: tools write facts here, guardrails read it,
and it is saved to runs/<run_id>/state.json after every step so a run can be
inspected (and, in future, resumed).
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


@dataclass
class AgentState:
    goal: str
    run_dir: Path
    plan: list[dict] = field(default_factory=list)            # [{"step": "...", "status": "todo|doing|done|skipped"}]
    actions: list[dict] = field(default_factory=list)         # every tool call, its input and result summary
    facts: dict = field(default_factory=dict)                 # validated facts, e.g. the extracted invoice
    approvals: list[dict] = field(default_factory=list)       # human approval requests and decisions
    errors: list[dict] = field(default_factory=list)          # failed actions (used by recovery)
    evidence: list[str] = field(default_factory=list)         # screenshots and files proving what happened
    verification: dict | None = None                          # result of the last independent verification
    status: str = "running"                                   # running | completed | on_hold | failed | cancelled
    summary: str = ""

    # ---- helpers used by tools and guardrails -----------------------------
    def has_plan(self) -> bool:
        return bool(self.plan)

    def approved_for(self, action_key: str) -> bool:
        return any(a["action_key"] == action_key and a["decision"] == "approved" for a in self.approvals)

    def any_approval_granted(self) -> bool:
        return any(a["decision"] == "approved" for a in self.approvals)

    def record_action(self, tool: str, tool_input: dict, ok: bool, result_summary: str) -> None:
        self.actions.append({"time": now(), "tool": tool, "input": tool_input, "ok": ok,
                             "result": result_summary[:300]})
        if not ok:
            self.errors.append({"time": now(), "tool": tool, "input": tool_input, "error": result_summary[:300]})

    def save(self) -> None:
        data = asdict(self)
        data["run_dir"] = str(self.run_dir)
        (self.run_dir / "state.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def snapshot_for_llm(self) -> str:
        """A short view of state that is shown to the model on every turn."""
        plan = "\n".join(f"  [{p['status']}] {p['step']}" for p in self.plan) or "  (no plan yet)"
        verified = "not run" if self.verification is None else (
            "PASSED" if self.verification.get("passed") else "FAILED")
        return (f"CURRENT PLAN:\n{plan}\n"
                f"FACTS RECORDED: {json.dumps(self.facts) if self.facts else 'none'}\n"
                f"APPROVALS: {json.dumps(self.approvals) if self.approvals else 'none'}\n"
                f"FAILED ACTIONS SO FAR: {len(self.errors)}\n"
                f"VERIFICATION: {verified}")
