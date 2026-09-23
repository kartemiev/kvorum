"""Panel composition: seats, providers, quorum, exclusions.

The panel lives entirely in data (``panel.json``), so adding a model never
requires a code change. A legacy ``council.json`` (the older SSOT format) is
imported on demand via :func:`load_panel`, never required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Built-in providers shipped with the default panel. Every entry is an
#: OpenAI-compatible endpoint (``{base_url}/chat/completions``), so custom /
#: local providers (Ollama, vLLM, LiteLLM, ...) are added the same way.
DEFAULT_PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": ["DEEPSEEK_API_KEY"],
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.com/v1",
        "api_key_env": ["SILICONFLOW_API_KEY", "SILICONFLOW_KEY"],
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": ["OPENROUTER_API_KEY"],
    },
}

#: Best-effort mapping from a legacy ``council.json`` model slug to
#: ``(provider, model_id)``. Provider-native model ids differ from the logical
#: slug (e.g. ``z-ai/glm-5.2`` is served as ``zai-org/GLM-5.2``); anything
#: unknown falls back to OpenRouter, which accepts the slug verbatim.
LEGACY_SLUG_MAP: dict[str, tuple[str, str]] = {
    "deepseek/deepseek-v4-pro": ("deepseek", "deepseek-v4-pro"),
    "z-ai/glm-5.2": ("siliconflow", "zai-org/GLM-5.2"),
    "z-ai/glm-5.3": ("siliconflow", "zai-org/GLM-5.3"),
    "moonshotai/kimi-k3": ("siliconflow", "moonshotai/Kimi-K3"),
    "qwen/qwen3.8-flash": ("siliconflow", "Qwen/Qwen3.8-2.4T-A95B"),
    "meituan/longcat-2.0": ("siliconflow", "meituan-longcat/LongCat-2.0"),
    "minimax/minimax-m3": ("siliconflow", "MiniMaxAI/MiniMax-M3"),
}


@dataclass
class ProviderConfig:
    """An OpenAI-compatible chat-completions endpoint."""

    name: str
    base_url: str
    api_key_env: tuple[str, ...] = ()
    extra_headers: dict[str, str] = field(default_factory=dict)

    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"


#: Logical model → per-provider ids, ordered by preference. A seat may declare the
#: logical name as its ``model`` and every provider gets its own id (the SSOT for
#: model naming). Extra entries per provider are alternatives used when a provider
#: retires or renames a model, e.g. ``"glm-5.2": {"siliconflow": ["zai-org/GLM-5.2",
#: "zai-org/GLM-5.2-air"]}``. Panel files may extend/override this map.
DEFAULT_MODEL_ALIASES: dict[str, dict[str, list[str]]] = {
    "deepseek-v4-pro": {
        "deepseek": ["deepseek-v4-pro"],
        "openrouter": ["deepseek/deepseek-v4-pro"],
    },
    "glm-5.2": {
        "siliconflow": ["zai-org/GLM-5.2"],
        "openrouter": ["z-ai/glm-5.2"],
    },
    "kimi-k3": {
        "siliconflow": ["moonshotai/Kimi-K3"],
        "openrouter": ["moonshotai/kimi-k3"],
    },
    "qwen3.8-flash": {
        "siliconflow": ["Qwen/Qwen3.8-2.4T-A95B"],
        "openrouter": ["qwen/qwen3.8-flash"],
    },
    "longcat-2.0": {
        "siliconflow": ["meituan-longcat/LongCat-2.0"],
        "openrouter": ["meituan/longcat-2.0"],
    },
    "minimax-m3": {
        "siliconflow": ["MiniMaxAI/MiniMax-M3"],
        "openrouter": ["minimax/minimax-m3"],
    },
}


@dataclass
class Fallback:
    """Secondary provider used only when the primary returns no visible text.

    ``model`` may be omitted when the seat's model alias already declares an id
    for that provider (per-provider naming).
    """

    provider: str
    model: str = ""


@dataclass
class Seat:
    """One chair of the panel.

    Every seat is fully described by data (``panel.json``): a unique
    :attr:`seat_id`, a specialist :attr:`role` (+ optional :attr:`description`),
    the primary ``provider``/``model``, an optional fallback chain and an
    :attr:`enabled` flag. Adding, removing or re-specialising a seat is a config
    edit, never a code change.
    """

    seat_id: str         # unique identifier; drives artifact file names
    seat: str            # human-readable name
    role: str            # specialist persona (e.g. "Security & Vulnerability Audit")
    provider: str
    model: str
    tier: str = "core"
    max_tokens: int = 20000
    #: One fallback or a chain of them (tried in order when the primary is silent).
    fallback: Fallback | tuple[Fallback, ...] | None = None
    extra_body: dict = field(default_factory=dict)
    #: Free-form longer role description, appended to the role inside prompts.
    description: str = ""
    #: Per-seat activity flag; ``false`` keeps the seat configured but out of a run.
    enabled: bool = True
    #: Alias the model was resolved from ("" when a literal id was given).
    model_alias: str = ""
    #: Alias table: provider → ordered model ids (from ``panel.model_aliases``).
    model_table: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        """Legacy alias for :attr:`seat_id`."""
        return self.seat_id

    @property
    def fallback_chain(self) -> tuple[Fallback, ...]:
        """Normalised fallback chain (empty tuple when no fallback is configured)."""
        if self.fallback is None:
            return ()
        if isinstance(self.fallback, Fallback):
            return (self.fallback,)
        return tuple(self.fallback)

    def model_ids_for(self, provider: str, declared: str = "") -> tuple[str, ...]:
        """Ordered model ids to try for ``provider`` (per-provider naming).

        Precedence: an explicit per-provider ``declared`` id wins; then an alias
        entry for that provider; then the seat's own :attr:`model` — the documented
        inheritance default for a fallback that does not override ``model``.
        """
        if declared:
            return (declared,)
        table = self.model_table.get(provider)
        if table:
            return table
        return (self.model,)

    @property
    def role_line(self) -> str:
        """Role plus optional description, as shown to the model."""
        return f"{self.role} — {self.description}" if self.description else self.role


@dataclass
class Sections:
    """Answer-contract: which markdown headings a full answer must contain."""

    required: tuple[str, ...] = (
        "## Verdict",
        "## Critical findings",
        "## Major findings",
        "## Blind spots and action plan",
    )
    resume: tuple[str, ...] = (
        "## Blind spots and action plan",
    )


@dataclass
class Panel:
    """The whole quorum configuration."""

    providers: dict[str, ProviderConfig]
    seats: list[Seat]
    quorum_required: int = 3
    fail_closed: bool = True
    timeout_s: float = 300.0
    fallback_timeout_s: float = 300.0
    max_tokens_retry: int = 32768
    excluded_by_default: list[str] = field(default_factory=list)
    sections: Sections = field(default_factory=Sections)
    #: Logical model name → ``{provider: (ids...)}`` (per-provider naming SSOT).
    model_aliases: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)

    def provider(self, name: str) -> ProviderConfig:
        try:
            return self.providers[name]
        except KeyError as exc:  # pragma: no cover - validated on load
            raise KeyError(
                f"unknown provider {name!r} (known: {sorted(self.providers)})"
            ) from exc

    def seat_by_ref(self, ref: str) -> Seat | None:
        for seat in self.seats:
            if ref in (seat.seat_id, seat.seat, seat.model, seat.model_alias):
                return seat
        return None


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def resolve_model_aliases(seat: Seat, aliases: dict[str, dict[str, tuple[str, ...]]]) -> Seat:
    """Resolve a logical ``model`` alias into per-provider ids (in place).

    A literal model id (no matching alias key) is left untouched, so existing
    panels keep working. When the alias covers the primary provider, ``model`` is
    rewritten to that provider's preferred id and :attr:`Seat.model_table` keeps
    the full ordered list — the alternatives a provider can be re-tried with after
    it retires or renames a model, and the ids fallbacks inherit per provider.
    """
    table = aliases.get(seat.model)
    if not table:
        return seat
    seat.model_alias = seat.model
    seat.model_table = {prov: tuple(ids) for prov, ids in table.items() if ids}
    ids = seat.model_table.get(seat.provider)
    if ids:
        seat.model = ids[0]
    return seat


def normalize_aliases(raw: dict | None) -> dict[str, dict[str, tuple[str, ...]]]:
    """Merge the built-in alias map with a panel's ``model_aliases`` block."""
    merged: dict[str, dict[str, tuple[str, ...]]] = {
        name: {prov: tuple(ids) for prov, ids in table.items()}
        for name, table in DEFAULT_MODEL_ALIASES.items()
    }
    for name, table in (raw or {}).items():
        merged[name] = {prov: tuple(ids) for prov, ids in table.items()}
    return merged


def seat_file_name(seat: Seat) -> str:
    return f"{_slugify(seat.seat_id)}.json"


def resolve_seat_refs(panel: Panel, refs: list[str]) -> tuple[list[Seat], list[str]]:
    """Resolve ``--seats`` refs to seats, reporting refs that matched nothing.

    Matching is by ``seat_id``, display name or model id. Returning the unmatched
    refs lets the CLI fail loudly instead of silently running (and paying for) the
    whole panel on a typo — essential for targeted re-runs.
    """
    matched: list[Seat] = []
    unmatched: list[str] = []
    for ref in refs:
        seat = panel.seat_by_ref(ref)
        if seat is None:
            unmatched.append(ref)
        elif seat not in matched:
            matched.append(seat)
    return matched, unmatched


def default_panel() -> Panel:
    """The shipped 6-seat panel (no machine-specific data, no product ties)."""
    providers = {
        name: ProviderConfig(name=name, **cfg)
        for name, cfg in DEFAULT_PROVIDERS.items()
    }
    seats = [
        Seat("logic", "DeepSeek-V4-Pro", "Logic & Edge Case Auditor",
             "deepseek", "deepseek-v4-pro", "core", 20000,
             Fallback("openrouter", "deepseek/deepseek-v4-pro"),
             description="Hunts logic bugs, off-by-one errors and unhandled edge cases."),
        Seat("architect", "GLM-5.2", "Lead Architecture Critic",
             "siliconflow", "glm-5.2", "core", 65536,
             Fallback("openrouter", "z-ai/glm-5.2"),
             extra_body={"chat_template_kwargs": {"enable_thinking": False}},
             description="Layering, dependency direction and structural regressions."),
        Seat("long-context", "Kimi-K3", "Long-Context Analyst",
             "siliconflow", "kimi-k3", "core", 40000,
             Fallback("openrouter", "moonshotai/kimi-k3"),
             description="Cross-file inconsistencies and API-limit risks over the whole artifact."),
        Seat("code-expert", "Qwen3.8-Flash", "Code Expert & Refactoring",
             "siliconflow", "qwen3.8-flash", "core", 20000,
             Fallback("openrouter", "qwen/qwen3.8-flash"),
             description="Code quality, duplication and refactoring opportunities.",
             enabled=False),
        Seat("reliability", "LongCat-2.0", "Workflows & Reliability",
             "siliconflow", "longcat-2.0", "panel", 20000,
             Fallback("openrouter", "meituan/longcat-2.0"),
             description="Pipelines, retries, idempotency and failure modes."),
        Seat("security", "MiniMax-M3", "Security & Vulnerability Audit",
             "siliconflow", "minimax-m3", "panel", 20000,
             Fallback("openrouter", "minimax/minimax-m3"),
             description="Auth/tenant scoping, secret handling, injection and abuse paths."),
    ]
    aliases = normalize_aliases(None)
    seats = [resolve_model_aliases(seat, aliases) for seat in seats]
    return Panel(
        providers=providers,
        seats=seats,
        quorum_required=3,
        fail_closed=True,
        timeout_s=600.0,
        fallback_timeout_s=300.0,
        max_tokens_retry=32768,
        excluded_by_default=["code-expert"],
        model_aliases=aliases,
    )


def _provider_from_cfg(name: str, cfg: dict) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        base_url=cfg["base_url"],
        api_key_env=tuple(cfg.get("api_key_env") or ()),
        extra_headers=dict(cfg.get("extra_headers") or {}),
    )


def _fallback_from_raw(raw) -> Fallback | tuple[Fallback, ...] | None:
    """Accept a single fallback object or a list of them (a fallback chain).

    ``model`` is optional per entry: when omitted, the seat's own ``model`` is
    used — or the model alias entry for that provider, which is the preferred way
    to give each provider its own model name.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return Fallback(raw["provider"], raw.get("model", ""))
    items = tuple(Fallback(item["provider"], item.get("model", "")) for item in raw)
    return items or None


def _panel_from_dict(data: dict, inherit: dict[str, ProviderConfig] | None = None) -> Panel:
    providers: dict[str, ProviderConfig] = dict(inherit or {})
    for name, cfg in (data.get("providers") or {}).items():
        providers[name] = _provider_from_cfg(name, cfg)

    aliases = normalize_aliases(data.get("model_aliases"))
    seats: list[Seat] = []
    for raw in data.get("seats") or []:
        seat_id = (raw.get("seat_id") or raw.get("slug") or raw.get("id")
                   or raw["model"])
        seats.append(resolve_model_aliases(Seat(
            seat_id=seat_id,
            seat=raw.get("seat") or raw.get("name") or seat_id,
            role=raw.get("role", ""),
            provider=raw["provider"],
            model=raw["model"],
            tier=raw.get("tier", "core"),
            max_tokens=int(raw.get("max_tokens", 20000)),
            fallback=_fallback_from_raw(raw.get("fallback")),
            extra_body=dict(raw.get("extra_body") or {}),
            description=raw.get("description", ""),
            enabled=(bool(raw.get("enabled", True))
                     and not bool(raw.get("excluded_by_default", False))),
        ), aliases))

    quorum = data.get("quorum") or {}
    timeouts = data.get("timeouts") or {}
    sections_cfg = data.get("sections") or {}
    defaults = Sections()
    return Panel(
        providers=providers,
        seats=seats,
        quorum_required=int(quorum.get("required", 3)),
        fail_closed=bool(quorum.get("fail_closed", True)),
        timeout_s=float(timeouts.get("primary_s", 300.0)),
        fallback_timeout_s=float(timeouts.get("fallback_s", 300.0)),
        max_tokens_retry=int(timeouts.get("max_tokens_retry", 32768)),
        excluded_by_default=list(data.get("excluded_by_default") or []),
        sections=Sections(
            required=tuple(sections_cfg.get("required") or defaults.required),
            resume=tuple(sections_cfg.get("resume") or defaults.resume),
        ),
        model_aliases=aliases,
    )


def _panel_from_council(data: dict) -> Panel:
    """Import a legacy ``council.json`` (the older SSOT format).

    Best-effort: provider/model ids are inferred from the slug via
    :data:`LEGACY_SLUG_MAP`; anything unknown is routed through OpenRouter,
    which accepts the slug verbatim. Every seat gets an OpenRouter fallback.
    """
    providers = {
        name: ProviderConfig(name=name, **cfg)
        for name, cfg in DEFAULT_PROVIDERS.items()
    }
    excluded = list(data.get("excluded_by_default") or [])
    seats: list[Seat] = []
    for raw in data.get("models") or []:
        slug = raw["slug"]
        provider, model = LEGACY_SLUG_MAP.get(slug, ("openrouter", slug))
        seats.append(Seat(
            seat_id=slug,
            seat=slug.rsplit("/", 1)[-1],
            role=raw.get("role", ""),
            provider=provider,
            model=model,
            tier=raw.get("tier", "core"),
            max_tokens=int(raw.get("max_output_tokens", 20000)),
            fallback=Fallback("openrouter", slug),
            description=raw.get("description", ""),
            enabled=slug not in excluded,
        ))
    quorum = data.get("quorum") or {}
    return Panel(
        providers=providers,
        seats=seats,
        quorum_required=int(quorum.get("core_required", 3)),
        fail_closed=bool(quorum.get("fail_closed", True)),
        timeout_s=float(data.get("model_timeout_s", 300.0)),
        fallback_timeout_s=float(data.get("model_timeout_s", 300.0)),
        excluded_by_default=list(data.get("excluded_by_default") or []),
        model_aliases=normalize_aliases(None),
    )


def load_panel(path: Path | None) -> Panel:
    """Load a panel from ``panel.json`` or a legacy ``council.json``.

    ``path=None`` returns the built-in 6-seat default; a missing explicit path
    raises :class:`FileNotFoundError`.
    """
    if path is None:
        return default_panel()
    if not path.exists():
        raise FileNotFoundError(f"panel file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    # Legacy council.json is detected structurally (it has ``models``, not ``seats``).
    if "models" in data and "seats" not in data:
        return _panel_from_council(data)
    return _panel_from_dict(data)


def effective_seats(panel: Panel, only: list[str] | None = None,
                    include_excluded: bool = False,
                    exclude_env: str | None = None) -> list[Seat]:
    """Seats of the run: the ``--seats`` subset minus disabled/excluded seats.

    A seat is out when its own :attr:`~Seat.enabled` flag is false **or** its
    ``seat_id`` is listed in ``panel.excluded_by_default`` (the legacy list is
    still honoured). ``--include-excluded`` brings both back. Passing
    ``exclude_env`` (``COUNCIL_EXCLUDE_MODELS``) replaces the shipped exclusion
    policy entirely — an empty string re-enables every configured seat.

    The result is never empty: rather lose a filter than the whole council.
    """
    selection = panel.seats
    if only:
        matched = [s for s in panel.seats
                   if any(ref in (s.seat_id, s.seat, s.model) for ref in only)]
        if matched:
            selection = matched

    overridden = include_excluded or exclude_env is not None
    if overridden:
        excluded: set[str] = set()
        if exclude_env is not None:
            excluded = {item for item in exclude_env.replace(",", " ").split() if item}
    else:
        excluded = set(panel.excluded_by_default)

    filtered = [s for s in selection
                if s.seat_id not in excluded and (s.enabled or overridden)]
    return filtered or list(selection)
