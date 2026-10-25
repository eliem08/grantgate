<!-- sell-kit:start -->
# GrantGate

[![CI](https://github.com/eliem08/grantgate/actions/workflows/ci.yml/badge.svg)](https://github.com/eliem08/grantgate/actions/workflows/ci.yml)

> **Approved, capped access for agents to banks, airlines and stores.**

**Who it's for:** Developers building agents that act on a user's accounts.

**The problem:** Giving an agent your bank or airline login is all or nothing, with no caps, no expiry and no record of what it did.

**What you get:** User-approved, scoped, capped and expiring grants enforced by a proxy, with an approved-payee list and a per-user access dashboard.

## Quickstart

Customers receive a release archive with the wheel and the examples. Python 3.10+ and no other dependencies.

```bash
pip install grantgate-0.1.0-py3-none-any.whl
grantgate demo
```

## Pricing

Every plan starts with a 14-day free trial. Prices in USD, billed monthly unless noted.

| Plan | Price | Includes |
|---|---|---|
| **Developer** | $49 per month | 100 active grants; Caps, expiry and payee lists; Access dashboard |
| **Growth** | $399 per month | 10,000 active grants; Custom connectors; Audit export |
| **Enterprise** | $2,000 per month | Unlimited grants; On-prem proxy; SLA |

To start a trial or buy a plan, email **sales@YOUR-DOMAIN.com**.

## Licence

Proprietary. Copyright (c) 2026 eliem08. All rights reserved. Use requires a paid plan or an active trial; see [LICENSE](LICENSE).
<!-- sell-kit:end -->

## What it does

- **Institutions & connectors** with a uniform operation catalogue:
  - bank: `balances`, `transactions`, `transfer`
  - airline: `search_flights`, `book`, `change_booking`
  - store: `order_status`, `checkout`, `return_item`
- **Link flow**: user links an account at an institution (mock credential exchange)
- **Grants**: user grants an agent a link with scopes (`bank:read`, `bank:transfer`…),
  per-transaction and total spend caps, payee allowlist, expiry
- **Tokens**: opaque random access tokens, stored only as SHA-256 hashes
- **Proxy**: `call(token, op, **params)` → scope check, cap check, expiry,
  revocation → forwards to the connector → audit log
- **Dashboard**: what each agent can do and has done; one-click revoke

## Quickstart

```bash
pip install -e .
grantgate demo
```

## Roadmap

- Real connectors: open banking (PSD2 / FDX), airline NDC, Shopify / Amazon order APIs
- Step-up approval: push notification for anything above a threshold
- Signed receipts for each action (feeds agent-buyer-protection claims)

## Development

Zero runtime dependencies; Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m unittest discover -s tests -t .           # unit + CLI smoke tests
ruff check . && ruff format --check .               # lint + format
```

```
grantgate/core.py       domain logic (pure, importable, fully tested)
grantgate/__main__.py   CLI (`grantgate ...` / `python -m grantgate ...`)
tests/                unit tests + generated CLI smoke tests
```

CI runs lint and tests on Ubuntu and Windows across Python 3.10, 3.12 and 3.13.
Design, data model, trade-offs and production path: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

Part of the [agent-economy](https://github.com/eliem08/agent-economy) portfolio (idea #32).
