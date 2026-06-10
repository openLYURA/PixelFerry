"""Verify reconstructed repository against manifest."""

import os
from typing import List, Tuple

from .manifest import parse_manifest_entries
from .utils import sha256_hex


def verify_reconstructed_repo(
    package_bytes: bytes,
    output_dir: str,
) -> Tuple[bool, List[str]]:
    """Verify that reconstructed files match the manifest.

    Returns (success, list_of_errors).
    """
    errors = []

    for entry, expected_content in parse_manifest_entries(package_bytes):
        rel_path = entry["path"]
        abs_path = os.path.join(output_dir, rel_path)

        if not os.path.exists(abs_path):
            errors.append(f"Missing: {rel_path}")
            continue

        with open(abs_path, "rb") as f:
            actual = f.read()

        actual_hash = sha256_hex(actual)
        if actual_hash != entry["sha256"]:
            errors.append(f"Hash mismatch: {rel_path}")

    return len(errors) == 0, errors
