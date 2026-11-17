# -*- coding: utf-8 -*-
"""预测基准测试脚本：性能计时 + Top-1/Top-3 命中率 + Wilson 95% CI

用途：
    在真实历史数据上做 walk-forward 式基准测试，作为优化前后的对照基线。
    优化必须满足：
    1. 等价性：预测输出逐期一致（正确性修复不得改变预测语义）
    2. 性能：单期预测耗时下降
    3. 命中率诚实对照：与随机基线（Top-1=10%/Top-3=30%）比较并给出 Wilson 95% CI，
       CI 与基线重叠即视为无统计显著超额，不得宣称提升预测力。

用法：
    python scripts/benchmark_prediction.py [--eval N] [--window W] [--tag TAG]

输出：
    scripts/benchmark_result_<tag>.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_CONFIG  # noqa: E402
from modules.stats_utils import wilson_ci  # noqa: E402

POSITIONS = ['wan', 'qian', 'bai', 'shi', 'ge']


def load_data():
    import pymysql
    conn = pymysql.connect(
        host=DB_CONFIG['host'], port=DB_CONFIG['port'], user=DB_CONFIG['user'],
        password=DB_CONFIG['password'], database=DB_CONFIG['database'],
        charset=DB_CONFIG.get('charset', 'utf8mb4'),
    )
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT issue, draw_date, wan, qian, bai, shi, ge, hezhi, span "
            "FROM p5_history_data WHERE is_valid=1 ORDER BY CAST(issue AS UNSIGNED)"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    cols = ['issue', 'draw_date', 'wan', 'qian', 'bai', 'shi', 'ge', 'hezhi', 'span']
    return [dict(zip(cols, r)) for r in rows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--eval', type=int, default=200, help='评估期数（最后 N 期）')
    parser.add_argument('--window', type=int, default=60, help='每次预测使用的历史窗口大小')
    parser.add_argument('--tag', type=str, default='baseline', help='结果文件标签')
    parser.add_argument('--preds', type=str, default='', help='预测输出快照文件（等价性比对）')
    parser.add_argument('--noai', action='store_true',
                        help='禁用 AI 辅助（隔离 LLM 非确定性，验证统计/ML 核心确定性）')
    args = parser.parse_args()

    data = load_data()
    n_total = len(data)
    n_eval = min(args.eval, n_total - args.window)
    start_idx = n_total - n_eval
    print(f"总数据 {n_total} 期，评估最后 {n_eval} 期（{data[start_idx]['issue']}~{data[-1]['issue']}），"
          f"窗口 {args.window}")

    # 关闭进程内预测缓存，测量真实计算耗时（缓存键随数据变化不会误命中，但保证口径纯净）
    import modules.predictor as _pred_mod
    _pred_mod._CACHE_ENABLED = False

    from modules.predictor import P5Predictor

    predictor = P5Predictor()
    if args.noai:
        # 关闭 AI 辅助：隔离 LLM 输出非确定性，用于统计/ML 核心的确定性对照
        predictor.ai_available = False
        predictor.config.set_global_param('enable_ai_model', False)
    top1_hits = 0        # Top-1 真实口径：首推号精确命中（5 位累加）
    top3_hits = 0        # Top-3 覆盖口径：实际号落入前 3 集合（5 位累加）
    times = []
    snapshots = {}

    for i in range(start_idx, n_total):
        target = data[i]
        hist = data[max(0, i - args.window): i][::-1]  # 倒序：最新在前
        t0 = time.perf_counter()
        res = predictor.predict(hist, target_issue=str(target['issue']))
        dt = time.perf_counter() - t0
        times.append(dt)

        if res.get('error') or not res.get('fused_probabilities'):
            print(f"[WARN] {target['issue']} 预测失败: {res.get('error')}")
            snapshots[target['issue']] = {'error': str(res.get('error'))}
            continue

        fused = res['fused_probabilities']
        actuals = [target[p] for p in POSITIONS]
        snap_top1 = []
        snap_top3 = []
        for pos in range(5):
            ranked = sorted(fused[pos].items(), key=lambda kv: (-kv[1], kv[0]))
            top1_num = ranked[0][0]
            top3_set = [n for n, _ in ranked[:3]]
            snap_top1.append(int(top1_num))
            snap_top3.append([int(x) for x in top3_set])
            if int(top1_num) == int(actuals[pos]):
                top1_hits += 1
            if int(actuals[pos]) in [int(x) for x in top3_set]:
                top3_hits += 1
        snapshots[target['issue']] = {'top1': snap_top1, 'top3': snap_top3,
                                      'actual': [int(a) for a in actuals]}
        if (i - start_idx + 1) % 25 == 0:
            done = i - start_idx + 1
            print(f"进度 {done}/{n_eval} | 单期均值 {np.mean(times):.3f}s | "
                  f"Top1 累计 {top1_hits/(5*done):.4f} | Top3 累计 {top3_hits/(5*done):.4f}")

    trials = 5 * len(snapshots)
    t1 = top1_hits / trials if trials else 0.0
    t3 = top3_hits / trials if trials else 0.0
    ci1 = wilson_ci(top1_hits, trials)
    ci3 = wilson_ci(top3_hits, trials)

    result = {
        'tag': args.tag,
        'timestamp': datetime.now().isoformat(),
        'n_eval': len(snapshots),
        'window': args.window,
        'trials': trials,
        'top1_hits': top1_hits, 'top1_rate': t1, 'top1_ci95': list(ci1),
        'top3_hits': top3_hits, 'top3_rate': t3, 'top3_ci95': list(ci3),
        'random_baseline': {'top1': 0.10, 'top3': 0.30},
        'significant_excess_top1': bool(ci1[0] > 0.10),
        'significant_excess_top3': bool(ci3[0] > 0.30),
        'time_mean_s': float(np.mean(times)),
        'time_median_s': float(np.median(times)),
        'time_p95_s': float(np.percentile(times, 95)),
        'time_total_s': float(np.sum(times)),
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f'benchmark_result_{args.tag}.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    snap_path = args.preds or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           f'benchmark_preds_{args.tag}.json')
    with open(snap_path, 'w', encoding='utf-8') as f:
        json.dump(snapshots, f, ensure_ascii=False)

    print(json.dumps({k: v for k, v in result.items() if k != 'timestamp'},
                     ensure_ascii=False, indent=2))
    print(f"结果: {out}\n快照: {snap_path}")


if __name__ == '__main__':
    main()
