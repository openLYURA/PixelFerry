#!/usr/bin/env python3
"""Unpack a .pxf package to a directory."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pixelferry.package import unpack_package_file


def main():
    parser = argparse.ArgumentParser(description="PixelFerry: Unpack .pxf package")
    parser.add_argument("--package", required=True, help="Path to .pxf file")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    if not os.path.isfile(args.package):
        print(f"Error: {args.package} not found")
        sys.exit(1)

    written = unpack_package_file(args.package, args.out, overwrite=args.overwrite)
    print(f"Unpacked {len(written)} files to {args.out}")


if __name__ == "__main__":
    main()
