"""End-to-end tests: real accounting app, real browser, real tools and guardrails, scripted LLM."""

import json
import urllib.request

from agent.agent import Agent
from tests.fake_llm import FakeLLM, ScriptedHuman, tool_call as t

PLAN = t("update_plan", reason="initial plan", steps=[{"step": "Find and read the latest Acme invoice", "status": "doing"}])
FACTS = t("record_invoice_facts", source_file="invoices/Acme_Invoice_002.pdf", vendor_name_on_invoice="Acme Corp",
          invoice_number="INV-ACME-002", invoice_date="21 Sep 2026", due_date="21 Oct 2026", po_number="PO-1002",
          line_items=[{"description": "Standing desk", "quantity": 10, "unit_price": 8450}], total=84500)


def fill_invoice_form(amount="84500"):
    return [t("browser_open", path="/invoices/new"),
            t("browser_fill", field_label="Vendor ID", value="V-001"),
            t("browser_fill", field_label="Invoice Number", value="INV-ACME-002"),
            t("browser_fill", field_label="Invoice Date", value="2026-09-21"),
            t("browser_fill", field_label="Due Date", value="2026-10-21"),
            t("browser_fill", field_label="PO Number", value="PO-1002"),
            t("browser_fill", field_label="Amount (INR)", value=amount)]


def api(base, path):
    return json.loads(urllib.request.urlopen(base + path).read())


def test_full_acme_flow_with_recovery_approval_and_verification(fresh_company, browser, config):
    script = [
        PLAN,
        t("list_files", folder="invoices"),
        t("read_document", path="invoices/Acme_Invoice_002.pdf"),
        FACTS,
        t("browser_open", path="/vendors?q=Acme Corp"),           # exact search fails: legal name differs
        t("browser_open", path="/vendors"),                       # recovery: list all vendors
        t("record_vendor_match", vendor_id="V-001", legal_name="Acme Corporation", how_found="listed all vendors"),
        t("three_way_match"),
        *fill_invoice_form(),
        t("browser_click", text="Save invoice"),                  # fails: cost center required above 50,000
        t("browser_fill", field_label="Cost Center", value="CC-OPS-04"),
        t("browser_click", text="Save invoice"),
        t("browser_click", text="Schedule payment"),              # blocked: no approval yet
        t("check_approval_policy"),
        t("request_approval", action="schedule_payment", invoice_number="INV-ACME-002", summary="Pay INR 84,500?"),
        t("browser_click", text="Schedule payment"),              # blocked: saved record not verified yet
        t("verify_invoice_entry", expected_status="Pending approval"),
        t("browser_click", text="Schedule payment"),
        t("finish", outcome="completed", summary="done"),         # blocked: payment changed the record
        t("verify_invoice_entry", expected_status="Scheduled for payment"),
        t("finish", outcome="completed", summary="Entered and scheduled INV-ACME-002, verified."),
    ]
    llm, human = FakeLLM(script), ScriptedHuman("approved")
    state = Agent(config, llm, browser, human).run("Process the latest Acme invoice.")

    results = {i: (a["tool"], a["ok"], a["result"]) for i, a in enumerate(state.actions)}
    failed = [(tool, res.splitlines()[0]) for tool, ok, res in results.values() if not ok]
    assert any("Cost Center is required" in r for _, r in failed)
    assert any("no granted approval" in r for _, r in failed)
    assert any("not been verified" in r for _, r in failed)
    assert any("no passing verification" in r for _, r in failed)
    assert "No vendors found" in state.actions[4]["result"]

    assert state.status == "completed"
    assert state.verification["passed"]
    record = api(fresh_company, "/api/invoices/INV-ACME-002")
    assert record["status"] == "Scheduled for payment" and record["amount"] == 84500
    assert sum(e["debit"] for e in record["gl_entries"]) == 84500
    assert len(human.approval_requests) == 1
    assert (state.run_dir / "report.md").exists() and state.evidence


def test_chaos_bad_save_is_caught_and_fixed_before_payment(fresh_company, browser, config):
    import app.accounting_app.app as accounting
    accounting.CHAOS = True
    script = [
        PLAN, FACTS,
        t("record_vendor_match", vendor_id="V-001", legal_name="Acme Corporation", how_found="vendor list"),
        *fill_invoice_form(),
        t("browser_fill", field_label="Amount (INR)", value="84500"),
        t("browser_click", text="Save invoice"),
        t("browser_fill", field_label="Cost Center", value="CC-OPS-04"),
        t("browser_click", text="Save invoice"),                  # chaos: stored amount is wrong
        t("verify_invoice_entry", expected_status="Pending approval"),   # FAILS: amount mismatch
        t("browser_click", text="Edit invoice"),
        t("browser_fill", field_label="Amount (INR)", value="84500"),
        t("browser_click", text="Update invoice"),
        t("verify_invoice_entry", expected_status="Pending approval"),   # passes
        t("finish", outcome="on_hold", summary="Saved and verified; awaiting payment run."),
    ]
    state = Agent(config, FakeLLM(script), browser, ScriptedHuman()).run("Enter the latest Acme invoice.")
    verify_results = [a for a in state.actions if a["tool"] == "verify_invoice_entry"]
    assert verify_results[0]["result"].startswith("VERIFICATION FAILED")
    assert verify_results[1]["result"].startswith("VERIFICATION PASSED")
    assert api(fresh_company, "/api/invoices/INV-ACME-002")["amount"] == 84500
    assert state.status == "on_hold"


def test_globex_three_way_mismatch_goes_on_hold(fresh_company, browser, config):
    script = [
        PLAN,
        t("record_invoice_facts", source_file="invoices/Globex_Invoice_004.pdf", vendor_name_on_invoice="Globex",
          invoice_number="INV-GLX-004", invoice_date="25 Sep 2026", due_date="25 Oct 2026", po_number="PO-2004",
          line_items=[{"description": "Monitor arm", "quantity": 10, "unit_price": 3120}], total=31200),
        t("record_vendor_match", vendor_id="V-002", legal_name="Globex Industries", how_found="vendor list"),
        t("three_way_match"),
    ]
    llm = FakeLLM(script)
    state = Agent(config, llm, browser, ScriptedHuman()).run("Process the Globex invoice.")
    match = state.facts["three_way_match"]
    assert not match["passed"]
    assert any("grn says 8" in i for i in match["issues"])


def test_invalid_invoice_totals_are_rejected(fresh_company, browser, config):
    script = [PLAN, t("record_invoice_facts", source_file="x.pdf", vendor_name_on_invoice="Acme Corp",
                      invoice_number="INV-X", invoice_date="2026-09-21", due_date="2026-10-21", po_number="PO-1002",
                      line_items=[{"description": "desk", "quantity": 10, "unit_price": 8450}], total=85000)]
    state = Agent(config, FakeLLM(script), browser, ScriptedHuman()).run("x")
    assert "line items add up to 84500" in state.actions[1]["result"]


def test_plan_first_and_file_sandbox_guardrails(fresh_company, browser, config):
    script = [t("list_files", folder="invoices"), PLAN, t("read_document", path="../../etc/passwd"),
              t("browser_open", path="https://example.com/")]
    state = Agent(config, FakeLLM(script), browser, ScriptedHuman()).run("x")
    assert "make a plan first" in state.actions[0]["result"]
    assert "outside the company data folder" in state.actions[2]["result"]
    assert "outside the allowed internal app" in state.actions[3]["result"]


def test_human_rejection_is_respected(fresh_company, browser, config):
    script = [
        PLAN, FACTS,
        t("record_vendor_match", vendor_id="V-001", legal_name="Acme Corporation", how_found="vendor list"),
        *fill_invoice_form(),
        t("browser_click", text="Save invoice"),
        t("browser_fill", field_label="Cost Center", value="CC-OPS-04"),
        t("browser_click", text="Save invoice"),
        t("check_approval_policy"),
        t("request_approval", action="schedule_payment", invoice_number="INV-ACME-002", summary="Pay?"),
        t("verify_invoice_entry", expected_status="Pending approval"),
        t("browser_click", text="Schedule payment"),              # blocked: approval was rejected
        t("browser_fill", field_label="Hold reason", value="Rejected by Finance Manager"),
        t("browser_click", text="Put on hold"),
        t("verify_invoice_entry", expected_status="On hold"),
        t("finish", outcome="on_hold", summary="Approval rejected; invoice on hold."),
    ]
    state = Agent(config, FakeLLM(script), browser, ScriptedHuman("rejected")).run("Process the latest Acme invoice.")
    assert any("no granted approval" in a["result"] for a in state.actions if not a["ok"])
    record = api(fresh_company, "/api/invoices/INV-ACME-002")
    assert record["status"] == "On hold" and record["gl_entries"] == []
    assert state.status == "on_hold" and state.verification["passed"]
