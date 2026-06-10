"""Capture a window's content using PrintWindow API.

Usage:
    python _capture_window.py <hwnd> <output_png>

PrintWindow works correctly with DWM-composed windows (unlike mss/BitBlt).
"""

import sys
import ctypes
import ctypes.wintypes
import struct
from PIL import Image


def capture_window(hwnd: int) -> Image.Image:
    """Capture window content using PrintWindow."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # Get client rect
    rect = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    if w == 0 or h == 0:
        raise ValueError(f"Window has zero size: {w}x{h}")

    # Create DCs
    hdc_window = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)

    # PrintWindow: PW_RENDERFULLCONTENT = 0x00000002 (Windows 8.1+)
    result = user32.PrintWindow(hwnd, hdc_mem, 0x00000002)
    if not result:
        # Fallback: PW_CLIENTONLY = 0x01
        user32.PrintWindow(hwnd, hdc_mem, 0x01)

    # Read bitmap bits
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.wintypes.DWORD),
            ("biWidth", ctypes.wintypes.LONG),
            ("biHeight", ctypes.wintypes.LONG),
            ("biPlanes", ctypes.wintypes.WORD),
            ("biBitCount", ctypes.wintypes.WORD),
            ("biCompression", ctypes.wintypes.DWORD),
            ("biSizeImage", ctypes.wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.wintypes.LONG),
            ("biYPelsPerMeter", ctypes.wintypes.LONG),
            ("biClrUsed", ctypes.wintypes.DWORD),
            ("biClrImportant", ctypes.wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0  # BI_RGB

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    # Convert BGRA to RGB
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)

    # Cleanup
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    return img


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: _capture_window.py <hwnd> <output_png>")
        sys.exit(1)

    hwnd = int(sys.argv[1])
    output = sys.argv[2]

    img = capture_window(hwnd)
    img.save(output)
    print(f"Captured {img.size} to {output}")
