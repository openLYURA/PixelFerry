# PixelFerry

An RGB-based visual data channel for research on screen-coupled data transfer.

English | [中文](README_zh.md)

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## What is PixelFerry?

PixelFerry is a research tool that explores visual data transfer via RGB color blocks. It encodes binary data into colored blocks displayed in a window, which can be captured by screen recording or screenshot tools and decoded back to the original data.

The core idea: each pixel block's R/G/B channel encodes 4 bits of data using 16 discrete color levels, achieving ~9 KB payload per frame at 1200x800 resolution.

## How It Works

```
Sender                              Receiver
┌─────────────────┐                ┌─────────────────┐
│  input data     │                │  screen capture  │
│       ↓         │                │       ↓          │
│  build_package  │                │  decode_frame    │
│       ↓         │                │       ↓          │
│  split chunks   │                │  collect chunks  │
│       ↓         │                │       ↓          │
│  encode frames  │                │  merge + verify  │
│       ↓         │   visual       │       ↓          │
│  display window │ ──channel──→   │  unpack          │
│  (loop playback)│  (screenshot)  │       ↓          │
└─────────────────┘                │  output data     │
                                   └─────────────────┘
```

1. **Sender** encodes data into RGB color blocks and displays them in a tkinter window
2. **Receiver** captures the screen, detects corner markers, decodes blocks via perspective correction
3. QR code protocol handles session initialization (frame count, checksum, data name)

## Installation

```bash
pip install -e .
```

Or install dependencies manually:

```bash
pip install Pillow numpy opencv-python qrcode[pil]
```

### Optional Dependencies

```bash
# Better QR code detection
pip install pyzbar

# Cross-platform screen capture
pip install mss
```

## Usage

### Send Data

```bash
pixelferry send /path/to/data

# With custom frame rate
pixelferry send /path/to/data --fps 3
```

### Receive Data

```bash
pixelferry receive

# Specify output directory
pixelferry receive /path/to/output
```

### Configuration

```bash
# Add a path alias
pixelferry config set myrepo /path/to/data

# List configured paths
pixelferry config

# Remove an alias
pixelferry config rm myrepo
```

## Technical Details

| Parameter | Value |
|---|---|
| Window Size | 1200x800 px |
| Block Size | 24x24 px |
| Grid | 46 cols x 29 rows |
| Color Levels | 16 per channel (values: 8, 24, ..., 248) |
| Bits per Block | 12 (4 bits x 3 channels) |
| Payload per Frame | ~9 KB |
| Header | 128 bytes (magic, session, frame index, SHA-256 checksums) |

### Color Encoding

Each RGB channel uses 16 discrete levels with 16-unit spacing. The decoder snaps captured values to the nearest level with a tolerance of 7 units. This provides resilience against minor color shifts.

```
Level:  0    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15
Value:  8   24   40   56   72   88  104  120  136  152  168  184  200  216  232  248
```

### Corner Markers

Four colored markers in the corners enable perspective correction:

| Corner | Color | Purpose |
|---|---|---|
| Top-left | Yellow (255,255,0) | Orientation anchor |
| Top-right | Red (255,0,0) | Orientation |
| Bottom-left | Green (0,255,0) | Orientation |
| Bottom-right | Blue (0,0,255) | Orientation |

## Security

- Only reads user-specified directories
- Excludes `.git`, `node_modules`, `.venv`, and other large directories by default
- Prevents path traversal attacks (`..` and absolute paths rejected)
- No network connections, no third-party services

## Platform Support

- **Windows**: Full support with screen capture
- **macOS/Linux**: PNG-based pipeline (generate frames, transfer via other means, decode)

## License

Apache 2.0 - See [LICENSE](LICENSE) for details.
