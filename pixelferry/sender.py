"""Sender: encode frames and display or save them."""

import os
import json
import secrets
from typing import List, Optional

from PIL import Image

from .constants import DEFAULT_SEND_FPS, WINDOW_WIDTH, WINDOW_HEIGHT
from .package import build_package, get_package_sha256
from .framing import split_into_chunks, build_frame_header
from .codec import encode_frame_to_image
from .utils import sha256_bytes


def generate_frame_images(
    repo_path: str,
    output_dir: str = None,
    extra_excludes: set = None,
    repo_name: str = "",
) -> tuple:
    """Build package, split into chunks, encode as frame images.

    Returns (frames, package_sha256_hex, session_id).
    If output_dir is given, saves frames as PNGs.
    """
    # Build package
    package_bytes = build_package(repo_path, output_path=None, extra_excludes=extra_excludes)
    package_sha256 = sha256_bytes(package_bytes)
    package_sha256_hex = package_sha256.hex()

    # Split into chunks
    chunks = split_into_chunks(package_bytes)
    total_frames = len(chunks)

    # Generate session ID
    session_id = secrets.token_bytes(16)

    # Build frames
    frames = []
    for idx, chunk in enumerate(chunks):
        header_bytes = build_frame_header(
            session_id=session_id,
            frame_index=idx,
            total_frames=total_frames,
            payload=chunk,
            package_sha256=package_sha256,
            repo_name=repo_name,
        )

        # Combine: start_marker + header + payload + end_marker
        # Markers are embedded in the nibble stream, not as raw bytes
        frame_bytes = _pack_frame_nibbles(header_bytes, chunk)
        img = encode_frame_to_image(frame_bytes)
        frames.append(img)

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            img.save(os.path.join(output_dir, f"frame_{idx:06d}.png"))

    return frames, package_sha256_hex, session_id


def _pack_frame_nibbles(header_bytes: bytes, payload: bytes) -> bytes:
    """Pack header + payload into a byte stream that the encoder expects.

    The encoder converts bytes to nibbles sequentially:
    [header as nibbles] [payload as nibbles]
    Markers are added by the codec during encoding.
    """
    return header_bytes + payload


def play_qr_then_frames(
    frames: List[Image.Image],
    session_id: bytes,
    total_frames: int,
    package_sha256_hex: str,
    repo_name: str,
    fps: float = DEFAULT_SEND_FPS,
    title: str = "PixelFerry Sender",
):
    """Show QR code first, then play frames after user clicks Start."""
    import tkinter as tk

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.configure(bg="white")  # White border for alignment reference

    BORDER_SIZE = 20

    # Lock root window geometry to prevent size changes when child widgets are shown/hidden
    window_w = WINDOW_WIDTH + 2 * BORDER_SIZE
    window_h = WINDOW_HEIGHT + 2 * BORDER_SIZE
    root.geometry(f"{window_w}x{window_h}")

    # Generate QR code
    qr_img = make_qr_image(session_id, total_frames, package_sha256_hex, repo_name)

    # Convert QR to PhotoImage
    from io import BytesIO
    buf = BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    qr_photo = tk.PhotoImage(data=buf.read())

    # Convert frame images to PhotoImage
    photo_images = []
    for frame in frames:
        buf = BytesIO()
        frame.save(buf, format="PNG")
        buf.seek(0)
        photo = tk.PhotoImage(data=buf.read())
        photo_images.append(photo)

    # Layout
    canvas = tk.Canvas(root, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, highlightthickness=0, bg="#1e1e1e")
    canvas.pack(padx=BORDER_SIZE, pady=BORDER_SIZE)

    # Show QR code centered, leaving space for buttons and status text below
    qr_x = (WINDOW_WIDTH - qr_img.width) // 2
    qr_y = (WINDOW_HEIGHT - qr_img.height - 120) // 2
    qr_y = max(10, qr_y)  # At least 10px top margin
    canvas_qr = canvas.create_image(qr_x, qr_y, anchor=tk.NW, image=qr_photo)

    # Status text as canvas element, centered near bottom
    status_text = canvas.create_text(
        WINDOW_WIDTH // 2, WINDOW_HEIGHT - 110,
        text=f"仓库: {repo_name} | {total_frames} 帧 | 等待接收端扫描二维码...",
        fill="white", font=("Microsoft YaHei", 10),
    )

    # Start button embedded in canvas to avoid pack_forget triggering window resize
    start_btn = tk.Button(
        canvas, text="开始发送 (Enter)", font=("Microsoft YaHei", 11, "bold"),
        bg="#2d8a2d", fg="white", width=20, relief="flat", cursor="hand2",
    )
    # Embed button as canvas window, positioned at bottom
    start_btn_win = canvas.create_window(
        WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60,
        anchor=tk.CENTER, window=start_btn
    )

    # State
    mode = {"state": "qr"}  # "qr" or "playing"
    idx = [0]
    running = [True]
    first_play = [True]

    # Pre-create frame image object (hidden), update via itemconfig to avoid flicker
    frame_image_obj = canvas.create_image(0, 0, anchor=tk.NW, image=photo_images[0], state="hidden")

    def start_playing():
        mode["state"] = "playing"
        # Remove button from canvas entirely
        canvas.delete(start_btn_win)
        # Hide QR code, show frame image
        canvas.itemconfigure(canvas_qr, state="hidden")
        canvas.itemconfigure(frame_image_obj, state="normal")
        # Move status text to bottom with high-contrast frame counter
        canvas.coords(status_text, WINDOW_WIDTH // 2, WINDOW_HEIGHT - 10)
        canvas.itemconfig(status_text, text=f"帧 {idx[0]+1}/{total_frames}", fill="yellow", font=("Consolas", 10))
        canvas.tag_raise(status_text)
        show_next()

    def show_next():
        if not running[0] or mode["state"] != "playing":
            return
        # Update image and frame counter in-place, no flicker
        canvas.itemconfig(frame_image_obj, image=photo_images[idx[0]])
        canvas.itemconfig(status_text, text=f"帧 {idx[0]+1}/{total_frames}")
        canvas.update()

        # Hold first frame for 3 seconds to allow receiver to lock alignment cache
        delay = int(1000 / fps)
        if idx[0] == 0 and first_play[0]:
            delay = 3000
            first_play[0] = False

        idx[0] = (idx[0] + 1) % len(photo_images)
        root.after(delay, show_next)

    def on_close():
        running[0] = False
        root.destroy()

    start_btn.config(command=start_playing)
    root.bind("<Return>", lambda e: start_playing())
    root.protocol("WM_DELETE_WINDOW", on_close)

    root.mainloop()


def save_frame_pngs(
    repo_path: str,
    output_dir: str,
    extra_excludes: set = None,
    repo_name: str = "",
):
    """Convenience: build package and save all frames as PNGs."""
    frames, sha256_hex, session_id = generate_frame_images(
        repo_path, output_dir=output_dir, extra_excludes=extra_excludes,
        repo_name=repo_name,
    )
    print(f"Generated {len(frames)} frames in {output_dir}")
    print(f"Package SHA-256: {sha256_hex}")
    print(f"Session ID: {session_id.hex()}")
    return frames, sha256_hex, session_id


def make_qr_image(session_id: bytes, total_frames: int, package_sha256_hex: str, repo_name: str) -> Image.Image:
    """Generate a QR code image containing session info."""
    import qrcode

    # QR code carries full session metadata: session_id, frame count, SHA-256, repo name
    data = json.dumps({
        "s": session_id.hex(),
        "f": total_frames,
        "h": package_sha256_hex,
        "r": repo_name,
    }, separators=(",", ":"))

    # Generate base QR code at 1:1 scale (each module = 1 pixel)
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # Maximum safe size that fits within the window
    max_safe_size = min(WINDOW_HEIGHT - 60, WINDOW_WIDTH - 60, 600)
    
    # Integer-multiple scaling to prevent module size variation (would break QR detection)
    qr_base_size = img.size[0]
    scale = max(1, max_safe_size // qr_base_size)
    
    # Integer-multiple scaling to prevent module distortion
    target_size = qr_base_size * scale
    img = img.resize((target_size, target_size), Image.NEAREST)

    return img


def play_frames_window(
    frames: List[Image.Image],
    fps: float = DEFAULT_SEND_FPS,
    title: str = "PixelFerry Sender",
):
    """Display frames in a tkinter window with loop playback."""
    import tkinter as tk

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.configure(bg="white")  # White border for alignment reference

    BORDER_SIZE = 10
    canvas = tk.Canvas(root, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, highlightthickness=0)
    canvas.pack(padx=BORDER_SIZE, pady=BORDER_SIZE)

    # Convert PIL images to PhotoImage
    photo_images = []
    for frame in frames:
        from io import BytesIO
        buf = BytesIO()
        frame.save(buf, format="PNG")
        buf.seek(0)
        photo = tk.PhotoImage(data=buf.read())
        photo_images.append(photo)

    idx = [0]
    running = [True]

    def show_next():
        if not running[0]:
            return
        canvas.create_image(0, 0, anchor=tk.NW, image=photo_images[idx[0]])
        canvas.update()
        idx[0] = (idx[0] + 1) % len(photo_images)
        root.after(int(1000 / fps), show_next)

    def on_close():
        running[0] = False
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    show_next()
    root.mainloop()
