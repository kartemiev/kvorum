# Providers, fallbacks & custom endpoints

kvorum talks only to OpenAI-compatible `{base_url}/chat/completions` endpoints,
so cloud hosts and local servers are configured identically.

## Presets

Two ready-made presets ship in `examples/` with the identical 6-seat composition:

| Preset | Primary | Fallback |
|---|---|---|
| `examples/panel.json` | SiliconFlow (+ DeepSeek native) | OpenRouter |
| `examples/panel.openrouter.json` | OpenRouter | SiliconFlow |

```bash
kvorum run --panel examples/panel.openrouter.json   # OpenRouter primary
kvorum run --panel examples/panel.json              # SiliconFlow primary
```

## Dual-provider caution (primary + fallback)

Review prompts are heavy — a 6-seat panel over a large artifact can reach ~1M
tokens of context — and providers throttle hard:

* SiliconFlow answers with **HTTP 429** ("Request was rejected due to rate limiting…");
* OpenRouter enforces **RPM/TPM limits**;
* slow reasoning models can exceed the read timeout.

With a fallback configured, the seat automatically moves to the secondary
provider and the run survives. Disabling the fallback (`--no-fallback`) or
leaving the second key unset means one provider hiccup can cost the whole seat
and drop the run below quorum.

Model ids also drift: the same model is `zai-org/GLM-5.2` on SiliconFlow and
`z-ai/glm-5.2` on OpenRouter, and either side can retire a name. Give every
fallback its own `model` and keep spare ids in `model_aliases` (see
[docs/CONFIGURATION.md](CONFIGURATION.md)) so a rename costs one extra request,
not a lost seat. Longer chains work too: SiliconFlow → OpenRouter → Native API.

## Custom endpoints & local proxies

Endpoint resolution (highest first):

1. an explicit `base_url` on the seat or its fallback object;
2. a `<NAME>_BASE_URL` environment variable (`SILICONFLOW_BASE_URL`,
   `OPENROUTER_BASE_URL`, `DEEPSEEK_BASE_URL`, `OLLAMA_BASE_URL`, `OPENAI_BASE_URL`, …);
3. the provider's default URL from the `providers` block.

Per-seat override:

```jsonc
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
```

Environment-wide override (no config edit):

```bash
export SILICONFLOW_BASE_URL=http://127.0.0.1:8000/v1
export OPENROUTER_BASE_URL=http://127.0.0.1:8080/v1
export OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
```

Local servers usually need no key; set `OLLAMA_API_KEY=` (or the provider's
`api_key_env`) to any value if one is still required.

## Rate-limit & serial execution

kvorum targets providers with strict concurrency/RPM limits, so the runner is
**strictly serial** — one seat at a time, never in parallel.

* **`--min-delay-s N`** — pause between seat calls (`KVORUM_MIN_DELAY_S`).
* **`--timeout` / `--fallback-timeout`** — per-call budgets.
* **`--no-fallback`** — primary providers only.

Combined with per-seat fallbacks and the fail-closed quorum, a run degrades
loudly instead of silently dropping seats.

## Execution time & heavy models

The shipped seats are 1M-context reasoning models that emit long `<think>` chains
and run sequentially on purpose. Budget:

* a full 6-seat run takes ~10–15 minutes (~1000–1100 s of model latency plus
  `--min-delay-s` pauses);
* a single seat can take 5–6 minutes (Kimi-K3 answered in 355 s, Qwen in 324 s in
  the shipped benchmark), so keep `--timeout` at **600 s** — a 300 s cap silently
  truncates such seats;
* targeted re-runs (`kvorum run --seats <id>`) cost minutes instead of a full panel.
