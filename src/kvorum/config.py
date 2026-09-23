"""Configuration and credential handling.

Everything here is path-relative or overridable via environment variables and CLI
flags. There are no machine-specific absolute paths and no project-specific
business logic: :class:`Settings` only describes *where* the panel/rules/packet/
runs live and *how* secrets are resolved.

Precedence for secrets (highest first):

1. process environment;
2. the first ``.env`` file that exists (``--env-file`` overrides the default).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Secret-shaped values that must never be printed or written into artifacts.
#: ``clean()`` collapses any match to ``<redacted>``.
REDACT = re.compile(
    r"(sk-[A-Za-z0-9_\-]{4,}"
    r"|re_[A-Za-z0-9]{6,}"
    r"|ghp_[A-Za-z0-9]{8,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{6,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,})"
)

#: Env-var prefix for every ``KVORUM_*`` override below.
_PREFIX = "KVORUM_"


def clean(text: object, limit: int = 400) -> str:
    """Return ``text`` as a string with secret-shaped values redacted."""
    return REDACT.sub("<redacted>", str(text))[:limit]


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Paths and run-time knobs for one kvorum invocation."""

    #: Panel composition (panel.json) — the SSOT of seats/providers/quorum.
    panel_path: Path = Path("panel.json")
    #: Optional domain rules injected into every prompt.
    rules_path: Path = Path("rules.md")
    #: The artifact under review (produced by ``kvorum pack``).
    packet_path: Path = Path("packet.md")
    #: Where per-seat answers, run.log and run_meta.json are written.
    runs_dir: Path = Path("runs")
    #: Assembled verdict outputs.
    verdict_path: Path = Path("verdict.md")
    verdict_json_path: Path = Path("verdict.json")
    #: Credential files, highest precedence first (earlier paths win).
    env_files: tuple[Path, ...] = (Path(".env"),)

    #: Explicit overrides. ``None`` means "take the panel.json value".
    timeout_s: float | None = None
    fallback_timeout_s: float | None = None
    max_tokens_retry: int | None = None
    quorum_required: int | None = None
    fail_closed: bool | None = None
    #: Pause between seat calls (seconds). Seats run strictly serially; a small
    #: delay keeps a run under tight provider RPM limits (free-tier 429s).
    min_delay_s: float = 0.0

    @classmethod
    def from_env(cls) -> "Settings":
        """Build :class:`Settings` from ``KVORUM_*`` environment variables."""
        env = os.environ
        env_files = tuple(
            Path(p) for p in env.get(_PREFIX + "ENV_FILE", ".env").split(":") if p
        ) or (Path(".env"),)
        return cls(
            panel_path=Path(env.get(_PREFIX + "PANEL", "panel.json")),
            rules_path=Path(env.get(_PREFIX + "RULES", "rules.md")),
            packet_path=Path(env.get(_PREFIX + "PACKET", "packet.md")),
            runs_dir=Path(env.get(_PREFIX + "RUNS_DIR", "runs")),
            verdict_path=Path(env.get(_PREFIX + "VERDICT", "verdict.md")),
            verdict_json_path=Path(env.get(_PREFIX + "VERDICT_JSON", "verdict.json")),
            env_files=env_files,
            timeout_s=_env_float(_PREFIX + "TIMEOUT_S"),
            fallback_timeout_s=_env_float(_PREFIX + "FALLBACK_TIMEOUT_S"),
            max_tokens_retry=_env_int(_PREFIX + "MAX_TOKENS_RETRY"),
            quorum_required=_env_int(_PREFIX + "QUORUM_REQUIRED"),
            fail_closed=_env_bool(_PREFIX + "FAIL_CLOSED"),
            min_delay_s=_env_float(_PREFIX + "MIN_DELAY_S") or 0.0,
        )


def load_env_file(paths: tuple[Path, ...]) -> dict[str, str]:
    """Merge ``KEY=VALUE`` files; earlier paths win, blank values are ignored."""
    env: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not value:
                continue
            env.setdefault(key, value)
    return env


def resolve_key(api_key_env: tuple[str, ...], file_env: dict[str, str]) -> tuple[str, str]:
    """Return ``(key, source)`` for a provider, or ``("", "MISSING")``.

    Process environment wins over ``.env`` files; the source string is used only
    for reporting (``env:NAME`` vs ``env-file:NAME``) and never contains the value.
    """
    for name in api_key_env:
        value = os.environ.get(name)
        if value:
            return value, f"env:{name}"
    for name in api_key_env:
        value = file_env.get(name, "")
        if value:
            return value, f"env-file:{name}"
    return "", "MISSING"
