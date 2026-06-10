"""PixelFerry CLI: visual data transfer through remote desktop screenshots.

Usage:
    python -m pixelferry send <repo_path_or_alias>
    python -m pixelferry receive
    python -m pixelferry config
"""

import sys
import os

# DPI awareness before any GUI import (HiDPI support)
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from .config import load_config, resolve_repo, save_config
from .constants import DEFAULT_SEND_FPS, DEFAULT_RECV_FPS


def cmd_send(args):
    """Send a repository: build package, display QR code, then play frames."""
    fps = DEFAULT_SEND_FPS
    if "--fps" in args:
        try:
            idx = args.index("--fps")
            fps = float(args[idx + 1])
            args.pop(idx + 1)
            args.pop(idx)
        except Exception:
            pass

    if not args:
        print("用法: pixelferry send <仓库路径或alias> [--fps 帧率]")
        print("别名列表:")
        cfg = load_config()
        for alias, path in cfg["repos"].items():
            print(f"  {alias} → {path}")
        sys.exit(1)

    spec = args[0]
    repo_path = resolve_repo(spec)
    repo_name = os.path.basename(repo_path)

    print(f"仓库: {repo_name} ({repo_path})")

    # Build package
    print("正在打包...")
    from .package import build_package
    pkg = build_package(repo_path, output_path=None)
    pkg_size = len(pkg)

    from .sender import generate_frame_images
    frames, pkg_sha, session_id = generate_frame_images(repo_path, repo_name=repo_name)

    print(f"包大小: {pkg_size:,} 字节 ({pkg_size/1024:.1f} KB)")
    print(f"帧数: {len(frames)}")
    print(f"预计耗时: {len(frames) / fps:.1f} 秒 ({fps} FPS)")
    print()
    print("窗口已打开，显示二维码。")
    print("接收端扫描二维码后，点击「开始发送」按钮。")

    # Show QR code first, then play frames (blocks until window is closed)
    from .sender import play_qr_then_frames
    play_qr_then_frames(
        frames, session_id, len(frames), pkg_sha, repo_name,
        fps=fps, title=f"PixelFerry - {repo_name}",
    )

    print("发送结束。")


def cmd_receive(args):
    """Receive: detect QR code on selected screen, capture frames."""
    fps = DEFAULT_RECV_FPS
    if "--fps" in args:
        try:
            idx = args.index("--fps")
            fps = float(args[idx + 1])
            args.pop(idx + 1)
            args.pop(idx)
        except Exception:
            pass

    from .receiver import receive_from_screen, wait_for_qr
    from .region_selector import select_region

    print("提示：请拖动并调整弹出的绿色透明窗口，使其覆盖云电脑中的发送端窗口。")
    print("调整完成后，点击窗口内的「OK」按钮（或按 Enter 键）确认。")
    region = select_region()
    if region is None:
        print("已取消。")
        sys.exit(0)

    print(f"\n捕获区域: {region[2]}x{region[3]} 位置({region[0]}, {region[1]})")

    output_dir = args[0] if args else None
    if output_dir is None:
        output_dir = os.path.join(os.getcwd(), "repo")

    print(f"输出根目录: {output_dir}")

    # Wait for QR code from sender
    qr_data = wait_for_qr(region, verbose=True)
    if qr_data is None:
        print("已取消。")
        sys.exit(0)

    # Show "Start Receiving" confirmation window
    if not _wait_for_start(region):
        print("已取消。")
        sys.exit(0)

    print("开始捕获...\n")

    pkg = receive_from_screen(
        region=region,
        output_dir=output_dir,
        fps=fps,
        max_cycles=300,
        verbose=True,
        qr_data=qr_data,
    )

    if pkg is not None:
        print("\n接收完成！")
    else:
        print("\n接收失败或超时。")


def _wait_for_start(region):
    """Show a small confirmation window. Returns True when user clicks Start."""
    import tkinter as tk

    left, top, width, height = region
    root = tk.Tk()
    root.title("PixelFerry")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#1e1e1e")

    # Position near the selected region (right side)
    win_w, win_h = 220, 90
    win_x = left + width + 12
    win_y = top
    root.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")

    result = {"started": False}

    lbl = tk.Label(
        root, text=f"区域: {width}x{height}",
        fg="white", bg="#1e1e1e", font=("Consolas", 11),
    )
    lbl.pack(pady=(12, 6))

    def on_start():
        result["started"] = True
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = tk.Frame(root, bg="#1e1e1e")
    btn_frame.pack(pady=(0, 10))

    start_btn = tk.Button(
        btn_frame, text="开始接收 (Enter)", command=on_start,
        bg="#2d8a2d", fg="white", font=("Microsoft YaHei", 10),
        width=14, relief="flat", cursor="hand2",
    )
    start_btn.pack()

    root.bind("<Return>", lambda e: on_start())
    root.bind("<Escape>", lambda e: on_cancel())

    root.mainloop()
    return result["started"]


def cmd_config(args):
    """Show or edit config."""
    cfg = load_config()

    if not args:
        print("当前配置:")
        if cfg["repos"]:
            for alias, path in cfg["repos"].items():
                exists = "✓" if os.path.isdir(path) else "✗"
                print(f"  {alias} → {path} {exists}")
        else:
            print("  (空)")
        print()
        print("用法:")
        print("  pixelferry config set <alias> <path>   添加/更新仓库")
        print("  pixelferry config rm <alias>           删除仓库")
        return

    action = args[0]
    if action == "set" and len(args) >= 3:
        alias = args[1]
        path = os.path.abspath(args[2])
        cfg["repos"][alias] = path
        save_config(cfg)
        print(f"已设置: {alias} → {path}")
    elif action == "rm" and len(args) >= 2:
        alias = args[1]
        if alias in cfg["repos"]:
            del cfg["repos"][alias]
            save_config(cfg)
            print(f"已删除: {alias}")
        else:
            print(f"别名 '{alias}' 不存在")
    else:
        print("用法: pixelferry config set/rm ...")


def main():
    if len(sys.argv) < 2:
        print("PixelFerry - 像素摆渡")
        print()
        print("用法:")
        print("  pixelferry send <路径或alias>   发送仓库")
        print("  pixelferry receive              接收仓库")
        print("  pixelferry config               管理配置")
        return

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    commands = {
        "send": cmd_send,
        "receive": cmd_receive,
        "recv": cmd_receive,
        "config": cmd_config,
    }

    if cmd in commands:
        commands[cmd](rest)
    else:
        print(f"未知命令: {cmd}")
        print("可用命令: send, receive, config")


if __name__ == "__main__":
    main()
