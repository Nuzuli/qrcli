"""Renderers for :class:`qrcli.qr.QrCode`: terminal text, PNG, and SVG.

All three are written directly on the standard library — the PNG writer uses
:mod:`zlib` and :mod:`struct`; the SVG is plain text; the terminal renderer
uses Unicode half-block characters so a QR printed to a terminal stays square.
"""

from __future__ import annotations

import struct
import zlib

from .qr import QrCode

__all__ = ["to_png", "to_svg", "to_terminal"]


def _padded_grid(qr: QrCode, border: int) -> list[list[bool]]:
    """The module matrix with a light quiet-zone border around it."""
    size = qr.size
    width = size + border * 2
    grid = [[False] * width for _ in range(width)]
    for y in range(size):
        for x in range(size):
            grid[y + border][x + border] = qr.module(x, y)
    return grid


def to_terminal(qr: QrCode, border: int = 2) -> str:
    """Render the QR as text using half-block characters.

    Two vertical modules are packed into one character cell, so the output
    looks square in a monospace terminal. Dark modules are the filled part.
    """
    grid = _padded_grid(qr, border)
    if len(grid) % 2 == 1:
        grid.append([False] * len(grid))
    lines = []
    for y in range(0, len(grid), 2):
        chars = []
        for top, bottom in zip(grid[y], grid[y + 1]):
            if top and bottom:
                chars.append("█")
            elif top:
                chars.append("▀")
            elif bottom:
                chars.append("▄")
            else:
                chars.append(" ")
        lines.append("".join(chars))
    return "\n".join(lines)


def to_png(qr: QrCode, scale: int = 10, border: int = 4) -> bytes:
    """Render the QR as an 8-bit grayscale PNG and return the file bytes."""
    grid = _padded_grid(qr, border)
    width = len(grid) * scale
    raw = bytearray()
    for row in grid:
        line = bytearray()
        for dark in row:
            line.extend(b"\x00" * scale if dark else b"\xff" * scale)
        for _ in range(scale):
            raw.append(0)  # filter type: none
            raw.extend(line)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", binascii_crc(tag, data)))

    ihdr = struct.pack(">IIBBBBB", width, width, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def binascii_crc(tag: bytes, data: bytes) -> int:
    return zlib.crc32(tag + data) & 0xFFFFFFFF


def to_svg(qr: QrCode, scale: int = 10, border: int = 4) -> str:
    """Render the QR as a standalone SVG document.

    Dark modules are drawn as one path of horizontal runs, which keeps the
    file small even for dense version-20 symbols.
    """
    grid = _padded_grid(qr, border)
    width = len(grid)
    parts = []
    for y, row in enumerate(grid):
        x = 0
        while x < width:
            if row[x]:
                start = x
                while x < width and row[x]:
                    x += 1
                run = x - start
                parts.append(f"M{start} {y}h{run}v1H{start}z")
            else:
                x += 1
    path = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width * scale}" height="{width * scale}" '
        f'viewBox="0 0 {width} {width}">'
        f'<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<path d="{path}" fill="#000000"/></svg>'
    )
