#!/usr/bin/env python3
"""Receive frames from PNGs or live screen capture."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pixelferry.receiver import decode_from_pngs, unpack_to_directory, receive_from_screen


def cmd_from_pngs(args):
    """Decode frames from a directory of PNGs."""
    if not os.path.isdir(args.frames):
        print(f"Error: {args.frames} is not a directory")
        sys.exit(1)

    print("Decoding frames...")
    package_bytes, state = decode_from_pngs(args.frames)

    if package_bytes is None:
        if state:
            print(f"Incomplete: {state.received_count}/{state.total_frames} frames")
            missing = state.missing_indices
            if missing:
                print(f"Missing frames: {missing[:20]}...")
        else:
            print("No valid frames found.")
        sys.exit(1)

    if args.out_package:
        with open(args.out_package, "wb") as f:
            f.write(package_bytes)
        print(f"Package saved to {args.out_package}")

    if args.out_repo:
        unpack_to_directory(package_bytes, args.out_repo)
    elif not args.out_package:
        out = "received_package.pxf"
        with open(out, "wb") as f:
            f.write(package_bytes)
        print(f"Package saved to {out}")


def cmd_live(args):
    """Capture screen region in real-time and decode frames."""
    region = (args.x, args.y, args.width, args.height)

    pkg = receive_from_screen(
        region=region,
        output_path=args.out_package,
        output_dir=args.out_repo,
        fps=args.fps,
        max_cycles=args.max_cycles,
    )

    if pkg is None:
        print("Failed to receive complete package.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="PixelFerry: Receive frames")
    sub = parser.add_subparsers(dest="command")

    # From PNGs
    png_cmd = sub.add_parser("from-pngs", help="Decode from PNG files")
    png_cmd.add_argument("--frames", required=True, help="Directory of frame PNGs")
    png_cmd.add_argument("--out-package", default=None, help="Save package.pxf")
    png_cmd.add_argument("--out-repo", default=None, help="Unpack to directory")

    # Live screen capture
    live_cmd = sub.add_parser("live", help="Capture from screen in real-time")
    live_cmd.add_argument("--x", type=int, default=0, help="Region left (default: 0)")
    live_cmd.add_argument("--y", type=int, default=0, help="Region top (default: 0)")
    live_cmd.add_argument("--width", type=int, default=640, help="Region width (default: 640)")
    live_cmd.add_argument("--height", type=int, default=360, help="Region height (default: 360)")
    live_cmd.add_argument("--fps", type=float, default=8.0, help="Capture FPS (default: 8)")
    live_cmd.add_argument("--max-cycles", type=int, default=50, help="Max cycles (default: 50)")
    live_cmd.add_argument("--out-package", default=None, help="Save package.pxf")
    live_cmd.add_argument("--out-repo", default=None, help="Unpack to directory")

    args = parser.parse_args()

    if args.command == "from-pngs":
        cmd_from_pngs(args)
    elif args.command == "live":
        cmd_live(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
