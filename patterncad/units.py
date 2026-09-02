"""인치 분수 표기.

허용 입력: 3, 3.5, 3 1/2, 3-1/2, 1/2, 그리고 포트폴리오 표기 3.1/2 (= 3½).
'3.1/2' 처럼 점 뒤에 분수가 오면 대분수로 읽는다. '3.5' 는 소수다.
"""

from __future__ import annotations

import math
import re

CM_PER_INCH = 2.54
MM_PER_INCH = 25.4

_NUM = r"\d+(?:\.\d+)?"
_QUOTES = "\"”″“'"
_PATTERNS = (
    re.compile(rf"^(?P<whole>\d+)\.(?P<num>\d+)/(?P<den>\d+)$"),  # 3.1/2 (포트폴리오식)
    re.compile(rf"^(?P<whole>{_NUM})\s*[-+ ]\s*(?P<num>\d+)\s*/\s*(?P<den>\d+)$"),  # 3 1/2, 3-1/2
    re.compile(rf"^(?P<num>\d+)\s*/\s*(?P<den>\d+)$"),  # 1/2
    re.compile(rf"^(?P<whole>{_NUM})$"),  # 3, 3.5
)


def parse_inch(text) -> float | None:
    """문자열(또는 숫자)을 인치 실수로. 못 읽으면 None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().replace(",", "")
    sign = 1.0
    if s[:1] in "+-":
        sign = -1.0 if s[0] == "-" else 1.0
        s = s[1:].strip()
    s = re.sub(rf"(?i)\s*(in(?:ch(?:es)?)?|[{_QUOTES}])\s*$", "", s).strip()
    if not s:
        return None
    for pat in _PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        g = m.groupdict()
        v = float(g.get("whole") or 0)
        if g.get("num") is not None:
            den = float(g["den"])
            if den == 0:
                return None
            v += float(g["num"]) / den
        return sign * v
    return None


def to_fraction(value: float, denominator: int = 16, style: str = "space") -> str:
    """가장 가까운 1/denominator 눈금 분수. style='space' → '3 1/2', 'dot' → '3.1/2' (포트폴리오식)."""
    sign = "-" if value < 0 else ""
    total = int(round(abs(value) * denominator))
    whole, num = divmod(total, denominator)
    if num == 0:
        return f"{sign}{whole}"
    g = math.gcd(num, denominator)
    num, den = num // g, denominator // g
    if whole == 0:
        return f"{sign}{num}/{den}"
    sep = "." if style == "dot" else " "
    return f"{sign}{whole}{sep}{num}/{den}"


def inch_to_cm(v: float) -> float:
    return v * CM_PER_INCH


def cm_to_inch(v: float) -> float:
    return v / CM_PER_INCH
