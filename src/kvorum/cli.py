"""Command-line interface for kvorum.

Subcommands: ``list-seats``, ``verify-models``, ``pack``, ``dry-run``, ``run``,
``resume``, ``verdict``. Every path is relative or overridable via CLI/env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import Settings, load_env_file
from .context import build_packet
from .notify import notify_enabled
from .panel import load_panel, effective_seats, seat_file_name
from .prompts import build_prompt, build_resume_prompt
from .providers import verify_models
from .runner import load_inputs, previous_answer, run_panel
from .verdict import build_verdict_json, build_verdict_markdown


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--panel", default=None,
                        help="panel file: panel.json, a legacy council.json, or an "
                             "examples preset (e.g. examples/panel.openrouter.json to "
                             "switch primary provider); defaults to KVORUM_PANEL or ./panel.json")
    parser.add_argument("--packet", default=None, help="review packet (default packet.md)")
    parser.add_argument("--rules", default=None, help="domain rules file (default rules.md)")
    parser.add_argument("--runs-dir", default=None, help="runs output directory (default runs)")
    parser.add_argument("--env-file", default=None, help="credential file (default .env)")


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if args.panel:
        settings.panel_path = Path(args.panel)
    if args.packet:
        settings.packet_path = Path(args.packet)
    if args.rules:
        settings.rules_path = Path(args.rules)
    if args.runs_dir:
        settings.runs_dir = Path(args.runs_dir)
    if getattr(args, "env_file", None):
        settings.env_files = tuple(Path(p) for p in args.env_file.split(":"))
    return settings


def _load(settings: Settings):
    if settings.panel_path.exists():
        panel = load_panel(settings.panel_path)
    elif settings.panel_path == Path("panel.json"):
        panel = load_panel(None)  # built-in default panel
    else:
        raise FileNotFoundError(f"panel file not found: {settings.panel_path}")
    file_env = load_env_file(settings.env_files)
    return panel, file_env


def _parse_seats(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def cmd_list_seats(args: argparse.Namespace) -> int:
    settings = _settings(args)
    panel, file_env = _load(settings)
    excluded = set(panel.excluded_by_default)
    print(f"packet: {settings.packet_path} | rules: {settings.rules_path}")
    print(f"excluded_by_default: {panel.excluded_by_default} | quorum: "
          f"{panel.quorum_required} (fail-closed={panel.fail_closed})")
    print(f"{'seat':18} {'tier':6} {'slug':24} {'provider':12} {'model':30} key")
    available = 0
    for seat in panel.seats:
        provider = panel.provider(seat.provider)
        key, source = _resolve(provider, file_env)
        available += 1 if key else 0
        marks = "excluded" if seat.slug in excluded else ""
        print(f"{seat.seat:18} {seat.tier:6} {seat.slug:24} "
              f"{seat.provider:12} {seat.model:30} "
              f"{source if key else 'MISSING'} {marks}".rstrip())
    effective = effective_seats(panel)
    print(f"\nseats with credentials: {available}/{len(panel.seats)} | effective: "
          f"{len(effective)} — {', '.join(s.seat for s in effective)}")
    return 0 if min(available, len(effective)) >= panel.quorum_required else 1


def _resolve(provider, file_env):
    from .config import resolve_key
    return resolve_key(provider.api_key_env, file_env)


def cmd_verify_models(args: argparse.Namespace) -> int:
    settings = _settings(args)
    panel, file_env = _load(settings)
    report = verify_models(panel, file_env)
    at_risk = 0
    for row in report:
        alias = f" (alias: {row['model_alias']})" if row["model_alias"] else ""
        state = {True: "ok", False: "MISSING"}.get(row["present"], "unknown (no key)")
        if row["present"] is False:
            at_risk += 1
        print(f"{row['seat_id']:14} {row['provider']:12} "
              f"{', '.join(row['model_ids']):34}{alias} -> {state}")
        for fb in row["fallbacks"]:
            print(f"{'':14} {'':12} fallback {fb['provider']}: "
                  f"{', '.join(fb['model_ids'])}")
    print(f"\nseats whose primary provider serves none of the configured ids: {at_risk}")
    return 1 if at_risk else 0


def cmd_pack(args: argparse.Namespace) -> int:
    settings = _settings(args)
    include = args.include or []
    if not include and not args.manifest:
        print("[!] pack needs --include or --manifest", file=sys.stderr)
        return 2
    packet = build_packet(include, exclude=args.exclude or [],
                          manifest=Path(args.manifest) if args.manifest else None,
                          max_chars=args.max_chars, ast_summary=args.ast_summary)
    out = Path(args.out) if args.out else settings.packet_path
    out.write_text(packet, encoding="utf-8")
    print(f"packet written: {out} ({len(packet)} chars)")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if not settings.packet_path.exists():
        print(f"[!] packet not found: {settings.packet_path} (run `kvorum pack` first)",
              file=sys.stderr)
        return 2
    panel, file_env = _load(settings)
    prompt_dir = settings.runs_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    packet, rules = load_inputs(settings)
    seats = effective_seats(panel, _parse_seats(args.seats) or None,
                            args.include_excluded)
    written = 0
    for seat in seats:
        if args.resume:
            previous = previous_answer(seat, settings.runs_dir)
            if not previous:
                print(f"[skip] {seat.seat}: no previous answer to continue")
                continue
            prompt = build_resume_prompt(seat, previous, packet, rules, panel)
            path = prompt_dir / seat_file_name(seat).replace(".json", ".resume.md")
        else:
            prompt = build_prompt(packet, rules, seat, panel)
            path = prompt_dir / seat_file_name(seat).replace(".json", ".md")
        path.write_text(prompt, encoding="utf-8")
        written += 1
        print(f"prompt written: {path} ({path.stat().st_size} B)")
    print(f"\nprompts: {written} | no API calls were made.")
    return 0


def _run_common(args: argparse.Namespace, resume: bool) -> int:
    settings = _settings(args)
    if not settings.packet_path.exists():
        print(f"[!] packet not found: {settings.packet_path} (run `kvorum pack` first)",
              file=sys.stderr)
        return 2
    if args.timeout is not None:
        settings.timeout_s = args.timeout
    if args.fallback_timeout is not None:
        settings.fallback_timeout_s = args.fallback_timeout
    if args.min_delay_s is not None:
        settings.min_delay_s = args.min_delay_s
    panel, file_env = _load(settings)
    return run_panel(
        panel, settings, file_env,
        only=_parse_seats(args.seats) or None,
        skip_seats=_parse_seats(args.skip_seats) or None,
        include_excluded=args.include_excluded,
        resume=resume,
        use_fallback=not args.no_fallback,
        soft_quorum=args.soft_quorum,
        archive=not args.no_archive,
        exclude_env=os.environ.get("COUNCIL_EXCLUDE_MODELS"),
        min_delay_s=settings.min_delay_s,
        notify=notify_enabled(args.notify),
    )


def cmd_run(args: argparse.Namespace) -> int:
    return _run_common(args, resume=False)


def cmd_resume(args: argparse.Namespace) -> int:
    return _run_common(args, resume=True)


def _read_results(runs_dir: Path) -> list[dict]:
    results: list[dict] = []
    for path in sorted(runs_dir.glob("*.json")):
        if path.name.startswith("run_meta") or ".continuation" in path.name:
            continue
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return results


def cmd_verdict(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if args.verdict:
        settings.verdict_path = Path(args.verdict)
    if args.verdict_json:
        settings.verdict_json_path = Path(args.verdict_json)
    panel, file_env = _load(settings)
    if not settings.packet_path.exists():
        print(f"[!] packet not found: {settings.packet_path} (run `kvorum pack` first)",
              file=sys.stderr)
        return 2
    packet = settings.packet_path.read_text(encoding="utf-8")
    results = _read_results(settings.runs_dir)
    if not results:
        print(f"[!] no seat answers found in {settings.runs_dir}", file=sys.stderr)
        return 2
    answered = sum(1 for r in results if r.get("text"))
    md = build_verdict_markdown(panel, settings.packet_path, results, answered, packet)
    info = build_verdict_json(panel, settings.packet_path, results, answered)
    settings.verdict_path.write_text(md, encoding="utf-8")
    settings.verdict_json_path.write_text(
        json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"verdict written: {settings.verdict_path} ({len(md)} chars) | "
          f"{settings.verdict_json_path}")
    return 0 if info["quorum"]["met"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kvorum", description="Cross-review by a panel of heterogeneous LLMs.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-seats", help="seat availability (no chat calls)")
    _common(p)
    p.set_defaults(func=cmd_list_seats)

    p = sub.add_parser("verify-models", help="check model ids against /models")
    _common(p)
    p.set_defaults(func=cmd_verify_models)

    p = sub.add_parser("pack", help="build the review packet")
    p.add_argument("--include", action="append", default=[],
                   help="glob of files to embed (repeatable)")
    p.add_argument("--exclude", action="append", default=[],
                   help="glob to skip (repeatable)")
    p.add_argument("--manifest", default=None, help="file listing paths (one per line)")
    p.add_argument("--max-chars", type=int, default=None, help="hard character budget")
    p.add_argument("--ast-summary", action="store_true",
                   help="embed an AST summary instead of full bodies")
    p.add_argument("--out", default=None, help="output path (default packet.md)")
    _common(p)
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("dry-run", help="write prompts without any API call")
    p.add_argument("--seats", "--only-seats", dest="seats", default="")
    p.add_argument("--include-excluded", action="store_true")
    p.add_argument("--resume", action="store_true")
    _common(p)
    p.set_defaults(func=cmd_dry_run)

    run_parents = [sub.add_parser(name, help=h) for name, h in
                   (("run", "real cross-review (costs money)"),
                    ("resume", "continue answers truncated by max_tokens"))]
    for p in run_parents:
        p.add_argument("--seats", "--only-seats", dest="seats", default="",
                       help="comma-separated seat refs to run (targeted re-run)")
        p.add_argument("--skip-seats", dest="skip_seats", default="",
                       help="comma-separated seat refs to skip in this run")
        p.add_argument("--include-excluded", action="store_true")
        p.add_argument("--no-fallback", action="store_true")
        p.add_argument("--soft-quorum", action="store_true")
        p.add_argument("--no-archive", action="store_true")
        p.add_argument("--timeout", type=float, default=None)
        p.add_argument("--fallback-timeout", type=float, default=None)
        p.add_argument("--min-delay-s", type=float, default=None,
                       help="pause between seat calls in seconds (serial run; "
                            "respect provider RPM limits)")
        p.add_argument("--notify", action="store_true",
                       help="send a completion notification (ntfy/webhook); "
                            "auto-enabled when KVORUM_NTFY_TOPIC or "
                            "KVORUM_NOTIFY_WEBHOOK is set")
        _common(p)
    run_parents[0].set_defaults(func=cmd_run)
    run_parents[1].set_defaults(func=cmd_resume)

    p = sub.add_parser("verdict", help="assemble verdict.md + verdict.json")
    p.add_argument("--verdict", default=None, help="verdict markdown output (default verdict.md)")
    p.add_argument("--verdict-json", default=None, help="verdict json output (default verdict.json)")
    _common(p)
    p.set_defaults(func=cmd_verdict)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, KeyError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


