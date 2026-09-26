#!/usr/bin/env python3
"""Render original DeckThere Steam artwork. Development only; requires PySide6."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "packaging/artwork"
DEFS = """
<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#11243b"/><stop offset="1" stop-color="#060d18"/>
  </linearGradient>
  <linearGradient id="screen" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#123e53"/><stop offset="1" stop-color="#11263e"/>
  </linearGradient>
  <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
    <stop stop-color="#5fdaff"/><stop offset="1" stop-color="#89ffd0"/>
  </linearGradient>
</defs>
"""
# Original geometric handheld + monitor illustration; no third-party logos.
DEVICES = """
<g stroke-linejoin="round" stroke-linecap="round">
  <rect x="480" y="24" width="292" height="190" rx="22" fill="#101f32" stroke="#6388a8" stroke-width="6"/>
  <rect x="497" y="41" width="258" height="154" rx="10" fill="url(#screen)"/>
  <path d="M626 216v38m-58 0h116" fill="none" stroke="#6388a8" stroke-width="9"/>
  <path d="M568 123l34 30 71-72" fill="none" stroke="#89ffd0" stroke-width="13"/>
  <path d="M319 168l37-31m20-17 37-23m21-12 21-8" fill="none" stroke="#5fdaff" stroke-width="7"/>
  <rect x="20" y="205" width="468" height="204" rx="58" fill="#102136" stroke="#5fdaff" stroke-width="7"/>
  <rect x="129" y="225" width="251" height="164" rx="15" fill="url(#screen)" stroke="#355b77" stroke-width="3"/>
  <path d="M192 284h66v-30l57 53-57 53v-30h-66z" fill="url(#accent)"/>
  <circle cx="76" cy="261" r="20" fill="#081523" stroke="#6388a8" stroke-width="5"/>
  <path d="M76 313v45m-22-22h44" stroke="#b5cbdf" stroke-width="10"/>
  <g fill="#b5cbdf">
    <circle cx="434" cy="248" r="8"/><circle cx="414" cy="268" r="8"/>
    <circle cx="454" cy="268" r="8"/><circle cx="434" cy="288" r="8"/>
  </g>
  <circle cx="434" cy="347" r="20" fill="#081523" stroke="#6388a8" stroke-width="5"/>
</g>
"""


def label(text, x, y, size, color="#edf6ff", bold=False, spacing=0):
    return (
        f'<text x="{x}" y="{y}" font-family="DejaVu Sans" font-size="{size}" '
        f'font-weight="{700 if bold else 400}" letter-spacing="{spacing}" '
        f'fill="{color}">{text}</text>'
    )


def artwork(width, height, content, transparent=False):
    background = (
        ""
        if transparent
        else (
            f'<rect width="{width}" height="{height}" fill="url(#background)"/>'
            f'<circle cx="{width * 0.85}" cy="{height * 0.4}" r="{height * 0.55}" '
            'fill="none" stroke="#244056" stroke-width="1.5"/>'
            f'<circle cx="{width * 0.85}" cy="{height * 0.4}" r="{height * 0.71}" '
            'fill="none" stroke="#1c3348" stroke-width="1.5"/>'
        )
    )
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">{DEFS}{background}{content}</svg>'


def devices(x, y, scale):
    return f'<g transform="translate({x} {y}) scale({scale})">{DEVICES}</g>'


def main():
    application = QGuiApplication([])
    designs = {
        "icon": (512, 512, devices(22, 120, 0.60), False),
        "portrait": (
            600,
            900,
            label("DeckThere", 42, 150, 82, bold=True, spacing=-3)
            + '<rect x="46" y="181" width="72" height="5" rx="2" fill="url(#accent)"/>'
            + devices(23, 302, 0.70)
            + label("CONTROLLER SHARING", 46, 794, 23, "#97b6cf", spacing=2),
            False,
        ),
        "landscape": (
            920,
            430,
            label("DeckThere", 40, 138, 74, bold=True, spacing=-3)
            + label("CONTROLLER SHARING", 44, 180, 18, "#97b6cf", spacing=2)
            + '<rect x="44" y="208" width="68" height="4" rx="2" fill="url(#accent)"/>'
            + devices(446, 156, 0.55),
            False,
        ),
        "hero": (1920, 620, devices(970, 65, 1.10), False),
        "logo": (800, 230, label("DeckThere", 16, 165, 164, bold=True, spacing=-6), True),
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, (width, height, content, transparent) in designs.items():
        svg = artwork(width, height, content, transparent)
        (ROOT / f"{name}.svg").write_text(svg + "\n")
        renderer = QSvgRenderer(svg.encode())
        if not renderer.isValid():
            raise RuntimeError(f"Invalid SVG: {name}")
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, width, height))
        painter.end()
        if not image.save(str(ROOT / f"{name}.png")):
            raise RuntimeError(f"Cannot write {name}.png")
        print(f"{name}: {width}x{height}")
    application.quit()


if __name__ == "__main__":
    main()
