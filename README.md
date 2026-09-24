# kvorum

> **Cross-review by an ensemble of heterogeneous LLMs, with a fail-closed quorum.**
> One artifact goes out, a panel of independent specialist models answers, and the
> verdict is authoritative only if a minimum number of seats really answered.

`kvorum` is a dependency-light CLI that runs a multi-model review **ensemble**.
It has no project-specific logic, no hard-coded paths and makes no network calls
beyond the OpenAI-compatible chat-completions endpoints you configure. The panel,
its providers, models, roles and fallback chains all live in data (`panel.json`);
adding or re-specialising a seat is a config edit, never a code change.

## The ensemble

The shipped `panel.json` seats a six-model panel across **SiliconFlow** and
**OpenRouter** (DeepSeek native for one seat), each with its own review persona.
Five seats are enabled by default; `code-expert` ships disabled and is re-enabled
with `--include-excluded`:

| # | Seat | Role | Primary provider / model |
|---|---|---|---|
| 1 | DeepSeek-V4-Pro | Logic & Edge Case Auditor | DeepSeek native — `deepseek-v4-pro` |
| 2 | GLM-5.2 | Lead Architecture Critic | SiliconFlow — `zai-org/GLM-5.2` |
| 3 | Kimi-K3 | Long-Context Analyst | SiliconFlow — `moonshotai/Kimi-K3` |
| 4 | Qwen3.8-Flash | Code Expert & Refactoring | SiliconFlow — `Qwen/Qwen3.8-2.4T-A95B` *(excluded by default)* |
| 5 | LongCat-2.0 | Workflows & Reliability | SiliconFlow — `meituan-longcat/LongCat-2.0` |
| 6 | MiniMax-M3 | Security & Vulnerability Audit | SiliconFlow — `MiniMaxAI/MiniMax-M3` |

Every seat has an **OpenRouter fallback** used only when the primary provider
returns no visible text (timeout / 5xx / outage). The **quorum** is
`required: 3` and **fail-closed**: fewer real answers and the run exits non-zero.
The disabled `code-expert` seat is re-enabled with `--include-excluded`, or with
an explicit `COUNCIL_EXCLUDE_MODELS=` (an empty value clears the exclusion list).

## Quickstart

```bash
pip install .
cp .env.example .env && chmod 600 .env   # OPENROUTER/SILICONFLOW/DEEPSEEK keys

kvorum pack  --include 'src/**/*.py' --ast-summary --max-chars 120000
kvorum list-seats                         # availability, no API calls
kvorum dry-run                            # prompts to runs/prompts/, no API calls
kvorum run                                # real cross-review (costs money)
kvorum verdict                            # verdict.md + verdict.json
```

Python 3.10+. Runtime dependency: `httpx`.

## Configuration

The panel is pure data (`panel.json`) — adding a model is a config change, not a
code change:

```jsonc
{
  "providers": {
    "siliconflow": { "base_url": "https://api.siliconflow.com/v1",
                     "api_key_env": ["SILICONFLOW_API_KEY"] },
    "openrouter":  { "base_url": "https://openrouter.ai/api/v1",
                     "api_key_env": ["OPENROUTER_API_KEY"] },
    "ollama":      { "base_url": "http://127.0.0.1:11434/v1",
                     "api_key_env": ["OLLAMA_API_KEY"] }
  },
  "seats": [
    {
      "seat_id": "code-expert",
      "seat": "Code Expert",
      "role": "Code Expert",
      "description": "Code quality, duplication and refactoring opportunities.",
      "provider": "siliconflow",
      "model": "Qwen/Qwen3.8-2.4T-A95B",
      "tier": "core",
      "max_tokens": 20000,
      "fallback": { "provider": "openrouter", "model": "qwen/qwen3.8-flash" }
    }
  ],
  "quorum": { "required": 2, "fail_closed": true },
  "timeouts": { "primary_s": 300, "fallback_s": 300, "max_tokens_retry": 32768 },
  "excluded_by_default": [],
  "sections": {
    "required": ["## Verdict", "## Critical findings", "## Blind spots and action plan"],
    "resume":   ["## Blind spots and action plan"]
  }
}
```

Every provider is an OpenAI-compatible endpoint: cloud hosts (OpenRouter,
DeepSeek, SiliconFlow) and local servers (Ollama, vLLM, LiteLLM) are configured
identically via `base_url` + `api_key_env`.

A legacy `council.json` (the older `models`-based SSOT) is imported when you pass
it explicitly: `kvorum list-seats --panel council.json`.

Secrets precedence: **process environment → env files** (`.env` by default;
`--env-file` / `KVORUM_ENV_FILE` replace it, and earlier files win in a
`:`-separated list). Keys are used only in `Authorization` headers and are never
printed; error text is redacted.

See **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** for the complete
`panel.json` schema — every seat field, fallback chains, per-provider
`model_aliases`, `base_url` overrides, `quorum`, `timeouts` and `sections`.

## Customizing seats & per-provider models

Every seat is a self-contained slot in `panel.json` — add, remove or re-specialise
a chair without touching code. The full field reference (including `role`,
`description`, `enabled`, fallback chains, `extra_body`, `base_url` and
per-provider `model_aliases`) lives in
**[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**; the essentials:

```jsonc
{
  "seat_id": "security",                       // unique id; names runs/security.json
  "seat": "MiniMax-M3",                        // display name
  "role": "Security & Vulnerability Audit",    // specialist persona (goes into the prompt)
  "description": "Auth/tenant scoping, secret handling, injection and abuse paths.",
  "provider": "siliconflow",                   // primary provider
  "model": "minimax-m3",                       // logical name or literal id (see aliases)
  "tier": "panel",
  "max_tokens": 20000,
  "enabled": true,
  "fallback": [ { "provider": "openrouter", "model": "minimax/minimax-m3" } ]
}
```

Model ids are **provider-specific**: a fallback entry carries its own `model`, and
`model_aliases` maps a logical name to per-provider ids (with ordered spares for
renames). Resolution order: explicit `model` on the seat/fallback → the alias entry
for that provider → the seat's own `model`. `kvorum verify-models` prints the
resolved ids per provider, and `kvorum list-seats` prints the resolved panel.

## Providers, fallbacks & custom endpoints

`kvorum` is **not tied to a single provider**. Every seat declares a `provider`
(primary) and an optional `fallback` chain; flip OpenRouter ⇄ SiliconFlow ⇄
Ollama/vLLM by editing `panel.json` or picking a preset (`--panel
examples/panel.openrouter.json`) — no code change.

> ⚠️ **Always run a dual-provider setup (primary + fallback).** Providers
> throttle hard (HTTP 429 / RPM limits) and slow reasoning models can exceed the
> read timeout; a configured fallback moves the seat to the secondary provider
> instead of dropping it. Leaving the second key unset or `--no-fallback` means
> one provider hiccup can drop the run below quorum.

Endpoints are also data: a seat/fallback can override `base_url` (a vLLM / Ollama /
LiteLLM gateway), and a `<NAME>_BASE_URL` env var overrides a whole provider.
Precedence: seat/fallback `base_url` → `<NAME>_BASE_URL` env → provider default.

The full deep-dive — presets, dual-provider caution, custom endpoints & local
proxies, rate-limit / serial-run design and execution-time budgeting — lives in
**[docs/PROVIDERS.md](docs/PROVIDERS.md)**.

## Notifications (ntfy / webhook)

When a run finishes, `kvorum` can push a one-line summary to **ntfy.sh** and/or
any **generic webhook** (a Slack/Telegram relay, CI, ...). No backend is required.

* `KVORUM_NTFY_TOPIC` → POST the summary text to `https://ntfy.sh/<topic>` with a
  `Title: kvorum Audit Finished` header.
* `KVORUM_NOTIFY_WEBHOOK` → POST a JSON summary (verdict, votes, quorum, tokens,
  elapsed seconds, cost when the provider reports it).

```bash
export KVORUM_NTFY_TOPIC=kvorum-<your-topic>
export KVORUM_NOTIFY_WEBHOOK=https://hooks.example.com/kvorum
kvorum run --notify          # --notify is implied once either variable is set
```

Notifications are **fail-safe**: a 2 s timeout and a swallowed network error can
never fail the run.

## CLI

| Command | Purpose | Key flags |
|---|---|---|
| `kvorum list-seats` | seat availability + effective panel (no chat calls) | — |
| `kvorum verify-models` | per-provider model resolution vs live `/models` (free) | — |
| `kvorum pack` | build the review packet | `--include` `--exclude` `--manifest` `--max-chars` `--ast-summary` `--out` |
| `kvorum dry-run` | write prompts to disk; **no API calls** | `--seats` `--include-excluded` `--resume` |
| `kvorum run` | real cross-review; archives the previous run | `--seats` `--skip-seats` `--include-excluded` `--no-fallback` `--soft-quorum` `--no-archive` `--timeout` `--fallback-timeout` `--min-delay-s` `--notify` |
| `kvorum resume` | continue answers truncated by `max_tokens` | same flags as `run` |
| `kvorum verdict` | assemble `verdict.md` + `verdict.json` | `--verdict` `--verdict-json` |

Common flags (all subcommands): `--panel`, `--packet`, `--rules`, `--runs-dir`,
`--env-file`. `--seats` and `--only-seats` are aliases.

Exit codes: `0` quorum met · `1` fail-closed (quorum not met) · `2` usage /
configuration error (missing packet, unknown seat reference, bad panel).

### Targeted re-runs (cheap offsets)

Naming seats re-runs **only** those seats; the results **merge into the existing
`runs/`** (`run_meta.json` is updated in place, previously successful seats are
never erased), and a partial run never archives the directory:

```bash
kvorum run --seats architect,long-context   # re-run just these two seats
kvorum run --skip-seats security            # run everything except this seat
kvorum run --only-seats architect           # alias of --seats
kvorum verdict                              # rebuild the verdict from the merged runs/
```

An unknown seat reference is a hard error (exit 2) listing the known seats — a
typo can never silently re-run (and pay for) the whole panel. Use `kvorum
list-seats` to see valid ids.

## How the quorum works

* **Seats, not calls.** Each seat is a persona bound to a provider + model id +
  output budget.
* **Quorum, fail-closed.** Fewer real answers than `quorum.required` → exit code 1.
* **Auditable fallbacks.** A seat retries on a secondary provider only when the
  primary returned no visible text; every attempt (http, ms, tokens, error) is
  recorded.
* **Resumable answers.** An answer cut off by `max_tokens` (thinking models burn
  the budget on reasoning tokens) is continued with `resume`, which asks for the
  missing sections only and merges with hashes and summed usage.
* **Deterministic consolidation.** `verdict` merges seats locally: any
  `CHANGES REQUESTED` or non-empty critical-findings section blocks the review.

## Context & AST

`pack` builds the review packet from globs or a manifest with a hard character
budget. `--ast-summary` **adds** a compact symbols/signatures/imports section
(stdlib `ast`, Python-only) on top of the embedded sources — the bodies are still
pasted until `--max-chars` is reached, so it is a navigation aid, not a
body-replacement switch.

## Artifacts

```
runs/
  <slug>.json            # answer + http/ms/usage/attempts (+ .continuation.json on resume)
  run.log                # one line per seat
  run_meta.json          # panel, exclusions, quorum, timeouts, per-seat status
  prompts/               # dry-run output
verdict.md / verdict.json
runs.<UTC stamp>/        # archived previous run
```

## Security

No telemetry. Outbound traffic is limited to the provider chat-completions calls
you configure and, only if you opt in, the completion notification (ntfy.sh / your
webhook). Keys are redacted in logs and errors. Artifact secret-scanning helpers
live in `kvorum.artifacts`; wiring them into the run path is a v0.2 item (see the
Roadmap). Review packets are built only from files you select.

## Dogfooding & Real-World Benchmark

kvorum reviews **its own source**. The live run below used the OpenRouter preset
(`examples/panel.openrouter.json` — OpenRouter primary for every seat, SiliconFlow /
DeepSeek native as fallbacks), a 6-seat quorum *including* the default-excluded
`code-expert` seat, and OpenRouter's own reported per-call cost:

```bash
kvorum pack --include 'src/**/*.py' --include 'tests/**/*.py' \
            --include '*.md' --include '*.toml' --include 'examples/*.json' \
            --max-chars 200000                       # -> 153.7 KB packet
kvorum run --panel examples/panel.openrouter.json \
           --min-delay-s 5 --timeout 600 --include-excluded
kvorum verdict
```

| Metric | Value |
|---|---|
| Packet | **157 352 bytes ≈ 153.7 KB** · 157 176 chars · ~39 k tokens (est.) · 27 files / 6 328 lines |
| Panel | 6 seats — **all answered on the OpenRouter primary, 0 fallbacks used** |
| Wall-clock | **≈ 24 min** (06:15:34 → 06:38:32 UTC) · 1 353 s of model latency |
| Tokens | **253 662 in / 88 559 out = 342 221 total** |
| Cost | **$0.6576** — actual `usage.cost` reported by OpenRouter |
| Quorum | **MET** — 6/6 answered (required 3, fail-closed) |
| Verdict | **`CHANGES REQUESTED`** — votes `{CHANGES REQUESTED: 5, n/a: 1}`, 25 critical findings |

| Seat | OpenRouter model | HTTP | Time (s) | In | Out | Cost ($) |
|---|---|---|---|---|---|---|
| DeepSeek-V4-Pro | `deepseek/deepseek-v4-pro` | 200 | 452.7 | 43 962 | 19 610 | 0.1006 |
| GLM-5.2 | `z-ai/glm-5.2` | 200 | 49.2 | 40 965 | 5 144 | 0.0800 |
| Kimi-K3 | `moonshotai/kimi-k3` | 200 | 195.1 | 40 852 | 18 590 | 0.4014 |
| Qwen3.8-Flash | `qwen/qwen3.8-flash` | 200 | 229.5 | 44 377 | 14 137 | 0.0133 |
| LongCat-2.0 | `meituan/longcat-2.0` | 200 | 296.0 | 41 890 | 16 830 | 0.0328 |
| MiniMax-M3 | `minimax/minimax-m3` | 200 | 130.5 | 41 616 | 14 248 | 0.0296 |
| **Total** | | | **1 353** | **253 662** | **88 559** | **0.6576** |

The prompt side (~41–44 k tokens per seat) matches the 153.7 KB packet, so the
`chars / 4` estimate held. An earlier **SiliconFlow-primary** run of the same panel
answer 3/5 seats and needed `--min-delay-s` (SiliconFlow returned HTTP 429) plus
`--timeout 600` (Kimi-K3 answered at 355 s, past a 300 s cap) — the OpenRouter
primary run above answered 6/6 without a single fallback.

### What the review found

All six seats returned `CHANGES REQUESTED`. MiniMax-M3 produced a full answer
(9 934 chars) but wrapped it in a reasoning block, so its verdict heading did not
parse (`n/a`) — the recurring format fragility (Roadmap #7). Beyond the tracked
backlog, this round surfaced:

* **`consolidate()` can fail open** — a panel whose answers are all unparseable
  (`n/a`) consolidates to `APPROVE`, contradicting the fail-closed contract
  (Kimi-K3). The single `n/a` seat above is that path in miniature.
* **`run` and `verdict` disagree on quorum** — `build_verdict_json` reads
  `panel.quorum_required`/`fail_closed`, while `run` applies the effective
  `KVORUM_*`/CLI overrides, so an exit-0 run can still yield `"met": false` (GLM-5.2).
* **`--ast-summary` is additive, not a body replacement** — it matched neither its
  own CLI help nor the previous README wording (DeepSeek-V4-Pro); the docs above
  are now corrected.
* **`verdict` globs every `runs/*.json`** without filtering to the current panel, so
  retired seats can leak into an authoritative verdict (Qwen3.8-Flash).
* **`--seats` bypasses `Seat.enabled`/`excluded_by_default`** (Qwen3.8-Flash), and a
  non-empty `COUNCIL_EXCLUDE_MODELS` re-enables per-seat-disabled seats
  (DeepSeek-V4-Pro) — both can run paid seats unintentionally.
* **`scan_secrets` is still unwired**, and its patterns duplicate/diverge from
  `config.REDACT` (Qwen3.8-Flash).

## Roadmap: v0.2 Backlog

Issues surfaced by the 6-seat self-reviews above (25 critical findings in the
latest run). Items 1 and 3 were closed while adding customizable seats and
targeted re-runs; the rest are intentionally left for v0.2:

1. ~~**`resume` broke with the default `--archive` (critical)**~~ — **fixed**:
   partial and `resume` runs no longer archive the directory, and targeted re-runs
   merge into `runs/` instead of replacing it.
2. **Critical findings are over-counted** — any non-empty line, including
   "No critical findings.", counts as a blocking critical.
3. ~~**`--seats` typos were silent**~~ — **fixed**: an unknown seat reference now
   exits 2 with the list of known seats.
4. **Secret scan is not wired into the run path** — `artifacts.scan_secrets` is never
   called by `pack`/`run`/`verdict`; sources and answers are persisted unredacted.
5. **`pack --max-chars` truncates silently** — dropped files remain in the inventory.
6. **`merge_resume` raises `FileNotFoundError`** when a seat has no prior answer.
7. **`extract_verdict` is format-fragile** — misses bold `**APPROVE**` and answers
   wrapped in `<think>…</think>` (a real 6/6-run case) → reports "n/a".
8. **`pack` manifest/include ordering** — manifest entries are prepended to includes.
9. **`merge_resume` keeps stale top-level metadata** (provider/model/http/ms).
10. **`consolidate()` fails open** — a panel whose answers are all unparseable
    (`n/a`) consolidates to `APPROVE`, contradicting the fail-closed contract.
11. **`run` and `verdict` disagree on quorum** — `build_verdict_json` reads the
    panel values instead of the effective `KVORUM_*`/CLI overrides recorded in
    `run_meta.json`, so an exit-0 run can yield `"met": false`.
12. **`--ast-summary` is additive, not exclusive** — it adds a summary on top of
    the embedded bodies (CLI help/README wording now corrected).
13. **`verdict` mixes stale seat artifacts** — it globs every `runs/*.json`
    regardless of the current panel, so retired seats can enter the verdict.
14. **`dry-run` does not validate `--seats`** — a typo silently writes prompts for
    the whole panel (`run` correctly exits 2).

## Documentation

* [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — full `panel.json` schema:
  providers, seats, fallback chains, per-provider `model_aliases`, `base_url`,
  `quorum`, `timeouts`, `sections`, and `KVORUM_*` environment overrides.
* [docs/PROVIDERS.md](docs/PROVIDERS.md) — presets, dual-provider caution,
  custom endpoints & local proxies, rate-limit / serial-run design, execution-time
  budgeting.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest          # network-free (httpx.MockTransport)
```

## License

MIT. Not affiliated with any model provider; you pay for your own API usage.
