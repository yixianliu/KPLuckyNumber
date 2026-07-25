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
v3.12 生产口径验证（B 行动：补真实世界证据）
===========================================
用 v3.12 默认融合配置，对已开奖的历史期号做 walk-forward 预测（防前视偏差），
按【生产真实口径】计算命中率：
  - 每位置取融合概率 Top-6（= position_top_n=6，即 p5_prediction_record 实际入库口径）
  - 与真实开奖逐位置比对，算逐位置 Top-6 命中率 / 平均 match_count（满分 5）
  - 同时统计概率排名 Top-1/3/5（对比随机基线 10/30/50%）
对比对象：
  - 旧模型 992 条真实记录标准化 Top-3 ≈ 28%（来自 opt_diagnostic Part A）
  - 随机基线：Top-1=10% / Top-3=30% / Top-5=50% / Top-6=60%
不写库（避免污染 992 条珍贵记录），纯离线验证 + 导出 JSON。

用法:
  python opt_verify_v312.py --count 120        # 默认验证最近120期
  python opt_verify_v312.py --count 10 --start -10   # 小样本冒烟
"""
import sys, os, json, argparse, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

POS_CN = ['万位', '千位', '百位', '十位', '个位']
RANDOM_BASE = {'top1': 10.0, 'top3': 30.0, 'top5': 50.0, 'top6': 60.0}


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


def v312_production_verify(history_data, start_index, test_count):
    from modules.predictor import P5Predictor
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False  # 纯算法，关AI

    pos_hit6 = [0, 0, 0, 0, 0]      # 逐位置 Top-6 命中
    pos_hit1 = [0, 0, 0, 0, 0]      # 逐位置 Top-1 命中
    pos_hit3 = [0, 0, 0, 0, 0]      # 逐位置 Top-3 命中
    pos_hit5 = [0, 0, 0, 0, 0]      # 逐位置 Top-5 命中
    match_counts = []               # 每期 match_count（满分5）
    total_positions = 0

    # 生产口径记录（用于抽样展示）
    samples = []
    n = len(history_data)
    end = min(start_index + test_count, n)
    tested = 0
    t0 = time.time()
    for i in range(start_index, end):
        train = history_data[:i]
        issue = history_data[i]['issue']
        actual = history_data[i]['numbers']  # [w,q,b,s,g]
        res = p.predict(train, issue)
        fused = res.get('fused_probabilities', [])
        if not fused or len(fused) != 5:
            continue
        mc = 0
        pred_top6 = {}
        for pos in range(5):
            pos_probs = fused[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top6 = [n for n, _ in sorted_nums[:6]]
            top3 = [n for n, _ in sorted_nums[:3]]
            top1 = sorted_nums[0][0]
            pred_top6[POS_CN[pos]] = top6
            real = actual[pos]
            total_positions += 1
            if real in top6:
                pos_hit6[pos] += 1
                mc += 1
            if real in top3:
                pos_hit3[pos] += 1
            if real == top1:
                pos_hit1[pos] += 1
            if real in [n for n, _ in sorted_nums[:5]]:
                pos_hit5[pos] += 1
        match_counts.append(mc)
        tested += 1
        if tested <= 3:
            samples.append({'issue': issue, 'actual': actual,
                            'pred_top6': pred_top6, 'match_count': mc})
        # 进度
        if tested % 20 == 0:
            print(f"  进度 {tested}/{end-start_index}  ({(time.time()-t0):.0f}s)")

    mc_arr = np.array(match_counts)
    out = {
        'config': 'v3.12 融合（默认）',
        'start_index': start_index,
        'tested': tested,
        'total_positions_evaluated': total_positions,
        'random_base': RANDOM_BASE,
        'old_model_real_top3': 28.0,  # 来自 opt_diagnostic Part A 标准化后均值
        # 生产口径 Top-6
        'pos_top6_rate': {POS_CN[i]: round(pos_hit6[i]/tested*100, 2) for i in range(5)},
        'avg_top6_match_count': round(float(mc_arr.mean()), 3),  # 满分5
        'top6_overall_rate': round(total_positions_top6(pos_hit6, tested), 2),
        # 概率排名口径
        'pos_top1_rate': {POS_CN[i]: round(pos_hit1[i]/tested*100, 2) for i in range(5)},
        'pos_top3_rate': {POS_CN[i]: round(pos_hit3[i]/tested*100, 2) for i in range(5)},
        'pos_top5_rate': {POS_CN[i]: round(pos_hit5[i]/tested*100, 2) for i in range(5)},
        'top1_overall': round(sum(pos_hit1)/tested/5*100, 2),
        'top3_overall': round(sum(pos_hit3)/tested/5*100, 2),
        'top5_overall': round(sum(pos_hit5)/tested/5*100, 2),
        'match_count_dist': {str(k): int((mc_arr == k).sum()) for k in range(6)},
        'samples': samples,
        'elapsed_s': round(time.time() - t0, 1),
    }
    return out


def total_positions_top6(pos_hit6, tested):
    return sum(pos_hit6) / (tested * 5) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=120)
    ap.add_argument('--tail', type=int, default=0,
                    help='若>0，验证最近 tail 期（覆盖 start 自动计算）')
    args = ap.parse_args()

    hist = load_history()
    n = len(hist)
    if args.tail > 0:
        start = n - args.tail
    else:
        start = n - args.count
    start = max(0, start)
    count = min(args.count, n - start)

    print(f"=== v3.12 生产口径验证（最近 {count} 期，start={start}）===")
    print(f"历史总数={n}, 关AI纯算法, walk-forward 防前视偏差")
    out = v312_production_verify(hist, start, count)

    print("\n【生产真实口径 · 每位置 Top-6 命中率】（随机基线=60%）")
    for i in range(5):
        r = out['pos_top6_rate'][POS_CN[i]]
        flag = '✓超基线' if r > RANDOM_BASE['top6'] else '✗低于基线'
        print(f"  {POS_CN[i]}: {r:5.2f}%  {flag}")
    print(f"\n平均 match_count(Top-6口径): {out['avg_top6_match_count']}/5 "
          f"(随机期望=3.00)")
    print(f"整体 Top-6 命中率: {out['top6_overall_rate']}%  (随机基线=60%)")
    print(f"\n【概率排名口径 vs 随机基线】")
    print(f"  Top-1: {out['top1_overall']}%  (基线10%)  "
          f"{'✓' if out['top1_overall']>10 else '✗'}")
    print(f"  Top-3: {out['top3_overall']}%  (基线30%)  "
          f"{'✓' if out['top3_overall']>30 else '✗'}")
    print(f"  Top-5: {out['top5_overall']}%  (基线50%)  "
          f"{'✓' if out['top5_overall']>50 else '✗'}")
    print(f"\n对比旧模型(992条真实记录标准化Top-3): {out['old_model_real_top3']}%")
    print(f"耗时: {out['elapsed_s']}s")

    os.makedirs('reports/diagnostic', exist_ok=True)
    with open('reports/diagnostic/v312_production.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已保存 reports/diagnostic/v312_production.json")


if __name__ == '__main__':
    main()
