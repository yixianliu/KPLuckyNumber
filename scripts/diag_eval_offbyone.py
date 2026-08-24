# -*- coding: utf-8 -*-
"""
诊断：self_evolution._evaluate 的走窗评估是否存在 off-by-one（前视泄漏）。

复现三种口径，跑同一组真实历史，对比 Top1/3/5：
  A) 当前代码（window=rows[:idx+1]，预测 idx+1，却与 idx 比对）—— 复现 63%
  B) 修正走窗（window=rows[:idx]，严格只用 idx 之前数据，预测并与 idx 比对）
  C) 随机基线（每位置固定取出现频率最高的数字，作为无信息下界参考）

运行：/d/anaconda3/python.exe scripts/diag_eval_offbyone.py
"""
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from modules.self_evolution import _connect_db, _row_to_sorted, ML_EVAL_MIN, POS
from modules import ml_predictor

POS = ['wan', 'qian', 'bai', 'shi', 'ge']


def load_rows(limit=400):
    conn = _connect_db()
    if conn is None:
        print("DB 不可用，无法诊断")
        sys.exit(2)
    try:
        cur = conn.cursor()
        cur.execute('SELECT issue, wan, qian, bai, shi, ge FROM '
                    'p5_history_data ORDER BY issue DESC LIMIT %s', (limit,))
        rows = cur.fetchall() or []
    finally:
        conn.close()
    return list(reversed(rows))


def eval_mode(rows, kind, periods):
    """kind='buggy' -> window=rows[:idx+1] 比对 idx；kind='fixed' -> window=rows[:idx] 比对 idx"""
    import traceback
    if len(rows) < ML_EVAL_MIN:
        print(f"  数据不足 {ML_EVAL_MIN}")
        return None
    train_min = ML_EVAL_MIN
    n_periods = min(periods, max(0, len(rows) - train_min))
    hit1 = hit3 = hit5 = 0
    total = 0
    for k, idx in enumerate(range(train_min, train_min + n_periods)):
        end = idx + 1 if kind == 'buggy' else idx
        window = _row_to_sorted(rows[:end])
        target_row = rows[idx]
        try:
            d = ml_predictor.predict_next(window, target_issue=str(target_row['issue']))
        except Exception as e:
            print(f"  [idx={idx}] predict_next 异常: {e}")
            traceback.print_exc()
            continue
        if not d:
            print(f"  [idx={idx}] predict_next 返回 None (跳过)")
            continue
        total += 1
        for i, p in enumerate(POS):
            actual = int(target_row[p])
            ranked = sorted(d[i].items(), key=lambda x: -x[1])
            if any(t[0] == actual for t in ranked[:1]):
                hit1 += 1
            if any(t[0] == actual for t in ranked[:3]):
                hit3 += 1
            if any(t[0] == actual for t in ranked[:5]):
                hit5 += 1
        if (k + 1) % 5 == 0:
            print(f"  [{kind}] 进度 {k+1}/{n_periods}, 当前累计 top1={round(hit1/max(1,total*5)*100,2)}%")
    denom = max(1, total * 5)
    return {
        'top1': round(hit1 / denom * 100, 2),
        'top3': round(hit3 / denom * 100, 2),
        'top5': round(hit5 / denom * 100, 2),
        'n': total,
    }


def random_baseline(rows, periods):
    """每位置取频率最高数字，作为无信息预测，比对 idx（fixed 口径同窗）。"""
    from collections import Counter
    counts = {p: Counter() for p in POS}
    for r in rows:
        for p in POS:
            try:
                counts[p][int(r[p])] += 1
            except Exception:
                pass
    mode_digit = {p: counts[p].most_common(1)[0][0] if counts[p] else 0 for p in POS}
    train_min = ML_EVAL_MIN
    n_periods = min(periods, max(0, len(rows) - train_min))
    hit1 = hit3 = hit5 = 0
    total = 0
    for idx in range(train_min, train_min + n_periods):
        total += 1
        for p in POS:
            actual = int(rows[idx][p])
            if mode_digit[p] == actual:
                hit1 += 1
            # top3/top5 随机基线仅 top1 有意义（单点预测）
    denom = max(1, total * 5)
    return {'top1': round(hit1 / denom * 100, 2), 'top3': None, 'top5': None, 'n': total}


def main():
    import traceback
    # ml_predictor 已改为纯 numpy 实现，始终可用
    try:
        rows = load_rows(400)
    except Exception as e:
        print("load_rows 异常:", e)
        traceback.print_exc()
        sys.exit(2)
    print(f"载入历史行数: {len(rows)} (ML_EVAL_MIN={ML_EVAL_MIN})")
    periods = 12
    print("--- A) 当前代码 buggy 口径 (window 含 idx, 预测 idx+1, 比对 idx) ---")
    a = eval_mode(rows, 'buggy', periods)
    print("  ", a)
    print("--- B) 修正口径 (window 严格 < idx, 预测并比对 idx) ---")
    b = eval_mode(rows, 'fixed', periods)
    print("  ", b)
    print("--- C) 随机基线 (每位置取众数, 比对 idx) ---")
    c = random_baseline(rows, periods)
    print("  ", c)
    print("\n解读：")
    print("  若 B 接近随机基线(~10/30/50) 且远低于 A，则证明 A 存在 off-by-one 前视污染；")
    print("  正确 OOS 评估应使用 B，任何诚实策略都应落回随机基线附近。")


if __name__ == '__main__':
    main()
