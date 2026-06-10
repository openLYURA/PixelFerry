"""Receiver: decode frames from images (PNGs or screenshots)."""

import os
import sys
import time
import json

# Initialize Windows DPI awareness to prevent logical pixel scaling
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field

from PIL import Image

from .codec import decode_image_to_frame
from .framing import FrameHeader
from .package import unpack_package
from .utils import sha256_hex
from .constants import WINDOW_WIDTH, WINDOW_HEIGHT, MARKER_SIZE


@dataclass
class ReceiveState:
    """Tracks received frames for a single session."""
    session_id: bytes
    total_frames: int
    package_sha256: bytes
    repo_name: str = ""
    frames: Dict[int, bytes] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return len(self.frames) >= self.total_frames

    @property
    def received_count(self) -> int:
        return len(self.frames)

    @property
    def missing_indices(self):
        return [i for i in range(self.total_frames) if i not in self.frames]

    @property
    def progress(self) -> float:
        if self.total_frames == 0:
            return 0.0
        return self.received_count / self.total_frames


# ── PNG-based decoding ────────────────────────────────────────────

def decode_from_pngs(frames_dir: str) -> Tuple[Optional[bytes], Optional[ReceiveState]]:
    """Decode all PNGs in a directory and merge into a single package.

    Returns (package_bytes, state).
    """
    state = None
    png_files = sorted(
        f for f in os.listdir(frames_dir) if f.endswith(".png")
    )

    for fname in png_files:
        img = Image.open(os.path.join(frames_dir, fname))
        result = decode_single_frame(img)
        if result is None:
            continue

        header, payload, valid = result

        if not valid:
            print(f"  Skip {fname}: checksum mismatch or unreliable blocks")
            continue

        # Initialize or verify session
        if state is None:
            state = ReceiveState(
                session_id=header.session_id,
                total_frames=header.total_frames,
                package_sha256=header.package_sha256,
                repo_name=header.repo_name.decode("utf-8", errors="replace"),
            )

        if header.session_id != state.session_id:
            continue

        # Store (ignore duplicates)
        if header.frame_index not in state.frames:
            state.frames[header.frame_index] = payload
            print(f"  Frame {header.frame_index}/{state.total_frames} OK")

    if state is None or not state.is_complete:
        return None, state

    pkg, ok = _merge_and_verify(state)
    if not ok:
        return None, state
    return pkg, state


# ── Screen capture ──────────────────────────────────────────────

_capture_cache = {"hwnd": None, "sender_box": None}


def _find_sender_by_corners(img: Image.Image, region_offset: Tuple[int, int] = (0, 0)) -> Tuple[Optional[Image.Image], Optional[Tuple[int, int, int, int]]]:
    """Find and correct sender window using corner position markers.

    Corner markers:
    - Top-left: Yellow (255, 255, 0)
    - Top-right: Red (255, 0, 0)
    - Bottom-left: Green (0, 255, 0)
    - Bottom-right: Blue (0, 0, 255)

    Uses HSV color space thresholding to detect markers, then applies
    perspective transform for correction. Supports 4-corner, 3-corner,
    and 2-corner reconstruction modes.
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

    # ── 颜色范围提取 ──────────────────────────────────────────────
    # 将颜色提取的饱和度（S）下限从 50 大幅下调至 15，以兼容由于浅灰色/白色水印叠加导致的色彩冲淡与饱和度退化。
    # 黄色（tl）：H 放宽到 10-45，S 降到 15+，V 降到 40+
    lower_white = np.array([10, 15, 40], dtype=np.uint8)
    upper_white = np.array([45, 255, 255], dtype=np.uint8)
    mask_white = cv2.inRange(hsv, lower_white, upper_white)

    # 红色（tr）：H1: 0-15 & H2: 160-180, S 降到 15+，V 降到 40+
    lower_red1 = np.array([0, 15, 40], dtype=np.uint8)
    upper_red1 = np.array([15, 255, 255], dtype=np.uint8)
    lower_red2 = np.array([160, 15, 40], dtype=np.uint8)
    upper_red2 = np.array([180, 255, 255], dtype=np.uint8)
    mask_red = cv2.bitwise_or(cv2.inRange(hsv, lower_red1, upper_red1),
                              cv2.inRange(hsv, lower_red2, upper_red2))

    # 绿色（bl）：H 放宽到 35-95，S 降到 15+，V 降到 40+
    lower_green = np.array([35, 15, 40], dtype=np.uint8)
    upper_green = np.array([95, 255, 255], dtype=np.uint8)
    mask_green = cv2.inRange(hsv, lower_green, upper_green)

    # 蓝色（br）：H 放宽到 90-150，S 降到 15+，V 降到 40+
    lower_blue = np.array([90, 15, 40], dtype=np.uint8)
    upper_blue = np.array([150, 255, 255], dtype=np.uint8)
    mask_blue = cv2.inRange(hsv, lower_blue, upper_blue)

    # ── 形态学去水印滤波 ───────────────────────────────────────────
    # 使用 2x2 的矩形结构元素对提取出的二值 mask 执行形态学开运算（Opening：先腐蚀后膨胀）。
    # 这能够轻松切断细小笔画的水印连线，并彻底清除单独的细线或像素噪点，保留粗壮的 12x12 角点轮廓本身。
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask_white = cv2.morphologyEx(mask_white, cv2.MORPH_OPEN, kernel)
    mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)
    mask_blue = cv2.morphologyEx(mask_blue, cv2.MORPH_OPEN, kernel)

    def get_candidates(mask, quadrant):
        """收集指定象限内所有符合基本面积和形态特征的候选点"""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cands = []
        for c in contours:
            area = cv2.contourArea(c)
            # 水印可能会轻微削减色块，因此放宽面积和形态学比例限制
            if area < 4 or area > 10000:
                continue
            x_box, y_box, w_box, h_box = cv2.boundingRect(c)
            aspect_ratio = float(w_box) / h_box
            if area >= 25:
                if aspect_ratio < 0.2 or aspect_ratio > 5.0:
                    continue
            M_moments = cv2.moments(c)
            if M_moments["m00"] == 0:
                continue
            cx = int(M_moments["m10"] / M_moments["m00"])
            cy = int(M_moments["m01"] / M_moments["m00"])

            # 象限大体过滤，防止极远端的明显错误点
            if quadrant == 'tl' and (cx > img_w * 0.7 or cy > img_h * 0.7):
                continue
            if quadrant == 'tr' and (cx < img_w * 0.3 or cy > img_h * 0.7):
                continue
            if quadrant == 'bl' and (cx > img_w * 0.7 or cy < img_h * 0.3):
                continue
            if quadrant == 'br' and (cx < img_w * 0.3 or cy < img_h * 0.3):
                continue

            cands.append((cx, cy))

        # ── 边缘距离排序与数量截断 ──────────────────────────────────────
        # 将候选点按照到其对应顶点（物理屏幕的角点位置）的欧几里得距离升序排序。
        # 即使数据区产生了上百个候选块，真正位于屏幕四角的定位点也一定会排列在最前面。
        # 我们只保留离角落最近的前 8 个候选点进行组合匹配，将匹配复杂度降低数万倍，确保闪电响应。
        if quadrant == 'tl':
            cands.sort(key=lambda pt: pt[0]**2 + pt[1]**2)
        elif quadrant == 'tr':
            cands.sort(key=lambda pt: (pt[0] - img_w)**2 + pt[1]**2)
        elif quadrant == 'bl':
            cands.sort(key=lambda pt: pt[0]**2 + (pt[1] - img_h)**2)
        elif quadrant == 'br':
            cands.sort(key=lambda pt: (pt[0] - img_w)**2 + (pt[1] - img_h)**2)

        return cands[:16]

    # 1. 搜集四色色块的全部候选点
    cands_tl = get_candidates(mask_white, 'tl')
    cands_tr = get_candidates(mask_red, 'tr')
    cands_bl = get_candidates(mask_green, 'bl')
    cands_br = get_candidates(mask_blue, 'br')

    best_quad = None
    target_ratio = float(WINDOW_WIDTH) / WINDOW_HEIGHT

    # 2. 第一优先：完美 4 角点匹配
    if cands_tl and cands_tr and cands_bl and cands_br:
        best_area = 0.0
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                for pt_bl in cands_bl:
                    for pt_br in cands_br:
                        # 2.1 相对排布关系校验
                        if not (pt_tr[0] > pt_tl[0] and pt_br[0] > pt_bl[0]):
                            continue
                        if not (pt_bl[1] > pt_tl[1] and pt_br[1] > pt_tr[1]):
                            continue
                        
                        # 2.1.2 倾斜度校验 (大体水平边的高差不能超过宽度的 25%，垂直边宽差不能超过高度 of 25%)
                        if abs(pt_tr[1] - pt_tl[1]) > 0.25 * (pt_tr[0] - pt_tl[0]):
                            continue
                        if abs(pt_br[1] - pt_bl[1]) > 0.25 * (pt_br[0] - pt_bl[0]):
                            continue
                        if abs(pt_bl[0] - pt_tl[0]) > 0.25 * (pt_bl[1] - pt_tl[1]):
                            continue
                        if abs(pt_br[0] - pt_tr[0]) > 0.25 * (pt_br[1] - pt_tr[1]):
                            continue
                        
                        # 2.2 估计宽高
                        w1 = pt_tr[0] - pt_tl[0]
                        w2 = pt_br[0] - pt_bl[0]
                        h1 = pt_bl[1] - pt_tl[1]
                        h2 = pt_br[1] - pt_tr[1]
                        w_mean = (w1 + w2) / 2.0
                        h_mean = (h1 + h2) / 2.0
                        
                        if w_mean < 50 or h_mean < 50:
                            continue
                            
                        # 2.2.2 面积占比校验（防噪核心：包围框面积必须占整个截图面积的 35% 以上）
                        area = w_mean * h_mean
                        if area < 0.05 * (img_w * img_h):
                            continue
                            
                        # 2.3 宽高比校验 (标准为 target_ratio)
                        ratio = w_mean / h_mean
                        if not (0.5 * target_ratio < ratio < 1.5 * target_ratio):
                            continue
                            
                        # 2.4 平行与对称度校验 (两侧边长差距不应超过 15%)
                        if abs(w1 - w2) / w_mean > 0.15:
                            continue
                        if abs(h1 - h2) / h_mean > 0.15:
                            continue
                            
                        if area > best_area:
                            best_area = area
                            best_quad = (pt_tl, pt_tr, pt_bl, pt_br)
        if best_quad is not None:
            pass  # 4-corner match found

    # 3. Second priority: 3-corner matching with reconstruction
    if best_quad is None:
        best_tri_area = 0.0
        best_tri = None
        
        # 缺 tl
        for pt_tr in cands_tr:
            for pt_bl in cands_bl:
                for pt_br in cands_br:
                    if not (pt_br[0] > pt_bl[0] and pt_br[1] > pt_tr[1]):
                        continue
                    # 倾斜度校验
                    if abs(pt_br[1] - pt_bl[1]) > 0.25 * (pt_br[0] - pt_bl[0]):
                        continue
                    if abs(pt_br[0] - pt_tr[0]) > 0.25 * (pt_br[1] - pt_tr[1]):
                        continue
                        
                    w_cand = pt_br[0] - pt_bl[0]
                    h_cand = pt_br[1] - pt_tr[1]
                    if w_cand < 50 or h_cand < 50:
                        continue
                    # 面积占比校验（防噪核心：包围框面积必须占整个截图面积 of 35% 以上）
                    area = w_cand * h_cand
                    if area < 0.05 * (img_w * img_h):
                        continue
                    ratio = w_cand / h_cand
                    if not (0.5 * target_ratio < ratio < 1.5 * target_ratio):
                        continue
                    pt_tl = (pt_tr[0] + pt_bl[0] - pt_br[0], pt_tr[1] + pt_bl[1] - pt_br[1])
                    if area > best_tri_area:
                        best_tri_area = area
                        best_tri = (pt_tl, pt_tr, pt_bl, pt_br)
                        
        # 缺 tr
        for pt_tl in cands_tl:
            for pt_bl in cands_bl:
                for pt_br in cands_br:
                    if not (pt_br[0] > pt_bl[0] and pt_bl[1] > pt_tl[1]):
                        continue
                    # 倾斜度校验
                    if abs(pt_br[1] - pt_bl[1]) > 0.25 * (pt_br[0] - pt_bl[0]):
                        continue
                    if abs(pt_bl[0] - pt_tl[0]) > 0.25 * (pt_bl[1] - pt_tl[1]):
                        continue
                        
                    w_cand = pt_br[0] - pt_bl[0]
                    h_cand = pt_bl[1] - pt_tl[1]
                    if w_cand < 50 or h_cand < 50:
                        continue
                    # 面积占比校验
                    area = w_cand * h_cand
                    if area < 0.05 * (img_w * img_h):
                        continue
                    ratio = w_cand / h_cand
                    if not (0.5 * target_ratio < ratio < 1.5 * target_ratio):
                        continue
                    pt_tr = (pt_tl[0] + pt_br[0] - pt_bl[0], pt_tl[1] + pt_br[1] - pt_bl[1])
                    if area > best_tri_area:
                        best_tri_area = area
                        best_tri = (pt_tl, pt_tr, pt_bl, pt_br)

        # 缺 bl
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                for pt_br in cands_br:
                    if not (pt_tr[0] > pt_tl[0] and pt_br[1] > pt_tr[1]):
                        continue
                    # 倾斜度校验
                    if abs(pt_tr[1] - pt_tl[1]) > 0.25 * (pt_tr[0] - pt_tl[0]):
                        continue
                    if abs(pt_br[0] - pt_tr[0]) > 0.25 * (pt_br[1] - pt_tr[1]):
                        continue
                        
                    w_cand = pt_tr[0] - pt_tl[0]
                    h_cand = pt_br[1] - pt_tr[1]
                    if w_cand < 50 or h_cand < 50:
                        continue
                    # 面积占比校验
                    area = w_cand * h_cand
                    if area < 0.05 * (img_w * img_h):
                        continue
                    ratio = w_cand / h_cand
                    if not (0.5 * target_ratio < ratio < 1.5 * target_ratio):
                        continue
                    pt_bl = (pt_tl[0] + pt_br[0] - pt_tr[0], pt_tl[1] + pt_br[1] - pt_tr[1])
                    if area > best_tri_area:
                        best_tri_area = area
                        best_tri = (pt_tl, pt_tr, pt_bl, pt_br)

        # 缺 br
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                for pt_bl in cands_bl:
                    if not (pt_tr[0] > pt_tl[0] and pt_bl[1] > pt_tl[1]):
                        continue
                    # 倾斜度校验
                    if abs(pt_tr[1] - pt_tl[1]) > 0.25 * (pt_tr[0] - pt_tl[0]):
                        continue
                    if abs(pt_bl[0] - pt_tl[0]) > 0.25 * (pt_bl[1] - pt_tl[1]):
                        continue
                        
                    w_cand = pt_tr[0] - pt_tl[0]
                    h_cand = pt_bl[1] - pt_tl[1]
                    if w_cand < 50 or h_cand < 50:
                        continue
                    # 面积占比校验
                    area = w_cand * h_cand
                    if area < 0.05 * (img_w * img_h):
                        continue
                    ratio = w_cand / h_cand
                    if not (0.5 * target_ratio < ratio < 1.5 * target_ratio):
                        continue
                    pt_br = (pt_tr[0] + pt_bl[0] - pt_tl[0], pt_tr[1] + pt_bl[1] - pt_tl[1])
                    if area > best_tri_area:
                        best_tri_area = area
                        best_tri = (pt_tl, pt_tr, pt_bl, pt_br)

        if best_tri is not None:
            best_quad = best_tri

    # 4. Third priority: 2-corner adjacent reconstruction (e.g. RDP occlusion)
    if best_quad is None:
        aspect_ratio = target_ratio
        inv_aspect = 1.0 / target_ratio

        def norm_vec(v):
            return np.sqrt(np.sum(v**2))

        best_di = None
        max_di_len = 0.0
        best_di_msg = ""

        # 情况 A: 只有底部的绿(bl)和蓝(br)
        for pt_bl in cands_bl:
            for pt_br in cands_br:
                if not (pt_br[0] > pt_bl[0]):
                    continue
                # 倾斜度校验：Y 轴偏差不得超过 X 轴偏差的 25% (大体水平)
                dx_val = pt_br[0] - pt_bl[0]
                dy_val = abs(pt_br[1] - pt_bl[1])
                if dy_val > 0.25 * dx_val:
                    continue

                v_bottom = np.array(pt_br) - np.array(pt_bl)
                len_bottom = norm_vec(v_bottom)
                # 长度占比防噪：底边长度必须占整个截图宽度的 50% 以上，防止内部噪点线段篡位
                if len_bottom > 0.5 * img_w:
                    if len_bottom > max_di_len:
                        dx = v_bottom[1] / len_bottom
                        dy = -v_bottom[0] / len_bottom
                        h_val = len_bottom * inv_aspect
                        v_up = np.array([dx * h_val, dy * h_val])
                        pt_tl = tuple(np.round(np.array(pt_bl) + v_up).astype(int))
                        pt_tr = tuple(np.round(np.array(pt_br) + v_up).astype(int))
                        max_di_len = len_bottom
                        best_di = (pt_tl, pt_tr, pt_bl, pt_br)
                        best_di_msg = f"[DEBUG] 触发底边(绿+蓝)双点几何重建，补全后角点: {{'tl': {pt_tl}, 'tr': {pt_tr}, 'bl': {pt_bl}, 'br': {pt_br}}}"

        # 情况 B: 只有顶部的白(tl)和红(tr)
        for pt_tl in cands_tl:
            for pt_tr in cands_tr:
                if not (pt_tr[0] > pt_tl[0]):
                    continue
                # 倾斜度校验：Y 轴偏差不得超过 X 轴偏差的 25% (大体水平)
                dx_val = pt_tr[0] - pt_tl[0]
                dy_val = abs(pt_tr[1] - pt_tl[1])
                if dy_val > 0.25 * dx_val:
                    continue

                v_top = np.array(pt_tr) - np.array(pt_tl)
                len_top = norm_vec(v_top)
                # 长度占比防噪：顶边长度必须占整个截图宽度的 50% 以上
                if len_top > 0.5 * img_w:
                    if len_top > max_di_len:
                        dx = -v_top[1] / len_top
                        dy = v_top[0] / len_top
                        h_val = len_top * inv_aspect
                        v_down = np.array([dx * h_val, dy * h_val])
                        pt_bl = tuple(np.round(np.array(pt_tl) + v_down).astype(int))
                        pt_br = tuple(np.round(np.array(pt_tr) + v_down).astype(int))
                        max_di_len = len_top
                        best_di = (pt_tl, pt_tr, pt_bl, pt_br)
                        best_di_msg = f"[DEBUG] 触发顶边(白+红)双点几何重建，补全后角点: {{'tl': {pt_tl}, 'tr': {pt_tr}, 'bl': {pt_bl}, 'br': {pt_br}}}"

        # 情况 C: 只有左侧的白(tl)和绿(bl)
        for pt_tl in cands_tl:
            for pt_bl in cands_bl:
                if not (pt_bl[1] > pt_tl[1]):
                    continue
                # 倾斜度校验：X 轴偏差不得超过 Y 轴偏差 of 25% (大体垂直)
                dy_val = pt_bl[1] - pt_tl[1]
                dx_val = abs(pt_bl[0] - pt_tl[0])
                if dx_val > 0.25 * dy_val:
                    continue

                v_left = np.array(pt_bl) - np.array(pt_tl)
                len_left = norm_vec(v_left)
                # 长度占比防噪：左边长度必须占整个截图高度的 50% 以上
                if len_left > 0.5 * img_h:
                    if len_left > max_di_len:
                        dx = v_left[1] / len_left
                        dy = -v_left[0] / len_left
                        w_val = len_left * aspect_ratio
                        v_right = np.array([dx * w_val, dy * w_val])
                        pt_tr = tuple(np.round(np.array(pt_tl) + v_right).astype(int))
                        pt_br = tuple(np.round(np.array(pt_bl) + v_right).astype(int))
                        max_di_len = len_left
                        best_di = (pt_tl, pt_tr, pt_bl, pt_br)
                        best_di_msg = f"[DEBUG] 触发左边(白+绿)双点几何重建，补全后角点: {{'tl': {pt_tl}, 'tr': {pt_tr}, 'bl': {pt_bl}, 'br': {pt_br}}}"

        # 情况 D: 只有右侧的红(tr)和蓝(br)
        for pt_tr in cands_tr:
            for pt_br in cands_br:
                if not (pt_br[1] > pt_tr[1]):
                    continue
                # 倾斜度校验：X 轴偏差不得超过 Y 轴偏差 of 25% (大体垂直)
                dy_val = pt_br[1] - pt_tr[1]
                dx_val = abs(pt_br[0] - pt_tr[0])
                if dx_val > 0.25 * dy_val:
                    continue

                v_right_side = np.array(pt_br) - np.array(pt_tr)
                len_right = norm_vec(v_right_side)
                # 长度占比防噪：右边长度必须占整个截图高度的 50% 以上
                if len_right > 0.5 * img_h:
                    if len_right > max_di_len:
                        dx = -v_right_side[1] / len_right
                        dy = v_right_side[0] / len_right
                        w_val = len_right * aspect_ratio
                        v_left = np.array([dx * w_val, dy * w_val])
                        pt_tl = tuple(np.round(np.array(pt_tr) + v_left).astype(int))
                        pt_bl = tuple(np.round(np.array(pt_br) + v_left).astype(int))
                        max_di_len = len_right
                        best_di = (pt_tl, pt_tr, pt_bl, pt_br)

        if best_di is not None:
            best_quad = best_di

    # 5. Final corner assembly
    if best_quad is not None:
        detected = {
            'tl': best_quad[0],
            'tr': best_quad[1],
            'bl': best_quad[2],
            'br': best_quad[3]
        }
        valid_pts = detected
    else:
        detected = {'tl': None, 'tr': None, 'bl': None, 'br': None}
        valid_pts = {}

    # 6. Apply perspective transform
    if len(valid_pts) == 4:
        src_pts = np.array([
            detected['tl'],
            detected['tr'],
            detected['bl'],
            detected['br']
        ], dtype=np.float32)

        # 发送端定位块中心的期望坐标，以 MARKER_SIZE 计中心偏移
        dst_pts = np.array([
            [MARKER_SIZE / 2, MARKER_SIZE / 2],
            [WINDOW_WIDTH - MARKER_SIZE / 2, MARKER_SIZE / 2],
            [MARKER_SIZE / 2, WINDOW_HEIGHT - MARKER_SIZE / 2],
            [WINDOW_WIDTH - MARKER_SIZE / 2, WINDOW_HEIGHT - MARKER_SIZE / 2]
        ], dtype=np.float32)

        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        warped_np = cv2.warpPerspective(np_img, M, (WINDOW_WIDTH, WINDOW_HEIGHT))
        warped_img = Image.fromarray(warped_np)

        # 计算绝对坐标框用于缓存
        ox, oy = region_offset
        # 定位角点检测出的是 24x24 大色块的重心坐标
        # 需要在四个方向各自向外扩展 MARKER_SIZE // 2 = 12 像素，才能完整复原出 3000x270 的物理窗口
        # 这能保证缓存裁剪出的图像也是完美的 3000x270 物理像素，完全免除拉伸造成的网格漂移
        half_marker = MARKER_SIZE // 2
        x1 = min(detected['tl'][0], detected['bl'][0]) + ox - half_marker
        y1 = min(detected['tl'][1], detected['tr'][1]) + oy - half_marker
        x2 = max(detected['tr'][0], detected['br'][0]) + ox + half_marker
        y2 = max(detected['bl'][1], detected['br'][1]) + oy + half_marker
        abs_box = (int(x1), int(y1), int(x2), int(y2))

        return warped_img, abs_box

    return None, None


def capture_screen_region(region: Tuple[int, int, int, int]) -> Image.Image:
    """Capture screen region and locate sender window within it."""
    img = _capture_screen(region)

    # 1. Use cached bounding box if available
    sender_box = _capture_cache["sender_box"]
    if sender_box:
        x1, y1, x2, y2 = sender_box
        rel_x1 = x1 - region[0]
        rel_y1 = y1 - region[1]
        rel_x2 = x2 - region[0]
        rel_y2 = y2 - region[1]
        if rel_x1 >= 0 and rel_y1 >= 0 and rel_x2 <= img.width and rel_y2 <= img.height:
            crop = img.crop((rel_x1, rel_y1, rel_x2, rel_y2))
            aligned_img = crop.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST)
            return aligned_img

    # 2. Try robust 4-corner perspective correction
    sender, box = _find_sender_by_corners(img, region_offset=(region[0], region[1]))
    if box:
        _capture_cache["sender_box"] = box
        return sender

    # 3. Fallback to start marker pixel scanning
    sender, box = _find_sender_in_capture(img, region_offset=(region[0], region[1]))
    if box:
        _capture_cache["sender_box"] = box
    return sender



def _find_obs_or_rdp_hwnd():
    """Find OBS Studio or remote desktop window."""
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
                    # Priority: OBS first, then remote desktop
                    if any(kw in title for kw in ["obs", "obs64", "obs studio"]):
                        found.insert(0, hwnd)  # OBS first
                        return False
                    if any(kw in title for kw in ["mstsc", "remote desktop", "rdp", "teamviewer", "anydesk"]):
                        found.append(hwnd)
            return True

        cb = WNDENUMPROC(enum_cb)
        user32.EnumWindows(cb, 0)
        return found[0] if found else None
    except Exception:
        return None


def _align_blocks(img: Image.Image) -> Image.Image:
    """Align block grid so decoder can find start marker. Crops title bar and pads to correct offsets."""
    from .constants import (
        WINDOW_WIDTH, WINDOW_HEIGHT, DATA_X_OFFSET, DATA_Y_OFFSET,
        BLOCK_SIZE, COLOR_LEVELS, START_MARKER_NIBBLES,
    )

    w, h = img.size
    if w < WINDOW_WIDTH or h < WINDOW_HEIGHT:
        return img

    # Find start marker: first block color = (248, 8, 56)
    target = (COLOR_LEVELS[START_MARKER_NIBBLES[0]],
              COLOR_LEVELS[START_MARKER_NIBBLES[1]],
              COLOR_LEVELS[START_MARKER_NIBBLES[2]])

    marker_x, marker_y = None, None
    for y in range(min(h, 80)):
        for x in range(min(w, 80)):
            r, g, b = img.getpixel((x, y))
            if r == target[0] and g == target[1] and b == target[2]:
                marker_x, marker_y = x, y
                break
        if marker_x is not None:
            break

    if marker_x is None:
        # Can't find marker, return center crop
        cx, cy = w // 2, h // 2
        x1 = max(0, cx - WINDOW_WIDTH // 2)
        y1 = max(0, cy - WINDOW_HEIGHT // 2)
        crop = img.crop((x1, y1, x1 + WINDOW_WIDTH, y1 + WINDOW_HEIGHT))
        return crop.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST)

    # Calculate what the decoder expects:
    # First block center is at (DATA_X_OFFSET + BLOCK_SIZE//2, DATA_Y_OFFSET + BLOCK_SIZE//2)
    # = (16 + 4, 16 + 4) = (20, 20)
    # But marker is at the top-left of the first block, so:
    # Expected marker position = (DATA_X_OFFSET, DATA_Y_OFFSET) = (16, 16)
    # Actual marker position = (marker_x, marker_y)

    # We need to crop/pad so that marker ends up at (DATA_X_OFFSET, DATA_Y_OFFSET)
    # Crop source: from (marker_x - DATA_X_OFFSET, marker_y - DATA_Y_OFFSET)
    src_x = marker_x - DATA_X_OFFSET
    src_y = marker_y - DATA_Y_OFFSET

    # Create a WINDOW_WIDTH x WINDOW_HEIGHT canvas
    result = Image.new("RGB", (WINDOW_WIDTH, WINDOW_HEIGHT), (0, 0, 0))
    # Paste the source image at the correct offset
    paste_x = max(0, -src_x)
    paste_y = max(0, -src_y)
    crop_x = max(0, src_x)
    crop_y = max(0, src_y)
    crop_w = min(WINDOW_WIDTH, w - crop_x)
    crop_h = min(WINDOW_HEIGHT, h - crop_y)
    if crop_w > 0 and crop_h > 0:
        region = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
        result.paste(region, (paste_x, paste_y))

    return result


def _find_sender_in_capture(img: Image.Image, region_offset: Tuple[int, int] = (0, 0)) -> Tuple[Image.Image, Optional[Tuple[int, int, int, int]]]:
    """Find the PixelFerry sender window by locating the start marker color.

    Returns (resized_image, bounding_box_in_absolute_coords_or_None).
    """
    from .constants import (
        WINDOW_WIDTH, WINDOW_HEIGHT, DATA_X_OFFSET, DATA_Y_OFFSET,
        COLOR_LEVELS, START_MARKER_NIBBLES, BLOCK_SIZE,
    )
    w, h = img.size
    ox, oy = region_offset  # Offset to convert to absolute screen coords

    # Find start marker: first block color = (248, 8, 56)
    target = (COLOR_LEVELS[START_MARKER_NIBBLES[0]],
              COLOR_LEVELS[START_MARKER_NIBBLES[1]],
              COLOR_LEVELS[START_MARKER_NIBBLES[2]])

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
        # No marker found, return center of image
        cx, cy = w // 2, h // 2
        x1 = max(0, cx - WINDOW_WIDTH // 2)
        y1 = max(0, cy - WINDOW_HEIGHT // 2)
        crop = img.crop((x1, y1, x1 + WINDOW_WIDTH, y1 + WINDOW_HEIGHT))
        return crop.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST), None

    # Measure actual block size
    block_w = BLOCK_SIZE
    for x in range(marker_x + 1, min(w, marker_x + 30)):
        r, g, b = img.getpixel((x, marker_y))
        if r != target[0] or g != target[1] or b != target[2]:
            block_w = x - marker_x
            break

    # Calculate scale factor
    scale = block_w / BLOCK_SIZE

    # Calculate sender window bounds
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
            (WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST), None

    cropped = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))

    # Convert to absolute screen coordinates
    abs_box = (crop_x1 + ox, crop_y1 + oy, crop_x2 + ox, crop_y2 + oy)

    return cropped.resize((WINDOW_WIDTH, WINDOW_HEIGHT), Image.NEAREST), abs_box


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
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
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


def _capture_screen(region: Tuple[int, int, int, int]) -> Image.Image:
    """Capture screen region. Uses Pillow ImageGrab on Windows, falls back to mss."""
    left, top, width, height = region

    # 1. Windows 下优先使用 Pillow ImageGrab 并利用 DPI 缩放映射比例换算防截图错位
    if sys.platform == "win32":
        try:
            import ctypes
            from PIL import ImageGrab
            
            user32 = ctypes.windll.user32
            # 获取逻辑虚拟屏幕的原点和大小
            x_min = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            y_min = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            virtual_w = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            virtual_h = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            
            # 抓取整张虚拟屏幕大图 (物理像素尺寸)
            full_img = ImageGrab.grab(all_screens=True)
            phys_w, phys_h = full_img.width, full_img.height
            
            # 计算 DPI 缩放因子
            scale_x = phys_w / virtual_w if virtual_w > 0 else 1.0
            scale_y = phys_h / virtual_h if virtual_h > 0 else 1.0
            
            # 换算逻辑偏移到物理大图相对坐标系，统一单位为物理像素
            # 物理相对坐标 = (逻辑目标坐标 - 逻辑大图起点) * DPI 缩放因子
            rel_left = int(round((left - x_min) * scale_x))
            rel_top = int(round((top - y_min) * scale_y))
            rel_right = int(round((left + width - x_min) * scale_x))
            rel_bottom = int(round((top + height - y_min) * scale_y))
            
            # Clamp to valid bounds
            rel_left = max(0, min(rel_left, phys_w))
            rel_left = max(0, min(rel_left, phys_w))
            rel_top = max(0, min(rel_top, phys_h))
            rel_right = max(rel_left, min(rel_right, phys_w))
            rel_bottom = max(rel_top, min(rel_bottom, phys_h))
            
            return full_img.crop((rel_left, rel_top, rel_right, rel_bottom))
        except Exception:
            pass

    # 2. 降级使用 mss
    try:
        import mss
        monitor = {"left": left, "top": top, "width": width, "height": height}
        with mss.MSS() as sct:
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            return img
    except Exception:
        # 如果均失败，返回黑色图像垫片
        return Image.new("RGB", (width, height), (0, 0, 0))


def _print_progress_bar(received, total, repo_name=""):
    if total == 0:
        return
    pct = received / total
    bar_length = 30
    filled_length = int(round(bar_length * pct))
    bar = "█" * filled_length + "░" * (bar_length - filled_length)
    sys.stdout.write(f"\r进度: [{bar}] {pct*100:.0f}% ({received}/{total} 帧) | 仓库: {repo_name}")
    sys.stdout.flush()


# ── Real-time receive loop ────────────────────────────────────────

def receive_from_screen(
    region: Tuple[int, int, int, int],
    output_path: str = None,
    output_dir: str = None,
    fps: float = 8.0,
    max_cycles: int = 50,
    verbose: bool = True,
    qr_data: dict = None,
) -> Optional[bytes]:
    """Capture screen region in a loop, decode frames, and reconstruct package.

    Args:
        region: (left, top, width, height) screen region to capture.
        output_path: If set, save reconstructed package.pxf here.
        output_dir: If set, unpack restored repo here.
        fps: Capture rate (should be >= sender fps * 2 for reliability).
        max_cycles: Max number of full cycles to wait before giving up.
        verbose: Print progress.
        qr_data: Pre-detected QR code data from wait_for_qr().

    Returns:
        Reconstructed package bytes, or None on failure.
    """
    state = None
    cycle_count = 0
    last_frame_count = 0
    stall_count = 0
    interval = 1.0 / fps
    consecutive_locate_failures = 0

    # Initialize state from QR data if available
    if qr_data:
        try:
            from .framing import bytes_to_nibbles
            session_id = bytes.fromhex(qr_data["s"])
            total_frames = qr_data["f"]
            package_sha256 = bytes.fromhex(qr_data["h"]) if "h" in qr_data else b""
            repo_name = qr_data.get("r", "")
            state = ReceiveState(
                session_id=session_id,
                total_frames=total_frames,
                package_sha256=package_sha256,
                repo_name=repo_name,
            )
            if verbose:
                print(f"检测到传输会话 (扫码): 共 {total_frames} 帧")
                _print_progress_bar(0, total_frames, repo_name)
        except Exception:
            pass

    if verbose:
        print(f"Listening on region {region} at {fps} FPS...")
        print("Press Ctrl+C to stop.\n")

    try:
        while cycle_count < max_cycles:
            t0 = time.monotonic()

            # Capture and decode
            img = capture_screen_region(region)
            result = decode_single_frame(img)

            if result is not None:
                header, payload, valid = result

                if valid:
                    consecutive_locate_failures = 0  # 只有成功接收到 100% 校验合格的有效帧，才重置连续失败计数
                    # Initialize session on first valid frame
                    if state is None:
                        state = ReceiveState(
                            session_id=header.session_id,
                            total_frames=header.total_frames,
                            package_sha256=header.package_sha256,
                            repo_name=header.repo_name.decode("utf-8", errors="replace"),
                        )
                        if verbose:
                            print(f"检测到传输会话: 仓库 '{state.repo_name}', 共 {header.total_frames} 帧")
                            _print_progress_bar(0, header.total_frames, state.repo_name)

                    if header.session_id == state.session_id:
                        # 动态从帧头补全从极简二维码中缺失的 package_sha256 与仓库名
                        if not state.package_sha256:
                            state.package_sha256 = header.package_sha256
                        if not state.repo_name and header.repo_name:
                            state.repo_name = header.repo_name.decode("utf-8", errors="replace")

                        if header.frame_index not in state.frames:
                            state.frames[header.frame_index] = payload
                            if verbose:
                                _print_progress_bar(state.received_count, state.total_frames, state.repo_name)

                        # Check completion
                        if state.is_complete:
                            pkg, ok = _merge_and_verify(state)
                            if ok:
                                if verbose:
                                    print()  # 换行以保护进度条显示
                                # Use repo_name from header for subfolder
                                actual_output_dir = output_dir
                                if output_dir and state.repo_name:
                                    actual_output_dir = os.path.join(output_dir, state.repo_name)

                                if output_path:
                                    with open(output_path, "wb") as f:
                                        f.write(pkg)
                                    if verbose:
                                        print(f"\nPackage saved to {output_path}")

                                if actual_output_dir:
                                    unpack_to_directory(pkg, actual_output_dir, overwrite=True)
                                    if verbose:
                                        print(f"Repository restored to {actual_output_dir}")

                                return pkg
                            else:
                                if verbose:
                                    print("Package verification failed, continuing...")
                                state = None
                else:
                                    consecutive_locate_failures += 1  # Count validation failures
            else:
                consecutive_locate_failures += 1

            # Reset capture cache after 60 consecutive failures to re-locate sender
            if consecutive_locate_failures >= 60:
                if _capture_cache["sender_box"] is not None:
                    _capture_cache["sender_box"] = None
                consecutive_locate_failures = 0


            # Detect cycle completion (frame count stalled = sender looped)
            if state and state.received_count == last_frame_count:
                stall_count += 1
                if stall_count >= int(fps * 1.5):  # ~1.5 seconds of no new frames
                    cycle_count += 1
                    if verbose:
                        missing = state.missing_indices
                        print(f"  Cycle {cycle_count} complete, "
                              f"{state.received_count}/{state.total_frames} frames"
                              f"{f' (missing {len(missing)})' if missing else ''}")
                    stall_count = 0
                    # If all frames received, stop early
                    if state.is_complete:
                        break
            else:
                stall_count = 0
            last_frame_count = state.received_count if state else 0

            # Maintain target FPS
            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        if verbose:
            print("\nStopped by user.")

    if state and verbose:
        print(f"\nReceived {state.received_count}/{state.total_frames} frames")
        missing = state.missing_indices
        if missing:
            print(f"Missing: {missing[:20]}{'...' if len(missing) > 20 else ''}")

    return None


# ── Helpers ───────────────────────────────────────────────────────

def decode_single_frame(img: Image.Image) -> Optional[Tuple[FrameHeader, bytes, bool]]:
    """Decode a single frame image."""
    return decode_image_to_frame(img)


def _merge_and_verify(state: ReceiveState) -> Tuple[Optional[bytes], bool]:
    """Merge received frames and verify package integrity."""
    package_bytes = b"".join(
        state.frames[i] for i in range(state.total_frames)
    )

    if sha256_hex(package_bytes) != state.package_sha256.hex():
        return None, False

    return package_bytes, True


def unpack_to_directory(
    package_bytes: bytes,
    output_dir: str,
    overwrite: bool = False,
):
    """Unpack a verified package into a directory."""
    written = unpack_package(package_bytes, output_dir, overwrite=overwrite)
    print(f"Unpacked {len(written)} files to {output_dir}")
    return written


# ── QR code detection ────────────────────────────────────────────

def detect_qr_code(img: Image.Image) -> Optional[dict]:
    """Detect and decode a QR code from an image. Returns parsed JSON dict or None.

    Uses pyzbar if available, otherwise falls back to OpenCV with multiple
    preprocessing strategies (adaptive thresholding, morphological operations,
    CLAHE) for robust detection under watermark interference.
    """
    w, h = img.size
    # ── 针对超宽窗口对二维码进行居中裁剪，防止 OpenCV 无法在海量空白中定位二维码 ──
    # 在 3000x270 等极宽长条窗口下，二维码必然水平居中。我们裁剪出中央 800 像素宽度的核心区域。
    if w > 800:
        crop_left = (w - 800) // 2
        crop_right = crop_left + 800
        img = img.crop((crop_left, 0, crop_right, h))

    # ── 优先使用 pyzbar 检测（容错及解码能力极高） ──
    try:
        from pyzbar import pyzbar
        # pyzbar 直接支持 Pillow Image
        decoded = pyzbar.decode(img)
        if decoded:
            for obj in decoded:
                data_str = obj.data.decode('utf-8', errors='ignore')
                try:
                    return json.loads(data_str)
                except json.JSONDecodeError:
                    pass
    except ImportError:
        pass

    try:
        import cv2
        import numpy as np

        img_np = np.array(img)

        # Convert to grayscale
        if len(img_np.shape) == 3:
            gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_np

        detector = cv2.QRCodeDetector()

        # 生成多策略图像处理链
        strategies = []

        # 1. 原始灰度图
        strategies.append(gray)

        # 2. 自适应高斯阈值二值化（防半透明文字水印的核心）
        try:
            # 2.1 较小的邻域窗口，适用于细线条水印
            adaptive_1 = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 25, 9
            )
            strategies.append(adaptive_1)

            # 2.2 较大的邻域窗口，适用于粗大字号水印
            adaptive_2 = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 51, 15
            )
            strategies.append(adaptive_2)
        except Exception:
            pass

        # 3. CLAHE（局部直方图对比度拉伸）+ 大津二值化
        try:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            cl_gray = clahe.apply(gray)
            _, cl_thresh = cv2.threshold(cl_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            strategies.append(cl_thresh)
        except Exception:
            pass

        # 4. 形态学开运算/闭运算去噪（消除水印细线条粘连与孔洞）
        try:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            
            # 开运算：先腐蚀后膨胀（去除白底上的零碎黑色杂点）
            opened = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
            _, op_thresh = cv2.threshold(opened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            strategies.append(op_thresh)

            # 闭运算：先膨胀后腐蚀（填补黑码块内部因淡色文字导致的白色漏孔）
            closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            _, cl_thresh2 = cv2.threshold(closed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            strategies.append(cl_thresh2)
        except Exception:
            pass

        # 对每一种预处理图像，采用多尺度策略进行扫码
        for s_img in strategies:
            # 原尺度解码
            data, points, _ = detector.detectAndDecode(s_img)
            if data:
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    pass

            # 缩放尺度解码
            for scale in [0.75, 1.5]:
                h, w = s_img.shape
                # 限制缩放后的大小不要过大或过小
                if (scale < 1.0 and min(h, w) < 150) or (scale > 1.0 and max(h, w) > 2500):
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

    Returns the parsed QR data dict with keys: s (session_id), f (total_frames),
    h (package_sha256), r (repo_name).
    """
    if verbose:
        print("等待发送端显示二维码...")
        print("请在 OBS 虚拟摄像机中显示发送窗口")

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
                sender_box = _capture_cache.get("sender_box")
                print(f"  第{attempt}次: {img.width}x{img.height}, 亮度={avg:.0f}, sender={'有' if sender_box else '无'}", end="")

            qr_data = detect_qr_code(img)

            if verbose:
                print(f", QR={'是' if qr_data else '否'}")

            if qr_data and "s" in qr_data and "f" in qr_data:
                if verbose:
                    repo = qr_data.get("r", "unknown")
                    frames = qr_data["f"]
                    print(f"\n检测到二维码！")
                    print(f"  仓库: {repo}")
                    print(f"  帧数: {frames}")
                    print(f"  SHA256: {qr_data.get('h', '?')[:16]}...")
                    print()
                return qr_data

            time.sleep(interval)

    except KeyboardInterrupt:
        if verbose:
            print("\n已取消。")
        return None
