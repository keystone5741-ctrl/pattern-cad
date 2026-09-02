"""원형(Block) 정의 파일(YAML) 읽기와 계산.

원형 파일은 좌표가 아니라 규칙이다:

    measurements:                  # 치수. value 또는 formula
      B: {ko: 가슴둘레, value: 33.1/2}
      반폭: {formula: "B/4 + 여유/4"}
    points:                        # 점. 이름 → 구하는 규칙 (위에서 아래로 차례로 계산)
      O:     {ko: 원점, at: [0, 0]}
      BL_SS: {ko: 겨드랑점, from: O, dx: 반폭, dy: 진동깊이}
      SP_F:  {circle: {center: SNP_F, radius: 앞어깨길이}, x: 앞어깨끝너비, side: down}
    lines:                         # 선. 점 이름들을 잇는다. 역할(role)이 있다
      - {name: 앞암홀, role: outline, curve: [SP_F, T_F, G_F, BL_SS],
         tangents: {SP_F: {perp: [SNP_F, SP_F]}, T_F: [0, 1], BL_SS: [1, 0]}}

점 규칙 종류:
    at: [x, y]                                  절대 좌표
    from: P, dx: , dy:                          P 에서 가로/세로 이동
    from: P, dir: [dx, dy], dist: L             P 에서 dir 방향으로 L
    along: [A, B], dist: L   (또는 ratio: r)    A→B 선 위 (B 를 넘어가도 됨)
    midpoint: [A, B]
    intersect: [[A, B], [C, D]]                 두 직선의 교점
    foot: {of: P, line: [A, B]}                 수선의 발
    perp: {from: P, line: [A, B], dist: L, side: [sx, sy]}   AB 에 수직으로 L 만큼. side 는 방향 힌트
    polar: {center: P, radius: R, angle: A}     P 에서 반지름 R, 각도 A(도, 0 = +x, 양수 = y 아래쪽)
    rotate: {of: P, center: C, angle: A}        P 를 C 중심으로 A 도 회전 (절개-벌림에 쓴다)
    mirror: {of: P, line: [A, B]}               직선 AB 에 대칭
    circle: {center: P, radius: R}, x: X, side: down|up      원 위에서 x 가 X 인 점
    circle: {center: P, radius: R}, y: Y, side: right|left   원 위에서 y 가 Y 인 점

값 자리에는 숫자, 인치표기('3.1/2'), 식('B/4 + 여유/4', 'SP_F.y', 'dist(A,B)') 모두 된다.

선:
    pts: [A, B, C]     직선(꺾은선)
    curve: [A, B, C]   점들을 지나는 3차 베지어 사슬. tangents 로 각 점의 접선 방향을,
                       handles 로 구간별 핸들 길이(현 대비 비율, [나가는, 들어오는])를 준다
    role: outline | construction | dart | mark | notch | grain | fold | dimension
    piece: 앞판 | 뒤판 …  (조각 분류용)

원형 파일 맨 위(선과 나란히)에 handles 를 둘 수 있다. 상속받은 선의 **곡선 모양만**
원본 도면에 맞춰 고칠 때 쓴다 — 선 정의를 통째로 다시 쓰지 않아도 된다:

    handles:
      앞암홀: [[0.20, 0.20], [0.31, 0.33], [0.32, 0.35]]

tools/align_block.py --fit 이 이 형식으로 뽑아 준다.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .expr import Env, evaluate
from .units import parse_inch
from .geometry import (
    Bezier,
    Pt,
    along,
    bezier_from_tangents,
    circle_line_x,
    circle_line_y,
    foot_of_perpendicular,
    intersect_lines,
    lerp,
)

ROLES = ("outline", "construction", "dart", "mark", "notch", "grain", "fold", "dimension")


def merge_block(parent: dict, child: dict) -> dict:
    """상속: 자식이 부모 원형 위에 바뀐 것만 적는다.

        extends: sichuni_basic.yaml
        measurements:  {여유: {value: 3.1/2}}          # 덮어쓰기 / 추가
        points:
          remove: [BP, BD1, BD2]                        # 지우기
          BNP: {from: TOP_CB, dy: "-뒤목점올림"}         # 같은 이름이면 그 자리에서 교체, 없으면 끝에 추가
        lines:
          remove: [가슴다트, 앞옆선위]
          replace: [{name: 앞암홀, ...}]                 # 같은 이름 자리에서 교체
          add: [{name: 앞옆선, ...}]                     # 끝에 추가
    """
    out = {k: v for k, v in parent.items() if k != "extends"}
    out.update({k: v for k, v in child.items() if k not in ("measurements", "points", "lines", "extends")})
    out["extends_from"] = parent.get("id")

    meas = dict(parent.get("measurements", {}))
    KINDS = ("value", "formula", "table", "choice")
    for k, v in (child.get("measurements") or {}).items():
        base = meas.get(k)
        if isinstance(base, dict) and isinstance(v, dict):
            # 자식이 값의 종류를 바꾸면(예: formula → value) 부모의 다른 종류는 버린다
            if any(kind in v for kind in KINDS):
                base = {bk: bv for bk, bv in base.items() if bk not in KINDS + ("key",)}
            meas[k] = {**base, **v}
        else:
            meas[k] = v
    out["measurements"] = meas

    pts = dict(parent.get("points", {}))
    cp = dict(child.get("points") or {})
    for name in cp.pop("remove", None) or []:
        pts.pop(name, None)
    for name, rule in cp.items():
        pts[name] = rule  # dict 는 삽입 순서를 지키므로 기존 이름은 제자리, 새 이름은 끝
    out["points"] = pts

    lines = list(parent.get("lines", []))
    cl = child.get("lines") or {}
    if isinstance(cl, list):
        cl = {"add": cl}
    removed = set(cl.get("remove") or [])
    lines = [l for l in lines if l["name"] not in removed]
    for rep in cl.get("replace") or []:
        idx = next((i for i, l in enumerate(lines) if l["name"] == rep["name"]), None)
        if idx is None:
            raise ValueError(f"교체할 선이 없다: {rep['name']}")
        lines[idx] = rep
    lines.extend(cl.get("add") or [])
    out["lines"] = lines

    handles = dict(parent.get("handles") or {})
    handles.update(child.get("handles") or {})
    if handles:
        out["handles"] = handles
    return out


@dataclass
class LineDef:
    name: str
    role: str
    points: list[str]
    kind: str = "straight"  # straight | curve
    tangents: dict = field(default_factory=dict)
    handles: list = field(default_factory=list)
    piece: str | None = None
    ko: str = ""
    note: str = ""
    closed: bool = False


@dataclass
class ResolvedLine:
    name: str
    role: str
    kind: str
    point_names: list[str]
    pts: list[Pt]
    beziers: list[Bezier]
    piece: str | None
    ko: str = ""

    def polyline(self, n: int = 24) -> list[Pt]:
        if self.kind != "curve":
            return list(self.pts)
        out = [self.pts[0]]
        for b in self.beziers:
            out.extend(b.sample(n)[1:])
        return out

    def length(self) -> float:
        pl = self.polyline(64)
        return sum(pl[i].dist(pl[i + 1]) for i in range(len(pl) - 1))


@dataclass
class Resolved:
    block: "Block"
    measurements: dict
    points: dict
    point_meta: dict
    lines: list[ResolvedLine]

    def line(self, name: str) -> ResolvedLine:
        for l in self.lines:
            if l.name == name:
                return l
        raise KeyError(name)


class Block:
    def __init__(self, data: dict, source: Path | None = None):
        self.data = data
        self.source = source
        self.name = data.get("name", "")
        self.id = data.get("id", "")
        self.category = data.get("category", "")
        self.measurements: dict = data.get("measurements", {})
        self.points: dict = data.get("points", {})
        self.lines: list[LineDef] = [self._line_def(d) for d in data.get("lines", [])]
        # handles: 선 이름 → 구간별 핸들 [[나가는, 들어오는], ...]
        # 상속받은 선의 곡선 모양만 원본 도면에 맞춰 고칠 때 쓴다 (선 정의를 통째로 다시 쓰지 않아도 된다).
        # tools/align_block.py --fit 이 이 형식으로 뽑아 준다.
        self.handles: dict = data.get("handles", {}) or {}
        for ld in self.lines:
            if ld.name in self.handles:
                ld.handles = self.handles[ld.name]

    @classmethod
    def load(cls, path) -> "Block":
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data.get("extends"):
            parent = cls.load(path.parent / data["extends"])
            data = merge_block(parent.data, data)
        return cls(data, path)

    @staticmethod
    def _line_def(d: dict) -> LineDef:
        if "curve" in d:
            pts, kind = list(d["curve"]), "curve"
        else:
            pts, kind = list(d["pts"]), "straight"
        role = d.get("role", "outline")
        if role not in ROLES:
            raise ValueError(f"선 {d.get('name')}: 모르는 역할 {role}")
        return LineDef(
            name=d["name"], role=role, points=pts, kind=kind,
            tangents=d.get("tangents", {}) or {}, handles=d.get("handles", []) or [],
            piece=d.get("piece"), ko=d.get("ko", ""), note=d.get("note", ""), closed=bool(d.get("closed")),
        )

    # ------------------------------------------------------------ 계산
    def evaluate(self, overrides: dict | None = None) -> Resolved:
        overrides = overrides or {}
        meas: dict[str, float] = {}
        points: dict[str, Pt] = {}
        env = Env(meas, points)

        # 치수: value 는 바로, formula 는 다른 치수·점을 참조할 수 있으므로 필요할 때 지연 평가
        pending = {}
        for name, spec in self.measurements.items():
            spec = spec if isinstance(spec, dict) else {"value": spec}
            if name in overrides:
                v = overrides[name]
                meas[name] = v if isinstance(v, str) and parse_inch(v) is None else float(parse_inch(v) if isinstance(v, str) else v)
            elif "choice" in spec:  # 글자 선택값 (예: 소매산단계: 기본)
                if spec.get("options") and spec["choice"] not in spec["options"]:
                    raise ValueError(f"치수 {name}: {spec['choice']} 는 선택지 {spec['options']} 에 없다")
                meas[name] = spec["choice"]
            elif "table" in spec:  # 다른 선택값에 따른 표 (예: 소매산단계 → 소매산조정)
                pending[name] = spec
            elif "formula" in spec:
                pending[name] = spec["formula"]
            elif "value" in spec:
                meas[name] = evaluate(spec["value"], env)
            else:
                raise ValueError(f"치수 {name}: value 나 formula 가 필요하다")

        def resolve_one(name):
            spec = pending[name]
            if isinstance(spec, dict):  # table
                key = meas[spec["key"]]  # 없으면 KeyError → 아직
                if isinstance(meas.get(name), str) and name in overrides:
                    return
                if key not in spec["table"]:
                    raise ValueError(f"치수 {name}: 표에 {key!r} 가 없다 (선택지 {list(spec['table'])})")
                meas[name] = evaluate(spec["table"][key], env)
            else:
                meas[name] = evaluate(spec, env)

        def resolve_pending():
            for name in list(pending):
                try:
                    resolve_one(name)
                    del pending[name]
                except (NameError, KeyError):
                    pass

        resolve_pending()  # 점을 참조하지 않는 식은 지금, 나머지는 점 계산 중에

        point_meta = {}
        for name, rule in self.points.items():
            resolve_pending()
            points[name] = self._point(name, rule, env)
            point_meta[name] = {k: v for k, v in rule.items() if k in ("ko", "en", "note")}
        resolve_pending()
        if pending:
            raise ValueError(f"계산 못 한 치수: {list(pending)}")

        lines = [self._resolve_line(ld, env) for ld in self.lines]
        return Resolved(self, meas, points, point_meta, lines)

    def _point(self, name: str, rule: dict, env: Env) -> Pt:
        ev = lambda v: evaluate(v, env)  # noqa: E731
        P = lambda n: env.p[n]  # noqa: E731
        try:
            if "at" in rule:
                x, y = rule["at"]
                return Pt(ev(x), ev(y))
            if "midpoint" in rule:
                a, b = rule["midpoint"]
                return lerp(P(a), P(b), 0.5)
            if "along" in rule:
                a, b = rule["along"]
                if "ratio" in rule:
                    return lerp(P(a), P(b), ev(rule["ratio"]))
                return along(P(a), P(b), ev(rule["dist"]))
            if "intersect" in rule:
                (a, b), (c, d) = rule["intersect"]
                return intersect_lines(P(a), P(b), P(c), P(d))
            if "foot" in rule:
                a, b = rule["foot"]["line"]
                return foot_of_perpendicular(P(rule["foot"]["of"]), P(a), P(b))
            if "perp" in rule:
                r = rule["perp"]
                a, b = r["line"]
                n = (P(b) - P(a)).unit().perp()
                if "side" in r:
                    sx, sy = r["side"]
                    if n.dot(Pt(ev(sx), ev(sy))) < 0:
                        n = -n
                return P(r["from"]) + n * ev(r["dist"])
            if "rotate" in rule:
                r = rule["rotate"]
                c = P(r["center"])
                return c + (P(r["of"]) - c).rotate(ev(r["angle"]))
            if "mirror" in rule:
                r = rule["mirror"]
                a, b = r["line"]
                f = foot_of_perpendicular(P(r["of"]), P(a), P(b))
                return f + (f - P(r["of"]))
            if "polar" in rule:
                r = rule["polar"]
                a = math.radians(ev(r["angle"]))
                R = ev(r["radius"])
                return P(r["center"]) + Pt(R * math.cos(a), R * math.sin(a))
            if "circle" in rule:
                c = rule["circle"]
                center, radius = P(c["center"]), ev(c["radius"])
                if "x" in rule:
                    return circle_line_x(center, radius, ev(rule["x"]), below=rule.get("side", "down") == "down")
                return circle_line_y(center, radius, ev(rule["y"]), right=rule.get("side", "right") == "right")
            if "from" in rule:
                base = P(rule["from"])
                if "dir" in rule:
                    dx, dy = rule["dir"]
                    return base + Pt(ev(dx), ev(dy)).unit() * ev(rule["dist"])
                return base + Pt(ev(rule.get("dx", 0)), ev(rule.get("dy", 0)))
        except (KeyError, NameError) as e:
            raise ValueError(f"점 {name}: 참조 오류 {e} (점은 정의된 순서대로 계산된다)") from e
        raise ValueError(f"점 {name}: 규칙을 못 알아봄 {rule}")

    def _resolve_line(self, ld: LineDef, env: Env) -> ResolvedLine:
        pts = [env.p[n] for n in ld.points]
        if ld.closed and pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        beziers: list[Bezier] = []
        if ld.kind == "curve":
            dirs = [self._tangent(ld, i, pts, env) for i in range(len(pts))]
            for i in range(len(pts) - 1):
                h = ld.handles[i] if i < len(ld.handles) and ld.handles[i] else [1 / 3, 1 / 3]
                beziers.append(bezier_from_tangents(pts[i], dirs[i], pts[i + 1], dirs[i + 1], float(h[0]), float(h[1])))
        return ResolvedLine(ld.name, ld.role, ld.kind, list(ld.points), pts, beziers, ld.piece, ld.ko)

    def _tangent(self, ld: LineDef, i: int, pts: list[Pt], env: Env) -> Pt:
        """i 번째 점의 접선 방향. 사슬 진행 방향(이전 점 → 다음 점)과 같은 쪽을 향하게 부호를 맞춘다."""
        spec = ld.tangents.get(ld.points[i], "auto")
        prev_p = pts[i - 1] if i > 0 else None
        next_p = pts[i + 1] if i + 1 < len(pts) else None
        flow = (next_p or pts[i]) - (prev_p or pts[i])
        if spec == "auto" or spec is None:
            if prev_p is None or next_p is None:
                d = flow
            else:  # Catmull-Rom 식: 양옆 점의 방향 평균
                d = (next_p - pts[i]).unit() + (pts[i] - prev_p).unit()
            return d.unit()
        if isinstance(spec, dict):
            if "perp" in spec:
                a, b = spec["perp"]
                d = (env.p[b] - env.p[a]).unit().perp()
            elif "along" in spec:
                a, b = spec["along"]
                d = (env.p[b] - env.p[a]).unit()
            else:
                raise ValueError(f"선 {ld.name}: 접선 지정 오류 {spec}")
        else:
            d = Pt(evaluate(spec[0], env), evaluate(spec[1], env)).unit()
        if d.dot(flow) < 0:
            d = -d
        return d
