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
排列5 深度诊断工具（数据诊断先行）
=================================
Part A: 基于 992 条已验证预测记录，做「真实世界」性能诊断
  - 逐位置 Top-3 命中率（对比 30% 随机基线）
  - match_count 分布 / 平均命中率
  - 置信度校准（高置信组合是否更可能命中）
Part B: 单算法隔离 walk-forward 回测（复用 P5Predictor 真实逻辑）
  - 将某算法权重设 1、其余设 0，测其独立信号质量
  - 对比 v3.12 融合结果与随机基线，量化各算法真实贡献

用法:
  python opt_diagnostic.py --mode db
  python opt_diagnostic.py --mode algo --algo frequency_weighted --start 400 --count 120
  python opt_diagnostic.py --mode all --start 400 --count 120
"""
import sys, os, json, argparse, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql
import numpy as np

DB = dict(host='localhost', port=3306, user='root', password='root',
          database='lucky_number', charset='utf8mb4')
POS = ['wan', 'qian', 'bai', 'shi', 'ge']
POS_CN = ['万位', '千位', '百位', '十位', '个位']
ALGO_KEYS = ['frequency_weighted', 'omission_regression', 'bayesian_inference',
             'trend_momentum', 'markov_transition', 'pattern_continuation',
             'feature_engineering']
ALGO_CN = {'frequency_weighted': '频率加权', 'omission_regression': '遗漏回归',
           'bayesian_inference': '贝叶斯推断', 'trend_momentum': '趋势动量',
           'markov_transition': '马尔可夫', 'pattern_continuation': '形态延续',
           'feature_engineering': '特征工程'}


def db_conn():
    return pymysql.connect(**DB, cursorclass=pymysql.cursors.DictCursor)


# ============================ Part A: 真实记录诊断 ============================
def db_diagnostic():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT target_issue, predicted_numbers, actual_numbers, "
                "match_count, accuracy_rate, confidence_scores "
                "FROM p5_prediction_record WHERE verification_status='verified'")
    rows = cur.fetchall()
    conn.close()

    n = len(rows)
    pos_hit3 = [0, 0, 0, 0, 0]
    pos_hit5 = [0, 0, 0, 0, 0]
    match_counts3 = []
    topn_dist = {}
    mc_agree = 0
    conf = []  # (max_conf, top3_match_count)
    empty = 0
    for r in rows:
        pred = json.loads(r['predicted_numbers'])  # {wan:[..k], ...}
        actual = json.loads(r['actual_numbers'])   # [w,q,b,s,g]
        # Top-N 口径分布（数据质量）
        try:
            topn_dist[len(pred['wan'])] = topn_dist.get(len(pred['wan']), 0) + 1
        except Exception:
            pass
        # 标准化到 Top-3 / Top-5
        try:
            top3 = [pred[p][:3] for p in POS]
            top5 = [pred[p][:5] for p in POS]
        except Exception:
            empty += 1
            continue
        mc3 = 0
        for i in range(5):
            if actual[i] in top3[i]:
                pos_hit3[i] += 1
                mc3 += 1
            if actual[i] in top5[i]:
                pos_hit5[i] += 1
        # 与库存 match_count 交叉校验（库存按验证当时 Top-N 计算，多数应为 Top-3）
        if mc3 == (r['match_count'] or 0):
            mc_agree += 1
        match_counts3.append(mc3)
        try:
            cs = json.loads(r['confidence_scores']) if r['confidence_scores'] else []
            cs = [float(x) for x in cs if x is not None]
            if cs:
                conf.append((max(cs), mc3))
        except Exception:
            pass

    mc_arr = np.array(match_counts3)
    print(f"\n=== Part A: 真实世界诊断（{n} 条已验证记录，空记录 {empty}）===")
    print(f"⚠️ 数据质量: predicted_numbers 每位置 Top-N 随版本漂移:")
    for k in sorted(topn_dist):
        print(f"    Top-{k}: {topn_dist[k]:4d} 条 ({topn_dist[k]/n*100:4.1f}%)")
    print(f"  标准化到 Top-3 后，自算 match_count 与库存一致率: {mc_agree}/{n} "
          f"({mc_agree/n*100:.1f}%)  ← 低说明版本间口径不一")
    print("\n【逐位置 真实 Top-3 命中率】（随机基线=30%）")
    for i in range(5):
        rate = pos_hit3[i] / n * 100
        bar = '█' * int(rate / 3)
        flag = '✓超基线' if rate > 30 else '✗低于基线'
        print(f"  {POS_CN[i]}: {rate:5.2f}%  {bar}  {flag}")
    print("\n【逐位置 真实 Top-5 命中率】（随机基线=50%，仅作版本漂移对照）")
    for i in range(5):
        rate = pos_hit5[i] / n * 100
        print(f"  {POS_CN[i]}: {rate:5.2f}%")
    print(f"\n平均命中位数(Top-3): {mc_arr.mean():.3f} / 5  (随机期望=1.50)")
    print(f"平均命中率 accuracy_rate(库存): "
          f"{np.mean([r['accuracy_rate'] or 0 for r in rows]):.2f}%")
    print("\nmatch_count(Top-3) 分布 (命中k位 / 记录数 / 占比):")
    dist = {}
    for k in range(6):
        c = int((mc_arr == k).sum())
        dist[k] = c
        print(f"  k={k}: {c:4d}  ({c/n*100:5.1f}%)")
    if conf:
        conf = np.array(conf, dtype=float)
        print("\n【置信度校准】（最高置信组合 vs 命中位）")
        edges = [0, 60, 70, 80, 90, 101]
        for a, b in zip(edges[:-1], edges[1:]):
            m = (conf[:, 0] >= a) & (conf[:, 0] < b)
            if m.sum() > 0:
                print(f"  置信[{a},{b}): n={int(m.sum()):4d}  平均命中位={conf[m,1].mean():.3f}")
        if conf.shape[0] > 2:
            corr = np.corrcoef(conf[:, 0], conf[:, 1])[0, 1]
            print(f"  置信度 vs 命中位 相关系数 r = {corr:.3f}")
        else:
            corr = None
    else:
        corr = None

    return {
        'n': n, 'empty': empty, 'mc_agree': mc_agree,
        'topn_dist': topn_dist,
        'pos_top3_rate': {POS_CN[i]: round(pos_hit3[i]/n*100, 2) for i in range(5)},
        'pos_top5_rate': {POS_CN[i]: round(pos_hit5[i]/n*100, 2) for i in range(5)},
        'avg_match_count': round(float(mc_arr.mean()), 3),
        'avg_accuracy': round(float(np.mean([r['accuracy_rate'] or 0 for r in rows])), 2),
        'match_dist': dist,
        'conf_corr': round(float(corr), 3) if corr is not None else None,
    }


# ===================== Part B: 单算法隔离 walk-forward =====================
def isolated_backtest(algo, start_index=400, test_count=120):
    from modules.predictor import P5Predictor
    from modules.backtester import Backtester

    p = P5Predictor()
    p.config.config['global']['enable_adaptive_weights'] = False
    p.config.config['global']['enable_ai_model'] = False
    for name in ALGO_KEYS:
        p.config.config['algorithms'][name]['enabled'] = (name == algo)
        p.config.config['algorithms'][name]['weight'] = 1.0 if name == algo else 0.0
    if algo == 'feature_engineering':
        p.config.config['global']['enable_feature_engineering'] = True

    bt = Backtester(p)
    res = bt.run_backtest(start_index=start_index, test_count=test_count)
    if res.get('status') != 'success':
        return {'algo': algo, 'error': res.get('message')}
    s = res['overall_stats']
    out = {
        'algo': algo,
        'cn': ALGO_CN[algo],
        'total': s.get('total_tested'),
        'top1': s.get('avg_top1_hit_rate'),
        'top3': s.get('avg_top3_hit_rate'),
        'top5': s.get('avg_top5_hit_rate'),
        'score': s.get('avg_overall_score'),
        'calibration': s.get('avg_calibration_score'),
        'pos_top3': s.get('position_top3_rates'),
    }
    return out


def run_all(start_index, test_count):
    results = []
    # v3.12 融合（默认）
    from modules.predictor import P5Predictor
    from modules.backtester import Backtester
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False  # 关闭AI以纯算法对比
    bt = Backtester(p)
    res = bt.run_backtest(start_index=start_index, test_count=test_count)
    if res.get('status') == 'success':
        s = res['overall_stats']
        results.append({'algo': 'fused_v312', 'cn': 'v3.12融合',
                        'total': s.get('total_tested'),
                        'top1': s.get('avg_top1_hit_rate'),
                        'top3': s.get('avg_top3_hit_rate'),
                        'top5': s.get('avg_top5_hit_rate'),
                        'score': s.get('avg_overall_score'),
                        'calibration': s.get('avg_calibration_score'),
                        'pos_top3': s.get('position_top3_rates')})
    for algo in ALGO_KEYS:
        t0 = time.time()
        r = isolated_backtest(algo, start_index, test_count)
        r['elapsed_s'] = round(time.time() - t0, 1)
        results.append(r)
        print(f"  {ALGO_CN[algo]:8s}: T1={r.get('top1')} T3={r.get('top3')} "
              f"score={r.get('score')}  ({r['elapsed_s']}s)")
    return results


def robustness_sweep():
    """融合 v3.12 跨多个不重叠窗口的稳健性扫描（纯算法, 关AI）"""
    from modules.predictor import P5Predictor
    from modules.backtester import Backtester
    windows = [(50, 150), (200, 150), (400, 150), (650, 150), (850, 150)]
    out = []
    for start, count in windows:
        p = P5Predictor()
        p.config.config['global']['enable_ai_model'] = False
        bt = Backtester(p)
        res = bt.run_backtest(start_index=start, test_count=count)
        if res.get('status') != 'success':
            out.append({'window': f'{start}+{count}', 'error': res.get('message')})
            continue
        s = res['overall_stats']
        out.append({
            'window': f'{start}+{count}',
            'total': s.get('total_tested'),
            'top1': s.get('avg_top1_hit_rate'),
            'top3': s.get('avg_top3_hit_rate'),
            'top5': s.get('avg_top5_hit_rate'),
            'score': s.get('avg_overall_score'),
            'trend': s.get('trend_direction'),
        })
        print(f"  窗口 {start}+{count}: T1={out[-1]['top1']} T3={out[-1]['top3']} "
              f"T5={out[-1]['top5']} score={out[-1]['score']} trend={out[-1]['trend']}")
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['db', 'algo', 'all', 'sweep'], default='db')
    ap.add_argument('--algo', default='frequency_weighted')
    ap.add_argument('--start', type=int, default=400)
    ap.add_argument('--count', type=int, default=120)
    args = ap.parse_args()

    if args.mode == 'db':
        res = db_diagnostic()
        os.makedirs('reports/diagnostic', exist_ok=True)
        with open('reports/diagnostic/partA.json', 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print("\n已保存 reports/diagnostic/partA.json")
    elif args.mode == 'algo':
        r = isolated_backtest(args.algo, args.start, args.count)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.mode == 'all':
        out = run_all(args.start, args.count)
        os.makedirs('reports/diagnostic', exist_ok=True)
        with open('reports/diagnostic/algo_isolation.json', 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("\n已保存 reports/diagnostic/algo_isolation.json")
    elif args.mode == 'sweep':
        out = robustness_sweep()
        os.makedirs('reports/diagnostic', exist_ok=True)
        with open('reports/diagnostic/robustness_sweep.json', 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("\n已保存 reports/diagnostic/robustness_sweep.json")
