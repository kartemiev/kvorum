"""Provider layer: OpenAI-compatible chat-completions + per-seat orchestration.

The only place that makes network calls. ``transport`` is an optional ``httpx``
transport injected by tests (``httpx.MockTransport``) so the pipeline runs with
no real network access. Seat semantics: primary first; fallback only when the
primary produced no visible text and its key is provisioned; an over-large
``max_tokens`` is retried once with a conservative budget on HTTP 400/413.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx

from .config import clean, resolve_key
from .panel import Panel, ProviderConfig, Seat

TEMPERATURE = 0.2


def post_chat(provider: ProviderConfig, model: str, key: str, payload: dict,
              timeout_s: float, *, transport: httpx.BaseTransport | None = None
              ) -> tuple[int | None, str, dict | None, str | None]:
    """One chat-completion call → ``(http, text, usage, error)``."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(provider.extra_headers)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30.0),
                          transport=transport) as client:
            response = client.post(provider.chat_url(), headers=headers, json=payload)
    except Exception as exc:  # noqa: BLE001
        return None, "", None, f"{type(exc).__name__}: {clean(exc)}"

    text, usage = "", None
    if response.status_code == 200:
        try:
            parsed = response.json()
            choices = parsed.get("choices") or []
            if choices:
                text = ((choices[0].get("message") or {}).get("content") or "").strip()
            usage = parsed.get("usage")
        except json.JSONDecodeError:
            text = response.text[:2000]
    error = None if text else clean(response.text)
    return response.status_code, text, usage, error


def call_seat(seat: Seat, panel: Panel, prompt: str, file_env: dict[str, str], *,
              timeout_s: float | None = None,
              fallback_timeout_s: float | None = None,
              use_fallback: bool = True,
              max_tokens_retry: int | None = None,
              transport: httpx.BaseTransport | None = None) -> dict:
    """Call one seat: primary provider, then fallback; record every attempt."""
    primary_timeout = timeout_s if timeout_s is not None else panel.timeout_s
    fb_timeout = fallback_timeout_s if fallback_timeout_s is not None else panel.fallback_timeout_s
    budget = max_tokens_retry if max_tokens_retry is not None else panel.max_tokens_retry

    result: dict = {
        "slug": seat.slug, "seat": seat.seat, "role": seat.role,
        "tier": seat.tier, "provider": seat.provider, "model": seat.model,
        "prompt_chars": len(prompt),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    attempts: list[dict] = []
    candidates: list[tuple[str, str, float, bool]] = [
        (seat.provider, seat.model, primary_timeout, False)]
    if use_fallback:
        candidates.extend(
            (fb.provider, fb.model, fb_timeout, True) for fb in seat.fallback_chain)

    for provider_name, model, tmo, is_fallback in candidates:
        provider = panel.provider(provider_name)
        key, source = resolve_key(provider.api_key_env, file_env)
        if not key:
            attempts.append({"provider": provider_name, "model": model,
                             "key_source": "MISSING", "http": None, "ms": 0,
                             "error": f"no credential for {provider_name}"})
            continue

        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": TEMPERATURE,
            "max_tokens": seat.max_tokens,
        }
        payload.update(seat.extra_body or {})

        started = time.perf_counter()
        http, text, usage, error = post_chat(provider, model, key, payload, tmo,
                                             transport=transport)
        retried_budget = False
        if not text and http in (400, 413) and seat.max_tokens > budget:
            payload["max_tokens"] = budget
            http, text, usage, error = post_chat(provider, model, key, payload, tmo,
                                                 transport=transport)
            retried_budget = True
        ms = int((time.perf_counter() - started) * 1000)
        attempts.append({
            "provider": provider_name, "model": model, "key_source": source,
            "http": http, "ms": ms, "max_tokens": payload["max_tokens"],
            "budget_retry": retried_budget, "fallback": is_fallback,
            "error": error,
        })
        if text:
            result.update({
                "provider": provider_name, "model": model, "key_source": source,
                "http": http, "ms": ms, "usage": usage, "text": text,
                "error": None, "fallback_used": is_fallback, "attempts": attempts,
            })
            if is_fallback:
                result["primary_failure"] = (
                    attempts[0].get("error") or f"http={attempts[0].get('http')}")
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
            return result

    last = attempts[-1] if attempts else {"http": None, "ms": 0, "error": "no candidate provider"}
    result.update({
        "http": last.get("http"), "ms": last.get("ms") or 0, "usage": None,
        "text": "", "error": last.get("error"),
        "fallback_used": bool(attempts and attempts[-1].get("fallback")),
        "attempts": attempts,
    })
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def verify_models(panel: Panel, file_env: dict[str, str], *,
                  transport: httpx.BaseTransport | None = None) -> int:
    """Check every seat's model id against the provider ``/models`` catalogue.

    Returns the number of missing primary models (0 = all present). Never makes
    a chat call.
    """
    names = {seat.provider for seat in panel.seats}
    names |= {seat.fallback.provider for seat in panel.seats if seat.fallback}
    catalogues: dict[str, list[str] | None] = {}
    for name in sorted(names):
        provider = panel.provider(name)
        key, _source = resolve_key(provider.api_key_env, file_env)
        if not key:
            catalogues[name] = None
            continue
        try:
            with httpx.Client(timeout=30.0, transport=transport) as client:
                response = client.get(provider.models_url(),
                                      headers={"Authorization": f"Bearer {key}"})
            ids = ([item.get("id") for item in (response.json().get("data") or [])]
                   if response.status_code == 200 else [])
            catalogues[name] = ids
        except Exception:  # noqa: BLE001
            catalogues[name] = None

    problems = 0
    for seat in panel.seats:
        ids = catalogues.get(seat.provider)
        if ids is None:
            continue  # unknown catalogue — not a proven miss
        if seat.model not in ids:
            problems += 1
    return problems
