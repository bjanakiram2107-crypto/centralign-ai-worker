"""File tools: find and read company documents (invoices, policies).

Access is limited to the company's data folder. Any path outside it is refused,
so the agent cannot read arbitrary files on the machine.
"""

from datetime import datetime
from pathlib import Path

import yaml
from pypdf import PdfReader

from tools.base import Tool, ToolContext, ToolError, schema


def _safe_path(ctx: ToolContext, relative: str) -> Path:
    root = ctx.config.data_dir.resolve()
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ToolError(f"Access denied: '{relative}' is outside the company data folder.")
    if not path.exists():
        raise ToolError(f"Not found: '{relative}'. Use list_files to see what exists.")
    return path


def list_files(ctx: ToolContext, folder: str = "") -> str:
    path = _safe_path(ctx, folder or ".")
    if not path.is_dir():
        raise ToolError(f"'{folder}' is not a folder.")
    lines = []
    for p in sorted(path.iterdir()):
        if p.name.startswith(".") or p.suffix == ".db":
            continue
        kind = "folder" if p.is_dir() else f"{p.stat().st_size} bytes"
        modified = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{p.relative_to(ctx.config.data_dir)}  ({kind}, modified {modified})")
    return "\n".join(lines) or "(empty folder)"


def read_document(ctx: ToolContext, path: str) -> str:
    p = _safe_path(ctx, path)
    if p.suffix.lower() == ".pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(p).pages)
        if not text.strip():
            raise ToolError(f"'{path}' has no text layer (scanned image). OCR is not supported in this prototype.")
        return text
    if p.suffix.lower() in (".yaml", ".yml"):
        yaml.safe_load(p.read_text(encoding="utf-8"))  # fail early on a broken policy file
        return p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".txt", ".md", ".csv", ".json"):
        return p.read_text(encoding="utf-8")
    raise ToolError(f"Unsupported file type: {p.suffix}")


TOOLS = [
    Tool("list_files",
         "List files in a folder of the company's shared drive (relative path, e.g. 'invoices' or 'policy'). "
         "Use '' for the top level.",
         schema({"folder": {"type": "string"}}), list_files),
    Tool("read_document",
         "Read the text of a company document (PDF invoice, YAML policy, text file) by its relative path.",
         schema({"path": {"type": "string"}}), read_document),
]
