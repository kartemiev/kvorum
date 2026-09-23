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

#: Substrings that mark "this provider no longer serves that model id".
_MODEL_MISSING_HINTS = (
    "model not found", "model_not_found", "no such model", "does not exist",
    "unknown model", "invalid model", "unsupported model", "not a valid model",
    "model is not available", "model unavailable", "unavailable model",
)


def model_missing(http: int | None, error: str | None) -> bool:
    """True when a provider rejected the request *because of the model id*.

    Lets the runner walk the alias alternatives for a provider that retired or
    renamed a model instead of losing the seat to the fallback provider.
    """
    if http == 404:
        return True
    if http in (400, 422) and error:
        low = error.lower()
        return any(hint in low for hint in _MODEL_MISSING_HINTS)
    return False


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
    # A model alias drives per-provider ids: the seat's own id is used only when
    # it is a literal (no alias table), so fallbacks can pick their own name.
    primary_declared = "" if seat.model_alias else seat.model
    candidates: list[tuple[str, str, float, bool]] = [
        (seat.provider, primary_declared, primary_timeout, False)]
    if use_fallback:
        candidates.extend(
            (fb.provider, fb.model, fb_timeout, True) for fb in seat.fallback_chain)

    for provider_name, declared, tmo, is_fallback in candidates:
        provider = panel.provider(provider_name)
        key, source = resolve_key(provider.api_key_env, file_env)
        model_ids = seat.model_ids_for(provider_name, declared)
        if not key:
            attempts.append({"provider": provider_name, "model": declared or "?",
                             "key_source": "MISSING", "http": None, "ms": 0,
                             "error": f"no credential for {provider_name}"})
            continue
        if not model_ids:
            attempts.append({"provider": provider_name, "model": "",
                             "key_source": source, "http": None, "ms": 0,
                             "error": f"no model id for provider {provider_name}: "
                                      "set `model` on the seat/fallback or add an "
                                      "alias entry"})
            continue

        # Walk the provider's ids in order: the next one is tried when the provider
        # reports that this exact model is gone (retired / renamed).
        for model_index, model in enumerate(model_ids):
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
            if (not text and http in (400, 413) and seat.max_tokens > budget
                    and not model_missing(http, error)):
                payload["max_tokens"] = budget
                http, text, usage, error = post_chat(provider, model, key, payload, tmo,
                                                     transport=transport)
                retried_budget = True
            ms = int((time.perf_counter() - started) * 1000)
            attempts.append({
                "provider": provider_name, "model": model, "key_source": source,
                "http": http, "ms": ms, "max_tokens": payload["max_tokens"],
                "budget_retry": retried_budget, "fallback": is_fallback,
                "model_index": model_index, "error": error,
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
            if not (model_missing(http, error) and model_index + 1 < len(model_ids)):
                break  # this provider is done (or there is no alternative id)

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
                  transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Resolve every seat's model per provider against the live ``/models`` catalogues.

    Returns one entry per seat: which ids are configured for the primary provider,
    which of them the catalogue actually serves (``present`` / ``first_present``),
    and the per-provider ids each fallback would use. ``present is False`` means the
    primary provider no longer serves *any* configured id — the seat is at risk
    unless a fallback covers it. Never makes a chat call (free).
    """
    names = {seat.provider for seat in panel.seats}
    for seat in panel.seats:
        names |= {fb.provider for fb in seat.fallback_chain}
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

    report: list[dict] = []
    for seat in panel.seats:
        declared = "" if seat.model_alias else seat.model
        primary_ids = seat.model_ids_for(seat.provider, declared)
        catalogue = catalogues.get(seat.provider)
        present = (None if catalogue is None
                   else any(model in catalogue for model in primary_ids))
        first_present = next((model for model in primary_ids
                              if catalogue and model in catalogue), None)
        report.append({
            "seat_id": seat.seat_id,
            "seat": seat.seat,
            "role": seat.role,
            "provider": seat.provider,
            "model_alias": seat.model_alias or None,
            "model_ids": list(primary_ids),
            "first_present": first_present,
            "present": present,
            "fallbacks": [
                {"provider": fb.provider,
                 "model_ids": list(seat.model_ids_for(fb.provider, fb.model))}
                for fb in seat.fallback_chain],
        })
    return report
