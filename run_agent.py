"""Command-line entry point.

Examples:
    python run_agent.py "Process the latest Acme invoice."
    python run_agent.py --show-browser "Process the latest Acme invoice."
    python run_agent.py --chaos "Process the latest Acme invoice."        # flaky accounting system
    python run_agent.py "Which invoices on the shared drive are not yet in AcmeBooks?"

The accounting app is started automatically in the background if it is not already running.
"""

import argparse
import os
import sys
import threading
import time
import urllib.request

from dotenv_loader import load_env


def app_is_up(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=1)
        return True
    except Exception:
        return False


def start_accounting_app(url: str) -> None:
    import logging

    from werkzeug.serving import make_server

    from app.accounting_app.app import app

    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # keep the agent's trace readable
    port = int(url.rsplit(":", 1)[1])
    server = make_server("127.0.0.1", port, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for _ in range(20):
        if app_is_up(url):
            return
        time.sleep(0.2)
    sys.exit(f"Could not start the accounting app on {url}")


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Autonomous accounts-payable AI worker (prototype)")
    parser.add_argument("task", help="What you want done, in plain language")
    parser.add_argument("--show-browser", action="store_true", help="Show the browser window (good for demos)")
    parser.add_argument("--chaos", action="store_true", help="Simulate a flaky accounting system (bad first save)")
    parser.add_argument("--reset", action="store_true", help="Reset the sandbox company data before running")
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL", "claude-opus-5-5"))
    parser.add_argument("--effort", default=os.environ.get("AGENT_EFFORT", "medium"),
                        choices=["low", "medium", "high", "xhigh", "max"])
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and put your key in it.")
    from scripts.setup_company import DB_PATH, main as setup_company
    if args.reset or not DB_PATH.exists():
        setup_company()
    if args.chaos:
        os.environ["ACCOUNTING_CHAOS"] = "1"  # must be set before the app module is imported

    import anthropic
    from agent.agent import Agent, Config
    from agent.llm import ClaudeLLM
    from agent.ui import TerminalHuman
    from tools.browser import Browser

    config = Config(model=args.model, effort=args.effort)
    if not app_is_up(config.app_url):
        start_accounting_app(config.app_url)

    browser = Browser(config.app_url, headless=not args.show_browser, slow_mo_ms=400 if args.show_browser else 0)
    try:
        agent = Agent(config, ClaudeLLM(config.model, config.effort), browser, TerminalHuman())
        state = agent.run(args.task)
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API (network timeout). Check your internet connection, "
                 "VPN or firewall, then run again. Test with: Test-NetConnection api.anthropic.com -Port 443")
    finally:
        browser.close()
    sys.exit(0 if state.status in ("completed", "on_hold") else 1)


if __name__ == "__main__":
    main()
