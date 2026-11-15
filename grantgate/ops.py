"""Operating commands: a real consent-and-enforcement gateway on a persistent store (also served by the web app).

An institution is whatever sits behind the gate. Register it with a --url and every *approved* call is
forwarded there as signed JSON (HMAC-SHA256 in X-GrantGate-Signature); denied calls never leave the gate.
Register it without a URL for decision-only mode: the agent's own integration asks "may I?" before acting.
"""

import hashlib
import hmac
import ipaddress
import itertools
import json
import os
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .core import MONEY_OPS, OP_SCOPES, AccessError, Connector, Grant, Hub

OPS = ("institution-add", "link", "grant", "call", "revoke", "dashboard", "audit")
KINDS = ("bank", "airline", "store")


def check_url(url: str, resolve=socket.getaddrinfo) -> str:
    """Only public https endpoints: a gateway must not be steerable into the server's own network."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise AccessError("institution URL must be https://")
    try:
        infos = resolve(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise AccessError(f"institution host does not resolve: {parts.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise AccessError(f"institution host {parts.hostname} points at a non-public address")
    return url


def _post(url: str, body: bytes, signature: str) -> dict:
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "X-GrantGate-Signature": signature}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(1_000_000)
    except urllib.error.HTTPError as e:
        raise AccessError(f"institution answered HTTP {e.code}") from None
    except OSError as e:
        raise AccessError(f"institution unreachable: {e}") from None
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")[:2000]}


class Gateway(Connector):
    """Forwards an approved call to the customer's backend, or just approves it in decision-only mode."""

    def __init__(self, kind: str, url: str, key: bytes, post=_post):
        self.kind, self.url, self.key, self.post = kind, url, key, post

    def handle(self, account: str, op: str, **params):
        if not self.url:
            return {"approved": True, "forwarded": False}
        body = json.dumps({"account": account, "op": op, "params": params}, sort_keys=True).encode()
        return self.post(self.url, body, hmac.new(self.key, body, hashlib.sha256).hexdigest())


def _key_for(store: str) -> bytes:
    env = os.environ.get("GRANTGATE_SIGNING_KEY")
    if env:
        return env.encode()
    path = f"{os.path.splitext(store)[0]}.key"
    try:
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key.encode()
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key.encode()


def load(path: str, post=_post) -> Hub:
    hub = Hub()
    hub.meta = {}
    key = _key_for(path)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        doc = {}
    if not isinstance(doc, dict):
        raise AccessError(f"{path}: not a grantgate store")
    for name, inst in doc.get("institutions", {}).items():
        hub.meta[name] = inst
        hub.add_institution(name, Gateway(inst["kind"], inst.get("url", ""), key, post))
    hub.links = doc.get("links", {})
    for gid, g in doc.get("grants", {}).items():
        g["scopes"] = set(g["scopes"])
        g["payees"] = set(g["payees"]) if g["payees"] is not None else None
        g["expires"] = datetime.fromisoformat(g["expires"])
        hub.grants[gid] = Grant(**g)
    hub.tokens = doc.get("tokens", {})
    hub._ids = itertools.count(int(doc.get("next_id", 1)))
    hub._key, hub._post = key, post
    return hub


def save(hub: Hub, path: str):
    next_id = next(hub._ids)
    hub._ids = itertools.count(next_id)
    grants = {}
    for gid, g in hub.grants.items():
        grants[gid] = {
            **g.__dict__,
            "scopes": sorted(g.scopes),
            "payees": sorted(g.payees) if g.payees is not None else None,
            "expires": g.expires.isoformat(),
        }
    doc = {
        "institutions": hub.meta,
        "links": hub.links,
        "grants": grants,
        "tokens": hub.tokens,  # sha256 of each token, never the token
        "next_id": next_id,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)


def add_ops(sub):
    store = os.environ.get("GRANTGATE_STORE", "grantgate.json")
    p = sub.add_parser("institution-add", help="put a bank, airline or store behind the gate")
    p.add_argument("name")
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--url", default="", help="https endpoint that receives approved calls; omit for decision-only")
    p = sub.add_parser("link", help="a user connects one of their accounts")
    p.add_argument("user")
    p.add_argument("institution")
    p.add_argument("account", help="the user's account reference at the institution")
    p = sub.add_parser("grant", help="a user gives an agent scoped, capped, expiring access (prints the token once)")
    p.add_argument("user")
    p.add_argument("agent")
    p.add_argument("link_id")
    p.add_argument("--scopes", nargs="+", required=True, choices=sorted(set(OP_SCOPES.values())))
    p.add_argument("--per-txn-cap", type=float)
    p.add_argument("--total-cap", type=float)
    p.add_argument("--payee", action="append", help="approved transfer payee (repeatable)")
    p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("call", help="an agent uses its token: allowed calls are forwarded, denied ones are not")
    p.add_argument("token")
    p.add_argument("op", choices=sorted(OP_SCOPES))
    p.add_argument("--amount", type=float)
    p.add_argument("--payee")
    p.add_argument("--param", action="append", default=[], help="extra parameter name=value (repeatable)")
    sub.add_parser("revoke", help="a user withdraws a grant, effective immediately").add_argument("grant_id")
    sub.add_parser("dashboard", help="everything a user has granted, with spend and denials").add_argument("user")
    sub.add_parser("audit", help="every call made under a grant").add_argument("grant_id")
    for name in OPS:
        sub.choices[name].add_argument("--store", default=store)


def run_ops(a, post=_post, resolve=socket.getaddrinfo) -> int:
    hub = load(a.store, post)
    try:
        code = _run(hub, a, resolve)
    except AccessError as e:
        print(f"DENY  {e}" if a.cmd == "call" else f"refused: {e}")
        code = 1
    save(hub, a.store)  # denials are part of the audit log, so they are saved too
    return code


def _grant(hub: Hub, gid: str) -> Grant:
    if gid not in hub.grants:
        raise AccessError(f"no grant {gid!r}")
    return hub.grants[gid]


def _run(hub: Hub, a, resolve) -> int:
    c = a.cmd
    if c == "institution-add":
        if a.name in hub.meta:
            raise AccessError(f"institution {a.name!r} already exists")
        url = check_url(a.url, resolve) if a.url else ""
        hub.meta[a.name] = {"kind": a.kind, "url": url}
        hub.add_institution(a.name, Gateway(a.kind, url, hub._key, hub._post))
        print(f"{a.name} ({a.kind}): {'forwarding approved calls to ' + url if url else 'decision-only'}")
    elif c == "link":
        print(f"{hub.link(a.user, a.institution, a.account)}: {a.user} linked {a.institution}")
    elif c == "grant":
        if a.link_id not in hub.links:
            raise AccessError(f"no link {a.link_id!r}")
        if a.days < 1:
            raise AccessError("a grant must last at least 1 day")
        for name, cap in (("per-txn-cap", a.per_txn_cap), ("total-cap", a.total_cap)):
            if cap is not None and not cap > 0:
                raise AccessError(f"{name} must be > 0")
        token = hub.grant(
            a.user, a.agent, a.link_id, a.scopes, a.per_txn_cap, a.total_cap, a.payee, timedelta(days=a.days)
        )
        gid = max(hub.grants, key=lambda g: int(g.split("-")[1]))
        print(f"{gid} for {a.agent}, expires {hub.grants[gid].expires:%Y-%m-%d}. Token (shown once):\n{token}")
    elif c == "call":
        params = {}
        for item in a.param:
            k, sep, v = item.partition("=")
            if not sep or not k.strip():
                raise AccessError(f"--param must look like name=value, got {item!r}")
            params[k.strip()] = v
        if a.amount is not None:
            params["amount"] = a.amount
        elif a.op in MONEY_OPS:
            raise AccessError(f"{a.op} needs --amount")
        if a.payee:
            params["payee"] = a.payee
        result = hub.call(a.token, a.op, **params)
        print(f"ALLOW  {json.dumps(result)}")
    elif c == "revoke":
        _grant(hub, a.grant_id)
        hub.revoke(a.grant_id)
        print(f"{a.grant_id} revoked")
    elif c == "dashboard":
        rows = hub.dashboard(a.user)
        for r in rows:
            cap = "no cap" if r["total_cap"] is None else f"of ${r['total_cap']:,.2f}"
            state = "REVOKED" if r["revoked"] else f"until {r['expires'][:10]}"
            print(f"{r['grant']:<10}{r['agent']:<16}{r['institution']:<14}${r['spent']:,.2f} {cap}  {state}")
            print(f"          {', '.join(r['scopes'])}; {r['calls']} calls, {r['denied']} denied")
        if not rows:
            print(f"{a.user} has granted nothing")
    else:
        g = _grant(hub, a.grant_id)
        for e in g.log:
            verdict = "ALLOW" if e["allowed"] else f"DENY  {e['reason']}"
            amount = f" ${e['amount']:,.2f}" if e.get("amount") else ""
            print(f"{e['ts'][:19]}  {e['op']:<16}{verdict}{amount}")
        if not g.log:
            print("no calls yet")
    return 0
