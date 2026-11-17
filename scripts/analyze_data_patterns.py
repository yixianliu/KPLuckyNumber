# -*- coding: utf-8 -*-
"""排列5 历史数据规律深度分析脚本

用途：
    对 p5_history_data 全量有效数据做统计特征挖掘，检验各位置数字是否偏离
    均匀随机分布，为「诚实边界」下的算法优化提供数据依据。

分析内容：
    1. 各位置 0-9 频次与卡方均匀性检验
    2. 各位置 lag-1..5 自相关（数字层面）
    3. 各位置一阶马尔可夫转移矩阵独立性检验（卡方）
    4. 和值/跨度/奇偶/大小分布特征
    5. 遗漏（间隔）分布与几何分布期望对照（期望平均间隔=10）
    6. 游程检验（中位数分割，检验序列独立性）

诚实边界声明：
    排列5每期五位置独立公平摇号。本脚本所有检验均为「历史数据的量化描述」，
    显著性检验采用 α=0.05 并做 Bonferroni 多重比较校正；即便个别指标通过
    校正后仍显著，也不能外推为对未来开奖的预测力。

输出：
    docs/data_pattern_analysis_<date>.md（报告）
    scripts/data_pattern_stats_<date>.json（原始统计量）
"""

import json
import os
import sys
from collections import Counter
from datetime import date

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_CONFIG  # noqa: E402

POSITIONS = ['wan', 'qian', 'bai', 'shi', 'ge']
POSITION_NAMES = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}

# Bonferroni 校正后的显著性阈值：25 个位置级检验 + 6 个全局检验 + 1 个和值拟合
N_TESTS = 32
ALPHA = 0.05
ALPHA_BONF = ALPHA / N_TESTS


def chi2_fit(observed, expected):
    """卡方拟合优度检验（给定期望值）。返回 (chi2, p)。"""
    observed = np.asarray(observed, dtype=float)
    expected = np.asarray(expected, dtype=float)
    df = max(len(observed) - 1, 1)
    chi2 = float(((observed - expected) ** 2 / expected).sum())
    try:
        from scipy import stats
        p = float(stats.chi2.sf(chi2, df))
    except ImportError:
        from math import erf, sqrt
        z = np.sqrt(9.0 * df / 2.0) * ((chi2 / df) ** (1.0 / 3.0) - 1 + 2.0 / (9.0 * df))
        p = 1.0 - 0.5 * (1.0 + erf(z / sqrt(2.0)))
    return chi2, p


def load_data():
    """从 MySQL 读取全量有效开奖数据，按期号数值升序返回。"""
    import pymysql
    conn = pymysql.connect(
        host=DB_CONFIG['host'], port=DB_CONFIG['port'], user=DB_CONFIG['user'],
        password=DB_CONFIG['password'], database=DB_CONFIG['database'],
        charset=DB_CONFIG.get('charset', 'utf8mb4'),
    )
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT issue, draw_date, wan, qian, bai, shi, ge, hezhi, span, "
            "odd_even_ratio, big_small_ratio FROM p5_history_data "
            "WHERE is_valid=1 ORDER BY CAST(issue AS UNSIGNED)"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    cols = ['issue', 'draw_date', 'wan', 'qian', 'bai', 'shi', 'ge',
            'hezhi', 'span', 'odd_even_ratio', 'big_small_ratio']
    data = [dict(zip(cols, r)) for r in rows]
    print(f"加载数据: {len(data)} 期 "
          f"({data[0]['issue']} ~ {data[-1]['issue']})")
    return data


def chi2_uniform(counts):
    """卡方均匀性检验（k 类，期望均等）。返回 (chi2, p)。"""
    arr = np.asarray(counts, dtype=float)
    n = arr.sum()
    expected = n / len(arr)
    chi2 = float(((arr - expected) ** 2 / expected).sum())
    k = len(arr) - 1  # 自由度
    try:
        from scipy import stats
        p = float(stats.chi2.sf(chi2, k))
    except ImportError:
        # 无 scipy 时用 Wilson-Hilferty 近似
        z = np.sqrt(9.0 * k / 2.0) * ((chi2 / k) ** (1.0 / 3.0) - 1 + 2.0 / (9.0 * k))
        from math import erf, sqrt
        p = 1.0 - 0.5 * (1.0 + erf(z / sqrt(2.0)))
    return chi2, p


def lag_autocorr(series, max_lag=5):
    """离散序列 lag-k 自相关系数（皮尔逊，数字值层面）。"""
    out = {}
    x = np.asarray(series, dtype=float)
    x = x - x.mean()
    denom = float((x * x).sum())
    for k in range(1, max_lag + 1):
        num = float((x[:-k] * x[k:]).sum())
        out[k] = num / denom if denom > 0 else 0.0
    return out


def markov_independence(series):
    """一阶马尔可夫转移矩阵独立性卡方检验。

    H0: 下一期数字与上一期数字独立（10x10 列联表）。
    """
    x = np.asarray(series, dtype=int)
    table = np.zeros((10, 10))
    for a, b in zip(x[:-1], x[1:]):
        table[a, b] += 1
    row = table.sum(axis=1, keepdims=True)
    col = table.sum(axis=0, keepdims=True)
    total = table.sum()
    expected = row * col / total
    mask = expected > 0
    chi2 = float(((table[mask] - expected[mask]) ** 2 / expected[mask]).sum())
    df = 81
    try:
        from scipy import stats
        p = float(stats.chi2.sf(chi2, df))
    except ImportError:
        from math import erf, sqrt
        z = np.sqrt(9.0 * df / 2.0) * ((chi2 / df) ** (1.0 / 3.0) - 1 + 2.0 / (9.0 * df))
        p = 1.0 - 0.5 * (1.0 + erf(z / sqrt(2.0)))
    return chi2, p, table


def omission_stats(series):
    """遗漏（间隔）统计：每个数字相邻出现之间的期数间隔。"""
    gaps = []
    x = np.asarray(series, dtype=int)
    for d in range(10):
        idx = np.where(x == d)[0]
        if len(idx) < 2:
            continue
        gaps.extend(np.diff(idx).tolist())
    arr = np.asarray(gaps, dtype=float)
    return {
        'n_gaps': int(len(arr)),
        'mean_gap': float(arr.mean()),
        'max_gap': float(arr.max()),
        'expected_mean_gap': 10.0,  # 均匀随机下几何分布期望=10
    }


def runs_test(series):
    """游程检验：以中位数为界做二值化，检验上下交替是否随机。"""
    x = np.asarray(series, dtype=float)
    med = np.median(x)
    b = (x > med).astype(int)
    n1, n2 = int(b.sum()), int(len(b) - b.sum())
    runs = 1 + int((np.diff(b) != 0).sum())
    # 游程数期望与方差（无并列假设近似）
    n = n1 + n2
    if n1 == 0 or n2 == 0:
        return {'runs': runs, 'expected': None, 'z': None}
    mu = 2.0 * n1 * n2 / n + 1.0
    var = 2.0 * n1 * n2 * (2.0 * n1 * n2 - n) / (n ** 2 * (n - 1.0))
    z = (runs - mu) / (var ** 0.5) if var > 0 else 0.0
    from math import erf, sqrt
    p_two = 2.0 * (1.0 - 0.5 * (1.0 + erf(abs(z) / sqrt(2.0))))
    return {'runs': runs, 'expected': mu, 'z': float(z), 'p': float(p_two)}


def main():
    data = load_data()
    digits = {p: np.array([d[p] for d in data], dtype=int) for p in POSITIONS}
    hezhi = np.array([d['hezhi'] for d in data], dtype=int)
    span = np.array([d['span'] for d in data], dtype=int)
    n = len(data)

    report = []
    stats_out = {'n_periods': n, 'issue_range': [data[0]['issue'], data[-1]['issue']],
                 'alpha_bonferroni': ALPHA_BONF}

    report.append(f"# 排列5 历史数据规律分析报告（{date.today().isoformat()}）\n")
    report.append(f"> 样本：{n} 期（{data[0]['issue']} ~ {data[-1]['issue']}），"
                  f"来源 p5_history_data（is_valid=1）\n")
    report.append("> 诚实边界：排列5每期五位置独立公平摇号，本报告仅为历史数据"
                  "量化描述，不构成任何购彩建议。\n")
    report.append(f"> 多重比较校正：共 {N_TESTS} 项检验，Bonferroni 校正阈值 "
                  f"α' = {ALPHA_BONF:.5f}\n")

    # ---------- 1. 各位置频次与均匀性 ----------
    report.append("\n## 1. 各位置数字频次与均匀性检验\n")
    report.append("| 位置 | 卡方 | p 值 | 校正后显著 | 最高频号 | 最低频号 |")
    report.append("| ---- | ---- | ---- | ---- | ---- | ---- |")
    stats_out['position_freq'] = {}
    for p in POSITIONS:
        counts = np.bincount(digits[p], minlength=10)
        chi2, pv = chi2_uniform(counts)
        sig = pv < ALPHA_BONF
        stats_out['position_freq'][p] = {
            'counts': counts.tolist(), 'chi2': chi2, 'p': pv, 'sig_after_bonf': bool(sig)}
        top = int(np.argmax(counts)); low = int(np.argmin(counts))
        report.append(f"| {POSITION_NAMES[p]} | {chi2:.2f} | {pv:.4f} | "
                      f"{'是' if sig else '否'} | {top}({counts[top]}次) | {low}({counts[low]}次) |")

    # ---------- 2. 自相关 ----------
    report.append("\n## 2. 各位置 lag-1..5 自相关系数\n")
    report.append("| 位置 | lag1 | lag2 | lag3 | lag4 | lag5 | 最大|r| |")
    report.append("| ---- | ---- | ---- | ---- | ---- | ---- | ---- |")
    stats_out['autocorr'] = {}
    for p in POSITIONS:
        ac = lag_autocorr(digits[p])
        stats_out['autocorr'][p] = ac
        mx = max(abs(v) for v in ac.values())
        report.append(f"| {POSITION_NAMES[p]} | {ac[1]:+.4f} | {ac[2]:+.4f} | "
                      f"{ac[3]:+.4f} | {ac[4]:+.4f} | {ac[5]:+.4f} | {mx:.4f} |")
    report.append("\n> 5% 显著性临界值约 ±1.96/√n = ±{:.4f}（未做多重校正时）。\n"
                  .format(1.96 / (n ** 0.5)))

    # ---------- 3. 马尔可夫独立性 ----------
    report.append("\n## 3. 一阶马尔可夫独立性检验（10x10 转移表）\n")
    report.append("| 位置 | 卡方(df=81) | p 值 | 校正后显著 |")
    report.append("| ---- | ---- | ---- | ---- |")
    stats_out['markov'] = {}
    for p in POSITIONS:
        chi2, pv, table = markov_independence(digits[p])
        sig = pv < ALPHA_BONF
        stats_out['markov'][p] = {'chi2': chi2, 'p': pv, 'sig_after_bonf': bool(sig)}
        report.append(f"| {POSITION_NAMES[p]} | {chi2:.2f} | {pv:.4f} | {'是' if sig else '否'} |")

    # ---------- 4. 和值/跨度/形态 ----------
    report.append("\n## 4. 和值 / 跨度 / 奇偶 / 大小分布\n")
    # 和值理论分布 = 5 个独立 Uniform{0..9} 的卷积（非均匀，钟形），卡方拟合优度对照理论分布
    theo = np.array([1.0] * 10)
    for _ in range(4):
        theo = np.convolve(theo, np.array([1.0] * 10))
    theo = theo / theo.sum()
    obs = np.bincount(hezhi, minlength=len(theo))
    expected = theo * obs.sum()
    mask = expected >= 5  # 卡方合并低期望组（E>=5 经验法则）
    chi2_h, pv_h = chi2_fit(obs[mask], expected[mask])
    stats_out['hezhi'] = {'min': int(hezhi.min()), 'max': int(hezhi.max()),
                          'mean': float(hezhi.mean()), 'std': float(hezhi.std()),
                          'theo_fit_chi2': chi2_h, 'theo_fit_p': pv_h}
    report.append(f"- 和值范围 [{hezhi.min()}, {hezhi.max()}]，均值 {hezhi.mean():.2f} "
                  f"（理论期望 22.5），标准差 {hezhi.std():.2f}；对照五位置均匀卷积理论分布的"
                  f"卡方拟合优度 p={pv_h:.4f}（校正后{'显著' if pv_h < ALPHA_BONF else '不显著'}偏离）")
    report.append(f"- 跨度均值 {span.mean():.2f}，中位数 {np.median(span):.1f}")
    odd_cnt = (hezhi % 2 == 1).mean()
    report.append(f"- 奇和值占比 {odd_cnt:.3f}（理论 0.5，标准误 {0.5/n**0.5:.4f}）")
    big_cnt = (hezhi > 22).mean()
    report.append(f"- 和值>22 占比 {big_cnt:.3f}（和值理论期望 22.5，以 0.5 为粗略参考）")

    # ---------- 5. 遗漏分布 ----------
    report.append("\n## 5. 遗漏（间隔）分布 vs 几何期望\n")
    report.append("| 位置 | 间隔数 | 平均间隔 | 最大间隔 | 理论期望 |")
    report.append("| ---- | ---- | ---- | ---- | ---- |")
    stats_out['omission'] = {}
    for p in POSITIONS:
        om = omission_stats(digits[p])
        stats_out['omission'][p] = om
        report.append(f"| {POSITION_NAMES[p]} | {om['n_gaps']} | {om['mean_gap']:.2f} | "
                      f"{om['max_gap']:.0f} | 10.0 |")

    # ---------- 6. 游程检验 ----------
    report.append("\n## 6. 游程检验（中位数分割）\n")
    report.append("| 序列 | 游程数 | 期望 | z | p | 校正后显著 |")
    report.append("| ---- | ---- | ---- | ---- | ---- | ---- |")
    stats_out['runs'] = {}
    for p in POSITIONS:
        rt = runs_test(digits[p])
        sig = bool(rt.get('p') is not None and rt['p'] < ALPHA_BONF)
        stats_out['runs'][p] = {**rt, 'sig_after_bonf': sig}
        report.append(f"| {POSITION_NAMES[p]} | {rt['runs']} | {rt.get('expected', 0):.1f} | "
                      f"{rt.get('z', 0):+.2f} | {rt.get('p', float('nan')):.4f} | {'是' if sig else '否'} |")
    rt = runs_test(hezhi)
    stats_out['runs']['hezhi'] = rt
    report.append(f"| 和值 | {rt['runs']} | {rt.get('expected', 0):.1f} | "
                  f"{rt.get('z', 0):+.2f} | {rt.get('p', float('nan')):.4f} | - |")

    # ---------- 结论 ----------
    report.append("\n## 7. 结论（诚实口径）\n")
    sig_list = []
    for k, v in stats_out['position_freq'].items():
        if v['sig_after_bonf']:
            sig_list.append(f"频次均匀性({POSITION_NAMES[k]})")
    for k, v in stats_out['markov'].items():
        if v['sig_after_bonf']:
            sig_list.append(f"马尔可夫独立性({POSITION_NAMES[k]})")
    if sig_list:
        report.append(f"经 Bonferroni 校正后仍显著的指标：**{', '.join(sig_list)}**。"
                      "需注意多重检验下的假阳性风险，且历史显著不等于未来可预测。")
    else:
        report.append("**经 Bonferroni 校正后无任何指标显著偏离均匀随机**——与排列5"
                      "「五位置独立公平摇号」的机制完全一致。任何『提升命中率』的优化"
                      "在统计上均无法超过随机基线（Top-1=10% / Top-3=30%），优化方向"
                      "应聚焦正确性、性能与稳定性。")

    out_md = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'docs', f'data_pattern_analysis_{date.today().isoformat()}.md')
    with open(out_md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))
    out_json = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f'data_pattern_stats_{date.today().isoformat()}.json')
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(stats_out, f, ensure_ascii=False, indent=2)
    print(f"报告: {out_md}\n统计: {out_json}")


if __name__ == '__main__':
    main()
