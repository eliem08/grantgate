"""Consent, scoped tokens and an enforcing proxy for agent access to user accounts."""

from __future__ import annotations

import hashlib
import itertools
import math
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar

__all__ = ["Connector", "MockBank", "MockAirline", "MockStore", "Hub", "AccessError", "OP_SCOPES"]

OP_SCOPES = {
    "balances": "bank:read",
    "transactions": "bank:read",
    "transfer": "bank:transfer",
    "search_flights": "airline:read",
    "book": "airline:book",
    "change_booking": "airline:book",
    "order_status": "store:read",
    "checkout": "store:buy",
    "return_item": "store:buy",
}
MONEY_OPS = {"transfer", "book", "checkout", "change_booking"}


class AccessError(Exception):
    pass


class Connector:
    kind = ""

    def handle(self, account: str, op: str, **p):
        fn = getattr(self, op, None)
        if not fn:
            raise AccessError(f"{self.kind} does not support {op}")
        return fn(account, **p)


class MockBank(Connector):
    kind = "bank"

    def __init__(self):
        self.balances_ = {}
        self.txns = {}

    def open(self, account, balance):
        self.balances_[account] = balance
        self.txns[account] = []

    def balances(self, account):
        return {"available": self.balances_[account]}

    def transactions(self, account, limit=10):
        return self.txns[account][-limit:]

    def transfer(self, account, amount, payee):
        if amount > self.balances_[account]:
            raise AccessError("insufficient funds")
        self.balances_[account] -= amount
        self.txns[account].append({"amount": -amount, "payee": payee})
        return {"status": "sent", "amount": amount, "payee": payee}


class MockAirline(Connector):
    kind = "airline"
    FLIGHTS: ClassVar[tuple[dict, ...]] = (
        {"flight": "TP201", "from": "JFK", "to": "LIS", "price": 640},
        {"flight": "TP203", "from": "JFK", "to": "LIS", "price": 520},
    )

    def __init__(self):
        self.bookings = {}

    def search_flights(self, account, origin, dest):
        return [f for f in self.FLIGHTS if f["from"] == origin and f["to"] == dest]

    def book(self, account, flight, amount):
        ref = f"PNR{len(self.bookings) + 1:03d}"
        self.bookings[ref] = {"account": account, "flight": flight, "price": amount}
        return {"record_locator": ref}

    def change_booking(self, account, record_locator, flight, amount):
        self.bookings[record_locator].update(flight=flight, price=amount)
        return {"record_locator": record_locator, "flight": flight}


class MockStore(Connector):
    kind = "store"

    def __init__(self):
        self.orders = {}

    def order_status(self, account, order_id):
        return self.orders.get(order_id, {"status": "not_found"})

    def checkout(self, account, item, amount):
        oid = f"O{len(self.orders) + 1:04d}"
        self.orders[oid] = {"item": item, "amount": amount, "status": "placed"}
        return {"order_id": oid}

    def return_item(self, account, order_id):
        self.orders[order_id]["status"] = "return_started"
        return self.orders[order_id]


@dataclass
class Grant:
    grant_id: str
    user: str
    agent: str
    link_id: str
    scopes: set
    per_txn_cap: float | None
    total_cap: float | None
    payees: set | None
    expires: datetime
    spent: float = 0.0
    revoked: bool = False
    log: list = field(default_factory=list)


class Hub:
    def __init__(self):
        self.institutions: dict[str, Connector] = {}
        self.links: dict[str, dict] = {}
        self.grants: dict[str, Grant] = {}
        self.tokens: dict[str, str] = {}  # sha256(token) -> grant_id
        self._ids = itertools.count(1)

    def add_institution(self, name: str, connector: Connector):
        self.institutions[name] = connector

    def link(self, user: str, institution: str, account: str) -> str:
        if institution not in self.institutions:
            raise AccessError("unknown institution")
        lid = f"link-{next(self._ids)}"
        self.links[lid] = {"user": user, "institution": institution, "account": account}
        return lid

    def grant(
        self,
        user,
        agent,
        link_id,
        scopes,
        per_txn_cap=None,
        total_cap=None,
        payees=None,
        ttl=timedelta(days=30),
        now=None,
    ) -> str:
        link = self.links[link_id]
        if link["user"] != user:
            raise AccessError("link belongs to another user")
        kind = self.institutions[link["institution"]].kind
        bad = [s for s in scopes if not s.startswith(kind + ":")]
        if bad:
            raise AccessError(f"scopes {bad} don't apply to a {kind}")
        gid = f"grant-{next(self._ids)}"
        self.grants[gid] = Grant(
            gid,
            user,
            agent,
            link_id,
            set(scopes),
            per_txn_cap,
            total_cap,
            set(payees) if payees else None,
            (now or datetime.now()) + ttl,
        )
        token = "ap_" + secrets.token_urlsafe(24)
        self.tokens[hashlib.sha256(token.encode()).hexdigest()] = gid
        return token

    def revoke(self, grant_id):
        self.grants[grant_id].revoked = True

    def call(self, token: str, op: str, now: datetime | None = None, **params):
        now = now or datetime.now()
        gid = self.tokens.get(hashlib.sha256(token.encode()).hexdigest())
        if not gid:
            raise AccessError("invalid token")
        g = self.grants[gid]

        def deny(reason):
            g.log.append({"ts": now.isoformat(), "op": op, "allowed": False, "reason": reason})
            raise AccessError(reason)

        if g.revoked:
            deny("grant revoked")
        if now >= g.expires:
            deny("grant expired")
        need = OP_SCOPES.get(op)
        if not need or need not in g.scopes:
            deny(f"scope {need} not granted")
        try:
            amount = float(params.get("amount", 0))
        except (TypeError, ValueError):
            amount = math.nan
        if op in MONEY_OPS:
            # nan would slip past every cap and then poison `spent`; a negative amount would refill the budget
            if not (math.isfinite(amount) and amount > 0):
                deny(f"invalid amount {params.get('amount')!r}")
            if g.per_txn_cap is not None and amount > g.per_txn_cap:
                deny(f"amount {amount} over per-transaction cap {g.per_txn_cap}")
            if g.total_cap is not None and g.spent + amount > g.total_cap:
                deny(f"would exceed total cap {g.total_cap}")
            if g.payees is not None and op == "transfer" and params.get("payee") not in g.payees:
                deny(f"payee {params.get('payee')} not approved")
        link = self.links[g.link_id]
        result = self.institutions[link["institution"]].handle(link["account"], op, **params)
        if op in MONEY_OPS:
            g.spent += amount
        g.log.append({"ts": now.isoformat(), "op": op, "allowed": True, "amount": amount or None})
        return result

    def dashboard(self, user: str) -> list[dict]:
        rows = []
        for g in self.grants.values():
            if g.user != user:
                continue
            link = self.links[g.link_id]
            rows.append(
                {
                    "grant": g.grant_id,
                    "agent": g.agent,
                    "institution": link["institution"],
                    "scopes": sorted(g.scopes),
                    "spent": g.spent,
                    "total_cap": g.total_cap,
                    "expires": g.expires.isoformat(timespec="minutes"),
                    "revoked": g.revoked,
                    "calls": len(g.log),
                    "denied": sum(1 for e in g.log if not e["allowed"]),
                }
            )
        return rows
