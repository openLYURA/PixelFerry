"""Helper script: display a frame PNG in a tkinter window.

Usage:
    python _show_frame_helper.py <png_path> <title> <x> <y>

Uses PPM-based PhotoImage and prints the window hwnd for capture.
"""
import sys
import io
import ctypes
import ctypes.wintypes

# DPI awareness: must be set before any GUI library import.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import tkinter as tk
from PIL import Image


def main():
    if len(sys.argv) < 5:
        print("Usage: _show_frame_helper.py <png_path> <title> <x> <y>")
        sys.exit(1)

    png_path = sys.argv[1]
    title = sys.argv[2]
    x = int(sys.argv[3])
    y = int(sys.argv[4])

    img = Image.open(png_path).convert("RGB")
    w, h = img.size

    root = tk.Tk()
    root.title(title)
    root.overrideredirect(True)  # Remove title bar so client area = window area
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.resizable(False, False)

    # PPM-based PhotoImage for full RGB
    buf = io.BytesIO()
    img.save(buf, format="PPM")
    photo = tk.PhotoImage(data=buf.getvalue())

    canvas = tk.Canvas(root, width=w, height=h, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, anchor=tk.NW, image=photo)

    for _ in range(5):
        root.update()
        root.update_idletasks()

    # Get hwnd and print it
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    if not hwnd:
        hwnd = root.winfo_id()
    print(f"READY {hwnd}", flush=True)

    root.mainloop()


if __name__ == "__main__":
    main()
