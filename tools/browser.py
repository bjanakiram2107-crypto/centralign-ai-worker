"""Browser tools: operate the internal accounting web app through a real Chromium browser (Playwright).

The LLM never sends raw JavaScript or CSS selectors. It gets four narrow actions:
open a page, read the page, fill a labelled field, click a button or link by its text.
Every action is checked (allowed site only) and screenshotted as evidence.
"""

import os
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from tools.base import Tool, ToolContext, ToolError, schema

MAX_PAGE_TEXT = 3500


class Browser:
    def __init__(self, base_url: str, headless: bool, slow_mo_ms: int = 0):
        self.base_url = base_url.rstrip("/")
        self._pw = sync_playwright().start()
        # CHROMIUM_PATH is optional: use an already-installed Chromium instead of Playwright's download.
        self._browser = self._pw.chromium.launch(headless=headless, slow_mo=slow_mo_ms,
                                                 executable_path=os.environ.get("CHROMIUM_PATH") or None)
        self.page = self._browser.new_page(viewport={"width": 1100, "height": 800})
        self.shot_count = 0

    def close(self):
        self._browser.close()
        self._pw.stop()

    def check_allowed(self, url: str) -> str:
        full = urljoin(self.base_url + "/", url.lstrip("/")) if not url.startswith("http") else url
        if urlparse(full).netloc != urlparse(self.base_url).netloc:
            raise ToolError(f"Blocked: {url} is outside the allowed internal app ({self.base_url}).")
        return full

    def screenshot(self, ctx: ToolContext, label: str) -> str:
        self.shot_count += 1
        path = ctx.state.run_dir / "screens" / f"{self.shot_count:02d}_{label}.png"
        path.parent.mkdir(exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        ctx.state.evidence.append(str(path))
        return str(path)

    def find_clickable(self, text: str):
        """Exact visible text first, then a case-insensitive contains match."""
        for role in ("button", "link"):
            loc = self.page.get_by_role(role, name=text, exact=True)
            if loc.count() == 1:
                return loc
        for role in ("button", "link"):
            loc = self.page.get_by_role(role, name=text)
            if loc.count() == 1:
                return loc
        return None

    def describe(self) -> str:
        p = self.page
        text = p.inner_text("main") if p.locator("main").count() else p.inner_text("body")
        fields = p.eval_on_selector_all(
            "input, select, textarea",
            "els => els.map(e => ({label: ((e.id && document.querySelector(`label[for='${e.id}']`))"
            " || e.closest('label') || {}).innerText || e.name, value: e.value}))")
        buttons = p.eval_on_selector_all(
            "button", "els => els.map(e => e.innerText + (e.dataset.irreversible ? ' [IRREVERSIBLE]' : ''))")
        links = p.eval_on_selector_all("main a", "els => els.map(e => e.innerText + ' -> ' + e.getAttribute('href'))")
        nav = p.eval_on_selector_all("header a", "els => els.map(e => e.innerText + ' -> ' + e.getAttribute('href'))")
        out = [f"URL: {p.url}", f"TITLE: {p.title()}", "PAGE TEXT:", text[:MAX_PAGE_TEXT]]
        if fields:
            out.append("FORM FIELDS: " + "; ".join(f"{f['label']}='{f['value']}'" for f in fields))
        if buttons:
            out.append("BUTTONS: " + "; ".join(buttons))
        if links:
            out.append("LINKS ON PAGE: " + "; ".join(links))
        out.append("NAVIGATION: " + "; ".join(nav))
        return "\n".join(out)


def _browser(ctx: ToolContext) -> Browser:
    if ctx.browser is None:
        raise ToolError("Browser is not available in this run.")
    return ctx.browser


def browser_open(ctx: ToolContext, path: str) -> str:
    b = _browser(ctx)
    url = b.check_allowed(path)
    try:
        response = b.page.goto(url)
    except PlaywrightError as e:
        raise ToolError(f"Could not open {url}: {e.message.splitlines()[0]}. Is the accounting app running?")
    b.screenshot(ctx, "open")
    status = response.status if response else "?"
    if status == 404:
        raise ToolError(f"Page not found (404): {url}")
    return f"Opened {url} (HTTP {status}).\n" + b.describe()


def browser_read(ctx: ToolContext) -> str:
    return _browser(ctx).describe()


def browser_fill(ctx: ToolContext, field_label: str, value: str) -> str:
    b = _browser(ctx)
    loc = b.page.get_by_label(field_label, exact=True)
    if loc.count() != 1:
        loc = b.page.get_by_label(field_label)
    if loc.count() != 1:
        raise ToolError(f"No unique form field labelled '{field_label}' on this page. "
                        "Call browser_read to see the exact field labels.")
    loc.fill(value)
    return f"Filled '{field_label}' with '{value}'."


def browser_click(ctx: ToolContext, text: str) -> str:
    b = _browser(ctx)
    loc = b.find_clickable(text)
    if loc is None:
        raise ToolError(f"No unique button or link with text '{text}'. Call browser_read to see what is clickable.")
    try:
        with b.page.expect_navigation(timeout=5000):
            loc.click()
    except PlaywrightError:
        pass  # some clicks do not navigate; that's fine
    shot = b.screenshot(ctx, "click")
    page_text = b.describe()
    alert = b.page.locator("[role=alert], .error")
    if alert.count():
        raise ToolError(f"Clicked '{text}', but the app showed an error: {alert.first.inner_text()}\n"
                        f"(screenshot: {shot})\n{page_text}")
    return f"Clicked '{text}'.\n{page_text}"


def is_irreversible_click(ctx: ToolContext, text: str) -> bool:
    """Used by the executor's guardrail before a click happens."""
    if ctx.browser is None:
        return False
    loc = ctx.browser.find_clickable(text)
    return bool(loc) and loc.get_attribute("data-irreversible") == "true"


def current_page_heading(ctx: ToolContext) -> str:
    h1 = ctx.browser.page.locator("h1")
    return h1.first.inner_text() if h1.count() else ""


TOOLS = [
    Tool("browser_open", "Open a page of the internal accounting app (AcmeBooks) by path, e.g. '/vendors' "
         "or '/vendors?q=Acme'. Returns the page's text, form fields, buttons and links.",
         schema({"path": {"type": "string"}}), browser_open),
    Tool("browser_read", "Re-read the current browser page (text, form fields, buttons, links).",
         schema({}), browser_read),
    Tool("browser_fill", "Type a value into a form field on the current page, identified by its visible label.",
         schema({"field_label": {"type": "string"}, "value": {"type": "string"}}), browser_fill),
    Tool("browser_click", "Click a button or link on the current page by its visible text. Buttons marked "
         "[IRREVERSIBLE] are blocked by the runtime unless approval has been granted.",
         schema({"text": {"type": "string"}}), browser_click),
]
