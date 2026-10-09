"""Builds the simulated company: invoice PDFs, approval policy, and the accounting database.

Run once before the demo (and again any time you want to reset):
    python scripts/setup_company.py

Everything here is fake sandbox data. No real company, person or credential is used.
"""

import sqlite3
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
INVOICE_DIR = ROOT / "data" / "invoices"
DB_PATH = ROOT / "data" / "accounting.db"

# ---------------------------------------------------------------------------
# 1. Invoices that arrive in the "inbox" (Receipt stage of accounts payable)
# ---------------------------------------------------------------------------
INVOICES = [
    {
        "file": "Acme_Invoice_001.pdf",
        "vendor": "Acme Corp",  # note: short name; the accounting system uses the legal name
        "invoice_number": "INV-ACME-001",
        "invoice_date": "15 Aug 2026",
        "due_date": "14 Sep 2026",
        "po_number": "PO-1001",
        "lines": [("Ergonomic office chair", 5, 9000)],
    },
    {
        "file": "Acme_Invoice_002.pdf",
        "vendor": "Acme Corp",
        "invoice_number": "INV-ACME-002",
        "invoice_date": "21 Sep 2026",
        "due_date": "21 Oct 2026",
        "po_number": "PO-1002",
        "lines": [("Standing desk", 10, 8450)],
    },
    {
        "file": "Globex_Invoice_004.pdf",
        "vendor": "Globex",
        "invoice_number": "INV-GLX-004",
        "invoice_date": "25 Sep 2026",
        "due_date": "25 Oct 2026",
        "po_number": "PO-2004",
        "lines": [("Monitor arm", 10, 3120)],
    },
]


def inr(amount: int) -> str:
    """Formats 84500 as 'Rs. 84,500' (the PDF font has no ₹ glyph)."""
    return f"Rs. {amount:,}"


def write_invoice_pdf(inv: dict) -> None:
    path = INVOICE_DIR / inv["file"]
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, y, f"{inv['vendor']}  -  TAX INVOICE")
    c.setFont("Helvetica", 11)
    y -= 40
    for label, value in [
        ("Invoice Number", inv["invoice_number"]),
        ("Invoice Date", inv["invoice_date"]),
        ("Payment Due", inv["due_date"]),
        ("Customer PO", inv["po_number"]),
        ("Bill To", "Johnny Traders Pvt Ltd (sandbox company)"),
    ]:
        c.drawString(50, y, f"{label}: {value}")
        y -= 18
    y -= 20
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Description")
    c.drawString(300, y, "Qty")
    c.drawString(360, y, "Unit Price")
    c.drawString(460, y, "Line Total")
    c.setFont("Helvetica", 11)
    total = 0
    for desc, qty, price in inv["lines"]:
        y -= 18
        line_total = qty * price
        total += line_total
        c.drawString(50, y, desc)
        c.drawString(300, y, str(qty))
        c.drawString(360, y, inr(price))
        c.drawString(460, y, inr(line_total))
    y -= 30
    c.setFont("Helvetica-Bold", 12)
    c.drawString(360, y, f"TOTAL DUE: {inr(total)}")
    c.save()


# ---------------------------------------------------------------------------
# 2. The internal accounting system's database (the "ERP")
# ---------------------------------------------------------------------------
SCHEMA = """
DROP TABLE IF EXISTS vendors;
DROP TABLE IF EXISTS purchase_orders;
DROP TABLE IF EXISTS goods_receipts;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS gl_entries;

CREATE TABLE vendors (
    vendor_id TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    gstin TEXT
);
CREATE TABLE purchase_orders (
    po_number TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    item TEXT NOT NULL,
    qty INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    cost_center TEXT NOT NULL
);
CREATE TABLE goods_receipts (
    grn_number TEXT PRIMARY KEY,
    po_number TEXT NOT NULL,
    qty_received INTEGER NOT NULL,
    received_on TEXT NOT NULL
);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id TEXT NOT NULL,
    invoice_number TEXT NOT NULL UNIQUE,
    invoice_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    po_number TEXT NOT NULL,
    amount INTEGER NOT NULL,
    cost_center TEXT,
    status TEXT NOT NULL,
    hold_reason TEXT
);
CREATE TABLE gl_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    account TEXT NOT NULL,
    debit INTEGER NOT NULL DEFAULT 0,
    credit INTEGER NOT NULL DEFAULT 0,
    memo TEXT
);
"""


def build_database() -> None:
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO vendors VALUES (?, ?, ?)",
        [
            ("V-001", "Acme Corporation", "29ABCDE1234F1Z5"),
            ("V-002", "Globex Industries", "27FGHIJ5678K1Z2"),
            ("V-003", "Initech Ltd", "07LMNOP9012Q1Z8"),
        ],
    )
    con.executemany(
        "INSERT INTO purchase_orders VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("PO-1001", "V-001", "Ergonomic office chair", 5, 9000, "CC-ADMIN-01"),
            ("PO-1002", "V-001", "Standing desk", 10, 8450, "CC-OPS-04"),
            ("PO-2004", "V-002", "Monitor arm", 10, 3120, "CC-IT-02"),
        ],
    )
    con.executemany(
        "INSERT INTO goods_receipts VALUES (?, ?, ?, ?)",
        [
            ("GRN-1001", "PO-1001", 5, "2026-08-12"),
            ("GRN-1002", "PO-1002", 10, "2026-09-19"),
            ("GRN-2004", "PO-2004", 8, "2026-09-23"),  # only 8 of 10 arrived -> mismatch
        ],
    )
    # The older Acme invoice was already processed and paid last month.
    con.execute(
        "INSERT INTO invoices (vendor_id, invoice_number, invoice_date, due_date, po_number,"
        " amount, cost_center, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("V-001", "INV-ACME-001", "2026-08-15", "2026-09-14", "PO-1001", 45000, "CC-ADMIN-01", "Paid"),
    )
    con.executemany(
        "INSERT INTO gl_entries (invoice_id, account, debit, credit, memo) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "5100 Office Equipment Expense", 45000, 0, "INV-ACME-001"),
            (1, "2100 Accounts Payable", 0, 45000, "INV-ACME-001"),
        ],
    )
    con.commit()
    con.close()


def main() -> None:
    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    for inv in INVOICES:
        write_invoice_pdf(inv)
    build_database()
    print(f"Wrote {len(INVOICES)} invoice PDFs to {INVOICE_DIR}")
    print(f"Reset accounting database at {DB_PATH}")


if __name__ == "__main__":
    main()
