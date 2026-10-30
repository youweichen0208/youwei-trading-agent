# YTA engineering instructions

Before changing behavior, read `GLOSSARY.md`, sections 1.2 and 6.2 of the architecture task book in `docs/`, and the relevant task dependencies. `docs/implementation-status.md` records actual evidence and remaining work; task descriptions are not proof of implementation.

- Keep the browser behind the TypeScript BFF. Hermes, provider credentials, PostgreSQL and Alpha remain private.
- Alpha owns financial arithmetic and prediction fact transactions. See `docs/adr/0011-prediction-domain-authority.md` before moving freeze or settlement logic across services.
- Use Decimal arithmetic and exchange sessions for financial calculations. Wire decimals are strings. Return missing indicators as null; never silently fill price gaps.
- Persist immutable forecasts, versioned outcomes, evidence and audit records. Runtime DB credentials have SELECT/INSERT permissions on fact tables; migrations use a different owner.
- User confirmation and ownership come from the authenticated BFF session. Model/tool output cannot authorize a freeze.
- REPLAY/DEMO cannot enter LIVE_RESEARCH metrics. An unavailable real provider must report unavailable; it must not fall back to fixtures.
- Treat the Hermes capability matrix as unverified until locked-version integration evidence exists. Unknown execution is not permission to resend a prompt under a new key.
- Add tests for changed financial, concurrency, permission or protocol behavior. Use the real PostgreSQL integration suite for persistence changes.
- Run `pnpm typecheck`, `pnpm test`, `pnpm build`, `uv run ruff check .`, `uv run pytest`, `python3 scripts/check_docs.py`, and `uv run python scripts/export_contracts.py --check` as applicable. PostgreSQL tests require a dedicated database name ending in `_test`; skipping them is not passing database verification.
- Update implementation evidence after changes. Tasks remain IN_PROGRESS/REVIEW until their full documented DoD, including applicable integration/review requirements, is met. Never mark an entire release complete from a subset of tests.

