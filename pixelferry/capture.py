"""Screen capture and sender window location.

Provides platform-specific screen capture (Windows DPI-aware ImageGrab, mss fallback)
and sender window detection via corner markers or start-marker pixel scanning.
"""

import sys
from typing import Optional, Tuple

from PIL import Image

from .constants import (
    WINDOW_WIDTH, WINDOW_HEIGHT,
    DATA_X_OFFSET, DATA_Y_OFFSET, BLOCK_SIZE,
    COLOR_LEVELS, START_MARKER_NIBBLES,
)


# Initialize Windows DPI awareness
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# Cache for sender window bounding box (avoids repeated detection)
_capture_cache = {"hwnd": None, "sender_box": None}


def _capture_screen(region: Tuple[int, int, int, int]) -> Image.Image:
    """Capture a screen region. Uses Pillow ImageGrab on Windows, falls back to mss."""
    left, top, width, height = region

    # Windows: DPI-aware ImageGrab with physical-pixel scaling
    if sys.platform == "win32":
        try:
            import ctypes
            from PIL import ImageGrab

            user32 = ctypes.windll.user32
            x_min = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            y_min = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            virtual_w = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            virtual_h = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

            full_img = ImageGrab.grab(all_screens=True)
            phys_w, phys_h = full_img.width, full_img.height

            scale_x = phys_w / virtual_w if virtual_w > 0 else 1.0
            scale_y = phys_h / virtual_h if virtual_h > 0 else 1.0

            rel_left = int(round((left - x_min) * scale_x))
            rel_top = int(round((top - y_min) * scale_y))
            rel_right = int(round((left + width - x_min) * scale_x))
            rel_bottom = int(round((top + height - y_min) * scale_y))

            rel_left = max(0, min(rel_left, phys_w))
            rel_top = max(0, min(rel_top, phys_h))
            rel_right = max(rel_left, min(rel_right, phys_w))
            rel_bottom = max(rel_top, min(rel_bottom, phys_h))

            return full_img.crop((rel_left, rel_top, rel_right, rel_bottom))
        except Exception:
            pass

    # Fallback: mss
    try:
        import mss

        monitor = {"left": left, "top": top, "width": width, "height": height}
        with mss.MSS() as sct:
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        return Image.new("RGB", (width, height), (0, 0, 0))


def capture_screen_region(region: Tuple[int, int, int, int]) -> Image.Image:
    """Capture screen region and locate sender window within it.

    Uses cached bounding box, then corner-marker detection, then start-marker
    pixel scanning as fallbacks.
    """
    from .corner_detect import _find_sender_by_corners

    img = _capture_screen(region)

    # 1. Cached bounding box
    sender_box = _capture_cache["sender_box"]
    if sender_box:
        x1, y1, x2, y2 = sender_box
        rel_x1 = x1 - region[0]
        rel_y1 = y1 - region[1]
        rel_x2 = x2 - region[0]
        rel_y2 = y2 - region[1]
        if rel_x1 >= 0 and rel_y1 >= 0 and rel_x2 <= img.width and rel_y2 <= img.height:
            crop = img.crop((rel_x1, rel_y1, rel_x2, rel_y2))
            return crop.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST)

    # 2. Corner-marker perspective correction
    sender, box = _find_sender_by_corners(img, region_offset=(region[0], region[1]))
    if box:
        _capture_cache["sender_box"] = box
        return sender

    # 3. Start-marker pixel scanning fallback
    sender, box = _find_sender_in_capture(img, region_offset=(region[0], region[1]))
    if box:
        _capture_cache["sender_box"] = box
    return sender


def _find_obs_or_rdp_hwnd():
    """Find OBS Studio or remote desktop window handle."""
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        found = []

        def enum_cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()
                    if any(kw in title for kw in ["obs", "obs64", "obs studio"]):
                        found.insert(0, hwnd)
                        return False
                    if any(kw in title for kw in ["mstsc", "remote desktop", "rdp", "teamviewer", "anydesk"]):
                        found.append(hwnd)
            return True

        cb = WNDENUMPROC(enum_cb)
        user32.EnumWindows(cb, 0)
        return found[0] if found else None
    except Exception:
        return None


def _capture_window_full(hwnd) -> Image.Image:
    """Capture entire window using PrintWindow API."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top

    hdc_window = user32.GetDC(hwnd)
    hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_window)
    hbitmap = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_window, win_w, win_h)
    ctypes.windll.gdi32.SelectObject(hdc_mem, hbitmap)

    ctypes.windll.user32.PrintWindow(hwnd, hdc_mem, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = win_w
    bmi.biHeight = -win_h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf = ctypes.create_string_buffer(win_w * win_h * 4)
    ctypes.windll.gdi32.GetDIBits(hdc_mem, hbitmap, 0, win_h, buf, ctypes.byref(bmi), 0)

    ctypes.windll.gdi32.DeleteObject(hbitmap)
    ctypes.windll.gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    return Image.frombuffer("RGB", (win_w, win_h), buf, "raw", "BGRX")


def _find_sender_in_capture(
    img: Image.Image,
    region_offset: Tuple[int, int] = (0, 0),
) -> Tuple[Image.Image, Optional[Tuple[int, int, int, int]]]:
    """Find the PixelFerry sender window by locating the start marker color.

    Returns (resized_image, bounding_box_in_absolute_coords_or_None).
    """
    w, h = img.size
    ox, oy = region_offset

    # Start marker: first block color = COLOR_LEVELS[15], COLOR_LEVELS[0], COLOR_LEVELS[3]
    target = (
        COLOR_LEVELS[START_MARKER_NIBBLES[0]],
        COLOR_LEVELS[START_MARKER_NIBBLES[1]],
        COLOR_LEVELS[START_MARKER_NIBBLES[2]],
    )

    marker_x, marker_y = None, None
    for y in range(h):
        for x in range(w):
            r, g, b = img.getpixel((x, y))
            if r == target[0] and g == target[1] and b == target[2]:
                marker_x, marker_y = x, y
                break
        if marker_x is not None:
            break

    if marker_x is None:
        cx, cy = w // 2, h // 2
        x1 = max(0, cx - WINDOW_WIDTH // 2)
        y1 = max(0, cy - WINDOW_HEIGHT // 2)
        crop = img.crop((x1, y1, x1 + WINDOW_WIDTH, y1 + WINDOW_HEIGHT))
        return crop.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST), None

    # Measure actual block width to estimate scale
    block_w = BLOCK_SIZE
    for x in range(marker_x + 1, min(w, marker_x + 30)):
        r, g, b = img.getpixel((x, marker_y))
        if r != target[0] or g != target[1] or b != target[2]:
            block_w = x - marker_x
            break

    scale = block_w / BLOCK_SIZE

    src_x = marker_x - int(DATA_X_OFFSET * scale)
    src_y = marker_y - int(DATA_Y_OFFSET * scale)
    src_w = int(WINDOW_WIDTH * scale)
    src_h = int(WINDOW_HEIGHT * scale)

    crop_x1 = max(0, src_x)
    crop_y1 = max(0, src_y)
    crop_x2 = min(w, src_x + src_w)
    crop_y2 = min(h, src_y + src_h)

    if crop_x2 - crop_x1 < 50 or crop_y2 - crop_y1 < 50:
        return img.crop((0, 0, min(WINDOW_WIDTH, w), min(WINDOW_HEIGHT, h))).resize(
            (WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST
        ), None

    cropped = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
    abs_box = (crop_x1 + ox, crop_y1 + oy, crop_x2 + ox, crop_y2 + oy)

    return cropped.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST), abs_box
