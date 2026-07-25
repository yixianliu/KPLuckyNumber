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
实验v2：Top-1 精准度驱动的「选择性boost」自适应
==================================================
问题：全7算法EWMA噪声太大(80期后频率14.3%但EWMA把它压低了)。
新方案：只学 Top-1 精准度，对表现好的算法给予boost，
        且使用「有上限混合」防过拟合。

思路：
  1. 对每个算法计算 Top-1 精准度(smooth_rate = top1_hits/periods/5)
  2. 构建「Top-1 驱动权重」: base_weight * (1 + boost_multiplier * (top1_rate - random_baseline))
     其中 random_baseline = 10%，boost_multiplier = 2.0
  3. 混合: blended = alpha * static_default + (1-alpha) * top1_boosted
  4. walk-forward 验证

候选配置:
  A: static_default (基准)
  B: top1_boost alpha=0.5, mult=2.0
  C: top1_boost alpha=0.7, mult=2.0 (保守)
  D: top1_boost alpha=0.3, mult=3.0 (激进)
  E: top1_boost alpha=0.5, mult=3.0
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

POS_CN = ['万位', '千位', '千位', '百位', '十位', '个位']
ALGO_KEYS = ['frequency_weighted', 'omission_regression', 'bayesian_inference',
             'trend_momentum', 'markov_transition', 'pattern_continuation',
             'feature_engineering']
DEFAULT_W = {'frequency_weighted': 0.54, 'omission_regression': 0.34,
             'bayesian_inference': 0.10, 'trend_momentum': 0.01,
             'markov_transition': 0.005, 'pattern_continuation': 0.003,
             'feature_engineering': 0.002}


def load_history():
    import pymysql
    conn = pymysql.connect(host='localhost', port=3306, user='root', password='root',
                           database='lucky_number', charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute("SELECT issue, wan, qian, bai, shi, ge FROM p5_history_data ORDER BY issue ASC")
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        r['numbers'] = [r['wan'], r['qian'], r['bai'], r['shi'], r['ge']]
    return rows


def compute_algo_top1_scores(history_data, start_index, count):
    """
    计算每个算法在最近N期的 Top-1 精准度。
    返回 {algo: {top1_hits, periods, smooth_rate}}
    """
    from modules.predictor import P5Predictor
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False
    p.config.config['global']['enable_feature_engineering'] = True

    results = {}
    n = len(history_data)

    for algo in ALGO_KEYS:
        top1_hits = 0
        periods = 0
        # 临时设为单一算法
        orig_cfg = {}
        for name in ALGO_KEYS:
            orig_cfg[name] = dict(p.config.config['algorithms'][name])
            p.config.config['algorithms'][name]['enabled'] = (name == algo)
            p.config.config['algorithms'][name]['weight'] = 1.0 if name == algo else 0.0

        for i in range(start_index, min(start_index + count, n)):
            train = history_data[:i]
            actual = history_data[i]['numbers']
            res = p.predict(train, history_data[i]['issue'])
            fused = res.get('fused_probabilities', [])
            if not fused or len(fused) != 5:
                continue
            periods += 1
            for pos in range(5):
                pos_probs = fused[pos]
                sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
                if sorted_nums[0][0] == actual[pos]:
                    top1_hits += 1

        # 恢复配置
        for name in ALGO_KEYS:
            p.config.config['algorithms'][name] = orig_cfg[name]

        smooth_rate = top1_hits / max(periods, 1) / 5.0
        results[algo] = {
            'top1_hits': top1_hits,
            'periods': periods,
            'smooth_rate': round(smooth_rate, 4)
        }
        print(f"  {algo[:25]:25s}: top1_hits={top1_hits}/{periods*5} = {smooth_rate:.1%}")

    return results


def build_top1_boost_weights(top1_data, baseline=0.10, boost_mult=2.0):
    """
    基于 Top-1 精准度构建boost权重。
    boost_factor = 1 + mult * (top1_rate - baseline)
    """
    boosted = {}
    for algo, info in top1_data.items():
        rate = info['smooth_rate']
        factor = 1.0 + boost_mult * (rate - baseline)
        factor = max(factor, 0.1)  # 下限10%
        boosted[algo] = DEFAULT_W[algo] * factor
    # 归一化
    total = sum(boosted.values())
    return {k: v / total for k, v in boosted.items()}


def run_walkforward(history_data, start_index, test_count, weights, label):
    from modules.predictor import P5Predictor
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False
    p.config.config['global']['enable_adaptive_weights'] = False
    p.config.config['global']['enable_feature_engineering'] = True

    pos_hit6 = [0]*5; pos_hit1 = [0]*5; pos_hit3 = [0]*5; pos_hit5 = [0]*5
    match_counts = []; tested = 0
    n = len(history_data)
    end = min(start_index + test_count, n)

    for i in range(start_index, end):
        train = history_data[:i]
        actual = history_data[i]['numbers']
        for name in ALGO_KEYS:
            p.config.config['algorithms'][name]['enabled'] = True
            p.config.config['algorithms'][name]['weight'] = weights.get(name, 0)

        res = p.predict(train, history_data[i]['issue'])
        fused = res.get('fused_probabilities', [])
        if not fused or len(fused) != 5:
            continue

        mc = 0
        for pos in range(5):
            sorted_nums = sorted(fused[pos].items(), key=lambda x: x[1], reverse=True)
            top6 = [n for n,_ in sorted_nums[:6]]
            top3 = [n for n,_ in sorted_nums[:3]]
            top5 = [n for n,_ in sorted_nums[:5]]
            real = actual[pos]
            if real in top6: pos_hit6[pos]+=1; mc+=1
            if real in top3: pos_hit3[pos]+=1
            if real in top5: pos_hit5[pos]+=1
            if real == sorted_nums[0][0]: pos_hit1[pos]+=1

        match_counts.append(mc)
        tested += 1

    mc_arr = np.array(match_counts)
    return {
        'label': label,
        'tested': tested,
        'top1_overall': round(sum(pos_hit1)/tested/5*100, 2),
        'top3_overall': round(sum(pos_hit3)/tested/5*100, 2),
        'top5_overall': round(sum(pos_hit5)/tested/5*100, 2),
        'top6_overall': round(sum(pos_hit6)/(tested*5)*100, 2),
        'avg_match_count': round(float(mc_arr.mean()), 3),
    }


def normalize(w):
    s = sum(w.values())
    return {k: v/s for k, v in w.items()}


def main():
    hist = load_history()
    n = len(hist)
    # 用50期学Top-1精准度，再70期验证
    train_for_top1 = max(50, min(100, n // 2))
    test_start = n - 70
    test_count = 70

    print(f"=== Top-1 Precision Boost 实验 (v2) ===")
    print(f"  Top-1 计算期数: {train_for_top1}")
    print(f"  Walk-forward 验证期数: {test_count} (从{test_start})")

    print("\n--- Step 1: 计算各算法 Top-1 精准度 ---")
    top1_data = compute_algo_top1_scores(hist, test_start - train_for_top1, train_for_top1)

    print("\n--- Step 2: 构建候选配置 ---")
    candidates = {
        'A_static_default': normalize(dict(DEFAULT_W)),
    }

    configs = [
        ('B_boost0.5_mult2', 0.5, 2.0),
        ('C_boost0.7_mult2', 0.7, 2.0),
        ('D_boost0.3_mult3', 0.3, 3.0),
        ('E_boost0.5_mult3', 0.5, 3.0),
        ('F_boost0.8_mult1', 0.8, 1.0),
        ('G_boost0.6_mult2.5', 0.6, 2.5),
    ]

    for label, alpha, mult in configs:
        boost_w = build_top1_boost_weights(top1_data, baseline=0.10, boost_mult=mult)
        blended = {a: alpha * DEFAULT_W[a] + (1-alpha) * boost_w[a] for a in ALGO_KEYS}
        candidates[label] = normalize(blended)
        print(f"  {label}: alpha={alpha} mult={mult}")
        for a in ALGO_KEYS:
            print(f"    {a[:18]:18s}: static={DEFAULT_W[a]:.3f} boost={boost_w[a]:.3f} blend={candidates[label][a]:.3f}")

    print("\n--- Step 3: Walk-forward 验证 ---")
    results = []
    t_all = time.time()
    for label, weights in candidates.items():
        r = run_walkforward(hist, test_start, test_count, weights, label)
        results.append(r)
        print(f"  {label}: Top-1={r['top1_overall']}%  Top-3={r['top3_overall']}%  "
              f"Top-5={r['top5_overall']}%  Top-6={r['top6_overall']}%  mc={r['avg_match_count']}")
    print(f"总耗时: {time.time()-t_all:.0f}s")

    # 评分
    base = next(r for r in results if r['label'] == 'A_static_default')['top1_overall']
    print(f"\n=== 评分 (基准Top-1={base:.2f}%) ===")
    best = None
    for r in results:
        if r['label'] == 'A_static_default':
            continue
        delta = r['top1_overall'] - base
        score = delta * 3  # Top-1是核心目标
        r['_delta'] = round(delta, 2)
        r['_score'] = round(score, 2)
        flag = "★" if score > 0 else " "
        print(f"  {flag} {r['label']:25s}: ΔTop-1={delta:+.2f}%  score={score:.2f}")
        if best is None or score > best['_score']:
            best = r

    if best:
        print(f"\n★ 推荐配置: {best['label']} (Top-1={best['top1_overall']}%, "
              f"Δ={best['_delta']:+.2f}% vs static)")

    # 保存
    os.makedirs('reports/diagnostic', exist_ok=True)
    out = {
        'top1_data': top1_data,
        'candidates_weights': {k: {a: round(v, 4) for a, v in candidates[k].items()} for k in candidates},
        'test_start': test_start, 'test_count': test_count,
        'results': results,
        'recommendation': best['label'] if best else 'none'
    }
    with open('reports/diagnostic/top1_boost_v2.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n已保存 reports/diagnostic/top1_boost_v2.json")


if __name__ == '__main__':
    main()
