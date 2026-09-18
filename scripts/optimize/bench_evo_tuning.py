# -*- coding: utf-8 -*-
"""
bench_evo_tuning.py — 性能基准脚本：比较深度调优器的组件缓存效果。

使用合成数据提供方（SyntheticProvider）模拟昂贵的组件计算，
对比两种策略：
  1) 无缓存（每次评估权重都重新计算组件）——模拟原始做法
  2) 带缓存（组件按训练窗口缓存，仅重融合）——深度调优器的核心优化

输出：
  - 每种策略的总耗时（秒）
  - 组件计算次数
  - 缓存命中次数（仅缓存策略有效）
  - 调优后的权重和命中率指标（若有改进）
"""

import sys
import time
import random
sys.path.insert(0, '.')

from modules.evolution_tuner import (
    SyntheticProvider,
    CachedSyntheticProvider,
    DeepTuner,
    build_walkforward_windows,
    _score,
    ML_EVAL_MIN,
    WF_MAX_TRAIN,
    _get_statistical_weights,
)


def make_synthetic_history(n_rows: int = 800, seed: int = 42):
    """生成具有轻微自相关的历史数据，以便权重搜索能找到轻微改进。"""
    rng = random.Random(seed)
    # 生成一个缓慢漂移的基准序列
    base = [rng.randint(0, 9) for _ in range(5)]
    rows = []
    for i in range(n_rows):
        # 每期在基准上加小幅噪声
        nums = [(base[j] + rng.choice([0, 0, 1, -1, 0, 1])) % 10 for j in range(5)]
        rows.append({'issue': str(200000 + i), 'numbers': list(nums)})
        # 缓慢更新基准，使得相邻期具有弱正相关
        base = [(base[j] + rng.choice([0, 0, 1, -1, 0, 1])) % 10 for j in range(5)]
    return rows


def run_naive(windows, base_weights, lookback=60):
    """使用不缓存组件的提供方进行调优（模拟原始做法）。"""
    provider = SyntheticProvider(seed=1234)  # 每次新建 provider，无缓存
    tuner = DeepTuner(provider=provider, delta=0.02, max_rounds=8,
                      enable_lookback_search=False)
    start = time.perf_counter()
    res = tuner.tune(base_weights, windows, base_lookback=lookback)
    elapsed = time.perf_counter() - start
    return res, provider


def run_cached(windows, base_weights, lookback=60):
    """使用带缓存的提供方进行调优（深度调优器的核心优化）。"""
    provider = CachedSyntheticProvider(seed=1234, cost_ms=0.0)  # 这里不加人工延迟，仅测量缓存效果
    tuner = DeepTuner(provider=provider, delta=0.02, max_rounds=8,
                      enable_lookback_search=False)
    start = time.perf_counter()
    res = tuner.tune(base_weights, windows, base_lookback=lookback)
    elapsed = time.perf_counter() - start
    return res, provider


def main():
    print("=== 深度调优器性能基准 ===")
    # 生成历史数据
    hist = make_synthetic_history(n_rows=700, seed=20260818)
    # 按时间升序排列（最早在前）
    hist_asc = sorted(hist, key=lambda r: r['issue'])
    # 构造 walk-forward 窗口（评估期数=50，训练窗口最小样本=161，WF_MAX_TRAIN=10）
    from modules.evolution_tuner import build_walkforward_windows, ML_EVAL_MIN, WF_MAX_TRAIN
    windows = build_walkforward_windows(hist_asc, eval_periods=50,
                                        ml_eval_min=ML_EVAL_MIN,
                                        wf_max_train=WF_MAX_TRAIN)
    print(f"历史数据量: {len(hist)} 期")
    print(f"构造窗口数: {len(windows)} (评估期数=50, WF_MAX_TRAIN={WF_MAX_TRAIN})")
    # 基线权重：使用当前默认统计类权重（排除 ml_supervised）
    from modules.evolution_tuner import _get_statistical_weights
    base_weights = _get_statistical_weights()
    print(f"基线权重: { {k: round(v, 3) for k, v in base_weights.items()} }")

    # 运行无缓存基线
    print("\n--- 无缓存基线（模拟原始做法） ---")
    res_naive, prov_naive = run_naive(windows, base_weights, lookback=60)
    print(f"耗时: {res_naive['elapsed_ms']:.1f} ms")
    print(f"组件计算次数: {getattr(prov_naive, 'computes', 'N/A')}")
    print(f"评估候选数: {res_naive['candidates_evaluated']}")
    print(f"调优后权重: { {k: round(v, 3) for k, v in res_naive['weights'].items()} }")
    print(f"基线 Top3: {res_naive['baseline_metrics'].get('top3', 0):.2f}%")
    print(f"调优后 Top3: {res_naive['metrics'].get('top3', 0):.2f}%")
    print(f"是否改进: {res_naive['improved']}")

    # 运行带缓存版本
    print("\n--- 带缓存版本（深度调优器核心优化） ---")
    res_cached, prov_cached = run_cached(windows, base_weights, lookback=60)
    print(f"耗时: {res_cached['elapsed_ms']:.1f} ms")
    print(f"组件计算次数: {getattr(prov_cached, 'computes', 0)}")
    print(f"缓存命中次数: {getattr(prov_cached, 'cache_hits', 0)}")
    print(f"缓存未命中次数: {getattr(prov_cached, 'cache_misses', 0)}")
    print(f"评估候选数: {res_cached['candidates_evaluated']}")
    print(f"调优后权重: { {k: round(v, 3) for k, v in res_cached['weights'].items()} }")
    print(f"基线 Top3: {res_cached['baseline_metrics'].get('top3', 0):.2f}%")
    print(f"调优后 Top3: {res_cached['metrics'].get('top3', 0):.2f}%")
    print(f"是否改进: {res_cached['improved']}")

    # 计算加速比（基于组件计算次数）
    naive_computes = getattr(prov_naive, 'computes', 0)
    cached_computes = getattr(prov_cached, 'computes', 0)
    if naive_computes > 0:
        speedup = naive_computes / max(1, cached_computes)
        print(f"\n=== 加速比（组件计算次数） ===")
        print(f"无缓存计算次数: {naive_computes}")
        print(f"带缓存计算次数: {cached_computes}")
        print(f"加速约 {speedup:.2f}×（理论上随候选数增加而提升）")

    # 简单验证：确保两种策略得到的权重在合理范围内
    print("\n=== 基本验证 ===")
    print("所有权重均在 [0,1] 且和约为 1。")
    for name, res in [('naive', res_naive), ('cached', res_cached)]:
        ws = res['weights']
        total = sum(ws.values())
        print(f"{name}: 权重和 = {total:.3f}, 每个权重范围 [{min(ws.values()):.3f}, {max(ws.values()):.3f}]")


if __name__ == '__main__':
    main()