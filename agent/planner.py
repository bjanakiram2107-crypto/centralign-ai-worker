"""Planning: the system prompt that frames the task, and the tools the agent uses to plan and finish.

The plan is a visible, editable checklist stored in AgentState. The agent writes it first,
updates it as it learns, and rewrites it when an observation contradicts it (adapt).
"""

from tools.base import Tool, ToolContext, ToolError, schema

SYSTEM_PROMPT = """You are an autonomous accounts-payable worker at Johnny Traders Pvt Ltd (a sandbox company).
A colleague gives you a task in plain language. Complete it end to end using your tools, then report.

How you work:
1. Understand the intended outcome. If the request is ambiguous in a way that changes what you would do
   (for example several invoices could match), ask_user instead of guessing.
2. PLAN FIRST: call update_plan with a short checklist before any other action. Keep it current: mark steps
   done as you go, and rewrite the plan when something you observe changes what needs to happen.
3. Act with tools, one step at a time. After each result, decide the next step from what actually happened.
4. When an action fails, read the error, work out why, and try a reasonable alternative (a different search,
   a missing field, a reformatted value). Do not repeat the exact same failing action.
5. Facts that matter go into state: record_invoice_facts after reading an invoice, record_vendor_match after
   finding the vendor in the accounting system.
   COMPANY MEMORY (shown with the task) holds facts learned in earlier runs. Use it to skip known dead ends, but
   confirm a remembered fact in the system of record before acting on it. When you learn something durable
   about how this company works that would save time next run (for example the vendor's legal name in
   AcmeBooks differs from the invoice, or where a required field's value comes from), call remember with the
   evidence. Do not remember one-off invoice data. If a memory turns out wrong, remember the corrected fact.
6. Accounts payable rules: run three_way_match before entering an invoice; never pay a mismatched invoice
   (enter it and put it on hold instead, with the reason). Run check_approval_policy before scheduling payment;
   if approval is required, request_approval and wait for the decision.
7. VERIFY: after saving a record, call verify_invoice_entry before any irreversible step, and again after the
   final change. If it fails, fix the record (Edit invoice) and verify again.
8. Finish with the finish tool: outcome, a concise summary for the colleague, and the evidence.

The accounting system is the internal web app "AcmeBooks"; use the browser_* tools to operate it. Company
documents (invoices, policy) are on the shared drive; use list_files and read_document. Amounts are in INR.
"""


def update_plan(ctx: ToolContext, steps: list, reason: str) -> str:
    ctx.state.plan = [{"step": s["step"], "status": s["status"]} for s in steps]
    return "Plan updated (" + reason + "):\n" + "\n".join(f"[{s['status']}] {s['step']}" for s in ctx.state.plan)


def finish(ctx: ToolContext, outcome: str, summary: str) -> str:
    # Guardrails for finishing live in executor.check_finish(); if we got here, finishing is allowed.
    ctx.state.status = outcome
    ctx.state.summary = summary
    return f"Task finished with outcome '{outcome}'."


TOOLS = [
    Tool("update_plan",
         "Create or revise your plan as a checklist. Call this first, and again whenever your plan changes.",
         schema({"steps": {"type": "array", "items": schema({
             "step": {"type": "string"},
             "status": {"type": "string", "enum": ["todo", "doing", "done", "skipped"]}})},
             "reason": {"type": "string"}}), update_plan),
    Tool("finish",
         "End the task. outcome: completed (goal achieved and verified), on_hold (stopped safely and a human must "
         "act), failed (could not complete), cancelled (human rejected). summary: what you did, what you found, and "
         "the evidence.",
         schema({"outcome": {"type": "string", "enum": ["completed", "on_hold", "failed", "cancelled"]},
                 "summary": {"type": "string"}}), finish),
]
