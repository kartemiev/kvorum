"""Lightweight AST summaries for the review packet (stdlib ``ast``, Python only).

Keeps huge codebases within the model's context budget: instead of embedding
every source body, ``kvorum pack --ast-summary`` embeds symbols, signatures and
imports so reviewers still see the shape of the code.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = []
    a = node.args
    pos = [arg.arg for arg in a.posonlyargs] + [arg.arg for arg in a.args]
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for name, default in zip(pos, defaults):
        args.append(name if default is None else f"{name}={ast.unparse(default)}")
    if a.vararg:
        args.append(f"*{a.vararg.arg}")
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        args.append(f"{arg.arg}={ast.unparse(default)}" if default is not None else arg.arg)
    if a.kwarg:
        args.append(f"**{a.kwarg.arg}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


def summarize_source(source: str, name: str = "<source>") -> str:
    """Return a compact markdown summary of one Python source string."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"- `{name}`: syntax error: {exc}"
    lines: list[str] = [f"### `{name}`"]
    imports: list[str] = []
    defs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node).strip())
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else _signature(node)
            defs.append(kind)
    if imports:
        lines.append("\nImports: `" + "` · `".join(sorted(set(imports))) + "`")
    if defs:
        lines.append("\nDefinitions:\n" + "\n".join(f"- `{d}`" for d in defs))
    return "\n".join(lines)


def summarize_file(path: Path) -> str:
    return summarize_source(path.read_text(encoding="utf-8"), str(path))


def summarize_files(paths: list[Path]) -> str:
    return "\n\n".join(summarize_file(p) for p in paths if p.suffix == ".py")
