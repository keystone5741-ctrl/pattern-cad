"""DXF 내보내기 — AAMA/ASTM D6673 층 관습 (R12 ASCII).

    1  재단선 (시접 붙인 외곽)      4  노치        7  식서
    8  안쪽 선 (다트·표시)          14 완성선(봉제선)  15 글자 (조각 이름·매수)

조각마다 BLOCK 하나, ENTITIES 에는 그 조각을 놓는 INSERT. 단위는 인치(y 는 위쪽이 +).
"""

from __future__ import annotations

from .pieces import NOTCH_LEN, Piece

LAYERS = {1: "CUT", 4: "NOTCH", 7: "GRAIN", 8: "INTERNAL", 14: "SEW", 15: "TEXT"}


def _f(v: float) -> str:
    return f"{v:.4f}"


class _W:
    def __init__(self):
        self.out = []

    def tag(self, code, value):
        self.out.append(f"{code}\n{value}")

    def polyline(self, layer, pts, closed=True):
        self.tag(0, "POLYLINE"); self.tag(8, layer); self.tag(66, 1); self.tag(70, 1 if closed else 0)
        self.tag(10, 0); self.tag(20, 0); self.tag(30, 0)
        for p in pts:
            self.tag(0, "VERTEX"); self.tag(8, layer); self.tag(10, _f(p.x)); self.tag(20, _f(-p.y)); self.tag(30, 0)
        self.tag(0, "SEQEND"); self.tag(8, layer)

    def line(self, layer, a, b):
        self.tag(0, "LINE"); self.tag(8, layer)
        self.tag(10, _f(a.x)); self.tag(20, _f(-a.y)); self.tag(30, 0)
        self.tag(11, _f(b.x)); self.tag(21, _f(-b.y)); self.tag(31, 0)

    def text(self, layer, p, h, s):
        self.tag(0, "TEXT"); self.tag(8, layer)
        self.tag(10, _f(p.x)); self.tag(20, _f(-p.y)); self.tag(30, 0); self.tag(40, _f(h)); self.tag(1, s)

    def text_str(self):
        return "\n".join(self.out) + "\n"


def _piece_entities(w: _W, pc: Piece, ox: float = 0.0, oy: float = 0.0):
    from .geometry import Pt
    T = lambda p: Pt(p.x + ox, p.y + oy)  # noqa: E731
    w.polyline(1, [T(p) for p in pc.cut])
    w.polyline(14, [T(p) for p in pc.loop])
    for name, role, pts in pc.internal:
        if role in ("dart", "mark", "fold"):
            w.polyline(8, [T(p) for p in pts], closed=False)
    for q, n in pc.notches:      # 노치는 재단선에서 안쪽으로 파는 짧은 선
        a = q + n * _allow_at(pc, q)
        w.line(4, T(a), T(a - n * NOTCH_LEN))
    if pc.grain:
        w.line(7, T(pc.grain[0]), T(pc.grain[1]))
    x0, y0, x1, y1 = pc.bbox()
    label = f"{pc.name} x{pc.quantity} {pc.fabric}" + (" (골 펼침)" if pc.unfolded else " (골)" if pc.fold else "")
    w.text(15, T(pc.grain[0] + (pc.grain[1] - pc.grain[0]) * 0.5 + type(pc.grain[0])(0.3, 0)) if pc.grain else T(pc.loop[0]), 0.35, label)


def _allow_at(pc: Piece, q) -> float:
    best, val = 1e9, 0.5
    for e in pc.edges:
        for p in e.pts:
            d = p.dist(q)
            if d < best:
                best, val = d, e.allowance
    return val


def write_dxf(pieces: list, positions: list | None = None, title: str = "pattern-cad") -> str:
    """pieces: Piece 목록, positions: 조각 순서대로 (dx, dy) 놓을 자리(인치). 없으면 왼쪽부터 나란히."""
    w = _W()
    w.tag(999, f"{title} — pattern-cad, units inch, AAMA layers: 1 cut, 14 sew, 8 internal, 4 notch, 7 grain, 15 text")
    w.tag(0, "SECTION"); w.tag(2, "HEADER"); w.tag(9, "$ACADVER"); w.tag(1, "AC1009"); w.tag(0, "ENDSEC")
    w.tag(0, "SECTION"); w.tag(2, "TABLES"); w.tag(0, "TABLE"); w.tag(2, "LAYER"); w.tag(70, len(LAYERS))
    for n in LAYERS:
        w.tag(0, "LAYER"); w.tag(2, str(n)); w.tag(70, 0); w.tag(62, 7); w.tag(6, "CONTINUOUS")
    w.tag(0, "ENDTAB"); w.tag(0, "ENDSEC")
    names = []
    for i, pc in enumerate(pieces):
        nm = f"{pc.block}_{pc.name}".replace(" ", "_")
        if nm in names:
            nm += f"_{i}"
        names.append(nm)
    w.tag(0, "SECTION"); w.tag(2, "BLOCKS")
    for pc, nm in zip(pieces, names):
        w.tag(0, "BLOCK"); w.tag(8, "0"); w.tag(2, nm); w.tag(70, 0); w.tag(10, 0); w.tag(20, 0); w.tag(30, 0)
        _piece_entities(w, pc)
        w.tag(0, "ENDBLK"); w.tag(8, "0")
    w.tag(0, "ENDSEC")
    w.tag(0, "SECTION"); w.tag(2, "ENTITIES")
    x = 0.0
    for i, (pc, nm) in enumerate(zip(pieces, names)):
        if positions and i < len(positions):
            dx, dy = positions[i]
        else:
            x0, y0, x1, y1 = pc.bbox()
            dx, dy = x - x0, -y0
            x += (x1 - x0) + 1.5
        w.tag(0, "INSERT"); w.tag(8, "0"); w.tag(2, nm); w.tag(10, _f(dx)); w.tag(20, _f(-dy)); w.tag(30, 0)
    w.tag(0, "ENDSEC"); w.tag(0, "EOF")
    return w.text_str()
