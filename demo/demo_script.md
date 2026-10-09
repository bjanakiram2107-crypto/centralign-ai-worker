# Demo video script (about 4–5 minutes)

Before recording: `python scripts/setup_company.py`, open http://127.0.0.1:5055/invoices in a browser tab to show
the "before" state, and make the terminal font large.

## 0:00–0:30 Problem
- "Companies spend hours moving invoices from PDFs into accounting systems, checking them against purchase orders,
  getting approvals and making sure nothing was mistyped. I built an AI worker that does this end to end."
- Show `data/invoices/` (three PDFs) and the AcmeBooks invoice list (only the old, paid Acme invoice is there).

## 0:30–2:30 Autonomous run
`python run_agent.py --show-browser "Process the latest Acme invoice."`
Point out, as they happen:
1. **Plan:** the PLAN table appears before anything else.
2. **Understanding:** it reads both Acme invoices and picks the newest by invoice date.
3. **Recovery 1:** searching "Acme Corp" finds nothing because the legal name is "Acme Corporation". It lists the
   vendors and matches the right one.
4. **Three-way match:** the invoice, PO and goods receipt agree (computed in code).
5. **Recovery 2:** saving fails because a cost center is required above INR 50,000. It finds the cost center on the
   PO and saves again.
6. **Approval:** policy says above INR 50,000 needs the Finance Manager. The approval panel appears; type `approve`.

## 2:30–3:30 Verification
- The verification table: every field of the saved record compared with the invoice, plus the GL debit and
  credit, then VERIFIED.
- Optional: show `--chaos` (the app saves the wrong amount). Verification fails, and the agent edits and re-verifies
  before it is allowed to pay.
- Open `runs/<id>/report.md`: actions, approvals, verification, screenshots.

## 3:30–4:00 Architecture
- Show the diagram in `docs/architecture.md`. "The LLM chooses actions; the runtime owns state, guardrails and
  verification. The model can't pay without approval, can't pay unverified data, and can't claim success without
  a passing check, because those rules are code."

## 4:00–5:00 Engineering discussion
- Why a real browser *and* an independent API check.
- Why the policy is in YAML (show changing the limit).
- Generalization: run `"Which invoices on the shared drive have not been entered in AcmeBooks yet?"`, a
  different task with no code changes.
- Limits and next steps (from the README).
