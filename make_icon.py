"""Draw the Reel Cut icon (a tape reel) into assets/reelcut.ico and reelcut.png."""
import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

app = QGuiApplication(sys.argv)
out = Path(__file__).parent / "assets"
out.mkdir(exist_ok=True)

S = 256
img = QImage(S, S, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
c, R = QPointF(S / 2, S / 2), S * 0.47
p.setPen(Qt.NoPen)
p.setBrush(QColor("#4a3626"))
p.drawEllipse(c, R * 0.8, R * 0.8)
flange = QPainterPath()
flange.addEllipse(c, R, R)
for k in range(3):
    w = QPainterPath()
    w.moveTo(c)
    w.arcTo(QRectF(c.x() - R * 0.86, c.y() - R * 0.86, R * 1.72, R * 1.72), k * 120 + 30, 80)
    w.closeSubpath()
    hole = QPainterPath()
    hole.addEllipse(c, R * 0.32, R * 0.32)
    flange = flange.subtracted(w.subtracted(hole))
g = QLinearGradient(0, 0, S, S)
g.setColorAt(0, QColor("#f4f4f0"))
g.setColorAt(0.5, QColor("#a5a6a2"))
g.setColorAt(1, QColor("#e2e2dd"))
p.setBrush(g)
p.setPen(QPen(QColor("#55524c"), 4))
p.drawPath(flange)
p.setPen(Qt.NoPen)
p.setBrush(QColor("#2a241e"))
p.drawEllipse(c, R * 0.14, R * 0.14)
# red splice cut across the reel
p.setPen(QPen(QColor("#b8352a"), 14, Qt.SolidLine, Qt.RoundCap))
a = math.radians(-35)
p.drawLine(QPointF(c.x() - math.cos(a) * R * 0.95, c.y() - math.sin(a) * R * 0.95),
           QPointF(c.x() + math.cos(a) * R * 0.95, c.y() + math.sin(a) * R * 0.95))
p.end()
img.save(str(out / "reelcut.png"))
ok = img.save(str(out / "reelcut.ico"))
print("ico written" if ok else "ico FAILED")
