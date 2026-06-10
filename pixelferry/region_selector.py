"""Interactive region selector: resizable window for selecting a capture area.

A transparent green-bordered window appears. The user drags to move,
resizes from edges/corners, then clicks "OK" or presses Enter to confirm.
ESC cancels.

Usage:
    from pixelferry.region_selector import select_region
    region = select_region()  # returns (left, top, width, height) or None
"""

import tkinter as tk
from typing import Tuple, Optional
import sys

# 初始化 Windows DPI 适配，防止系统对 Tkinter 坐标进行逻辑像素虚化，确保返回物理像素
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class RegionSelector:
    """A draggable/resizable transparent window for region selection."""

    BORDER_COLOR = "#00ff00"
    BG_COLOR = "#00ff0020"
    HANDLE_SIZE = 8
    MIN_SIZE = 40

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PixelFerry Select")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        # Transparent background on Windows
        try:
            self.root.attributes("-transparentcolor", "#010101")
            self.root.configure(bg="#010101")
        except Exception:
            self.root.configure(bg="black")

        # Default size and position (center of screen)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        try:
            from .constants import WINDOW_WIDTH, WINDOW_HEIGHT
            # 比发送端窗口稍微宽裕 80 像素，省去老大重复拖拉的繁琐
            self.win_w = WINDOW_WIDTH + 80
            self.win_h = WINDOW_HEIGHT + 80
        except Exception:
            self.win_w, self.win_h = 1280, 880

        # 解除对超宽窗口宽度的限制，允许自适应 3000px 宽度
        self.win_x = max(10, (sw - self.win_w) // 2)
        self.win_y = max(10, (sh - self.win_h) // 2)
        self.root.geometry(f"{self.win_w}x{self.win_h}+{self.win_x}+{self.win_y}")

        # Canvas for border and controls
        self.canvas = tk.Canvas(self.root, highlightthickness=0,
                                bg="#010101", cursor="fleur")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self._draw_border()
        self._draw_controls()

        # State for dragging
        self._drag_data = {"x": 0, "y": 0, "action": None}
        self.result = None

        # Bind events
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Key>", self._on_key)

    def _draw_border(self):
        """Draw the green border rectangle."""
        self.canvas.delete("border")
        m = 2  # margin
        self.canvas.create_rectangle(
            m, m, self.win_w - m, self.win_h - m,
            outline=self.BORDER_COLOR, width=2, tags="border",
        )
        # Corner handles
        hs = self.HANDLE_SIZE
        for tag, coords in [
            ("nw", (m, m, m + hs, m + hs)),
            ("ne", (self.win_w - m - hs, m, self.win_w - m, m + hs)),
            ("sw", (m, self.win_h - m - hs, m + hs, self.win_h - m)),
            ("se", (self.win_w - m - hs, self.win_h - m - hs,
                    self.win_w - m, self.win_h - m)),
        ]:
            self.canvas.create_rectangle(
                *coords, fill=self.BORDER_COLOR, outline="white",
                width=1, tags=f"handle_{tag}",
            )

    def _draw_controls(self):
        """Draw the OK / Cancel buttons and size label at the bottom."""
        self.canvas.delete("controls")
        # Semi-transparent bar at bottom
        bar_h = 36
        y0 = self.win_h - bar_h
        self.canvas.create_rectangle(
            0, y0, self.win_w, self.win_h,
            fill="#333333", outline="", tags="controls",
        )
        # Size label
        self.canvas.create_text(
            12, y0 + bar_h // 2,
            text=f"{self.win_w} x {self.win_h}",
            fill="white", font=("Consolas", 11), anchor=tk.W,
            tags="sizelabel",
        )
        # OK button
        btn_w = 70
        ok_x = self.win_w - btn_w * 2 - 20
        self.canvas.create_rectangle(
            ok_x, y0 + 6, ok_x + btn_w, y0 + bar_h - 6,
            fill="#2d8a2d", outline="#4caf50", width=1, tags="btn_ok",
        )
        self.canvas.create_text(
            ok_x + btn_w // 2, y0 + bar_h // 2,
            text="OK (Enter)", fill="white",
            font=("Microsoft YaHei", 10), tags="btn_ok",
        )
        # Cancel button
        cancel_x = self.win_w - btn_w - 10
        self.canvas.create_rectangle(
            cancel_x, y0 + 6, cancel_x + btn_w, y0 + bar_h - 6,
            fill="#8a2d2d", outline="#f44336", width=1, tags="btn_cancel",
        )
        self.canvas.create_text(
            cancel_x + btn_w // 2, y0 + bar_h // 2,
            text="Cancel (Esc)", fill="white",
            font=("Microsoft YaHei", 10), tags="btn_cancel",
        )

    def _hit_test(self, x, y):
        """Determine what was clicked: 'nw/ne/sw/se' handle, 'move', 'ok', 'cancel', or None."""
        m = 2
        hs = self.HANDLE_SIZE
        # Check handles
        handles = {
            "nw": (m, m, m + hs, m + hs),
            "ne": (self.win_w - m - hs, m, self.win_w - m, m + hs),
            "sw": (m, self.win_h - m - hs, m + hs, self.win_h - m),
            "se": (self.win_w - m - hs, self.win_h - m - hs,
                   self.win_w - m, self.win_h - m),
        }
        for tag, (x1, y1, x2, y2) in handles.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return f"resize_{tag}"

        # Check buttons
        bar_h = 36
        y0 = self.win_h - bar_h
        btn_w = 70
        ok_x = self.win_w - btn_w * 2 - 20
        cancel_x = self.win_w - btn_w - 10
        if y0 + 6 <= y <= y0 + bar_h - 6:
            if ok_x <= x <= ok_x + btn_w:
                return "ok"
            if cancel_x <= x <= cancel_x + btn_w:
                return "cancel"

        # Default: move
        return "move"

    def _on_press(self, event):
        action = self._hit_test(event.x, event.y)
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root
        self._drag_data["action"] = action
        self._drag_data["start_geo"] = (
            self.win_x, self.win_y, self.win_w, self.win_h,
        )

    def _on_drag(self, event):
        action = self._drag_data["action"]
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]

        if action == "ok" or action == "cancel":
            return

        sx, sy, sw, sh = self._drag_data["start_geo"]

        if action == "move":
            self.win_x = sx + dx
            self.win_y = sy + dy
        elif action == "resize_se":
            self.win_w = max(self.MIN_SIZE, sw + dx)
            self.win_h = max(self.MIN_SIZE, sh + dy)
        elif action == "resize_nw":
            new_w = max(self.MIN_SIZE, sw - dx)
            new_h = max(self.MIN_SIZE, sh - dy)
            self.win_x = sx + sw - new_w
            self.win_y = sy + sh - new_h
            self.win_w = new_w
            self.win_h = new_h
        elif action == "resize_ne":
            self.win_w = max(self.MIN_SIZE, sw + dx)
            new_h = max(self.MIN_SIZE, sh - dy)
            self.win_y = sy + sh - new_h
            self.win_h = new_h
        elif action == "resize_sw":
            new_w = max(self.MIN_SIZE, sw - dx)
            self.win_x = sx + sw - new_w
            self.win_w = new_w
            self.win_h = max(self.MIN_SIZE, sh + dy)

        self.root.geometry(f"{self.win_w}x{self.win_h}+{self.win_x}+{self.win_y}")
        self._draw_border()
        self._draw_controls()

    def _on_release(self, event):
        action = self._drag_data["action"]
        if action == "ok":
            self.root.update_idletasks()
            self.result = (
                self.root.winfo_rootx(),
                self.root.winfo_rooty(),
                self.root.winfo_width(),
                self.root.winfo_height()
            )
            self.root.destroy()
        elif action == "cancel":
            self.result = None
            self.root.destroy()
        self._drag_data["action"] = None

    def _on_key(self, event):
        if event.keysym == "Return":
            self.root.update_idletasks()
            self.result = (
                self.root.winfo_rootx(),
                self.root.winfo_rooty(),
                self.root.winfo_width(),
                self.root.winfo_height()
            )
            self.root.destroy()
        elif event.keysym == "Escape":
            self.result = None
            self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result


def select_region(prompt: str = "") -> Optional[Tuple[int, int, int, int]]:
    """Show a draggable/resizable window for region selection.

    Returns:
        (left, top, width, height) in screen pixels, or None if cancelled.
    """
    selector = RegionSelector()
    return selector.run()
