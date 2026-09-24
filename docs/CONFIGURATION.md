# Configuration reference — `panel.json`

`panel.json` is the single source of truth for the panel: providers, seats,
per-provider model naming, quorum rules and the answer contract. It is pure data —
no code change is needed to add a model, a provider or a role.

## Top-level structure

| Key | Type | Required | Purpose |
|---|---|---|---|
| `providers` | object | yes | OpenAI-compatible endpoints, keyed by provider name |
| `seats` | array | yes | the panel's chairs (one object per seat) |
| `model_aliases` | object | no | logical model name → per-provider id(s) |
| `quorum` | object | no | `{ required, fail_closed }` (defaults `3` / `true`) |
| `timeouts` | object | no | `{ primary_s, fallback_s, max_tokens_retry }` |
| `excluded_by_default` | array | no | `seat_id`s disabled unless `--include-excluded` |
| `sections` | object | no | the markdown answer contract (`required` / `resume`) |

## Providers

```jsonc
"providers": {
  "siliconflow": {
    "base_url": "https://api.siliconflow.com/v1",
    "api_key_env": ["SILICONFLOW_API_KEY", "SILICONFLOW_KEY"],
    "extra_headers": { }               // optional static headers
  },
  "ollama": {
    "base_url": "http://127.0.0.1:11434/v1",
    "api_key_env": ["OLLAMA_API_KEY"]
  }
}
```

* `base_url` — the OpenAI-compatible root: chat calls hit
  `{base_url}/chat/completions`, and `verify-models` hits `{base_url}/models`.
* `api_key_env` — ordered list of environment variables read for the bearer key
  (process environment first, then env files).
* `extra_headers` — optional static headers merged into every request.

Built-in providers: `deepseek`, `siliconflow`, `openrouter`. Local/private
providers (Ollama, vLLM, LiteLLM, ...) are added the same way.

## Seats

A seat is a fully-described chair. Full field reference:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `seat_id` | string | — | unique id; drives `runs/<seat_id>.json` (aliases: `slug`, `id`) |
| `seat` | string | — | human-readable display name |
| `role` | string | — | specialist persona injected into the prompt |
| `description` | string | `""` | longer brief, appended to `role` in the prompt |
| `provider` | string | — | primary provider name (must exist in `providers`) |
| `model` | string | — | logical alias or literal model id for the primary |
| `tier` | string | `core` | free-form grouping (informational) |
| `max_tokens` | int | `20000` | per-call output budget |
| `max_context_tokens` | int | built-in table | context-window budget (input tokens) for `--preflight`; an explicit value wins over the built-in defaults |
| `fallback` | object/array | none | secondary provider(s), tried in order |
| `base_url` | string | `""` | endpoint override for this seat |
| `extra_body` | object | `{}` | merged into the chat-completions payload |
| `enabled` | bool | `true` | `false` keeps the seat configured but out of runs |
| `excluded_by_default` | bool | `false` | per-seat alias for `"enabled": false` |

### Fallback chains

`fallback` is a single object or an ordered list (tried in order when the previous
provider produced no visible text):

```jsonc
"fallback": [
  { "provider": "openrouter", "model": "deepseek/deepseek-v4" },
  { "provider": "deepseek",   "model": "deepseek-chat" }   // Native API name
]
```

Each entry accepts `provider`, `model` (optional) and `base_url` (optional). When
`model` is omitted the seat's own `model` is used — or, preferably, the
per-provider alias (below).

### Per-provider model mapping (`model_aliases`)

Model ids are provider-specific (`zai-org/GLM-5.2` on SiliconFlow vs `z-ai/glm-5.2`
on OpenRouter). `model_aliases` is the naming SSOT; a seat then declares the
logical name:

```jsonc
"model_aliases": {
  "glm-5.2": {
    "siliconflow": ["zai-org/GLM-5.2", "zai-org/GLM-5.3"],
    "openrouter":  ["z-ai/glm-5.2", "z-ai/glm-5.3"]
  }
}
```

The per-provider list is ordered; the extras are re-tried when a provider reports
the model as gone (HTTP 404 / "model not found"), so a rename costs one extra
request instead of a lost seat.

Resolution order for the id actually sent to a provider:

1. the explicit `model` on that seat / fallback entry — always wins;
2. the `model_aliases` entry for that provider;
3. the seat's own `model` (the documented default when a fallback omits `model`).

## Quorum, timeouts & sections

```jsonc
"quorum":   { "required": 3, "fail_closed": true },
"timeouts": { "primary_s": 600, "fallback_s": 300, "max_tokens_retry": 32768 },
"sections": {
  "required": ["## Verdict", "## Critical findings", "## Major findings", "## Blind spots and action plan"],
  "resume":   ["## Blind spots and action plan"]
}
```

* `quorum.required` — minimum seats that must answer for a valid verdict.
* `quorum.fail_closed` — `true` exits non-zero when the quorum is not met
  (`--soft-quorum` overrides at run time).
* `timeouts.primary_s` / `timeouts.fallback_s` — per-call budgets.
* `timeouts.max_tokens_retry` — an over-large `max_tokens` (HTTP 400/413) is
  retried once with this budget.
* `sections.required` — headings a full answer must contain; `sections.resume` —
  headings a continuation (`kvorum resume`) must produce.

## Pre-flight check (`--preflight` / `--skip-overflow`)

`--preflight` (on `run` / `dry-run`) compares the assembled packet against each
seat's context budget before any API call:

* The packet size is estimated as `len(text) // 4` tokens (plus its UTF-8 bytes).
* A seat's budget is its `max_context_tokens` when set, otherwise a built-in
  per-model table: `deepseek-v4-pro` / `qwen3.8-flash` → 128 k, `kimi-k3` /
  `minimax-m3` → 256 k, `glm-5.2` / `longcat-2.0` → 1 M; unknown models → 1 M.

Any `OVERFLOW` blocks the run (exit 1) by default. `--skip-overflow` drops the
overflowing seats and recomputes the quorum from the survivors; if fewer than
`quorum.required` remain, the run blocks with `INSUFFICIENT_SEATS`.

## Legacy `council.json`

An older `models`-based format is still importable: pass it with `--panel` and it
is detected structurally (`models` instead of `seats`). Slugs are mapped to
provider/model ids via a built-in table; unknown slugs route through OpenRouter.
Prefer the `seats` schema for anything new.

## Environment overrides

Every path/knob has a `KVORUM_*` environment override (CLI flags win over env,
env wins over `panel.json`):

| Variable | Purpose |
|---|---|
| `KVORUM_PANEL` | panel path (default `panel.json`) |
| `KVORUM_PACKET` | packet path (default `packet.md`) |
| `KVORUM_RULES` | rules path (default `rules.md`) |
| `KVORUM_RUNS_DIR` | runs directory (default `runs`) |
| `KVORUM_VERDICT` / `KVORUM_VERDICT_JSON` | verdict output paths |
| `KVORUM_ENV_FILE` | credential file(s), `:`-separated |
| `KVORUM_TIMEOUT_S` / `KVORUM_FALLBACK_TIMEOUT_S` | per-call budgets |
| `KVORUM_MAX_TOKENS_RETRY` | retry budget |
| `KVORUM_QUORUM_REQUIRED` / `KVORUM_FAIL_CLOSED` | quorum overrides |
| `KVORUM_MIN_DELAY_S` | pause between seat calls |
| `COUNCIL_EXCLUDE_MODELS` | replaces the shipped seat-exclusion list |
| `<NAME>_BASE_URL` | endpoint override for provider `name` (e.g. `SILICONFLOW_BASE_URL`) |

