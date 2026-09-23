"""Verdict extraction, resume merging, and local (deterministic) consolidation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .panel import Panel, Seat, seat_file_name

VERDICT_RE = re.compile(r"##\s*Verdict\s*\n+\s*`?([A-Za-z ]+)`?")

APPROVE = "APPROVE"
APPROVE_WITH_COMMENTS = "APPROVE WITH COMMENTS"
CHANGES_REQUESTED = "CHANGES REQUESTED"


def extract_verdict(text: str) -> str:
    match = VERDICT_RE.search(text or "")
    if not match:
        return "n/a"
    verdict = match.group(1).strip().upper()
    if "CHANGES" in verdict:
        return CHANGES_REQUESTED
    if "COMMENTS" in verdict:
        return APPROVE_WITH_COMMENTS
    if "APPROVE" in verdict:
        return APPROVE
    return verdict


def _combine_usage(first: dict | None, second: dict | None) -> dict:
    """Sum the numeric usage fields of a two-part answer (tokens are additive)."""
    out: dict = {}
    for source in (first or {}, second or {}):
        for key, value in source.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = out.get(key, 0) + value
            elif key not in out:
                out[key] = value
    return out


def merge_resume(seat: Seat, continuation: dict, runs_dir: Path) -> dict:
    """Merge a continuation answer into the seat artifact (the verdict source).

    ``<slug>.json`` becomes the complete answer (previous part + continuation),
    the raw continuation is preserved as ``<slug>.continuation.json``, and the
    record carries a ``resume`` block (chars/hashes/usage) so the merge is
    auditable.
    """
    path = runs_dir / seat_file_name(seat)
    previous_record = json.loads(path.read_text(encoding="utf-8"))
    previous_text = previous_record.get("text") or ""
    cont_text = (continuation.get("text") or "").strip()
    if cont_text and previous_text.strip() == cont_text.strip():
        raise RuntimeError(
            "refusing to merge: the seat artifact already holds the continuation")
    cont_path = path.with_name(path.stem + ".continuation.json")
    cont_path.write_text(json.dumps(continuation, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    merged = dict(previous_record)
    merged["text"] = (previous_text.rstrip() + "\n\n" + cont_text).strip()
    merged["usage"] = _combine_usage(previous_record.get("usage"),
                                     continuation.get("usage"))
    merged["resume"] = {
        "at": continuation.get("finished_at"),
        "previous_chars": len(previous_text),
        "previous_sha256": hashlib.sha256(previous_text.encode()).hexdigest()[:16],
        "continuation_chars": len(cont_text),
        "continuation_file": cont_path.name,
        "continuation_provider": continuation.get("provider"),
        "continuation_model": continuation.get("model"),
        "continuation_http": continuation.get("http"),
        "continuation_ms": continuation.get("ms"),
        "continuation_usage": continuation.get("usage"),
        "continuation_attempts": continuation.get("attempts"),
    }
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    return merged


def split_sections(text: str) -> dict[str, str]:
    """Split a verdict into ``{heading: body}`` on level-2 markdown headings."""
    parts = re.split(r"\n##\s*", "\n" + (text or ""))
    out: dict[str, str] = {}
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        out[head.strip()] = body.strip()
    return out


def critical_findings(text: str) -> list[str]:
    """Non-empty bullet lines from the ``Critical findings`` section."""
    sections = split_sections(text)
    for head, body in sections.items():
        if "critical" in head.lower():
            return [line.strip().lstrip("-* ").strip()
                    for line in body.splitlines()
                    if line.strip().lstrip("-* ").strip()]
    return []


def consolidate(results: list[dict]) -> dict:
    """Deterministic local consolidation of the panel's verdicts.

    Rule: any seat answering ``CHANGES REQUESTED`` or reporting a non-empty
    critical-findings section blocks the review (mirrors the reference "any
    CRITICAL/HIGH -> block" rule).
    """
    answered = [r for r in results if r.get("text")]
    verdicts = [extract_verdict(r["text"]) for r in answered]
    criticals: list[dict] = []
    for r in answered:
        for finding in critical_findings(r["text"]):
            criticals.append({"seat": r.get("seat"), "finding": finding})
    if any(v == CHANGES_REQUESTED for v in verdicts) or criticals:
        verdict = CHANGES_REQUESTED
    elif verdicts and all(v == APPROVE for v in verdicts):
        verdict = APPROVE
    elif any(v == APPROVE_WITH_COMMENTS for v in verdicts):
        verdict = APPROVE_WITH_COMMENTS
    else:
        verdict = APPROVE if verdicts else CHANGES_REQUESTED
    return {
        "verdict": verdict,
        "votes": {v: verdicts.count(v) for v in sorted(set(verdicts))},
        "critical": criticals,
        "per_seat": [
            {"seat": r.get("seat"), "slug": r.get("slug"),
             "verdict": extract_verdict(r.get("text") or ""),
             "provider": r.get("provider"), "model": r.get("model"),
             "http": r.get("http"), "ms": r.get("ms"),
             "answered": bool(r.get("text"))}
            for r in results
        ],
    }


def build_verdict_json(panel: Panel, packet_path: Path, results: list[dict],
                       answered: int) -> dict:
    """Machine-readable verdict (what CI / a PR comment consumes)."""
    consensus = consolidate(results)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "packet": str(packet_path),
        "quorum": {
            "required": panel.quorum_required,
            "answered": answered,
            "total": len(panel.seats),
            "met": answered >= panel.quorum_required,
            "fail_closed": panel.fail_closed,
        },
        "verdict": consensus["verdict"],
        "votes": consensus["votes"],
        "critical": consensus["critical"],
        "seats": consensus["per_seat"],
    }


def build_verdict_markdown(panel: Panel, packet_path: Path, results: list[dict],
                           answered: int, packet: str) -> str:
    """Human-readable verdict.md with a quorum table and consolidated verdict."""
    info = build_verdict_json(panel, packet_path, results, answered)
    rows = []
    for r in results:
        usage = r.get("usage") or {}
        verdict = extract_verdict(r.get("text") or "") if r.get("text") else "NOT ANSWERED"
        fb = " (fallback)" if r.get("fallback_used") else ""
        rows.append(
            f"| {r.get('seat')} | `{r.get('slug')}` | {r.get('role')} | "
            f"`{r.get('provider')} / {r.get('model')}`{fb} | {r.get('http')} | "
            f"{r.get('ms')} ms | {usage.get('prompt_tokens', '—')} / "
            f"{usage.get('completion_tokens', '—')} | {verdict} |")
    quorum_state = "MET" if info["quorum"]["met"] else "NOT MET"
    criticals = "\n".join(
        f"- [{c['seat']}] {c['finding']}" for c in info["critical"]) or "- none"
    sections = "\n\n".join(
        f"### {r.get('seat')} — {r.get('role')}\n\n"
        f"`{r.get('provider')} / {r.get('model')}` · http={r.get('http')} · "
        f"{r.get('ms')} ms\n\n{r.get('text') or ('_(no answer)_ ' + (r.get('error') or ''))}"
        for r in results)
    return (
        "# Panel verdict\n\n"
        f"> Panel: {len(panel.seats)} seats — "
        f"{' · '.join(s.seat for s in panel.seats)}.\n"
        f"> Subject: `{packet_path}` ({len(packet)} chars).\n"
        f"> Quorum: >= {panel.quorum_required} seats, fail-closed={panel.fail_closed}.\n\n"
        "## 1. Quorum and seat status\n\n"
        "| Seat | Slug | Role | Provider / model | HTTP | Latency | Tokens (in/out) | Verdict |\n"
        "|---|---|---|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n\n"
        f"**Quorum:** {quorum_state} (answered {answered}/{len(panel.seats)}).\n\n"
        "## 2. Consolidated verdict\n\n"
        f"**`{info['verdict']}`** — votes: {info['votes']}.\n\n"
        f"Critical findings:\n\n{criticals}\n\n"
        "## 3. Raw seat answers\n\n"
        + sections + "\n"
    )

