# -*- coding: utf-8 -*-
"""
模块：modules/metrics_dashboard.py
功能：B4 命中率量化评估体系核心 —— 多维命中指标 + Wilson CI + 随机基线对照

本模块是「命中率量化评估体系」的独立评估引擎，纯函数、零侵入：
  * 不从数据库写入路径读取（不依赖 verify_prediction 的宽松口径），
  * 从 p5_prediction_record 的 predicted_numbers / actual_numbers 重算，
  * 覆盖位级 Top-1/3/5（精确口径）与维度命中（和值/跨度/奇偶/形态），
  * 每维度附 Wilson 95% CI + 随机基线 + CI 重叠判定，
  * 生成结构化 JSON 报告到 reports/eval/metrics_{date}.json。

诚实边界铁律（不可违背）：
  * 排列5 为公平摇号，精确全中概率恒为 K/100000 不可提升；
  * 所有命中率结论一律附 Wilson 95% CI，并诚实标注是否与随机基线重叠；
  * 任何维度 CI 若显著高于基线，标记为「需排查前视泄漏」，而非「预测力」。

依赖：json, os, numpy, datetime, wilson_ci（modules.stats_utils）
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from modules.stats_utils import wilson_ci
from paths import EVAL_DIR

# 随机基线（排列5 公平摇号）
BASELINE_TOP1 = 0.10   # Top-1 每位置精确命中 = 1/10
BASELINE_TOP3 = 0.30   # Top-3 候选集含实际 = 3/10
BASELINE_TOP5 = 0.50   # Top-5 候选集含实际 = 5/10
# 维度基线
BASELINE_SUM = None    # 和值命中基线与推荐和值口径相关，动态计算
BASELINE_SPAN = None
BASELINE_ODD = 0.5     # 奇偶预测命中 = 1/2
BASELINE_BIG = 0.5     # 大小（>=5 为大）预测命中 = 1/2

# 位置标识
POSITIONS = ('wan', 'qian', 'bai', 'shi', 'ge')


def _parse_predicted(raw: Any) -> Dict[str, List[int]]:
    """解析 predicted_numbers 兼容新旧两种格式。

    新格式：{'wan': [4,5,6], ...}（扁平列表）
    旧格式：{'wan': {'numbers': [4,5,6], ...}, ...}
    """
    if not isinstance(raw, dict):
        return {p: [] for p in POSITIONS}
    out: Dict[str, List[int]] = {}
    for pos in POSITIONS:
        pd = raw.get(pos, [])
        if isinstance(pd, dict):
            pd = pd.get('numbers', [])
        if isinstance(pd, list):
            try:
                out[pos] = [int(n) for n in pd]
            except (TypeError, ValueError):
                out[pos] = []
        else:
            out[pos] = []
    return out


def _actual_numbers(actual: Any) -> Optional[List[int]]:
    """解析 actual_numbers（list）为 5 整数列表。"""
    if not isinstance(actual, (list, tuple)):
        return None
    try:
        nums = [int(n) for n in actual]
        if len(nums) != 5:
            return None
        return nums
    except (TypeError, ValueError):
        return None


def _calc_dimension_hits(pred: Dict[str, List[int]], actual: List[int]) -> Dict[str, Any]:
    """计算单条记录的各维度命中标志（精确口径）。

    维度定义：
      - top1/top3/top5：每位置精确命中计数（首号精确 / 候选集前3含实际 / 前5含实际）
      - sum：和值命中（推荐和值 == 实际和值），和值推荐取各位置 Top-3 众数近似
      - span：跨度命中（推荐跨度 == 实际跨度），同理
      - odd_even：奇偶形态命中（推荐奇偶序列 == 实际奇偶序列）
      - big_small：大小形态命中（推荐大小序列 == 实际大小序列）
    """
    def _topk_hit(pos: str, k: int) -> int:
        nums = pred.get(pos, [])
        actual_num = actual[POSITIONS.index(pos)]
        if not nums:
            return 0
        cand = nums[:k]
        return 1 if actual_num in [int(c) for c in cand] else 0

    # 位级 Top-1/3/5 精确命中计数（0-5）
    top1 = sum(_topk_hit(p, 1) for p in POSITIONS)
    top3 = sum(_topk_hit(p, 3) for p in POSITIONS)
    top5 = sum(_topk_hit(p, 5) for p in POSITIONS)

    # 和值/跨度：推荐值取各位置 Top-3 候选的众数近似（无候选则回退实际，标记缺失）
    pred_sum = None
    pred_span = None
    sum_candidates = []
    span_candidates = []
    for p in POSITIONS:
        cand = pred.get(p, [])[:3]
        if cand:
            cand = [int(c) for c in cand]
            sum_candidates.append(cand)
    if sum_candidates:
        # 和值推荐 = 各位置候选集的均值取整（稳健近似）
        pos_means = [float(np.mean(c)) for c in sum_candidates]
        pred_sum = int(round(sum(pos_means)))
        pred_span = int(round(max(pos_means) - min(pos_means)))

    actual_sum = sum(actual)
    actual_span = max(actual) - min(actual)

    sum_hit = 1 if (pred_sum is not None and pred_sum == actual_sum) else 0
    span_hit = 1 if (pred_span is not None and pred_span == actual_span) else 0

    # 奇偶形态：每位置推荐首号（最高概率）的奇偶 == 实际奇偶
    odd_even_hit = 1
    big_small_hit = 1
    for i, p in enumerate(POSITIONS):
        nums = pred.get(p, [])
        if not nums:
            odd_even_hit = 0
            big_small_hit = 0
            continue
        top_num = int(nums[0])
        actual_num = actual[i]
        if (top_num % 2) != (actual_num % 2):
            odd_even_hit = 0
        if (1 if top_num >= 5 else 0) != (1 if actual_num >= 5 else 0):
            big_small_hit = 0

    return {
        'top1_count': top1, 'top3_count': top3, 'top5_count': top5,
        'top1_rate': round(top1 / 5, 4), 'top3_rate': round(top3 / 5, 4),
        'top5_rate': round(top5 / 5, 4),
        'sum_hit': sum_hit, 'span_hit': span_hit,
        'odd_even_hit': odd_even_hit, 'big_small_hit': big_small_hit,
        'pred_sum': pred_sum, 'actual_sum': actual_sum,
        'pred_span': pred_span, 'actual_span': actual_span,
    }


def _wilson_interval(success: int, total: int) -> Dict[str, float]:
    """计算 Wilson 95% CI（返回 rate/lower/upper）。"""
    if total <= 0:
        return {'rate': 0.0, 'lower': 0.0, 'upper': 0.0}
    rate = success / total
    low, high = wilson_ci(success, total, z=1.96)
    return {'rate': round(rate, 4), 'lower': round(low, 4), 'upper': round(high, 4)}


def _overlaps(lower: float, upper: float, baseline: float) -> bool:
    """CI 是否与随机基线重叠（真实预测力需 CI 下界 > 基线）。"""
    return lower <= baseline <= upper


def compute_metrics_from_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从预测记录列表重算多维命中评估。

    Args:
        records: p5_prediction_record 行列表，每行含
            predicted_numbers（JSON 串或 dict）、actual_numbers（JSON 串或 list）。

    Returns:
        {n, position: {...}, dimensions: {...}, summary: {...}, honesty}
        无有效记录时 n=0 且各维 rate=0。
    """
    # 规范化：解析 predicted / actual
    norm_records = []
    for r in records:
        pred_raw = r.get('predicted_numbers')
        if isinstance(pred_raw, str):
            try:
                pred_raw = json.loads(pred_raw)
            except (ValueError, TypeError):
                continue
        actual_raw = r.get('actual_numbers')
        if isinstance(actual_raw, str):
            try:
                actual_raw = json.loads(actual_raw)
            except (ValueError, TypeError):
                continue
        actual = _actual_numbers(actual_raw)
        if actual is None:
            continue
        pred = _parse_predicted(pred_raw)
        norm_records.append((pred, actual))

    n = len(norm_records)
    if n == 0:
        return {'n': 0, 'position': {}, 'dimensions': {}, 'summary': {}, 'honesty': {}}

    # 维度累计
    acc = {
        'top1_count': 0, 'top3_count': 0, 'top5_count': 0,
        'sum_hit': 0, 'span_hit': 0, 'odd_even_hit': 0, 'big_small_hit': 0,
    }
    # 位置级 Top-1 命中累计（5 位置 × n 记录 = 5n 试次）
    pos_top1 = {p: 0 for p in POSITIONS}
    pos_top3 = {p: 0 for p in POSITIONS}
    pos_top5 = {p: 0 for p in POSITIONS}

    for pred, actual in norm_records:
        dims = _calc_dimension_hits(pred, actual)
        acc['top1_count'] += dims['top1_count']
        acc['top3_count'] += dims['top3_count']
        acc['top5_count'] += dims['top5_count']
        acc['sum_hit'] += dims['sum_hit']
        acc['span_hit'] += dims['span_hit']
        acc['odd_even_hit'] += dims['odd_even_hit']
        acc['big_small_hit'] += dims['big_small_hit']
        for i, p in enumerate(POSITIONS):
            nums = pred.get(p, [])
            if not nums:
                continue
            cand = [int(c) for c in nums]
            actual_num = actual[i]
            if actual_num in cand[:1]:
                pos_top1[p] += 1
            if actual_num in cand[:3]:
                pos_top3[p] += 1
            if actual_num in cand[:5]:
                pos_top5[p] += 1

    total_pos_trials = n * 5  # 位置级总试次

    # 位置级 Top-1/3/5（精确口径）
    position = {}
    for p in POSITIONS:
        position[p] = {
            'top1': _wilson_interval(pos_top1[p], n),
            'top3': _wilson_interval(pos_top3[p], n),
            'top5': _wilson_interval(pos_top5[p], n),
        }

    # 汇总维度（整体）
    dimensions = {
        'top1': _wilson_interval(acc['top1_count'], total_pos_trials),
        'top3': _wilson_interval(acc['top3_count'], total_pos_trials),
        'top5': _wilson_interval(acc['top5_count'], total_pos_trials),
        'sum': _wilson_interval(acc['sum_hit'], n),
        'span': _wilson_interval(acc['span_hit'], n),
        'odd_even': _wilson_interval(acc['odd_even_hit'], n),
        'big_small': _wilson_interval(acc['big_small_hit'], n),
    }

    # 基线对照 + CI 重叠判定
    baselines = {
        'top1': BASELINE_TOP1, 'top3': BASELINE_TOP3, 'top5': BASELINE_TOP5,
        'sum': None, 'span': None, 'odd_even': BASELINE_ODD, 'big_small': BASELINE_BIG,
    }
    comparison = {}
    for key, base in baselines.items():
        d = dimensions[key]
        if base is None:
            # 和值/跨度基线：随机口径下的理论命中，此处标注需动态推断
            comparison[key] = {
                'baseline': None,
                'overlaps_baseline': None,
                'note': '和值/跨度基线依赖推荐口径，需结合分布推断；不做虚假基线声明',
            }
        else:
            ov = _overlaps(d['lower'], d['upper'], base)
            comparison[key] = {
                'baseline': round(base, 4),
                'overlaps_baseline': ov,
                'exceeds_baseline': d['lower'] > base,
            }

    summary = {
        'total_records': n,
        'position_trials': total_pos_trials,
        'top1_rate_overall': dimensions['top1']['rate'],
        'top3_rate_overall': dimensions['top3']['rate'],
        'top5_rate_overall': dimensions['top5']['rate'],
    }

    # 诚实口径：任何维度 CI 下界显著高于基线 → 需排查前视泄漏（非预测力）
    honesty = {
        'note': '排列5 为公平摇号，精确全中概率恒为 K/100000，不可突破随机基线；'
                'CI 下界高于基线仅表示样本统计涨落或需排查前视泄漏。',
        'flags': {
            k: comparison[k]['exceeds_baseline']
            for k in ('top1', 'top3', 'top5', 'odd_even', 'big_small')
            if k in comparison and comparison[k].get('exceeds_baseline') is not None
        },
    }

    return {
        'n': n,
        'position': position,
        'dimensions': dimensions,
        'comparison': comparison,
        'summary': summary,
        'honesty': honesty,
    }


def generate_metrics_report(records: List[Dict[str, Any]],
                            output_path: Optional[str] = None) -> str:
    """生成多维命中评估 JSON 报告。

    Args:
        records: 预测记录列表。
        output_path: 输出路径，默认 reports/eval/metrics_{date}.json。

    Returns:
        JSON 文件完整路径；异常返回空串。
    """
    os.makedirs(EVAL_DIR, exist_ok=True)
    date_str = datetime.now().strftime('%Y%m%d_%H%M')
    if output_path is None:
        output_path = os.path.join(EVAL_DIR, f'metrics_{date_str}.json')

    metrics = compute_metrics_from_records(records)
    payload = {
        'generated_at': datetime.now().isoformat(),
        'engine': 'metrics_dashboard v1 (B4)',
        'sample_size': metrics.get('n', 0),
        'honesty': metrics.get('honesty', {}),
        'position': metrics.get('position', {}),
        'dimensions': metrics.get('dimensions', {}),
        'comparison': metrics.get('comparison', {}),
        'summary': metrics.get('summary', {}),
    }
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return output_path
    except OSError as e:
        return ''
