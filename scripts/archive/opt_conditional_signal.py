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
P2 条件信号探索 (walk-forward, 防前视)
问题: 是否存在"上一期形态"条件, 使频率模型 Top-1 稳定超越 10% 随机基线?
方法: 对每期 i, 用 [i-60, i) 窗口频率预测; 条件定义在 i-1 期(预测时已知)。
"""
import json
import math
import pymysql
from collections import defaultdict

DB = dict(host="localhost", port=3306, user="root", password="root",
          database="lucky_number", charset="utf8mb4")
POS = ["wan", "qian", "bai", "shi", "ge"]
WINDOW = 60
BASELINE_T1 = 0.10


def binom_p(n, k, p0):
    """双侧二项检验 p 值 (近似), 避免 scipy 依赖。"""
    if n == 0:
        return 1.0
    # 用正态近似(大样本); n 小时用精确累计
    if n >= 30:
        se = math.sqrt(n * p0 * (1 - p0))
        if se == 0:
            return 1.0
        z = abs(k - n * p0) / se
        # 标准正态尾概率近似
        return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    # 小样本精确
    from math import comb
    p = 0.0
    for x in range(n + 1):
        px = comb(n, x) * (p0 ** x) * ((1 - p0) ** (n - x))
        if x <= k or x >= n - k:
            p += px
    return min(1.0, p)


def main():
    conn = pymysql.connect(**DB)
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute("SELECT issue, wan, qian, bai, shi, ge, hezhi, span, "
                    "odd_even_pattern FROM p5_history_data ORDER BY issue ASC")
        rows = cur.fetchall()
    finally:
        conn.close()

    # 全局 hezhi 分位 (hot/cold)
    hezhis = [r["hezhi"] for r in rows if r["hezhi"] is not None]
    hezhis.sort()
    p10 = hezhis[int(0.10 * len(hezhis))]
    p90 = hezhis[int(0.90 * len(hezhis))]

    def conditions(prev):
        d = [prev["wan"], prev["qian"], prev["bai"], prev["shi"], prev["ge"]]
        par = [x % 2 for x in d]
        all_even = all(x == 0 for x in par)
        all_odd = all(x == 1 for x in par)
        has_repeat = len(set(d)) < 5
        alt = par in ([0, 1, 0, 1, 0], [1, 0, 1, 0, 1])
        span = prev["span"] if prev["span"] is not None else 9
        hz = prev["hezhi"] if prev["hezhi"] is not None else 20
        return {
            "all_same_parity": all_even or all_odd,
            "has_repeat": has_repeat,
            "alt_parity": alt,
            "wide_span": span >= 8,
            "narrow_span": span <= 2,
            "hot_sum": hz >= p90,
            "cold_sum": hz <= p10,
        }

    # 聚合器: 每条件记录 top1 命中与 top5 覆盖 (per-position 统计)
    agg = defaultdict(lambda: {"N": 0, "t1": 0, "t5": 0})
    overall = {"N": 0, "t1": 0, "t5": 0}

    n = len(rows)
    for i in range(WINDOW, n):
        window = rows[i - WINDOW:i]
        freq = {p: defaultdict(int) for p in POS}
        for w in window:
            for p in POS:
                freq[p][w[p]] += 1
        top1 = {p: max(freq[p], key=lambda k: freq[p][k]) for p in POS}
        top5 = {p: [k for k, _ in sorted(freq[p].items(), key=lambda kv: -kv[1])[:5]]
                for p in POS}
        cur_draw = rows[i]
        cond = conditions(rows[i - 1])
        for p in POS:
            overall["N"] += 1
            if top1[p] == cur_draw[p]:
                overall["t1"] += 1
            if cur_draw[p] in top5[p]:
                overall["t5"] += 1
            for cname, flag in cond.items():
                if flag:
                    agg[cname]["N"] += 1
                    if top1[p] == cur_draw[p]:
                        agg[cname]["t1"] += 1
                    if cur_draw[p] in top5[p]:
                        agg[cname]["t5"] += 1

    # ---- 输出 ----
    print("=" * 72)
    print("P2 条件信号探索 | 频率模型窗口=%d | 基线 Top-1=10%% / Top-5=50%%" % WINDOW)
    print("=" * 72)
    ov_t1 = overall["t1"] / overall["N"]
    ov_t5 = overall["t5"] / overall["N"]
    print("\n[整体] Top-1 位命中率=%.3f (N=%d位) | Top-5 位覆盖率=%.3f"
          % (ov_t1, overall["N"], ov_t5))

    print("\n[条件]  name | N(位) | Top-1率 | Δvs基线 | p值 | Top-5率")
    results = []
    for cname in sorted(agg.keys()):
        a = agg[cname]
        if a["N"] == 0:
            continue
        r1 = a["t1"] / a["N"]
        r5 = a["t5"] / a["N"]
        pval = binom_p(a["N"], a["t1"], BASELINE_T1)
        sig = "★显著" if (pval < 0.05 and a["N"] >= 200) else ""
        print("  %-16s | %6d | %.3f | %+.3f | %.4f | %.3f  %s"
              % (cname, a["N"], r1, r1 - BASELINE_T1, pval, r5, sig))
        results.append({"condition": cname, "N": a["N"], "top1_rate": round(r1, 4),
                        "delta": round(r1 - BASELINE_T1, 4), "p_value": round(pval, 4),
                        "top5_rate": round(r5, 4)})

    sig_any = [r for r in results if r["p_value"] < 0.05 and r["N"] >= 200]
    print("\n[结论] 达到 p<0.05 且样本>=200 的显著条件: %d 个"
          % len(sig_any))
    if not sig_any:
        print("  → 无任何条件使频率预测稳定超越随机基线, 与 v3.14 审计(公平摇号)一致。")
    print("  (注: 多条件并行比较, 个别小幅偏离多为抽样噪声, 需独立样本复验)")

    out = {"window": WINDOW, "baseline_t1": BASELINE_T1,
           "overall_top1": round(ov_t1, 4), "overall_top5": round(ov_t5, 4),
           "n_positions": overall["N"], "conditions": results}
    with open("reports/diagnostic/conditional_signal.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n[交付] reports/diagnostic/conditional_signal.json")


if __name__ == "__main__":
    main()
