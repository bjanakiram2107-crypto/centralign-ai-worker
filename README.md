# Autonomous AI Accounts-Payable Worker

A prototype AI worker that takes a plain-language request such as

> "Process the latest Acme invoice."

and completes it end to end on a computer. It finds the right invoice on the shared drive, reads it, and finds
the vendor in the internal accounting web app (operating a real browser). It checks the invoice against the
purchase order and goods receipt, enters it, and asks a human to approve the payment. Then it independently
verifies that the accounting system now says what the invoice says, and reports back with evidence.

It was built for the CentrAlign AI *AI Engineering Intern* problem statement ("Autonomous AI Task Worker").
Everything runs against a **sandbox company**: fake invoices, a fake accounting app, and no real credentials.

---

## 1. Setup and run

Requirements: Python 3.10+, an Anthropic API key.

```bash
git clone <this repo> && cd centralign-ai-worker
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env                 # then put your ANTHROPIC_API_KEY in .env
python scripts/setup_company.py      # creates the sandbox invoices + accounting database
```

Run the agent (the accounting app starts automatically in the background):

```bash
python run_agent.py --show-browser "Process the latest Acme invoice."
```

You can watch the browser while the agent works. When it asks for approval in the terminal, type `approve` or
`reject`. Each run writes `runs/<timestamp>/report.md`, `state.json` and screenshots.

To look at the accounting app yourself: `python -m app.accounting_app.app`, then open http://127.0.0.1:5055.

### Demo scenarios

| Command | What it shows |
|---|---|
| `python run_agent.py --reset --show-browser "Process the latest Acme invoice."` | Full flow: picks the newest of two Acme invoices, recovers from a failed vendor search ("Acme Corp" vs "Acme Corporation"), recovers from a validation error (cost center required), three-way match passes, policy requires approval (above INR 50,000), human approves, payment scheduled, GL posted, verified. |
| `python run_agent.py --reset --chaos "Process the latest Acme invoice."` | The accounting system silently saves the wrong amount. Verification catches it, and the agent edits the record and re-verifies **before** paying. |
| `python run_agent.py --reset "Process the Globex invoice."` | The three-way match fails (10 invoiced, only 8 received). The agent does not pay; it enters the invoice, puts it on hold with the reason, and reports. |
| `python run_agent.py --reset "Process the latest Acme invoice."` and answer `reject` | Human-in-the-loop: the rejection is respected. Payment is never scheduled, and the invoice is put on hold with your note. |
| `python run_agent.py --reset "Which invoices on the shared drive have not been entered in AcmeBooks yet?"` | **Generalization:** a different task with the same tools and the same loop, and no code changes. |

`--reset` restores the sandbox data so a scenario can be repeated.

### Tests

```bash
pytest -q
```

The tests drive the **real** runtime, tools, guardrails, browser and accounting app with a scripted stand-in for
the LLM (`tests/fake_llm.py`), so they need no API key and are deterministic. They cover the full flow, the chaos
recovery, the three-way-match hold, invoice validation, and the guardrails.

---

## 2. Architecture

```
            ┌──────────────────────────────── runtime (Python, deterministic) ───────────────────────────────┐
 user goal ─►  Agent loop (agent/agent.py)                                                                    │
            │    1. send goal + history + AgentState snapshot to the LLM                                      │
            │    2. LLM picks a tool call  ───────────────►  Executor (agent/executor.py)                      │
            │                                                 ├─ guardrails: plan-first, approval, verify-first │
            │                                                 └─ run tool, log action, save state              │
            │    3. observation (result or error) ◄──────────  Tools                                          │
            │    4. Recovery policy (agent/recovery.py) adds advice / escalates                               │
            │    5. repeat until finish (allowed only if verification passed)                                 │
            └───────────────┬─────────────────────┬────────────────────┬──────────────────┬──────────────────┘
                            │                     │                    │                  │
                  tools/filesystem.py      tools/browser.py     tools/accounting.py   tools/human.py
                  shared drive (PDFs,      Playwright Chromium  invoice validation,   approval and
                  policy YAML)             ─► AcmeBooks web app three-way match,      clarification
                                                                approval policy        (terminal)
                                                     agent/verifier.py ─► AcmeBooks JSON API (independent channel)
```

More detail, including how each CentrAlign criterion maps to code: [docs/architecture.md](docs/architecture.md).

**The loop:** GOAL → PLAN (`update_plan`) → ACT (a tool) → OBSERVE (the result or error) → ADAPT (next tool,
or a revised plan) → VERIFY (`verify_invoice_entry`) → FINISH (`finish`, with evidence).

**Who decides what:**

| Decision | Made by | Why |
|---|---|---|
| What to do next, how to recover, what to ask | LLM (Claude) | Needs judgement, and the steps are not hard-coded |
| Whether line items add up, whether invoice/PO/GRN agree, whether the saved record matches | Code | Must be exact and repeatable |
| Whether an irreversible action may run | Code (guardrail) + human | Safety cannot depend on the model following instructions |
| Whether the task can be reported "completed" | Code (guardrail) | The agent cannot claim success without passing verification |

---

## 3. Important design decisions

1. **The runtime controls, the LLM chooses.** The model only acts through narrow tools (open page, fill a labelled
   field, click by visible text, read a document). It never gets raw JavaScript, SQL or arbitrary file paths.
   Guardrails in `agent/executor.py` are enforced in code, so the model cannot talk its way past them.
2. **Explicit state (`agent/state.py`).** Goal, plan, actions, facts, approvals, errors, evidence and verification
   live in one object. It is shown to the model each turn and saved to `runs/<id>/state.json` after every step,
   so you can see exactly why the agent did what it did.
3. **Plan, then adapt.** The agent must write a plan before acting (guardrail), and it rewrites the plan when
   observations contradict it. This is a hybrid of plan-first and step-by-step (ReAct) agents: you can see the
   plan, and it still reacts to failures.
4. **A real browser, not an API shortcut.** The agent operates the accounting app through Chromium (Playwright),
   as an employee would. The verifier deliberately uses a *different* channel (the app's JSON API), so a
   browser-side mistake cannot hide itself.
5. **Verification is code, and it is mandatory.** `agent/verifier.py` compares every field of the saved record
   (and the GL entries) with the facts extracted from the invoice. A failed check sends the agent back to fix
   the record. Irreversible actions are blocked until the latest save is verified.
6. **Company rules live in data, not code.** Approval limit, approver role and match tolerances are in
   `data/policy/company_policy.yaml`. Change the limit and the agent's behaviour changes, with no code changes.
7. **Bounded recovery.** The model reasons about each failure. `agent/recovery.py` flags repeated identical
   failures and escalates to a human after 8 failures, so a confused agent cannot loop forever.
8. **No framework.** The loop is about 100 lines of plain Python on the Anthropic SDK, so every step can be read,
   debugged and changed. A framework would hide exactly the parts this task is about.

---

## 4. Known limitations

- One workflow family (accounts-payable invoices) and one internal app. Supporting a new app means pointing the browser
  at it (one allowed site per run) and maybe adding a domain check, but the browser and file tools are generic.
- Invoices must be text PDFs; there is no OCR for scanned images.
- Invoices have a single line item in the three-way match logic, and there is no multi-currency, tax or
  partial-delivery handling.
- Payment is simulated as a status change plus GL entries. No bank or payment rail is touched.
- The human is reached through the terminal; there is no web UI, email or Slack approval.
- State is saved to disk for inspection, but a crashed run cannot yet be resumed automatically.
- The agent is only as fast as the LLM: a full run takes a few minutes and roughly 25–40 model calls.

## 5. What I would build next

1. **Resume and background execution:** reload `state.json` and continue a run; a task queue for many invoices.
2. **Learning from feedback:** store human corrections (e.g. "Acme Corp is vendor V-001") in persistent company
   memory, so the next run skips the failed search.
3. **More connectors** behind the same tool interface: an email inbox for invoice receipt, OCR, a real ERP sandbox.
4. **Approval routing UI** (web or Slack) with an audit log, instead of the terminal prompt.
5. **Evaluation suite:** a set of tasks with known correct outcomes, run on every change, to measure reliability.

## 6. Assumptions

- "Latest invoice" means the most recent *invoice date*, not file name or file time.
- The legal vendor name in the accounting system can differ from the name printed on the invoice.
- Policy: above INR 50,000 needs Finance Manager approval; any PO/GRN mismatch means hold, never pay.
- Scheduling a payment counts as the irreversible action; entering an invoice (status "Pending approval") is
  reversible because it can still be edited or put on hold.
- The sandbox company, people, vendors and amounts are all fictional.

## 7. Models, APIs, frameworks and external components

| Component | Used for |
|---|---|
| Claude (`claude-opus-5-5`) via the Anthropic API, Python SDK `anthropic` | The agent's reasoning and tool selection (adaptive thinking, effort `medium`, prompt caching, server-side refusal fallback) |
| Playwright + Chromium | Operating the accounting web app in a real browser |
| Flask + SQLite | The simulated internal accounting system (AcmeBooks) |
| pypdf / reportlab | Reading invoice PDFs / generating the sandbox invoices |
| rich | Terminal trace and prompts |
| PyYAML | Company policy file |
| pytest | Tests |

No agent framework is used. AI coding tools (Claude) helped write this code, which CentrAlign's brief allows.

## Repository layout

```
agent/      agent.py (loop) · planner.py (prompt, plan/finish) · state.py · executor.py (guardrails)
            verifier.py · recovery.py · llm.py · ui.py
tools/      filesystem.py · browser.py · accounting.py · human.py · base.py
app/        accounting_app/app.py (the simulated AcmeBooks system)
data/       invoices/ (sandbox PDFs) · policy/company_policy.yaml · accounting.db (generated)
scripts/    setup_company.py (creates/resets the sandbox)
tests/      end-to-end tests with a scripted LLM
docs/       architecture.md
demo/       demo_script.md
```
