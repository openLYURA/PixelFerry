"""Corner marker detection and perspective transform.

Detects four colored corner markers (yellow/red/green/blue) in a screenshot
using HSV thresholding and morphological filtering, then applies perspective
correction to extract the sender window image.

Supports 4-corner, 3-corner, and 2-corner reconstruction modes for robustness
against occlusion (e.g. remote desktop watermarks).
"""

from typing import Optional, Tuple

from PIL import Image

from .constants import WINDOW_WIDTH, WINDOW_HEIGHT, MARKER_SIZE


def _find_sender_by_corners(
    img: Image.Image,
    region_offset: Tuple[int, int] = (0, 0),
) -> Tuple[Optional[Image.Image], Optional[Tuple[int, int, int, int]]]:
    """Find and correct sender window using corner position markers.

    Corner markers:
    - Top-left: Yellow (255, 255, 0)
    - Top-right: Red (255, 0, 0)
    - Bottom-left: Green (0, 255, 0)
    - Bottom-right: Blue (0, 0, 255)

    Returns (warped_image, absolute_bounding_box) or (None, None).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None, None

    np_img = np.array(img)
    if len(np_img.shape) == 2:
        np_img = cv2.cvtColor(np_img, cv2.COLOR_GRAY2RGB)
    elif np_img.shape[2] == 4:
        np_img = cv2.cvtColor(np_img, cv2.COLOR_RGBA2RGB)

    h, w, _ = np_img.shape
    img_h, img_w = h, w
    hsv = cv2.cvtColor(np_img, cv2.COLOR_RGB2HSV)

    # Color ranges for marker detection (low saturation threshold for watermark tolerance)
    # Yellow (tl): H 10-45, S 15+
    lower_yellow = np.array([10, 15, 40], dtype=np.uint8)
    upper_yellow = np.array([45, 255, 255], dtype=np.uint8)
    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)

    # Red (tr): H 0-15 and H 160-180, S 15+
    lower_red1 = np.array([0, 15, 40], dtype=np.uint8)
    upper_red1 = np.array([15, 255, 255], dtype=np.uint8)
    lower_red2 = np.array([160, 15, 40], dtype=np.uint8)
    upper_red2 = np.array([180, 255, 255], dtype=np.uint8)
    mask_red = cv2.bitwise_or(
        cv2.inRange(hsv, lower_red1, upper_red1),
        cv2.inRange(hsv, lower_red2, upper_red2),
    )

    # Green (bl): H 35-95, S 15+
    lower_green = np.array([35, 15, 40], dtype=np.uint8)
    upper_green = np.array([95, 255, 255], dtype=np.uint8)
    mask_green = cv2.inRange(hsv, lower_green, upper_green)

    # Blue (br): H 90-150, S 15+
    lower_blue = np.array([90, 15, 40], dtype=np.uint8)
    upper_blue = np.array([150, 255, 255], dtype=np.uint8)
    mask_blue = cv2.inRange(hsv, lower_blue, upper_blue)

    # Morphological opening to remove watermark artifacts
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask_yellow = cv2.morphologyEx(mask_yellow, cv2.MORPH_OPEN, kernel)
    mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)
    mask_blue = cv2.morphologyEx(mask_blue, cv2.MORPH_OPEN, kernel)

    def get_candidates(mask, quadrant):
        """Collect candidate marker centroids in the given screen quadrant."""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cands = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < 4 or area > 10000:
                continue
            x_box, y_box, w_box, h_box = cv2.boundingRect(c)
            aspect_ratio = float(w_box) / h_box
            if area >= 25 and (aspect_ratio < 0.2 or aspect_ratio > 5.0):
                continue
            M_moments = cv2.moments(c)
            if M_moments["m00"] == 0:
                continue
            cx = int(M_moments["m10"] / M_moments["m00"])
            cy = int(M_moments["m01"] / M_moments["m00"])

            # Quadrant filtering
            if quadrant == "tl" and (cx > img_w * 0.7 or cy > img_h * 0.7):
                continue
            if quadrant == "tr" and (cx < img_w * 0.3 or cy > img_h * 0.7):
                continue
            if quadrant == "bl" and (cx > img_w * 0.7 or cy < img_h * 0.3):
                continue
            if quadrant == "br" and (cx < img_w * 0.3 or cy < img_h * 0.3):
                continue

            cands.append((cx, cy))

        # Sort by distance to corresponding corner, keep top 16
        if quadrant == "tl":
            cands.sort(key=lambda pt: pt[0] ** 2 + pt[1] ** 2)
        elif quadrant == "tr":
            cands.sort(key=lambda pt: (pt[0] - img_w) ** 2 + pt[1] ** 2)
        elif quadrant == "bl":
            cands.sort(key=lambda pt: pt[0] ** 2 + (pt[1] - img_h) ** 2)
        elif quadrant == "br":
            cands.sort(key=lambda pt: (pt[0] - img_w) ** 2 + (pt[1] - img_h) ** 2)

        return cands[:16]

    cands_tl = get_candidates(mask_yellow, "tl")
    cands_tr = get_candidates(mask_red, "tr")
    cands_bl = get_candidates(mask_green, "bl")
    cands_br = get_candidates(mask_blue, "br")

    best_quad = None
    target_ratio = float(WINDOW_WIDTH) / WINDOW_HEIGHT

    # --- Priority 1: Full 4-corner match ---
    if cands_tl and cands_tr and cands_bl and cands_br:
        best_area = 0.0
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                for pt_bl in cands_bl:
                    for pt_br in cands_br:
                        if not (pt_tr[0] > pt_tl[0] and pt_br[0] > pt_bl[0]):
                            continue
                        if not (pt_bl[1] > pt_tl[1] and pt_br[1] > pt_tr[1]):
                            continue

                        # Tilt check
                        if abs(pt_tr[1] - pt_tl[1]) > 0.25 * (pt_tr[0] - pt_tl[0]):
                            continue
                        if abs(pt_br[1] - pt_bl[1]) > 0.25 * (pt_br[0] - pt_bl[0]):
                            continue
                        if abs(pt_bl[0] - pt_tl[0]) > 0.25 * (pt_bl[1] - pt_tl[1]):
                            continue
                        if abs(pt_br[0] - pt_tr[0]) > 0.25 * (pt_br[1] - pt_tr[1]):
                            continue

                        w1 = pt_tr[0] - pt_tl[0]
                        w2 = pt_br[0] - pt_bl[0]
                        h1 = pt_bl[1] - pt_tl[1]
                        h2 = pt_br[1] - pt_tr[1]
                        w_mean = (w1 + w2) / 2.0
                        h_mean = (h1 + h2) / 2.0

                        if w_mean < 50 or h_mean < 50:
                            continue

                        area = w_mean * h_mean
                        if area < 0.05 * (img_w * img_h):
                            continue

                        ratio = w_mean / h_mean
                        if not (0.5 * target_ratio < ratio < 1.5 * target_ratio):
                            continue

                        if abs(w1 - w2) / w_mean > 0.15:
                            continue
                        if abs(h1 - h2) / h_mean > 0.15:
                            continue

                        if area > best_area:
                            best_area = area
                            best_quad = (pt_tl, pt_tr, pt_bl, pt_br)

    # --- Priority 2: 3-corner match with reconstruction ---
    if best_quad is None:
        best_tri_area = 0.0
        best_tri = None

        def _check_tri(pt_a, pt_b, pt_c, pt_d, w_cand, h_cand):
            nonlocal best_tri_area, best_tri
            if w_cand < 50 or h_cand < 50:
                return
            # All corners must be within image bounds
            for pt in (pt_a, pt_b, pt_c, pt_d):
                if pt[0] < 0 or pt[1] < 0 or pt[0] >= img_w or pt[1] >= img_h:
                    return
            area = w_cand * h_cand
            if area < 0.05 * (img_w * img_h):
                return
            ratio = w_cand / h_cand
            if not (0.5 * target_ratio < ratio < 1.5 * target_ratio):
                return
            if area > best_tri_area:
                best_tri_area = area
                best_tri = (pt_a, pt_b, pt_c, pt_d)

        # Missing tl
        for pt_tr in cands_tr:
            for pt_bl in cands_bl:
                for pt_br in cands_br:
                    if not (pt_br[0] > pt_bl[0] and pt_br[1] > pt_tr[1]):
                        continue
                    if abs(pt_br[1] - pt_bl[1]) > 0.25 * (pt_br[0] - pt_bl[0]):
                        continue
                    if abs(pt_br[0] - pt_tr[0]) > 0.25 * (pt_br[1] - pt_tr[1]):
                        continue
                    pt_tl = (pt_tr[0] + pt_bl[0] - pt_br[0], pt_tr[1] + pt_bl[1] - pt_br[1])
                    _check_tri(pt_tl, pt_tr, pt_bl, pt_br,
                               pt_br[0] - pt_bl[0], pt_br[1] - pt_tr[1])

        # Missing tr
        for pt_tl in cands_tl:
            for pt_bl in cands_bl:
                for pt_br in cands_br:
                    if not (pt_br[0] > pt_bl[0] and pt_bl[1] > pt_tl[1]):
                        continue
                    if abs(pt_br[1] - pt_bl[1]) > 0.25 * (pt_br[0] - pt_bl[0]):
                        continue
                    if abs(pt_bl[0] - pt_tl[0]) > 0.25 * (pt_bl[1] - pt_tl[1]):
                        continue
                    pt_tr = (pt_tl[0] + pt_br[0] - pt_bl[0], pt_tl[1] + pt_br[1] - pt_bl[1])
                    _check_tri(pt_tl, pt_tr, pt_bl, pt_br,
                               pt_br[0] - pt_bl[0], pt_bl[1] - pt_tl[1])

        # Missing bl
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                for pt_br in cands_br:
                    if not (pt_tr[0] > pt_tl[0] and pt_br[1] > pt_tr[1]):
                        continue
                    if abs(pt_tr[1] - pt_tl[1]) > 0.25 * (pt_tr[0] - pt_tl[0]):
                        continue
                    if abs(pt_br[0] - pt_tr[0]) > 0.25 * (pt_br[1] - pt_tr[1]):
                        continue
                    pt_bl = (pt_tl[0] + pt_br[0] - pt_tr[0], pt_tl[1] + pt_br[1] - pt_tr[1])
                    _check_tri(pt_tl, pt_tr, pt_bl, pt_br,
                               pt_tr[0] - pt_tl[0], pt_br[1] - pt_tr[1])

        # Missing br
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                for pt_bl in cands_bl:
                    if not (pt_tr[0] > pt_tl[0] and pt_bl[1] > pt_tl[1]):
                        continue
                    if abs(pt_tr[1] - pt_tl[1]) > 0.25 * (pt_tr[0] - pt_tl[0]):
                        continue
                    if abs(pt_bl[0] - pt_tl[0]) > 0.25 * (pt_bl[1] - pt_tl[1]):
                        continue
                    pt_br = (pt_tr[0] + pt_bl[0] - pt_tl[0], pt_tr[1] + pt_bl[1] - pt_tl[1])
                    _check_tri(pt_tl, pt_tr, pt_bl, pt_br,
                               pt_tr[0] - pt_tl[0], pt_bl[1] - pt_tl[1])

        if best_tri is not None:
            best_quad = best_tri

    # --- Priority 3: 2-corner adjacent reconstruction ---
    if best_quad is None:
        aspect_ratio = target_ratio
        inv_aspect = 1.0 / target_ratio

        def norm_vec(v):
            return np.sqrt(np.sum(v ** 2))

        max_di_len = 0.0

        def _check_2corner_quad(pt_tl, pt_tr, pt_bl, pt_br, edge_len):
            nonlocal max_di_len, best_quad
            # All corners must be within image bounds
            for pt in (pt_tl, pt_tr, pt_bl, pt_br):
                if pt[0] < 0 or pt[1] < 0 or pt[0] >= img_w or pt[1] >= img_h:
                    return
            max_di_len = edge_len
            best_quad = (pt_tl, pt_tr, pt_bl, pt_br)

        # Case A: bottom edge (green + blue)
        for pt_bl in cands_bl:
            for pt_br in cands_br:
                if not (pt_br[0] > pt_bl[0]):
                    continue
                dx_val = pt_br[0] - pt_bl[0]
                dy_val = abs(pt_br[1] - pt_bl[1])
                if dy_val > 0.25 * dx_val:
                    continue
                v_bottom = np.array(pt_br) - np.array(pt_bl)
                len_bottom = norm_vec(v_bottom)
                if len_bottom > 0.5 * img_w and len_bottom > max_di_len:
                    dx = v_bottom[1] / len_bottom
                    dy = -v_bottom[0] / len_bottom
                    h_val = len_bottom * inv_aspect
                    v_up = np.array([dx * h_val, dy * h_val])
                    pt_tl = tuple(np.round(np.array(pt_bl) + v_up).astype(int))
                    pt_tr = tuple(np.round(np.array(pt_br) + v_up).astype(int))
                    _check_2corner_quad(pt_tl, pt_tr, pt_bl, pt_br, len_bottom)

        # Case B: top edge (yellow + red)
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                if not (pt_tr[0] > pt_tl[0]):
                    continue
                dx_val = pt_tr[0] - pt_tl[0]
                dy_val = abs(pt_tr[1] - pt_tl[1])
                if dy_val > 0.25 * dx_val:
                    continue
                v_top = np.array(pt_tr) - np.array(pt_tl)
                len_top = norm_vec(v_top)
                if len_top > 0.5 * img_w and len_top > max_di_len:
                    dx = -v_top[1] / len_top
                    dy = v_top[0] / len_top
                    h_val = len_top * inv_aspect
                    v_down = np.array([dx * h_val, dy * h_val])
                    pt_bl = tuple(np.round(np.array(pt_tl) + v_down).astype(int))
                    pt_br = tuple(np.round(np.array(pt_tr) + v_down).astype(int))
                    _check_2corner_quad(pt_tl, pt_tr, pt_bl, pt_br, len_top)

        # Case C: left edge (yellow + green)
        for pt_tl in cands_tl:
            for pt_bl in cands_bl:
                if not (pt_bl[1] > pt_tl[1]):
                    continue
                dy_val = pt_bl[1] - pt_tl[1]
                dx_val = abs(pt_bl[0] - pt_tl[0])
                if dx_val > 0.25 * dy_val:
                    continue
                v_left = np.array(pt_bl) - np.array(pt_tl)
                len_left = norm_vec(v_left)
                if len_left > 0.5 * img_h and len_left > max_di_len:
                    dx = v_left[1] / len_left
                    dy = -v_left[0] / len_left
                    w_val = len_left * aspect_ratio
                    v_right = np.array([dx * w_val, dy * w_val])
                    pt_tr = tuple(np.round(np.array(pt_tl) + v_right).astype(int))
                    pt_br = tuple(np.round(np.array(pt_bl) + v_right).astype(int))
                    _check_2corner_quad(pt_tl, pt_tr, pt_bl, pt_br, len_left)

        # Case D: right edge (red + blue)
        for pt_tr in cands_tr:
            for pt_br in cands_br:
                if not (pt_br[1] > pt_tr[1]):
                    continue
                dy_val = pt_br[1] - pt_tr[1]
                dx_val = abs(pt_br[0] - pt_tr[0])
                if dx_val > 0.25 * dy_val:
                    continue
                v_right_side = np.array(pt_br) - np.array(pt_tr)
                len_right = norm_vec(v_right_side)
                if len_right > 0.5 * img_h and len_right > max_di_len:
                    dx = -v_right_side[1] / len_right
                    dy = v_right_side[0] / len_right
                    w_val = len_right * aspect_ratio
                    v_left = np.array([dx * w_val, dy * w_val])
                    pt_tl = tuple(np.round(np.array(pt_tr) + v_left).astype(int))
                    pt_bl = tuple(np.round(np.array(pt_br) + v_left).astype(int))
                    _check_2corner_quad(pt_tl, pt_tr, pt_bl, pt_br, len_right)

    # --- Apply perspective transform ---
    if best_quad is not None:
        detected = {
            "tl": best_quad[0],
            "tr": best_quad[1],
            "bl": best_quad[2],
            "br": best_quad[3],
        }
    else:
        return None, None

    src_pts = np.array([
        detected["tl"],
        detected["tr"],
        detected["bl"],
        detected["br"],
    ], dtype=np.float32)

    dst_pts = np.array([
        [MARKER_SIZE / 2, MARKER_SIZE / 2],
        [WINDOW_WIDTH - MARKER_SIZE / 2, MARKER_SIZE / 2],
        [MARKER_SIZE / 2, WINDOW_HEIGHT - MARKER_SIZE / 2],
        [WINDOW_WIDTH - MARKER_SIZE / 2, WINDOW_HEIGHT - MARKER_SIZE / 2],
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped_np = cv2.warpPerspective(np_img, M, (WINDOW_WIDTH, WINDOW_HEIGHT))
    warped_img = Image.fromarray(warped_np)

    # Compute absolute bounding box for caching
    ox, oy = region_offset
    half_marker = MARKER_SIZE // 2
    x1 = min(detected["tl"][0], detected["bl"][0]) + ox - half_marker
    y1 = min(detected["tl"][1], detected["tr"][1]) + oy - half_marker
    x2 = max(detected["tr"][0], detected["br"][0]) + ox + half_marker
    y2 = max(detected["bl"][1], detected["br"][1]) + oy + half_marker
    abs_box = (int(x1), int(y1), int(x2), int(y2))

    return warped_img, abs_box
