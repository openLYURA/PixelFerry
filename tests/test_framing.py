"""Tests for frame header construction and validation."""

import secrets
from pixelferry.framing import (
    split_into_chunks, build_frame_header, parse_frame_header, validate_frame,
)
from pixelferry.utils import sha256_bytes


def test_split_into_chunks():
    """Test data splitting into chunks."""
    data = b"x" * 100
    chunks = split_into_chunks(data, chunk_size=30)
    assert len(chunks) == 4
    assert chunks[0] == b"x" * 30
    assert chunks[3] == b"x" * 10
    assert b"".join(chunks) == data


def test_frame_header_roundtrip():
    """Test header build → parse roundtrip."""
    session_id = secrets.token_bytes(16)
    payload = b"hello world"
    pkg_hash = sha256_bytes(b"package_content")

    header_bytes = build_frame_header(
        session_id=session_id,
        frame_index=42,
        total_frames=100,
        payload=payload,
        package_sha256=pkg_hash,
    )

    assert len(header_bytes) == 128

    header = parse_frame_header(header_bytes)
    assert header is not None
    assert header.magic == b"PXF1"
    assert header.version == 1
    assert header.session_id == session_id
    assert header.frame_index == 42
    assert header.total_frames == 100
    assert header.payload_len == len(payload)
    assert header.package_sha256 == pkg_hash


def test_validate_frame_valid():
    """Test that valid frame passes validation."""
    payload = b"test payload data"
    pkg_hash = sha256_bytes(b"pkg")
    session_id = secrets.token_bytes(16)

    header_bytes = build_frame_header(
        session_id=session_id,
        frame_index=0,
        total_frames=1,
        payload=payload,
        package_sha256=pkg_hash,
    )

    header = parse_frame_header(header_bytes)
    assert validate_frame(header, payload)


def test_validate_frame_corrupt():
    """Test that corrupted payload fails validation."""
    payload = b"original payload"
    corrupted = b"corrupted payload"
    pkg_hash = sha256_bytes(b"pkg")
    session_id = secrets.token_bytes(16)

    header_bytes = build_frame_header(
        session_id=session_id,
        frame_index=0,
        total_frames=1,
        payload=payload,
        package_sha256=pkg_hash,
    )

    header = parse_frame_header(header_bytes)
    assert not validate_frame(header, corrupted)
