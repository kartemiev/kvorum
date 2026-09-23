# Agent Directives & Architecture Context

This repository is designed to be fully navigable and maintainable by AI Coding Agents (Cline, Aider, Claude Code).

## Architecture Essentials
- **No Monoliths:** Core logic is split into modular components (`runner`, `panel`, `verdict`, `ast_summary`).
- **AST First:** Always use `ast_summary.py` to inspect repository structure before ingesting large context blocks.
- **Fail-Closed Consensus:** All code analysis runs through a multi-model ensemble ($\ge 3$ models required for valid consensus).
- **Environment Isolation:** Secrets MUST be loaded via `os.getenv()`. Hardcoded tokens or absolute system paths (`/home/...`) are strictly prohibited and caught by secret-scan gates.

## Testing Strategy
- Unit tests use `httpx.MockTransport` exclusively. Never issue real network calls during `pytest`.
- Run validation: `.venv/bin/pytest -q`
