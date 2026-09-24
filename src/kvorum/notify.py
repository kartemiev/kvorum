"""Lightweight completion notifications (ntfy.sh / generic webhook), fail-safe.

Sends a short summary when a quorum run finishes. Two zero-backend channels:

* ``KVORUM_NTFY_TOPIC`` → POST the summary text to ``https://ntfy.sh/<topic>``
  with a ``Title`` header (an ntfy.sh topic is a free, subscription-less channel).
* ``KVORUM_NOTIFY_WEBHOOK`` → POST a JSON summary (verdict, votes, quorum, tokens,
  elapsed seconds, cost) to an arbitrary HTTPS endpoint (a CI hook, a Slack /
  Telegram relay, ...).

Every send is best-effort: a 2 s timeout and swallowed exceptions mean a broken
notification can never fail a run.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .panel import Panel
from .verdict import build_verdict_json

TIMEOUT_S = 2.0
NTFY_TITLE = "kvorum Audit Finished"


def topic_from_env() -> str:
    return os.environ.get("KVORUM_NTFY_TOPIC", "").strip()


def webhook_from_env() -> str:
    return os.environ.get("KVORUM_NOTIFY_WEBHOOK", "").strip()


def notify_enabled(explicit: bool = False) -> bool:
    """True when a notification should be sent (``--notify`` or an env channel)."""
    return bool(explicit or topic_from_env() or webhook_from_env())


def _tokens(results: list[dict]) -> dict:
    prompt = completion = 0
    total_cost = 0.0
    has_cost = False
    for result in results:
        usage = result.get("usage") or {}
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        if cost is None:
            cost = result.get("cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)
            has_cost = True
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": round(total_cost, 6) if has_cost else None,
    }


def _summary_text(info: dict, tokens: dict, elapsed_s: float) -> str:
    quorum = info["quorum"]
    state = "MET" if quorum["met"] else "NOT MET"
    return (
        f"kvorum audit finished — verdict {info['verdict']} "
        f"(quorum {state} {quorum['answered']}/{quorum['total']}, "
        f"{tokens['total_tokens']} tokens, {elapsed_s:.1f}s)"
    )


def build_summary(panel: Panel, results: list[dict], answered: int,
                  elapsed_s: float, packet: Path) -> dict:
    """Assemble the notification summary (verdict, votes, quorum, tokens, cost)."""
    info = build_verdict_json(panel, packet, results, answered)
    tokens = _tokens(results)
    return {
        "event": "kvorum_audit_finished",
        "verdict": info["verdict"],
        "votes": info["votes"],
        "quorum": info["quorum"],
        "tokens": tokens,
        "elapsed_s": round(float(elapsed_s), 1),
        "cost_usd": tokens["cost_usd"],
        "packet": str(packet),
        "seats": info["seats"],
        "text": _summary_text(info, tokens, elapsed_s),
    }


def _post(url: str, *, data: str | None = None, json_body: dict | None = None,
          headers: dict | None = None,
          transport: httpx.BaseTransport | None = None) -> bool:
    try:
        with httpx.Client(timeout=TIMEOUT_S, transport=transport) as client:
            client.post(url, content=data, json=json_body, headers=headers)
    except Exception:  # noqa: BLE001
        return False
    return True


def send_ntfy(topic: str, text: str, *,
              transport: httpx.BaseTransport | None = None) -> bool:
    if not topic:
        return False
    return _post(
        f"https://ntfy.sh/{topic}",
        data=text,
        headers={"Title": NTFY_TITLE, "Content-Type": "text/plain"},
        transport=transport,
    )


def send_webhook(url: str, summary: dict, *,
                 transport: httpx.BaseTransport | None = None) -> bool:
    if not url:
        return False
    return _post(
        url,
        json_body=summary,
        headers={"Content-Type": "application/json"},
        transport=transport,
    )


def notify_completion(summary: dict, *, topic: str | None = None,
                      webhook: str | None = None,
                      transport: httpx.BaseTransport | None = None) -> dict:
    """Send the summary to every configured channel; never raises."""
    topic = topic if topic is not None else topic_from_env()
    webhook = webhook if webhook is not None else webhook_from_env()
    return {
        "ntfy": send_ntfy(topic, summary.get("text", ""),
                          transport=transport) if topic else False,
        "webhook": send_webhook(webhook, summary, transport=transport) if webhook else False,
    }
