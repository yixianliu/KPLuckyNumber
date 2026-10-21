# -*- coding: utf-8 -*-
"""特征工程缓存 A28 探针（bench）：同指纹重复抽取耗时降幅 ≥80% 验收。

用法：
    python scripts/diagnose/bench_feature_cache.py

验收口径（PUP-20261019-013100 ALG-04 / A28）：
    优化前基线 = 指纹 miss 的全量特征提取（cache bypass 等价路径）；
    优化后     = 同 history + 同 windows 二次调用 → 缓存命中。
    目标：第二次调用耗时相对第一次降幅 ≥ 80%。

计时设计（保证口径可复现）：
    - 固定随机种子构造 200 期模拟历史（模拟数据与真实数据对
      extract_all_features 的计算成本同构，且不依赖数据库环境）；
    - ROUNDS 轮独立冷/热对比：每轮先 reset_feature_cache() 清空
      共享缓存，保证 cold 段必为 miss 路径、hot 段必为 hit 路径；
    - 以逐轮降幅的中位数与最小值为判定依据（规避单次时序抖动），
      结果同时落盘 reports/prep/bench_feature_cache.json。

排列五开奖为完全独立的随机事件，本脚本仅度量工程性能指标，
与中奖概率无关。
"""
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime

sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

from modules.features import P5Features  # noqa: E402
from modules.features import (  # noqa: E402
    reset_feature_cache,
    _get_feature_cache,
)

# --- bench 参数（写入 JSON，保证口径可复现） ---
ROUNDS = 5                           # 独立冷/热对比轮数
HISTORY_COUNT = 200                  # 模拟历史期数（≥ DATA_WINDOW_PERIOD）
WINDOWS = [5, 10, 20, 50]            # 默认窗口配置
DROP_TARGET = 0.80                   # A28：第二次调用耗时降幅 ≥ 80%
_PROJ_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPORT_PATH = os.path.join(
    _PROJ_ROOT, 'reports', 'prep', 'bench_feature_cache.json')


def _gen_history(count: int = HISTORY_COUNT, seed: int = 42) -> list:
    """构造按 issue 正序的模拟历史数据（固定种子，跨运行可复现）"""
    rng = random.Random(seed)
    return [{
        'issue': f'2026{i:04d}',
        'numbers': [rng.randint(0, 9) for _ in range(5)],
        'draw_date': f'2026-{i % 12 + 1:02d}-{i % 28 + 1:02d}',
    } for i in range(count)]


def _run_round(fe: P5Features, data: list) -> dict:
    """单轮冷/热对比：reset 后 cold 必 miss、hot 必 hit"""
    reset_feature_cache()
    _get_feature_cache().clear()

    t0 = time.perf_counter()
    fe.extract_all_features(data, windows=WINDOWS)
    cold_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    fe.extract_all_features(data, windows=WINDOWS)
    hot_ms = (time.perf_counter() - t1) * 1000.0

    return {
        'cold_ms': round(cold_ms, 2),
        'hot_ms': round(hot_ms, 2),
        # 缓存命中使 hot 不可能超过 cold；仍做除零保护
        'drop': 1.0 - hot_ms / cold_ms if cold_ms > 0 else 0.0,
        'stats': _get_feature_cache().stats(),
    }


def main() -> int:
    """主入口：跑 ROUNDS 轮对比并落盘 JSON 报告"""
    fe = P5Features()
    data = _gen_history()

    rounds = [_run_round(fe, data) for _ in range(ROUNDS)]

    cold_median = statistics.median(r['cold_ms'] for r in rounds)
    hot_median = statistics.median(r['hot_ms'] for r in rounds)
    drop_values = [r['drop'] for r in rounds]
    drop_median = statistics.median(drop_values)
    drop_min = min(drop_values)

    print('=' * 60)
    print('ALG-04 A28 特征缓存探针（同指纹重复抽取）')
    print('=' * 60)
    print(f'轮数: {ROUNDS}  历史期数: {HISTORY_COUNT}  '
          f'windows: {WINDOWS}')
    for i, r in enumerate(rounds, 1):
        print(f"  第{i}轮: cold={r['cold_ms']:>8.2f}ms  "
              f"hot={r['hot_ms']:>7.2f}ms  "
              f"降幅={r['drop'] * 100:.1f}%  "
              f"(hits={r['stats']['hits']}, misses={r['stats']['misses']})")
    print('-' * 60)
    print(f'cold 中位数: {cold_median:.2f}ms')
    print(f'hot  中位数: {hot_median:.2f}ms')
    print(f'降幅中位数: {drop_median * 100:.1f}%  '
          f'降幅最小值: {drop_min * 100:.1f}%  '
          f'目标: ≥{DROP_TARGET * 100:.0f}%')

    passed = drop_median >= DROP_TARGET
    print('判定: ' + ('PASS' if passed else 'FAIL'))
    print('=' * 60)

    report = {
        'metric': 'A28_feature_cache_drop',
        'generated_at': datetime.now().isoformat(),
        'params': {
            'rounds': ROUNDS,
            'history_count': HISTORY_COUNT,
            'windows': WINDOWS,
            'drop_target': DROP_TARGET,
        },
        'result': {
            'rounds': rounds,
            'cold_ms_median': round(cold_median, 2),
            'hot_ms_median': round(hot_median, 2),
            'drop_median': round(drop_median, 4),
            'drop_min': round(drop_min, 4),
            'passed': passed,
        },
    }
    os.makedirs(os.path.dirname(_REPORT_PATH), exist_ok=True)
    with open(_REPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'报告已落盘: {_REPORT_PATH}')

    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
