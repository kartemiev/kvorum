"""Shared test fixtures: src on sys.path + mock helpers (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kvorum.panel import Fallback, Panel, ProviderConfig, Seat  # noqa: E402


def make_panel(n_seats: int = 3, quorum: int = 3, fail_closed: bool = True) -> Panel:
    providers = {"p": ProviderConfig("p", "https://p.example/v1", ("TEST_KEY",))}
    seats = [Seat(f"s{i}", f"Seat {i}", "role", "p", f"model-{i}",
                  max_tokens=1000) for i in range(n_seats)]
    return Panel(providers=providers, seats=seats,
                 quorum_required=quorum, fail_closed=fail_closed)


def answer_text(verdict: str = "APPROVE") -> str:
    return (f"## Verdict\n{verdict}\n\n"
            "## Critical findings\n\n## Major findings\n\n"
            "## Blind spots and action plan\nok")


def ok_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={
        "model": body["model"],
        "choices": [{"message": {"content": answer_text()}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })


def ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(ok_response)


def status_transport(status: int, text: str = "") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=text)
    return httpx.MockTransport(handler)
