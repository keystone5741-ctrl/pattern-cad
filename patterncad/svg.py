"""계산된 원형을 SVG로 그린다. 좌표 변환은 (scale, dx, dy): 출력 = 인치 * scale + 이동."""

from __future__ import annotations

from .block import Resolved, ResolvedLine
from .units import MM_PER_INCH

STYLES = {
    "outline": 'stroke="#111" stroke-width="{w}"',
    "dart": 'stroke="#111" stroke-width="{w}"',
    "construction": 'stroke="#888" stroke-width="{w2}" stroke-dasharray="{d}"',
    "mark": 'stroke="#111" stroke-width="{w2}"',
    "notch": 'stroke="#111" stroke-width="{w}"',
    "grain": 'stroke="#111" stroke-width="{w}"',
    "fold": 'stroke="#111" stroke-width="{w}" stroke-dasharray="{d2}"',
    "dimension": 'stroke="#c33" stroke-width="{w2}"',
}


def _f(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def line_path(line: ResolvedLine, scale: float, dx: float, dy: float) -> str:
    T = lambda p: (p.x * scale + dx, p.y * scale + dy)  # noqa: E731
    if line.kind == "curve":
        x, y = T(line.pts[0])
        d = [f"M{_f(x)} {_f(y)}"]
        for b in line.beziers:
            (x1, y1), (x2, y2), (x3, y3) = T(b.c1), T(b.c2), T(b.p3)
            d.append(f"C{_f(x1)} {_f(y1)} {_f(x2)} {_f(y2)} {_f(x3)} {_f(y3)}")
        return "".join(d)
    pts = [T(p) for p in line.pts]
    return "M" + "L".join(f"{_f(x)} {_f(y)}" for x, y in pts)


def render_group(res: Resolved, scale: float, dx: float, dy: float, color: str | None = None,
                 labels: bool = True, stroke_w: float = 0.5, roles=None) -> str:
    """<g> 조각. color 를 주면 역할 무관하게 그 색 (겹침 검증용)."""
    w, w2 = stroke_w, stroke_w * 0.6
    dash = f"{stroke_w*4},{stroke_w*3}"
    dash2 = f"{stroke_w*8},{stroke_w*4}"
    out = []
    for role in STYLES:
        if roles and role not in roles:
            continue
        ls = [l for l in res.lines if l.role == role]
        if not ls:
            continue
        style = STYLES[role].format(w=_f(w), w2=_f(w2), d=dash, d2=dash2)
        if color:
            style = style.replace('stroke="#111"', f'stroke="{color}"').replace('stroke="#888"', f'stroke="{color}"').replace('stroke="#c33"', f'stroke="{color}"')
        out.append(f'<g id="{role}" fill="none" {style}>')
        for l in ls:
            out.append(f'<path d="{line_path(l, scale, dx, dy)}"><title>{l.name}</title></path>')
        out.append("</g>")
    if labels:
        fs = _f(scale * 0.11)
        r = _f(scale * 0.03)
        out.append(f'<g id="points" font-size="{fs}" font-family="sans-serif" fill="{color or "#06c"}">')
        for name, p in res.points.items():
            x, y = p.x * scale + dx, p.y * scale + dy
            out.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{r}" fill="{color or "#06c"}"/>'
                       f'<text x="{_f(x + scale*0.05)}" y="{_f(y - scale*0.04)}">{name}</text>')
        out.append("</g>")
    return "\n".join(out)


def render_svg(res: Resolved, unit: str = "mm", margin_in: float = 1.0, labels: bool = True) -> str:
    """원형 하나를 실물 크기(mm)의 독립 SVG로."""
    xs = [p.x for p in res.points.values()]
    ys = [p.y for p in res.points.values()]
    for l in res.lines:
        for p in l.polyline(8):
            xs.append(p.x)
            ys.append(p.y)
    x0, x1, y0, y1 = min(xs) - margin_in, max(xs) + margin_in, min(ys) - margin_in, max(ys) + margin_in
    S = MM_PER_INCH
    W, H = (x1 - x0) * S, (y1 - y0) * S
    body = render_group(res, S, -x0 * S, -y0 * S, labels=labels, stroke_w=0.35)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(W)}mm" height="{_f(H)}mm" viewBox="0 0 {_f(W)} {_f(H)}">'
        f'<rect width="100%" height="100%" fill="white"/>'
        f'<title>{res.block.name}</title>{body}</svg>'
    )
