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
Plan B 实验：防止权重过均匀化
================================
背景：复活自学习闭环后，EWMA 把所有算法权重拉向均匀（因各算法真实命中率都≈随机0.5），
导致覆盖类指标(Top-5/6)略升，但 Top-1 精准度从 11.5% 跌到 8.33%。
本实验测量几种"加权上限 + 调小 EWMA alpha"组合的真实效果，找出最优配置。

方法：复用 P5Predictor，关 AI、关自适应权重（纯用配置权重），对最近 120 期真实已开奖做
walk-forward 验证。对每个候选权重配置统计 Top-1/3/5/6 命中率与 match_count。

候选配置（全部归一化到 sum=1）：
  A default              : v3.12 硬编码默认 (freq .54 omi .34 bayes .10 次 .018)
  B ewma_unconstrained   : 0.7*def + 0.3*ewma_hr  (复现当前无约束闭环)
  C cap_minor_0.10       : 上述混合后对次要算法(趋势/马尔可夫/形态/特征)封顶 0.10
  D cap_minor_0.05       : 封顶 0.05
  E alpha0.15_cap0.10    : 0.85*def + 0.15*ewma_hr，封顶 0.10
  F keep_core            : freq .50 omi .32 bayes .10 次算法小幅提升(.03/.02/.015/.015)

用法:
  python opt_plan_b.py --count 120        # 全量6配置对比(后台推荐)
  python opt_plan_b.py --count 10         # 小样本冒烟
"""
import sys, os, json, argparse, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

POS_CN = ['万位', '千位', '百位', '十位', '个位']
RANDOM_BASE = {'top1': 10.0, 'top3': 30.0, 'top5': 50.0, 'top6': 60.0}
ALGO_KEYS = ['frequency_weighted', 'omission_regression', 'bayesian_inference',
             'trend_momentum', 'markov_transition', 'pattern_continuation',
             'feature_engineering']
MINOR = ['trend_momentum', 'markov_transition', 'pattern_continuation', 'feature_engineering']
CORE = ['frequency_weighted', 'omission_regression', 'bayesian_inference']

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


def normalize(w):
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def cap_normalize(w, cap):
    nw = normalize(w)
    for a in MINOR:
        if nw[a] > cap:
            nw[a] = cap
    return normalize(nw)


def build_candidates(ewma_hr):
    cands = {}
    cands['A_default'] = normalize(dict(DEFAULT_W))
    blend = {a: 0.7 * DEFAULT_W[a] + 0.3 * ewma_hr[a] for a in ALGO_KEYS}
    cands['B_ewma_unconstrained'] = normalize(blend)
    cands['C_cap_minor_0.10'] = cap_normalize(blend, 0.10)
    cands['D_cap_minor_0.05'] = cap_normalize(blend, 0.05)
    blend2 = {a: 0.85 * DEFAULT_W[a] + 0.15 * ewma_hr[a] for a in ALGO_KEYS}
    cands['E_alpha0.15_cap0.10'] = cap_normalize(blend2, 0.10)
    # G 静态默认主导 + EWMA 仅微调(混合系数 0.3→0.1) + 封顶 —— 真正"防过均匀且保Top-1"
    blend3 = {a: 0.9 * DEFAULT_W[a] + 0.1 * ewma_hr[a] for a in ALGO_KEYS}
    cands['G_blend0.1_cap0.10'] = cap_normalize(blend3, 0.10)
    cands['F_keep_core'] = normalize({
        'frequency_weighted': 0.50, 'omission_regression': 0.32,
        'bayesian_inference': 0.10, 'trend_momentum': 0.03,
        'markov_transition': 0.02, 'pattern_continuation': 0.015,
        'feature_engineering': 0.015})
    return cands


def verify_with_weights(history_data, start_index, test_count, weights, label):
    from modules.predictor import P5Predictor
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False
    p.config.config['global']['enable_adaptive_weights'] = False
    for name in ALGO_KEYS:
        p.config.config['algorithms'][name]['enabled'] = True
        p.config.config['algorithms'][name]['weight'] = weights[name]
    p.config.config['global']['enable_feature_engineering'] = True

    pos_hit6 = [0, 0, 0, 0, 0]
    pos_hit1 = [0, 0, 0, 0, 0]
    pos_hit3 = [0, 0, 0, 0, 0]
    pos_hit5 = [0, 0, 0, 0, 0]
    match_counts = []
    n = len(history_data)
    end = min(start_index + test_count, n)
    tested = 0
    t0 = time.time()
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
            real = actual[pos]
            if real in top6:
                pos_hit6[pos] += 1
                mc += 1
            if real in top3:
                pos_hit3[pos] += 1
            if real == sorted_nums[0][0]:
                pos_hit1[pos] += 1
            if real in [n for n, _ in sorted_nums[:5]]:
                pos_hit5[pos] += 1
        match_counts.append(mc)
        tested += 1
        if tested % 20 == 0:
            print(f"    [{label}] {tested}/{end-start_index}  ({(time.time()-t0):.0f}s)")
    mc_arr = np.array(match_counts)
    return {
        'label': label,
        'weights': {k: round(v, 4) for k, v in weights.items()},
        'tested': tested,
        'top1_overall': round(sum(pos_hit1) / tested / 5 * 100, 2),
        'top3_overall': round(sum(pos_hit3) / tested / 5 * 100, 2),
        'top5_overall': round(sum(pos_hit5) / tested / 5 * 100, 2),
        'top6_overall': round(sum(pos_hit6) / (tested * 5) * 100, 2),
        'avg_match_count': round(float(mc_arr.mean()), 3),
        'pos_top1': {POS_CN[i]: round(pos_hit1[i]/tested*100, 2) for i in range(5)},
        'pos_top6': {POS_CN[i]: round(pos_hit6[i]/tested*100, 2) for i in range(5)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=120)
    args = ap.parse_args()

    hist = load_history()
    n = len(hist)
    start = max(0, n - args.count)
    count = min(args.count, n - start)

    with open('reports/diagnostic/revive_loop.json', encoding='utf-8') as f:
        rl = json.load(f)
    ewma_hr = rl['ewma_after_replay']

    cands = build_candidates(ewma_hr)
    print(f"=== Plan B 实验：{len(cands)} 种权重配置 × 最近 {count} 期 ===")
    for k, w in cands.items():
        print(f"  {k}: " + ", ".join(f"{a[:4]}={w[a]:.3f}" for a in ALGO_KEYS))

    results = []
    t_all = time.time()
    for k, w in cands.items():
        print(f"\n--- 运行 {k} ---")
        r = verify_with_weights(hist, start, count, w, k)
        results.append(r)
        print(f"  Top-1={r['top1_overall']}%  Top-3={r['top3_overall']}%  "
              f"Top-5={r['top5_overall']}%  Top-6={r['top6_overall']}%  "
              f"mc={r['avg_match_count']}")

    print(f"\n=== 总耗时 {time.time()-t_all:.0f}s ===")

    os.makedirs('reports/diagnostic', exist_ok=True)
    out = {
        'start_index': start, 'tested': count,
        'random_base': RANDOM_BASE,
        'ewma_hr': ewma_hr,
        'candidates_weights': {k: v for k, v in cands.items()},
        'results': results,
    }
    with open('reports/diagnostic/plan_b.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已保存 reports/diagnostic/plan_b.json")

    # 选优（以 Plan B 真正目标为准：保住 Top-1 精准度，覆盖不塌）
    # 评分 = Top-1增益×3 − Top-5损失 − Top-6损失
    a = next(r for r in results if r['label'] == 'A_default')
    best = None
    for r in results:
        if r['label'] == 'A_default':
            continue
        t1_gain = r['top1_overall'] - a['top1_overall']
        t5_loss = a['top5_overall'] - r['top5_overall']
        t6_loss = a['top6_overall'] - r['top6_overall']
        score = t1_gain * 3 - t5_loss - t6_loss
        r['_score'] = round(score, 2)
        r['_t1_gain'] = round(t1_gain, 2)
        r['_t5_loss'] = round(t5_loss, 2)
        r['_t6_loss'] = round(t6_loss, 2)
        if best is None or score > best['_score']:
            best = r
    print("\n【候选评分（Top-1增益 - 2×Top-5损失 - 2×Top-6损失）】")
    for r in results:
        if r['label'] == 'A_default':
            continue
        print(f"  {r['label']}: score={r.get('_score')}  "
              f"ΔT1={r.get('_t1_gain')}  ΔT5={r.get('_t5_loss')}  ΔT6={r.get('_t6_loss')}")
    if best:
        print(f"\n★ 推荐配置: {best['label']}  "
              f"(Top-1={best['top1_overall']}%, Top-5={best['top5_overall']}%, "
              f"Top-6={best['top6_overall']}%)")
        print("   权重: " + ", ".join(f"{k[:4]}={v:.3f}" for k, v in best['weights'].items()))


if __name__ == '__main__':
    main()
