#!/usr/bin/env python3
"""Build a .pxf package from a repository directory."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pixelferry.package import build_package, get_package_sha256


def main():
    parser = argparse.ArgumentParser(description="PixelFerry: Build package from repo")
    parser.add_argument("--repo", required=True, help="Path to repository")
    parser.add_argument("--out", required=True, help="Output .pxf file path")
    args = parser.parse_args()

    if not os.path.isdir(args.repo):
        print(f"Error: {args.repo} is not a directory")
        sys.exit(1)

    print(f"Building package from {args.repo}...")
    package_bytes = build_package(args.repo, args.out)
    sha = get_package_sha256(package_bytes)
    print(f"Package written to {args.out}")
    print(f"Size: {len(package_bytes)} bytes")
    print(f"SHA-256: {sha}")


if __name__ == "__main__":
    main()
