# -*- coding: utf-8 -*-
"""
backtest_multisource.py - 排列5 多源数据 走窗回测（诚实基线对照）

目的：在真实开奖数据上，用走窗(walk-forward)方式严格检验各策略是否超越随机基线。
策略对照：
  A. 随机基线      (理论 Top-k 命中率 = k/10)
  B. 频率策略      (取近窗最常出现的 k 个数字 —— 无偏朴素基准)
  C. 冷号/遗漏策略  (取近窗遗漏最大的 k 个数字 —— 检验"赌徒谬误"是否真的有害)
  D. 监督模型(全特征) GradientBoosting，融合历史/遗漏/热温冷/连号/SPJ方向/和值
  E. 监督模型(仅频率类) 仅用窗口频率特征，检验 ML 相较朴素频率是否真有增益

用法：python scripts/backtest_multisource.py
依赖：Anaconda python (含 sklearn)
"""
import pymysql
import math
import numpy as np
from math import sqrt
from collections import Counter
from sklearn.ensemble import GradientBoostingClassifier

POS = ['wan', 'qian', 'bai', 'shi', 'ge']
NUM = list(range(10))

# ---------- 数据加载 ----------
def load():
    conn = pymysql.connect(host='localhost', port=3306, user='root',
                           password='root', database='lucky_number', charset='utf8mb4')
    cur = conn.cursor(pymysql.cursors.DictCursor)
    cur.execute('SELECT issue, wan,qian,bai,shi,ge,hezhi FROM p5_history_data ORDER BY issue ASC')
    rows = cur.fetchall()
    conn.close()
    issues = [r['issue'] for r in rows]
    digits = {p: [int(r[p]) for r in rows] for p in POS}
    hezhi = [int(r['hezhi']) for r in rows]
    return issues, digits, hezhi

# ---------- 特征构造（严格只用 issue i 之前的数据） ----------
def build_features(digits_pos, hezhi, i):
    """返回预测 issue i+1 时使用的特征向量（只用 <= i 的数据）。"""
    if i < 60:
        return None
    y = digits_pos
    feats = []
    # 多窗口频率
    for w in (10, 20, 40, 60):
        cnt = Counter(y[i - w + 1: i + 1])
        feats += [cnt.get(d, 0) / w for d in NUM]
    # 多窗口遗漏（距上次出现）
    for w in (20, 60):
        win = y[i - w + 1: i + 1]
        om = []
        for d in NUM:
            # 在窗口内从右向左找 d 的最近位置
            idx = None
            for k in range(len(win) - 1, -1, -1):
                if win[k] == d:
                    idx = k; break
            om.append((len(win) - 1 - idx) if idx is not None else w)
        feats += om
    # 最近值及其属性
    last = y[i]
    feats += [last, last % 3, last % 2, 1 if last >= 5 else 0, 1 if last in (2, 3, 5, 7) else 0]
    # 连号
    feats.append(1 if (i > 0 and y[i] == y[i - 1]) else 0)
    # SPJ 方向（升/平/降）近窗统计与遗漏
    up = cnt_up = cnt_eq = cnt_dn = 0
    last_up = last_eq = last_dn = 999
    for k in range(i - 40, i):
        diff = y[k + 1] - y[k]
        if diff > 0: cnt_up += 1
        elif diff == 0: cnt_eq += 1
        else: cnt_dn += 1
    feats += [cnt_up / 40.0, cnt_eq / 40.0, cnt_dn / 40.0]
    # 和值/和尾 近窗频率与均值
    hz_win = hezhi[i - 40 + 1: i + 1]
    hwei_win = [h % 10 for h in hz_win]
    chz = Counter(hwei_win)
    feats += [chz.get(d, 0) / 40.0 for d in NUM]
    feats.append(np.mean(hz_win))
    return np.array(feats, dtype=float)

def feature_dim():
    # 4窗口频率(40) + 2窗口遗漏(20) + 5属性 + 1连号 + 3方向 + 10和尾 + 1均值 = 80
    return 40 + 20 + 5 + 1 + 3 + 10 + 1

# ---------- 策略实现 ----------
def topk_freq(digits_pos, i, k):
    cnt = Counter(digits_pos[i - 60 + 1: i + 1])
    order = sorted(NUM, key=lambda d: (-cnt.get(d, 0), d))
    return order[:k]

def topk_cold(digits_pos, i, k):
    win = digits_pos[i - 60 + 1: i + 1]
    om = {}
    for d in NUM:
        idx = None
        for kk in range(len(win) - 1, -1, -1):
            if win[kk] == d:
                idx = kk; break
        om[d] = (len(win) - 1 - idx) if idx is not None else len(win)
    order = sorted(NUM, key=lambda d: (-om[d], d))
    return order[:k]

# 复现「生产权重融合」以量化冷号偏差拖累：0.54频率 + 0.34冷号 + 0.10均匀(贝叶斯近似)
def dist_freq(digits_pos, i):
    cnt = Counter(digits_pos[i - 60 + 1: i + 1])
    a = 0.1
    tot = sum(cnt.get(d, 0) + a for d in NUM)
    return {d: (cnt.get(d, 0) + a) / tot for d in NUM}

def dist_cold(digits_pos, i, beta=0.018, cap=50):
    win = digits_pos[i - 60 + 1: i + 1]
    L = len(win)
    raw = {}
    for d in NUM:
        idx = None
        for kk in range(L - 1, -1, -1):
            if win[kk] == d:
                idx = kk; break
        o = (L - 1 - idx) if idx is not None else L
        o = min(o, cap)
        raw[d] = math.exp(beta * o)
    tot = sum(raw.values())
    return {d: raw[d] / tot for d in NUM}

def current_fusion_topk(digits_pos, i, k):
    wf, wc = 0.54, 0.34
    pf = dist_freq(digits_pos, i); pc = dist_cold(digits_pos, i)
    fused = {d: (wf * pf[d] + wc * pc[d] + 0.10 * 0.1) / (wf + wc + 0.10) for d in NUM}
    order = sorted(NUM, key=lambda d: -fused[d])
    return order[:k]

# ---------- 走窗回测 ----------
def wilson_ci(success, n, z=1.96):
    if n == 0: return (0, 0)
    p = success / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0, center - half), min(1, center + half))

def evaluate(issues, digits, hezhi, split_frac=0.6, retrain=200):
    """
    走窗回测：训练集 = 前 split_frac 比例（至少 200 期），测试集 = 剩余。
    retrain: 测试期内扩展窗口重训间隔；设大值≈单次训练（固定切分）。
    """
    N = len(issues)
    warmup = max(200, int(N * split_frac))
    test_idx = list(range(warmup, N - 1))  # 预测 i+1
    # 累计命中
    hit = {s: {1: 0, 3: 0, 5: 0} for s in ('freq', 'cold', 'gb_all', 'gb_freq', 'cur_fusion', 'new_fusion')}
    n_test = len(test_idx) * len(POS)  # 每个(位置,期)为一次独立试验

    # 预构建全量特征（用 <= i）
    feat_cache = {}
    for p in POS:
        feat_cache[p] = [build_features(digits[p], hezhi, i) for i in range(N)]

    # 监督模型：为每个位置训练；走窗扩展，定期重训
    gb_all, gb_freq = {}, {}
    last_train = -10**9
    for t in test_idx:
        if t - last_train >= retrain:
            X, yc = {p: [] for p in POS}, {p: [] for p in POS}
            for j in range(60, t):
                for p in POS:
                    f = feat_cache[p][j]
                    if f is not None:
                        X[p].append(f); yc[p].append(digits[p][j + 1])
            for p in POS:
                Xa = np.array(X[p]); ya = np.array(yc[p])
                m1 = GradientBoostingClassifier(n_estimators=150, max_depth=3,
                                                learning_rate=0.1, subsample=0.8,
                                                random_state=0)
                m1.fit(Xa, ya)
                gb_all[p] = m1
                m2 = GradientBoostingClassifier(n_estimators=150, max_depth=3,
                                                learning_rate=0.1, subsample=0.8,
                                                random_state=0)
                m2.fit(Xa[:, :40], ya)
                gb_freq[p] = m2
            last_train = t

        # 评估
        for p in POS:
            true = digits[p][t + 1]
            for k in (1, 3, 5):
                if true in topk_freq(digits[p], t, k): hit['freq'][k] += 1
                if true in topk_cold(digits[p], t, k): hit['cold'][k] += 1
                if true in current_fusion_topk(digits[p], t, k): hit['cur_fusion'][k] += 1
            fa = feat_cache[p][t]
            if fa is not None:
                pa = gb_all[p].predict_proba(fa.reshape(1, -1))[0]
                order = np.argsort(-pa)
                for k in (1, 3, 5):
                    if true in order[:k]: hit['gb_all'][k] += 1
                pf = gb_freq[p].predict_proba(fa[:40].reshape(1, -1))[0]
                orderf = np.argsort(-pf)
                for k in (1, 3, 5):
                    if true in orderf[:k]: hit['gb_freq'][k] += 1
                # G. 新生产融合(权重再平衡): 0.68频率 + 0.14监督ML(gb_freq代理) + 0.06冷号 + 0.10均匀(贝叶斯近似)
                pf_dict = {d: pf[d] for d in NUM}
                pfr = dist_freq(digits[p], t)
                pco = dist_cold(digits[p], t)
                wf, wml, wc, wb = 0.68, 0.14, 0.06, 0.10
                nf = {d: (wf * pfr[d] + wml * pf_dict[d] + wc * pco[d] + wb * 0.1) / (wf + wml + wc + wb) for d in NUM}
                onf = sorted(NUM, key=lambda d: -nf[d])
                for k in (1, 3, 5):
                    if true in onf[:k]: hit['new_fusion'][k] += 1

    print(f'测试期数(每位置): {n_test}  (训练切分={warmup}, retrain={retrain})')
    print(f'{"策略":14s} {"Top1":>10s} {"Top3":>10s} {"Top5":>10s}   随机基线: 10.0% / 30.0% / 50.0%')
    print('-' * 72)
    labels = {'freq': 'B.频率(无偏)', 'cold': 'C.冷号遗漏', 'gb_all': 'D.GB全特征',
              'gb_freq': 'E.GB仅频率', 'cur_fusion': 'F.生产当前融合', 'new_fusion': 'G.新融合(v3.48)'}
    for s in ('freq', 'cold', 'gb_all', 'gb_freq', 'cur_fusion', 'new_fusion'):
        row = []
        for k in (1, 3, 5):
            succ = hit[s][k]
            rate = succ / n_test * 100
            lo, hi = wilson_ci(succ, n_test)
            row.append(f'{rate:5.2f}%[{lo*100:.1f}-{hi*100:.1f}]')
        print(f'{labels[s]:14s} ' + ' '.join(f'{r:>22s}' for r in row))

if __name__ == '__main__':
    issues, digits, hezhi = load()
    print(f'加载完成: 期数={len(issues)} 范围[{issues[0]}..{issues[-1]}]')
    evaluate(issues, digits, hezhi, split_frac=0.6, retrain=10**9)
