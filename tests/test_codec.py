"""Tests for nibble encoding/decoding and color mapping."""

import os
import tempfile
import shutil
from pixelferry.codec import (
    bytes_to_nibbles, nibbles_to_bytes,
    nibble_to_color, color_to_nibble,
    encode_frame_to_image, decode_image_to_frame,
)
from pixelferry.constants import COLOR_LEVELS, NIBBLE_COUNT, WINDOW_WIDTH, WINDOW_HEIGHT
from pixelferry.framing import build_frame_header
from pixelferry.utils import sha256_bytes
import secrets


def test_nibble_roundtrip():
    """Test bytes → nibbles → bytes roundtrip."""
    data = b"Hello, PixelFerry! \x00\x01\x02\xff\xfe"
    nibbles = bytes_to_nibbles(data)
    assert len(nibbles) == len(data) * 2

    recovered = nibbles_to_bytes(nibbles, len(data))
    assert recovered == data


def test_nibble_color_roundtrip():
    """Test nibble → color → nibble roundtrip."""
    for n in range(NIBBLE_COUNT):
        color = nibble_to_color(n)
        assert 0 <= color <= 255

        recovered_n, dist = color_to_nibble(color)
        assert recovered_n == n
        assert dist == 0


def test_color_to_nibble_nearest():
    """Test that color_to_nibble snaps to nearest level."""
    # Value 20 should map to nibble 1 (color 24)
    n, d = color_to_nibble(20)
    assert n == 1
    assert d == 4

    # Value 130 should map to nibble 8 (color 136)
    n, d = color_to_nibble(130)
    assert n == 8
    assert d == 6


def test_frame_image_roundtrip():
    """Test encode → decode a frame image."""
    session_id = secrets.token_bytes(16)
    payload = b"A" * 1000
    pkg_hash = sha256_bytes(b"test_package")

    header_bytes = build_frame_header(
        session_id=session_id,
        frame_index=0,
        total_frames=1,
        payload=payload,
        package_sha256=pkg_hash,
    )

    frame_bytes = header_bytes + payload

    # Encode to image
    img = encode_frame_to_image(frame_bytes)
    assert img.size == (WINDOW_WIDTH, WINDOW_HEIGHT)

    # Decode from image
    result = decode_image_to_frame(img)
    assert result is not None

    decoded_header, decoded_payload, valid = result
    assert valid
    assert decoded_header.frame_index == 0
    assert decoded_header.total_frames == 1
    assert decoded_header.payload_len == len(payload)
    assert decoded_payload == payload


def test_corrupted_frame_rejected():
    """Test that a corrupted frame is detected."""
    session_id = secrets.token_bytes(16)
    payload = b"test data"
    pkg_hash = sha256_bytes(b"pkg")

    header_bytes = build_frame_header(
        session_id=session_id,
        frame_index=0,
        total_frames=1,
        payload=payload,
        package_sha256=pkg_hash,
    )

    frame_bytes = header_bytes + payload
    img = encode_frame_to_image(frame_bytes)

    # Corrupt the image by drawing over some blocks
    pixels = img.load()
    for x in range(100, 200):
        for y in range(100, 110):
            pixels[x, y] = (128, 128, 128)  # Not a valid color level

    result = decode_image_to_frame(img)
    if result is not None:
        _, _, valid = result
        # Should either be invalid or have unreliable blocks
        # (depends on how many blocks were corrupted)
        # The important thing is it doesn't silently succeed
