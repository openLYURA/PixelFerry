"""Debug: capture a tkinter frame window with PrintWindow, save the image, check pixels."""
import sys, os, time, ctypes, ctypes.wintypes, subprocess, threading
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pixelferry.constants import COLOR_LEVELS, BLOCK_SIZE, DATA_X_OFFSET, DATA_Y_OFFSET, WINDOW_WIDTH, WINDOW_HEIGHT
from pixelferry.codec import encode_frame_to_image, decode_image_to_frame, set_gamma_lut, bytes_to_nibbles

# Build a simple frame with known content
from pixelferry.framing import build_frame_header
from pixelferry.utils import sha256_bytes
import secrets

# Create a simple payload
payload = b"Hello PixelFerry! " * 100  # 1800 bytes
session_id = secrets.token_bytes(16)
header = build_frame_header(
    session_id=session_id, frame_index=0, total_frames=1,
    payload=payload, package_sha256=sha256_bytes(b"test"),
)
frame_bytes = header + payload
img = encode_frame_to_image(frame_bytes)
img.save("_debug_frame.png")
print(f"Frame image: {img.size}")

# Show the frame in a tkinter window
import tkinter as tk
import io

root = tk.Tk()
root.title("Debug-PixelFerry")
root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+300+300")
root.resizable(False, False)

buf = io.BytesIO()
img.save(buf, format="PPM")
photo = tk.PhotoImage(data=buf.getvalue())

canvas = tk.Canvas(root, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, highlightthickness=0)
canvas.pack()
canvas.create_image(0, 0, anchor=tk.NW, image=photo)

for _ in range(10):
    root.update()
    root.update_idletasks()
time.sleep(0.5)

hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
if not hwnd:
    hwnd = root.winfo_id()
print(f"hwnd: {hwnd}")

# Capture with PrintWindow
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

rect = ctypes.wintypes.RECT()
user32.GetClientRect(hwnd, ctypes.byref(rect))
w = rect.right - rect.left
h = rect.bottom - rect.top
print(f"Client area: {w}x{h}")

hdc_window = user32.GetDC(hwnd)
hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
gdi32.SelectObject(hdc_mem, hbmp)

result = user32.PrintWindow(hwnd, hdc_mem, 0x00000002)
print(f"PrintWindow result: {result}")

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
bmi.biHeight = -h
bmi.biPlanes = 1
bmi.biBitCount = 32
bmi.biCompression = 0

buf = ctypes.create_string_buffer(w * h * 4)
gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

captured = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
captured.save("_debug_captured.png")
print(f"Captured: {captured.size}")

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(hwnd, hdc_window)

# Check some pixel values
pixels = captured.load()
print("\nSample pixels from captured image:")
# Check the first data block area (after 16px border)
for row in range(min(5, WINDOW_HEIGHT)):
    for col in range(min(10, WINDOW_WIDTH)):
        x = DATA_X_OFFSET + col * BLOCK_SIZE + BLOCK_SIZE // 2
        y = DATA_Y_OFFSET + row * BLOCK_SIZE + BLOCK_SIZE // 2
        if x < w and y < h:
            r, g, b = pixels[x, y]
            if (r, g, b) != (0, 0, 0):  # Skip black
                print(f"  Block ({col},{row}) at ({x},{y}): RGB=({r},{g},{b})")

# Try to decode
print("\nDecoding with gamma LUT...")
GAMMA_LUT = [
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
    2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
    4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
    5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
    8, 8, 8, 8, 8, 8, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9,
    9, 9, 9, 9, 9, 9, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10,
    10, 10, 10, 10, 1, 1, 1, 1, 1, 1, 1, 1, 11, 11, 11, 11,
    11, 11, 11, 11, 11, 11, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14,
    14, 14, 14, 14, 14, 14, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15,
    15, 15, 15, 15, 15, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
]
set_gamma_lut(GAMMA_LUT)

result = decode_image_to_frame(captured)
if result is not None:
    header, payload_out, valid = result
    print(f"  Decoded! valid={valid}, frame_index={header.frame_index}")
    print(f"  Payload match: {payload_out == payload}")
else:
    print("  Failed to decode!")

# Also try without gamma LUT
set_gamma_lut(None)
result2 = decode_image_to_frame(captured)
if result2 is not None:
    header2, payload2, valid2 = result2
    print(f"\nWithout gamma LUT: valid={valid2}")
else:
    print("\nWithout gamma LUT: Failed to decode")

root.destroy()
