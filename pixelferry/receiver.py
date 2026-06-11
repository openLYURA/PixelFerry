"""Receiver: decode frames from images (PNGs or screenshots).

This module orchestrates the receiving pipeline. Lower-level concerns
are split into dedicated modules:
  - corner_detect: corner marker detection and perspective transform
  - qr_detect: QR code detection and session initialization
  - capture: screen capture and sender window location
"""

import os
import sys
import time

from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field

from PIL import Image

from .codec import decode_image_to_frame
from .framing import FrameHeader
from .package import unpack_package
from .utils import sha256_hex
from .constants import WINDOW_WIDTH, WINDOW_HEIGHT

# Re-export public functions from submodules for backward compatibility
from .capture import (
    _capture_cache,
    _capture_screen,
    capture_screen_region,
    _find_obs_or_rdp_hwnd,
    _capture_window_full,
    _find_sender_in_capture,
)
from .corner_detect import _find_sender_by_corners
from .qr_detect import detect_qr_code, wait_for_qr


@dataclass
class ReceiveState:
    """Tracks received frames for a single session."""

    session_id: bytes
    total_frames: int
    package_sha256: bytes
    repo_name: str = ""
    frames: Dict[int, bytes] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return len(self.frames) >= self.total_frames

    @property
    def received_count(self) -> int:
        return len(self.frames)

    @property
    def missing_indices(self):
        return [i for i in range(self.total_frames) if i not in self.frames]

    @property
    def progress(self) -> float:
        if self.total_frames == 0:
            return 0.0
        return self.received_count / self.total_frames


# ── PNG-based decoding ────────────────────────────────────────────


def decode_from_pngs(frames_dir: str) -> Tuple[Optional[bytes], Optional[ReceiveState]]:
    """Decode all PNGs in a directory and merge into a single package.

    Returns (package_bytes, state).
    """
    state = None
    png_files = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))

    for fname in png_files:
        img = Image.open(os.path.join(frames_dir, fname))
        result = decode_single_frame(img)
        if result is None:
            continue

        header, payload, valid = result

        if not valid:
            print(f"  Skip {fname}: checksum mismatch or unreliable blocks")
            continue

        if state is None:
            state = ReceiveState(
                session_id=header.session_id,
                total_frames=header.total_frames,
                package_sha256=header.package_sha256,
                repo_name=header.repo_name.decode("utf-8", errors="replace"),
            )

        if header.session_id != state.session_id:
            continue

        if header.frame_index not in state.frames:
            state.frames[header.frame_index] = payload
            print(f"  Frame {header.frame_index}/{state.total_frames} OK")

    if state is None or not state.is_complete:
        return None, state

    pkg, ok = _merge_and_verify(state)
    if not ok:
        return None, state
    return pkg, state


# ── Progress display ──────────────────────────────────────────────


def _print_progress_bar(received, total, repo_name=""):
    if total == 0:
        return
    pct = received / total
    bar_length = 30
    filled_length = int(round(bar_length * pct))
    bar = "\u2588" * filled_length + "\u2591" * (bar_length - filled_length)
    sys.stdout.write(
        f"\rProgress: [{bar}] {pct * 100:.0f}% ({received}/{total} frames) | Repo: {repo_name}"
    )
    sys.stdout.flush()


# ── Real-time receive loop ────────────────────────────────────────


def receive_from_screen(
    region: Tuple[int, int, int, int],
    output_path: str = None,
    output_dir: str = None,
    fps: float = 8.0,
    max_cycles: int = 50,
    verbose: bool = True,
    qr_data: dict = None,
) -> Optional[bytes]:
    """Capture screen region in a loop, decode frames, and reconstruct package.

    Args:
        region: (left, top, width, height) screen region to capture.
        output_path: If set, save reconstructed package.pxf here.
        output_dir: If set, unpack restored repo here.
        fps: Capture rate (should be >= sender fps * 2 for reliability).
        max_cycles: Max number of full cycles to wait before giving up.
        verbose: Print progress.
        qr_data: Pre-detected QR code data from wait_for_qr().

    Returns:
        Reconstructed package bytes, or None on failure.
    """
    state = None
    cycle_count = 0
    last_frame_count = 0
    stall_count = 0
    interval = 1.0 / fps
    consecutive_locate_failures = 0

    # Initialize state from QR data if available
    if qr_data:
        try:
            session_id = bytes.fromhex(qr_data["s"])
            total_frames = qr_data["f"]
            package_sha256 = bytes.fromhex(qr_data["h"]) if "h" in qr_data else b""
            repo_name = qr_data.get("r", "")
            state = ReceiveState(
                session_id=session_id,
                total_frames=total_frames,
                package_sha256=package_sha256,
                repo_name=repo_name,
            )
            if verbose:
                print(f"Session detected (QR): {total_frames} frames")
                _print_progress_bar(0, total_frames, repo_name)
        except Exception:
            pass

    if verbose:
        print(f"Listening on region {region} at {fps} FPS...")
        print("Press Ctrl+C to stop.\n")

    try:
        while cycle_count < max_cycles:
            t0 = time.monotonic()

            img = capture_screen_region(region)
            result = decode_single_frame(img)

            if result is not None:
                header, payload, valid = result

                if valid:
                    consecutive_locate_failures = 0

                    if state is None:
                        state = ReceiveState(
                            session_id=header.session_id,
                            total_frames=header.total_frames,
                            package_sha256=header.package_sha256,
                            repo_name=header.repo_name.decode("utf-8", errors="replace"),
                        )
                        if verbose:
                            print(
                                f"Session detected: repo '{state.repo_name}', "
                                f"{header.total_frames} frames"
                            )
                            _print_progress_bar(0, header.total_frames, state.repo_name)

                    if header.session_id == state.session_id:
                        # Fill in missing metadata from frame header
                        if not state.package_sha256:
                            state.package_sha256 = header.package_sha256
                        if not state.repo_name and header.repo_name:
                            state.repo_name = header.repo_name.decode("utf-8", errors="replace")

                        if header.frame_index not in state.frames:
                            state.frames[header.frame_index] = payload
                            if verbose:
                                _print_progress_bar(
                                    state.received_count, state.total_frames, state.repo_name
                                )

                        if state.is_complete:
                            pkg, ok = _merge_and_verify(state)
                            if ok:
                                if verbose:
                                    print()
                                actual_output_dir = output_dir
                                if output_dir and state.repo_name:
                                    actual_output_dir = os.path.join(output_dir, state.repo_name)

                                if output_path:
                                    with open(output_path, "wb") as f:
                                        f.write(pkg)
                                    if verbose:
                                        print(f"\nPackage saved to {output_path}")

                                if actual_output_dir:
                                    unpack_to_directory(pkg, actual_output_dir, overwrite=True)
                                    if verbose:
                                        print(f"Repository restored to {actual_output_dir}")

                                return pkg
                            else:
                                if verbose:
                                    print("Package verification failed, continuing...")
                                state = None
                else:
                    consecutive_locate_failures += 1
            else:
                consecutive_locate_failures += 1

            # Reset capture cache after persistent failures to re-locate sender
            if consecutive_locate_failures >= 60:
                if _capture_cache["sender_box"] is not None:
                    _capture_cache["sender_box"] = None
                consecutive_locate_failures = 0

            # Detect cycle completion (frame count stalled = sender looped)
            if state and state.received_count == last_frame_count:
                stall_count += 1
                if stall_count >= int(fps * 1.5):  # ~1.5 seconds of no new frames
                    cycle_count += 1
                    if verbose:
                        missing = state.missing_indices
                        print(
                            f"  Cycle {cycle_count} complete, "
                            f"{state.received_count}/{state.total_frames} frames"
                            f"{f' (missing {len(missing)})' if missing else ''}"
                        )
                    stall_count = 0
                    if state.is_complete:
                        break
            else:
                stall_count = 0
            last_frame_count = state.received_count if state else 0

            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        if verbose:
            print("\nStopped by user.")

    if state and verbose:
        print(f"\nReceived {state.received_count}/{state.total_frames} frames")
        missing = state.missing_indices
        if missing:
            print(f"Missing: {missing[:20]}{'...' if len(missing) > 20 else ''}")

    return None


# ── Helpers ───────────────────────────────────────────────────────


def decode_single_frame(img: Image.Image) -> Optional[Tuple[FrameHeader, bytes, bool]]:
    """Decode a single frame image."""
    return decode_image_to_frame(img)


def _merge_and_verify(state: ReceiveState) -> Tuple[Optional[bytes], bool]:
    """Merge received frames and verify package integrity."""
    package_bytes = b"".join(state.frames[i] for i in range(state.total_frames))

    if sha256_hex(package_bytes) != state.package_sha256.hex():
        return None, False

    return package_bytes, True


def unpack_to_directory(
    package_bytes: bytes,
    output_dir: str,
    overwrite: bool = False,
):
    """Unpack a verified package into a directory."""
    written = unpack_package(package_bytes, output_dir, overwrite=overwrite)
    print(f"Unpacked {len(written)} files to {output_dir}")
    return written
