"""Utility functions for file detection, path safety, and hashing."""

import hashlib
import os


def sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_text_file(path: str, sample_size: int = 8192) -> bool:
    """Heuristic: try UTF-8 decode on a chunk; if it fails, treat as binary."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
        chunk.decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def safe_relpath(path: str) -> str:
    """Ensure path uses forward slashes and has no traversal."""
    normalized = path.replace("\\", "/").strip("/")
    if normalized.startswith("..") or normalized.startswith("/"):
        raise ValueError(f"Unsafe path: {path}")
    if ".." in normalized.split("/"):
        raise ValueError(f"Path traversal detected: {path}")
    return normalized


def file_mode_hex(path: str) -> str:
    """Get file permissions as octal string, e.g. '0644'."""
    try:
        st = os.stat(path)
        return oct(st.st_mode)[-4:]
    except OSError:
        return "0644"
