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
opt_window_study.py — 训练/预测历史窗口寻优 + 命中率审计
(2026-07-19, v3.14 审计任务)

目标(用户授权: 自行决定最优历史期数):
  用 walk-forward 回测测试多个训练窗口 N(截断传入 predict 的历史长度),
  对比 Top-1/3/5/6 命中率与平均 match_count, 确定频率主信号的最优历史窗口。

设计:
  - 频率算法 lookback_periods=None => 实际训练期数 = 传入历史长度。
    故对每期 i 截断 train = history[max(0,i-N):i] 即实现"固定窗口 N"。
  - 其他算法(趋势30/形态7/贝叶斯60)用各自内部窗口, 不随 N 变, 保证对照干净。
  - AI 关闭(纯统计模型), 与历史回测口径一致。
  - 评估窗口取最近 EVAL 期(默认120), 避免小样本噪声。
  - 同时输出每个窗口在评估窗内的 std(稳定性)。

输出:
  reports/diagnostic/window_study.json
  stdout: 窗口对比表
"""
import json
import sys
import time
import pymysql
import numpy as np

sys.path.insert(0, '.')
from modules.predictor import P5Predictor


def load_history(limit=1010):
    conn = pymysql.connect(host='localhost', port=3306, user='root', password='root',
                           database='lucky_number', charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute('SELECT issue, wan, qian, bai, shi, ge FROM p5_history_data ORDER BY issue ASC LIMIT %s', (limit,))
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        r['numbers'] = [r['wan'], r['qian'], r['bai'], r['shi'], r['ge']]
    return rows  # oldest -> newest


def eval_window(history, window, eval_start, eval_end, ai_off=True):
    """walk-forward 在 [eval_start, eval_end) 评估固定窗口 window。"""
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False
    if not ai_off:
        pass
    t1 = t3 = t5 = t6 = mc_sum = 0
    per_period_mc = []
    tested = 0
    for i in range(eval_start, eval_end):
        lo = max(0, i - window)
        train = history[lo:i]
        actual = history[i]['numbers']
        try:
            res = p.predict(train, history[i]['issue'])
        except Exception:
            continue
        fp = res.get('fused_probabilities', [])
        if not fp or len(fp) != 5:
            continue
        tested += 1
        mc = 0
        for pos in range(5):
            sn = sorted(fp[pos].items(), key=lambda x: -x[1])
            nums = [num for num, _ in sn]
            if sn[0][0] == actual[pos]:
                mc += 1
            if actual[pos] in nums[:3]:
                t3 += 1
            if actual[pos] in nums[:5]:
                t5 += 1
            if actual[pos] in nums[:6]:
                t6 += 1
        mc_sum += mc
        per_period_mc.append(mc)
    if tested == 0:
        return None
    mc_arr = np.array(per_period_mc)
    return {
        'window': window,
        'tested': tested,
        'top1': round(mc_sum / tested / 5 * 100, 2),
        'top3': round(t3 / (tested * 5) * 100, 2),
        'top5': round(t5 / (tested * 5) * 100, 2),
        'top6': round(t6 / (tested * 5) * 100, 2),
        'avg_mc': round(mc_sum / tested, 3),
        'mc_std': round(float(mc_arr.std()), 3),
    }


def main():
    EVAL = 120          # 评估窗口期数
    history = load_history()
    total = len(history)
    eval_start = max(0, total - EVAL)
    eval_end = total
    print(f'总历史 {total} 期, 评估窗口 = 最近 {EVAL} 期 (idx {eval_start}..{eval_end-1})')
    print(f'随机基线: T1=10% T3=30% T5=50% T6=60% mc=3.0\n')

    windows = [30, 40, 50, 60, 80, 100, 120, 150, 200, 9999]
    results = []
    t0 = time.time()
    for w in windows:
        wlabel = 'ALL' if w >= 9999 else w
        r = eval_window(history, w, eval_start, eval_end)
        if r:
            results.append(r)
            print(f'窗口={str(wlabel):>4}: T1={r["top1"]:>5}% T3={r["top3"]:>5}% T5={r["top5"]:>5}% '
                  f'T6={r["top6"]:>5}% mc={r["avg_mc"]:.3f} std={r["mc_std"]:.3f} (n={r["tested"]})')
        else:
            print(f'窗口={str(wlabel):>4}: NO DATA')
    print(f'\n耗时 {time.time()-t0:.1f}s')

    # 最优窗口: 以 Top-1 为主, 兼顾 T6 与稳定性
    best_t1 = max(results, key=lambda x: x['top1'])
    best_mc = max(results, key=lambda x: x['avg_mc'])
    print(f'\nTop-1 最优窗口: {best_t1["window"] if best_t1["window"]<9999 else "ALL"} '
          f'(T1={best_t1["top1"]}%, mc={best_t1["avg_mc"]})')
    print(f'avg_mc 最优窗口: {best_mc["window"] if best_mc["window"]<9999 else "ALL"} '
          f'(mc={best_mc["avg_mc"]}, T1={best_mc["top1"]}%)')

    with open('reports/diagnostic/window_study.json', 'w', encoding='utf-8') as f:
        json.dump({
            'eval_window': EVAL,
            'total_history': total,
            'eval_start': eval_start,
            'eval_end': eval_end,
            'random_baseline': {'top1': 10, 'top3': 30, 'top5': 50, 'top6': 60, 'avg_mc': 3.0},
            'results': results,
            'best_by_top1': best_t1,
            'best_by_mc': best_mc,
        }, f, ensure_ascii=False, indent=2)
    print('\n已保存 reports/diagnostic/window_study.json')


if __name__ == '__main__':
    main()
