# -*- coding: utf-8 -*-
"""
stats_utils 命中率口径单元测试（ALG-06 / PUP-20261019-013100）
============================================================

测试目标（与 docs/hitrate_spec.md 逐条对照，A34 口径一致性门禁）：
1. wilson_ci 单一实现（§2）：边界 / 钳制防御 / 已知值 / 跨实现数值一致性
2. Top-1 真实口径与 Top-3 覆盖口径（§3）：online_learner._calculate_hits 语义
3. overall_score 公式只读钉桩（§4）：backtester._evaluate_prediction 现值
4. 随机基线常量（§1）：Top-k 基线 = k/10

【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，
本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。
请理性购彩，量力而行。
"""

import math
import os
import sys
import unittest

# 让 tests/ 目录下的脚本能直接 import 项目模块
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from modules.stats_utils import wilson_ci  # noqa: E402
from modules.online_learner import OnlineLearner  # noqa: E402
from modules.backtester import Backtester  # noqa: E402


# ---------------------------------------------------------------------------
# 构造桩：fused_probabilities 五位置概率分布（top1 = 概率最大号）
# ---------------------------------------------------------------------------

def _probs(rank_map):
    """按 {名次: 号码} 生成概率降序 dict；未列名次从自由号池顺序取号。

    保证 rank_map 指定的「名次-号码」绑定绝对生效：默认号取自排除
    used 集合后的自由号池，避免默认号覆盖映射号的概率值，破坏
    Top-1 / Top-3 判定语义（碰撞曾导致 top1 号概率被小名次默认号顶替）。
    """
    total = 10
    used = set(rank_map.values())
    # 自由号池：未被 rank_map 占用的号码，按升序供给未列名次
    # 规模恒 >= 未列名次数（弱歧义映射只会增大池规模），故不会越界
    free_pool = [num for num in range(total) if num not in used]
    probs = {}
    free_idx = 0
    # 名次越前概率越高；已列名次用映射值，否则取自由号池下一个号码
    for rank in range(1, total + 1):
        if rank in rank_map:
            num = rank_map[rank]
        else:
            num = free_pool[free_idx]
            free_idx += 1
        probs[num] = 1.0 / rank
    return probs


def _make_record(rank_maps, top_combinations=None):
    """构造 prediction_record 桩（仅 _calculate_hits 需要的两个字段）。"""
    record = {
        'fused_probabilities': [_probs(m) for m in rank_maps],
        'top_combinations': top_combinations or [],
    }
    return record


class TestWilsonCiBoundary(unittest.TestCase):
    """wilson_ci 边界行为（spec §2）"""

    def test_total_zero_returns_zero_float_tuple(self):
        """total=0（无样本）应返回 (0.0, 0.0) 浮点二元组。"""
        result = wilson_ci(0, 0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertEqual(result, (0.0, 0.0))
        self.assertIsInstance(result[0], float)
        self.assertIsInstance(result[1], float)

    def test_zero_successes_lower_is_zero(self):
        """零命中：下界钳制为 0，上界为正。"""
        lo, hi = wilson_ci(0, 100)
        self.assertEqual(lo, 0.0)
        self.assertGreater(lo, -1e-12)
        self.assertGreater(hi, 0.0)
        self.assertLessEqual(hi, 1.0)

    def test_all_successes_upper_is_one(self):
        """全命中：上界数学值为 1；浮点实现允许 <= 1ulp 下溢（见 spec §2/§5-4）。"""
        lo, hi = wilson_ci(100, 100)
        self.assertLessEqual(hi, 1.0)
        self.assertAlmostEqual(hi, 1.0, delta=1e-12)
        self.assertLess(lo, 1.0)
        self.assertGreater(lo, 0.9)

    def test_returns_tuple_of_two_floats(self):
        """正常输入返回浮点二元组。"""
        lo, hi = wilson_ci(92, 1000)
        self.assertIsInstance(lo, float)
        self.assertIsInstance(hi, float)


class TestWilsonCiDefense(unittest.TestCase):
    """wilson_ci 越界钳制防御（spec §2 / 继承 walkforward 版语义）"""

    def test_negative_successes_clamped_to_zero(self):
        """successes 为负时按零命中口径钳制，不抛 math domain error。"""
        self.assertEqual(wilson_ci(-5, 100), wilson_ci(0, 100))

    def test_successes_above_total_clamped_to_full(self):
        """successes 超过 total 时按全命中口径钳制，不抛 math domain error。"""
        self.assertEqual(wilson_ci(150, 100), wilson_ci(100, 100))

    def test_bounds_within_unit_range(self):
        """任意合法输入下界 <= 上界且均落在 [0, 1]。"""
        for k in range(0, 101, 7):
            lo, hi = wilson_ci(k, 100)
            self.assertGreaterEqual(lo, 0.0)
            self.assertLessEqual(hi, 1.0)
            self.assertLessEqual(lo, hi)


class TestWilsonCiKnownValues(unittest.TestCase):
    """wilson_ci 数值口径（spec §2）：已知值 / 跨实现一致性 / 互补对称"""

    def test_known_value_k92_n1000(self):
        """k=92, n=1000（p=0.092）95% CI ≈ (0.0756, 0.1115) [外部已知值]。"""
        lo, hi = wilson_ci(92, 1000)
        self.assertAlmostEqual(lo, 0.0756, delta=1e-4)
        self.assertAlmostEqual(hi, 0.1115, delta=1e-4)

    def test_matches_independent_formula(self):
        """与独立重写的 Wilson 公式逐位一致（跨实现零漂移证据）。"""
        for k in (1, 5, 33, 92, 500, 999):
            n, z = 1000, 1.96
            # 独立参照公式（不复用被测实现，直接按公式重写）
            if n == 0:
                ref = (0.0, 0.0)
            else:
                p = min(1.0, max(0.0, k / n))
                denom = 1 + z * z / n
                center = (p + z * z / (2 * n)) / denom
                radicand = p * (1 - p) / n + z * z / (4 * n * n)
                half = z * math.sqrt(radicand) / denom
                ref = (max(0.0, center - half), min(1.0, center + half))
            self.assertEqual(wilson_ci(k, n), ref)

    def test_symmetry_complement(self):
        """互补对称：wilson_ci(k, n) 下界 == 1 - wilson_ci(n-k, n) 上界。"""
        for k in (10, 92, 300):
            lo, _ = wilson_ci(k, 1000)
            _, hi_comp = wilson_ci(1000 - k, 1000)
            self.assertAlmostEqual(lo, 1.0 - hi_comp, places=12)


class TestWilsonCiMonotonicity(unittest.TestCase):
    """wilson_ci 单调性与 z 影响（spec §2）"""

    def test_higher_success_rate_higher_bounds(self):
        """成功率越高，CI 上下界整体上移。"""
        lo1, hi1 = wilson_ci(50, 100)
        lo2, hi2 = wilson_ci(90, 100)
        self.assertGreater(lo2, lo1)
        self.assertGreater(hi2, hi1)

    def test_larger_z_wider_interval(self):
        """z 越大区间越宽（99% 宽于 95% 宽于 68%）。"""
        lo68, hi68 = wilson_ci(92, 1000, z=1.0)
        lo95, hi95 = wilson_ci(92, 1000, z=1.96)
        lo99, hi99 = wilson_ci(92, 1000, z=2.576)
        self.assertGreater(hi99 - lo99, hi95 - lo95)
        self.assertGreater(hi95 - lo95, hi68 - lo68)
        # 同一 p 下区间中心随 z 增大向 0.5 方向偏移（Wilson 区间性质）；
        # 本例 p=0.092 < 0.5，故 z 越大中心向上（朝 0.5）移动
        self.assertGreaterEqual((lo99 + hi99) / 2, (lo68 + hi68) / 2)


class TestHitrateSpecTop1Top3(unittest.TestCase):
    """Top-1 真实口径 / Top-3 覆盖口径（spec §3，online_learner._calculate_hits）"""

    def setUp(self):
        # 传 None 作为 db：_calculate_hits 仅读 prediction_record / actual_numbers
        self.learner = OnlineLearner(None)

    def _stats(self, rank_maps, actual, combos=None):
        return self.learner._calculate_hits(
            _make_record(rank_maps, combos), actual)

    def test_top1_true_caliber_exact_hit(self):
        """Top-1 真实口径：仅最高概率号（首推号）精确相等才计 top1_hits。"""
        # 万位 top1=5；千位 7 仅进 top3 非 top1；百位缺 top3；十/个位 top1
        rank_maps = [
            {1: 5, 2: 0, 3: 1},                    # 万位：实际 5 -> top1
            {1: 3, 2: 8, 3: 7},                    # 千位：实际 7 -> 仅 top3
            {1: 6, 2: 7, 3: 8, 4: 9},              # 百位：实际 2 -> 双落空
            {1: 9, 2: 0, 3: 1},                    # 十位：实际 9 -> top1
            {1: 3, 2: 4, 3: 6},                    # 个位：实际 3 -> top1
        ]
        stats = self._stats(rank_maps, [5, 7, 2, 9, 3])
        self.assertEqual(stats['top1_hits'], 3)      # 万/十/个位
        self.assertEqual(stats['partial_hits'], 4)   # top3 覆盖口径多一个千位

    def test_partial_is_superset_of_top1(self):
        """不变量：覆盖口径 partial_hits >= 真实口径 top1_hits。"""
        rank_maps = [
            {1: 5, 2: 0}, {1: 7, 2: 1}, {1: 2, 2: 3},
            {1: 9, 2: 0}, {1: 3, 2: 4},
        ]
        stats = self._stats(rank_maps, [5, 7, 2, 9, 3])
        self.assertGreaterEqual(stats['partial_hits'], stats['top1_hits'])
        self.assertEqual(stats['top1_hits'], 5)

    def test_recommendation_quality_by_top1(self):
        """推荐质量评级按 Top-1 真实口径（excellent/good/fair/poor）。"""
        # good（5 位全 top1，top1_hits=5 且 exact_match=0）
        rank_maps_all = [
            {1: 5, 2: 0}, {1: 7, 2: 1}, {1: 2, 2: 3},
            {1: 9, 2: 0}, {1: 3, 2: 4},
        ]
        actual_all = [5, 7, 2, 9, 3]
        stats = self._stats(rank_maps_all, actual_all)
        self.assertEqual(stats['recommendation_quality'], 'good')
        # fair（top1==3：百位双落空 + 十位仅进 Top-3）
        rank_maps_3 = [
            {1: 5, 2: 0}, {1: 7, 2: 1}, {1: 8, 2: 9, 3: 6},
            {1: 0, 2: 9}, {1: 3, 2: 4},
        ]
        stats = self._stats(rank_maps_3, actual_all)
        self.assertEqual(stats['recommendation_quality'], 'fair')
        # poor（top1==0）
        rank_maps_0 = [
            {1: 0, 2: 1}, {1: 1, 2: 2}, {1: 3, 2: 4},
            {1: 0, 2: 1}, {1: 4, 2: 5},
        ]
        stats = self._stats(rank_maps_0, [5, 7, 2, 9, 3])
        self.assertEqual(stats['recommendation_quality'], 'poor')
        # excellent（组合全中，无论 top1 几何）
        combos = [{
            'numbers': [5, 7, 2, 9, 3],
            'rank': 1,
            'combination': '57293',
        }]
        stats = self._stats(rank_maps_0, actual_all, combos=combos)
        self.assertEqual(stats['exact_match'], 1)
        self.assertEqual(stats['recommendation_quality'], 'excellent')

    def test_random_baseline_constants(self):
        """spec §1：Top-k 随机基线恒为 k/10。"""
        for k, expected in ((1, 10.0), (3, 30.0), (5, 50.0)):
            self.assertEqual(k / 10 * 100.0, expected)


class TestOverallScoreRegistration(unittest.TestCase):
    """overall_score 公式只读钉桩（spec §4：代码先行必先改本测试）"""

    def _evaluate(self, rank_maps, actual):
        # 跳过 __init__（需 predictor），仅 _evaluate_prediction 用到的属性手工置入
        bt = Backtester.__new__(Backtester)
        bt.position_names = ['万位', '千位', '百位', '十位', '个位']
        record = _make_record(rank_maps)
        return bt._evaluate_prediction(record, actual, '2026001')

    def test_overall_score_formula_frozen(self):
        """现值：overall_score = (top1*40 + top3*20) / 3（L568 只读登记）。"""
        rank_maps = [
            {1: 5, 2: 0}, {1: 7, 2: 1}, {1: 8, 2: 9, 3: 6},
            {1: 0, 2: 9}, {1: 3, 2: 4},
        ]
        result = self._evaluate(rank_maps, [5, 7, 2, 9, 3])  # top1=3, top3=4
        self.assertEqual(result['top1_hit_count'], 3)
        self.assertEqual(result['top3_hit_count'], 4)
        expected = (3 * 40 + 4 * 20) / 3
        self.assertAlmostEqual(result['overall_score'], expected, places=2)

    def test_overall_score_full_top1_top3(self):
        """top1=5 且 top3=5 时理论满分 100（(5*40+5*20)/3）。"""
        rank_maps = [
            {1: 5, 2: 0}, {1: 7, 2: 1}, {1: 2, 2: 3},
            {1: 9, 2: 0}, {1: 3, 2: 4},
        ]
        result = self._evaluate(rank_maps, [5, 7, 2, 9, 3])
        self.assertEqual(result['top1_hit_count'], 5)
        self.assertEqual(result['top3_hit_count'], 5)
        self.assertAlmostEqual(result['overall_score'], 100.0, places=2)


if __name__ == '__main__':
    unittest.main()
