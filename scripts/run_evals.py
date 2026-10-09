"""Agent evaluation: run the REAL agent (Claude) on a fixed set of scenarios and score the outcomes.

The pytest suite checks the runtime with a scripted model. This script answers a different question:
does the actual AI worker reach the right end state on its own? Each scenario starts from a clean sandbox,
runs the agent with an automatic human (approve, reject or answer as the scenario says), and then checks
the accounting system itself, not the agent's own claims.

Usage:
    python scripts/run_evals.py                      # all scenarios (uses API credit: ~6 agent runs)
    python scripts/run_evals.py --only happy chaos   # a subset
Results: printed as a scorecard and saved to runs/evals-<timestamp>.json
"""

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVAL_PORT = 5077  # separate from the demo app on 5055, so chaos mode can be switched per scenario


class AutoHuman:
    """Answers approval requests and questions automatically, and records that it was asked."""

    def __init__(self, decision: str = "approved", answer: str = ""):
        self.decision, self.answer = decision, answer
        self.approvals: list[str] = []
        self.questions: list[str] = []

    def approve(self, summary):
        self.approvals.append(summary)
        return self.decision, "automatic evaluator"

    def ask(self, question):
        self.questions.append(question)
        return self.answer or "Use your best judgement."


@dataclass
class Scenario:
    name: str
    task: str
    shows: str
    checks: Callable  # (state, human, api) -> list of (check name, passed)
    human: dict = field(default_factory=dict)
    chaos: bool = False


def api(base: str, path: str) -> dict:
    try:
        return json.loads(urllib.request.urlopen(base + path, timeout=5).read())
    except urllib.error.HTTPError:
        return {"found": False}


def failed_verifications(state) -> int:
    return sum(1 for a in state.actions if a["tool"] == "verify_invoice_entry" and "VERIFICATION FAILED" in a["result"])


SCENARIOS = [
    Scenario("happy", "Process the latest Acme invoice.", "full AP flow with approval",
             lambda s, h, a: [
                 ("outcome is completed", s.status == "completed"),
                 ("INV-ACME-002 scheduled for payment", a("/api/invoices/INV-ACME-002").get("status") == "Scheduled for payment"),
                 ("GL entries posted", len(a("/api/invoices/INV-ACME-002").get("gl_entries", [])) == 2),
                 ("human approval was requested", len(h.approvals) == 1),
                 ("final verification passed", bool(s.verification and s.verification["passed"])),
             ]),
    Scenario("chaos", "Process the latest Acme invoice.", "detects and fixes a silently corrupted save",
             lambda s, h, a: [
                 ("outcome is completed", s.status == "completed"),
                 ("verifier caught the bad amount", failed_verifications(s) >= 1),
                 ("saved amount is correct (84500)", a("/api/invoices/INV-ACME-002").get("amount") == 84500),
                 ("scheduled for payment", a("/api/invoices/INV-ACME-002").get("status") == "Scheduled for payment"),
             ], chaos=True),
    Scenario("mismatch", "Process the Globex invoice.", "three-way match fails, so it must not pay",
             lambda s, h, a: [
                 ("outcome is on_hold", s.status == "on_hold"),
                 ("INV-GLX-004 not paid", a("/api/invoices/INV-GLX-004").get("status") != "Scheduled for payment"),
                 ("no approval requested for a bad invoice", len(h.approvals) == 0),
             ]),
    Scenario("rejection", "Process the latest Acme invoice.", "a human rejection is respected",
             lambda s, h, a: [
                 ("approval was requested", len(h.approvals) == 1),
                 ("not paid", a("/api/invoices/INV-ACME-002").get("status") != "Scheduled for payment"),
                 ("no GL entries", a("/api/invoices/INV-ACME-002").get("gl_entries", []) == []),
                 ("outcome is on_hold or cancelled", s.status in ("on_hold", "cancelled")),
             ], human={"decision": "rejected"}),
    Scenario("generalize", "Which invoices on the shared drive have not been entered in AcmeBooks yet?",
             "a different task with the same tools and no code change",
             lambda s, h, a: [
                 ("outcome is completed", s.status == "completed"),
                 ("answer names INV-ACME-002", "INV-ACME-002" in s.summary),
                 ("answer names INV-GLX-004", "INV-GLX-004" in s.summary),
                 ("nothing was written to AcmeBooks", len(a("/api/invoices")) == 1),
             ]),
    Scenario("ambiguous", "Pay the invoice.", "asks for clarification instead of guessing",
             lambda s, h, a: [
                 ("agent asked a clarifying question", len(h.questions) >= 1),
                 ("Globex (the other candidate) was not paid",
                  a("/api/invoices/INV-GLX-004").get("status") != "Scheduled for payment"),
             ], human={"answer": "The Acme Corp invoice INV-ACME-002."}),
]


def main() -> None:
    from dotenv_loader import load_env

    load_env()
    parser = argparse.ArgumentParser(description="Score the real agent on fixed scenarios")
    parser.add_argument("--only", nargs="*", help="scenario names: " + " ".join(s.name for s in SCENARIOS))
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--effort", default="medium")
    args = parser.parse_args()

    import app.accounting_app.app as accounting
    from agent.agent import Agent, Config
    from agent.llm import ClaudeLLM
    from run_agent import app_is_up, start_accounting_app
    from scripts.setup_company import main as setup_company
    from tools.browser import Browser

    base = f"http://127.0.0.1:{EVAL_PORT}"
    if not app_is_up(base):
        start_accounting_app(base)
    scenarios = [s for s in SCENARIOS if not args.only or s.name in args.only]
    results = []
    for sc in scenarios:
        setup_company()                                  # every scenario starts from the same clean company
        accounting.CHAOS = sc.chaos
        accounting._chaos_already_hit.clear()
        human = AutoHuman(**sc.human)
        with tempfile.TemporaryDirectory() as tmp:       # fresh memory: scenarios must not help each other
            config = Config(app_url=base, model=args.model, effort=args.effort,
                            memory_path=Path(tmp) / "memory.json")
            browser = Browser(base, headless=True)
            started = time.time()
            try:
                state = Agent(config, ClaudeLLM(config.model, config.effort), browser, human).run(sc.task)
            finally:
                browser.close()
        checks = sc.checks(state, human, lambda p: api(base, p))
        results.append({"scenario": sc.name, "shows": sc.shows, "task": sc.task, "outcome": state.status,
                        "steps": len(state.actions), "failed_actions": len(state.errors),
                        "seconds": round(time.time() - started),
                        "checks": [{"check": c, "passed": ok} for c, ok in checks],
                        "passed": all(ok for _, ok in checks), "report": str(state.run_dir / "report.md")})

    from rich.console import Console
    from rich.table import Table

    table = Table(title="Agent evaluation (real model)")
    for col in ("Scenario", "Result", "Steps", "Failed actions", "Seconds", "Checks"):
        table.add_column(col)
    for r in results:
        detail = "\n".join(("✓ " if c["passed"] else "✗ ") + c["check"] for c in r["checks"])
        table.add_row(r["scenario"], "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]",
                      str(r["steps"]), str(r["failed_actions"]), str(r["seconds"]), detail)
    Console().print(table)
    passed = sum(r["passed"] for r in results)
    Console().print(f"[bold]{passed}/{len(results)} scenarios passed[/bold]")
    out = ROOT / "runs" / f"evals-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    Console().print(f"Saved: {out}")


if __name__ == "__main__":
    main()
