"""Debug: find exact pixel offset of content in PrintWindow capture."""
import sys, os, time, ctypes, ctypes.wintypes, io, tkinter as tk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pixelferry.constants import COLOR_LEVELS, BLOCK_SIZE, DATA_X_OFFSET, DATA_Y_OFFSET, WINDOW_WIDTH, WINDOW_HEIGHT

# Build a simple test: all blocks are color level 8 (dark gray)
img = Image.new("RGB", (WINDOW_WIDTH, WINDOW_HEIGHT), (0, 0, 0))
pixels = img.load()
# Fill data area with known color (level 8 = value 136)
for y in range(DATA_Y_OFFSET, WINDOW_HEIGHT - DATA_Y_OFFSET):
    for x in range(DATA_X_OFFSET, WINDOW_WIDTH - DATA_X_OFFSET):
        pixels[x, y] = (136, 136, 136)
# Mark corners with distinct colors
pixels[0, 0] = (255, 255, 255)  # white TL
pixels[WINDOW_WIDTH-1, 0] = (255, 0, 0)  # red TR
pixels[0, WINDOW_HEIGHT-1] = (0, 255, 0)  # green BL
pixels[WINDOW_WIDTH-1, WINDOW_HEIGHT-1] = (0, 0, 255)  # blue BR

img.save("_debug_offset_input.png")

# Show in tkinter
root = tk.Tk()
root.title("Offset-Test")
root.overrideredirect(True)
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

# Capture
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

rect = ctypes.wintypes.RECT()
user32.GetClientRect(hwnd, ctypes.byref(rect))
w = rect.right - rect.left
h = rect.bottom - rect.top

import win32gui
cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
lt = win32gui.ClientToScreen(hwnd, (cl, ct))
rb = win32gui.ClientToScreen(hwnd, (cr, cb))
print(f"GetClientRect: {w}x{h}")
print(f"ClientToScreen LT: {lt}, RB: {rb}")

hdc_window = user32.GetDC(hwnd)
hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
gdi32.SelectObject(hdc_mem, hbmp)

result = user32.PrintWindow(hwnd, hdc_mem, 0x00000002)
print(f"PrintWindow: {result}")

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD), ("biWidth", ctypes.wintypes.LONG),
        ("biHeight", ctypes.wintypes.LONG), ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD), ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD), ("biXPelsPerMeter", ctypes.wintypes.LONG),
        ("biYPelsPerMeter", ctypes.wintypes.LONG), ("biClrUsed", ctypes.wintypes.DWORD),
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
captured.save("_debug_offset_captured.png")

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(hwnd, hdc_window)

# Scan for the data area start
pixels = captured.load()
print(f"\nCaptured size: {captured.size}")

# Find where the 136,136,136 region starts
print("\nScanning for data area (looking for RGB=136):")
found_y = None
found_x = None
for y in range(h):
    for x in range(w):
        r, g, b = pixels[x, y]
        if r == 136 and g == 136 and b == 136:
            if found_y is None:
                found_y = y
                found_x = x
                print(f"  First 136 pixel at ({x}, {y})")
                break
    if found_y is not None:
        break

# Scan first row of pixels
print("\nFirst 5 rows, every 8th pixel:")
for y in range(min(50, h)):
    if y % 5 == 0:
        vals = []
        for x in range(0, min(100, w), 8):
            r, g, b = pixels[x, y]
            vals.append(f"({r},{g},{b})")
        print(f"  y={y}: {' '.join(vals)}")

root.destroy()
