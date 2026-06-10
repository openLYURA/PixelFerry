"""RGB nibble encoding and decoding for frame images."""

from typing import List, Optional, Tuple
from dataclasses import dataclass

from PIL import Image

from .constants import (
    COLOR_LEVELS, NIBBLE_COUNT, MAX_COLOR_DISTANCE,
    BLOCK_SIZE, DATA_X_OFFSET, DATA_Y_OFFSET,
    DATA_WIDTH, DATA_HEIGHT, GRID_COLS, GRID_ROWS,
    WINDOW_WIDTH, WINDOW_HEIGHT,
    START_MARKER_NIBBLES, END_MARKER_NIBBLES,
    TOTAL_NIBBLES,
    CORNER_WHITE, CORNER_RED, CORNER_GREEN, CORNER_BLUE,
    MARKER_SIZE,
)
from .framing import FrameHeader, parse_frame_header, validate_frame


# ── Gamma correction LUT ─────────────────────────────────────────
# PrintWindow applies a nonlinear gamma curve to captured colors.
# This LUT maps captured RGB values (0-255) back to the original nibble (0-15).
# Built by tests/_calibrate_gamma.py — set via set_gamma_lut().
_GAMMA_LUT: list = None


def set_gamma_lut(lut: list):
    """Set the gamma correction LUT (256 entries, each 0-15).

    Pass None to disable gamma correction.
    """
    global _GAMMA_LUT
    _GAMMA_LUT = lut


def get_gamma_lut() -> list:
    """Get the current gamma correction LUT, or None if disabled."""
    return _GAMMA_LUT


# ── Nibble ↔ byte conversion ──────────────────────────────────────

def bytes_to_nibbles(data: bytes) -> List[int]:
    """Convert bytes to a flat list of 4-bit nibbles (high first)."""
    nibbles = []
    for byte in data:
        nibbles.append((byte >> 4) & 0x0F)
        nibbles.append(byte & 0x0F)
    return nibbles


def nibbles_to_bytes(nibbles: List[int], expected_len: int) -> bytes:
    """Convert a flat nibble list back to bytes."""
    result = bytearray()
    for i in range(0, len(nibbles), 2):
        hi = nibbles[i] if i < len(nibbles) else 0
        lo = nibbles[i + 1] if i + 1 < len(nibbles) else 0
        result.append((hi << 4) | lo)
    return bytes(result[:expected_len])


# ── Color ↔ nibble conversion ─────────────────────────────────────

def nibble_to_color(n: int) -> int:
    """Map a 4-bit nibble (0-15) to its RGB channel value."""
    if n < 0 or n >= NIBBLE_COUNT:
        raise ValueError(f"Nibble out of range: {n}")
    return COLOR_LEVELS[n]


def color_to_nibble(v: int) -> int:
    """Snap an RGB channel value (0-255) to the nearest nibble (0-15).

    Returns (nibble, distance). If distance > MAX_COLOR_DISTANCE,
    the block is unreliable.

    If a gamma correction LUT is set, uses it instead of nearest-match.
    """
    if _GAMMA_LUT is not None:
        nibble = _GAMMA_LUT[v & 0xFF]
        # Distance relative to the expected captured value for this nibble
        expected = COLOR_LEVELS[nibble]
        # The LUT already corrected for gamma, so distance is 0
        return nibble, 0

    best = 0
    best_dist = abs(v - COLOR_LEVELS[0])
    for i, level in enumerate(COLOR_LEVELS):
        d = abs(v - level)
        if d < best_dist:
            best_dist = d
            best = i
    return best, best_dist


# ── Frame → Image encoding ────────────────────────────────────────

def _draw_corner_markers(img: Image.Image):
    """Draw the four corner position markers."""
    pixels = img.load()
    w, h = img.size

    corners = [
        (0, 0, CORNER_WHITE),              # top-left: yellow (for preventing boundary sticking)
        (w - MARKER_SIZE, 0, CORNER_RED),    # top-right: red
        (0, h - MARKER_SIZE, CORNER_GREEN),  # bottom-left: green
        (w - MARKER_SIZE, h - MARKER_SIZE, CORNER_BLUE),  # bottom-right: blue
    ]

    for cx, cy, color in corners:
        for dy in range(MARKER_SIZE):
            for dx in range(MARKER_SIZE):
                px = cx + dx
                py = cy + dy
                if 0 <= px < w and 0 <= py < h:
                    pixels[px, py] = color


def encode_frame_to_image(frame_bytes: bytes) -> Image.Image:
    """Encode frame bytes into a 640×360 RGB image with colored blocks.

    frame_bytes contains header + payload. Markers are injected automatically.
    Nibble stream layout (fixed positions):
    [0..11]     start marker (12 nibbles)
    [12..267]   header (256 nibbles)
    [268..N-1]  payload nibbles
    [N..N+11]   end marker (12 nibbles)
    [N+12..36479] padding zeros
    """
    data_nibbles = bytes_to_nibbles(frame_bytes)
    end_marker_pos = len(START_MARKER_NIBBLES) + len(data_nibbles)

    nibbles = [0] * TOTAL_NIBBLES

    # Start marker at position 0
    for i, v in enumerate(START_MARKER_NIBBLES):
        nibbles[i] = v

    # Data (header + payload) starting at position 12
    for i, v in enumerate(data_nibbles):
        nibbles[len(START_MARKER_NIBBLES) + i] = v

    # End marker right after data
    for i, v in enumerate(END_MARKER_NIBBLES):
        nibbles[end_marker_pos + i] = v

    img = Image.new("RGB", (WINDOW_WIDTH, WINDOW_HEIGHT), (0, 0, 0))
    pixels = img.load()

    # Map nibble index to (col, row) in the grid
    nibble_idx = 0
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            if nibble_idx + 2 >= len(nibbles):
                break

            # 3 nibbles per block: R, G, B
            r_n = nibbles[nibble_idx]
            g_n = nibbles[nibble_idx + 1]
            b_n = nibbles[nibble_idx + 2]

            r_color = nibble_to_color(r_n)
            g_color = nibble_to_color(g_n)
            b_color = nibble_to_color(b_n)

            # Draw 4×4 block
            bx = DATA_X_OFFSET + col * BLOCK_SIZE
            by = DATA_Y_OFFSET + row * BLOCK_SIZE
            for dy in range(BLOCK_SIZE):
                for dx in range(BLOCK_SIZE):
                    pixels[bx + dx, by + dy] = (r_color, g_color, b_color)

            nibble_idx += 3

    _draw_corner_markers(img)
    return img


# ── Image → Frame decoding ────────────────────────────────────────

@dataclass
class DecodedBlock:
    r_nibble: int
    g_nibble: int
    b_nibble: int
    reliable: bool
    max_distance: int


def _read_block_center(img_or_arr, bx: int, by: int) -> Tuple[int, int, int]:
    """Compute median of block center region using Numpy vectorized operations."""
    import numpy as np
    if isinstance(img_or_arr, Image.Image):
        img_arr = np.array(img_or_arr)
    else:
        img_arr = img_or_arr

    half = BLOCK_SIZE // 2
    offset = (BLOCK_SIZE - half) // 2
    
    # Extract center sub-region [half, half, 3]
    sub = img_arr[by + offset : by + offset + half, bx + offset : bx + offset + half]
    if sub.size == 0:
        return 0, 0, 0

    # Compute median across spatial dimensions
    r_mid = int(np.median(sub[:, :, 0]))
    g_mid = int(np.median(sub[:, :, 1]))
    b_mid = int(np.median(sub[:, :, 2]))
    return r_mid, g_mid, b_mid



def decode_block(img_or_arr, col: int, row: int) -> DecodedBlock:
    """Decode a single data block at grid position (col, row)."""
    bx = DATA_X_OFFSET + col * BLOCK_SIZE
    by = DATA_Y_OFFSET + row * BLOCK_SIZE

    r_avg, g_avg, b_avg = _read_block_center(img_or_arr, bx, by)

    r_nib, r_dist = color_to_nibble(r_avg)
    g_nib, g_dist = color_to_nibble(g_avg)
    b_nib, b_dist = color_to_nibble(b_avg)

    max_dist = max(r_dist, g_dist, b_dist)

    return DecodedBlock(
        r_nibble=r_nib,
        g_nibble=g_nib,
        b_nibble=b_nib,
        reliable=(max_dist <= MAX_COLOR_DISTANCE),
        max_distance=max_dist,
    )


def decode_image_to_frame(img: Image.Image) -> Optional[Tuple[FrameHeader, bytes, bool]]:
    """Decode an image into (header, payload, is_valid).

    Returns None if the image cannot be decoded at all.
    The third element indicates whether all blocks were reliable.
    """
    if img.size != (WINDOW_WIDTH, WINDOW_HEIGHT):
        # Try to resize if needed
        img = img.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST)

    import numpy as np
    img_arr = np.array(img)

    nibbles = []
    all_reliable = True

    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            block = decode_block(img_arr, col, row)
            nibbles.append(block.r_nibble)
            nibbles.append(block.g_nibble)
            nibbles.append(block.b_nibble)
            if not block.reliable:
                all_reliable = False

    if len(nibbles) < TOTAL_NIBBLES:
        nibbles.extend([0] * (TOTAL_NIBBLES - len(nibbles)))

    # Extract start marker (first 12 nibbles)
    start_marker = nibbles[:12]
    # Extract header (next 256 nibbles after start marker)
    header_nibbles = nibbles[12:12 + 256]

    # Convert header nibbles to bytes (128 bytes from 256 nibbles)
    header_bytes = nibbles_to_bytes(header_nibbles, 128)
    header = parse_frame_header(header_bytes)
    if header is None:
        return None


    # Extract payload nibbles (right after header)
    payload_nibble_count = header.payload_len * 2
    payload_nibbles = nibbles[268:268 + payload_nibble_count]

    # Extract end marker (right after payload)
    end_marker_pos = 268 + payload_nibble_count
    end_marker = nibbles[end_marker_pos:end_marker_pos + 12]

    # Verify markers
    start_ok = start_marker == START_MARKER_NIBBLES
    end_ok = end_marker == END_MARKER_NIBBLES

    if not start_ok or not end_ok:
        return None


    # Convert payload nibbles to bytes
    payload = nibbles_to_bytes(payload_nibbles, header.payload_len)

    # Validate using strong hash checksum, not block reliability (RDP color shifts can cause false negatives)
    valid = validate_frame(header, payload)
    
    return header, payload, valid
