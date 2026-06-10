"""Tests for manifest building and parsing."""

import os
import tempfile
import shutil
import json
from pixelferry.package import build_package, unpack_package, get_package_sha256
from pixelferry.verify import verify_reconstructed_repo


def _make_repo(base):
    """Create a test repository."""
    os.makedirs(os.path.join(base, "src"), exist_ok=True)
    os.makedirs(os.path.join(base, "docs"), exist_ok=True)

    with open(os.path.join(base, "README.md"), "w") as f:
        f.write("# Test Project\n\nHello world.\n")

    with open(os.path.join(base, "src", "main.py"), "w") as f:
        f.write("def hello():\n    print('hello')\n")

    with open(os.path.join(base, "src", "utils.py"), "w") as f:
        f.write("import os\n\ndef join(*a):\n    return os.path.join(*a)\n")

    with open(os.path.join(base, "docs", "guide.md"), "w") as f:
        f.write("# Guide\n\nStep 1.\nStep 2.\n")


def test_package_roundtrip():
    """Test build → unpack roundtrip with text files."""
    tmpdir = tempfile.mkdtemp()
    try:
        repo = os.path.join(tmpdir, "repo")
        out_dir = os.path.join(tmpdir, "restored")
        pkg_path = os.path.join(tmpdir, "test.pxf")

        _make_repo(repo)

        # Build
        pkg = build_package(repo, pkg_path)
        assert os.path.exists(pkg_path)
        assert len(pkg) > 0

        # Verify SHA-256
        sha = get_package_sha256(pkg)
        assert len(sha) == 64

        # Unpack
        os.makedirs(out_dir, exist_ok=True)
        written = unpack_package(pkg, out_dir)
        assert len(written) == 4  # 4 files created

        # Verify reconstruction
        ok, errors = verify_reconstructed_repo(pkg, out_dir)
        assert ok, f"Verification failed: {errors}"

        # Check specific files
        with open(os.path.join(out_dir, "README.md")) as f:
            content = f.read()
        assert content == "# Test Project\n\nHello world.\n"

    finally:
        shutil.rmtree(tmpdir)


def test_empty_repo():
    """Test with an empty repository."""
    tmpdir = tempfile.mkdtemp()
    try:
        repo = os.path.join(tmpdir, "empty_repo")
        os.makedirs(repo)

        pkg = build_package(repo, None)
        # Should still have header
        assert b"PXFERRY_PACKAGE_V1" in pkg
        assert b"FILE_COUNT=0" in pkg
    finally:
        shutil.rmtree(tmpdir)


def test_binary_file():
    """Test with a binary file (Base64 encoding)."""
    tmpdir = tempfile.mkdtemp()
    try:
        repo = os.path.join(tmpdir, "repo")
        os.makedirs(repo)

        # Create a fake binary file (not valid UTF-8)
        binary_path = os.path.join(repo, "image.bin")
        with open(binary_path, "wb") as f:
            f.write(bytes(range(256)) * 10)

        pkg = build_package(repo, None)

        out_dir = os.path.join(tmpdir, "restored")
        os.makedirs(out_dir)
        unpack_package(pkg, out_dir)

        ok, errors = verify_reconstructed_repo(pkg, out_dir)
        assert ok, f"Binary verification failed: {errors}"
    finally:
        shutil.rmtree(tmpdir)


def test_path_traversal_rejected():
    """Test that path traversal in manifest is rejected."""
    from pixelferry.manifest import parse_manifest_entries

    # Craft a malicious manifest entry
    entry = {"kind": "file", "path": "../../etc/passwd", "type": "text",
             "encoding": "utf-8", "mode": "0644", "length": 0, "sha256": "abc"}
    content = json.dumps(entry).encode() + b"\n\nPXFERRY_FILE_END\n"

    # The parser doesn't enforce path safety; the unpacker does.
    # This tests that safe_relpath catches it.
    from pixelferry.utils import safe_relpath
    try:
        safe_relpath("../../etc/passwd")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
