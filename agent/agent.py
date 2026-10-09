"""The agent loop: GOAL -> PLAN -> ACT -> OBSERVE -> ADAPT -> VERIFY -> FINISH.

Each turn:
  1. The LLM sees the conversation so far plus a snapshot of AgentState and picks the next tool call(s).
  2. The executor checks guardrails and runs the tool.
  3. The result (or the error) goes back to the LLM as an observation, and the loop repeats.
The loop ends when the agent calls `finish` (and the guardrails allow it), when the step budget runs out,
or when the recovery policy escalates to a human after too many failures.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import anthropic

from agent import ui
from agent.executor import Executor
from agent.planner import SYSTEM_PROMPT
from agent.recovery import RecoveryPolicy
from agent.state import AgentState
from tools.base import ToolContext
from tools.memory import CompanyMemory


# Errors that mean "the model is temporarily unreachable", not "the request is wrong".
TRANSIENT_LLM_ERRORS = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)


@dataclass
class Config:
    app_url: str = "http://127.0.0.1:5055"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    runs_dir: Path = Path(__file__).resolve().parent.parent / "runs"
    memory_path: Path = Path(__file__).resolve().parent.parent / "memory" / "company_memory.json"
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_steps: int = 45


def new_run_dir(runs_dir: Path) -> Path:
    run_dir = runs_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


class Agent:
    def __init__(self, config: Config, llm, browser, human):
        self.config = config
        self.llm = llm
        self.browser = browser
        self.human = human

    def run(self, goal: str) -> AgentState:
        state = AgentState(goal=goal, run_dir=new_run_dir(self.config.runs_dir))
        ctx = ToolContext(state=state, config=self.config, browser=self.browser, human=self.human)
        executor = Executor(ctx)
        recovery = RecoveryPolicy()
        ui.show_goal(goal, state.run_dir)
        company_memory = CompanyMemory(self.config.memory_path).as_prompt()
        ui.show_memory(company_memory)

        messages = [{"role": "user", "content": f"TASK FROM YOUR COLLEAGUE: {goal}\n\n{company_memory}"}]
        nudges = 0
        step = 0
        while state.status == "running":
            if step >= self.config.max_steps:
                state.status, state.summary = "failed", f"Stopped: step budget of {self.config.max_steps} used up."
                break
            try:
                response = self.llm.create(SYSTEM_PROMPT, executor.api_tools(), messages)
            except TRANSIENT_LLM_ERRORS as e:
                # Retries are exhausted. Stop safely and say why, instead of crashing mid-task.
                state.status = "on_hold"
                state.summary = (f"Stopped safely: the AI model could not be reached after several retries "
                                 f"({type(e).__name__}). Nothing after step {step} was attempted. "
                                 f"Check the network and run the task again.")
                break
            if response.stop_reason == "refusal":
                state.status, state.summary = "failed", "The model declined to continue this task."
                break
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = []
            for block in response.content:
                if block.type == "thinking":
                    ui.show_thinking(getattr(block, "thinking", "") or "")
                elif block.type == "text":
                    ui.show_text(block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)

            if not tool_uses:
                # The model stopped talking without calling `finish`. Nudge it (bounded).
                nudges += 1
                if nudges > 2:
                    state.status, state.summary = "failed", "Agent stopped without finishing the task."
                    break
                messages.append({"role": "user", "content": "Continue the task with your tools. "
                                 "When you are done, call the finish tool."})
                continue

            results = []
            for tu in tool_uses:
                step += 1
                ui.show_tool_call(step, tu.name, tu.input)
                ok, result = executor.execute(tu.name, tu.input)
                ui.show_tool_result(ok, result)
                if tu.name == "update_plan" and ok:
                    ui.show_plan(state.plan)
                if ok:
                    recovery.reset()
                else:
                    advice = recovery.advice_after_failure(state, tu.name, tu.input)
                    if advice == "ESCALATE":
                        state.status = "on_hold"
                        state.summary = (f"Escalated to a human after {len(state.errors)} failed actions. "
                                         f"Last error: {result[:200]}")
                    else:
                        ui.show_recovery(advice)
                        result = result + "\n\n" + advice
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result, "is_error": not ok})

            # Observations go back to the model together with a fresh view of the agent's state.
            messages.append({"role": "user", "content": results + [
                {"type": "text", "text": "AGENT STATE (maintained by the runtime):\n" + state.snapshot_for_llm()}]})

        state.save()
        write_report(state)
        ui.show_final(state)
        return state


def write_report(state: AgentState) -> None:
    """A human-readable record of the run: the evidence that the task was (or was not) done."""
    lines = [f"# Run report\n", f"**Goal:** {state.goal}\n", f"**Outcome:** {state.status}\n",
             f"## Summary\n\n{state.summary}\n", "## Final plan\n"]
    lines += [f"- [{p['status']}] {p['step']}" for p in state.plan]
    lines.append("\n## Verification\n")
    if state.verification:
        lines.append("Passed: **" + str(state.verification["passed"]) + "**\n")
        lines.append("| Field | Invoice document | Accounting system | Match |\n|---|---|---|---|")
        lines += [f"| {r['field']} | {r['invoice_document']} | {r['accounting_system']} | {'✓' if r['match'] else '✗'} |"
                  for r in state.verification.get("rows", [])]
    else:
        lines.append("Not run.")
    lines.append("\n## Approvals\n")
    lines += [f"- {json.dumps(a)}" for a in state.approvals] or ["- none"]
    lines.append("\n## Facts recorded\n\n```json\n" + json.dumps(state.facts, indent=2) + "\n```\n")
    lines.append("## Actions taken\n\n| # | Time | Tool | Input | OK | Result (start) |\n|---|---|---|---|---|---|")
    for i, a in enumerate(state.actions, 1):
        result = a["result"].splitlines()[0][:80].replace("|", "/") if a["result"] else ""
        lines.append(f"| {i} | {a['time']} | {a['tool']} | `{json.dumps(a['input'])[:80]}` | "
                     f"{'✓' if a['ok'] else '✗'} | {result} |")
    lines.append("\n## Evidence (screenshots)\n")
    lines += [f"- {Path(e).name}" for e in state.evidence] or ["- none"]
    (state.run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
