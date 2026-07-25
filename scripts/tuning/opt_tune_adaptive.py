#!/usr/bin/env python
# -*- coding: utf-8 -*-
# --- 路径锚定(B3修复): 向上搜索项目根(modules/+main.py), 注入 sys.path ---
import os
import sys
def _find_project_root(_start):
    _cur = os.path.abspath(_start)
    while True:
        if os.path.isdir(os.path.join(_cur, 'modules')) and \
           os.path.isfile(os.path.join(_cur, 'main.py')):
            return _cur
        _p = os.path.dirname(_cur)
        if _p == _cur:
            return os.path.dirname(os.path.abspath(_start))
        _cur = _p
_PROJECT_ROOT = _find_project_root(__file__)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

"""
opt_tune_adaptive.py (v3.16 纯新增, 零改封板)

P3 自适应权重扫参 —— 诚实验证 enable_adaptive_weights 是否构成稳健超随机杠杆。

背景:
    冻结基线验证 P0/P1/P2 均未发现任何参数构成稳健"超随机"杠杆。
    enable_adaptive_weights 是最后一个尚未实证的全局布尔参数。
    根据 v3.14 代码注释(walk-forward 不够 30~50 期), P3 预期噪声 + 易过拟合。

设计原则:
    - 直接复用 P2(opt_tune_weights.py) 的 walk-forward 内核(零 matplotlib 依赖)
    - 通过 deep_merge 注入 tuning_config, 控制组=冻结默认权重
    - 诚实边界: Top-5 的 95%CI 下界必须 > 冻结基线 + 随机 才判提升
    - 严格隔离: 每个配置独立 P5Predictor 实例, 共享同一历史数据

调用方式:
    python opt_tune_adaptive.py                  # 全量(约 50 档 * 300期 * ~2min ≈ 2h)
    python opt_tune_adaptive.py --quick          # 快速(4 档, ~8min)
    python opt_tune_adaptive.py --lite           # 精选(9 档, ~18min, 推荐日常用)

输出:
    reports/backtest/sweep_adaptive_p3.json   - JSON 报告(含 95%CI + fingerprint)
    reports/backtest/sweep_adaptive_p3.xlsx   - Excel 仪表盘
"""
import sys
import os
import json
import time
import hashlib
import math
import logging
import argparse
from typing import Dict, Any, List, Tuple
from copy import deepcopy

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('opt_tune_adaptive')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts (override takes precedence)."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_freeze_baseline() -> dict:
    """加载冻结基线配置 (复用 opt_freeze_baseline 评估口径)."""
    report_path = os.path.join(PROJECT_ROOT, 'reports', 'backtest', 'freeze_baseline_v315.json')
    with open(report_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    overall = data.get('overall_stats', {})
    ci95 = data.get('confidence_95', {})
    random = data.get('random_baseline', {})
    
    baseline = {
        'top1_accuracy': overall.get('avg_top1_hit_rate', 0),
        'top3_accuracy': overall.get('avg_top3_hit_rate', 0),
        'top5_accuracy': overall.get('avg_top5_hit_rate', 0),
        'top6_accuracy': overall.get('avg_top6_hit_rate', 0),
        'top5_ci': ci95.get('top5', {}),
        'top1_ci': ci95.get('top1', {}),
        'random_top1': random.get('top1', 10.0),
        'random_top3': random.get('top3', 30.0),
        'random_top5': random.get('top5', 50.0),
        'random_top6': random.get('top6', 60.0),
    }
    logger.info(f'加载冻结基线: Top-5={baseline["top5_accuracy"]:.2f}%, '
                f'CI=[{baseline["top5_ci"].get("ci95_low", 0):.2f},{baseline["top5_ci"].get("ci95_high", 0):.2f}]')
    return baseline


def build_sweep_configs() -> List[Dict[str, Any]]:
    """构建自适应权重扫参网格(全量)."""
    control = {'global': {'enable_adaptive_weights': False}}
    param_grid = {
        'ewma_alpha': [0.1, 0.2, 0.3, 0.5, 0.7],
        'ewma_blend': [0.05, 0.1, 0.2, 0.3, 0.5],
        'adaptive_metric': ['top1_hit', 'hit_rate'],
    }
    full_configs = []
    for alpha in param_grid['ewma_alpha']:
        for blend in param_grid['ewma_blend']:
            for metric in param_grid['adaptive_metric']:
                cfg = {
                    'global': {
                        'enable_adaptive_weights': True,
                        'ewma_alpha': alpha,
                        'ewma_blend': blend,
                        'adaptive_metric': metric,
                    }
                }
                full_configs.append(cfg)
    return [control] + full_configs


def get_test_configs(mode='all'):
    """按模式返回测试配置列表.
    
    Args:
        mode: 'all' / 'quick' / 'lite'
    Returns:
        List[Dict]
    """
    all_configs = build_sweep_configs()
    
    if mode == 'quick':
        # 快速: 控制组 + 4 档极端配置
        return [
            all_configs[0],  # 控制组
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.7, 'ewma_blend': 0.5, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.1, 'ewma_blend': 0.05, 'adaptive_metric': 'hit_rate'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.3, 'ewma_blend': 0.1, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.3, 'ewma_blend': 0.1, 'adaptive_metric': 'hit_rate'}},
        ]
    
    elif mode == 'lite':
        # Lite: 控制组 + 8 档代表性配置
        reps = [
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.1, 'ewma_blend': 0.1, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.3, 'ewma_blend': 0.1, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.5, 'ewma_blend': 0.1, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.3, 'ewma_blend': 0.05, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.3, 'ewma_blend': 0.3, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.3, 'ewma_blend': 0.1, 'adaptive_metric': 'hit_rate'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.7, 'ewma_blend': 0.5, 'adaptive_metric': 'top1_hit'}},
            {'global': {'enable_adaptive_weights': True, 'ewma_alpha': 0.1, 'ewma_blend': 0.05, 'adaptive_metric': 'hit_rate'}},
        ]
        return [reps[0]] + reps  # 加控制组 = 9 档
        
    else:  # all
        return all_configs


def _evaluate_prediction(pred: dict, actual_numbers: list) -> dict:
    """单期预测评估 —— 基于 fused_probabilities 逐位排名 (复用 opt_freeze_baseline 内核).
    
    pred: P5Predictor.predict() 返回的扁平字典
          包含 'fused_probabilities' (list of 5 dicts: [{digit_str: prob}, ...])
    actual_numbers: [wan, qian, bai, shi, ge] 开奖号码
    """
    fused_probs = pred.get('fused_probabilities', [])
    top1_hits = 0
    top3_hits = 0
    top5_hits = 0
    top6_hits = 0
    
    for pos in range(5):
        if pos >= len(fused_probs):
            break
        pos_probs = fused_probs[pos]
        actual_num = actual_numbers[pos]
        # 按概率降序排名
        sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
        rank = next((i + 1 for i, (n, _) in enumerate(sorted_nums) if int(n) == int(actual_num)), 10)
        
        if rank == 1:
            top1_hits += 1
        if rank <= 3:
            top3_hits += 1
        if rank <= 5:
            top5_hits += 1
        if rank <= 6:
            top6_hits += 1
    
    return {
        'top1': top1_hits,
        'top3': top3_hits,
        'top5': top5_hits,
        'top6': top6_hits,
    }


def evaluate(results: List[dict]) -> Tuple[dict, dict]:
    """计算整体命中率和 95% CI (复用 opt_freeze_baseline 聚合口径).
    
    results: list of {'top1': 0-5, 'top3': 0-5, 'top5': 0-5, 'top6': 0-5}
    返回: (agg, ci) — agg 值为百分比(0-100), ci 为 (low, high) 百分比
    """
    if not results:
        return {}, {}
    
    n = len(results)
    # hit_count 范围 0-5, accuracy = avg_count / 5 * 100
    avg_top1 = sum(r['top1'] for r in results) / n / 5 * 100
    avg_top3 = sum(r['top3'] for r in results) / n / 5 * 100
    avg_top5 = sum(r['top5'] for r in results) / n / 5 * 100
    avg_top6 = sum(r['top6'] for r in results) / n / 5 * 100
    
    # 95% CI for Top-5 (二项分布近似)
    p = avg_top5 / 100
    se = math.sqrt(p * (1 - p) / n)
    ci5_low = (p - 1.96 * se) * 100
    ci5_high = (p + 1.96 * se) * 100
    
    # 95% CI for Top-1
    p1 = avg_top1 / 100
    se1 = math.sqrt(p1 * (1 - p1) / n)
    ci1_low = (p1 - 1.96 * se1) * 100
    ci1_high = (p1 + 1.96 * se1) * 100
    
    agg = {
        'avg_top1_hit_rate': round(avg_top1, 2),
        'avg_top3_hit_rate': round(avg_top3, 2),
        'avg_top5_hit_rate': round(avg_top5, 2),
        'avg_top6_hit_rate': round(avg_top6, 2),
    }
    ci = {
        'top1': (round(ci1_low, 2), round(ci1_high, 2)),
        'top5': (round(ci5_low, 2), round(ci5_high, 2)),
    }
    return agg, ci


def _load_history() -> list:
    """从 DB 加载历史开奖数据 (供 run_sweep 和 run_walkforward 复用)."""
    from modules.database import P5Database
    db = P5Database()
    db.connect()
    history = db.get_history_data(limit=None, order='ASC')
    db.disconnect()
    return history


def run_walkforward(test_count, custom_config=None, data_limit=60, min_train=30, history=None):
    """与 P2(opt_tune_weights.py) 一致的 walk-forward; custom_config 注入自适应权重.
    
    零 matplotlib 依赖，用 history 参数避免重复连接 DB。
    """
    from modules.predictor import P5Predictor, P5PredictorConfig
    
    if history is None:
        history = _load_history()
    
    total_hist = len(history)
    start_index = max(min_train, total_hist - test_count)
    effective = min(test_count, total_hist - start_index)
    if effective <= 0:
        return []
    
    if custom_config:
        base_config = deepcopy(P5PredictorConfig.DEFAULT_CONFIG)
        merged_config = deep_merge(base_config, custom_config)
        predictor = P5Predictor(config=P5PredictorConfig(custom_config=merged_config))
    else:
        predictor = P5Predictor()
    
    # 禁 AI
    predictor.ai_available = False
    predictor.config.config.setdefault('global', {})['enable_ai_model'] = False
    
    results = []
    for i in range(start_index, start_index + effective):
        train = history[:i]
        target = history[i]['issue']
        actual = history[i]['numbers']
        pr = predictor.predict(train, target)
        if 'error' in pr:
            continue
        results.append(_evaluate_prediction(pr, actual))
    return results


def run_sweep(mode='all', data_limit=60, test_limit=None, min_train=30):
    """跑自适应权重扫参 (复用 P2 walk-forward 内核, 零 matplotlib 依赖)."""
    # Load freeze baseline for comparison
    baseline = load_freeze_baseline()
    baseline_top5 = baseline.get('top5_accuracy', 0)
    baseline_ci = baseline.get('top5_ci', {})
    baseline_ci_low = baseline_ci.get('ci95_low', 0)
    baseline_ci_high = baseline_ci.get('ci95_high', 0)
    random_top5 = baseline.get('random_top5', 50.0)
    random_top1 = baseline.get('random_top1', 10.0)
    logger.info(f'冻结基线: Top-5={baseline_top5:.2f}%, CI=[{baseline_ci_low:.2f},{baseline_ci_high:.2f}]')
    logger.info(f'随机基线: Top-5={random_top5:.2f}%, Top-1={random_top1:.2f}%')
    
    # 加载历史数据（只连接一次 DB）
    history = _load_history()
    if not history:
        logger.error('✗ 历史数据为空, 退出')
        return
    
    test_limit = test_limit or len(history)
    
    # Get sweep configs
    configs = get_test_configs(mode)
    n_configs = len(configs)
    logger.info(f'开始 P3 自适应权重扫参: {n_configs} 档 * {test_limit}期 walk-forward, 模式={mode}')
    
    results = []
    start_time = time.time()
    
    for idx, custom_cfg in enumerate(configs, 1):
        label_parts = []
        for k, v in custom_cfg.get('global', {}).items():
            label_parts.append(f'{k}={v}')
        label = '|'.join(label_parts) if label_parts else 'FROZEN'
        
        iter_start = time.time()
        logger.info(f'[{idx}/{n_configs}] 测试 {label} ...')
        
        try:
            res = run_walkforward(test_limit, custom_cfg, data_limit, min_train, history)
            if not res:
                logger.error(f'  ✗ No results for {label}')
                continue
            
            agg, ci = evaluate(res)
            top1 = agg.get('avg_top1_hit_rate', 0)
            top3 = agg.get('avg_top3_hit_rate', 0)
            top5 = agg.get('avg_top5_hit_rate', 0)
            top6 = agg.get('avg_top6_hit_rate', 0)
            ci5 = ci.get('top5', (0, 0))
            ci5_low = ci5[0]
            ci5_high = ci5[1]
            
            fp_hash = hashlib.md5(
                json.dumps(custom_cfg, sort_keys=True, ensure_ascii=False).encode('utf-8')
            ).hexdigest()[:8]
            
            elapsed = round(time.time() - iter_start, 2)
            total_elapsed = round(time.time() - start_time, 2)
            
            entry = {
                'config_id': idx,
                'label': label,
                'fingerprint': fp_hash,
                'custom_config': custom_cfg,
                'top1_accuracy': round(top1, 2),
                'top3_accuracy': round(top3, 2),
                'top5_accuracy': round(top5, 2),
                'top6_accuracy': round(top6, 2),
                'top5_ci_95': [round(ci5_low, 2), round(ci5_high, 2)],
                'beats_random': ci5_low > random_top5,
                'beats_baseline': ci5_low > baseline_ci_low,
                'samples': len(res),
                'elapsed_seconds': elapsed,
                'total_elapsed_seconds': total_elapsed,
            }
            
            results.append(entry)
            logger.info(f'  ✓ Top-1={entry["top1_accuracy"]:.2f}% Top-5={entry["top5_accuracy"]:.2f}% '
                        f'CI=[{ci5_low:.2f},{ci5_high:.2f}] ({elapsed}s)')
            
        except Exception as e:
            logger.error(f'  ✗ Config {label}: {e}', exc_info=True)
            results.append({
                'config_id': idx,
                'label': label,
                'error': str(e),
            })
    
    # Summary
    logger.info('=' * 70)
    logger.info('=== P3 自适应权重扫参结果 ===')
    logger.info(f'  控制组(冻结): Top-5={baseline_top5:.2f}% CI=[{baseline_ci_low:.2f},{baseline_ci_high:.2f}]')
    logger.info(f'  随机基线: Top-5={random_top5:.2f}% Top-1={random_top1:.2f}%')
    
    for r in results:
        if 'error' in r:
            continue
        vs_baseline = r['top5_accuracy'] - baseline_top5
        vs_random = r['top5_accuracy'] - random_top5
        arrow = '↑' if vs_baseline > 0 else '↓'
        logger.info(f'  {r["label"]:40s} Top-5={r["top5_accuracy"]:6.2f}% (基线{arrow}{vs_baseline:+.2f}%, 随机{vs_random:+.2f}%)')
    
    logger.info(f'  总耗时: {round(time.time() - start_time, 2)}秒')
    logger.info('=' * 70)
    
    return {
        'mode': mode,
        'history_size': len(history),
        'data_limit': data_limit,
        'test_limit': test_limit,
        'min_train': min_train,
        'baseline_top5': baseline_top5,
        'baseline_top5_ci_low': baseline_ci_low,
        'baseline_top5_ci_high': baseline_ci_high,
        'random_top5': random_top5,
        'configs_tested': len(results),
        'results': results,
    }


def main():
    parser = argparse.ArgumentParser(description='P3 自适应权重扫参')
    parser.add_argument('--mode', choices=['all', 'quick', 'lite'], default='lite',
                        help='扫参模式: quick(4档) / lite(9档,推荐) / all(~50档)')
    parser.add_argument('--data-limit', type=int, default=60,
                        help='每次预测回看窗口大小 (default=60)')
    parser.add_argument('--test-limit', type=int, default=None,
                        help='Walk-forward 测试期数 (default=剩余全部)')
    parser.add_argument('--min-train', type=int, default=30,
                        help='最小训练期数 (default=30)')
    
    args = parser.parse_args()
    
    report = run_sweep(
        mode=args.mode,
        data_limit=args.data_limit,
        test_limit=args.test_limit,
        min_train=args.min_train,
    )
    
    # Save JSON report
    os.makedirs(os.path.join(PROJECT_ROOT, 'reports', 'backtest'), exist_ok=True)
    out_json = os.path.join(PROJECT_ROOT, 'reports', 'backtest', 'sweep_adaptive_p3.json')
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info(f'JSON 已保存: {out_json}')
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
