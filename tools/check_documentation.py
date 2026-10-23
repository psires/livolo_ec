#!/usr/bin/env python3
"""Check purpose and parameter docstrings for all project-owned Python functions."""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    """Yield maintained Python sources, excluding dependencies and build outputs."""
    for directory in ("tools", "host", "firmware"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            if not {"third_party", "build", ".venv", "__pycache__"} & set(path.parts):
                yield path


def check_file(path):
    """Report missing function intent or parameter documentation in one module.

    @param path Path to a project-owned Python source file.
    @return List of diagnostics suitable for a local check or CI log.
    """
    errors = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        location = f"{path.relative_to(ROOT)}:{node.lineno}: {node.name}"
        doc = ast.get_docstring(node) or ""
        if not doc.strip() or doc.lstrip().startswith("@param"):
            errors.append(f"{location}: missing purpose docstring")
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        arguments += [arg for arg in (node.args.vararg, node.args.kwarg) if arg]
        for argument in arguments:
            name = argument.arg
            if name in ("self", "cls"):
                continue
            pattern = rf"@param\s+{re.escape(name)}\s+\S"
            if not re.search(pattern, doc):
                errors.append(f"{location}: missing @param {name} description")
    return errors


def main():
    """Fail the contributor check when a function lacks its Python API contract."""
    files = list(source_files())
    errors = [error for path in files for error in check_file(path)]
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Function documentation checked in {len(files)} Python files")


if __name__ == "__main__":
    main()
