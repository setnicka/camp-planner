"""WSGI entry point. Run with: flask --app wsgi run (or gunicorn wsgi:app).

Loads .env before importing the package, so config (which reads
DB_TABLE_PREFIX at import time) and create_app see the environment.

ProxyFix trusts one hop of X-Forwarded-* (incl. X-Forwarded-Prefix)
so the app generates correct URLs when served behind a reverse proxy under a
path. It only acts on those headers when present, so it's harmless for direct
local runs. Required for proxy auth mode (see camp_planner/auth/proxy.py).
"""

from dotenv import load_dotenv

load_dotenv()

from camp_planner import create_app  # noqa: E402  (must follow load_dotenv)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

app = create_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
