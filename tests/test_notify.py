"""Notification tests: summary building + ntfy/webhook sends (mocked, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from kvorum.notify import (build_summary, notify_completion, notify_enabled,
                           send_ntfy, send_webhook)

from conftest import answer_text, make_panel


def _result(seat: str, slug: str, model: str, prompt: int, completion: int,
            verdict: str = "APPROVE", cost: float | None = None) -> dict:
    usage: dict = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cost is not None:
        usage["cost"] = cost
    return {
        "seat": seat, "slug": slug, "provider": "p", "model": model,
        "http": 200, "ms": 100, "usage": usage, "text": answer_text(verdict),
    }


def test_build_summary():
    panel = make_panel(n_seats=2, quorum=2)
    results = [
        _result("Seat 0", "s0", "model-0", 10, 5),
        _result("Seat 1", "s1", "model-1", 20, 7),
    ]
    summary = build_summary(panel, results, 2, 12.5, Path("packet.md"))
    assert summary["verdict"] == "APPROVE"
    assert summary["quorum"]["met"] is True
    assert summary["tokens"]["prompt_tokens"] == 30
    assert summary["tokens"]["completion_tokens"] == 12
    assert summary["tokens"]["total_tokens"] == 42
    assert summary["elapsed_s"] == 12.5
    assert summary["cost_usd"] is None
    assert "APPROVE" in summary["text"]


def test_build_summary_cost_when_provider_reports_it():
    panel = make_panel(n_seats=1, quorum=1)
    results = [_result("Seat 0", "s0", "model-0", 100, 50, "CHANGES REQUESTED",
                       cost=0.0123)]
    summary = build_summary(panel, results, 1, 1.0, Path("packet.md"))
    assert summary["verdict"] == "CHANGES REQUESTED"
    assert summary["cost_usd"] == 0.0123


def test_send_ntfy():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["title"] = request.headers.get("Title")
        captured["body"] = request.content.decode()
        return httpx.Response(200)

    ok = send_ntfy("mytopic", "hello audit", transport=httpx.MockTransport(handler))
    assert ok is True
    assert captured["url"] == "https://ntfy.sh/mytopic"
    assert captured["title"] == "kvorum Audit Finished"
    assert captured["body"] == "hello audit"


def test_send_webhook():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    payload = {"event": "kvorum_audit_finished", "verdict": "APPROVE"}
    ok = send_webhook("https://hooks.example.com/k", payload,
                      transport=httpx.MockTransport(handler))
    assert ok is True
    assert captured["url"] == "https://hooks.example.com/k"
    assert captured["body"] == payload


def test_notify_fail_safe_on_network_error():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    transport = httpx.MockTransport(boom)
    assert send_ntfy("t", "x", transport=transport) is False
    assert send_webhook("https://x", {"a": 1}, transport=transport) is False


def test_notify_enabled(monkeypatch):
    monkeypatch.delenv("KVORUM_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("KVORUM_NOTIFY_WEBHOOK", raising=False)
    assert notify_enabled() is False
    assert notify_enabled(True) is True
    monkeypatch.setenv("KVORUM_NTFY_TOPIC", "mytopic")
    assert notify_enabled() is True
    monkeypatch.delenv("KVORUM_NTFY_TOPIC")
    monkeypatch.setenv("KVORUM_NOTIFY_WEBHOOK", "https://h.example")
    assert notify_enabled() is True


def test_notify_completion_both_channels(monkeypatch):
    monkeypatch.setenv("KVORUM_NTFY_TOPIC", "t")
    monkeypatch.setenv("KVORUM_NOTIFY_WEBHOOK", "https://h.example")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    summary = {"text": "kvorum audit finished — verdict APPROVE"}
    outcome = notify_completion(summary, transport=httpx.MockTransport(handler))
    assert outcome == {"ntfy": True, "webhook": True}
    assert "https://ntfy.sh/t" in seen
    assert "https://h.example" in seen
