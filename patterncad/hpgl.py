"""플로터용 HPGL. 단위 1/40 mm (0.025 mm). 조각의 재단선·완성선·안쪽 선·노치·식서를 펜 하나로 긋는다."""

from __future__ import annotations

from .pieces import NOTCH_LEN, Piece

U = 25.4 * 40.0   # 인치 → HPGL 단위


def _pa(p) -> str:
    return f"{round(p.x * U)},{round(p.y * U)}"


def _poly(out: list, pts, closed=False):
    if not pts:
        return
    out.append(f"PU{_pa(pts[0])};")
    out.append("PD" + ",".join(_pa(p) for p in (pts[1:] + ([pts[0]] if closed else []))) + ";")


def write_hpgl(pieces: list, positions: list | None = None) -> str:
    from .geometry import Pt
    from .dxf import _allow_at
    out = ["IN;SP1;"]
    x = 0.0
    for i, pc in enumerate(pieces):
        if positions and i < len(positions):
            dx, dy = positions[i]
        else:
            x0, y0, x1, y1 = pc.bbox()
            dx, dy = x - x0, -y0
            x += (x1 - x0) + 1.5
        T = lambda p: Pt(p.x + dx, -(p.y + dy))  # noqa: E731  — 플로터는 y 가 위쪽
        _poly(out, [T(p) for p in pc.cut], closed=True)
        _poly(out, [T(p) for p in pc.loop], closed=True)
        for name, role, pts in pc.internal:
            if role in ("dart", "mark", "fold"):
                _poly(out, [T(p) for p in pts])
        for q, n in pc.notches:
            a = q + n * _allow_at(pc, q)
            _poly(out, [T(a), T(a - n * NOTCH_LEN)])
        if pc.grain:
            _poly(out, [T(pc.grain[0]), T(pc.grain[1])])
    out.append("PU0,0;SP0;IN;")
    return "\n".join(out) + "\n"
