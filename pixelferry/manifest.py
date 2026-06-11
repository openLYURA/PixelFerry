"""Build and parse manifest entries for the package."""

import base64
import json
import os
from typing import List, Dict

from .utils import is_text_file, safe_relpath, file_mode_hex, sha256_hex
from .constants import DEFAULT_EXCLUDES


def build_manifest(repo_path: str, extra_excludes: set = None) -> List[Dict]:
    """Walk repo_path and return a list of manifest entries."""
    excludes = DEFAULT_EXCLUDES | (extra_excludes or set())
    entries = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d for d in dirs
            if d not in excludes and not d.startswith(".")
        ]
        for fname in sorted(files):
            abs_path = os.path.join(root, fname)
            rel_path = safe_relpath(os.path.relpath(abs_path, repo_path))

            text_mode = is_text_file(abs_path)
            if text_mode:
                with open(abs_path, "rb") as f:
                    raw = f.read()
                content_bytes = raw
                encoding = "utf-8"
                file_type = "text"
            else:
                with open(abs_path, "rb") as f:
                    raw = f.read()
                content_bytes = base64.b64encode(raw)
                encoding = "base64"
                file_type = "binary"

            entry = {
                "kind": "file",
                "path": rel_path,
                "type": file_type,
                "encoding": encoding,
                "mode": file_mode_hex(abs_path),
                "length": len(raw),
                "sha256": sha256_hex(raw),
            }
            entries.append((entry, content_bytes))

    return entries


def parse_manifest_entries(package_bytes: bytes):
    """Parse a package byte stream into (manifest_entries, file_contents).

    Yields (entry_dict, content_bytes) for each file.
    """
    lines = []
    pos = 0

    # Read header
    while pos < len(package_bytes):
        end = package_bytes.find(b"\n", pos)
        if end == -1:
            break
        line = package_bytes[pos:end].decode("utf-8")
        pos = end + 1
        lines.append(line)
        if line.startswith("FILE_COUNT="):
            break

    if not lines or not lines[-1].startswith("FILE_COUNT="):
        raise ValueError("Missing FILE_COUNT header in package")

    file_count = int(lines[-1].split("=", 1)[1])

    for i in range(file_count):
        # Read JSON line
        entry_line = None
        while pos < len(package_bytes):
            end = package_bytes.find(b"\n", pos)
            if end == -1:
                break
            line = package_bytes[pos:end].decode("utf-8")
            pos = end + 1
            if line.strip():
                entry_line = line
                break

        if entry_line is None:
            raise ValueError(f"Unexpected end of package: missing manifest entry {i}")

        try:
            entry = json.loads(entry_line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid manifest JSON at entry {i}: {e}") from e

        # Read content until FILE_END_MARKER
        end_marker = b"PXFERRY_FILE_END\n"
        marker_pos = package_bytes.find(end_marker, pos)
        if marker_pos == -1:
            raise ValueError(f"Missing PXFERRY_FILE_END marker for entry {i}")

        content_bytes = package_bytes[pos:marker_pos]
        pos = marker_pos + len(end_marker)

        yield entry, content_bytes
