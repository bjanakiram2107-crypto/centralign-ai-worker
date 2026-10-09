"""Executor: runs the tool the LLM picked, but only after the runtime's guardrails allow it.

Guardrails (enforced in code, so the LLM cannot talk its way past them):
  1. Plan first: no action before a plan exists.
  2. Irreversible actions (buttons marked data-irreversible) need a granted approval for that exact invoice,
     and a passing verification of the saved record (no irreversible action on unverified data).
  3. No "completed" without verification: if records were changed, the latest verification must have passed.
  4. Any record change makes the previous verification stale (it must be run again).
Every call is logged into AgentState as an action, with its result.
"""

import re

from agent import planner, verifier
from tools import accounting, browser, filesystem, human, memory
from tools.base import ToolContext, ToolError

ALL_TOOLS = {t.name: t for t in (planner.TOOLS + filesystem.TOOLS + browser.TOOLS + accounting.TOOLS
                                 + human.TOOLS + memory.TOOLS + verifier.TOOLS)}
NO_PLAN_NEEDED = {"update_plan", "ask_user"}


class Executor:
    def __init__(self, ctx: ToolContext, tool_names: list[str] | None = None):
        self.ctx = ctx
        self.tools = {n: ALL_TOOLS[n] for n in (tool_names or ALL_TOOLS)}
        self.writes_made = False

    def api_tools(self) -> list[dict]:
        return [t.to_api() for t in self.tools.values()]

    # ---------------------------------------------------------------- guardrails
    def check_guardrails(self, name: str, tool_input: dict) -> None:
        state = self.ctx.state
        if name not in self.tools:
            raise ToolError(f"Unknown tool '{name}'.")
        if not state.has_plan() and name not in NO_PLAN_NEEDED:
            raise ToolError("Guardrail: make a plan first (call update_plan) before acting.")
        if name == "browser_click" and browser.is_irreversible_click(self.ctx, tool_input["text"]):
            m = re.search(r"Invoice\s+(\S+)", browser.current_page_heading(self.ctx))
            invoice_number = m.group(1) if m else "?"
            if not state.approved_for(f"schedule_payment:{invoice_number}"):
                raise ToolError(f"Guardrail: '{tool_input['text']}' is irreversible and there is no granted approval "
                                f"for {invoice_number}. Run check_approval_policy and, if required, request_approval.")
            if self.writes_made and not (state.verification and state.verification.get("passed")):
                raise ToolError("Guardrail: the saved record has not been verified since it last changed. Never take "
                                "an irreversible action on unverified data: call verify_invoice_entry first.")
        if name == "finish" and tool_input["outcome"] in ("completed", "on_hold") and self.writes_made:
            v = state.verification
            if not v or not v.get("passed"):
                raise ToolError("Guardrail: records were changed, but there is no passing verification since the "
                                "last change. Call verify_invoice_entry (and fix the record if it fails).")

    def _is_write(self, name: str, tool_input: dict) -> bool:
        if name != "browser_click" or self.ctx.browser is None:
            return False
        loc = self.ctx.browser.find_clickable(tool_input["text"])
        if loc is None:
            return False
        return loc.evaluate("el => !!(el.form && el.form.method.toLowerCase() === 'post')")

    # ----------------------------------------------------------------- execute
    def execute(self, name: str, tool_input: dict) -> tuple[bool, str]:
        is_write = False
        try:
            self.check_guardrails(name, tool_input)
            is_write = self._is_write(name, tool_input)
            result = self.tools[name].fn(self.ctx, **tool_input)
            ok = True
        except ToolError as e:
            result, ok = str(e), False
        except Exception as e:  # a bug or an unexpected state: report it to the agent instead of crashing
            result, ok = f"Unexpected error in {name}: {type(e).__name__}: {e}", False
        if is_write:
            # The click reached the server (even if the app then showed a validation error).
            self.writes_made = True
            self.ctx.state.verification = None
        self.ctx.state.record_action(name, tool_input, ok, result)
        self.ctx.state.save()
        return ok, result
