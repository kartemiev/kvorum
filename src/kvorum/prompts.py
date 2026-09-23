"""Prompt construction (role instruction + domain rules + the review packet)."""

from __future__ import annotations

from .panel import Panel, Seat


def build_prompt(packet: str, rules: str, seat: Seat, panel: Panel) -> str:
    """Role instruction + domain rules + the reviewed packet."""
    sections = "\n".join(panel.sections.required)
    return (
        f"# Seat role: {seat.seat} ({seat.seat_id}) — {seat.role_line}\n\n"
        "You are a member of a cross-review panel (a \"quorum\") performing a strict,\n"
        "independent review of the artifact below. Review it from your assigned role\n"
        "and do not simply agree: look for bugs, blind spots, security issues and\n"
        "contract mismatches.\n\n"
        "Return your answer STRICTLY in the following markdown shape (no preamble, no\n"
        "apologies). Be compact — no wholesale quoting of the artifact, only findings\n"
        "and references to concrete paths/lines. Reasoning is allowed, but the visible\n"
        "answer MUST contain every section below:\n\n"
        f"{sections}\n\n"
        "Domain rules that are mandatory to check (violating any of them is a\n"
        "blocking `CHANGES REQUESTED` with a critical finding):\n\n"
        f"{rules}\n\n"
        "---\n\n# ARTIFACT UNDER REVIEW\n\n" + packet
    )


def build_resume_prompt(seat: Seat, previous: str, packet: str, rules: str,
                        panel: Panel) -> str:
    """Continuation prompt: finish an answer that hit the ``max_tokens`` cap."""
    sections = "\n".join(panel.sections.resume)
    return (
        f"# Seat role: {seat.seat} ({seat.seat_id}) — {seat.role_line} "
        "(continuation of the review)\n\n"
        "Your previous verdict on the artifact was TRUNCATED by the output budget:\n"
        "the trailing mandatory sections never arrived. Finish the review now.\n\n"
        "The artifact may have changed since your first answer: the artifact below is\n"
        "authoritative; note any discrepancies in the blind-spots section.\n\n"
        "STRICTLY: start IMMEDIATELY with the sections below — no preamble, no\n"
        "apologies, no re-statement of the sections you already produced.\n\n"
        "## Mandatory sections (only these)\n"
        f"{sections}\n\n"
        "Domain rules (violating any is a blocking `CHANGES REQUESTED`):\n\n"
        f"{rules}\n\n"
        "---\n\n# YOUR PREVIOUS ANSWER (truncated here; do not repeat it)\n\n"
        + previous
        + "\n\n---\n\n# ARTIFACT UNDER REVIEW\n\n" + packet
    )
