# agent-plaid

> **Plaid for agents** — approved access to banks, airlines and stores.

For an agent to pay a bill, rebook a flight or check an order, it needs
access to the user's accounts. Handing over passwords is insane. `agent-plaid`
is the consent + connectivity layer: users link an institution once, grant
an agent **scoped, capped, expiring** access, and every call goes through a
proxy that enforces it and logs it.

## What the MVP does

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
agent-plaid demo
```

## Roadmap

- Real connectors: open banking (PSD2 / FDX), airline NDC, Shopify / Amazon order APIs
- Step-up approval: push notification for anything above a threshold
- Signed receipts for each action (feeds agent-buyer-protection claims)
