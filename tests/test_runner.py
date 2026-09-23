"""Run orchestration tests: run_meta.json formation + fail-closed quorum."""

from __future__ import annotations

import json

import httpx

from kvorum.config import Settings
from kvorum.runner import run_panel

from conftest import answer_text, make_panel, ok_transport


def _settings(tmp_path):
    packet = tmp_path / "packet.md"
    packet.write_text("# artifact under review\n\nsome code", encoding="utf-8")
    return Settings(
        packet_path=packet,
        rules_path=tmp_path / "rules.md",  # may not exist
        runs_dir=tmp_path / "runs",
        verdict_path=tmp_path / "verdict.md",
        verdict_json_path=tmp_path / "verdict.json",
    ), packet


def test_run_meta_formation(tmp_path):
    panel = make_panel(n_seats=3, quorum=3)
    settings, packet = _settings(tmp_path)
    file_env = {"TEST_KEY": "sk-test-12345678"}

    rc = run_panel(panel, settings, file_env, transport=ok_transport(),
                   archive=False)

    assert rc == 0
    meta = json.loads((settings.runs_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["answered"] == 3
    assert meta["quorum_required"] == 3
    assert meta["fail_closed"] is True
    assert meta["run_panel"] == ["s0", "s1", "s2"]
    assert len(meta["seats"]) == 3
    assert all(s["answered"] is True for s in meta["seats"])
    assert meta["packet"] == str(packet)
    # per-seat artifacts + log exist
    for i in range(3):
        assert (settings.runs_dir / f"s{i}.json").exists()
    assert (settings.runs_dir / "run.log").exists()


def test_fail_closed_returns_nonzero(tmp_path):
    panel = make_panel(n_seats=3, quorum=3)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "model-2":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    settings, _packet = _settings(tmp_path)
    rc = run_panel(panel, settings, {"TEST_KEY": "sk-test-12345678"},
                   transport=httpx.MockTransport(handler), archive=False)
    assert rc == 1
    meta = json.loads((settings.runs_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["answered"] == 2


def test_soft_quorum_returns_zero(tmp_path):
    panel = make_panel(n_seats=3, quorum=3)
    settings, _packet = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "model-0":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    rc = run_panel(panel, settings, {"TEST_KEY": "sk-test-12345678"},
                   transport=httpx.MockTransport(handler), archive=False,
                   soft_quorum=True)
    assert rc == 0


def test_targeted_rerun_merges_meta_without_erasing(tmp_path):
    """A partial run updates only its seats and keeps the rest of the record."""
    panel = make_panel(n_seats=3, quorum=3)
    settings, _packet = _settings(tmp_path)
    env = {"TEST_KEY": "sk-test-12345678"}

    # full run: all three answer
    assert run_panel(panel, settings, env, transport=ok_transport(),
                     archive=False) == 0
    meta = json.loads((settings.runs_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["answered"] == 3 and meta["scope"] == "full"

    # targeted re-run of one seat only: the other two must survive
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "model-1":
            return httpx.Response(500, text="boom")  # this re-run fails
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    rc = run_panel(panel, settings, env, only=["s1"],
                   transport=httpx.MockTransport(handler), archive=False,
                   soft_quorum=True)
    assert rc == 0
    meta = json.loads((settings.runs_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["scope"] == "targeted"
    assert meta["requested_seats"] == ["s1"]
    assert meta["run_panel"] == ["s1"]
    assert {s["slug"]: s["answered"] for s in meta["seats"]} == {
        "s0": True, "s1": False, "s2": True}
    assert meta["answered"] == 2  # previous successes were not erased


def test_skip_seats_excludes_from_run(tmp_path):
    panel = make_panel(n_seats=3, quorum=1)
    settings, _packet = _settings(tmp_path)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    rc = run_panel(panel, settings, {"TEST_KEY": "sk-test-12345678"},
                   skip_seats=["s1"], transport=httpx.MockTransport(handler),
                   archive=False, soft_quorum=True)
    assert rc == 0
    assert seen == ["model-0", "model-2"]
    meta = json.loads((settings.runs_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["skipped_seats"] == ["s1"]


def test_unknown_seat_reference_fails_fast(tmp_path):
    panel = make_panel(n_seats=3, quorum=1)
    settings, _packet = _settings(tmp_path)
    rc = run_panel(panel, settings, {"TEST_KEY": "sk-test-12345678"},
                   only=["does-not-exist"], transport=ok_transport(), archive=False)
    assert rc == 2  # a typo must never silently run (and pay for) the whole panel


def test_partial_run_does_not_archive(tmp_path):
    panel = make_panel(n_seats=3, quorum=1)
    settings, _packet = _settings(tmp_path)
    env = {"TEST_KEY": "sk-test-12345678"}
    assert run_panel(panel, settings, env, transport=ok_transport(), archive=False) == 0
    assert run_panel(panel, settings, env, only=["s0"], transport=ok_transport()) == 0
    # a targeted run keeps runs/ in place (no runs.<stamp>/ sibling), full runs archive
    assert not list(settings.runs_dir.parent.glob("runs.*"))
    assert (settings.runs_dir / "run_meta.json").exists()
