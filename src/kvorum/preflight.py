"""Pre-flight context check: packet size vs per-seat context budgets.

Runs *before* any API call. ``--preflight`` prints a per-seat FIT/OVERFLOW table
and, by default, blocks the run (exit 1) when any seat overflows its context
budget. ``--skip-overflow`` instead drops the overflowing seats and recomputes
the quorum from the survivors — and blocks with ``INSUFFICIENT_SEATS`` when the
remaining seats cannot reach ``quorum.required``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .panel import Seat, seat_context_limit

#: Crude token estimate — 1 token ≈ 4 characters (matches the ``len/4`` rule
#: used everywhere else in the docs/benchmarks).
TOKENS_PER_CHAR_DIVISOR = 4


def estimate_tokens(text: str) -> int:
    """Approximate token count for ``text`` (``len(text) // 4``)."""
    return len(text) // TOKENS_PER_CHAR_DIVISOR


def packet_bytes(text: str) -> int:
    """UTF-8 byte size of ``text``."""
    return len(text.encode("utf-8"))


@dataclass(frozen=True)
class SeatCheck:
    """One row of the pre-flight table."""

    seat: Seat
    model: str
    limit_tokens: int
    packet_tokens: int
    status: str  # "FIT" | "OVERFLOW"


def check_seats(seats: list[Seat], packet_text: str) -> list[SeatCheck]:
    """Compare the packet's estimated token count against each seat's budget."""
    est = estimate_tokens(packet_text)
    rows: list[SeatCheck] = []
    for seat in seats:
        limit = seat_context_limit(seat)
        rows.append(SeatCheck(seat, seat.model, limit, est,
                              "FIT" if est <= limit else "OVERFLOW"))
    return rows


def format_table(rows: list[SeatCheck], est_tokens: int, est_bytes: int) -> str:
    """Render the ``Seat | Model | Limit | Packet Size | Status`` summary."""
    lines = [
        f"pre-flight: packet ≈ {est_tokens:,} tokens | {est_bytes:,} bytes",
        "| Seat | Model | Limit (tokens) | Packet Size | Status |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(f"| {row.seat.seat} | {row.model} | {row.limit_tokens:,} | "
                     f"{row.packet_tokens:,} | {row.status} |")
    return "\n".join(lines)


def run_preflight(seats: list[Seat], packet_text: str, quorum_required: int, *,
                  skip_overflow: bool = False) -> tuple[list[Seat], int | None]:
    """Run the pre-flight check and decide whether (and with which seats) to proceed.

    Returns ``(seats_to_run, exit_code)``. ``exit_code`` is ``None`` when the run
    may proceed and ``1`` when it must be blocked — either because a seat
    overflows (strict, fail-closed default) or because ``--skip-overflow`` leaves
    fewer seats than ``quorum_required`` (``INSUFFICIENT_SEATS``).
    """
    rows = check_seats(seats, packet_text)
    print(format_table(rows, estimate_tokens(packet_text), packet_bytes(packet_text)),
          flush=True)
    overflow = [r for r in rows if r.status == "OVERFLOW"]
    if not overflow:
        return seats, None

    dropped = ", ".join(r.seat.seat_id for r in overflow)
    if not skip_overflow:
        print(f"[!] pre-flight: {len(overflow)} seat(s) OVERFLOW ({dropped}) — "
              f"run blocked. Re-run with --skip-overflow to drop them, or "
              f"--ast-summary to shrink the packet.", file=sys.stderr, flush=True)
        return seats, 1

    remaining = [r.seat for r in rows if r.status == "FIT"]
    if len(remaining) < quorum_required:
        print(f"[!] pre-flight: INSUFFICIENT_SEATS — {len(remaining)} seat(s) fit "
              f"but quorum requires {quorum_required}. Dropped OVERFLOW seats: "
              f"{dropped}.", file=sys.stderr, flush=True)
        return remaining, 1

    print(f"[pre-flight] --skip-overflow: dropping {len(overflow)} OVERFLOW "
          f"seat(s) ({dropped}); {len(remaining)} remain (quorum "
          f"{quorum_required}).", flush=True)
    return remaining, None
