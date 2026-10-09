"""Independent verification: did the requested outcome actually happen?

The verifier does NOT trust the LLM or the browser. It compares
  (a) the invoice facts recorded in agent state (extracted from the PDF and validated), with
  (b) what the accounting system's database now says (read through its JSON API).
Pass or fail is decided by code. The agent cannot report success unless this passes
(enforced in agent/executor.py).
"""

import json

from tools.accounting import system_of_record
from tools.base import Tool, ToolContext, ToolError, schema


def verify_invoice_entry(ctx: ToolContext, expected_status: str) -> str:
    inv = ctx.state.facts.get("invoice")
    vendor = ctx.state.facts.get("vendor")
    if not inv or not vendor:
        raise ToolError("Nothing to verify yet: record the invoice facts and the vendor match first.")
    record = system_of_record(ctx, f"/api/invoices/{inv['invoice_number']}")
    if not record.get("found"):
        result = {"passed": False, "rows": [], "issues": [f"{inv['invoice_number']} is not in the accounting system"]}
        ctx.state.verification = result
        return "VERIFICATION FAILED\n" + json.dumps(result, indent=2)

    checks = [
        ("vendor_id", vendor["vendor_id"], record["vendor_id"]),
        ("invoice_number", inv["invoice_number"], record["invoice_number"]),
        ("invoice_date", inv["invoice_date"], record["invoice_date"]),
        ("due_date", inv["due_date"], record["due_date"]),
        ("po_number", inv["po_number"], record["po_number"]),
        ("amount", inv["total"], record["amount"]),
        ("status", expected_status, record["status"]),
    ]
    if expected_status == "Scheduled for payment":
        debit = sum(e["debit"] for e in record["gl_entries"])
        credit = sum(e["credit"] for e in record["gl_entries"])
        checks.append(("GL debits", inv["total"], debit))
        checks.append(("GL credits", inv["total"], credit))
    rows = [{"field": f, "invoice_document": exp, "accounting_system": got, "match": exp == got}
            for f, exp, got in checks]
    issues = [f"{r['field']}: expected {r['invoice_document']!r}, system has {r['accounting_system']!r}"
              for r in rows if not r["match"]]
    result = {"passed": not issues, "expected_status": expected_status, "rows": rows, "issues": issues}
    ctx.state.verification = result
    table = "\n".join(f"  {'OK ' if r['match'] else 'BAD'} {r['field']:<15} document={r['invoice_document']!s:<24}"
                      f" system={r['accounting_system']}" for r in rows)
    if issues:
        return ("VERIFICATION FAILED - the system does not match the invoice. Fix the record (e.g. 'Edit invoice') "
                "and verify again.\n" + table)
    return "VERIFICATION PASSED\n" + table


TOOLS = [
    Tool("verify_invoice_entry",
         "Independently re-read the invoice from the accounting system's database and compare every field with the "
         "recorded invoice facts. Required before you can finish with outcome 'completed' or 'on_hold' after changing "
         "records.",
         schema({"expected_status": {"type": "string",
                                     "enum": ["Pending approval", "Scheduled for payment", "On hold"]}}),
         verify_invoice_entry),
]
