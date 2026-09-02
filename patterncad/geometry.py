"""2D 기하. 단위는 인치, y 축은 아래로 (제도지·화면과 같은 방향)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Pt:
    x: float
    y: float

    def __add__(self, o):
        return Pt(self.x + o.x, self.y + o.y)

    def __sub__(self, o):
        return Pt(self.x - o.x, self.y - o.y)

    def __mul__(self, k: float):
        return Pt(self.x * k, self.y * k)

    __rmul__ = __mul__

    def __truediv__(self, k: float):
        return Pt(self.x / k, self.y / k)

    def __neg__(self):
        return Pt(-self.x, -self.y)

    def dot(self, o) -> float:
        return self.x * o.x + self.y * o.y

    def cross(self, o) -> float:
        return self.x * o.y - self.y * o.x

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def unit(self):
        L = self.length()
        if L == 0:
            raise ValueError("영벡터는 단위벡터가 없다")
        return Pt(self.x / L, self.y / L)

    def perp(self):
        """반시계 90° 회전 (y 아래 좌표계에서는 화면상 시계 방향으로 보인다)."""
        return Pt(-self.y, self.x)

    def rotate(self, deg: float):
        r = math.radians(deg)
        c, s = math.cos(r), math.sin(r)
        return Pt(self.x * c - self.y * s, self.x * s + self.y * c)

    def dist(self, o) -> float:
        return (self - o).length()

    def round(self, n=4):
        return Pt(round(self.x, n), round(self.y, n))

    def __iter__(self):
        yield self.x
        yield self.y


ORIGIN = Pt(0.0, 0.0)


def lerp(a: Pt, b: Pt, t: float) -> Pt:
    return a + (b - a) * t


def along(a: Pt, b: Pt, dist: float) -> Pt:
    """a에서 b 방향으로 dist 만큼 간 점 (b를 넘어가도 된다)."""
    return a + (b - a).unit() * dist


def intersect_lines(p1: Pt, p2: Pt, p3: Pt, p4: Pt) -> Pt:
    """무한직선 p1p2 와 p3p4 의 교점."""
    d1, d2 = p2 - p1, p4 - p3
    den = d1.cross(d2)
    if abs(den) < 1e-12:
        raise ValueError("평행한 두 직선은 교점이 없다")
    t = (p3 - p1).cross(d2) / den
    return p1 + d1 * t


def foot_of_perpendicular(p: Pt, a: Pt, b: Pt) -> Pt:
    """p에서 직선 ab에 내린 수선의 발."""
    d = (b - a).unit()
    return a + d * (p - a).dot(d)


def circle_line_x(center: Pt, radius: float, x: float, below: bool = True) -> Pt:
    """중심 center, 반지름 radius 인 원 위에서 x 좌표가 x 인 점. below=True 면 아래쪽(y 큰) 해."""
    dx = x - center.x
    if abs(dx) > radius:
        raise ValueError(f"x={x} 는 반지름 {radius} 원에 닿지 않는다 (중심 {center})")
    dy = math.sqrt(radius * radius - dx * dx)
    return Pt(x, center.y + dy if below else center.y - dy)


def circle_line_y(center: Pt, radius: float, y: float, right: bool = True) -> Pt:
    dy = y - center.y
    if abs(dy) > radius:
        raise ValueError(f"y={y} 는 반지름 {radius} 원에 닿지 않는다")
    dx = math.sqrt(radius * radius - dy * dy)
    return Pt(center.x + dx if right else center.x - dx, y)


# ---------------------------------------------------------------- 3차 베지어
@dataclass(frozen=True)
class Bezier:
    p0: Pt
    c1: Pt
    c2: Pt
    p3: Pt

    def at(self, t: float) -> Pt:
        u = 1 - t
        return (
            self.p0 * (u * u * u)
            + self.c1 * (3 * u * u * t)
            + self.c2 * (3 * u * t * t)
            + self.p3 * (t * t * t)
        )

    def tangent(self, t: float) -> Pt:
        u = 1 - t
        return (
            (self.c1 - self.p0) * (3 * u * u)
            + (self.c2 - self.c1) * (6 * u * t)
            + (self.p3 - self.c2) * (3 * t * t)
        )

    def sample(self, n: int = 32) -> list[Pt]:
        return [self.at(i / n) for i in range(n + 1)]

    def length(self, n: int = 64) -> float:
        pts = self.sample(n)
        return sum(pts[i].dist(pts[i + 1]) for i in range(n))

    def point_at_length(self, s: float, n: int = 256) -> Pt:
        pts = self.sample(n)
        acc = 0.0
        for i in range(n):
            seg = pts[i].dist(pts[i + 1])
            if acc + seg >= s:
                return lerp(pts[i], pts[i + 1], (s - acc) / seg if seg else 0)
            acc += seg
        return pts[-1]

    def split(self, t: float):
        """de Casteljau 분할."""
        a = lerp(self.p0, self.c1, t)
        b = lerp(self.c1, self.c2, t)
        c = lerp(self.c2, self.p3, t)
        d = lerp(a, b, t)
        e = lerp(b, c, t)
        m = lerp(d, e, t)
        return Bezier(self.p0, a, d, m), Bezier(m, e, c, self.p3)


def bezier_from_tangents(p0: Pt, t0: Pt, p3: Pt, t3: Pt, h0: float, h3: float) -> Bezier:
    """양 끝 접선 방향(t0: p0 에서 나가는 방향, t3: p3 로 들어오는 방향)과
    핸들 길이(현 길이 대비 비율)로 베지어를 만든다."""
    chord = p0.dist(p3)
    return Bezier(p0, p0 + t0.unit() * (h0 * chord), p3 - t3.unit() * (h3 * chord), p3)


def fit_handles(p0: Pt, t0: Pt, p3: Pt, t3: Pt, samples: list[Pt], iterations: int = 5) -> tuple[float, float]:
    """끝점과 접선 방향을 고정하고, 표본점들에 가장 가까운 핸들 길이 비율(h0, h3)을 최소제곱으로 구한다.
    표본은 p0 → p3 순서로 놓인 폴리라인이라고 본다. 매개변수 u 는 처음엔 누적 현 길이로 잡고,
    맞춘 곡선 위의 최근접점으로 다시 잡기를 반복한다."""
    t0, t3 = t0.unit(), t3.unit()
    chord = p0.dist(p3)
    if len(samples) < 2 or chord == 0:
        return 1 / 3, 1 / 3
    cum = [0.0]
    for i in range(1, len(samples)):
        cum.append(cum[-1] + samples[i].dist(samples[i - 1]))
    total = cum[-1] or 1.0
    us = [c / total for c in cum]

    def solve(us):
        # 잔차 r(u) = B(u) - S 는 a, b 에 대해 선형: B = base(u) + a*A(u) + b*C(u)
        m11 = m12 = m22 = r1 = r2 = 0.0
        for s, u in zip(samples, us):
            w0, w1, w2, w3 = (1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u * u, u ** 3
            base = p0 * (w0 + w1) + p3 * (w2 + w3)
            A = t0 * w1
            C = t3 * (-w2)
            d = s - base
            m11 += A.dot(A)
            m12 += A.dot(C)
            m22 += C.dot(C)
            r1 += A.dot(d)
            r2 += C.dot(d)
        det = m11 * m22 - m12 * m12
        if abs(det) < 1e-12:
            return 1 / 3, 1 / 3
        return (r1 * m22 - r2 * m12) / det / chord, (m11 * r2 - m12 * r1) / det / chord

    h = solve(us)
    for _ in range(iterations):
        bz = bezier_from_tangents(p0, t0, p3, t3, h[0], h[1])
        grid = bz.sample(200)
        us = []
        for s in samples:  # 최근접 t 로 다시 매개화
            best = min(range(len(grid)), key=lambda i: grid[i].dist(s))
            us.append(best / 200)
        h = solve(us)

    # 교대 최소화는 근처의 잘못된 고정점에 멈출 수 있으므로, 실제 목적함수(표본→곡선 최근접 거리 제곱합)로
    # 두 핸들을 직접 패턴 탐색해 마무리한다
    def objective(h0, h3):
        grid = bezier_from_tangents(p0, t0, p3, t3, h0, h3).sample(160)
        return sum(min(g.dist(s) for g in grid) ** 2 for s in samples)

    lo = 0.05  # 핸들이 음수가 되면 끝점에서 꺾여 나가므로 하한을 둔다
    h0, h3 = max(lo, h[0]), max(lo, h[1])
    best = objective(h0, h3)
    step = 0.1
    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))
    while step > 1e-3:
        improved = False
        for dx, dy in dirs:
            dh0, dh3 = dx * step, dy * step
            if h0 + dh0 < lo or h3 + dh3 < lo:
                continue
            v = objective(h0 + dh0, h3 + dh3)
            if v < best - 1e-12:
                best, h0, h3, improved = v, h0 + dh0, h3 + dh3, True
                break
        if not improved:
            step /= 2
    return h0, h3


def polyline_length(pts: list[Pt]) -> float:
    return sum(pts[i].dist(pts[i + 1]) for i in range(len(pts) - 1))


def nearest_on_polyline(p: Pt, pts: list[Pt]) -> tuple[float, Pt]:
    """점 p에서 폴리라인까지 최단거리와 그 위치."""
    best, bp = float("inf"), pts[0]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        d = b - a
        L2 = d.dot(d)
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, (p - a).dot(d) / L2))
        q = a + d * t
        dist = p.dist(q)
        if dist < best:
            best, bp = dist, q
    return best, bp
