# Architecture — grantgate

## 1. Scope
Consent and connectivity layer that gives agents scoped, capped, expiring access to a user's
bank, airline and store accounts, without ever handing over credentials.
**MVP:** hub with institutions and connectors (mock bank, airline, store), link flow, grants
(scopes, per-transaction and total caps, payee allowlist, expiry), opaque tokens stored as SHA-256
hashes, enforcing proxy with an audit log, dashboard, revoke.
**Product:** real connectors (open banking, airline NDC, commerce APIs), step-up approvals, signed action receipts.

## 2. System context
```mermaid
flowchart LR
  User -->|link account once| Hub
  User -->|grant: agent X, scopes, caps, expiry| Hub
  Hub -->|opaque token| Agent
  Agent -->|call(token, op, params)| Proxy[Enforcing proxy]
  Proxy -->|scope + caps + expiry + revocation| Grants[(Grants)]
  Proxy --> Connectors[Bank / Airline / Store connectors]
  Proxy --> Audit[(Audit log)] --> Dashboard --> User
```

## 3. Enforcement order (per call)
token lookup (by hash) → revoked? → expired? → op's required scope granted? → for money ops:
per-transaction cap, total cap (`spent + amount`), payee allowlist → connector → record spend → audit.
Every denial is audited with its reason, and the dashboard counts denials per grant.

## 4. Design decisions
- **The proxy pattern, not token-to-agent-then-direct-API.** The institution never sees the
  agent's token, and the user can revoke in one place. *Trade-off:* the proxy sits in the hot path,
  so it's built stateless and horizontally scaled.
- **Opaque random tokens, stored hashed.** A DB leak doesn't leak usable tokens, unlike JWTs that
  need no lookup. Revocation is instant.
- **Scopes are validated against the institution kind** at grant time (`bank:*` only on banks),
  which catches mis-grants early.
- **Spend is recorded only after the connector succeeds.** A failed transfer doesn't eat into caps.

## 5. Production architecture
A proxy service (Go) with Postgres (links, grants, spend ledger) and Redis (token hash → grant
cache, invalidated on revoke). Connector workers hold institution credentials in a vault (per-user
OAuth tokens from open-banking flows). Step-up approvals over threshold go out via push.

## 6. Security
Link credentials never touch agents. Least privilege by default (read-only presets). Mutual TLS
to connectors. A tamper-evident audit chain (hash-linked entries).

## 7. Risks
Institutional API coverage. Start with open-banking regions and commerce platforms with OAuth
APIs, and use screen-scraping only with explicit user consent.
