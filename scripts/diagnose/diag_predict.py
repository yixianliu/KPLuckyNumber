# -*- coding: utf-8 -*-
"""直接测试 predict_next 的完整堆栈（诊断）+ A19 连续 10 期回放计时（bench）。

用法：
    python scripts/diagnose/diag_predict.py               # 诊断段 + bench 段
    python scripts/diagnose/diag_predict.py --bench-only  # 仅 bench 段

bench 段（PUP-20261019-013100 §1.3 B3 扩展点 / §3.3 ALG-01 A19 验收）：
    输出 reports/prep/bench_predict.json。

    计时口径（cold/warm 双段，同一回放窗口内自洽对比）：
        cold = 指纹 miss → 5 位全量训练 + 落盘，与优化前路径逐字节等价
               （视作「优化前基线」——B3 计划采集项，本脚本实现采集动作）；
        warm = 同输入同目标期二次调用 → 缓存命中、跳过训练（ALG-01 优化后）。
        A19 目标：warm P50 ≤ cold P50 的 60%。

    回放构造：
        取全库历史尾部 REPLAY_TOTAL 期，逐期用截至目标期之前的真实切片调用
        （第 i 期 input = tail[:REPLAY_BASE + i]）。切片行数 ≥ ML_MIN_SAMPLES
        且各期互异 → 不触发 DB 补全、指纹互异，cold 段不会虚假命中；
        同时 < 200 期避开 Optuna 长时调参，保证 bench 墙钟时长可控
        （口径已在基线 note 中登记，复现时窗口参数固定即可复现）。

    缓存隔离：
        bench 段将 ml_predictor.MODEL_CACHE_ROOT 临时指向项目内一次性目录
        （.gitignore 豁免），既保证 bench 可重复（不受生产缓存影响），
        也避免 bench 污染 models/v5.1/ 生产缓存；结束即清理。
"""
import json
import os
import shutil
import statistics
import sys
import time
import traceback
from datetime import datetime
from unittest import mock

sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

from modules.database import P5Database  # noqa: E402
from modules.self_evolution import _row_to_sorted  # noqa: E402
from modules.ml_predictor import predict_next  # noqa: E402
import modules.ml_predictor as ml  # noqa: E402

# --- bench 参数（写入 JSON，保证口径可复现） ---
REPLAY_ISSUES = 10                       # 连续回放期数（A19 口径：10 期）
REPLAY_BASE = 170                        # 首期回放输入行数（≥ ML_MIN_SAMPLES）
REPLAY_TOTAL = REPLAY_BASE + REPLAY_ISSUES  # 取库尾窗口 = 180 期
_PROJ_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH_OUT = os.path.join(
    _PROJ_ROOT, 'reports', 'prep', 'bench_predict.json')
CACHE_ISOLATED = os.path.join(
    _PROJ_ROOT, '.cleanup_trash_bench_predict_cache')
TARGET_RATIO = 0.6                       # A19：warm P50 ≤ cold P50 * 0.6


def _stats(values):
    """统计助手：n/mean/median(P50)/p95/min/max，空列表返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1,
                     int(round(0.95 * (len(ordered) - 1)))))
    return {
        'n': len(values),
        'mean': round(statistics.fmean(values), 1),
        'median': round(statistics.median(values), 1),
        'p95': round(ordered[idx], 1),
        'min': round(min(values), 1),
        'max': round(max(values), 1),
    }


def _run_diag(rows):
    """原诊断段（保留）：150 期全量 input 直接调用一次并打印结果。"""
    print(f'获取 {len(rows)} 行')
    sorted_data = _row_to_sorted(rows[:150])
    print(f'_row_to_sorted 输出 {len(sorted_data)} 行')
    print(f'第一行: {sorted_data[0] if sorted_data else "空"}')

    print('\n调用 predict_next...')
    try:
        result = predict_next(sorted_data, target_issue='test')
        print(f'结果: {result}')
    except Exception as e:
        print(f'异常: {e}')
        traceback.print_exc()


def _run_bench(sorted_data):
    """bench 段（A19）：连续 REPLAY_ISSUES 期回放，每期 cold/warm 双调用计时。"""
    if len(sorted_data) < REPLAY_TOTAL:
        print(f'[bench] 历史仅 {len(sorted_data)} 期，不足回放窗口 '
              f'{REPLAY_TOTAL} 期，放弃。')
        return False
    tail = sorted_data[-REPLAY_TOTAL:]
    print(f'[bench] 回放窗口：库尾 {REPLAY_TOTAL} 期'
          f'（{tail[0]["issue"]} .. {tail[-1]["issue"]}），'
          f'每期 cold(全量训练)+warm(缓存命中) 双调用。')

    samples = []
    cold_all, warm_all = [], []
    consistent_all = True
    # 缓存隔离：一次性目录，跑前清空、跑后清理，保证 bench 可重复
    if os.path.isdir(CACHE_ISOLATED):
        shutil.rmtree(CACHE_ISOLATED, ignore_errors=True)
    os.makedirs(CACHE_ISOLATED, exist_ok=True)
    try:
        with mock.patch.object(ml, 'MODEL_CACHE_ROOT', CACHE_ISOLATED):
            for i in range(REPLAY_ISSUES):
                data_i = tail[:REPLAY_BASE + i]
                issue_i = tail[REPLAY_BASE + i]['issue']
                # cold：指纹 miss → 5 位训练 + 落盘
                ml._reset_ml_cache_stats()
                hits0 = ml._ML_CACHE_STATS['cache_hits']
                t0 = time.perf_counter()
                res_cold = predict_next(data_i, target_issue=issue_i)
                cold_ms = (time.perf_counter() - t0) * 1000.0
                # warm：同输入同目标期 → 缓存命中
                t0 = time.perf_counter()
                res_warm = predict_next(data_i, target_issue=issue_i)
                warm_ms = (time.perf_counter() - t0) * 1000.0
                hit = (ml._ML_CACHE_STATS['cache_hits'] - hits0) == 1
                consistent = False
                if res_cold is not None and res_warm is not None:
                    # A20 一致性：cold 训练结果与 warm 缓存结果逐位相同
                    consistent = all(
                        a.keys() == b.keys() and
                        all(abs(a[d] - b[d]) < 1e-12 for d in a)
                        for a, b in zip(res_cold, res_warm))
                consistent_all = consistent_all and consistent
                cold_all.append(cold_ms)
                warm_all.append(warm_ms)
                samples.append({
                    'issue': issue_i,
                    'input_rows': len(data_i),
                    'cold_ms': round(cold_ms, 1),
                    'warm_ms': round(warm_ms, 1),
                    'warm_hit': hit,
                    'dist_identical': consistent,
                })
                print(f'[bench] {issue_i}  rows={len(data_i)}  '
                      f'cold={cold_ms:8.1f}ms  warm={warm_ms:7.1f}ms  '
                      f'hit={hit}  一致={consistent}')
    finally:
        shutil.rmtree(CACHE_ISOLATED, ignore_errors=True)

    cold_stats = _stats(cold_all)
    warm_stats = _stats(warm_all)
    ratio = None
    passed = False
    if cold_stats and warm_stats and cold_stats['median'] > 0:
        ratio = round(warm_stats['median'] / cold_stats['median'], 4)
        passed = (ratio <= TARGET_RATIO) and consistent_all
    payload = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'mode': 'replay10_cold_warm',
        'environment': {
            'python': sys.version.split()[0],
            'platform': sys.platform,
            'replay_issues': REPLAY_ISSUES,
            'replay_base_rows': REPLAY_BASE,
            'window_total': REPLAY_TOTAL,
            'ml_min_samples': ml.ML_MIN_SAMPLES,
            'db_total_rows': len(sorted_data),
        },
        'phases': {'cold': cold_stats, 'warm': warm_stats},
        'samples': samples,
        'baseline_evaluation': {
            'source': 'PUP-20261019-013100 §3.3 ALG-01 A19'
                      '（基线口径同 §1.3 B3；B3 未提前采集，'
                      '本脚本 cold 段即基线等价路径）',
            'baseline_p50_ms': (cold_stats or {}).get('median'),
            'optimized_p50_ms': (warm_stats or {}).get('median'),
            'ratio': ratio,
            'target_ratio': TARGET_RATIO,
            'dists_identical_all': consistent_all,
            'pass': passed,
            'note': '窗口 170-179 期（<200 不触发 Optuna），'
                    '复现需固定 REPLAY_BASE/REPLAY_ISSUES。',
        },
    }
    os.makedirs(os.path.dirname(BENCH_OUT), exist_ok=True)
    with open(BENCH_OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f'[bench] 已写出 {BENCH_OUT}')
    print(f'[bench] cold P50={cold_stats and cold_stats["median"]}ms  '
          f'warm P50={warm_stats and warm_stats["median"]}ms  '
          f'ratio={ratio}  目标≤{TARGET_RATIO}  '
          f'判定={"PASS" if passed else "FAIL"}')
    return passed


def main():
    # 诊断段与 bench 段共用一次 DB 拉取
    db = P5Database()
    db.connect()
    rows = db.get_history_data(limit=100000, order='ASC')
    db.disconnect()
    if not rows:
        print('数据库无历史数据，退出。')
        return 1
    sorted_all = _row_to_sorted(rows)
    print(f'全库历史 {len(rows)} 期（{rows[0]["issue"]} .. '
          f'{rows[-1]["issue"]}）')

    bench_only = '--bench-only' in sys.argv
    if not bench_only:
        _run_diag(rows)

    ok = _run_bench(sorted_all)
    return 0 if ok else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
