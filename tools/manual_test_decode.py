import sys
import os
from PIL import Image
from itertools import product

# 添加项目根路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pixelferry.codec import (
    decode_image_to_frame, decode_block, _read_block_center,
    nibbles_to_bytes, color_to_nibble
)
from pixelferry.constants import (
    START_MARKER_NIBBLES, END_MARKER_NIBBLES, BLOCK_SIZE,
    GRID_COLS, GRID_ROWS, DATA_X_OFFSET, DATA_Y_OFFSET,
    MAX_COLOR_DISTANCE, COLOR_LEVELS
)
from pixelferry.framing import parse_frame_header, validate_frame


def main():
    img_path = os.path.join(os.path.dirname(__file__), "debug_recv_aligned.png")
    if not os.path.exists(img_path):
        print(f"找不到测试图片: {img_path}")
        return

    img = Image.open(img_path)
    print(f"图片分辨率: {img.size}")

    import numpy as np
    img_arr = np.array(img)

    # 1. 逐个 Block 进行分析
    unreliable_blocks = []
    total_dist = 0
    max_single_dist = 0

    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            block = decode_block(img_arr, col, row)
            total_dist += block.max_distance
            if block.max_distance > max_single_dist:
                max_single_dist = block.max_distance
            
            if not block.reliable or block.max_distance >= 4:
                bx = DATA_X_OFFSET + col * BLOCK_SIZE
                by = DATA_Y_OFFSET + row * BLOCK_SIZE
                r_avg, g_avg, b_avg = _read_block_center(img_arr, bx, by)
                unreliable_blocks.append({
                    "pos": (col, row),
                    "pixel_coords": (bx, by),
                    "rgb_avg": (r_avg, g_avg, b_avg),
                    "nibbles": (block.r_nibble, block.g_nibble, block.b_nibble),
                    "dist": block.max_distance,
                    "reliable": block.reliable
                })

    avg_dist = total_dist / (GRID_ROWS * GRID_COLS)
    print("\n=== Snapping 诊断报告 ===")
    print(f"所有 Block 的平均 Snap 偏差距离: {avg_dist:.2f} (最大容许距离: {MAX_COLOR_DISTANCE})")
    print(f"单点最大 Snapping 偏差: {max_single_dist}")
    print(f"偏离较大 (max_distance >= 4) 的 Block 总数: {len(unreliable_blocks)} / {GRID_ROWS * GRID_COLS}")

    if unreliable_blocks:
        print("\n偏离较大 (>= 4) Block 详情 (前 40 个):")
        for idx, item in enumerate(unreliable_blocks[:40]):
            print(f"  #{idx+1}: 坐标(col={item['pos'][0]}, row={item['pos'][1]}), 物理起点x={item['pixel_coords'][0]},y={item['pixel_coords'][1]}, 可靠={item['reliable']}")
            print(f"       采样平均RGB: {item['rgb_avg']}")
            print(f"       Snap后Nibbles: R={item['nibbles'][0]} (标准{COLOR_LEVELS[item['nibbles'][0]]}), G={item['nibbles'][1]} (标准{COLOR_LEVELS[item['nibbles'][1]]}), B={item['nibbles'][2]} (标准{COLOR_LEVELS[item['nibbles'][2]]})")
            print(f"       通道最大偏差距离: {item['dist']}")
            print("-" * 40)


    # 2. 手动调用解码器
    result = decode_image_to_frame(img)
    nibbles = []
    
    # 提取用于爆破的 nibbles
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            block = decode_block(img_arr, col, row)
            nibbles.append(block.r_nibble)
            nibbles.append(block.g_nibble)
            nibbles.append(block.b_nibble)

    if result is None:
        print("\n解码器直接返回了 None (校验或解析失败)！")
    else:
        header, payload, valid = result
        print("\n解码成功！")
        print(f"帧索引: {header.frame_index}/{header.total_frames}")
        print(f"仓库名: {header.repo_name.decode('utf-8', errors='replace')}")
        print(f"有效性: {valid}")
        print(f"数据大小: {len(payload)} 字节")

        # 3. 自动诊断：如果只有一个不可靠 Block 且校验失败，我们尝试对它的 3 个 nibbles 进行 16 阶暴破，看看是不是它引起了校验失败！
        if not valid and len(unreliable_blocks) == 1:
            print("\n=== 自动爆破修复诊断 ===")
            bad_block = unreliable_blocks[0]
            col, row = bad_block["pos"]
            print(f"正在对不可靠 Block (col={col}, row={row}) 的 R/G/B nibbles 进行爆破校验...")
            
            # 找到这个 block 在 nibbles 数组里的起始位置
            block_idx = (row * GRID_COLS + col) * 3
            
            found_fix = False
            original_nibbles = list(nibbles)
            
            # 先单独爆破 G/B 波动极小的 R 通道（有 16 种可能）
            for r_try in range(16):
                test_nibbles = list(original_nibbles)
                test_nibbles[block_idx] = r_try
                
                h_nib = test_nibbles[12:12 + 256]
                h_bytes = nibbles_to_bytes(h_nib, 128)
                h_obj = parse_frame_header(h_bytes)
                if h_obj is None:
                    continue
                    
                p_nib_count = h_obj.payload_len * 2
                p_nibbles = test_nibbles[268:268 + p_nib_count]
                p_bytes = nibbles_to_bytes(p_nibbles, h_obj.payload_len)
                
                if validate_frame(h_obj, p_bytes):
                    print(f"🎉 爆破成功！发现正确值：当 R 通道 nibble 为 {r_try} (对应标准色阶: {COLOR_LEVELS[r_try]}) 时，SHA-256 成功通过匹配！")
                    print(f"     原 Snap 值: {bad_block['nibbles'][0]} (对应标准色阶: {COLOR_LEVELS[bad_block['nibbles'][0]]})")
                    print(f"     这证明了仅有这一个 Block 因 R 通道色值偏差 ({bad_block['rgb_avg'][0]} 踩在分界线) 导致认错色阶！")
                    found_fix = True
                    break
                    
            if not found_fix:
                print("只爆破 R 通道未能匹配。现在尝试爆破 R/G/B 的所有组合（共 16x16x16 = 4096 种可能）...")
                for r_try in range(16):
                    for g_try in range(16):
                        for b_try in range(16):
                            test_nibbles = list(original_nibbles)
                            test_nibbles[block_idx] = r_try
                            test_nibbles[block_idx + 1] = g_try
                            test_nibbles[block_idx + 2] = b_try
                            
                            h_nib = test_nibbles[12:12 + 256]
                            h_bytes = nibbles_to_bytes(h_nib, 128)
                            h_obj = parse_frame_header(h_bytes)
                            if h_obj is None:
                                continue
                                
                            p_nib_count = h_obj.payload_len * 2
                            p_nibbles = test_nibbles[268:268 + p_nib_count]
                            p_bytes = nibbles_to_bytes(p_nibbles, h_obj.payload_len)
                            
                            if validate_frame(h_obj, p_bytes):
                                print(f"🎉 爆破成功！发现正确组合：R={r_try}, G={g_try}, B={b_try}")
                                print(f"     原 Snap 值: R={bad_block['nibbles'][0]}, G={bad_block['nibbles'][1]}, B={bad_block['nibbles'][2]}")
                                found_fix = True
                                break
                        if found_fix:
                            break
                    if found_fix:
                        break
                        
            if not found_fix:
                print("❌ 爆破该 Block 的所有组合仍未成功。说明可能还有其他被判定为 reliable 的 Block 也被 Snap 错了！")

        # 4. 全局偏色补偿爆破器
        print("\n=== 全局偏色补偿爆破诊断 ===")
        print("正在搜集所有 Block 的原始采样平均值进行通道平移爆破...")
        
        raw_rgb_list = []
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                bx = DATA_X_OFFSET + col * BLOCK_SIZE
                by = DATA_Y_OFFSET + row * BLOCK_SIZE
                r_avg, g_avg, b_avg = _read_block_center(img, bx, by)
                raw_rgb_list.append((r_avg, g_avg, b_avg))
                
        found_global_fix = False
        offset_range = list(range(-12, 13))
        
        print("正在执行 17x17x17 全局通道色彩微调爆破 [-8..8]...")
        for r_off in range(-8, 9):
            for g_off in range(-8, 9):
                for b_off in range(-8, 9):
                    test_nibbles = []
                    for r_avg, g_avg, b_avg in raw_rgb_list:
                        r_n, _ = color_to_nibble(r_avg + r_off)
                        g_n, _ = color_to_nibble(g_avg + g_off)
                        b_n, _ = color_to_nibble(b_avg + b_off)
                        test_nibbles.append(r_n)
                        test_nibbles.append(g_n)
                        test_nibbles.append(b_n)
                        
                    h_nib = test_nibbles[12:12 + 256]
                    h_bytes = nibbles_to_bytes(h_nib, 128)
                    h_obj = parse_frame_header(h_bytes)
                    if h_obj is None:
                        continue
                        
                    p_nib_count = h_obj.payload_len * 2
                    if len(test_nibbles) < 268 + p_nib_count:
                        continue
                    p_nibbles = test_nibbles[268:268 + p_nib_count]
                    p_bytes = nibbles_to_bytes(p_nibbles, h_obj.payload_len)
                    
                    if validate_frame(h_obj, p_bytes):
                        print(f"🎉 🎉 🎉 全局爆破匹配成功！")
                        print(f"  当色彩补偿值为：R_offset={r_off}, G_offset={g_off}, B_offset={b_off} 时")
                        print(f"  数据 SHA-256 成功通过 100% 无损匹配校验！")
                        found_global_fix = True
                        break
                if found_global_fix:
                    break
            if found_global_fix:
                break
                
        if not found_global_fix:
            print("在中等范围 [-8..8] 内未能匹配，正在扩大到较大范围 [-12..12] （需耗时约 5 秒）...")
            for r_off in offset_range:
                for g_off in offset_range:
                    for b_off in offset_range:
                        if abs(r_off) <= 8 and abs(g_off) <= 8 and abs(b_off) <= 8:
                            continue
                        test_nibbles = []
                        for r_avg, g_avg, b_avg in raw_rgb_list:
                            r_n, _ = color_to_nibble(r_avg + r_off)
                            g_n, _ = color_to_nibble(g_avg + g_off)
                            b_n, _ = color_to_nibble(b_avg + b_off)
                            test_nibbles.append(r_n)
                            test_nibbles.append(g_n)
                            test_nibbles.append(b_n)
                            
                        h_nib = test_nibbles[12:12 + 256]
                        h_bytes = nibbles_to_bytes(h_nib, 128)
                        h_obj = parse_frame_header(h_bytes)
                        if h_obj is None:
                            continue
                            
                        p_nib_count = h_obj.payload_len * 2
                        if len(test_nibbles) < 268 + p_nib_count:
                            continue
                        p_nibbles = test_nibbles[268:268 + p_nib_count]
                        p_bytes = nibbles_to_bytes(p_nibbles, h_obj.payload_len)
                        
                        if validate_frame(h_obj, p_bytes):
                            print(f"🎉 🎉 🎉 全局爆破匹配成功！")
                            print(f"  当较大色彩补偿值为：R_offset={r_off}, G_offset={g_off}, B_offset={b_off} 时")
                            print(f"  数据 SHA-256 成功通过 100% 无损匹配校验！")
                            found_global_fix = True
                            break
                    if found_global_fix:
                        break
                if found_global_fix:
                    break
                    
        if not found_global_fix:
            print("❌ 全局平移补偿爆破仍未匹配。这说明可能存在局部几何形变导致部分行列坐标偏位，或者存在非线性的色彩伽马畸变。")

        # 5. 临界 Block 多变量组合爆破器 (针对踩在 7px 和 8px 底线上的临界色块进行自适应邻近色阶爆破)
        critical_blocks = [item for item in unreliable_blocks if item["dist"] >= 7]
        if not valid and critical_blocks and len(critical_blocks) <= 5:
            print(f"\n=== 临界 Block 多变量组合爆破诊断 (共 {len(critical_blocks)} 个 Block) ===")
            
            candidates_list = []
            block_indices = []
            
            for item in critical_blocks:
                col, row = item["pos"]
                block_idx = (row * GRID_COLS + col) * 3
                
                rgb_avg = item["rgb_avg"]
                nibbles_snap = item["nibbles"]
                
                # 找出最大偏差所在的通道索引
                dists = [
                    abs(rgb_avg[0] - COLOR_LEVELS[nibbles_snap[0]]),
                    abs(rgb_avg[1] - COLOR_LEVELS[nibbles_snap[1]]),
                    abs(rgb_avg[2] - COLOR_LEVELS[nibbles_snap[2]])
                ]
                max_ch = dists.index(max(dists))
                
                snap_val = nibbles_snap[max_ch]
                # 计算次近候选
                sec_val = snap_val - 1 if rgb_avg[max_ch] < COLOR_LEVELS[snap_val] else snap_val + 1
                sec_val = max(0, min(15, sec_val))
                
                candidates_list.append((snap_val, sec_val))
                block_indices.append(block_idx + max_ch)
                ch_name = ['R','G','B'][max_ch]
                print(f"  Block(col={col}, row={row}) {ch_name}通道采样={rgb_avg[max_ch]}，Snap候选={snap_val} (标准{COLOR_LEVELS[snap_val]})，次近候选={sec_val} (标准{COLOR_LEVELS[sec_val]})")
                
            print(f"  正在执行 {2**len(critical_blocks)} 种临界候选组合爆破...")
            found_combo = False
            original_nibbles = list(nibbles)
            
            for combo in product(*candidates_list):
                test_nibbles = list(original_nibbles)
                for idx, val in zip(block_indices, combo):
                    test_nibbles[idx] = val
                    
                h_nib = test_nibbles[12:12 + 256]
                h_bytes = nibbles_to_bytes(h_nib, 128)
                h_obj = parse_frame_header(h_bytes)
                if h_obj is None:
                    continue
                    
                p_nib_count = h_obj.payload_len * 2
                if len(test_nibbles) < 268 + p_nib_count:
                    continue
                p_nibbles = test_nibbles[268:268 + p_nib_count]
                p_bytes = nibbles_to_bytes(p_nibbles, h_obj.payload_len)
                
                if validate_frame(h_obj, p_bytes):
                    print(f"🎉 🎉 🎉 临界多变量爆破匹配成功！")
                    print(f"  修正后的临界 Block 色阶配置为：")
                    for c_block, final_val, c_idx in zip(critical_blocks, combo, block_indices):
                        ch_name = ['R','G','B'][c_idx % 3]
                        print(f"    Block(col={c_block['pos'][0]}, row={c_block['pos'][1]}) {ch_name}通道修正为: {final_val} (标准色阶: {COLOR_LEVELS[final_val]})")
                    found_combo = True
                    break
                    
            if not found_combo:
                print("❌ 临界多变量爆破仍未成功。说明可能还有其他偏差较小（如 4、5、6）的 Block 也发生了反向错判。")


if __name__ == "__main__":
    main()



