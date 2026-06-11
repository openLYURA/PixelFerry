"""All configuration constants for PixelFerry."""

import struct

# ── Protocol ──────────────────────────────────────────────────────
MAGIC = b"PXF1"
VERSION = 1
PACKAGE_HEADER = b"PXFERRY_PACKAGE_V1\n"
FILE_END_MARKER = b"PXFERRY_FILE_END\n"

# 屏幕无水印黄金适配模式：
# "4K": 针对 4K 屏幕实测最大无水印遮挡数据区 (750x430 限值，精密整除换算后数据区 744x420, 窗口 792x468)
# "1080P": 针对 1080P 屏幕实测最大无水印遮挡数据区 (464x238 限值，精密整除换算后数据区 456x228, 窗口 504x276)
# "LARGE": 默认的 1200x800 大窗口规格
SCREEN_MODE = "LARGE"

if SCREEN_MODE == "4K":
    # 优化后：总窗口 744x420 (严格在 750x430 限制内)，避空 24px 角块，数据区 696x372 (12像素整除)
    WINDOW_WIDTH = 744
    WINDOW_HEIGHT = 420
    DATA_X_OFFSET = 24
    DATA_Y_OFFSET = 24
    DATA_WIDTH = 696
    DATA_HEIGHT = 372
    BLOCK_SIZE = 12
    MARKER_SIZE = 24
elif SCREEN_MODE == "1080P":
    # 优化后：总窗口 456x228 (严格在 464x238 限制内)，避空 24px 角块，数据区 408x180 (12像素整除)
    WINDOW_WIDTH = 456
    WINDOW_HEIGHT = 228
    DATA_X_OFFSET = 24
    DATA_Y_OFFSET = 24
    DATA_WIDTH = 408
    DATA_HEIGHT = 180
    BLOCK_SIZE = 12
    MARKER_SIZE = 24
else:
    # Large window mode: 1200x800, 24px blocks for better distortion resistance
    WINDOW_WIDTH = 1200
    WINDOW_HEIGHT = 800
    MARKER_SIZE = 48
    BLOCK_SIZE = 24
    DATA_X_OFFSET = 48
    DATA_Y_OFFSET = 52
    DATA_WIDTH = 1104
    DATA_HEIGHT = 696

# ── Block encoding ────────────────────────────────────────────────
GRID_COLS = DATA_WIDTH // BLOCK_SIZE   # 98
GRID_ROWS = DATA_HEIGHT // BLOCK_SIZE  # 64

# ── 16-level nibble encoding ──────────────────────────────────────
COLOR_LEVELS = [
    8, 24, 40, 56, 72, 88, 104, 120,
    136, 152, 168, 184, 200, 216, 232, 248,
]
NIBBLE_COUNT = len(COLOR_LEVELS)  # 16
MAX_COLOR_DISTANCE = 7

# ── Frame header (byte-level) ────────────────────────────────────
HEADER_SIZE = 128  # bytes
HEADER_FIELD_OFFSETS = {
    "magic":          (0,  4),
    "version":        (4,  5),
    "header_len":     (5,  7),
    "session_id":     (7,  23),
    "frame_index":    (23, 27),
    "total_frames":   (27, 31),
    "payload_len":    (31, 35),
    "payload_sha256": (35, 67),
    "package_sha256": (67, 99),
    "reserved":       (99, 128),
}
HEADER_STRUCT = struct.Struct(">4sBH16sIII32s32s24s5s")

# ── Markers ───────────────────────────────────────────────────────
START_MARKER_NIBBLES = [15, 0, 3, 12, 5, 10, 9, 6, 14, 1, 7, 8]
END_MARKER_NIBBLES   = [1, 14, 11, 4, 9, 6, 12, 3, 8, 7, 0, 15]

# ── Corner markers ───────────────────────────────────────────────
CORNER_YELLOW = (255, 255, 0)  # 黄色（防范与 20px 白色外框背景粘连）
CORNER_RED   = (255, 0,   0)
CORNER_GREEN = (0,   255, 0)
CORNER_BLUE  = (0,   0,   255)

# ── Capacity ──────────────────────────────────────────────────────
TOTAL_NIBBLES = GRID_COLS * GRID_ROWS * 3  # 18816
OVERHEAD_NIBBLES = len(START_MARKER_NIBBLES) + HEADER_SIZE * 2 + len(END_MARKER_NIBBLES)  # 280
PAYLOAD_NIBBLES = TOTAL_NIBBLES - OVERHEAD_NIBBLES  # 18536
PAYLOAD_SIZE = PAYLOAD_NIBBLES // 2  # 9268 bytes

# ── Playback ──────────────────────────────────────────────────────
DEFAULT_SEND_FPS = 2.0
DEFAULT_RECV_FPS = 10.0

# ── Packaging ─────────────────────────────────────────────────────
DEFAULT_EXCLUDES = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".cache", "target", "out", "coverage",
}
