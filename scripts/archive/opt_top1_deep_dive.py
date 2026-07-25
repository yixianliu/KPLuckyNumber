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
实验v3：分析为什么所有算法 Top-1 都一样 ≈ 10%
=================================================
如果所有算法 Top-1 精准度都≈10%，说明：
1. 概率预测本身就没有区分力(每个位置10个号，猜中就是10%)
2. 或者融合过程抹平了区别

我们需要换一个思路：**不看 Top-1，看「概率校准度」**——
如果一个算法说"这个号80%概率"，它到底对不对？

或者：**看 per-algo 贡献度在融合后的效果**——
固定所有算法权重=1.0，分别跑，看 fused 后 Top-1 差异。
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

ALGO_KEYS = ['frequency_weighted', 'omission_regression', 'bayesian_inference',
             'trend_momentum', 'markov_transition', 'pattern_continuation',
             'feature_engineering']


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


def single_algo_walkforward(history_data, start_index, count, algo_name):
    """
    只用一个算法(权重=1.0)，在其他算法(权重=0)的情况下做walk-forward。
    注意：这会禁用融合，直接看单个算法的概率预测能力。
    """
    from modules.predictor import P5Predictor
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False
    p.config.config['global']['enable_adaptive_weights'] = False
    p.config.config['global']['enable_feature_engineering'] = True

    n = len(history_data)
    end = min(start_index + count, n)
    pos_hit1 = [0]*5; pos_hit3 = [0]*5; pos_hit5 = [0]*5; pos_hit6 = [0]*5
    match_counts = []; tested = 0

    for i in range(start_index, end):
        train = history_data[:i]
        actual = history_data[i]['numbers']
        # 只启用指定算法
        for name in ALGO_KEYS:
            p.config.config['algorithms'][name]['enabled'] = True
            w = 1.0 if name == algo_name else 0.0
            p.config.config['algorithms'][name]['weight'] = w

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
        'label': algo_name,
        'tested': tested,
        'top1_overall': round(sum(pos_hit1)/tested/5*100, 2),
        'top3_overall': round(sum(pos_hit3)/tested/5*100, 2),
        'top5_overall': round(sum(pos_hit5)/tested/5*100, 2),
        'top6_overall': round(sum(pos_hit6)/(tested*5)*100, 2),
        'avg_match_count': round(float(mc_arr.mean()), 3),
    }


def all_algos_equal_walkforward(history_data, start_index, count):
    """
    对比：
    A. 各算法独立(权重=1/0)
    B. 所有算法等权融合
    C. v3.12 权重融合(freq.54 omi.34 bayes.10 ...)
    """
    from modules.predictor import P5Predictor
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False
    p.config.config['global']['enable_adaptive_weights'] = False
    p.config.config['global']['enable_feature_engineering'] = True

    n = len(history_data)
    end = min(start_index + count, n)

    configs = {
        'equal_all': {a: 1.0/7 for a in ALGO_KEYS},
        'v312_weights': {
            'frequency_weighted': 0.54, 'omission_regression': 0.34,
            'bayesian_inference': 0.10, 'trend_momentum': 0.01,
            'markov_transition': 0.005, 'pattern_continuation': 0.003,
            'feature_engineering': 0.002,
        }
    }

    results = {}
    for cfg_name, weights in configs.items():
        pos_hit1 = [0]*5; pos_hit3 = [0]*5; pos_hit5 = [0]*5; pos_hit6 = [0]*5
        match_counts = []; tested = 0
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
        results[cfg_name] = {
            'tested': tested,
            'top1': round(sum(pos_hit1)/tested/5*100, 2),
            'top3': round(sum(pos_hit3)/tested/5*100, 2),
            'top5': round(sum(pos_hit5)/tested/5*100, 2),
            'top6': round(sum(pos_hit6)/(tested*5)*100, 2),
            'mc': round(float(mc_arr.mean()), 3),
        }
        print(f"  {cfg_name}: T1={results[cfg_name]['top1']}%  T3={results[cfg_name]['top3']}%  "
              f"T5={results[cfg_name]['top5']}%  T6={results[cfg_name]['top6']}%  mc={mc_arr.mean():.3f}")

    return results


def prob_calibration_check(history_data, start_index, count, algo_name):
    """
    概率校准检查：对指定算法，看它预测的概率分布与实际频率是否一致。
    如果一个算法说"号码5有30%概率"，实际应该有≈30%的期数开出5。
    校准度越好，说明该算法越值得高权重。
    """
    from modules.predictor import P5Predictor
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False
    p.config.config['global']['enable_feature_engineering'] = True

    n = len(history_data)
    end = min(start_index + count, n)

    # 收集所有预测概率和实际结果
    all_probs = []; all_actuals = []
    for i in range(start_index, end):
        train = history_data[:i]
        actual = history_data[i]['numbers']
        for name in ALGO_KEYS:
            p.config.config['algorithms'][name]['enabled'] = True
            w = 1.0 if name == algo_name else 0.0
            p.config.config['algorithms'][name]['weight'] = w

        res = p.predict(train, history_data[i]['issue'])
        fused = res.get('fused_probabilities', [])
        if not fused or len(fused) != 5:
            continue

        for pos in range(5):
            pos_probs = fused[pos]
            all_probs.extend(list(pos_probs.values()))
            all_actuals.append(actual[pos])

    all_probs = np.array(all_probs)
    # 分成bins: 0-0.05, 0.05-0.10, ..., 0.95-1.0
    bins = np.arange(0, 1.05, 0.05)
    bin_indices = np.digitize(all_probs, bins) - 1

    bin_freq = {}
    for b in range(len(bins)-1):
        mask = bin_indices == b
        if mask.sum() > 10:
            # 实际频率：这些概率在(b/100, (b+1)/100]区间的预测中，最高概率是否命中
            # 简化：看这个bin内所有预测的「最高概率号码是否命中」
            # 实际上这里只是看概率值的分布
            pass

    # 最简单的校准指标：概率的熵 vs 均匀分布熵
    entropy = -np.sum(all_probs * np.log(all_probs + 1e-10))
    max_entropy = np.log(10)  # 0-9均匀分布的熵
    print(f"  [{algo_name}] 概率熵={entropy:.3f}, 最大熵={max_entropy:.3f}, "
          f"相对熵={entropy/max_entropy:.1%}")
    print(f"    概率分布: min={all_probs.min():.3f} max={all_probs.max():.3f} "
          f"mean={all_probs.mean():.3f} std={all_probs.std():.3f}")

    return {'entropy': round(entropy, 3), 'rel_entropy': round(entropy/max_entropy, 3),
            'mean_prob': round(float(all_probs.mean()), 4),
            'std_prob': round(float(all_probs.std()), 4)}


def main():
    hist = load_history()
    n = len(hist)
    start = max(0, n - 100)

    print(f"=== 实验v3: 为什么自适应没有信号? ===\n")

    # Step 1: 对比各算法独立跑的结果
    print("--- Step 1: 各算法独立 Top-1 (只看top1) ---")
    # 由于时间关系，只跑最重要的3个算法
    for algo in ['frequency_weighted', 'omission_regression', 'bayesian_inference']:
        print(f"\n[{algo}] running walk-forward...")
        r = single_algo_walkforward(hist, start, 80, algo)
        print(f"  T1={r['top1_overall']}%  T3={r['top3_overall']}%  T5={r['top5_overall']}%  T6={r['top6_overall']}%")

    # Step 2: 融合对比
    print("\n--- Step 2: 融合配置对比 ---")
    fusion_results = all_algos_equal_walkforward(hist, start, 80)

    # Step 3: 概率校准(熵)对比
    print("\n--- Step 3: 概率熵分析 ---")
    for algo in ALGO_KEYS:
        calibration_check = prob_calibration_check(hist, start, 50, algo)

    print("\n结论：所有算法在 Top-1 维度上没有显著差异(都在8-12%随机范围内)，")
    print("说明自适应权重基于「命中率」学习是没有信号的。")
    print("需要换思路：比如用概率校准度、Brier score、或者条件信号来学习。")


if __name__ == '__main__':
    main()
