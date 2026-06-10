"""Calibrate PrintWindow gamma distortion.

Creates a test image with all 16 color levels, displays in tkinter,
captures with PrintWindow, and outputs the input→output mapping.

Usage:
    python _calibrate_gamma.py

Output: prints the gamma LUT as a Python dict.
"""

import sys
import io
import time
import ctypes
import ctypes.wintypes
import tkinter as tk
from PIL import Image

from pixelferry.constants import COLOR_LEVELS, BLOCK_SIZE, DATA_X_OFFSET, DATA_Y_OFFSET

# Build calibration image: 16 horizontal stripes, one per color level
STRIP_HEIGHT = 20
IMG_WIDTH = 400
IMG_HEIGHT = STRIP_HEIGHT * len(COLOR_LEVELS)


def build_calibration_image() -> Image.Image:
    """Create an image with 16 horizontal stripes of known colors."""
    img = Image.new("RGB", (IMG_WIDTH, IMG_HEIGHT), (0, 0, 0))
    pixels = img.load()

    for level_idx, color_val in enumerate(COLOR_LEVELS):
        y_start = level_idx * STRIP_HEIGHT
        y_end = y_start + STRIP_HEIGHT
        for y in range(y_start, y_end):
            for x in range(IMG_WIDTH):
                pixels[x, y] = (color_val, color_val, color_val)

    return img


def capture_window(hwnd: int) -> Image.Image:
    """Capture window content using PrintWindow."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    hdc_window = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)

    # PW_RENDERFULLCONTENT = 0x02
    result = user32.PrintWindow(hwnd, hdc_mem, 0x00000002)
    if not result:
        user32.PrintWindow(hwnd, hdc_mem, 0x01)

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
    bmi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    return img


def main():
    cal_img = build_calibration_image()

    # Save calibration image for reference
    cal_img.save("_calibration_input.png")
    print(f"Calibration image: {IMG_WIDTH}x{IMG_HEIGHT}")

    # Display in tkinter
    root = tk.Tk()
    root.title("GammaCalibration")
    root.overrideredirect(True)
    root.geometry(f"{IMG_WIDTH}x{IMG_HEIGHT}+100+100")
    root.resizable(False, False)

    buf = io.BytesIO()
    cal_img.save(buf, format="PPM")
    photo = tk.PhotoImage(data=buf.getvalue())

    canvas = tk.Canvas(root, width=IMG_WIDTH, height=IMG_HEIGHT, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, anchor=tk.NW, image=photo)

    # Let window render
    for _ in range(10):
        root.update()
        root.update_idletasks()

    time.sleep(0.5)

    # Get hwnd
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    if not hwnd:
        hwnd = root.winfo_id()
    print(f"Window hwnd: {hwnd}")

    # Capture with PrintWindow
    captured = capture_window(hwnd)
    captured.save("_calibration_captured.png")
    print(f"Captured: {captured.size}")

    # Analyze: for each stripe, compute average captured value
    cap_pixels = captured.load()
    cap_w, cap_h = captured.size

    print("\nGamma curve (input → captured):")
    print(f"{'Level':>5} {'Input':>6} {'Captured':>8} {'Diff':>5}")

    captured_values = []
    for level_idx, color_val in enumerate(COLOR_LEVELS):
        # Sample from the center of each stripe
        y_center = level_idx * STRIP_HEIGHT + STRIP_HEIGHT // 2
        x_center = cap_w // 2

        # Average a small region
        r_sum, g_sum, b_sum = 0, 0, 0
        count = 0
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                x, y = x_center + dx, y_center + dy
                if 0 <= x < cap_w and 0 <= y < cap_h:
                    r, g, b = cap_pixels[x, y]
                    r_sum += r
                    g_sum += g
                    b_sum += b
                    count += 1

        r_avg = r_sum // count
        g_avg = g_sum // count
        b_avg = b_sum // count
        captured_values.append((r_avg, g_avg, b_avg))

        diff = r_avg - color_val
        print(f"{level_idx:>5} {color_val:>6} {r_avg:>8} {diff:>+5}")

    # Build reverse LUT: for each captured value (0-255), find the closest original level
    # We only have 16 data points, so interpolate
    print("\n--- Gamma Correction LUT ---")
    print("GAMMA_LUT = {")

    lut = {}
    for captured_val in range(256):
        # Find which original level this captured value most likely came from
        best_nibble = 0
        best_dist = 999
        for level_idx, (r, g, b) in enumerate(captured_values):
            d = abs(captured_val - r)  # use R channel (all channels are equal for grayscale)
            if d < best_dist:
                best_dist = d
                best_nibble = level_idx
        lut[captured_val] = best_nibble

    # Print LUT compactly (16 values per line)
    for i in range(0, 256, 16):
        entries = [f"{lut[j]:>2}" for j in range(i, min(i + 16, 256))]
        print(f"    {i:>3}: [{', '.join(entries)}],  # {i}-{min(i+15, 255)}")

    print("}")

    # Also print the forward mapping for reference
    print("\n--- Captured values for each level ---")
    for idx, (r, g, b) in enumerate(captured_values):
        print(f"  Level {idx:>2} (input {COLOR_LEVELS[idx]:>3}): captured ({r}, {g}, {b})")

    root.destroy()


if __name__ == "__main__":
    main()
