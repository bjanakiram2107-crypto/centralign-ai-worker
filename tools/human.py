"""Human-in-the-loop tools: ask for approval or clarification.

The actual prompt is handled by a `human` object (terminal by default, scripted in tests),
so the agent's logic does not depend on how the human is reached.
"""

from tools.base import Tool, ToolContext, ToolError, schema


def request_approval(ctx: ToolContext, action: str, invoice_number: str, summary: str) -> str:
    if action != "schedule_payment":
        raise ToolError("Only 'schedule_payment' needs approval in this prototype.")
    inv = ctx.state.facts.get("invoice")
    if not inv or inv["invoice_number"] != invoice_number:
        raise ToolError("Record the invoice facts for this invoice before asking for approval.")
    decision, note = ctx.human.approve(summary)
    ctx.state.approvals.append({"action_key": f"{action}:{invoice_number}", "decision": decision,
                                "by": "human", "note": note})
    if decision == "approved":
        return f"APPROVED by human. Note: {note or '-'}. You may now schedule the payment."
    return (f"REJECTED by human. Note: {note or '-'}. Do not schedule payment. Put the invoice on hold with the "
            "reason, then finish with outcome 'on_hold'.")


def ask_user(ctx: ToolContext, question: str) -> str:
    return "User answered: " + ctx.human.ask(question)


TOOLS = [
    Tool("request_approval",
         "Ask the human approver to approve an irreversible action. Include the key facts and checks in the summary.",
         schema({"action": {"type": "string", "enum": ["schedule_payment"]}, "invoice_number": {"type": "string"},
                 "summary": {"type": "string"}}), request_approval),
    Tool("ask_user",
         "Ask the user a clarifying question when the request is ambiguous or you cannot safely continue.",
         schema({"question": {"type": "string"}}), ask_user),
]
