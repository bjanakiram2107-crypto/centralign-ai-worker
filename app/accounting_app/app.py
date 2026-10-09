"""AcmeBooks: a tiny simulated internal accounting system (the company's "ERP").

The AI worker operates this app through a real browser, the same way an employee would.
A small JSON API (/api/...) also exists. The agent's *verifier* and *three-way match*
use it as an independent channel to read the system of record; the agent's LLM never calls it.

Run:  python -m app.accounting_app.app      (serves http://127.0.0.1:5055)

Set ACCOUNTING_CHAOS=1 to simulate a flaky system: the first save of each invoice
silently stores a wrong amount. Used to prove that verification catches bad saves.
"""

import os
import re
import sqlite3
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template_string, request, url_for

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "accounting.db"
CHAOS = os.environ.get("ACCOUNTING_CHAOS") == "1"
COST_CENTER_REQUIRED_ABOVE = 50000

app = Flask(__name__)
_chaos_already_hit: set[str] = set()


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


LAYOUT = """<!doctype html>
<html><head><title>{{ title }} - AcmeBooks</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1d2330}
 header{background:#1f3a5f;color:#fff;padding:12px 24px}
 header a{color:#cfe0ff;margin-right:16px;text-decoration:none}
 main{padding:24px;max-width:960px}
 table{border-collapse:collapse;background:#fff;width:100%}
 th,td{border:1px solid #d6d9e0;padding:6px 10px;text-align:left}
 .error{background:#fde8e8;border:1px solid #e0a3a3;padding:10px;margin-bottom:12px}
 .ok{background:#e6f6ea;border:1px solid #9fd1ad;padding:10px;margin-bottom:12px}
 label{display:block;margin-top:10px} input{padding:6px;width:280px}
 button{margin-top:14px;padding:8px 14px}
 .danger{background:#b3261e;color:#fff;border:0}
</style></head>
<body>
<header><strong>AcmeBooks</strong> &nbsp;
 <a href="/vendors">Vendors</a><a href="/purchase-orders">Purchase Orders</a>
 <a href="/goods-receipts">Goods Receipts</a><a href="/invoices">Invoices</a>
 <a href="/invoices/new">New Invoice</a><a href="/ledger">General Ledger</a></header>
<main><h1>{{ title }}</h1>{{ body|safe }}</main></body></html>"""


def page(title: str, body: str):
    return render_template_string(LAYOUT, title=title, body=body)


def rows_table(headers, rows):
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


# ---------------------------------------------------------------- pages ----
@app.route("/")
def home():
    return page("Home", "<p>Internal accounting system for Johnny Traders Pvt Ltd (sandbox).</p>")


@app.route("/vendors")
def vendors():
    q = request.args.get("q", "").strip()
    form = ('<form method="get"><label>Search vendor by name <input name="q" value="'
            f'{q}"></label><button type="submit">Search</button></form><br>')
    if q:
        # Deliberately strict: exact legal-name match only, like many real ERPs.
        found = db().execute("SELECT * FROM vendors WHERE lower(legal_name) = lower(?)", (q,)).fetchall()
        if not found:
            return page("Vendors", form + f'<div class="error">No vendors found matching "{q}".</div>')
    else:
        found = db().execute("SELECT * FROM vendors ORDER BY vendor_id").fetchall()
    table = rows_table(["Vendor ID", "Legal name", "GSTIN"],
                       [(v["vendor_id"], v["legal_name"], v["gstin"]) for v in found])
    return page("Vendors", form + table)


@app.route("/purchase-orders")
def purchase_orders():
    pos = db().execute("SELECT * FROM purchase_orders ORDER BY po_number").fetchall()
    rows = [(f'<a href="/purchase-orders/{p["po_number"]}">{p["po_number"]}</a>', p["vendor_id"], p["item"])
            for p in pos]
    return page("Purchase Orders", rows_table(["PO number", "Vendor ID", "Item"], rows))


@app.route("/purchase-orders/<po_number>")
def purchase_order(po_number):
    p = db().execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)).fetchone()
    if not p:
        return page("Purchase Order", f'<div class="error">Purchase order {po_number} not found.</div>')
    rows = [("PO number", p["po_number"]), ("Vendor ID", p["vendor_id"]), ("Item", p["item"]),
            ("Quantity ordered", p["qty"]), ("Unit price (INR)", p["unit_price"]),
            ("Total (INR)", p["qty"] * p["unit_price"]), ("Cost center", p["cost_center"])]
    return page(f"Purchase Order {po_number}", rows_table(["Field", "Value"], rows))


@app.route("/goods-receipts")
def goods_receipts():
    grns = db().execute("SELECT * FROM goods_receipts ORDER BY grn_number").fetchall()
    rows = [(r["grn_number"], r["po_number"], r["qty_received"], r["received_on"]) for r in grns]
    return page("Goods Receipts", rows_table(["GRN", "PO number", "Qty received", "Received on"], rows))


@app.route("/invoices")
def invoices():
    invs = db().execute(
        "SELECT i.*, v.legal_name FROM invoices i JOIN vendors v USING(vendor_id) ORDER BY id").fetchall()
    rows = [(f'<a href="/invoices/{i["id"]}">{i["invoice_number"]}</a>', i["legal_name"], i["amount"],
             i["due_date"], i["status"]) for i in invs]
    return page("Invoices", rows_table(["Invoice", "Vendor", "Amount (INR)", "Due date", "Status"], rows))


INVOICE_FIELDS = [
    ("vendor_id", "Vendor ID"),
    ("invoice_number", "Invoice Number"),
    ("invoice_date", "Invoice Date"),
    ("due_date", "Due Date"),
    ("po_number", "PO Number"),
    ("amount", "Amount (INR)"),
]


def invoice_form(action: str, values: dict, error: str = "", show_cost_center: bool = False, submit="Save invoice"):
    fields = list(INVOICE_FIELDS)
    if show_cost_center:
        fields.append(("cost_center", "Cost Center"))
    inputs = "".join(
        f'<label for="{n}">{lbl}</label><input id="{n}" name="{n}" value="{values.get(n, "")}">'
        for n, lbl in fields)
    err = f'<div class="error" role="alert">{error}</div>' if error else ""
    return err + f'<form method="post" action="{action}">{inputs}<br><button type="submit">{submit}</button></form>'


def validate_invoice(form: dict) -> str:
    """Server-side business rules. Returns an error message, or '' if valid."""
    for name, label in INVOICE_FIELDS:
        if not form.get(name, "").strip():
            return f"{label} is required."
    if not db().execute("SELECT 1 FROM vendors WHERE vendor_id = ?", (form["vendor_id"],)).fetchone():
        return f"Vendor ID {form['vendor_id']} does not exist."
    for name in ("invoice_date", "due_date"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", form[name].strip()):
            return f"{dict(INVOICE_FIELDS)[name]} must be in YYYY-MM-DD format."
    if not re.fullmatch(r"\d+", form["amount"].replace(",", "").strip()):
        return "Amount (INR) must be a whole number, e.g. 84500."
    if int(form["amount"].replace(",", "")) > COST_CENTER_REQUIRED_ABOVE and not form.get("cost_center", "").strip():
        return (f"Cost Center is required for invoices above INR {COST_CENTER_REQUIRED_ABOVE:,}. "
                "Enter the cost center and save again.")
    return ""


@app.route("/invoices/new", methods=["GET", "POST"])
def new_invoice():
    if request.method == "GET":
        return page("New Invoice", invoice_form("/invoices/new", {}))
    form = {k: v.strip() for k, v in request.form.items()}
    error = validate_invoice(form)
    if not error and db().execute("SELECT 1 FROM invoices WHERE invoice_number = ?",
                                  (form["invoice_number"],)).fetchone():
        error = f"Invoice {form['invoice_number']} already exists (possible duplicate)."
    if error:
        show_cc = "Cost Center" in error or "cost_center" in form
        return page("New Invoice", invoice_form("/invoices/new", form, error, show_cost_center=show_cc)), 400
    amount = int(form["amount"].replace(",", ""))
    if CHAOS and form["invoice_number"] not in _chaos_already_hit:
        _chaos_already_hit.add(form["invoice_number"])
        amount = amount - 450  # silent data corruption on first save
    cur = db().execute(
        "INSERT INTO invoices (vendor_id, invoice_number, invoice_date, due_date, po_number, amount, cost_center,"
        " status) VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending approval')",
        (form["vendor_id"], form["invoice_number"], form["invoice_date"], form["due_date"], form["po_number"],
         amount, form.get("cost_center") or None))
    db().commit()
    return redirect(url_for("invoice_detail", invoice_id=cur.lastrowid, saved=1))


@app.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    i = db().execute("SELECT i.*, v.legal_name FROM invoices i JOIN vendors v USING(vendor_id) WHERE id = ?",
                     (invoice_id,)).fetchone()
    if not i:
        abort(404)
    msg = '<div class="ok">Invoice saved.</div>' if request.args.get("saved") else ""
    rows = [("Invoice number", i["invoice_number"]), ("Vendor", f'{i["legal_name"]} ({i["vendor_id"]})'),
            ("Invoice date", i["invoice_date"]), ("Due date", i["due_date"]), ("PO number", i["po_number"]),
            ("Amount (INR)", i["amount"]), ("Cost center", i["cost_center"] or "-"), ("Status", i["status"])]
    if i["hold_reason"]:
        rows.append(("Hold reason", i["hold_reason"]))
    actions = ""
    if i["status"] in ("Pending approval", "On hold"):
        actions += f'<p><a href="/invoices/{invoice_id}/edit">Edit invoice</a></p>'
    if i["status"] == "Pending approval":
        actions += (f'<form method="post" action="/invoices/{invoice_id}/schedule-payment">'
                    '<button class="danger" data-irreversible="true" type="submit">Schedule payment</button></form>'
                    f'<form method="post" action="/invoices/{invoice_id}/hold">'
                    '<label for="hold_reason">Hold reason</label><input id="hold_reason" name="hold_reason">'
                    '<button type="submit">Put on hold</button></form>')
    return page(f"Invoice {i['invoice_number']}", msg + rows_table(["Field", "Value"], rows) + actions)


@app.route("/invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
def edit_invoice(invoice_id):
    i = db().execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not i:
        abort(404)
    if request.method == "GET":
        return page("Edit Invoice", invoice_form(f"/invoices/{invoice_id}/edit", dict(i), show_cost_center=True,
                                                 submit="Update invoice"))
    form = {k: v.strip() for k, v in request.form.items()}
    error = validate_invoice(form)
    if error:
        return page("Edit Invoice", invoice_form(f"/invoices/{invoice_id}/edit", form, error,
                                                 show_cost_center=True, submit="Update invoice")), 400
    db().execute(
        "UPDATE invoices SET vendor_id=?, invoice_number=?, invoice_date=?, due_date=?, po_number=?, amount=?,"
        " cost_center=? WHERE id=?",
        (form["vendor_id"], form["invoice_number"], form["invoice_date"], form["due_date"], form["po_number"],
         int(form["amount"].replace(",", "")), form.get("cost_center") or None, invoice_id))
    db().commit()
    return redirect(url_for("invoice_detail", invoice_id=invoice_id, saved=1))


@app.route("/invoices/<int:invoice_id>/schedule-payment", methods=["POST"])
def schedule_payment(invoice_id):
    """Irreversible in real life (money leaves the company). Here it only changes a status and posts to the GL."""
    i = db().execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not i or i["status"] != "Pending approval":
        return page("Schedule Payment", '<div class="error">Only invoices pending approval can be scheduled.</div>'), 400
    db().execute("UPDATE invoices SET status='Scheduled for payment' WHERE id=?", (invoice_id,))
    db().executemany(
        "INSERT INTO gl_entries (invoice_id, account, debit, credit, memo) VALUES (?, ?, ?, ?, ?)",
        [(invoice_id, "5100 Office Equipment Expense", i["amount"], 0, i["invoice_number"]),
         (invoice_id, "2100 Accounts Payable", 0, i["amount"], i["invoice_number"])])
    db().commit()
    return redirect(url_for("invoice_detail", invoice_id=invoice_id))


@app.route("/invoices/<int:invoice_id>/hold", methods=["POST"])
def hold_invoice(invoice_id):
    reason = request.form.get("hold_reason", "").strip()
    if not reason:
        return page("Put on hold", '<div class="error">Hold reason is required.</div>'), 400
    db().execute("UPDATE invoices SET status='On hold', hold_reason=? WHERE id=?", (reason, invoice_id))
    db().commit()
    return redirect(url_for("invoice_detail", invoice_id=invoice_id))


@app.route("/ledger")
def ledger():
    entries = db().execute("SELECT * FROM gl_entries ORDER BY id").fetchall()
    rows = [(e["id"], e["memo"], e["account"], e["debit"], e["credit"]) for e in entries]
    return page("General Ledger", rows_table(["#", "Memo", "Account", "Debit", "Credit"], rows))


# ------------------------------------------------------- read-only JSON API --
@app.route("/api/invoices/<invoice_number>")
def api_invoice(invoice_number):
    i = db().execute("SELECT i.*, v.legal_name FROM invoices i JOIN vendors v USING(vendor_id)"
                     " WHERE invoice_number = ?", (invoice_number,)).fetchone()
    if not i:
        return jsonify({"found": False}), 404
    gl = db().execute("SELECT account, debit, credit FROM gl_entries WHERE invoice_id = ?", (i["id"],)).fetchall()
    return jsonify({"found": True, **dict(i), "gl_entries": [dict(e) for e in gl]})


@app.route("/api/purchase-orders/<po_number>")
def api_po(po_number):
    p = db().execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)).fetchone()
    grns = db().execute("SELECT * FROM goods_receipts WHERE po_number = ?", (po_number,)).fetchall()
    if not p:
        return jsonify({"found": False}), 404
    return jsonify({"found": True, **dict(p), "goods_receipts": [dict(r) for r in grns]})


@app.route("/api/invoices")
def api_invoices():
    rows = db().execute("SELECT invoice_number, vendor_id, amount, status FROM invoices").fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("ACCOUNTING_PORT", "5055")), debug=False)
