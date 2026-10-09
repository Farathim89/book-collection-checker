"""Draws the app icon (assets/icon.png + icon.ico): a shelf of books with a check badge - Book Collection
Checker's own, not the Book Sorter's. Run: .venv\\Scripts\\python assets\\make_icon.py"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

HERE = Path(__file__).resolve().parent
GOLD, GOLD_DARK, BG, BG2 = QColor("#f2c14e"), QColor("#b8862b"), QColor("#1d2230"), QColor("#2b3346")
SPINES = [QColor("#e05d5d"), QColor("#4fa3d9"), QColor("#f2c14e"), QColor("#6cc28a"), QColor("#a07be0")]


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 256, size / 256)
    # rounded dark tile
    g = QLinearGradient(0, 0, 0, 256)
    g.setColorAt(0, BG2)
    g.setColorAt(1, BG)
    p.setPen(QPen(GOLD_DARK, 6))
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 48, 48)
    # the shelf
    p.setPen(Qt.NoPen)
    p.setBrush(GOLD)
    p.drawRoundedRect(QRectF(36, 186, 184, 14), 5, 5)
    # five books: one leaning, one missing (the gap)
    x = 46
    for i, (w, h) in enumerate(((26, 122), (22, 100), (0, 0), (28, 84), (24, 70))):
        if not w:  # the gap = a missing volume, dashed outline
            p.setPen(QPen(QColor(255, 255, 255, 120), 3, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(x, 186 - 100, 24, 100), 4, 4)
            p.setPen(Qt.NoPen)
            x += 30
            continue
        p.setBrush(SPINES[i])
        p.drawRoundedRect(QRectF(x, 186 - h, w, h), 4, 4)
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawRect(QRectF(x + 4, 186 - h + 14, w - 8, 5))
        p.drawRect(QRectF(x + 4, 186 - h + 24, w - 8, 3))
        x += w + 6
    # the check badge
    p.setBrush(GOLD)
    p.setPen(QPen(BG, 6))
    p.drawEllipse(QPointF(190, 64), 40, 40)
    tick = QPainterPath(QPointF(171, 64))
    tick.lineTo(185, 79)
    tick.lineTo(210, 50)
    p.setPen(QPen(BG, 11, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(tick)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841 - QPainter needs it
    draw(256).save(str(HERE / "icon.png"))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    pngs = [png_bytes(draw(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    (HERE / "icon.ico").write_bytes(head + entries + b"".join(pngs))
    print("icon.png + icon.ico written")


if __name__ == "__main__":
    main()
