# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-25

### Added

- QR encoder written from scratch on the Python standard library: byte mode,
  versions 1–20, all four error-correction levels, Reed–Solomon error
  correction, block interleaving, and penalty-based mask selection.
- Renderers: terminal (Unicode half-blocks), PNG, and SVG — all stdlib.
- CLI: `qrcli "text"` prints to the terminal, `-o file.png` / `file.svg`
  saves an image, `--ecc L|M|Q|H` picks the error-correction level.
- `qrcli wifi --ssid ... --password ...` builds Wi-Fi join QR codes.
