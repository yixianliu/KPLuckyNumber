# -*- coding: utf-8 -*-
"""回测特征与端到端基准（可重复、只读数据库、不写库）。

用途：
  1) 记录 extract_all_features 在 N=200/500/1000/2000 的单次耗时（冷/热）；
  2) 记录特征缓存容量 8 vs 256 在「重复窗口（重跑/断点重试）」场景的命中率；
  3) 可选：记录端到端 run_backtest 的耗时（关闭 AI、关闭 resume，避免写盘）。

数据来源：p5_history_data（is_valid=1）只读查询；当前库存不足 N 时用
合成序列补齐并在结果中标记 data_source='synthetic-extended'，仅用于计时，
不作为语义等价性证据。

运行：
  D:\\anaconda3\\python.exe scripts/bench/bench_backtest_features.py
  D:\\anaconda3\\python.exe scripts/bench/bench_backtest_features.py --no-e2e
"""
import argparse
import json
import logging
import os
import pickle
import platform
import random
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

logging.disable(logging.CRITICAL)

from config import DB_CONFIG
from modules.database import P5Database
from modules.features import P5Features, reset_feature_cache, _get_feature_cache

SIZES = [200, 500, 1000, 2000]


def load_real_history() -> list:
    """只读加载全部有效历史（按期号正序）"""
    db = P5Database()
    if not db.connect():
        raise RuntimeError('数据库连接失败，基准无法运行')
    try:
        rows = db.get_history_data(limit=None, order_by='issue ASC')
        return rows or []
    finally:
        db.disconnect()


def build_series(real: list, n: int) -> tuple:
    """返回 (data, source)；真实库存不足时用顺序合成期号补齐（仅计时用）"""
    if len(real) >= n:
        return real[-n:], 'real'
    data = list(real)
    last_issue = int(real[-1]['issue']) if real else 2023000
    idx = 1
    rng = random.Random(20261104)
    while len(data) < n:
        data.append({
            'issue': str(last_issue + idx),
            'numbers': [rng.randint(0, 9) for _ in range(5)],
        })
        idx += 1
    return data, 'synthetic-extended'


def timed_extract(features: P5Features, data: list, warm: bool) -> float:
    """单次 extract_all_features 计时（warm=True 前先跑一次填充缓存）"""
    if warm:
        features.extract_all_features(data)
    t0 = time.perf_counter()
    features.extract_all_features(data)
    return time.perf_counter() - t0


def cache_benchmark(features: P5Features, data: list, capacity: int, windows: int = 40) -> dict:
    """重复窗口场景：对最后 windows 个前缀各跑两轮，统计命中率。

    第一轮逐窗口写入缓存；第二轮按相同顺序重跑（模拟重跑/断点重试）。
    容量 >= windows 时第二轮应全部命中；容量 8 时只能命中最后 8 个。
    """
    # 直接改写已加载的 config 字典（进程内改环境变量对已完成求值的配置无效）
    from config import FEATURE_CACHE_CONFIG
    FEATURE_CACHE_CONFIG['enabled'] = True
    FEATURE_CACHE_CONFIG['capacity'] = capacity
    reset_feature_cache()
    cache = _get_feature_cache()
    start = max(50, len(data) - windows)
    prefixes = [data[:i] for i in range(start, len(data) + 1)]
    for prefix in prefixes:
        features.extract_all_features(prefix)
    for prefix in prefixes:
        features.extract_all_features(prefix)
    stats = cache.stats()
    return stats


class _TruncatedDB:
    """只读数据库桩：让回测只看到前 n 期，使 N 真正控制训练历史规模。

    Backtester 内部固定调用 get_history_data(limit=None, order='ASC')，
    若直连真实库会永远使用全量 1114 期，基准里的 N 就失去意义。
    """

    def __init__(self, series: list):
        self._series = series

    def connect(self):
        return True

    def disconnect(self):
        return None

    def get_history_data(self, limit=None, order_by='issue ASC', order=None):
        return list(self._series)


def e2e_benchmark(series: list, n: int, test_count: int) -> dict:
    """端到端回测计时（关闭 AI、关闭 resume，避免外部 IO 干扰）"""
    from modules.predictor import P5Predictor
    from modules.backtester import Backtester

    predictor = P5Predictor()
    engine = Backtester(predictor, db_instance=_TruncatedDB(series))
    t0 = time.perf_counter()
    result = engine.run_backtest(
        start_index=50,
        test_count=test_count,
        # recent：评估「最近 test_count 期」，不同 N 的训练窗口不同，
        # 避免各 N 复用到同一批前缀（legacy 固定从第 50 期起，会串缓存）
        eval_mode='recent',
        enable_ai=False,
        max_bayes_aux_calls=0,
        resume=False,
    )
    elapsed = time.perf_counter() - t0
    return {
        'test_count': test_count,
        'status': result.get('status'),
        'elapsed_s': round(elapsed, 3),
        'per_window_ms': round(elapsed / max(1, test_count) * 1000, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sizes', default='200,500,1000,2000')
    parser.add_argument('--test-count', type=int, default=2)
    parser.add_argument('--no-e2e', action='store_true')
    args = parser.parse_args()

    sizes = [int(x) for x in args.sizes.split(',') if x.strip()]
    real = load_real_history()
    features = P5Features()

    report = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'machine': platform.platform(),
        'python': sys.version.split()[0],
        'db_host': DB_CONFIG.get('host'),
        'db_valid_rows': len(real),
        'sizes': [],
    }

    for n in sizes:
        data, source = build_series(real, n)
        reset_feature_cache()
        cold = timed_extract(features, data, warm=False)
        reset_feature_cache()
        warm = timed_extract(features, data, warm=True)
        reset_feature_cache()
        feats = features.extract_all_features(data)
        payload_bytes = len(pickle.dumps(feats, protocol=4))
        entry = {
            'n': n,
            'data_source': source,
            'extract_cold_s': round(cold, 4),
            'extract_warm_s': round(warm, 4),
            'feature_payload_kb': round(payload_bytes / 1024, 1),
        }
        report['sizes'].append(entry)
        print(f'N={n} [{source}] cold={cold * 1000:.0f}ms warm={warm * 1000:.0f}ms '
              f'payload={payload_bytes / 1024:.0f}KB')

    cache_data, _ = build_series(real, 1000)
    report['cache'] = {
        'capacity_8': cache_benchmark(features, cache_data, 8),
        'capacity_256': cache_benchmark(features, cache_data, 256),
    }
    print('cache:', report['cache'])

    if not args.no_e2e:
        report['e2e'] = []
        for n in sizes:
            data, source = build_series(real, n)
            if source != 'real':
                report['e2e'].append({'n': n, 'skipped': '真实库存不足，端到端不跑合成数据'})
                print(f'e2e N={n} skipped（真实库存不足）')
                continue
            res = e2e_benchmark(data, n, args.test_count)
            res['n'] = n
            res['data_source'] = source
            report['e2e'].append(res)
            print(f'e2e N={n} status={res["status"]} {res["elapsed_s"]}s '
                  f'({res["per_window_ms"]}ms/window)')

    out_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'bench_features_{datetime.now():%Y%m%d_%H%M%S}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('RESULT_FILE', out_path)


if __name__ == '__main__':
    main()
