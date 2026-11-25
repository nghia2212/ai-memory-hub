"""Path safety, symlink escape rejection, secret exclusions, and Git worktree inspection."""
from __future__ import annotations

import fnmatch
import hashlib
import os
import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git/**",
    ".git",
    ".codebase-memory/**",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    "node_modules/**",
    "**/node_modules/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    ".cache/**",
    "**/.cache/**",
    ".tmp/**",
    ".wrangler/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".venv/**",
    "**/.venv/**",
    "test-results/**",
    "test-results-*/**",
    "outputs/**",
    "art-in/**",
    "art-src/**",
    "trao-doi-anh/**",
    "public/voice/**",
    "public/audio/**",
    "static/voice/**",
    "static/audio/**",
    "**/credentials*",
    "credentials*",
    # Binary 3D/media/archive/font assets (cataloged as metadata only, never text-indexed)
    "*.glb",
    "**/*.glb",
    "*.gltf",
    "**/*.gltf",
    "*.blend",
    "**/*.blend",
    "*.png",
    "**/*.png",
    "*.jpg",
    "**/*.jpg",
    "*.jpeg",
    "**/*.jpeg",
    "*.webp",
    "**/*.webp",
    "*.gif",
    "**/*.gif",
    "*.ico",
    "**/*.ico",
    "*.mp3",
    "**/*.mp3",
    "*.ogg",
    "**/*.ogg",
    "*.wav",
    "**/*.wav",
    "*.mp4",
    "**/*.mp4",
    "*.webm",
    "**/*.webm",
    "*.zip",
    "**/*.zip",
    "*.woff",
    "**/*.woff",
    "*.woff2",
    "**/*.woff2",
    "*.ttf",
    "**/*.ttf",
    "*.otf",
    "**/*.otf",
    "*.pdf",
    "**/*.pdf",
    "*.log",
    "**/*.log",
)


class UnsafePathError(ValueError):
    """Raised when a path fails containment, traversal, or symlink checks."""


def normalize_rel_posix(raw_path: str) -> str:
    """Validate and normalize a repository-relative POSIX path."""
    if not raw_path or "\x00" in raw_path:
        raise UnsafePathError("Empty or null-byte path")
    cleaned = raw_path.replace("\\", "/")
    if cleaned.startswith("/") or (len(cleaned) >= 2 and cleaned[1] == ":"):
        raise UnsafePathError("Absolute path is not allowed")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise UnsafePathError("Path traversal segment is not allowed")
    return posixpath.normpath("/".join(parts))


def resolve_safe_path(container_root: Path, raw_rel_path: str) -> tuple[str, Path]:
    """Resolve raw_rel_path inside container_root, rejecting symlinks escaping root."""
    rel_posix = normalize_rel_posix(raw_rel_path)
    root_real = Path(os.path.realpath(container_root))
    candidate = container_root / Path(*PurePosixPath(rel_posix).parts)
    candidate_real = Path(os.path.realpath(candidate))
    try:
        candidate_real.relative_to(root_real)
    except ValueError as exc:
        raise UnsafePathError("Symlink or path escapes worktree root") from exc
    return rel_posix, candidate_real


_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".codebase-memory",
        "node_modules",
        "dist",
        "build",
        ".cache",
        ".tmp",
        ".wrangler",
        "__pycache__",
        ".venv",
        "outputs",
        "art-in",
        "art-src",
        "trao-doi-anh",
        "test-results",
    }
)
_EXCLUDED_EXTS: frozenset[str] = frozenset(
    {
        ".glb", ".gltf", ".blend", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
        ".mp3", ".ogg", ".wav", ".mp4", ".webm", ".zip", ".woff", ".woff2", ".ttf",
        ".otf", ".pdf", ".log", ".pem", ".key",
    }
)


def is_excluded_path(rel_posix_path: str, extra_excludes: list[str] | None = None) -> bool:
    """Return True if path matches built-in secret/dependency exclusions or project excludes."""
    parts = rel_posix_path.split("/")
    for seg in parts:
        low = seg.lower()
        if low in _EXCLUDED_DIR_NAMES or low.startswith("test-results-") or low == ".env" or low.startswith(".env.") or "credentials" in low:
            return True
    last = parts[-1].lower()
    dot = last.rfind(".")
    if dot > 0 and last[dot:] in _EXCLUDED_EXTS:
        return True
    if not extra_excludes:
        return False
    pure = PurePosixPath(rel_posix_path)
    for pat in extra_excludes:
        norm_pat = pat.replace("\\", "/")
        if norm_pat.startswith("./"):
            norm_pat = norm_pat[2:]
        if fnmatch.fnmatch(rel_posix_path, norm_pat) or pure.match(norm_pat):
            return True
        if norm_pat.endswith("/**"):
            prefix = norm_pat[:-3]
            if rel_posix_path == prefix or rel_posix_path.startswith(prefix + "/"):
                return True
    return False


def map_to_host_path(host_root: str, rel_posix_path: str) -> str:
    """Map a validated relative POSIX path onto the registered host_root."""
    rel = normalize_rel_posix(rel_posix_path)
    if "\\" in host_root or (len(host_root) >= 2 and host_root[1] == ":"):
        return str(PureWindowsPath(host_root) / PureWindowsPath(*PurePosixPath(rel).parts))
    return str(PurePosixPath(host_root) / PurePosixPath(rel))


@dataclass(frozen=True)
class GitWorktreeState:
    head_commit: str
    branch: str | None
    detached: bool
    dirty_fingerprint: str
    is_linked_worktree: bool


def _read_git_head_from_dir(git_dir: Path) -> tuple[str, str | None, bool]:
    head_file = git_dir / "HEAD"
    if not head_file.is_file():
        return "unknown", None, False
    raw = head_file.read_text(encoding="utf-8", errors="replace").strip()
    if raw.startswith("ref: "):
        ref = raw[5:].strip()
        branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ref
        ref_file = git_dir / Path(*ref.split("/"))
        if ref_file.is_file():
            return ref_file.read_text(encoding="utf-8", errors="replace").strip(), branch, False
        packed_refs = git_dir / "packed-refs"
        if packed_refs.is_file():
            for line in packed_refs.read_text(encoding="utf-8", errors="replace").splitlines():
                if line and not line.startswith("#") and not line.startswith("^"):
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1].strip() == ref:
                        return parts[0].strip(), branch, False
        return "unborn", branch, False
    return raw, None, True


def compute_tree_fingerprint(container_root: Path, extra_excludes: list[str] | None = None) -> str:
    """Compute a deterministic SHA-256 fingerprint over non-excluded files (content + relpath)."""
    hasher = hashlib.sha256()
    root_real = Path(os.path.realpath(container_root))
    if not root_real.is_dir():
        return "missing"

    entries: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root_real).as_posix()
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            full_d = Path(dirpath) / d
            if full_d.is_symlink():
                continue
            rel_d = d if rel_dir == "." else f"{rel_dir}/{d}"
            if is_excluded_path(rel_d, extra_excludes) or is_excluded_path(rel_d + "/x", extra_excludes):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            full_f = Path(dirpath) / fname
            if full_f.is_symlink():
                continue
            rel_f = fname if rel_dir == "." else f"{rel_dir}/{fname}"
            if is_excluded_path(rel_f, extra_excludes):
                continue
            entries.append((rel_f, full_f))

    use_content = len(entries) <= 100
    for rel_f, full_f in sorted(entries, key=lambda item: item[0]):
        hasher.update(rel_f.encode("utf-8"))
        hasher.update(b"\x00")
        try:
            st = full_f.stat()
            if not use_content or st.st_size > 256 * 1024:
                hasher.update(f"meta:{st.st_size}:{st.st_mtime_ns}".encode("utf-8"))
            else:
                with open(full_f, "rb") as fh:
                    while chunk := fh.read(65536):
                        hasher.update(chunk)
        except OSError:
            hasher.update(b"<unreadable>")
        hasher.update(b"\x00")
    return hasher.hexdigest()[:16]


def inspect_worktree_git(
    container_root: Path,
    fallback_head: str | None = None,
    fallback_branch: str | None = None,
    fallback_dirty: str | None = None,
    extra_excludes: list[str] | None = None,
) -> GitWorktreeState:
    """Inspect HEAD, branch/detached state, linked worktree .git file, and dirty fingerprint."""
    git_entry = container_root / ".git"
    head_commit = fallback_head or "unknown"
    branch = fallback_branch
    detached = False
    is_linked = False

    if git_entry.is_dir():
        head_commit, branch, detached = _read_git_head_from_dir(git_entry)
    elif git_entry.is_file():
        is_linked = True
        text = git_entry.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            target = text[7:].strip()
            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = (container_root / target_path).resolve()
            try:
                target_real = Path(os.path.realpath(target_path))
                root_real = Path(os.path.realpath(container_root))
                target_real.relative_to(root_real)
                if target_real.is_dir():
                    head_commit, branch, detached = _read_git_head_from_dir(target_real)
            except ValueError:
                # Linked .git points outside container_root (typical Windows worktree mount).
                # Use host-reported metadata if available without failing or escaping root.
                pass

    tree_fp = compute_tree_fingerprint(container_root, extra_excludes)
    dirty_fp = f"{fallback_dirty}:{tree_fp}" if fallback_dirty else tree_fp
    return GitWorktreeState(
        head_commit=head_commit,
        branch=branch,
        detached=detached,
        dirty_fingerprint=dirty_fp,
        is_linked_worktree=is_linked,
    )
