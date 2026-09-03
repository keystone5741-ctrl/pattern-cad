"""python -m pytest tests  또는  python -m unittest discover tests"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from patterncad.block import Block  # noqa: E402
from patterncad.expr import Env, evaluate  # noqa: E402
from patterncad.geometry import Bezier, Pt, circle_line_x, fit_handles, intersect_lines  # noqa: E402
from patterncad.units import parse_inch, to_fraction  # noqa: E402


class Units(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_inch("3.1/2"), 3.5)  # 포트폴리오식
        self.assertEqual(parse_inch("3 1/2"), 3.5)
        self.assertEqual(parse_inch("3-1/2"), 3.5)
        self.assertEqual(parse_inch("1/8”"), 0.125)
        self.assertEqual(parse_inch("26”"), 26.0)
        self.assertEqual(parse_inch("3.5"), 3.5)
        self.assertEqual(parse_inch(" 14.5/8\" "), 14.625)
        self.assertIsNone(parse_inch("B/4"))

    def test_format(self):
        self.assertEqual(to_fraction(3.5), "3 1/2")
        self.assertEqual(to_fraction(3.5, style="dot"), "3.1/2")
        self.assertEqual(to_fraction(0.125), "1/8")
        self.assertEqual(to_fraction(14.625, 8), "14 5/8")


class Geometry(unittest.TestCase):
    def test_intersect(self):
        p = intersect_lines(Pt(0, 0), Pt(2, 2), Pt(0, 2), Pt(2, 0))
        self.assertAlmostEqual(p.x, 1)
        self.assertAlmostEqual(p.y, 1)

    def test_circle_x(self):
        p = circle_line_x(Pt(0, 0), 5, 3)
        self.assertAlmostEqual(p.y, 4)

    def test_bezier_length(self):
        b = Bezier(Pt(0, 0), Pt(1, 0), Pt(2, 0), Pt(3, 0))
        self.assertAlmostEqual(b.length(), 3, places=6)

    def test_fit_recovers_handles(self):
        p0, p3 = Pt(0, 0), Pt(10, 0)
        t0, t3 = Pt(1, 1), Pt(1, -1)
        from patterncad.geometry import bezier_from_tangents

        truth = bezier_from_tangents(p0, t0, p3, t3, 0.4, 0.3)
        h0, h3 = fit_handles(p0, t0, p3, t3, truth.sample(40))
        self.assertAlmostEqual(h0, 0.4, places=2)
        self.assertAlmostEqual(h3, 0.3, places=2)


class Expr(unittest.TestCase):
    def test_eval(self):
        env = Env({"B": 33.5, "여유": 2.5}, {"A": Pt(1, 2), "C": Pt(4, 6)})
        self.assertAlmostEqual(evaluate("B/4 + 여유/4", env), 9)
        self.assertAlmostEqual(evaluate("dist(A, C)", env), 5)
        self.assertAlmostEqual(evaluate("C.y - A.y", env), 4)
        self.assertAlmostEqual(evaluate("3.1/2", env), 3.5)
        with self.assertRaises(ValueError):
            evaluate("__import__('os')", env)


class Sichuni(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "sichuni_basic.yaml").evaluate()

    def test_frame(self):
        p = self.res.points
        self.assertAlmostEqual(p["TOP_CB"].x, 18)  # B/2 + 여유/2
        self.assertAlmostEqual(p["UP"].x, 9)
        self.assertAlmostEqual(p["UP"].y, 8.5)
        self.assertAlmostEqual(p["HEM_CF"].y, 16.25)

    def test_front_shoulder_is_back_minus_dart(self):
        r = self.res
        back = r.line("뒤어깨선목쪽").length() + r.line("뒤어깨선끝쪽").length()
        self.assertAlmostEqual(r.line("앞어깨선").length(), back, places=6)

    def test_front_shoulder_points_at_guide(self):
        """앞어깨는 옆목점에서 가이드 방향으로 앞어깨길이만큼.
        기울기는 뒤어깨에서 따라가되, p.44 값에서는 도면 표기 가이드점(7.1/2, 2)과 같아야 한다."""
        p = self.res.points
        d1 = (p["SP_F"] - p["SNP_F"]).unit()
        d2 = (p["G_SF"] - p["SNP_F"]).unit()
        self.assertAlmostEqual(d1.cross(d2), 0, places=9)
        self.assertAlmostEqual(p["G_SF0"].x, 7.5)
        self.assertAlmostEqual(p["G_SF0"].y, 2)
        d0 = (p["G_SF0"] - p["SNP_F"]).unit()
        self.assertAlmostEqual(d0.cross(d2), 0, places=3)  # 가이드점과 사실상 같은 방향

    def test_front_shoulder_slope_follows_the_back(self):
        """어깨너비를 넓히면(드롭 숄더) 앞어깨 기울기도 뒤를 따라 완만해진다."""
        blk = Block.load(ROOT / "blocks" / "sichuni_basic.yaml")
        base, wide = blk.evaluate(), blk.evaluate({"뒤어깨끝너비": 9.5})
        self.assertLess(wide.measurements["앞어깨기울기"], base.measurements["앞어깨기울기"])
        for r in (base, wide):
            m = r.measurements
            self.assertAlmostEqual(m["앞어깨기울기"] - m["뒤어깨기울기"], m["앞어깨추가기울기"])

    def test_scapula_dart_perpendicular_to_shoulder(self):
        p = self.res.points
        shoulder = (p["SP_B"] - p["SNP_B"]).unit()
        leg = p["SCAP"] - p["BSD_M"]
        self.assertAlmostEqual(shoulder.dot(leg), 0, places=9)
        self.assertAlmostEqual(leg.length(), 4)

    def test_dart_width(self):
        p = self.res.points
        self.assertAlmostEqual(p["BSD1"].dist(p["BSD2"]), 0.25)
        self.assertAlmostEqual(p["BD1"].dist(p["BD2"]), 1.25)

    def test_armholes_meet_at_underarm(self):
        r = self.res
        self.assertEqual(r.line("앞암홀").pts[-1], r.points["UP"])
        self.assertEqual(r.line("뒤암홀").pts[-1], r.points["UP"])

    def test_resize_moves_points(self):
        big = Block.load(ROOT / "blocks" / "sichuni_basic.yaml").evaluate({"B": 37.5})
        self.assertAlmostEqual(big.points["UP"].x, 10)
        self.assertAlmostEqual(big.points["TOP_CB"].x, 20)


class SichuniDartless(unittest.TestCase):
    """기본 원형을 상속한 무다트 원형."""

    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "sichuni_dartless.yaml").evaluate()

    def test_inherits_and_overrides(self):
        r = self.res
        self.assertEqual(r.block.data["extends_from"], "sichuni_basic")
        self.assertAlmostEqual(r.points["UP"].x, 9.25)  # 여유 3.1/2
        self.assertAlmostEqual(r.points["UP"].y, 8.625)
        self.assertAlmostEqual(r.points["HEM_CF"].y, r.points["WL_CB"].y)  # 앞처짐 0 → 밑단 같은 높이
        self.assertAlmostEqual(r.points["SNP_F"].y, -1.25)  # 앞올림

    def test_no_darts(self):
        names = {l.name for l in self.res.lines}
        self.assertNotIn("가슴다트", names)
        self.assertNotIn("뒤어깨다트", names)
        self.assertIn("앞옆선", names)
        self.assertIn("뒤어깨선", names)
        self.assertNotIn("BP", self.res.points)

    def test_front_shoulder_is_back_minus_ease(self):
        r = self.res
        self.assertAlmostEqual(r.line("앞어깨선").length(), r.line("뒤어깨선").length() - 0.25, places=6)


class Sleeve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "sleeve_basic.yaml").evaluate()

    def test_cap_height_formula(self):
        m = self.res.measurements
        self.assertAlmostEqual(m["소매산높이"], (m["앞AH"] + m["뒤AH"]) / 3 + m["소매산조정"])

    def test_slant_lengths(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(p["SCP"].dist(p["BIC_B"]), m["뒤AH"] + m["뒤소매이세"])
        self.assertAlmostEqual(p["SCP"].dist(p["BIC_F"]), m["앞AH"] + m["앞소매이세"])

    def test_crossing_on_slant(self):
        p = self.res.points
        d = (p["BIC_B"] - p["SCP"]).unit()
        self.assertAlmostEqual((p["X_B"] - p["SCP"]).cross(d), 0, places=9)

    def test_elbow_on_elbow_line(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(p["EL_B"].y, m["팔꿈치길이"])
        self.assertAlmostEqual(p["EL_F"].y, m["팔꿈치길이"])

    def test_cap_level_sets_adjust_and_ease(self):
        b = Block.load(ROOT / "blocks" / "sleeve_basic.yaml")
        low = b.evaluate({"소매산단계": "낮음"}).measurements
        self.assertAlmostEqual(low["소매산조정"], -1.625)
        self.assertAlmostEqual(low["앞소매이세"], -0.5)
        # 이세는 단계와 별개로 덮어쓸 수 있다
        custom = b.evaluate({"소매산단계": "낮음", "앞소매이세": "1/4"}).measurements
        self.assertAlmostEqual(custom["소매산조정"], -1.625)
        self.assertAlmostEqual(custom["앞소매이세"], 0.25)
        with self.assertRaises(ValueError):
            b.evaluate({"소매산단계": "없는단계"})

    def test_lower_cap_lowers_height(self):
        low = Block.load(ROOT / "blocks" / "sleeve_basic.yaml").evaluate({"소매산조정": -1.625})
        self.assertLess(low.measurements["소매산높이"], self.res.measurements["소매산높이"])
        self.assertGreater(low.points["BIC_F"].x - low.points["BIC_B"].x,
                           self.res.points["BIC_F"].x - self.res.points["BIC_B"].x)  # 낮은 소매산 → 소매통 넓어짐


class Blouse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "shirt_collar_blouse_body.yaml").evaluate()

    def test_overrides_from_block(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(m["진동깊이"], 8.25)
        self.assertAlmostEqual(p["UP2"].x, 9.25)  # 옆선여유 1/4
        self.assertAlmostEqual(p["UP2"].y, 8.375)  # 진동내림 1/8
        self.assertAlmostEqual(p["HEM_CF"].y, 25.0)  # 기장 + 앞내림 + 앞처짐(가슴다트 3/4)
        self.assertAlmostEqual(p["CB_WL"].x, 17.25)  # 뒤중심 허리 3/4 들임

    def test_front_shoulder_equals_back(self):
        r = self.res
        self.assertAlmostEqual(r.line("앞어깨선").length(), r.line("뒤어깨선").length(), places=6)

    def test_waist_darts(self):
        p = self.res.points
        self.assertAlmostEqual(p["FWD_L"].x, 5.375)
        self.assertAlmostEqual(p["FWD_R"].x - p["FWD_L"].x, 1)
        self.assertAlmostEqual(p["CB_WL"].x - p["BWD_R"].x, 4.25)

    def test_style_sleeve_follows_blouse_armhole(self):
        from patterncad.style import Style

        res = Style.load(ROOT / "styles" / "shirt_collar_blouse.yaml").evaluate()
        s, b = res["sleeve"], res["body"]
        self.assertAlmostEqual(s.measurements["AH"], b.line("앞암홀").length() + b.line("뒤암홀").length())
        self.assertAlmostEqual(s.measurements["소매산조정"], -0.5)


class Skirt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "skirt_basic.yaml").evaluate()

    def test_widths_and_darts(self):
        m = self.res.measurements
        self.assertAlmostEqual(m["앞폭"], 8.75)
        self.assertAlmostEqual(m["뒤폭"], 9.25)
        self.assertAlmostEqual(m["허리4"], 6.625)
        # 다트 분량 = 폭 − (W/4 + 이세) − 옆선들임, 둘로 나눔
        self.assertAlmostEqual(m["앞다트폭"], (8.75 - 6.625 - 0.75) / 2)
        self.assertAlmostEqual(m["뒤다트폭"], (9.25 - 6.625 - 0.75) / 2)

    def test_waist_adds_up(self):
        """허리선 길이(직선 근사) − 다트 = W/4 + 이세."""
        p, m = self.res.points, self.res.measurements
        front = p["CF_W"].dist(p["SS_WF"]) - 2 * m["앞다트폭"]
        self.assertAlmostEqual(front, m["허리4"], delta=0.01)

    def test_dart_tip_leans_to_side(self):
        p = self.res.points
        self.assertAlmostEqual(p["FD1_T"].x - p["FD1_C"].x, 0.125)
        self.assertAlmostEqual(p["BD1_T"].x - p["BD1_C"].x, -0.125)

    def test_waistband_links(self):
        from patterncad.style import Style

        res = Style.load(ROOT / "styles" / "basic_skirt.yaml").evaluate()
        self.assertAlmostEqual(res["waistband"].measurements["길이"], 2 * res["skirt"].measurements["허리4"])


class Hipbone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "skirt_hipbone.yaml").evaluate()

    def test_child_value_overrides_parent_formula(self):
        """상속에서 자식의 value 가 부모의 formula 를 이긴다."""
        m = self.res.measurements
        self.assertAlmostEqual(m["뒤다트폭"], 1.0)
        self.assertAlmostEqual(m["앞다트폭"], 0.0)

    def test_front_narrower_than_back(self):
        m = self.res.measurements
        self.assertAlmostEqual(m["앞폭"], 8.5)
        self.assertAlmostEqual(m["뒤폭"], 9.5)

    def test_front_side_seam_takes_more(self):
        """앞 옆솔기가 뒤보다 더 들어가야 옆솔기가 뒤로 기울지 않는다."""
        m = self.res.measurements
        self.assertGreater(m["앞옆선들임"], m["뒤옆선들임"])

    def test_no_front_dart(self):
        names = {l.name for l in self.res.lines}
        self.assertNotIn("앞다트1", names)
        self.assertIn("뒤다트", names)

    def test_dart_tip_above_hip(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(m["엉덩이길이"] - p["BD_T"].y, 1.375)


class CurvedWaistband(unittest.TestCase):
    def test_arc_lengths_match_measurements(self):
        r = Block.load(ROOT / "blocks" / "waistband_curved.yaml").evaluate({"밑선": 8.0, "차이": 0.75, "높이": 1.5})
        self.assertAlmostEqual(r.line("밑선").length(), 8.0, places=2)  # 베지어 근사 오차 0.0005"
        self.assertAlmostEqual(r.line("윗선").length(), 7.25, places=2)

    def test_bigger_difference_curves_more(self):
        b = Block.load(ROOT / "blocks" / "waistband_curved.yaml")
        small = b.evaluate({"차이": 0.5}).measurements["각도"]
        big = b.evaluate({"차이": 0.75}).measurements["각도"]
        self.assertGreater(big, small)

    def test_top_is_waist_circumference(self):
        from patterncad.style import Style

        res = Style.load(ROOT / "styles" / "hipbone_skirt.yaml").evaluate()
        top = 2 * (res["waistband_front"].measurements["윗선"] + res["waistband_back"].measurements["윗선"])
        self.assertAlmostEqual(top, 28.9, delta=0.6)  # 사이즈표 28.1/8


class Trig(unittest.TestCase):
    def test_degrees_and_polar(self):
        env = Env({}, {"C": Pt(0, 0)})
        self.assertAlmostEqual(evaluate("sin(30)", env), 0.5)
        self.assertAlmostEqual(evaluate("cos(60)", env), 0.5)
        self.assertAlmostEqual(evaluate("atan2(1, 1)", env), 45)


class ALine(unittest.TestCase):
    """절개-벌림 (첫 기하 조작)."""

    @classmethod
    def setUpClass(cls):
        cls.b = Block.load(ROOT / "blocks" / "skirt_aline.yaml")

    def hips(self, res):
        p = res.points
        return p["SS_H_F"].x + (p["CB_HEM"].x - p["SS_H_B"].x)

    def test_semi_a_adds_half_inch_at_hip(self):
        """문서: 세미A 는 힙본보다 엉덩이둘레가 최대 1/2 커진다."""
        r = self.b.evaluate({"실루엣": "세미A"})
        self.assertAlmostEqual(2 * (self.hips(r) - 18.0), 0.5, delta=0.1)

    def test_a_line_adds_one_to_one_and_half(self):
        """문서: A라인은 1 ~ 1.1/2 커진다."""
        r = self.b.evaluate({"실루엣": "A라인"})
        self.assertGreaterEqual(2 * (self.hips(r) - 18.0), 1.0)
        self.assertLessEqual(2 * (self.hips(r) - 18.0), 1.5)

    def test_side_seam_released(self):
        self.assertAlmostEqual(self.b.evaluate().measurements["앞옆선들임"], 0.625)

    def test_spread_goes_to_hem_and_outline_is_closed(self):
        """다트 분량이 밑단으로 가고, 외곽선은 쐐기를 메운 채 이어진다."""
        r = self.b.evaluate({"실루엣": "A라인"})
        p = r.points
        self.assertAlmostEqual(p["HEM_F_OUT"].x - p["HEM_F_IN"].x, 1.0, delta=0.02)
        # 앞밑단이 앞중심에서 옆선까지 한 줄로 이어진다 (벌어진 자리가 열려 있지 않다)
        hem = r.line("앞밑단")
        self.assertEqual(hem.pts[0], p["CF_HEM"])
        self.assertEqual(hem.pts[-1], p["SS_HEM_F"])

    def test_hem_grows_but_stays_a_line(self):
        r = self.b.evaluate({"실루엣": "A라인"})
        grow = 2 * (r.line("앞밑단").length() + r.line("뒤밑단").length()) - 36.0
        self.assertGreater(grow, 1.0)
        self.assertLess(grow, 8.0)  # 8 이상이면 A라인이 아니라 플레어

    def test_rotate_and_mirror_rules(self):
        from patterncad.geometry import Pt

        env_pts = {"C": Pt(0, 0), "P": Pt(1, 0), "A": Pt(0, 0), "B": Pt(0, 1)}
        blk = Block({"measurements": {}, "points": {
            "C": {"at": [0, 0]}, "P": {"at": [1, 0]}, "A": {"at": [0, 0]}, "B": {"at": [0, 1]},
            "R": {"rotate": {"of": "P", "center": "C", "angle": 90}},
            "M": {"mirror": {"of": "P", "line": ["A", "B"]}},
        }, "lines": []})
        res = blk.evaluate()
        self.assertAlmostEqual(res.points["R"].x, 0, places=9)
        self.assertAlmostEqual(res.points["R"].y, 1, places=9)
        self.assertAlmostEqual(res.points["M"].x, -1, places=9)


class Tapered(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "skirt_tapered.yaml").evaluate()
        cls.hb = Block.load(ROOT / "blocks" / "skirt_hipbone.yaml").evaluate()

    def waist(self, res, names):
        return sum(res.line(n).length() for n in names)

    def test_folded_waist_matches_block(self):
        """턱을 접으면 허리가 힙본 원형과 (뒤다트를 흡수해) 맞는다."""
        fin = self.waist(self.res, ["앞허리선1", "앞허리선2", "앞허리선3", "뒤허리선1", "뒤허리선2", "뒤허리선3"])
        hb = self.waist(self.hb, ["앞허리선", "뒤허리선1", "뒤허리선2"])
        self.assertAlmostEqual(fin, hb, delta=0.5)

    def test_hem_narrower_than_hip(self):
        p = self.res.points
        hem = p["SS_HEM_FT"].x + (p["CB_HEM"].x - p["SS_HEM_BT"].x)
        hip = p["SS_H_FT"].x + (p["CB_HEM"].x - p["SS_H_BT"].x)
        self.assertLess(hem, hip)  # 테이퍼드 — 아래로 모인다
        self.assertLess(2 * hem, 36.0)  # 힙본 밑단보다 좁다

    def test_folding_a_tuck_lands_on_the_other_leg(self):
        """턱을 접으면 바깥 다리가 안쪽 다리에 딱 맞아야 허리선이 오비선과 이어진다."""
        p, m = self.res.points, self.res.measurements
        for outer, pivot, ang, inner, sign in [("FT1_R", "P1_B", "앞턱1각", "FT1_L", -1),
                                               ("FT2_R", "P2_B", "앞턱2각", "FT2_L", -1),
                                               ("BT2_L", "Q2_B", "뒤턱2각", "BT2_R", 1)]:
            c = p[pivot]
            self.assertAlmostEqual((c + (p[outer] - c).rotate(sign * m[ang])).dist(p[inner]), 0, places=6)

    def test_back_first_tuck_absorbs_the_dart(self):
        """뒤턱1 은 힙본의 뒤다트 1" 도 함께 접는다."""
        p, m = self.res.points, self.res.measurements
        c = p["Q1_B"]
        folded = c + (p["BT1_L"] - c).rotate(m["뒤턱1각"])
        self.assertAlmostEqual(folded.dist(p["BT1_R"]), m["뒤다트흡수"], places=3)

    def test_tuck_waist_arc_is_at_leg_radius(self):
        """턱 위 허리선은 다리와 같은 반지름의 호 — 접으면 서로 겹친다."""
        p = self.res.points
        self.assertAlmostEqual(p["P1_B"].dist(p["FT1_P"]), p["P1_B"].dist(p["FT1_L"]), places=6)

    def test_tuck_length_scales_with_amount(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(p["FT1_M"].dist(p["FT1_TIP"]), m["앞턱1량"] * m["턱길이비"], places=6)
        self.assertGreater(p["FT1_M"].dist(p["FT1_TIP"]), p["BT2_M"].dist(p["BT2_TIP"]))


class Pants(unittest.TestCase):
    """팬츠 8종 — 계산된 허리·엉덩이·밑단이 포트폴리오 사이즈표와 맞는가."""

    # 원형 id → (허리둘레, 엉덩이둘레, 밑단둘레)
    SIZES = {
        "pants_basic":    (26.5,   36,     8),
        "pants_hipbone":  (29.625, 36,     7.5),
        "pants_onetuck":  (29.625, 38.375, 5.75),
        "pants_tapered":  (29.625, 42,     6),
        "pants_training": (37.75,  38.5,   9),
        "pants_wide":     (28,     36.5,   12),
        "pants_skinny":   (29.5,   34.5,   5.25),
        "leggings":       (27,     31,     4.75),
    }

    def test_size_table(self):
        for bid, (waist, hip, hem) in self.SIZES.items():
            with self.subTest(bid):
                res = Block.load(ROOT / "blocks" / f"{bid}.yaml").evaluate()
                m = res.measurements
                # 허리 = 다트·턱을 뺀 허리선들의 합 × 2 (앞·뒤 반쪽씩 제도한다)
                w = 2 * sum(l.length() for l in res.lines
                            if l.name.startswith(("앞허리선", "뒤허리선")))
                # 허리선이 곡선이라 직선 합보다 조금 길게 나온다 — 0.25 까지 인정
                self.assertAlmostEqual(w, waist, delta=0.25)
                self.assertAlmostEqual(2 * (m["앞폭"] + m["뒤폭"]), hip, delta=0.02)
                self.assertAlmostEqual((m["앞밑단폭"] + m["뒤밑단폭"]) / 2, hem, delta=0.02)

    def test_back_crotch_extension_is_larger_than_front(self):
        """뒤샅은 언제나 앞샅보다 크다 — 엉덩이가 뒤에 있기 때문."""
        for bid in self.SIZES:
            with self.subTest(bid):
                m = Block.load(ROOT / "blocks" / f"{bid}.yaml").evaluate().measurements
                self.assertGreater(m["뒤샅"], m["앞샅"])

    def test_back_rise_angle_lays_the_center_back(self):
        """뒤중심을 눕힐수록 뒤밑위 길이가 길어진다(캐주얼), 세울수록 짧아진다(정장) — p.28."""
        blk = Block.load(ROOT / "blocks" / "pants_basic.yaml")

        def rise(angle):
            r = blk.evaluate({"뒤중심각": angle})
            return r.points["B_W_CB"].dist(r.points["B_CL_CB"]) + r.line("뒤밑위곡선").length()

        self.assertGreater(rise(8), rise(2.5))
        self.assertGreater(rise(2.5), rise(0))

    def test_back_rise_angle_widens_the_back_waist(self):
        """눕히면 뒤중심 허리점이 옆으로 나가고, 허리선이 직각을 지키느라 위로도 올라간다 (p.30)."""
        blk = Block.load(ROOT / "blocks" / "pants_basic.yaml")
        wide, narrow = blk.evaluate({"뒤중심각": 8}), blk.evaluate({"뒤중심각": 0})
        self.assertGreater(wide.points["B_W_CB"].x, narrow.points["B_W_CB"].x)
        self.assertLess(wide.points["B_W_CB"].y, narrow.points["B_W_CB"].y)

    def test_elastic_band_is_smaller_than_the_drafted_waist(self):
        """고무줄 밴드는 제도된 허리보다 작다 — 레깅스는 2~3."""
        from patterncad.style import Style

        res = Style.load(ROOT / "styles" / "leggings.yaml").evaluate()
        band = res["band"].measurements
        self.assertAlmostEqual(band["패턴허리"], res["pants"].measurements["패턴허리"])
        self.assertTrue(2 <= band["패턴허리"] - band["완성"] <= 3)


class Tops(unittest.TestCase):
    """상의 12종 — 계산된 가슴·허리·엉덩이가 포트폴리오 사이즈표와 맞는가."""

    # 원형 id → (가슴, 허리, 엉덩이)  None = 사이즈표에 없거나 다른 방식으로 잰다
    SIZES = {
        "shirt_collar_blouse_body": (36,      30,     37.75),   # 표기 가슴 35.1/2 — 도면은 36
        "oversize_blouse_body":     (45.5,    45.375, 45.375),
        "china_collar_blouse_body": (37.25,   36.625, 38.75),
        "pussy_bow_blouse_body":    (40,      39.25,  39.25),
        "sweat_shirt_body":         (44,      42,     40),
        "oversize_hoodie_body":     (47.25,   46,     None),
        "tight_t_shirt_body":       (32.375,  30,     33.5),
        "sleeveless_dress_body":    (34.875,  28,     37.875),
        "h_line_dress_body":        (34.75,   28.625, 37),
        "mermaid_dress_body":       (35.5,    29.625, 37.375),
        "flat_collar_dress_body":   (36.875,  29.75,  40),
        "jump_suite_body":          (44.25,   41.75,  None),
    }

    def test_size_table(self):
        for bid, (bust, waist, hip) in self.SIZES.items():
            with self.subTest(bid):
                m = Block.load(ROOT / "blocks" / f"{bid}.yaml").evaluate().measurements
                self.assertAlmostEqual(m["패턴가슴"], bust, delta=0.02)
                self.assertAlmostEqual(m["패턴허리"], waist, delta=0.2)
                if hip is not None:
                    self.assertAlmostEqual(m["패턴엉덩이"], hip, delta=0.2)

    def test_front_and_back_side_seams_are_separate(self):
        """옆선을 앞·뒤 따로 잡아야 허리를 줄인 만큼 둘레가 줄어든다."""
        blk = Block.load(ROOT / "blocks" / "top_body.yaml")
        loose, tight = blk.evaluate({"옆선허리들임": 0}), blk.evaluate({"옆선허리들임": 1})
        self.assertAlmostEqual(loose.measurements["패턴허리"] - tight.measurements["패턴허리"], 4)
        # 앞 옆선은 안쪽으로, 뒤 옆선은 바깥쪽으로 (뒤중심에서 보면 역시 안쪽으로)
        p = tight.points
        self.assertLess(p["WL_SS_F"].x, p["WL_SS_B"].x)

    def test_neck_rule_p54(self):
        """앞네크너비 1/8 깎으면 앞중심네크 3/16 내림(2:3), 뒤는 1/16 내림(2:1)."""
        blk = Block.load(ROOT / "blocks" / "top_body.yaml")
        base, cut = blk.evaluate(), blk.evaluate({"앞네크깎음": 0.125, "뒤네크깎음": 0.125})
        self.assertAlmostEqual(base.measurements["앞목너비"] - cut.measurements["앞목너비"], 0.125)
        self.assertAlmostEqual(cut.measurements["앞목깊이"] - base.measurements["앞목깊이"], 0.1875)
        self.assertAlmostEqual(base.measurements["뒤목너비"] - cut.measurements["뒤목너비"], 0.125)
        self.assertAlmostEqual(base.measurements["뒤목점올림"] - cut.measurements["뒤목점올림"], 0.0625)

    def test_hood_bottom_is_shorter_than_the_neckline(self):
        """후드 밑선은 바이어스라 네크보다 1/8 작게 제도해 늘려 봉제한다 (p.60)."""
        res = Block.load(ROOT / "blocks" / "hood.yaml").evaluate()
        m = res.measurements
        self.assertAlmostEqual(m["후드밑선"], m["네크길이"] - m["밑선줄임"])
        self.assertAlmostEqual(res.line("후드밑선").length(), m["후드밑선"], delta=0.1)

    def test_two_piece_sleeve_keeps_the_sleeve_width(self):
        """큰소매 + 작은소매 = 한 장 소매의 소매통."""
        res = Block.load(ROOT / "blocks" / "sleeve_two_piece.yaml").evaluate()
        p, m = res.points, res.measurements
        one = p["BIC_F"].x - p["BIC_B"].x
        upper = p["FS_T"].x - p["BS_T"].x
        self.assertAlmostEqual(upper + m["작은소매폭"], one)

    def test_flat_collar_outer_edge_grows_with_width(self):
        """플랫 칼라: 폭이 넓어질수록 외곽둘레가 커지고, 겹침이 클수록 곡이 세진다 (p.70)."""
        blk = Block.load(ROOT / "blocks" / "flat_collar.yaml")
        narrow, wide = blk.evaluate({"드러난폭": 2.75}), blk.evaluate({"드러난폭": 4})
        self.assertGreater(wide.measurements["외곽둘레"], narrow.measurements["외곽둘레"])
        flat, curved = blk.evaluate({"겹침분": 0.5}), blk.evaluate({"겹침분": 2.5})
        self.assertGreater(flat.measurements["반지름"], curved.measurements["반지름"])
        # 목둘레선은 어떤 경우에도 몸판 목선보다 칼라줄임만큼 짧다
        for r in (narrow, wide, flat, curved):
            self.assertAlmostEqual(r.line("목둘레선").length(), r.measurements["목선길이"], delta=0.02)

    def test_rib_is_smaller_than_the_body(self):
        """시보리는 몸판 둘레보다 작다. 블루종 핏은 10cm(≈4") 이상 (p.58)."""
        from patterncad.style import Style

        res = Style.load(ROOT / "styles" / "sweat_shirt.yaml").evaluate()
        rib = res["밑단시보리"].measurements
        self.assertLess(rib["길이"], res["body"].measurements["패턴엉덩이"])
        self.assertGreaterEqual(rib["줄임"], 4)


class Jackets(unittest.TestCase):
    """자켓 9종 — 계산된 가슴·허리·엉덩이·밑단이 포트폴리오 사이즈표와 맞는가."""

    # 원형 id → (가슴, 허리, 엉덩이, 밑단)
    SIZES = {
        "jacket_body":               (35.25,  30.375, 38.375, 38),      # 테일러드
        "hourglass_jacket_body":     (36.375, 30.75,  38.375, 45.625),
        "half_double_jacket_body":   (34.125, 29,     37.375, 35.375),
        "one_button_jacket_body":    (35.875, 29,     37.375, 36.25),
        "shawl_collar_jacket_body":  (34.375, 32.25,  38,     40.375),
        "stand_collar_jacket_body":  (34.375, 29.5,   37.375, 41.125),
        "hunting_jacket_body":       (46.375, 46.625, 47.625, None),
        "rider_jacket_body":         (35.875, 34.25,  None,   35.625),
        "oversized_jacket_body":     (43.125, 41.25,  44,     44.25),
    }

    def test_size_table(self):
        for bid, (bust, waist, hip, hem) in self.SIZES.items():
            with self.subTest(bid):
                m = Block.load(ROOT / "blocks" / f"{bid}.yaml").evaluate().measurements
                self.assertAlmostEqual(m["패턴가슴"], bust, delta=0.02)
                self.assertAlmostEqual(m["패턴허리"], waist, delta=0.2)
                if hip is not None:
                    self.assertAlmostEqual(m["패턴엉덩이"], hip, delta=0.2)
                if hem is not None:
                    self.assertAlmostEqual(m["패턴밑단"], hem, delta=0.2)

    def test_front_back_width_difference_is_a_jacket_value(self):
        """자켓의 앞뒤 품 차이는 5/8~3/4 (원형은 1/2) — p.76."""
        for bid in self.SIZES:
            with self.subTest(bid):
                m = Block.load(ROOT / "blocks" / f"{bid}.yaml").evaluate().measurements
                self.assertGreaterEqual(m["뒤품"] - m["앞품"], 0.625 - 1e-9)

    def test_waist_dart_is_within_the_princess_limit(self):
        """사이바 라인의 허리 다트는 1~1.3/8, 1.1/2 이상이면 무리한 다트량 — p.75."""
        m = Block.load(ROOT / "blocks" / "jacket_body.yaml").evaluate().measurements
        self.assertTrue(1 <= m["허리다트폭"] <= 1.375)

    def test_shoulder_pad_raises_the_shoulder_by_80_percent(self):
        """패드가 들어가면 패드 두께의 80% 만큼 어깨끝점을 올린다 — p.80."""
        blk = Block.load(ROOT / "blocks" / "jacket_body.yaml")
        flat, padded = blk.evaluate(), blk.evaluate({"패드두께": 0.375})
        raised = flat.points["SP_B"].y - padded.points["SP_B"].y
        self.assertAlmostEqual(raised, 0.375 * 0.8 * 0.625)

    def test_hip_point_raise_widens_the_hem(self):
        """엉덩이 포인트를 올릴수록 밑단이 커진다 — p.80 아워글라스."""
        blk = Block.load(ROOT / "blocks" / "jacket_body.yaml")
        low, high = blk.evaluate({"힙포인트올림": 0}), blk.evaluate({"힙포인트올림": 2})
        self.assertLess(low.points["HL_SS_F"].y, high.points["HL_SS_F"].y + 2.001)
        self.assertGreater(low.points["HL_SS_F"].y, high.points["HL_SS_F"].y)

    def test_collar_back_length_is_at_least_three_inches(self):
        """칼라 뒤중심 길이는 3 이상이어야 칼라밴드를 덮는다 — p.75."""
        for bid in ("tailored_collar", "shawl_collar"):
            with self.subTest(bid):
                m = Block.load(ROOT / "blocks" / f"{bid}.yaml").evaluate().measurements
                self.assertGreaterEqual(m["뒤중심길이"], 3)

    def test_collar_outer_edge_grows_with_collar_width(self):
        """칼라 폭이 넓을수록 외곽길이가 길어야 칼라가 눌리지 않는다 — p.76."""
        blk = Block.load(ROOT / "blocks" / "tailored_collar.yaml")
        narrow, wide = blk.evaluate({"칼라폭": 1.5}), blk.evaluate({"칼라폭": 2.5})
        self.assertGreater(wide.measurements["외곽늘림"], narrow.measurements["외곽늘림"])
        self.assertGreater(wide.measurements["외곽둘레"], narrow.measurements["외곽둘레"])


class StyleLink(unittest.TestCase):
    """몸판 암홀 길이가 소매로 자동 전달되는 스타일."""

    def test_armhole_length_flows_to_sleeve(self):
        from patterncad.style import Style

        st = Style.load(ROOT / "styles" / "sichuni_with_sleeve.yaml")
        res = st.evaluate()
        self.assertAlmostEqual(res["sleeve"].measurements["앞AH"], res["body"].line("앞암홀").length())
        self.assertAlmostEqual(res["sleeve"].measurements["뒤AH"], res["body"].line("뒤암홀").length())
        big = st.evaluate({"body.B": 37.5})
        self.assertGreater(big["sleeve"].measurements["앞AH"], res["sleeve"].measurements["앞AH"])


if __name__ == "__main__":
    unittest.main()
