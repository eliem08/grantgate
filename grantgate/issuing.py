"""Stripe Issuing: a grant decides, in real time, whether a card payment goes through.

Attach a Stripe Issuing card to a grant (`card-attach`). Point Issuing's real-time authorization webhook at
https://<PUBLIC_URL>/stripe/issuing. For every `issuing_authorization.request`, the grant's own rules decide:
revoked, expired, the per-payment cap, the total cap and the approved payees (matched against the merchant's
name or network id). The answer goes back in the same response, as Stripe requires, within its 2-second limit:
HTTP 200, a Stripe-Version header and {"approved": true|false}. Approved amounts count towards the grant's
total, and every decision lands in the grant's audit log.

Environment: ISSUING_WEBHOOK_SECRET (the endpoint's whsec_ signing secret), STRIPE_API_VERSION (the version
the response declares; default below). Requests without a valid Stripe-Signature are refused and change nothing.
Card refunds and reversals are not credited back to the grant's total: a conservative choice, never a loose one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from datetime import datetime

DEFAULT_API_VERSION = "2025-03-31.basil"
TOLERANCE = 300  # seconds between Stripe's timestamp and now


def verify(body: bytes, header: str, secret: str, now: float | None = None) -> dict:
    """Stripe's webhook signature: t=<unix>,v1=<hex HMAC-SHA256 of "t.body">. Returns the event."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    sigs = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    try:
        t = int(parts.get("t", ""))
    except ValueError:
        raise ValueError("missing timestamp in Stripe-Signature") from None
    if abs((now if now is not None else time.time()) - t) > TOLERANCE:
        raise ValueError("Stripe-Signature timestamp is too old or in the future")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise ValueError("bad Stripe-Signature")
    return json.loads(body)


def decide(hub, auth: dict, now: datetime | None = None) -> tuple[bool, str, str | None]:
    """(approved, reason, grant_id) for one issuing_authorization object."""
    now = now or datetime.now()
    card = auth.get("card") or {}
    card_id = card.get("id") if isinstance(card, dict) else card
    gid = getattr(hub, "cards", {}).get(card_id)
    if not gid or gid not in hub.grants:
        return False, "card is not attached to any grant", None
    g = hub.grants[gid]
    req = auth.get("pending_request") or {}
    cents = req.get("amount")
    currency = str(req.get("currency") or "").lower()
    merchant = auth.get("merchant_data") or {}
    payee = str(merchant.get("name") or "").strip()

    def no(reason):
        g.log.append({"ts": now.isoformat(), "op": "card_payment", "allowed": False, "reason": reason, "payee": payee})
        return False, reason, gid

    if g.revoked:
        return no("grant revoked")
    if now >= g.expires:
        return no("grant expired")
    want = getattr(hub, "card_currency", {}).get(card_id, "usd")
    if currency != want:
        return no(f"currency {currency or '?'} is not the grant's {want}")
    if not isinstance(cents, int) or cents <= 0:
        return no(f"invalid amount {cents!r}")
    amount = cents / 100
    if not math.isfinite(amount):
        return no("invalid amount")
    if g.per_txn_cap is not None and amount > g.per_txn_cap:
        return no(f"{amount:.2f} over per-payment cap {g.per_txn_cap:g}")
    if g.total_cap is not None and g.spent + amount > g.total_cap:
        return no(f"would exceed total cap {g.total_cap:g}")
    if g.payees is not None:
        ids = {payee.lower(), str(merchant.get("network_id") or "").lower()}
        if not ids & {p.lower() for p in g.payees}:
            return no(f"merchant {payee or '?'} not approved")
    g.spent += amount
    g.log.append({"ts": now.isoformat(), "op": "card_payment", "allowed": True, "amount": amount, "payee": payee})
    return True, "within the grant", gid
