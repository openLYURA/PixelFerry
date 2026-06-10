"""Full pipeline roundtrip test: repo → package → frames → decode → unpack."""

import os
import tempfile
import shutil
from pixelferry.package import build_package, unpack_package, get_package_sha256
from pixelferry.framing import split_into_chunks, build_frame_header
from pixelferry.codec import encode_frame_to_image, decode_image_to_frame
from pixelferry.receiver import decode_from_pngs
from pixelferry.sender import save_frame_pngs
from pixelferry.verify import verify_reconstructed_repo
from pixelferry.utils import sha256_hex


def _make_repo(base):
    """Create a diverse test repository."""
    os.makedirs(os.path.join(base, "src"), exist_ok=True)
    os.makedirs(os.path.join(base, "lib"), exist_ok=True)

    with open(os.path.join(base, "README.md"), "w") as f:
        f.write("# PixelFerry Test\n\nThis is a test repository.\n")

    with open(os.path.join(base, "src", "main.py"), "w") as f:
        f.write("import sys\n\ndef main():\n    print(sys.version)\n\nif __name__ == '__main__':\n    main()\n")

    with open(os.path.join(base, "src", "utils.py"), "w") as f:
        f.write("# Utility functions\n\ndef add(a, b):\n    return a + b\n")

    with open(os.path.join(base, "lib", "data.json"), "w") as f:
        f.write('{"key": "value", "count": 42}\n')

    # File with spaces in name
    with open(os.path.join(base, "my file.txt"), "w") as f:
        f.write("File with space in name\n")

    # File with newlines preservation
    with open(os.path.join(base, "src", "newlines.txt"), "w", newline="") as f:
        f.write("line1\nline2\r\nline3\n")


def test_full_roundtrip():
    """Full pipeline: repo → package → frames → decode → unpack → verify."""
    tmpdir = tempfile.mkdtemp()
    try:
        repo = os.path.join(tmpdir, "repo")
        frames_dir = os.path.join(tmpdir, "frames")
        restored = os.path.join(tmpdir, "restored")

        _make_repo(repo)

        # Step 1: Build package
        pkg = build_package(repo, None)
        pkg_sha = sha256_hex(pkg)
        print(f"Package size: {len(pkg)} bytes")

        # Step 2: Split and encode frames
        chunks = split_into_chunks(pkg)
        print(f"Total frames: {len(chunks)}")

        # Step 3: Save as PNGs
        save_frame_pngs(repo, frames_dir)

        # Step 4: Decode from PNGs
        os.makedirs(restored, exist_ok=True)
        decoded_pkg, state = decode_from_pngs(frames_dir)

        assert decoded_pkg is not None, "Failed to decode package"
        assert state.is_complete, f"Incomplete: {state.received_count}/{state.total_frames}"
        assert sha256_hex(decoded_pkg) == pkg_sha, "Package SHA-256 mismatch"

        # Step 5: Unpack
        written = unpack_package(decoded_pkg, restored)
        print(f"Unpacked {len(written)} files")

        # Step 6: Verify
        ok, errors = verify_reconstructed_repo(decoded_pkg, restored)
        assert ok, f"Verification failed: {errors}"

        # Step 7: Check file content
        with open(os.path.join(restored, "src", "main.py")) as f:
            content = f.read()
        assert "def main():" in content
        assert "print(sys.version)" in content

        print("Full roundtrip test PASSED!")

    finally:
        shutil.rmtree(tmpdir)


def test_missing_frames_recovered():
    """Test that missing frames are handled (not a full recovery, just graceful)."""
    tmpdir = tempfile.mkdtemp()
    try:
        repo = os.path.join(tmpdir, "repo")
        frames_dir = os.path.join(tmpdir, "frames")

        _make_repo(repo)
        save_frame_pngs(repo, frames_dir)

        # Delete the only frame (small repo = 1 frame)
        files = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
        assert len(files) >= 1, "Need at least 1 frame"

        # Delete all frames
        for f in files:
            os.remove(os.path.join(frames_dir, f))

        # Should report incomplete (no valid frames)
        decoded_pkg, state = decode_from_pngs(frames_dir)
        assert decoded_pkg is None
        # state is None when no frames decoded at all
        assert state is None

    finally:
        shutil.rmtree(tmpdir)
