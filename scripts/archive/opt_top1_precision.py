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
实验：用 Top-1 精准度替代覆盖命中率做自适应权重学习
======================================================
问题：当前 algo_evaluations 存的是 per-position 覆盖率(0-5位/5)，
     对所有算法都≈0.5，EWMA 没有区分力，最终把权重拉向均匀，稀释 Top-1 精准度。

思路：改为存 Top-1 精准度(预测的最高概率号码=开奖号 ? 1:0)，
     频率算法应该在这项指标上有显著优势。

用法:
  python opt_top1_precision.py [--count 120]  # 最近N期walk-forward验证
"""
import sys, os, json, argparse, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

POS_CN = ['万位', '千位', '百位', '十位', '个位']
RANDOM_BASE = {'top1': 10.0}
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


class Top1AdaptiveManager:
    """
    新版自适应权重管理器：用 Top-1 精准度(是否是最高概率预测号)驱动 EWMA，
    而非覆盖命中率。
    """
    def __init__(self, ewma_alpha=0.3):
        self.ewma_alpha = ewma_alpha
        # {algo_name: {'count': N, 'top1_hits': H, 'ewma': initial_weight}}
        self.algo_top1 = {}
        for algo in ALGO_KEYS:
            self.algo_top1[algo] = {'count': 0, 'top1_hits': 0.0, 'ewma': DEFAULT_W[algo]}

    def record_top1_hit(self, algo_name: str, is_top1_correct: float):
        """
        记录 Top-1 精准度。
        is_top1_correct: 0-1，表示本期该算法在5个位置上是否有1+个位置
                         的最高概率预测号=实际开奖号(可叠加0-5)。
        """
        if algo_name not in self.algo_top1:
            return
        rec = self.algo_top1[algo_name]
        rec['count'] += 1
        rec['top1_hits'] += is_top1_correct
        # EWMA 基于累计精准度(smooth_rate)
        smooth_rate = rec['top1_hits'] / max(rec['count'], 1)
        rec['ewma'] = (self.ewma_alpha * smooth_rate +
                       (1 - self.ewma_alpha) * rec['ewma'])

    def get_adaptive_weights(self):
        total = sum(v['ewma'] for v in self.algo_top1.values())
        if total == 0:
            return {k: v['ewma'] for k, v in self.algo_top1.items()}
        return {k: v['ewma'] / total for k, v in self.algo_top1.items()}


def compute_algo_top1(p, history_data, start_index, count, algo_name):
    """
    对指定算法，计算其在最近 count 期内的 Top-1 精准度向量(每期1个值0-5)。
    每期: 看该算法在各位置的概率分布中，最高概率号码是否匹配实际开奖。
    """
    n = len(history_data)
    top1_scores = []
    for i in range(start_index, min(start_index + count, n)):
        train = history_data[:i]
        actual = history_data[i]['numbers']
        # 直接操纵 predictor 的内部概率
        orig_algos = dict(p.config.config['algorithms'])
        for name in ALGO_KEYS:
            p.config.config['algorithms'][name]['enabled'] = (name == algo_name)
            if name == algo_name:
                p.config.config['algorithms'][name]['weight'] = 1.0
            else:
                p.config.config['algorithms'][name]['weight'] = 0.0
        p.config.config['global']['enable_ai_model'] = False
        res = p.predict(train, history_data[i]['issue'])
        fused = res.get('fused_probabilities', [])
        if not fused or len(fused) != 5:
            continue
        score = 0
        for pos in range(5):
            pos_probs = fused[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top1_pred = sorted_nums[0][0]
            if top1_pred == actual[pos]:
                score += 1
        top1_scores.append(score)
        # 还原算法配置
        for name, cfg in orig_algos.items():
            p.config.config['algorithms'][name] = cfg
    return top1_scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=120)
    args = ap.parse_args()

    hist = load_history()
    n = len(hist)
    start = max(0, n - args.count)
    count = min(args.count, n - start)

    from modules.predictor import P5Predictor
    p = P5Predictor()

    print(f"=== Top-1 Precision 实验：最近 {count} 期，7 算法逐一分析 ===")

    # Step 1: 收集每个算法的 Top-1 精准度向量
    algo_top1_vectors = {}
    for algo in ALGO_KEYS:
        print(f"  计算 [{algo}] Top-1 精准度...")
        vec = compute_algo_top1(p, hist, start, count, algo)
        top1_rates = np.mean(vec) / 5.0  # 归一化到 0-1
        algo_top1_vectors[algo] = {
            'scores': vec,
            'mean_score': round(float(np.mean(vec)), 4),
            'top1_rate': round(float(top1_rates), 4),
            'periods': len(vec)
        }
        print(f"    平均top1命中数={algo_top1_vectors[algo]['mean_score']:.2f}/5 "
              f"(Top-1精准度={top1_rates:.1%})")

    # Step 2: 模拟用 Top-1 精准度做 EWMA 学习
    mgr = Top1AdaptiveManager(ewma_alpha=0.3)
    for algo, info in algo_top1_vectors.items():
        for score in info['scores']:
            mgr.record_top1_hit(algo, score)  # 0-5分/期

    final_weights = mgr.get_adaptive_weights()
    print("\n=== EWMA Top-1 学习后的权重 ===")
    for algo, w in sorted(final_weights.items(), key=lambda x: -x[1]):
        print(f"  {algo[:15]:15s}: {w:.4f}")

    # Step 3: 用 Top-1 驱动的权重做 walk-forward 验证
    print("\n=== Walk-forward 验证：Top-1 驱动的自适应权重 vs 静态默认 ===")
    # 配置 A: 静态默认
    w_static = normalize(dict(DEFAULT_W))
    r_static = run_walkforward(hist, start, count, p, w_static, "A_static_default")

    # 配置 B: Top-1 EWMA 驱动(直接用 mgr 输出的归一化权重)
    w_top1 = final_weights
    r_top1 = run_walkforward(hist, start, count, p, w_top1, "B_top1_ewma")

    # 配置 C: 混合 0.9*static + 0.1*top1(类似 Plan B G 配置)
    w_blend = {a: 0.9 * DEFAULT_W[a] + 0.1 * final_weights[a] for a in ALGO_KEYS}
    w_blend = normalize(w_blend)
    r_blend = run_walkforward(hist, start, count, p, w_blend, "C_blend0.1_top1")

    results = [r_static, r_top1, r_blend]

    # Step 4: 对比评分
    print("\n=== 评分对比 ===")
    print(f"{'配置':18s} {'Top-1%':8s} {'Top-3%':8s} {'Top-5%':8s} {'Top-6%':8s} {'mc':6s}")
    for r in results:
        print(f"{r['label']:18s} {r['top1_overall']:8.2f} {r['top3_overall']:8.2f} "
              f"{r['top5_overall']:8.2f} {r['top6_overall']:8.2f} {r['avg_match_count']:6.3f}")

    # 评分 = Top-1增益×3 (主要目标)
    base_t1 = r_static['top1_overall']
    best = None
    best_score = -999
    for r in results:
        if r['label'] == 'A_static_default':
            continue
        t1_gain = r['top1_overall'] - base_t1
        score = t1_gain * 3
        r['_score'] = round(score, 4)
        r['_t1_delta'] = round(t1_gain, 4)
        print(f"  {r['label']}: score={score:.4f} (ΔTop-1={t1_gain:+.2f}%)")
        if score > best_score:
            best_score = score
            best = r

    if best:
        print(f"\n★ 推荐: {best['label']} (Top-1={best['top1_overall']}%, "
              f"Δ={best['_t1_delta']:+.2f}% vs static default {base_t1:.2f}%)")

    # 保存 JSON
    os.makedirs('reports/diagnostic', exist_ok=True)
    out = {
        'start_index': start, 'tested': count,
        'algo_top1_vectors': {k: {ik: iv for ik, iv in v.items() if ik != 'scores'}
                               for k, v in algo_top1_vectors.items()},
        'ewma_final_weights': {k: round(v, 4) for k, v in final_weights.items()},
        'static_weights': {k: round(v, 4) for k, v in w_static.items()},
        'blended_weights': {k: round(v, 4) for k, v in w_blend.items()},
        'random_base': RANDOM_BASE,
        'results': results,
        'recommendation': best['label'] if best else 'none'
    }
    with open('reports/diagnostic/top1_precision.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n已保存 reports/diagnostic/top1_precision.json")


def normalize(w):
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def run_walkforward(history_data, start_index, test_count, predictor, weights, label):
    p = predictor
    p.config.config['global']['enable_ai_model'] = False
    p.config.config['global']['enable_adaptive_weights'] = False
    for name in ALGO_KEYS:
        p.config.config['algorithms'][name]['enabled'] = True
        p.config.config['algorithms'][name]['weight'] = weights.get(name, 0)
    p.config.config['global']['enable_feature_engineering'] = True

    pos_hit6 = [0] * 5
    pos_hit1 = [0] * 5
    pos_hit3 = [0] * 5
    pos_hit5 = [0] * 5
    match_counts = []
    n = len(history_data)
    end = min(start_index + test_count, n)
    tested = 0
    for i in range(start_index, end):
        train = history_data[:i]
        actual = history_data[i]['numbers']
        res = p.predict(train, history_data[i]['issue'])
        fused = res.get('fused_probabilities', [])
        if not fused or len(fused) != 5:
            continue
        mc = 0
        for pos in range(5):
            pos_probs = fused[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top6 = [n for n, _ in sorted_nums[:6]]
            top3 = [n for n, _ in sorted_nums[:3]]
            top5 = [n for n, _ in sorted_nums[:5]]
            real = actual[pos]
            if real in top6: pos_hit6[pos] += 1; mc += 1
            if real in top3: pos_hit3[pos] += 1
            if real in top5: pos_hit5[pos] += 1
            if real == sorted_nums[0][0]: pos_hit1[pos] += 1
        match_counts.append(mc)
        tested += 1

    mc_arr = np.array(match_counts)
    return {
        'label': label,
        'tested': tested,
        'top1_overall': round(sum(pos_hit1) / tested / 5 * 100, 2),
        'top3_overall': round(sum(pos_hit3) / tested / 5 * 100, 2),
        'top5_overall': round(sum(pos_hit5) / tested / 5 * 100, 2),
        'top6_overall': round(sum(pos_hit6) / (tested * 5) * 100, 2),
        'avg_match_count': round(float(mc_arr.mean()), 3),
    }


if __name__ == '__main__':
    main()
