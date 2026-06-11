"""QR code detection for session initialization.

Detects and decodes QR codes from captured screen images, supporting
multiple preprocessing strategies (adaptive threshold, CLAHE, morphology)
for robustness against remote desktop watermarks.
"""

import json
import time
import sys
from typing import Optional, Tuple

from PIL import Image


def detect_qr_code(img: Image.Image) -> Optional[dict]:
    """Detect and decode a QR code from an image.

    Returns parsed JSON dict with keys: s (session_id), f (total_frames),
    h (package_sha256), r (repo_name). Returns None on failure.
    """
    w, h = img.size
    # Crop center for ultra-wide windows where QR is always centered
    if w > 800:
        crop_left = (w - 800) // 2
        crop_right = crop_left + 800
        img = img.crop((crop_left, 0, crop_right, h))

    # Try pyzbar first (best decoding quality)
    try:
        from pyzbar import pyzbar

        decoded = pyzbar.decode(img)
        if decoded:
            for obj in decoded:
                data_str = obj.data.decode("utf-8", errors="ignore")
                try:
                    return json.loads(data_str)
                except json.JSONDecodeError:
                    pass
    except ImportError:
        pass

    # Fallback: OpenCV with multi-strategy preprocessing
    try:
        import cv2
        import numpy as np

        img_np = np.array(img)

        if len(img_np.shape) == 3:
            gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_np

        detector = cv2.QRCodeDetector()
        strategies = [gray]

        # Adaptive Gaussian thresholding (two window sizes)
        try:
            strategies.append(cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 25, 9,
            ))
            strategies.append(cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 51, 15,
            ))
        except Exception:
            pass

        # CLAHE + Otsu
        try:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            cl_gray = clahe.apply(gray)
            _, cl_thresh = cv2.threshold(cl_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            strategies.append(cl_thresh)
        except Exception:
            pass

        # Morphological opening/closing + Otsu
        try:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            opened = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
            _, op_thresh = cv2.threshold(opened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            strategies.append(op_thresh)

            closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            _, cl_thresh2 = cv2.threshold(closed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            strategies.append(cl_thresh2)
        except Exception:
            pass

        for s_img in strategies:
            # Try at original scale
            data, points, _ = detector.detectAndDecode(s_img)
            if data:
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    pass

            # Try at rescaled sizes
            for scale in [0.75, 1.5]:
                sh, sw = s_img.shape
                if (scale < 1.0 and min(sh, sw) < 150) or (scale > 1.0 and max(sh, sw) > 2500):
                    continue
                resized = cv2.resize(s_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
                data, points, _ = detector.detectAndDecode(resized)
                if data:
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        pass

    except Exception:
        pass

    return None


def wait_for_qr(
    region: Tuple[int, int, int, int],
    verbose: bool = True,
) -> Optional[dict]:
    """Capture screen in a loop until a QR code is detected.

    Returns the parsed QR data dict, or None if cancelled.
    """
    from .capture import _capture_screen

    if verbose:
        print("Waiting for sender QR code...")
        print("Please display the sender window in the OBS virtual camera.")

    interval = 1.0
    attempt = 0

    try:
        while True:
            attempt += 1
            img = _capture_screen(region)

            if verbose:
                from PIL import ImageStat

                stat = ImageStat.Stat(img)
                avg = sum(stat.mean) / 3
                from .capture import _capture_cache

                sender_box = _capture_cache.get("sender_box")
                print(
                    f"  Attempt {attempt}: {img.width}x{img.height}, "
                    f"brightness={avg:.0f}, sender={'yes' if sender_box else 'no'}",
                    end="",
                )

            qr_data = detect_qr_code(img)

            if verbose:
                print(f", QR={'yes' if qr_data else 'no'}")

            if qr_data and "s" in qr_data and "f" in qr_data:
                if verbose:
                    repo = qr_data.get("r", "unknown")
                    frames = qr_data["f"]
                    print(f"\nQR code detected!")
                    print(f"  Repository: {repo}")
                    print(f"  Frames: {frames}")
                    print(f"  SHA256: {qr_data.get('h', '?')[:16]}...")
                    print()
                return qr_data

            time.sleep(interval)

    except KeyboardInterrupt:
        if verbose:
            print("\nCancelled.")
        return None
