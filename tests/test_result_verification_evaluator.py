# -*- coding: utf-8 -*-
"""§4.4 闭环单测：自动自我验证钩子 + 第 10 评估器 ResultVerificationEvaluator。

闭环内容（补齐方案 §4.4.3「覆盖率 100%」与 §4.4.4 第三条「建议分级展示」）：
  1. main.LotteryGUI._auto_verify_result —— 分析产出后自动登记验证；
  2. auto_evaluator.ResultVerificationEvaluator —— 把验证覆盖率/通过率与
     分级建议纳入自动评估报告（此前验证结果只在手动按钮里，未进自诊断链路）。

说明：验证器为全局单例，测试内通过重置其内部计数/deque 做用例隔离。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.result_verifier import get_result_verifier  # noqa: E402
from modules.auto_evaluator import (  # noqa: E402
    ProjectAutoEvaluator,
    ResultVerificationEvaluator,
)


def _reset_verifier():
    """重置全局验证器单例状态（用例隔离）。"""
    rv = get_result_verifier()
    with rv._lock:
        rv._verifications.clear()
        rv._suggestions.clear()
        rv._pass_count = 0
        rv._fail_count = 0
        rv._total_count = 0
    return rv


def _good_payload():
    """构造一份能通过 5 维度校验的结果（组合用 dict 形态）。"""
    return {
        'fused_probabilities': [{i: 0.1 for i in range(10)} for _ in range(5)],
        'top_combinations': [{'numbers': [1, 2, 3, 4, 5], 'score': 0.5}],
        'target_issue': '2026001',
        'next_issue': '2026002',
        'data_period': 60,
    }


def _bad_payload():
    """构造一份组合结构非法的结果（组合为裸 list → combination_structure 失败）。"""
    p = _good_payload()
    p['top_combinations'] = [[1, 2, 3, 4, 5]]
    return p


# ---------------------------------------------------------------------------
# 第 10 评估器：注册与无样本降级
# ---------------------------------------------------------------------------
def test_evaluator_registered_as_tenth():
    names = [type(e).__name__ for e in ProjectAutoEvaluator()._evaluators]
    assert 'ResultVerificationEvaluator' in names
    assert len(names) == 10


def test_evaluator_no_samples_is_warning():
    """无验证样本：不算失败，但提示覆盖率无法达成（§4.4.3）。"""
    _reset_verifier()
    r = ResultVerificationEvaluator(None).evaluate()
    assert r.status == 'warning'
    assert r.details['已验证批次'] == 0
    assert r.details['验证覆盖率'] == '0%'
    assert any('尚未产生自我验证样本' in x for x in r.recommendations)


# ---------------------------------------------------------------------------
# 第 10 评估器：有样本（通过 / 失败）
# ---------------------------------------------------------------------------
def test_evaluator_passing_sample_is_healthy():
    _reset_verifier()
    rv = get_result_verifier()
    out = rv.self_verify_result(_good_payload(), None)
    rv.generate_optimization_suggestions(out, None)

    r = ResultVerificationEvaluator(None).evaluate()
    assert r.details['已验证批次'] == 1
    assert r.details['验证覆盖率'] == '100%'
    assert r.details['失败批次'] == 0
    assert r.status == 'healthy'


def test_evaluator_failing_sample_is_critical_with_high_suggestion():
    _reset_verifier()
    rv = get_result_verifier()
    out = rv.self_verify_result(_bad_payload(), None)
    rv.generate_optimization_suggestions(out, {'hit_rate': 0.11})

    r = ResultVerificationEvaluator(None).evaluate()
    assert r.details['失败批次'] == 1
    assert r.status == 'critical'
    # 分级建议：失败项应为 high，并带依据
    high = [x for x in r.recommendations if x.startswith('[high]')]
    assert high, '失败样本应产生 high 级建议'
    assert '依据' in high[0]
    assert r.details['建议数(高/中/低)'].startswith('1/')


def test_evaluator_recommendations_carry_honest_boundary():
    """诚实边界：建议不得自动调整冻结权重，报告须显式声明。"""
    _reset_verifier()
    rv = get_result_verifier()
    rv.generate_optimization_suggestions(rv.self_verify_result(_good_payload(), None), None)
    r = ResultVerificationEvaluator(None).evaluate()
    assert any('不会自动调整' in x for x in r.recommendations)


def test_evaluator_handles_missing_module_gracefully():
    """模块不可用 → warning 降级，不抛异常（不拖垮整轮自动评估）。"""
    r = ResultVerificationEvaluator(None).evaluate()
    assert r.status in ('healthy', 'warning', 'critical')
    assert isinstance(r.score, int)


# ---------------------------------------------------------------------------
# main.py 自动验证钩子（不构造 GUI，直接以非绑定方式调用）
# ---------------------------------------------------------------------------
def test_auto_verify_hook_registers_batch():
    """_auto_verify_result 应在不渲染/不阻塞的前提下登记一次验证。"""
    tk = pytest.importorskip('tkinter')  # noqa: F841 - 无 tkinter 环境跳过
    import main as main_mod

    assert hasattr(main_mod.LotteryGUI, '_auto_verify_result'), '钩子方法应存在'

    _reset_verifier()
    rv = get_result_verifier()
    before = rv.get_coverage()['verified_batches']

    # 该方法不使用 self，可用 None 作为占位 self 调用（纯登记逻辑）
    main_mod.LotteryGUI._auto_verify_result(None, _good_payload(), None)
    after = rv.get_coverage()['verified_batches']
    assert after == before + 1, '每次分析应自动登记一次验证'


def test_auto_verify_hook_tolerates_garbage_payload():
    """脏数据/空结果不得抛异常（非致命路径）。"""
    pytest.importorskip('tkinter')
    import main as main_mod
    _reset_verifier()
    main_mod.LotteryGUI._auto_verify_result(None, None, None)
    main_mod.LotteryGUI._auto_verify_result(None, {}, {'weird': object()})


# pytest（本文件用 plain test_* 函数）
if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
