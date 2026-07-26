"""WSGI entry point. Run with: flask --app wsgi run (or gunicorn wsgi:app).

Loads .env before importing the package, so config (which reads
DB_TABLE_PREFIX at import time) and create_app see the environment.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from camp_planner import create_app  # noqa: E402  (must follow load_dotenv)

app = create_app()

# BEHIND_PROXY=1: trust one hop of X-Forwarded-* (incl. X-Forwarded-Prefix) so the
# app generates correct URLs when served behind a reverse proxy under a path.
# Opt-in on purpose — on a directly exposed process these headers are client-
# controlled and would let anyone spoof the host/path of generated URLs. Proxy
# auth mode (camp_planner/auth/proxy.py) sits behind a proxy by definition, so
# such deployments set this. See docs/DEPLOYMENT.md.
if os.environ.get("BEHIND_PROXY", "").strip().lower() in {"1", "true", "yes"}:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
else:
    # Loud one-time signal for the common misconfig: X-Forwarded-* arriving while ProxyFix
    # is off means url_for() silently drops the reverse-proxy path prefix (unstyled pages,
    # broken links). Warn instead of failing quietly. Set BEHIND_PROXY=1 to opt in.
    from flask import request

    _FWD = ("X-Forwarded-Prefix", "X-Forwarded-For", "X-Forwarded-Host")
    _proxy_warned: list[bool] = []

    @app.before_request
    def _warn_unset_behind_proxy() -> None:
        if not _proxy_warned and any(h in request.headers for h in _FWD):
            _proxy_warned.append(True)
            app.logger.warning(
                "Received %s but BEHIND_PROXY is not set: ProxyFix is off, so url_for() omits "
                "the reverse-proxy path prefix (CSS/JS/links break). Set BEHIND_PROXY=1 if this "
                "app runs behind a trusted proxy.",
                next(h for h in _FWD if h in request.headers),
            )
