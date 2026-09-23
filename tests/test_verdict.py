"""Verdict tests: usage summing, resume merge, consolidation."""

from __future__ import annotations

import json

from kvorum.panel import Seat
from kvorum.verdict import (_combine_usage, consolidate, extract_verdict,
                            merge_resume)


def test_combine_usage_sums_numeric():
    out = _combine_usage(
        {"prompt_tokens": 10, "completion_tokens": 4, "label": "x"},
        {"prompt_tokens": 5, "completion_tokens": 2, "label": "y"},
    )
    assert out["prompt_tokens"] == 15
    assert out["completion_tokens"] == 6
    assert out["label"] == "x"  # non-numeric: first wins


def test_extract_verdict_normalizes():
    assert extract_verdict("## Verdict\nCHANGES REQUESTED\n") == "CHANGES REQUESTED"
    assert extract_verdict("## Verdict\n`APPROVE WITH COMMENTS`") == "APPROVE WITH COMMENTS"
    assert extract_verdict("## Verdict\nAPPROVE") == "APPROVE"
    assert extract_verdict("no verdict here") == "n/a"


def test_merge_resume(tmp_path):
    seat = Seat("s1", "Seat 1", "role", "p", "m")
    first = {"slug": "s1", "seat": "Seat 1", "text": "## Verdict\nAPPROVE\n\npart one",
             "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    (tmp_path / "s1.json").write_text(json.dumps(first), encoding="utf-8")
    continuation = {"text": "## Blind spots and action plan\n\npart two",
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                    "provider": "p", "model": "m", "http": 200, "ms": 5,
                    "finished_at": "2026-09-23T00:00:00+00:00", "attempts": []}

    merged = merge_resume(seat, continuation, tmp_path)

    assert "part one" in merged["text"] and "part two" in merged["text"]
    assert merged["usage"]["prompt_tokens"] == 12
    assert merged["resume"]["continuation_file"] == "s1.continuation.json"
    assert merged["resume"]["previous_sha256"]
    assert (tmp_path / "s1.continuation.json").exists()


def test_merge_resume_refuses_duplicate(tmp_path):
    seat = Seat("s1", "Seat 1", "role", "p", "m")
    first = {"slug": "s1", "seat": "Seat 1", "text": "## Verdict\nAPPROVE"}
    (tmp_path / "s1.json").write_text(json.dumps(first), encoding="utf-8")
    continuation = {"text": "## Verdict\nAPPROVE"}  # identical text

    try:
        merge_resume(seat, continuation, tmp_path)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_consolidate_blocks_on_changes_requested():
    results = [
        {"seat": "A", "text": "## Verdict\nCHANGES REQUESTED\n\n## Critical findings\n\n"},
        {"seat": "B", "text": "## Verdict\nAPPROVE WITH COMMENTS\n\n## Critical findings\n\n"},
    ]
    info = consolidate(results)
    assert info["verdict"] == "CHANGES REQUESTED"


def test_consolidate_blocks_on_critical_finding():
    results = [
        {"seat": "A", "text": "## Verdict\nAPPROVE WITH COMMENTS\n\n"
                              "## Critical findings\n- file.py:1 broken"},
    ]
    info = consolidate(results)
    assert info["verdict"] == "CHANGES REQUESTED"
    assert info["critical"][0]["finding"] == "file.py:1 broken"


def test_consolidate_approve_when_all_approve():
    results = [
        {"seat": "A", "text": "## Verdict\nAPPROVE\n\n## Critical findings\n\n"},
        {"seat": "B", "text": "## Verdict\nAPPROVE\n\n## Critical findings\n\n"},
    ]
    assert consolidate(results)["verdict"] == "APPROVE"
