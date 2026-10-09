"""Accounts-payable checks that are done in plain code, not by the LLM.

The LLM decides *when* to run these. The code decides *whether the numbers agree*.
That split is deliberate: arithmetic and comparisons must be exact and repeatable.
"""

import json
import urllib.error
import urllib.request
from datetime import datetime

import yaml

from tools.base import Tool, ToolContext, ToolError, schema


def system_of_record(ctx: ToolContext, path: str) -> dict:
    """Read from the accounting app's JSON API (an independent channel from the browser)."""
    url = ctx.config.app_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"found": False}
        raise ToolError(f"Accounting system returned HTTP {e.code} for {path}")
    except urllib.error.URLError as e:
        raise ToolError(f"Accounting system unreachable ({e.reason}). Is it running?")


def load_policy(ctx: ToolContext) -> dict:
    return yaml.safe_load((ctx.config.data_dir / "policy" / "company_policy.yaml").read_text(encoding="utf-8"))


def _iso_date(value: str) -> str:
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ToolError(f"Could not understand the date '{value}'. Use YYYY-MM-DD.")


# --------------------------------------------------------------------------
def record_invoice_facts(ctx: ToolContext, source_file: str, vendor_name_on_invoice: str, invoice_number: str,
                         invoice_date: str, due_date: str, po_number: str, line_items: list, total: int) -> str:
    """Stage 2 (extraction & validation): store what the LLM read from the invoice, after code checks it."""
    problems = []
    line_sum = 0
    for li in line_items:
        line_sum += int(li["quantity"]) * int(li["unit_price"])
    if line_sum != int(total):
        problems.append(f"line items add up to {line_sum}, but the invoice total says {total}")
    for name, value in [("invoice_number", invoice_number), ("po_number", po_number)]:
        if not value.strip():
            problems.append(f"{name} is missing")
    if problems:
        raise ToolError("Invoice failed validation: " + "; ".join(problems) +
                        ". Re-read the document; if the invoice itself is wrong, put it on hold and tell a human.")
    facts = {
        "source_file": source_file, "vendor_name_on_invoice": vendor_name_on_invoice,
        "invoice_number": invoice_number.strip(), "invoice_date": _iso_date(invoice_date),
        "due_date": _iso_date(due_date), "po_number": po_number.strip(),
        "line_items": line_items, "total": int(total),
    }
    ctx.state.facts["invoice"] = facts
    return "Validated and recorded invoice facts:\n" + json.dumps(facts, indent=2)


def record_vendor_match(ctx: ToolContext, vendor_id: str, legal_name: str, how_found: str) -> str:
    """Remember which vendor record in the accounting system the invoice belongs to."""
    ctx.state.facts["vendor"] = {"vendor_id": vendor_id, "legal_name": legal_name, "how_found": how_found}
    return f"Recorded vendor {legal_name} ({vendor_id})."


def three_way_match(ctx: ToolContext) -> str:
    """Stage 3: compare invoice (from state) vs PO vs GRN (from the system of record)."""
    inv = ctx.state.facts.get("invoice")
    if not inv:
        raise ToolError("No invoice facts recorded yet. Call record_invoice_facts first.")
    policy = load_policy(ctx)["three_way_match"]
    po = system_of_record(ctx, f"/api/purchase-orders/{inv['po_number']}")
    if not po.get("found"):
        result = {"passed": False, "issues": [f"PO {inv['po_number']} does not exist in the accounting system"]}
        ctx.state.facts["three_way_match"] = result
        return json.dumps(result, indent=2)
    received = sum(g["qty_received"] for g in po["goods_receipts"])
    invoiced_qty = sum(int(li["quantity"]) for li in inv["line_items"])
    invoiced_price = int(inv["line_items"][0]["unit_price"])
    rows, issues = [], []

    def compare(label, invoice_value, other_label, other_value, tolerance):
        ok = abs(invoice_value - other_value) <= tolerance * max(other_value, 1)
        rows.append({"check": label, "invoice": invoice_value, other_label: other_value, "ok": ok})
        if not ok:
            issues.append(f"{label}: invoice says {invoice_value}, {other_label} says {other_value}")

    compare("quantity vs PO", invoiced_qty, "po", po["qty"], policy["quantity_tolerance"])
    compare("unit price vs PO", invoiced_price, "po", po["unit_price"], policy["price_tolerance"])
    compare("quantity vs goods received", invoiced_qty, "grn", received, policy["quantity_tolerance"])
    if ctx.state.facts.get("vendor") and ctx.state.facts["vendor"]["vendor_id"] != po["vendor_id"]:
        issues.append(f"PO belongs to vendor {po['vendor_id']}, not {ctx.state.facts['vendor']['vendor_id']}")
    result = {"passed": not issues, "rows": rows, "issues": issues, "cost_center": po["cost_center"]}
    ctx.state.facts["three_way_match"] = result
    if not issues:
        return "THREE-WAY MATCH PASSED\n" + json.dumps(result, indent=2)
    return ("THREE-WAY MATCH FAILED. Company policy: on mismatch, put the invoice on hold and do not pay.\n"
            + json.dumps(result, indent=2))


def check_approval_policy(ctx: ToolContext) -> str:
    """Stage 4: decide, from the policy file, whether a human must approve payment."""
    inv = ctx.state.facts.get("invoice")
    if not inv:
        raise ToolError("No invoice facts recorded yet.")
    policy = load_policy(ctx)["approval"]
    limit = policy["auto_approve_limit"]
    key = f"schedule_payment:{inv['invoice_number']}"
    if inv["total"] <= limit:
        if not ctx.state.approved_for(key):
            ctx.state.approvals.append({"action_key": key, "decision": "approved", "by": "policy (auto)",
                                        "reason": f"amount {inv['total']} <= limit {limit}"})
        return f"Amount INR {inv['total']:,} is within the auto-approval limit (INR {limit:,}). No human approval needed."
    return (f"Amount INR {inv['total']:,} is above the auto-approval limit (INR {limit:,}). "
            f"The {policy['approver_role']} must approve before payment is scheduled. Use request_approval.")


TOOLS = [
    Tool("record_invoice_facts",
         "Record the fields you extracted from an invoice document. Code validates them (line items must add up "
         "to the total). Dates may be in the invoice's own format; they are normalised to YYYY-MM-DD.",
         schema({
             "source_file": {"type": "string"},
             "vendor_name_on_invoice": {"type": "string"},
             "invoice_number": {"type": "string"},
             "invoice_date": {"type": "string"},
             "due_date": {"type": "string"},
             "po_number": {"type": "string"},
             "line_items": {"type": "array", "items": schema({
                 "description": {"type": "string"}, "quantity": {"type": "integer"},
                 "unit_price": {"type": "integer"}})},
             "total": {"type": "integer"},
         }), record_invoice_facts),
    Tool("record_vendor_match",
         "Record which vendor record in the accounting system matches the invoice's vendor, and how you found it.",
         schema({"vendor_id": {"type": "string"}, "legal_name": {"type": "string"}, "how_found": {"type": "string"}}),
         record_vendor_match),
    Tool("three_way_match",
         "Compare the recorded invoice against its Purchase Order and Goods Receipt in the accounting system. "
         "Run this before entering or paying an invoice.", schema({}), three_way_match),
    Tool("check_approval_policy",
         "Check the company's approval policy for the recorded invoice: is human approval needed before payment?",
         schema({}), check_approval_policy),
]
