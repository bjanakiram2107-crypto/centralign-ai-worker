import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_PORT = 5099


@pytest.fixture(scope="session")
def app_server():
    from werkzeug.serving import make_server

    from app.accounting_app.app import app

    server = make_server("127.0.0.1", TEST_PORT, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{TEST_PORT}"
    server.shutdown()


@pytest.fixture()
def fresh_company(app_server):
    """Reset invoices and the database before each test."""
    import app.accounting_app.app as accounting
    from scripts.setup_company import main as setup

    setup()
    accounting.CHAOS = False
    accounting._chaos_already_hit.clear()
    return app_server


@pytest.fixture()
def browser(app_server):
    from tools.browser import Browser

    b = Browser(app_server, headless=True)
    yield b
    b.close()


@pytest.fixture()
def config(app_server, tmp_path):
    from agent.agent import Config

    return Config(app_url=app_server, runs_dir=tmp_path / "runs")


@pytest.fixture(scope="session", autouse=True)
def leave_company_clean():
    """Tests share data/accounting.db with real runs, so reset it afterwards: the next real run starts clean."""
    yield
    from scripts.setup_company import main as setup

    setup()
