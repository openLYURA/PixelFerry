"""Frame header construction, parsing, and validation."""

import struct
from typing import List, Optional
from dataclasses import dataclass

from .constants import (
    MAGIC, VERSION, HEADER_SIZE, HEADER_STRUCT,
    PAYLOAD_SIZE, START_MARKER_NIBBLES, END_MARKER_NIBBLES,
)
from .utils import sha256_bytes


@dataclass
class FrameHeader:
    magic: bytes
    version: int
    header_len: int
    session_id: bytes
    frame_index: int
    total_frames: int
    payload_len: int
    payload_sha256: bytes
    package_sha256: bytes
    repo_name: bytes = b""

    def to_bytes(self) -> bytes:
        repo_bytes = self.repo_name[:24].ljust(24, b"\x00")
        return HEADER_STRUCT.pack(
            self.magic,
            self.version,
            self.header_len,
            self.session_id,
            self.frame_index,
            self.total_frames,
            self.payload_len,
            self.payload_sha256,
            self.package_sha256,
            repo_bytes,
            b"\x00" * 5,
        )


def split_into_chunks(data: bytes, chunk_size: int = PAYLOAD_SIZE) -> List[bytes]:
    """Split data into fixed-size chunks."""
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]


def build_frame_header(
    session_id: bytes,
    frame_index: int,
    total_frames: int,
    payload: bytes,
    package_sha256: bytes,
    repo_name: str = "",
) -> bytes:
    """Build a 128-byte frame header."""
    header = FrameHeader(
        magic=MAGIC,
        version=VERSION,
        header_len=HEADER_SIZE,
        session_id=session_id,
        frame_index=frame_index,
        total_frames=total_frames,
        payload_len=len(payload),
        payload_sha256=sha256_bytes(payload),
        package_sha256=package_sha256,
        repo_name=repo_name.encode("utf-8")[:24],
    )
    return header.to_bytes()


def parse_frame_header(header_bytes: bytes) -> Optional[FrameHeader]:
    """Parse a 128-byte header. Returns None on format error."""
    if len(header_bytes) != HEADER_SIZE:
        return None

    try:
        (magic, version, header_len, session_id,
         frame_index, total_frames, payload_len,
         payload_sha256, package_sha256, repo_name_bytes,
         _reserved) = HEADER_STRUCT.unpack(header_bytes)
    except struct.error:
        return None

    if magic != MAGIC or version != VERSION:
        return None

    repo_name = repo_name_bytes.rstrip(b"\x00")

    return FrameHeader(
        magic=magic,
        version=version,
        header_len=header_len,
        session_id=session_id,
        frame_index=frame_index,
        total_frames=total_frames,
        payload_len=payload_len,
        payload_sha256=payload_sha256,
        package_sha256=package_sha256,
        repo_name=repo_name,
    )


def validate_frame(header: FrameHeader, payload: bytes) -> bool:
    """Validate that payload matches its SHA-256 hash."""
    return sha256_bytes(payload) == header.payload_sha256
