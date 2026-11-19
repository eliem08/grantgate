"""POST /stripe/issuing: Stripe Issuing's real-time authorization webhook (see issuing.py)."""

from __future__ import annotations

import json
import os
import sys
import threading

from .issuing import DEFAULT_API_VERSION, decide, verify
from .ops import load, save

STORE_FILE = "grantgate-grantgate.json"  # the same store the API's commands use
_lock = threading.Lock()


def issuing(handler, body: bytes):
    secret = os.environ.get("ISSUING_WEBHOOK_SECRET", "")
    if not secret:
        return 503, "text/plain; charset=utf-8", b"set ISSUING_WEBHOOK_SECRET to accept Stripe Issuing webhooks"
    try:
        event = verify(body, handler.headers.get("Stripe-Signature", ""), secret)
    except ValueError as e:
        return 400, "text/plain; charset=utf-8", str(e).encode()
    if event.get("type") != "issuing_authorization.request":
        return 200, "application/json", b'{"received": true}'  # other Issuing events: acknowledged, not acted on
    auth = (event.get("data") or {}).get("object") or {}
    store = str(handler.data_dir / STORE_FILE)
    with _lock:
        hub = load(store)
        approved, reason, gid = decide(hub, auth)
        if gid:
            save(hub, store)
    sys.stderr.write(f"issuing {auth.get('id', '?')}: {'approved' if approved else 'declined'} ({reason})\n")
    out = {"approved": approved, "metadata": {"grantgate_reason": reason[:450], "grant_id": gid or ""}}
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Stripe-Version", os.environ.get("STRIPE_API_VERSION", DEFAULT_API_VERSION))
    data = json.dumps(out).encode()
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return None  # answered here: Stripe needs the Stripe-Version header


ROUTES = {"/stripe/issuing": issuing}
