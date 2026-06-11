<div align="center">

```
 ██▓███ ▓█████ ██▒   █▓▓█████  ██▀███   ██▓ ██▓     ▒█████
▓██░  ██▒▓█   ▀▓██░   █▒▓█   ▀ ▓██ ▒ ██▒▓██▒▓██▒    ▒██▒  ██▒
▓██░ ██▓▒▒███   ▓██  █▒░▒███   ▓██ ░▄█ ▒▒██▒▒██░    ▒██░  ██▒
▒██▄█▓▒ ▒▒▓█  ▄  ▒██ █░░▒▓█  ▄ ▒██▀▀█▄  ░██░▒██░    ▒██   ██░
▒██▒ ░  ░░▒████▒  ▒▀█░  ░▒████▒░██▓ ▒██▒░██░░██████▒░ ████▓▒░
▒▓▒░ ░  ░░░ ▒░ ░  ░ ▐░  ░░ ▒░ ░░ ▒▓ ░▒▓░░▓  ░ ▒░▓  ░░ ▒░▒░▒░
```

**Visual data transfer through RGB color blocks**

`#FF0000` `#00FF00` `#0000FF` `#FFFF00`

English | [中文](README_zh.md)

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9+-yellow.svg)](https://python.org)

</div>

---

## What is PixelFerry?

PixelFerry encodes binary data into RGB color blocks displayed on screen. A receiver captures the display, detects corner markers, applies perspective correction, and decodes the original data — all without network, clipboard, or shared storage.

Each pixel block's R/G/B channel encodes **4 bits** using 16 discrete color levels:

```
 ┌──────────────────────────────────────────────────────┐
 │  Level:   0    1    2    3  ···   12   13   14   15  │
 │  Value:   8   24   40   56  ···  200  216  232  248  │
 │                                                      │
 │  3 nibbles/block × 12 bits = 1.5 bytes per block     │
 │  1200×800 window → 46×29 grid → ~9 KB per frame      │
 └──────────────────────────────────────────────────────┘
```

## How It Works

```
  SENDER                                 RECEIVER
 ┌────────────────────┐                 ┌────────────────────┐
 │  repo directory     │                 │  screen capture     │
 │        │            │                 │        │            │
 │  ┌─────▼─────┐      │                 │  ┌─────▼─────┐      │
 │  │  package   │      │   ┌─────────┐  │  │  locate    │      │
 │  │  (.pxf)    │      │   │ ░░▓▓░░  │  │  │  corners   │      │
 │  └─────┬─────┘      │   │ ▓▓░░▓▓  │  │  └─────┬─────┘      │
 │  ┌─────▼─────┐      │   │ ░░▓▓░░  │  │  ┌─────▼─────┐      │
 │  │  split     │      │   │ ▓▓░░▓▓  │  │  │  decode    │      │
 │  │  frames    │      │   │ ░░▓▓░░  │  │  │  blocks    │      │
 │  └─────┬─────┘      │   └─────────┘  │  └─────┬─────┘      │
 │  ┌─────▼─────┐      │    RGB blocks   │  ┌─────▼─────┐      │
 │  │  display   │──────│──────────────→ │  │  verify    │      │
 │  │  window    │ QR   │   screenshot   │  │  SHA-256   │      │
 │  └───────────┘      │                 │  └─────┬─────┘      │
 │                     │                 │  ┌─────▼─────┐      │
 │                     │                 │  │  unpack    │      │
 │                     │                 │  │  repo      │      │
 └────────────────────┘                 └────────────────────┘
```

1. **Sender** packs a repo into `.pxf`, splits into chunks, encodes each as an RGB frame, displays in a tkinter window
2. **Receiver** captures the screen, detects 4 corner markers (Y/R/G/B), applies perspective transform, decodes nibble-encoded blocks
3. **QR code** carries session metadata (session ID, frame count, SHA-256, repo name) for zero-config pairing

## Quick Start

```bash
# Install
pip install -e .

# Send a repo
pixelferry send /path/to/repo

# Receive (on another machine, viewing the sender window)
pixelferry receive
```

### With aliases

```bash
pixelferry config set myproject /home/user/myproject
pixelferry send myproject --fps 5
```

## Technical Reference

<table>
<tr>
<td>

**Frame Layout**

| Parameter | Value |
|---|---|
| Window | 1200 × 800 px |
| Block Size | 24 × 24 px |
| Grid | 46 cols × 29 rows |
| Header | 128 bytes |
| Payload | ~9 KB / frame |

</td>
<td>

**Color Encoding**

| Nibble | Channel Value |
|---|---|
| `0x0` | 8 |
| `0x1` | 24 |
| `0x2` | 40 |
| `⋮` | `⋮` |
| `0xE` | 232 |
| `0xF` | 248 |

</td>
</tr>
</table>

### Corner Markers

Four colored markers in the corners enable automatic perspective correction:

```
 ┌──────────────────────────────────────────┐
 │ ■■■■■                                 ■■■│  Yellow → Red
 │ ■■■■■                                 ■■■│
 │                                          │
 │                                          │
 │ ■■■■■                                 ■■■│  Green → Blue
 │ ■■■■■                                 ■■■│
 └──────────────────────────────────────────┘
```

| Corner | Color | RGB |
|---|---|---|
| Top-left | Yellow | `(255, 255, 0)` |
| Top-right | Red | `(255, 0, 0)` |
| Bottom-left | Green | `(0, 255, 0)` |
| Bottom-right | Blue | `(0, 0, 255)` |

### Frame Header (128 bytes)

```
 0                   4   5    7          23         31         35        67        99      128
 ├───────────────────┬───┬────┬──────────┬──────────┬──────────┬─────────┬─────────┬───────┤
 │  magic "PXF1"     │ver│hlen│ session_id (16B)    │frame_idx │total    │payload  │ SHA   │
 │                   │   │    │          │          │          │_len     │_sha256  │ pkg   │
 └───────────────────┴───┴────┴──────────┴──────────┴──────────┴─────────┴─────────┴───────┘
```

## Security

```
 ✓  Reads only user-specified directories
 ✓  Excludes .git, node_modules, .venv by default
 ✓  Prevents path traversal (.. and absolute paths rejected)
 ✓  SHA-256 per-frame and per-package integrity verification
 ✗  No network connections
 ✗  No third-party services
 ✗  No clipboard access
```

## Platform Support

| Platform | Mode | Notes |
|---|---|---|
| **Windows** | Live capture | Full support with screen capture via PrintWindow API |
| **macOS / Linux** | PNG pipeline | Generate frames → transfer → decode |

## Project Structure

```
pixelferry/
├── codec.py           # RGB nibble encoding/decoding
├── framing.py         # Frame header construction & validation
├── manifest.py        # Repository manifest builder & parser
├── package.py         # .pxf package build & unpack
├── sender.py          # Frame generation & display window
├── receiver.py        # Receive loop & frame collection
├── corner_detect.py   # Corner marker detection & perspective transform
├── qr_detect.py       # QR code detection & session init
├── capture.py         # Screen capture & sender window location
├── config.py          # Path alias configuration
├── verify.py          # Reconstruction integrity verification
└── utils.py           # Hashing, path safety, file detection
```

## License

[Apache 2.0](LICENSE)
