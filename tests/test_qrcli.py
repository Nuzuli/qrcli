"""Tests for qrcli. The golden matrix below was produced by the encoder in
this package and cross-verified module-by-module against an independent,
widely used QR implementation, plus decode-back checks with a real QR
detector: if the matrix ever changes, something regressed.
"""

import pytest

from qrcli import Ecc, QrTooLongError, encode, encode_text
from qrcli.cli import _wifi_payload, main
from qrcli.render import to_png, to_svg, to_terminal

GOLDEN_HELLO_WORLD_M = """\
#######.##..#.#######
#.....#....#..#.....#
#.###.#..#.#..#.###.#
#.###.#.#..#..#.###.#
#.###.#.###.#.#.###.#
#.....#.#..#..#.....#
#######.#.#.#.#######
........#..##........
#...#.######.#####..#
...#....#.###....####
..######..##.##.#..#.
#####...##...#.......
#####.#.#.#.#.##..##.
........#.#.####.#.##
#######.###.#.#.##.#.
#.....#..#.###.##..##
#.###.#.##.#.##...##.
#.###.#..#..#...##.##
#.###.#..###...###...
#.....#....#.#.......
#######.#########.#.#"""


def render_text(qr):
    return "\n".join("".join("#" if c else "." for c in row)
                     for row in qr.modules)


def test_golden_hello_world():
    qr = encode_text("HELLO WORLD", Ecc.MEDIUM)
    assert qr.version == 1
    assert qr.size == 21
    assert render_text(qr) == GOLDEN_HELLO_WORLD_M


def test_size_grows_with_payload():
    assert encode(b"a").size == 21
    assert encode(b"x" * 100).size > 21
    big = encode(b"x" * 800, Ecc.LOW)
    assert big.version <= 20
    assert big.size == big.version * 4 + 17


def test_all_ecc_levels_encode():
    for ecc in Ecc:
        qr = encode(b"https://example.com", ecc)
        assert qr.ecl is ecc


def test_unicode_payload():
    qr = encode_text("Halo, dunia! ✓")
    assert qr.size >= 21


def test_too_long_raises():
    with pytest.raises(QrTooLongError):
        encode(b"x" * 5000, Ecc.HIGH)


def test_finder_patterns_present():
    qr = encode(b"test")
    for x0, y0 in ((0, 0), (qr.size - 7, 0), (0, qr.size - 7)):
        assert qr.module(x0, y0) is True
        assert qr.module(x0 + 6, y0) is True
        assert qr.module(x0 + 3, y0 + 3) is True
        assert qr.module(x0 + 1, y0 + 1) is False


def test_dark_module_set():
    qr = encode(b"test")
    assert qr.module(8, qr.size - 8) is True


def test_wifi_payload():
    assert _wifi_payload("Home", "secret", "WPA", False) == \
        "WIFI:T:WPA;S:Home;P:secret;;"
    assert _wifi_payload("Cafe", None, "nopass", False) == \
        "WIFI:T:nopass;S:Cafe;;"
    assert _wifi_payload("A;B", "p:ss", "WPA", True) == \
        "WIFI:T:WPA;S:A\\;B;P:p\\:ss;H:true;;"


def test_render_terminal_shape():
    qr = encode(b"https://example.com")
    text = to_terminal(qr, border=2)
    lines = text.split("\n")
    assert all(len(line) == qr.size + 4 for line in lines)
    assert "█" in text


def test_render_png_header():
    qr = encode(b"https://example.com")
    png = to_png(qr, scale=3, border=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"IHDR" in png[:32]
    assert png[-12:-8] == b"\x00\x00\x00\x00" or b"IEND" in png[-12:]


def test_render_svg():
    qr = encode(b"https://example.com")
    svg = to_svg(qr, scale=5, border=2)
    assert svg.startswith("<svg")
    assert "<path" in svg
    assert svg.endswith("</svg>")


def test_cli_terminal(capsys):
    assert main(["hello"]) == 0
    out = capsys.readouterr().out
    assert "█" in out


def test_cli_writes_png(tmp_path):
    target = tmp_path / "code.png"
    assert main(["hello", "-o", str(target)]) == 0
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_cli_writes_svg(tmp_path):
    target = tmp_path / "code.svg"
    assert main(["hello", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("<svg")


def test_cli_wifi(tmp_path):
    target = tmp_path / "wifi.png"
    assert main(["wifi", "--ssid", "Home", "--password", "secret",
                 "-o", str(target)]) == 0
    assert target.exists()


def test_cli_too_long(capsys):
    assert main(["x" * 5000]) == 1
    assert "qrcli:" in capsys.readouterr().err
