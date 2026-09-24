"""Run orchestration: archive, per-seat calls, run.log, run_meta.json, quorum."""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import Settings
from .panel import Panel, Seat, effective_seats, resolve_seat_refs, seat_file_name
from .prompts import build_prompt, build_resume_prompt
from .notify import build_summary, notify_completion
from .providers import call_seat
from .verdict import extract_verdict, merge_resume


def load_inputs(settings: Settings) -> tuple[str, str]:
    """Return ``(packet, rules)`` — the artifact and optional domain rules."""
    packet = settings.packet_path.read_text(encoding="utf-8")
    rules = (settings.rules_path.read_text(encoding="utf-8")
             if settings.rules_path.exists()
             else "(no domain rules provided)")
    return packet, rules


def previous_answer(seat: Seat, runs_dir: Path) -> str:
    path = runs_dir / seat_file_name(seat)
    if not path.exists():
        return ""
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("text") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def archive_previous_run(runs_dir: Path) -> Path | None:
    """Move a non-empty runs dir aside before a new run (verdicts never mix)."""
    if not runs_dir.exists() or not any(runs_dir.glob("*.json")):
        return None
    target = runs_dir.with_name(
        f"{runs_dir.name}.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    shutil.move(str(runs_dir), str(target))
    print(f"archived previous run: {target}")
    return target


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _read_meta(meta_path: Path) -> dict:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_seat_statuses(panel: Panel, previous: list[dict],
                         fresh: list[dict]) -> list[dict]:
    """Union of previous and fresh seat statuses, ordered like the panel.

    A fresh status replaces the previous one for the same seat; seats that did
    not run this time keep their last known result, so a targeted re-run never
    erases an already successful answer.
    """
    by_id = {s.get("slug"): s for s in previous if s.get("slug")}
    for status in fresh:
        if status.get("slug"):
            by_id[status["slug"]] = status
    ids = [s.seat_id for s in panel.seats]
    ordered = [by_id[sid] for sid in ids if sid in by_id]
    ordered += [status for sid, status in by_id.items() if sid not in ids]
    return ordered


def _read_seat_results(panel: Panel, runs_dir: Path) -> list[dict]:
    """Read every seat artifact that holds an answer (for the notification summary)."""
    results: list[dict] = []
    for seat in panel.seats:
        path = runs_dir / seat_file_name(seat)
        if not path.exists():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("text"):
            results.append(record)
    return results


def _send_notification(panel: Panel, settings: Settings, started: float) -> None:
    """Best-effort completion notification (ntfy / webhook); never raises."""
    results = _read_seat_results(panel, settings.runs_dir)
    answered = sum(1 for r in results if r.get("text"))
    summary = build_summary(panel, results, answered,
                            time.perf_counter() - started, settings.packet_path)
    outcome = notify_completion(summary)
    print(f"[notify] ntfy={'sent' if outcome['ntfy'] else 'skipped'} "
          f"webhook={'sent' if outcome['webhook'] else 'skipped'}", flush=True)


def run_panel(panel: Panel, settings: Settings, file_env: dict[str, str], *,
              only: list[str] | None = None,
              skip_seats: list[str] | None = None,
              include_excluded: bool = False,
              resume: bool = False,
              use_fallback: bool = True,
              soft_quorum: bool = False,
              archive: bool = True,
              exclude_env: str | None = None,
              min_delay_s: float = 0.0,
              notify: bool = False,
              transport: httpx.BaseTransport | None = None) -> int:
    """Run the cross-review and return an exit code (0 quorum, 1 fail-closed, 2 error).

    ``only`` selects a subset (targeted re-run / offset) and ``skip_seats``
    subtracts seats from the effective panel. Any partial invocation **merges
    into the existing runs directory**: seats that did not run keep their last
    answer and ``run_meta.json`` keeps their status — only the selected seats are
    re-called and updated. A fresh full run still archives the previous one.
    """
    packet, rules = load_inputs(settings)
    started = time.perf_counter()

    matched_only, unmatched_only = resolve_seat_refs(panel, list(only or []))
    matched_skip, unmatched_skip = resolve_seat_refs(panel, list(skip_seats or []))
    unmatched = unmatched_only + unmatched_skip
    if unmatched:
        known = ", ".join(s.seat_id for s in panel.seats)
        print(f"[!] unknown seat reference(s): {', '.join(unmatched)}\n"
              f"    known seats: {known}", file=sys.stderr)
        return 2
    if only and not matched_only:
        return 2

    partial = bool(only) or bool(skip_seats) or resume
    if archive and not partial:
        archive_previous_run(settings.runs_dir)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)

    # Explicit ``--seats`` beats the default exclusion policy: if you name a
    # seat, you mean it. Without a subset the shipped policy applies.
    seats = (list(matched_only) if only
             else effective_seats(panel, None, include_excluded, exclude_env))
    if matched_skip:
        skip_ids = {s.seat_id for s in matched_skip}
        seats = [s for s in seats if s.seat_id not in skip_ids]
    if not seats:
        print(f"[!] no seats to run (known: {', '.join(s.seat_id for s in panel.seats)})",
              file=sys.stderr)
        return 2

    timeout_s = settings.timeout_s if settings.timeout_s is not None else panel.timeout_s
    fb_timeout = (settings.fallback_timeout_s if settings.fallback_timeout_s is not None
                  else panel.fallback_timeout_s)
    budget = (settings.max_tokens_retry if settings.max_tokens_retry is not None
              else panel.max_tokens_retry)
    quorum = (settings.quorum_required if settings.quorum_required is not None
              else panel.quorum_required)
    fail_closed = (settings.fail_closed if settings.fail_closed is not None
                   else panel.fail_closed)

    log = settings.runs_dir / "run.log"
    successes = 0
    merged_seats = 0
    statuses: list[dict] = []

    print(f"panel: {len(seats)}/{len(panel.seats)} seats — "
          f"{', '.join(s.seat for s in seats)}")
    print(f"timeout: {timeout_s:.0f} s (fallback {fb_timeout:.0f} s, "
          f"{'enabled' if use_fallback else 'disabled'}) | quorum: {quorum} "
          f"{'fail-closed' if fail_closed else 'soft'}")
    if min_delay_s > 0:
        print(f"rate-limit: {min_delay_s:.1f} s delay between seats (serial run)")

    for idx, seat in enumerate(seats):
        if idx and min_delay_s > 0:
            time.sleep(min_delay_s)
        if resume:
            previous = previous_answer(seat, settings.runs_dir)
            if not previous:
                print(f"[!] {seat.seat}: no previous answer to continue — skipped",
                      flush=True)
                continue
            prompt = build_resume_prompt(seat, previous, packet, rules, panel)
            print(f"[{_stamp()}] {seat.seat} -> continuation "
                  f"(replaying {len(previous)} chars)...", flush=True)
        else:
            prompt = build_prompt(packet, rules, seat, panel)
            print(f"[{_stamp()}] {seat.seat} ({seat.slug} -> {seat.model}) "
                  f"-> calling...", flush=True)

        result = call_seat(seat, panel, prompt, file_env,
                           timeout_s=timeout_s, fallback_timeout_s=fb_timeout,
                           use_fallback=use_fallback, max_tokens_retry=budget,
                           transport=transport)

        if resume:
            merged = merge_resume(seat, result, settings.runs_dir)
            merged_seats += 1 if result["text"] else 0
            missing = [s for s in panel.sections.required if s not in merged["text"]]
            print(f"    merged: {len(merged['text'])} chars | missing sections: "
                  f"{missing or 'none'}", flush=True)
        else:
            (settings.runs_dir / seat_file_name(seat)).write_text(
                json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

        statuses.append({
            "seat": seat.seat, "slug": seat.slug, "http": result.get("http"),
            "ms": result.get("ms"), "provider": result.get("provider"),
            "model": result.get("model"), "fallback_used": result.get("fallback_used"),
            "answered": bool(result["text"]), "error": result.get("error"),
        })
        if result["text"]:
            successes += 1
            verdict = extract_verdict(result["text"])
            line = (f"[{_stamp()}] OK   {seat.seat:16} http={result['http']} "
                    f"{result['ms']} ms provider={result['provider']} "
                    f"verdict={verdict} chars={len(result['text'])}"
                    + (" [fallback]" if result.get("fallback_used") else "")
                    + (" [continuation]" if resume else ""))
        else:
            line = (f"[{_stamp()}] FAIL {seat.seat:16} http={result['http']} "
                    f"ms={result.get('ms')} err={result['error']} "
                    f"attempts={len(result.get('attempts') or [])}")
        print(line, flush=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    if resume and not merged_seats:
        print("[!] --resume: no seat had a previous answer to continue", file=sys.stderr)
        return 2

    meta_path = settings.runs_dir / "run_meta.json"
    previous = _read_meta(meta_path)
    merged_statuses = _merge_seat_statuses(panel, previous.get("seats") or [], statuses)
    answered_total = sum(1 for s in merged_statuses if s.get("answered"))

    meta_path.write_text(json.dumps({
        "scope": "targeted" if partial else "full",
        "resume": resume,
        "requested_seats": list(only or []),
        "skipped_seats": list(skip_seats or []),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "packet": str(settings.packet_path),
        "packet_chars": len(packet),
        "panel_size": len(panel.seats),
        "run_panel": [s.seat_id for s in seats],
        "excluded_by_default": panel.excluded_by_default,
        "quorum_required": quorum,
        "fail_closed": fail_closed,
        "timeout_s": timeout_s,
        "fallback_timeout_s": fb_timeout,
        "fallback_enabled": use_fallback,
        "min_delay_s": min_delay_s,
        "seats": merged_statuses,
        "answered": answered_total,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    if partial:
        print(f"targeted re-run merged: {len(merged_statuses)} seat record(s) in "
              f"{meta_path.name}, previous answers kept")

    quorum_met = answered_total >= quorum
    print(f"\nanswered this run: {successes} | panel answered: "
          f"{answered_total}/{len(merged_statuses)} | quorum {quorum} -> "
          f"{'QUORUM MET' if quorum_met else 'QUORUM NOT MET'}")
    if notify:
        _send_notification(panel, settings, started)
    if not quorum_met and fail_closed and not soft_quorum:
        print("[!] fail-closed — verdict is not authoritative; use --soft-quorum "
              "to accept it anyway")
        return 1
    return 0
