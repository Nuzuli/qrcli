"""qrcli — QR codes from your terminal. Zero dependencies."""

from .qr import Ecc, QrCode, QrTooLongError, encode, encode_text

__version__ = "0.1.0"

__all__ = [
    "Ecc",
    "QrCode",
    "QrTooLongError",
    "__version__",
    "encode",
    "encode_text",
]
