"""Post-run artifact checks: secret scan, JUnit invariants, latency percentiles."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import REDACT

#: ``(label, pattern)`` — each fires on a secret *value*, never on a bare name.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("provider-key", re.compile(r"sk-[A-Za-z0-9_\-]{8,}")),
    ("resend-key", re.compile(r"re_[A-Za-z0-9]{16,}")),
    ("github-pat", re.compile(r"ghp_[A-Za-z0-9]{8,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}")),
)
SCAN_SUFFIXES = (".xml", ".json", ".log", ".txt", ".md")


def scan_secrets(paths: list[Path]) -> list[str]:
    """Return human-readable findings for any secret-shaped value in ``paths``."""
    findings: list[str] = []
    for path in paths:
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"{path}:{label}")
    return findings


def junit_counts(path: Path) -> dict:
    """Sum JUnit counters and split xfail out of plain skips."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    counts = {"tests": 0, "errors": 0, "failures": 0, "skipped": 0}
    xfailed = 0
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.get(key, 0) or 0)
    for case in root.iter("testcase"):
        for child in case:
            if child.tag in ("skipped", "error", "failure"):
                blob = f"{child.get('type', '')} {child.get('message', '')}".lower()
                if "xfail" in blob:
                    xfailed += 1
    counts["xfailed"] = xfailed
    counts["passed"] = (counts["tests"] - counts["errors"]
                        - counts["failures"] - counts["skipped"])
    return counts


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = max(0, min(len(ordered) - 1, int(round(pct * (len(ordered) - 1)))))
    return ordered[idx]


def timings_summary(timings_path: Path) -> dict | None:
    """Summarise a ``timings.json`` list of ``{ms, status, path}`` records."""
    if not timings_path.exists():
        return None
    try:
        records = json.loads(timings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    lats = [float(r["ms"]) for r in records if isinstance(r.get("ms"), (int, float))]
    statuses: dict[str, int] = {}
    for r in records:
        statuses[str(r.get("status"))] = statuses.get(str(r.get("status")), 0) + 1
    return {
        "calls": len(records),
        "statuses": statuses,
        "p50": percentile(lats, 0.50),
        "p95": percentile(lats, 0.95),
        "p99": percentile(lats, 0.99),
        "max": max(lats) if lats else 0.0,
    }
