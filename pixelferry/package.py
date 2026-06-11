"""Build and unpack .pxf package files."""

import base64
import json
import os
from typing import List, Dict

from .manifest import build_manifest, parse_manifest_entries
from .constants import PACKAGE_HEADER, FILE_END_MARKER
from .utils import sha256_hex, safe_relpath


def build_package(repo_path: str, output_path: str,
                  extra_excludes: set = None) -> bytes:
    """Build a .pxf package from a repo directory.

    Returns the raw package bytes.
    """
    entries = build_manifest(repo_path, extra_excludes)
    file_count = len(entries)

    parts = [PACKAGE_HEADER]
    parts.append(f"FILE_COUNT={file_count}\n".encode("utf-8"))

    for entry, content_bytes in entries:
        parts.append((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        parts.append(content_bytes)
        parts.append(FILE_END_MARKER)

    package_bytes = b"".join(parts)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(package_bytes)

    return package_bytes


def unpack_package(package_bytes: bytes, output_dir: str,
                   overwrite: bool = False) -> List[Dict]:
    """Unpack a .pxf package into output_dir.

    Returns list of manifest entries written.
    """
    written = []

    for entry, content_bytes in parse_manifest_entries(package_bytes):
        rel_path = entry["path"]

        # Safety: no traversal
        safe_relpath(rel_path)

        abs_path = os.path.join(output_dir, rel_path)
        real_path = os.path.realpath(abs_path)
        real_output = os.path.realpath(output_dir)
        if not real_path.startswith(real_output + os.sep) and real_path != real_output:
            raise ValueError(f"Path escapes output directory: {rel_path}")
        if os.path.exists(abs_path) and not overwrite:
            raise FileExistsError(f"File exists, use overwrite=True: {rel_path}")

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        if entry["encoding"] == "base64":
            data = base64.b64decode(content_bytes)
        else:
            data = content_bytes

        with open(abs_path, "wb") as f:
            f.write(data)

        written.append(entry)

    return written


def unpack_package_file(package_path: str, output_dir: str,
                        overwrite: bool = False) -> List[Dict]:
    """Unpack from a file path."""
    with open(package_path, "rb") as f:
        package_bytes = f.read()
    return unpack_package(package_bytes, output_dir, overwrite)


def get_package_sha256(package_bytes: bytes) -> str:
    return sha256_hex(package_bytes)
