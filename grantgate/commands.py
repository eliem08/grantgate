"""Stateless commands: read a JSON request, print a JSON decision (also served by the web app)."""

import json

from .core import AccessError, Hub, MockAirline, MockBank, MockStore

CONNECTORS = {"bank": MockBank, "airline": MockAirline, "store": MockStore}


def add_commands(sub):
    c = sub.add_parser("check", help="would this grant allow the agent's request (scope, caps, payees, expiry)")
    c.add_argument("grant", help="JSON grant: institution, scopes, per_txn_cap, total_cap, payees, spent")
    c.add_argument("--op", required=True, help="e.g. transfer, balances, checkout, book")
    c.add_argument("--amount", type=float)
    c.add_argument("--payee")
    c.add_argument("--item")
    c.add_argument("--flight")


def run(a):
    with open(a.grant, encoding="utf-8-sig") as fh:
        g = json.load(fh)
    if not isinstance(g, dict) or g.get("institution") not in CONNECTORS:
        raise ValueError(f"grant must be an object with institution one of {sorted(CONNECTORS)}")
    hub = Hub()
    conn = CONNECTORS[g["institution"]]()
    if isinstance(conn, MockBank):
        conn.open("acct", 10**9)
    hub.add_institution("inst", conn)
    link = hub.link("user", "inst", "acct")
    token = hub.grant("user", "agent", link, g["scopes"], g.get("per_txn_cap"), g.get("total_cap"), g.get("payees"))
    next(iter(hub.grants.values())).spent = float(g.get("spent", 0))
    params = {
        k: v
        for k, v in {"amount": a.amount, "payee": a.payee, "item": a.item, "flight": a.flight}.items()
        if v is not None
    }
    try:
        hub.call(token, a.op, **params)
    except AccessError as e:
        print(json.dumps({"allowed": False, "reason": str(e)}, indent=2))
        return 1
    except TypeError as e:  # the connector needs a parameter this request did not give
        raise ValueError(f"{a.op}: missing or unexpected parameters ({e})") from None
    print(json.dumps({"allowed": True}, indent=2))
    return 0
