"""Context assembly: build the review packet from a glob/manifest selection."""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from pathlib import Path

from .ast_summary import summarize_files


def _expand(include: list[str], exclude: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in include:
        matches = sorted(Path(".").glob(pattern))
        for path in matches:
            if not path.is_file():
                continue
            rel = str(path)
            if any(fnmatch.fnmatch(rel, pat) for pat in exclude):
                continue
            if path not in files:
                files.append(path)
    return files


def _read_manifest(manifest: Path) -> list[Path]:
    paths: list[Path] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line)
        if p.is_file():
            paths.append(p)
    return paths


def build_packet(include: list[str] | None = None, *,
                 exclude: list[str] | None = None,
                 manifest: Path | None = None,
                 max_chars: int | None = None,
                 ast_summary: bool = False,
                 header: str = "") -> str:
    """Build a review packet: inventory + (AST summary) + embedded sources."""
    include = include or []
    exclude = exclude or []
    files = _expand(include, exclude)
    if manifest:
        files = [p for p in _read_manifest(manifest) if p not in files] + files

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_lines = 0
    inventory = ["| File | Lines | Bytes |", "|---|---|---|"]
    body: list[str] = []
    budget = max_chars if max_chars is not None else float("inf")  # type: ignore[assignment]
    used = 0

    for path in files:
        text = path.read_text(encoding="utf-8").rstrip("\n")
        lines = len(text.splitlines())
        total_lines += lines
        inventory.append(f"| `{path}` | {lines} | {len(text.encode())} |")

        block = (f"### `{path}`\n\n"
                 f"```{_language(path)}\n{text}\n```\n")
        if used + len(block) <= budget:
            body.append(block)
            used += len(block)

    packet = [
        "# Review packet\n" if not header else header,
        f"> Assembled: {stamp} · {len(files)} files · {total_lines} lines.\n",
        "## Inventory\n",
        "\n".join(inventory),
        "\n",
    ]
    if ast_summary:
        packet.append("## AST summary\n")
        packet.append(summarize_files(files))
        packet.append("\n")
    if files:
        packet.append("## Sources\n")
        packet.extend(body)
    return "\n".join(packet)


def _language(path: Path) -> str:
    mapping = {".py": "python", ".ts": "typescript", ".js": "javascript",
               ".json": "json", ".md": "markdown", ".ini": "ini",
               ".yml": "yaml", ".yaml": "yaml", ".sh": "bash", ".toml": "toml"}
    return mapping.get(path.suffix, "")
