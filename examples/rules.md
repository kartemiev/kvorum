# Domain rules for review (injected into every prompt)

These rules supplement the repository's own conventions. Before issuing a verdict,
the panel MUST check the artifact for violations of the areas below. Violating any
of them is a blocking `CHANGES REQUESTED` with a critical finding.

## 1. Security & auth

- No hard-coded secrets, API keys, or JWTs in the artifact.
- Any new auth/authorisation path must enforce tenant or user scoping; no
  cross-tenant or privilege-escalation leaks.

## 2. Async & performance

- Async routes must not run blocking I/O (sync HTTP/DB/subprocess) in the event
  loop; use `asyncio.to_thread` / executors.
- External calls need timeouts; exceptions must not be silently swallowed.

## 3. Reliability & resilience

- Backoff/retry for transient failures; no unbounded retry loops.
- Idempotency / safe error envelopes for user-facing endpoints.

## 4. Architecture & maintainability

- No duplicated business logic between layers; contract mismatches are defects.
- Public interfaces and error codes must match the documented contract.
