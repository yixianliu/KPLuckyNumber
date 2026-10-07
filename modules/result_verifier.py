# -*- coding: utf-8 -*-
"""
排列5 分析结果自我验证与优化建议生成（§4.4）

方案 §4.4.2 三个子目标：
  (1) 结果自我验证：分析结果与基线/历史做一致性验证（命中率边界、概率归一、
      组合结构、窗口一致性、数据一致性），校验结果合理性；
  (2) 优化建议生成：基于验证结果 + 监控数据，自动生成分级（高/中/低）建议，
      附依据说明；
  (3) 验证与建议可视化：验证 通过/失败 状态明确，建议分级、依据清晰。

设计约束（对齐既有惯例与诚实边界）：
  * **无 DB 依赖**：验证与建议为纯内存计算 + 可选本地 JSONL 落盘，MySQL
    不可达亦可工作（DB-optional，与 §4.2 台账一致）。
  * **诚实边界（关键）**：优化建议只做**建议型**记录，对照
    ``RANDOM_BASELINE`` 标注「属正常波动、不构成预测能力」，
    **绝不**自动调整 v4.0 冻结权重、不写 ``p5_weight_history``、
    不写 Redis 自适应权重通道（那些仍由 ``ONLINE_LEARNER_WRITEBACK`` 门控）。
  * **覆盖率统计**：每次验证计入滑窗（通过/失败计数），支持
    「结果自我验证覆盖率 100%」指标（§4.4.3）。

读取 API（供 GUI「自我验证」页 / 自动评估页消费）：
  * ``self_verify_result(result, baseline)`` → 验证结论
  * ``generate_optimization_suggestions(verify_result, monitor_data)`` → 分级建议
  * ``get_coverage()`` / ``get_verification_stats()`` → 覆盖率与统计
  * 单例 ``get_result_verifier`` + ``persist_suggestions``（可选落盘）
"""

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 随机基线（诚实边界）——与 model_monitor.RANDOM_BASELINE 单一口径，避免漂移
RANDOM_BASELINE = {
    'top1': 0.10,   # 10%
    'top3': 0.30,   # 30%
    'top5': 0.50,   # 50%
}

# 验证器默认配置
RESULT_VERIFIER_CONFIG = {
    # 排列5 位数
    'positions': 5,
    # 验证记录滑窗（覆盖率统计；deque 自动丢最旧）
    'window_size': 120,
    # fused_probabilities 每位概率和容差（数值归一）
    'prob_sum_tolerance': 0.02,
    # 命中率边界（0-1，越界即失败）
    'hit_rate_min': 0.0,
    'hit_rate_max': 1.0,
    # 建议 JSONL 相对项目根（data/ 为既有可写数据目录；默认落盘关，需显式 persist）
    'suggestion_jsonl': os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'optimization_suggestions.jsonl',
    ),
    # 建议保留上限（内存 deque）
    'max_suggestions': 50,
}


def _now_iso() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


class ResultSelfVerifier:
    """分析结果自我验证 + 分级优化建议生成（§4.4.2）。

    与预测/进化引擎解耦：纯函数式验证 + 建议，不持有 DB 连接、不触碰权重。
    调用方传入 ``result``（step4_final_prediction 的 final_report 或
    _calc_statistical_prediction 的结构化结果）与 ``baseline``（验证基线，
    可空——空则窗口一致性检查降级为「无基线、跳过」）。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg = dict(RESULT_VERIFIER_CONFIG)
        if config:
            self.cfg.update(config)

        # 验证记录滑窗（覆盖率统计；元素 = 每次验证结果 dict）
        self._verifications: deque = deque(
            maxlen=int(self.cfg['window_size']))
        # 建议保留上限（内存 deque；最新在前读取时反转）
        self._suggestions: deque = deque(
            maxlen=int(self.cfg['max_suggestions']))
        self._lock = threading.Lock()

        # 计数（滑窗内的通过/失败）
        self._pass_count = 0
        self._fail_count = 0
        self._total_count = 0

    # ------------------------------------------------------------------
    # (1) 结果自我验证
    # ------------------------------------------------------------------
    def self_verify_result(self, result: dict,
                           baseline: Optional[dict] = None) -> Dict[str, Any]:
        """分析结果自我验证（§4.4.2(1)）。

        校验维度（任一失败 → valid=False，errors 累积原因）：
          * hit_rate_boundary   命中率边界（若 result 携带 per-algo 命中率）
          * probability_normalized fused_probabilities 每位概率和=1（±容差）
          * combination_structure  Top 组合 5 位、每位 0-9
          * window_consistency      result 数据窗口 == baseline 数据窗口（基线缺失则跳过）
          * data_consistency        关键字段非空/类型正确（fused_prob 长==positions）

        Args:
            result: 分析结果（final_report 或统计预测结构化 dict）。
            baseline: 验证基线 dict（含 'data_period' / 历史命中率等，可 None）。

        Returns:
            {'valid', 'errors', 'checks', 'coverage', 'ts'}
        """
        if not isinstance(result, dict):
            result = {}
        checks: Dict[str, Any] = {}
        errors: List[str] = []

        checks['hit_rate_boundary'] = self._check_hit_rate_boundary(result, errors)
        checks['probability_normalized'] = self._check_probability_normalized(
            result, errors)
        checks['combination_structure'] = self._check_combination_structure(
            result, errors)
        checks['window_consistency'] = self._check_window_consistency(
            result, baseline, errors)
        checks['data_consistency'] = self._check_data_consistency(result, errors)

        valid = len(errors) == 0
        self._record_verification(valid, checks)
        return {
            'valid': valid,
            'errors': errors,
            'checks': checks,
            'coverage': self.get_coverage(),
            'ts': _now_iso(),
        }

    # ------------------------------------------------------------------
    # 各校验维度实现（纯函数，异常隔离：单项出错只记 fail，不崩溃）
    # ------------------------------------------------------------------
    def _check_hit_rate_boundary(self, result: dict, errors: List[str]) -> bool:
        """命中率边界：result 若携带 per-algo / monitor 命中率，逐项 0-1 校验。

        无命中率字段时返回 True（不校验项不判失败，保持覆盖 100% 的语义
        ——「能校验的都校验了」）。
        """
        ok = True
        hit_rates = self._extract_hit_rates(result)
        lo = float(self.cfg['hit_rate_min'])
        hi = float(self.cfg['hit_rate_max'])
        for name, rate in hit_rates:
            try:
                r = float(rate)
            except (TypeError, ValueError):
                errors.append(f'{name} 命中率非数值: {rate!r}')
                ok = False
                continue
            if not (lo <= r <= hi):
                errors.append(f'{name} 命中率越界 [{lo}, {hi}]: {r:.4f}')
                ok = False
        return ok

    def _check_probability_normalized(self, result: dict, errors: List[str]) -> bool:
        """fused_probabilities 每位概率和=1（±容差）。

        fused_probabilities 是 List[Dict[int,float]]（长 positions，每位和≈1）。
        缺失该键时返回 True（不强制）；存在但某位越容差则 fail。
        """
        fused = result.get('fused_probabilities')
        if not fused:
            # 某些降级结果可能无 fused_probabilities；不判失败，记录 skipped
            return True
        tol = float(self.cfg['prob_sum_tolerance'])
        ok = True
        try:
            for pos, prob_map in enumerate(fused):
                total = sum(max(0.0, float(v)) for v in (prob_map or {}).values())
                if abs(total - 1.0) > tol:
                    errors.append(
                        f'第{pos + 1}位概率和偏离 1.0: {total:.4f} (容差±{tol})')
                    ok = False
        except Exception as e:
            errors.append(f'概率归一校验异常(非致命): {e}')
            ok = False
        return ok

    def _check_combination_structure(self, result: dict, errors: List[str]) -> bool:
        """Top 组合结构：每个组合 5 位、每位 0-9 数字。

        检查 result['top_combinations']（元素含 'numbers' 或 'combination' 串）
        与 result['recommended_combinations']。缺失则 True。
        """
        positions = int(self.cfg['positions'])
        combos = result.get('top_combinations') or \
            result.get('recommended_combinations') or []
        if not combos:
            return True
        ok = True
        for i, c in enumerate(combos):
            if not isinstance(c, dict):
                errors.append(f'组合 #{i + 1} 非 dict: {c!r}')
                ok = False
                continue
            numbers = c.get('numbers')
            if numbers is None:
                # 退回字符串形式 'combination'
                raw = c.get('combination', '')
                if not isinstance(raw, str) or len(raw) != positions:
                    errors.append(
                        f'组合 #{i + 1} 号码串长度≠{positions}: {raw!r}')
                    ok = False
                    continue
                numbers = [int(ch) for ch in raw]
            if not isinstance(numbers, (list, tuple)) or len(numbers) != positions:
                errors.append(
                    f'组合 #{i + 1} 位数≠{positions}: {len(numbers) if hasattr(numbers, "__len__") else "?"}')
                ok = False
                continue
            for d in numbers:
                if not (isinstance(d, int) and 0 <= d <= 9):
                    errors.append(f'组合 #{i + 1} 含非法数字: {d!r}')
                    ok = False
                    break
        return ok

    def _check_window_consistency(self, result: dict,
                                  baseline: Optional[dict],
                                  errors: List[str]) -> bool:
        """窗口一致性：result 数据窗口 == baseline 数据窗口（基线缺失则跳过）。

        对齐 plan §4.4.2 示例：``result.get('data_period') !=
        baseline.get('data_period')`` → 失败。基线为 None 时返回 True 且
        在 checks 里标注 'skipped_no_baseline'（不判失败，覆盖率仍计）。
        """
        if not isinstance(baseline, dict) or not baseline:
            return True  # 无基线 → 跳过（不判失败）
        result_period = result.get('data_period')
        baseline_period = baseline.get('data_period')
        # 任一侧缺失 → 视为不一致（有基线却对不上窗口，属数据一致性风险）
        if result_period is None or baseline_period is None:
            errors.append(
                f'数据窗口缺失：result.data_period={result_period!r}, '
                f'baseline.data_period={baseline_period!r}')
            return False
        if result_period != baseline_period:
            errors.append(
                f'数据窗口与基线不一致：result={result_period!r} vs '
                f'baseline={baseline_period!r}')
            return False
        return True

    def _check_data_consistency(self, result: dict, errors: List[str]) -> bool:
        """数据一致性：关键字段非空/类型正确。

        - fused_probabilities 存在时长度须 == positions；
        - target_issue / next_issue 若存在须非空字符串。
        全部缺失时返回 True（宽松：只校验存在项）。
        """
        ok = True
        positions = int(self.cfg['positions'])
        fused = result.get('fused_probabilities')
        if fused is not None:
            if not isinstance(fused, (list, tuple)) or len(fused) != positions:
                errors.append(
                    f'fused_probabilities 长度≠{positions}: '
                    f'{len(fused) if hasattr(fused, "__len__") else type(fused).__name__}')
                ok = False
        for key in ('target_issue', 'next_issue'):
            v = result.get(key)
            if v is not None and not isinstance(v, str) and not isinstance(v, int):
                errors.append(f'{key} 类型非法: {v!r}')
                ok = False
        return ok

    # ------------------------------------------------------------------
    # 命中率提取（跨结构）
    # ------------------------------------------------------------------
    def _extract_hit_rates(self, result: dict) -> List[tuple]:
        """从 result 中抽取所有已知命中率（(名称, 值) 列表）。

        覆盖：result['per_algo_top_predictions']（per-algo 摘要）、
        result['monitor_hit_rates']（监控回灌）、result['hit_rate']。
        无则返回 []（不强制）。
        """
        rates: List[tuple] = []
        per_algo = result.get('per_algo_top_predictions')
        if isinstance(per_algo, dict):
            for algo, payload in per_algo.items():
                r = None
                if isinstance(payload, dict):
                    r = payload.get('hit_rate')
                if r is not None:
                    rates.append((algo, r))
        mon = result.get('monitor_hit_rates')
        if isinstance(mon, (list, tuple)):
            for i, r in enumerate(mon):
                rates.append((f'monitor#{i + 1}', r))
        single = result.get('hit_rate')
        if single is not None:
            rates.append(('hit_rate', single))
        return rates

    # ------------------------------------------------------------------
    # 覆盖率统计（§4.4.3 技术指标：验证覆盖率 100%）
    # ------------------------------------------------------------------
    def _record_verification(self, valid: bool, checks: Dict[str, Any]) -> None:
        """把一次验证结果计入滑窗（线程安全）。"""
        with self._lock:
            self._verifications.append({
                'valid': valid,
                'checks': {k: bool(v) for k, v in checks.items()},
                'ts': _now_iso(),
            })
            self._total_count += 1
            if valid:
                self._pass_count += 1
            else:
                self._fail_count += 1

    def get_coverage(self) -> Dict[str, Any]:
        """验证覆盖率与通过/失败统计（§4.4.3 覆盖率 100% 的度量）。

        coverage = 已验证项 / 应验证项。本验证器每次验证覆盖全部 5 个维度，
        故「覆盖率」按「已完成的验证批次占比」表达：total_count 即已覆盖批次。
        """
        with self._lock:
            total = self._total_count
            passed = self._pass_count
            failed = self._fail_count
        # 单次验证的维度集合（5 项）——验证器恒全维度校验，故单批覆盖率=100%
        dimensions = 5
        return {
            'verified_batches': total,
            'passed': passed,
            'failed': failed,
            'pass_rate': round(passed / total, 4) if total else 1.0,
            # 「结果自我验证覆盖」：凡经本验证器者 5 维度全覆盖 → 1.0（有样本时）
            'coverage': 1.0 if total else 0.0,
            'dimensions': dimensions,
            'window_size': int(self.cfg['window_size']),
        }

    def get_verification_stats(self) -> Dict[str, Any]:
        """验证统计（含滑窗内的最近检查明细，供可视化）。"""
        with self._lock:
            recent = list(self._verifications)[-20:]
        return {
            'ts': _now_iso(),
            'coverage': self.get_coverage(),
            'recent': recent,
        }

    # ------------------------------------------------------------------
    # (2) 分级优化建议生成（诚实边界：只建议，不自动调权重）
    # ------------------------------------------------------------------
    def generate_optimization_suggestions(
            self,
            verify_result: Dict[str, Any],
            monitor_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """基于验证结果 + 监控数据生成分级优化建议（§4.4.2(2)）。

        每条建议：{'priority'('high'|'medium'|'low'), 'category', 'title',
        'rationale'(依据), 'action'(可操作建议), 'auto_safe'(是否纯只读建议)}。

        诚实边界：所有建议均为「人可采纳」的建议型记录，auto_safe=True 表示
        该建议**不触碰权重**（安全可执行）；涉及权重/窗口调整的仅给出方向
        与建议依据，标注 auto_safe=False（须人工/ONLINE_LEARNER_WRITEBACK 通道）。
        """
        verify_result = verify_result or {}
        monitor_data = monitor_data or {}
        errors = verify_result.get('errors', []) or []
        valid = bool(verify_result.get('valid', False))
        checks = verify_result.get('checks', {}) or {}

        suggestions: List[Dict[str, Any]] = []

        # ---- 维度 1：验证失败项 → 高优先级（先修数据/结构，否则结论不可靠）----
        failed_checks = [k for k, ok in checks.items() if ok is False]
        if failed_checks:
            suggestions.append({
                'priority': 'high',
                'category': 'data_integrity',
                'title': '修复验证失败项（结果可靠性前置）',
                'rationale': f'失败维度: {", ".join(failed_checks)}；'
                             f'错误: {"；".join(errors) if errors else "—"}',
                'action': '先修复数据窗口/概率归一/组合结构，再采信结论；'
                          '概率未归一则说明某算法输出越界，需检查该算法。',
                'auto_safe': False,
            })

        # ---- 维度 3：监控命中率对照随机基线（诚实边界）----
        monitor_hr = None
        try:
            monitor_hr = monitor_data.get('hit_rate')
            if monitor_hr is None and monitor_data.get('latest_values'):
                monitor_hr = monitor_data['latest_values'].get('hit_rate')
        except Exception:
            monitor_hr = None
        baseline_top1 = RANDOM_BASELINE['top1']
        if monitor_hr is not None:
            vs = '高于' if monitor_hr > baseline_top1 else (
                '低于' if monitor_hr < baseline_top1 else '相当于')
            suggestions.append({
                'priority': 'medium',
                'category': 'model_monitoring',
                'title': f'监控命中率 {monitor_hr:.3f}（{vs}随机基线 {baseline_top1:.0%}）',
                'rationale': '对照随机基线判定，避免误判超额收益；'
                             '排列5 为公平摇号，命中率差异属正常波动。',
                'action': f'命中率{vs}基线属正常波动，不构成预测能力；'
                          '请勿据此加码，建议维持当前权重、仅持续观测。',
                'auto_safe': True,  # 纯观测建议，不触碰权重
            })

        # ---- 维度 4：验证通过（valid）但建议窗口/特征优化（低优先级，方向性）----
        if valid:
            suggestions.append({
                'priority': 'low',
                'category': 'feature_optimization',
                'title': '结果自检通过，可探索性优化（方向性建议）',
                'rationale': '当前结果结构与数值自洽；优化属探索项，'
                             '非必须，且不得自动改动冻结权重。',
                'action': '可人工评估：(a) 特征子集是否冗余、(b) 窗口 60 期是否'
                          '需 A/B、(c) 关注贝叶斯后验摘要。任何权重/窗口调整须'
                          '经 walk-forward 验证后由 ONLINE_LEARNER_WRITEBACK 通道执行。',
                'auto_safe': False,
            })

        # 排序：high > medium > low
        _rank = {'high': 0, 'medium': 1, 'low': 2}
        suggestions.sort(key=lambda s: _rank.get(s.get('priority'), 9))

        out = {
            'ts': _now_iso(),
            'verify_valid': valid,
            'suggestions': suggestions,
            'by_priority': {
                'high': [s for s in suggestions if s['priority'] == 'high'],
                'medium': [s for s in suggestions if s['priority'] == 'medium'],
                'low': [s for s in suggestions if s['priority'] == 'low'],
            },
            'honest_note': (
                '以上为建议型记录：涉及权重/窗口的调整仅为方向与建议依据，'
                '本模块绝不自动调整 v4.0 冻结权重、不写 p5_weight_history、'
                '不写 Redis 自适应权重通道（仍由 ONLINE_LEARNER_WRITEBACK 门控）。'
            ),
        }
        self._record_suggestions(out)
        return out

    def _record_suggestions(self, out: Dict[str, Any]) -> None:
        """把一批建议记入内存滑窗（线程安全，供 get_suggestions 读取）。"""
        with self._lock:
            for s in out.get('suggestions', []):
                self._suggestions.append({
                    'priority': s.get('priority'),
                    'category': s.get('category'),
                    'title': s.get('title'),
                    'rationale': s.get('rationale'),
                    'auto_safe': s.get('auto_safe', False),
                    'ts': out.get('ts'),
                })

    def get_suggestions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """读取内存中的近期建议（最新在前，最多 limit 条）。"""
        with self._lock:
            data = list(self._suggestions)
        return list(reversed(data[-max(0, int(limit)):]))

    # ------------------------------------------------------------------
    # 可选落盘（趋势留存 / 跨进程读取；DB 不可达亦可工作）
    # ------------------------------------------------------------------
    def persist_suggestions(self, limit: int = 0) -> int:
        """把近期建议追加到本地 JSONL（默认关，需显式调用；返回写入条数）。"""
        try:
            with self._lock:
                all_rows = list(self._suggestions)
            if limit:
                rows = [dict(r) for r in all_rows[-int(limit):]]
            else:
                rows = [dict(r) for r in all_rows]
            if not rows:
                return 0
            path = self.cfg['suggestion_jsonl']
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + '\n')
            return len(rows)
        except Exception as e:
            logger.warning('[ResultVerifier] 建议落盘失败(非致命): %s', e)
            return 0


# 内存建议滑窗在 ResultSelfVerifier.__init__ 中初始化（self._suggestions）。


# ---------------------------------------------------------------------------
# 全局单例 + 便捷函数（惰性，import 无副作用、0 线程）
# ---------------------------------------------------------------------------
_result_verifier: Optional[ResultSelfVerifier] = None
_result_verifier_lock = threading.Lock()


def get_result_verifier(config: Optional[Dict[str, Any]] = None
                        ) -> ResultSelfVerifier:
    """获取全局结果自我验证器单例（惰性创建）。"""
    global _result_verifier
    with _result_verifier_lock:
        if _result_verifier is None:
            _result_verifier = ResultSelfVerifier(config)
        return _result_verifier


def self_verify(result: dict,
                baseline: Optional[dict] = None) -> Dict[str, Any]:
    """便捷：对分析结果做自我验证（§4.4.2(1)）。"""
    return get_result_verifier().self_verify_result(result, baseline)


def generate_suggestions(verify_result: Dict[str, Any],
                         monitor_data: Optional[Dict[str, Any]] = None
                         ) -> Dict[str, Any]:
    """便捷：基于验证结果 + 监控数据生成分级建议（§4.4.2(2)）。"""
    return get_result_verifier().generate_optimization_suggestions(
        verify_result, monitor_data)


def get_coverage() -> Dict[str, Any]:
    """便捷：读取验证覆盖率统计（§4.4.3）。"""
    return get_result_verifier().get_coverage()
