#!/usr/bin/env python3
"""Generate frame PNGs from a package and optionally play them."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pixelferry.sender import generate_frame_images, play_frames_window, save_frame_pngs


def main():
    parser = argparse.ArgumentParser(description="PixelFerry: Generate/send frames")
    sub = parser.add_subparsers(dest="command")

    # Save PNGs
    save_cmd = sub.add_parser("save", help="Save frames as PNGs")
    save_cmd.add_argument("--repo", required=True, help="Repository path")
    save_cmd.add_argument("--out", required=True, help="Output directory for PNGs")

    # Play in window
    play_cmd = sub.add_parser("play", help="Play frames in window")
    play_cmd.add_argument("--repo", required=True, help="Repository path")
    play_cmd.add_argument("--fps", type=float, default=5, help="Frames per second")

    args = parser.parse_args()

    if args.command == "save":
        save_frame_pngs(args.repo, args.out)
    elif args.command == "play":
        frames, sha, sid = generate_frame_images(args.repo)
        print(f"Package SHA-256: {sha}")
        print("Playing frames... Press Ctrl+C or close window to stop.")
        play_frames_window(frames, fps=args.fps)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
