"""Read-only views of the registered worktree and its persisted CBM index."""
from __future__ import annotations

import heapq
import re
from pathlib import Path

from fastapi import HTTPException

from .security import UnsafePathError, is_excluded_path, resolve_safe_path

SOURCE_TYPES = {".ts", ".tsx", ".js", ".jsx", ".py", ".cs", ".cpp", ".c", ".h", ".go", ".rs",
                ".java", ".lua", ".gd", ".css", ".html", ".md", ".sql", ".json", ".yaml", ".yml"}
SECRET = re.compile(
    r"(?i)(?:password|api[_-]?key|access[_-]?key|token|secret|authorization).{0,6}[:=]\s*[\"']?.{6,}"
    r"|-----BEGIN [^-]*PRIVATE KEY-----|\b(?:sk-|gh[pousr]_|github_pat_|AKIA|AIza|Bearer\s+)\S{10,}"
)


def safe_entry(root, path, excludes):
    try:
        rel, file = resolve_safe_path(root, path)
    except (UnsafePathError, OSError, ValueError) as exc:
        raise HTTPException(422, "Invalid worktree path") from exc
    resolved = file.relative_to(root.resolve()).as_posix()
    if rel != resolved or is_excluded_path(rel, excludes) or any(p.startswith(".") for p in Path(rel).parts):
        raise HTTPException(403, "Excluded path")
    return file


def directory_page(root, path, excludes, offset, limit):
    folder = safe_entry(root, path, excludes) if path else root
    if not folder.is_dir():
        raise HTTPException(404, "Directory unavailable")
    def entries():
        for child in folder.iterdir():
            rel = child.relative_to(root).as_posix()
            if child.is_symlink() or child.name.startswith(".") or is_excluded_path(rel, excludes):
                continue
            yield (not child.is_dir(), child.name.casefold(), rel)
    page = heapq.nsmallest(offset + limit + 1, entries())[offset:]
    return {"items": [{"path": rel, "kind": "file" if is_file else "directory"} for is_file, _, rel in page[:limit]],
            "next_offset": offset + limit if len(page) > limit else None}


def source_page(root, path, excludes, line):
    file = safe_entry(root, path, excludes)
    if not file.is_file() or file.suffix.lower() not in SOURCE_TYPES:
        raise HTTPException(415, "Unsupported source type")
    with file.open("rb") as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576 or b"\0" in raw:
        raise HTTPException(413, "Source exceeds preview limits")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(415, "Unsupported encoding") from exc
    if SECRET.search(source):
        raise HTTPException(403, "Source contains sensitive content")
    lines = source.splitlines()
    return {"path": path, "line": line, "total_lines": len(lines), "bytes": len(raw),
            "lines": [s[:600] for s in lines[line - 1:line + 79]]}

