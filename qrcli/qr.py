"""A small QR Code encoder written from scratch on the Python standard library.

Implements byte-mode encoding for QR versions 1 to 20 at all four
error-correction levels, following ISO/IEC 18004: segment header, Reed-Solomon
error correction over GF(256), block interleaving, function patterns, masking
with penalty-based mask selection, and format/version information.

The public surface is intentionally tiny: :func:`encode` (and its text
convenience wrapper :func:`encode_text`) return a :class:`QrCode` whose module
matrix can be rendered by :mod:`qrcli.render` or consumed directly.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence

__all__ = ["Ecc", "QrCode", "QrTooLongError", "encode", "encode_text"]

MIN_VERSION = 1
MAX_VERSION = 20


class QrTooLongError(ValueError):
    """Raised when the payload does not fit in the maximum QR version."""


class Ecc(enum.Enum):
    """Error-correction level. The ordinal indexes the tables below."""

    LOW = 0
    MEDIUM = 1
    QUARTILE = 2
    HIGH = 3

    @property
    def format_bits(self) -> int:
        """The two bits identifying this level in the format information."""
        return (0b01, 0b00, 0b11, 0b10)[self.value]


# Error-correction codewords per block, indexed [ecc][version - 1].
# Values from the QR specification's error-correction tables (versions 1-20).
_ECC_CODEWORDS_PER_BLOCK = (
    (7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28),
    (10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26),
    (13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30),
    (17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28),
)

# Number of error-correction blocks, indexed [ecc][version - 1].
_NUM_EC_BLOCKS = (
    (1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8),
    (1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16),
    (1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20),
    (1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25),
)

# Penalty weights from the specification's mask evaluation rules.
_PENALTY_N1 = 3
_PENALTY_N2 = 3
_PENALTY_N3 = 40
_PENALTY_N4 = 10


# ---------------------------------------------------------------------------
# Galois field GF(256) arithmetic, primitive polynomial x^8+x^4+x^3+x^2+1.

def _build_gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 256
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    exp[255] = exp[0]
    return exp, log


_GF_EXP, _GF_LOG = _build_gf_tables()


def _gf_mul(x: int, y: int) -> int:
    if x == 0 or y == 0:
        return 0
    return _GF_EXP[_GF_LOG[x] + _GF_LOG[y] if _GF_LOG[x] + _GF_LOG[y] < 255
                   else _GF_LOG[x] + _GF_LOG[y] - 255]


def _reed_solomon_divisor(degree: int) -> list[int]:
    """Coefficients of the generator polynomial of the given degree."""
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(len(result)):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < len(result):
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _reed_solomon_remainder(data: Sequence[int], divisor: Sequence[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ---------------------------------------------------------------------------
# Bit buffer

class _BitBuffer:
    def __init__(self) -> None:
        self._bits: list[int] = []

    def __len__(self) -> int:
        return len(self._bits)

    def append(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def to_bytes(self) -> bytes:
        assert len(self._bits) % 8 == 0
        out = bytearray()
        for i in range(0, len(self._bits), 8):
            byte = 0
            for bit in self._bits[i:i + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return bytes(out)


# ---------------------------------------------------------------------------
# Capacity helpers

def _num_raw_data_modules(version: int) -> int:
    result = (16 * version + 128) * version + 64
    if version >= 2:
        num_align = version // 7 + 2
        result -= (25 * num_align - 10) * num_align - 55
        if version >= 7:
            result -= 36
    assert 208 <= result <= 13648
    return result


def _num_data_codewords(version: int, ecl: Ecc) -> int:
    return (_num_raw_data_modules(version) // 8
            - _ECC_CODEWORDS_PER_BLOCK[ecl.value][version - 1]
            * _NUM_EC_BLOCKS[ecl.value][version - 1])


def _char_count_bits(version: int) -> int:
    """Length field size for byte mode at the given version."""
    return 8 if version <= 9 else 16


def _alignment_pattern_positions(version: int) -> list[int]:
    if version == 1:
        return []
    num = version // 7 + 2
    step = (version * 4 + num * 2 + 1) // (num * 2 - 2) * 2
    size = version * 4 + 17
    result = [size - 7 - i * step for i in range(num - 1)] + [6]
    return list(reversed(result))


# ---------------------------------------------------------------------------
# Encoding

def encode(data: bytes | str, ecl: Ecc = Ecc.MEDIUM) -> QrCode:
    """Encode bytes (or text, UTF-8) into a :class:`QrCode`.

    Picks the smallest version from 1 to 20 that fits the payload.
    Raises :class:`QrTooLongError` if nothing fits.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    version = _fit_version(len(data), ecl)
    codewords = _encode_codewords(data, version, ecl)
    return _build_matrix(codewords, version, ecl)


def encode_text(text: str, ecl: Ecc = Ecc.MEDIUM) -> QrCode:
    return encode(text.encode("utf-8"), ecl)


def _fit_version(num_bytes: int, ecl: Ecc) -> int:
    for version in range(MIN_VERSION, MAX_VERSION + 1):
        capacity_bits = _num_data_codewords(version, ecl) * 8
        needed_bits = 4 + _char_count_bits(version) + num_bytes * 8
        if needed_bits <= capacity_bits:
            return version
    raise QrTooLongError(
        f"payload of {num_bytes} bytes does not fit in a version "
        f"{MAX_VERSION} QR code at {ecl.name} error correction"
    )


def _encode_codewords(data: bytes, version: int, ecl: Ecc) -> bytes:
    capacity_bits = _num_data_codewords(version, ecl) * 8
    bb = _BitBuffer()
    bb.append(0b0100, 4)  # byte mode indicator
    bb.append(len(data), _char_count_bits(version))
    for b in data:
        bb.append(b, 8)
    # Terminator and padding
    bb.append(0, min(4, capacity_bits - len(bb)))
    bb.append(0, -len(bb) % 8)
    pad_bytes = (0xEC, 0x11)
    i = 0
    while len(bb) < capacity_bits:
        bb.append(pad_bytes[i % 2], 8)
        i += 1
    return _add_ecc_and_interleave(bb.to_bytes(), version, ecl)


def _add_ecc_and_interleave(data: bytes, version: int, ecl: Ecc) -> bytes:
    num_blocks = _NUM_EC_BLOCKS[ecl.value][version - 1]
    ecc_len = _ECC_CODEWORDS_PER_BLOCK[ecl.value][version - 1]
    total_data = _num_data_codewords(version, ecl)
    assert len(data) == total_data
    short_block = total_data // num_blocks
    long_blocks = total_data % num_blocks
    divisor = _reed_solomon_divisor(ecc_len)

    blocks: list[bytes] = []
    ecc_blocks: list[bytes] = []
    offset = 0
    for i in range(num_blocks):
        block_len = short_block + (1 if i >= num_blocks - long_blocks else 0)
        block = data[offset:offset + block_len]
        offset += block_len
        blocks.append(block)
        ecc_blocks.append(bytes(_reed_solomon_remainder(block, divisor)))

    result = bytearray()
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                result.append(block[i])
    for i in range(ecc_len):
        for ecc in ecc_blocks:
            result.append(ecc[i])
    return bytes(result)


# ---------------------------------------------------------------------------
# Matrix construction

class QrCode:
    """An encoded QR symbol: a square matrix of dark/light modules."""

    def __init__(self, version: int, ecl: Ecc, mask: int,
                 modules: list[list[bool]]) -> None:
        self.version = version
        self.ecl = ecl
        self.mask = mask
        self._modules = modules

    @property
    def size(self) -> int:
        return self.version * 4 + 17

    def module(self, x: int, y: int) -> bool:
        """True if the module at (x, y) is dark. (0, 0) is the top-left."""
        return self._modules[y][x]

    @property
    def modules(self) -> list[list[bool]]:
        """Rows of the symbol, top to bottom, without the quiet zone."""
        return self._modules

    def __repr__(self) -> str:
        return (f"QrCode(version={self.version}, size={self.size}, "
                f"ecl={self.ecl.name}, mask={self.mask})")


def _build_matrix(codewords: bytes, version: int, ecl: Ecc) -> QrCode:
    size = version * 4 + 17
    modules = [[False] * size for _ in range(size)]
    is_function = [[False] * size for _ in range(size)]

    def set_function(x: int, y: int, dark: bool) -> None:
        if 0 <= x < size and 0 <= y < size:
            modules[y][x] = dark
            is_function[y][x] = True

    # Timing patterns
    for i in range(size):
        set_function(i, 6, i % 2 == 0)
        set_function(6, i, i % 2 == 0)

    # Finder patterns with separators
    for dx, dy in ((0, 0), (size - 7, 0), (0, size - 7)):
        for ddy in range(-1, 8):
            for ddx in range(-1, 8):
                xx, yy = dx + ddx, dy + ddy
                dark = (0 <= ddx <= 6 and ddy in (0, 6)) or \
                       (0 <= ddy <= 6 and ddx in (0, 6)) or \
                       (2 <= ddx <= 4 and 2 <= ddy <= 4)
                set_function(xx, yy, dark)

    # Alignment patterns
    positions = _alignment_pattern_positions(version)
    for i, a in enumerate(positions):
        for j, b in enumerate(positions):
            if not ((i == 0 and j == 0) or (i == 0 and j == len(positions) - 1)
                    or (i == len(positions) - 1 and j == 0)):
                for ddy in range(-2, 3):
                    for ddx in range(-2, 3):
                        set_function(b + ddx, a + ddy,
                                     max(abs(ddx), abs(ddy)) != 1)

    # Reserve the format information cells (filled after masking). These are
    # exactly the cells _draw_format writes; note (8, 6) and (6, 8) are
    # timing-pattern cells and must NOT be reserved.
    format_cells = (
        [(8, i) for i in (0, 1, 2, 3, 4, 5, 7, 8)]
        + [(i, 8) for i in (0, 1, 2, 3, 4, 5, 7)]
        + [(size - 1 - i, 8) for i in range(8)]
        + [(8, size - 1 - i) for i in range(7)]
    )
    for x, y in format_cells:
        set_function(x, y, False)
    set_function(8, size - 8, True)  # dark module

    # Version information (versions 7+)
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = version << 12 | rem
        for i in range(18):
            dark = ((bits >> i) & 1) != 0
            set_function(size - 11 + i % 3, i // 3, dark)
            set_function(i // 3, size - 11 + i % 3, dark)

    # Place data bits in the zig-zag, trying every mask
    best: QrCode | None = None
    best_penalty: int | None = None
    for mask in range(8):
        trial = [row[:] for row in modules]
        _place_data(trial, is_function, codewords, mask)
        _draw_format(trial, size, ecl, mask)
        penalty = _penalty_score(trial, size)
        if best_penalty is None or penalty < best_penalty:
            best_penalty = penalty
            best = QrCode(version, ecl, mask, trial)
    assert best is not None
    return best


def _place_data(modules: list[list[bool]], is_function: list[list[bool]],
                codewords: bytes, mask: int) -> None:
    size = len(modules)
    bit_count = len(codewords) * 8
    bit_index = 0
    right = size - 1
    upward = True
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            y = size - 1 - vert if upward else vert
            for j in range(2):
                x = right - j
                if not is_function[y][x]:
                    dark = False
                    if bit_index < bit_count:
                        dark = ((codewords[bit_index >> 3]
                                 >> (7 - (bit_index & 7))) & 1) != 0
                        bit_index += 1
                    if _mask_bit(mask, x, y):
                        dark = not dark
                    modules[y][x] = dark
        right -= 2
        upward = not upward


def _mask_bit(mask: int, x: int, y: int) -> bool:
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (x // 3 + y // 2) % 2 == 0
    if mask == 5:
        return (x * y) % 2 + (x * y) % 3 == 0
    if mask == 6:
        return ((x * y) % 2 + (x * y) % 3) % 2 == 0
    return ((x + y) % 2 + (x * y) % 3) % 2 == 0


def _draw_format(modules: list[list[bool]], size: int, ecl: Ecc,
                 mask: int) -> None:
    data = ecl.format_bits << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (data << 10 | rem) ^ 0x5412

    def bit(i: int) -> bool:
        return ((bits >> i) & 1) != 0

    # First copy (around the top-left finder)
    for i in range(6):
        modules[i][8] = bit(i)
    modules[7][8] = bit(6)
    modules[8][8] = bit(7)
    modules[8][7] = bit(8)
    for i in range(9, 15):
        modules[8][14 - i] = bit(i)
    # Second copy (split by the other two finders)
    for i in range(8):
        modules[8][size - 1 - i] = bit(i)
    for i in range(8, 15):
        modules[size - 15 + i][8] = bit(i)
    modules[size - 8][8] = True  # dark module


# ---------------------------------------------------------------------------
# Mask penalty evaluation (ISO/IEC 18004 rules N1-N4). Adjacent-run and
# finder-pattern penalties are computed in a single pass per row/column using
# a short run-length history, the same evaluation the specification describes.

def _penalty_score(modules: list[list[bool]], size: int) -> int:
    score = 0

    def line_penalty(line: list[bool]) -> int:
        penalty = 0
        run_color = False
        run_len = 0
        history = [0] * 7  # most recent run first

        def add_history(length: int) -> None:
            if history[0] == 0:
                length += size  # light border before the line starts
            history.pop()
            history.insert(0, length)

        def count_patterns() -> int:
            n = history[1]
            core = n > 0 and history[2] == n and history[4] == n \
                and history[5] == n and history[3] == n * 3
            return (1 if core and history[0] >= n * 4 and history[6] >= n
                    else 0) \
                + (1 if core and history[6] >= n * 4 and history[0] >= n
                   else 0)

        for cell in line:
            if cell == run_color:
                run_len += 1
                if run_len == 5:
                    penalty += _PENALTY_N1
                elif run_len > 5:
                    penalty += 1
            else:
                add_history(run_len)
                if not run_color:
                    penalty += count_patterns() * _PENALTY_N3
                run_color = cell
                run_len = 1
        # Terminate the line: close a trailing dark run, then add the
        # virtual light border run after the line ends.
        if run_color:
            add_history(run_len)
            run_len = 0
        add_history(run_len + size)
        penalty += count_patterns() * _PENALTY_N3
        return penalty

    for y in range(size):
        score += line_penalty(modules[y])
    for x in range(size):
        score += line_penalty([modules[y][x] for y in range(size)])

    # 2x2 blocks of the same colour
    for y in range(size - 1):
        for x in range(size - 1):
            c = modules[y][x]
            if modules[y][x + 1] == c and modules[y + 1][x] == c \
                    and modules[y + 1][x + 1] == c:
                score += _PENALTY_N2

    # Balance of dark and light modules: smallest k >= 0 such that the
    # dark ratio lies within (50 +/- 5k) percent, times N4.
    dark = sum(sum(row) for row in modules)
    total = size * size
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += k * _PENALTY_N4
    return score
