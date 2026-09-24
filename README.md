# kvorum

> **Cross-review by an ensemble of heterogeneous LLMs, with a fail-closed quorum.**
> One artifact goes out, six independent models answer, and the verdict is
> authoritative only if a minimum number of seats really answered.

`kvorum` is a dependency-light CLI that runs a multi-model review **ensemble**.
It has no project-specific logic, no hard-coded paths and makes no network calls
beyond the OpenAI-compatible chat-completions endpoints you configure.

## The ensemble

The shipped `panel.json` seats a six-model panel across **SiliconFlow** and
**OpenRouter** (DeepSeek native for one seat), each with its own review persona:

| # | Seat | Role | Provider / model |
|---|---|---|---|
| 1 | DeepSeek-V4-Pro | Logic / Bug-Hunter | DeepSeek native — `deepseek-v4-pro` |
| 2 | GLM-5.2 | System Architect | SiliconFlow — `zai-org/GLM-5.2` |
| 3 | Kimi-K3 | Long-Context Analyst | SiliconFlow — `moonshotai/Kimi-K3` |
| 4 | Qwen3.8-Flash | Code Expert / Refactoring | SiliconFlow — `Qwen/Qwen3.8-2.4T-A95B` *(excluded by default)* |
| 5 | LongCat-2.0 | Workflows & Reliability | SiliconFlow — `meituan-longcat/LongCat-2.0` |
| 6 | MiniMax-M3 | Adversarial / Blind-Spot Reviewer | SiliconFlow — `MiniMaxAI/MiniMax-M3` |

Every seat has an **OpenRouter fallback** that is used only when the primary
provider returns no visible text (timeout / 5xx / outage). The **quorum** is
`required: 3` and **fail-closed**: fewer real answers and the run exits non-zero.
`qwen/qwen3.8-flash` is excluded by default and re-enabled with
`--include-excluded` or `COUNCIL_EXCLUDE_MODELS=`.

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
    { "slug": "qwen", "seat": "Code Expert", "role": "Code Expert",
      "provider": "siliconflow", "model": "Qwen/Qwen3.8-2.4T-A95B",
      "tier": "core", "max_tokens": 20000,
      "fallback": { "provider": "openrouter", "model": "qwen/qwen3.8-flash" } }
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

Secrets precedence: **process env → `./.env` → `--env-file`**. Keys are used only
in `Authorization` headers and are never printed; error text is redacted.

## Customizing Panel Seats & Roles

Every seat is a self-contained slot in `panel.json` — add, remove or
re-specialise a chair without touching a line of code:

```jsonc
{
  "seat_id": "security",                       // unique id; names runs/security.json
  "seat": "MiniMax-M3",                        // display name
  "role": "Security & Vulnerability Audit",    // specialist persona (goes into the prompt)
  "description": "Auth/tenant scoping, secret handling, injection and abuse paths.",
  "provider": "siliconflow",                   // primary provider
  "model": "MiniMaxAI/MiniMax-M3",             // primary model id
  "tier": "panel",
  "max_tokens": 20000,
  "enabled": true,                             // false = configured, but not run by default
  "fallback": { "provider": "openrouter", "model": "minimax/minimax-m3" }
}
```

* **Add a seat** — append another object. Give it a new `seat_id`, a `role` and a
  `fallback`; nothing else to change.
* **Remove a seat** — delete the object, or set `"enabled": false` to keep it
  configured but out of the run (also settable via the alias
  `"excluded_by_default": true`).
* **Re-specialise a role** — edit `role` / `description`. Both are injected into
  the prompt (`kvorum dry-run` shows the exact text), so a seat can become
  "Security & Vulnerability Audit" or "Logic & Edge Case Auditor" without code.
* **Fallback chain** — `fallback` also accepts a list, tried in order:
  `"fallback": [{"provider": "siliconflow", "model": "…"}, {"provider": "openrouter", "model": "…"}]`.

### Per-provider model mapping & aliases

A model id is **provider-specific**, so kvorum never assumes one name fits all:
each fallback entry carries its own `model`, and the runner sends exactly that
name to that provider.

```jsonc
{
  "seat_id": "lead_auditor",
  "seat": "DeepSeek-V4-Pro",
  "role": "Lead Architecture Critic",
  "provider": "siliconflow",
  "model": "deepseek-ai/DeepSeek-V4",                    // name for the primary
  "fallback": [
    { "provider": "openrouter", "model": "deepseek/deepseek-v4" },
    { "provider": "deepseek",   "model": "deepseek-chat" }   // Native API name
  ]
}
```

Resolution order for the id sent to a provider:

1. the explicit `model` on that seat / fallback entry — always wins;
2. the `model_aliases` entry for that provider (logical name → per-provider ids);
3. the seat's own `model` — the documented default when a fallback omits `model`.

`model_aliases` is the naming SSOT for seats that share a logical model, and it
may list **several ids per provider**. The extras are tried in order when a
provider reports the model as gone (HTTP 404 / "model not found"), so a
provider-side rename costs one extra request instead of a lost seat:

```jsonc
"model_aliases": {
  "glm-5.2": {
    "siliconflow": ["zai-org/GLM-5.2", "zai-org/GLM-5.3"],
    "openrouter":  ["z-ai/glm-5.2", "z-ai/glm-5.3"]
  }
}
```

A seat then simply declares `"model": "glm-5.2"` and every provider gets its own
id. `kvorum verify-models` prints the resolved ids, whether the live catalogue
still serves them, and which provider each fallback would use:

```
architect      siliconflow  zai-org/GLM-5.2, zai-org/GLM-5.3 (alias: glm-5.2) -> ok
               fallback openrouter: z-ai/glm-5.2
```

`kvorum list-seats` prints the resolved panel (ids, roles, providers, key sources).

## Provider Flexibility

`kvorum` is **not tied to a single provider**. Every seat declares a `provider`
(primary) and an optional `fallback`; the order is just data in `panel.json`, so
you can flip OpenRouter ⇄ SiliconFlow ⇄ Ollama/vLLM by editing the file or
picking a preset — no code change.

Two ready-made presets ship in `examples/` with the identical 6-model composition:

| Preset | Primary | Fallback |
|---|---|---|
| `examples/panel.json` | SiliconFlow (+ DeepSeek native) | OpenRouter |
| `examples/panel.openrouter.json` | OpenRouter (`OPENROUTER_API_KEY`) | SiliconFlow |

> ⚠️ **Caution — always run a dual-provider setup (primary + fallback).**
> Review prompts are heavy (a 6-seat panel over a large artifact can reach ~1M
> tokens of context) and providers throttle hard: SiliconFlow answers with
> **HTTP 429** ("Request was rejected due to rate limiting — System is too busy
> now"), OpenRouter runs into **RPM/TPM limits**, and slow reasoning models can
> exceed the read timeout. With a fallback configured the seat automatically
> moves to the secondary provider and the run survives — exactly what saved
> GLM-5.2 in the benchmark below.
>
> Provider **model ids also drift**: the same model is `zai-org/GLM-5.2` on
> SiliconFlow and `z-ai/glm-5.2` on OpenRouter, and either side can retire a name.
> Give every fallback its own `model` and keep spare ids in `model_aliases` (see
> *Per-provider model mapping & aliases*) — a provider-side rename then costs one
> extra request instead of a lost seat. Longer chains work too:
> SiliconFlow → OpenRouter → Native API.
>
> **Disabling the fallback (`--no-fallback`) or leaving the second API key unset
> means one provider hiccup costs you the whole seat and can drop the run below
> quorum.**

Switch provider on any command with `--panel`:

```bash
kvorum run --panel examples/panel.openrouter.json   # OpenRouter primary
kvorum run --panel examples/panel.json              # SiliconFlow primary
kvorum list-seats --panel examples/panel.openrouter.json
```

The same seat model ids keep working across providers because each seat stores
both the OpenRouter slug (`z-ai/glm-5.2`) and the provider-native id
(`zai-org/GLM-5.2`) as primary/fallback pairs.

## Custom Endpoints & Local Proxies

Every provider is just an OpenAI-compatible `{base_url}/chat/completions`, so a
seat can point at any endpoint — a self-hosted vLLM/Ollama/LiteLLM gateway, a
proxy, or a mirror — without touching the provider registry.

The endpoint is resolved with this precedence (highest first):

1. an explicit `base_url` on the seat or its fallback object;
2. a `<NAME>_BASE_URL` environment variable (`SILICONFLOW_BASE_URL`,
   `OPENROUTER_BASE_URL`, `OPENAI_BASE_URL`, `OLLAMA_BASE_URL`, ...);
3. the provider's default public URL from the `providers` block.

Per-seat override:

```jsonc
{
  "seats": [
    {
      "seat_id": "architect",
      "seat": "GLM-5.2",
      "role": "Lead Architecture Critic",
      "provider": "siliconflow",
      "model": "glm-5.2",
      "base_url": "http://127.0.0.1:8000/v1",          // this seat hits a local vLLM
      "fallback": [
        { "provider": "openrouter", "model": "z-ai/glm-5.2",
          "base_url": "http://127.0.0.1:8080/v1" }       // fallback hits LiteLLM
      ]
    }
  ]
}
```

Environment-wide override (no config edit):

```bash
export SILICONFLOW_BASE_URL=http://127.0.0.1:8000/v1   # vLLM / LiteLLM gateway
export OPENROUTER_BASE_URL=http://127.0.0.1:8080/v1
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1       # Ollama
```

Local servers usually need no key; set `OLLAMA_API_KEY=` (or the provider's
`api_key_env`) to any value if one is still required.

## Rate-Limit & Concurrency Control

`kvorum` is designed for providers with strict concurrency/RPM limits (OpenRouter
and SiliconFlow free/tiered plans throttle parallel requests — a single 1M-token
packet already saturates a per-key window). To avoid self-inflicted `429`s, the
runner is **strictly serial**: it calls one seat at a time, never in parallel.

* **Serial, one seat at a time** — no concurrent burst against a provider.
* **`--min-delay-s N`** — add a pause between seat calls to stay under RPM limits
  (`kvorum run --min-delay-s 5`); also settable via `KVORUM_MIN_DELAY_S`.
* **`--timeout` / `--fallback-timeout`** — per-call budgets; a slow seat never
  blocks the whole run.
* **`--no-fallback`** — restrict a run to primary providers only.

Combined with the per-seat fallback (a provider outage moves the seat to its
secondary provider) and the fail-closed quorum, a run degrades loudly instead of
silently dropping seats.

### ⏱️ Execution Time & Heavy Models

The shipped seats are heavy 1M-context reasoning models that emit long `<think>`
chains, and they run **sequentially on purpose** (one seat at a time — that is the
whole point of the rate-limit protection above). Budget for it:

* a **full 6-seat run takes ~10–15 minutes** (~1000–1100 s of model latency, plus
  the `--min-delay-s` pauses);
* a single seat can take **5–6 minutes** (Kimi-K3 answered in 355 s and Qwen in
  324 s in the benchmark below), so keep `--timeout` at **600 s** — a 300 s cap
  silently truncates such seats;
* targeted re-runs (`kvorum run --seats <id>`) cost minutes instead of a full panel.

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

| Command | Purpose |
|---|---|
| `kvorum list-seats` | seat availability + effective panel (no chat calls) |
| `kvorum verify-models` | per-provider resolution vs the live `/models` catalogues (free) |
| `kvorum pack` | build the review packet (globs/manifest, `--max-chars`, `--ast-summary`) |
| `kvorum dry-run` | write prompts to disk; **no API calls** |
| `kvorum run` | real cross-review; archives the previous run; writes `run_meta.json` |
| `kvorum resume` | continue answers truncated by `max_tokens` (missing sections only) |
| `kvorum verdict` | assemble `verdict.md` + machine-readable `verdict.json` |

Flags: `--seats` / `--only-seats`, `--skip-seats`, `--include-excluded`,
`--no-fallback`, `--soft-quorum`, `--no-archive`, `--timeout`,
`--fallback-timeout`, `--min-delay-s`, `--notify`, `--panel`, `--packet`,
`--rules`, `--runs-dir`, `--env-file`.

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
budget; `--ast-summary` embeds symbols, signatures and imports (stdlib `ast`,
Python) so large codebases fit without pasting every body.

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

No telemetry, no uploads beyond your own provider calls. Keys are redacted in
logs and errors. Artifact secret-scanning helpers live in `kvorum.artifacts`;
wiring them into the run path is a v0.2 item (see the Roadmap above). Review
packets are built only from files you select.

## Dogfooding & Real-World Benchmark

kvorum's first real review targeted **its own source**
(`kvorum pack --include 'src/**/*.py' --ast-summary` → a 70 KB packet), run as a
full 6-seat quorum:

```bash
kvorum pack --include 'src/**/*.py' --ast-summary
kvorum run  --min-delay-s 10 --timeout 600 --include-excluded
kvorum verdict
```

| Model | Provider | In Tokens | Out Tokens | Time (s) | Cost ($) |
|---|---|---|---|---|---|
| DeepSeek-V4-Pro | deepseek (native) | 19 429 | 16 253 | 167 | 0.0762 \* |
| GLM-5.2 | openrouter (fallback) | 17 867 | 4 441 | 38 | 0.0446 † |
| Kimi-K3 | siliconflow | 17 915 | 12 912 | 355 | 0.2474 \* |
| Qwen3.8-Flash | siliconflow | 19 305 | 11 436 | 324 | 0.0083 \* |
| LongCat-2.0 | siliconflow | 18 142 | 10 596 | 139 | 0.0000 \* |
| MiniMax-M3 | siliconflow | 18 330 | 11 456 | 72 | 0.0192 \* |
| **Total** | | **110 988** | **67 094** | **1 095** | **0.3957** |

**Total: 178 082 tokens, ≈ $0.40 for the full 6-seat quorum.**

\* estimated from the panel price registry (SiliconFlow/DeepSeek return tokens but
not a cost field). † actual cost reported by the provider (OpenRouter).

What the tuning bought — **6/6 seats answered** (vs 3/5 without the flags),
quorum **MET**, verdict **`CHANGES REQUESTED`** with 23 critical findings:

* **`--timeout 600` recovered Kimi-K3** — it answered in 355 s, past the previous
  300 s cap that had cut it off mid-generation.
* **`--min-delay-s 10` + the OpenRouter fallback recovered GLM-5.2** — SiliconFlow
  throttled the primary call with HTTP 429, and the fallback answered in 38 s, so
  the run stayed lossless instead of dropping the seat.
* MiniMax-M3 returned a full answer but wrapped it in a `<think>` block, so its
  verdict heading did not parse (`n/a`) — see v0.2 item 7.

## Roadmap: v0.2 Backlog

Issues surfaced by the full 6-seat self-review above (23 critical findings).
Items 1 and 3 were closed while adding customizable seats and targeted re-runs;
the rest are intentionally left for v0.2:

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

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest          # network-free (httpx.MockTransport)
```

## License

MIT. Not affiliated with any model provider; you pay for your own API usage.
