"""Command-line interface for qrcli.

Usage:
    qrcli "https://example.com"            print the QR in the terminal
    qrcli "Hello" -o hello.png             save a PNG (or .svg)
    qrcli wifi --ssid Home --password s3cret   Wi-Fi QR: scan to join

Everything runs locally and offline; the payload never leaves the machine.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .qr import Ecc, QrTooLongError, encode
from .render import to_png, to_svg, to_terminal

_ECC_LEVELS = {
    "L": Ecc.LOW,
    "M": Ecc.MEDIUM,
    "Q": Ecc.QUARTILE,
    "H": Ecc.HIGH,
}


def _wifi_payload(ssid: str, password: str | None, security: str,
                  hidden: bool) -> str:
    def esc(text: str) -> str:
        return "".join("\\" + ch if ch in '\\;,:"' else ch for ch in text)

    parts = [f"WIFI:T:{security};S:{esc(ssid)};"]
    if security != "nopass":
        parts.append(f"P:{esc(password or '')};")
    if hidden:
        parts.append("H:true;")
    parts.append(";")
    return "".join(parts)


def _emit(payload: str, args: argparse.Namespace) -> int:
    try:
        qr = encode(payload, _ECC_LEVELS[args.ecc])
    except QrTooLongError as exc:
        print(f"qrcli: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        print(to_terminal(qr, border=args.border))
        return 0
    fmt = args.format
    if fmt is None:
        fmt = "svg" if args.output.lower().endswith(".svg") else "png"
    if fmt == "png":
        data: bytes = to_png(qr, scale=args.scale, border=args.border)
        with open(args.output, "wb") as fh:
            fh.write(data)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(to_svg(qr, scale=args.scale, border=args.border))
    print(f"qrcli: wrote {args.output} "
          f"(version {qr.version}, {qr.size}x{qr.size}, ECC {args.ecc})")
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--output",
                        help="output file (.png or .svg); "
                             "default: print to the terminal")
    parser.add_argument("--format", choices=("png", "svg"),
                        help="output format (default: from the file name)")
    parser.add_argument("--ecc", choices=tuple(_ECC_LEVELS), default="M",
                        help="error-correction level (default: M)")
    parser.add_argument("--scale", type=int, default=10,
                        help="pixels per module for image output "
                             "(default: 10)")
    parser.add_argument("--border", type=int, default=4,
                        help="quiet-zone width in modules (default: 4)")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "wifi":
        parser = argparse.ArgumentParser(
            prog="qrcli wifi",
            description="Make a Wi-Fi QR code: guests scan it to join "
                        "the network.")
        parser.add_argument("--ssid", required=True, help="network name")
        parser.add_argument("--password", help="network password")
        parser.add_argument("--security", choices=("WPA", "WEP", "nopass"),
                            default="WPA", help="security type (default: WPA)")
        parser.add_argument("--hidden", action="store_true",
                            help="the network does not broadcast its name")
        _add_common(parser)
        args = parser.parse_args(argv[1:])
        if args.security != "nopass" and not args.password:
            parser.error("--password is required unless --security=nopass")
        return _emit(_wifi_payload(args.ssid, args.password, args.security,
                                   args.hidden), args)

    parser = argparse.ArgumentParser(
        prog="qrcli",
        description="Make QR codes from the terminal. Zero dependencies, "
                    "fully offline. For a Wi-Fi code: qrcli wifi --ssid ...")
    parser.add_argument("text", help="the text or URL to encode")
    parser.add_argument("--version", action="version",
                        version=f"qrcli {__version__}")
    _add_common(parser)
    args = parser.parse_args(argv)
    return _emit(args.text, args)


if __name__ == "__main__":
    raise SystemExit(main())
