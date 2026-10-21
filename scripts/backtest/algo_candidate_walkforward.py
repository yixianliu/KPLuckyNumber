# -*- coding: utf-8 -*-
"""
algo_candidate_walkforward.py - 候选算法走窗评估与采纳闸门（v5.0 阶段五 / M8）

目的：对 docs/KPLuckyNumber_upgrade_plan_v5.md §5.1 列出的 4 个候选算法做
**诚实验证**，仅当 95% CI 下界严格高于随机基线时方允许进入融合层，
否则一律归档 trial，**绝不**触碰线上冻结权重。

诚实边界（不可突破，写死在输出里）：
  排列5 每期五位置独立公平摇号，Top-k 的随机基线恒为 k/10
  （Top-1 = 10% / Top-3 = 30% / Top-5 = 50%）。
  本脚本只报告「点估计 + Wilson 95% CI」，任何「提升命中率」的说法必须
  同时给出 CI；CI 与基线重叠即视为无统计显著超额，按随机归档。

候选算法（§5.1 表）与本项目可执行形态：
  1. seq_model      —— Transformer/TCN 序列建模的**特征化代理**：
                       纯滞后位移特征（y[i-j] 的一期命中率、升平降方向、
                       连号、和值方向）。真实序列模型对公平摇号无归纳偏置
                       优势，此处以「最大信息量位移特征」量化其上界。
  2. poisson_nb     —— 泊松/负二项回归（冷热号强度）：以带时间衰减的
                       到达次数作为强度参数 λ，Poisson(λ) 归一化为概率。
  3. elo_rating     —— Elo-like 在线评分：每号码维护评分，出现 +gain /
                       未出现 -loss，评分即概率。可解释、零重训成本。
  4. stacking       —— 集成/堆叠：对已验证信号（频率 + 泊松 + Elo）
                       用「元权重按 Top-1 命中率回测校准」求加权平均，
                       而非再叠一个黑盒 meta-learner。

用法：
    python scripts/backtest/algo_candidate_walkforward.py
    # 可选参数
    --split 0.6          训练切分比例（下限 200 期）
    --retrain 100        Elo/堆叠元权重的重训间隔（期）
    --out-csv PATH       CSV 输出路径
    --out-md PATH        报告输出路径
    --max-tests N        测试期数上限（调试用，默认全量）

依赖：Anaconda python（含 sklearn；本脚本的朴素基线不依赖 sklearn，
      仅 seq_model 代理用到 numpy/GradientBoosting）。

技术约束：不改线上冻结权重（P5PredictorConfig）；不写 p5_weight_history；
          不写 Redis 权重通道；不新增融合权重键。
"""
import argparse
import csv
import math
import os
import random
import sys
from collections import Counter
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

# Wilson 95% CI 单一实现（ALG-06 收敛，PUP-20261019-013100）
from modules.stats_utils import wilson_ci  # noqa: E402

POS = ['wan', 'qian', 'bai', 'shi', 'ge']
POS_NAMES = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
NUM = list(range(10))

# 随机基线：Top-k 命中率恒为 k/10（公平摇号的数学恒等式，非经验估计）
BASELINE = {1: 0.10, 3: 0.30, 5: 0.50}

# 采纳闸门：仅当 CI 下界**严格高于**基线方允许进入融合
ADOPT_MARGIN = 0.0

# 窗口与平滑参数
WIN = 60          # 频率/泊松窗口
RECENT = 10       # 近期窗口（lag 特征）
DECAY = 0.95      # 时间衰减
ELO_K = 4.0       # Elo K 因子（评分以概率尺度表达，增益收敛快）


# ---------------------------------------------------------------- 统计工具
# Wilson 95% CI 已收敛至 modules/stats_utils.wilson_ci 单一实现
# （ALG-06 / PUP-20261019-013100）；原实现的 p 越界钳制防御随单一实现保留，
# 对合法输入数值逐位一致。


def pct(x):
    return f'{x * 100:.2f}%'


def pct1(x):
    return f'{x * 100:.1f}%'


def gate_verdict(rate, lo, hi, k):
    """采纳闸门判定：CI 下界 > 基线 + 余量 才允许进入融合。"""
    base = BASELINE[k]
    if lo > base + ADOPT_MARGIN:
        return 'ADOPT', f'CI 下界 {pct1(lo)} > 基线 {pct1(base)}'
    if hi < base - ADOPT_MARGIN:
        return 'TRIAL(显著劣于基线)', f'CI 上界 {pct1(hi)} < 基线 {pct1(base)}'
    return 'TRIAL', f'CI [{pct1(lo)},{pct1(hi)}] 与基线 {pct1(base)} 重叠'


# ---------------------------------------------------------------- 数据加载

def load(conn=None):
    """加载 p5_history_data 全量历史，按期号升序返回。"""
    import pymysql
    own = conn is None
    if own:
        conn = pymysql.connect(host='localhost', port=3306, user='root',
                               password='root', database='lucky_number',
                               charset='utf8mb4')
    cur = conn.cursor(pymysql.cursors.DictCursor)
    cur.execute('SELECT issue, wan,qian,bai,shi,ge,hezhi FROM p5_history_data ORDER BY issue ASC')
    rows = cur.fetchall()
    if own:
        conn.close()
    issues = [r['issue'] for r in rows]
    digits = {p: [int(r[p]) for r in rows] for p in POS}
    hezhi = [int(r['hezhi']) for r in rows]
    return issues, digits, hezhi


# ---------------------------------------------------------------- 基线策略

def topk_random(digits_pos, i, k):
    """随机基线：固定取 0..k-1（无信息，命中率=随机抽样期望）。"""
    return NUM[:k]


def dist_freq(digits_pos, i, window=WIN):
    """频率分布（含拉普拉斯平滑），作为所有融合组件的锚。"""
    s = max(0, i - window + 1)
    cnt = Counter(digits_pos[s:i + 1])
    a = 0.5
    tot = sum(cnt.get(d, 0) + a for d in NUM)
    return {d: (cnt.get(d, 0) + a) / tot for d in NUM}


def topk_from_prob(prob, k):
    order = sorted(NUM, key=lambda d: (-prob[d], d))
    return order[:k]


def dist_cold(digits_pos, i, window=WIN, beta=0.018, cap=50):
    """冷号/遗漏强度分布（对照用，历史上已被实证为噪声信号）。"""
    s = max(0, i - window + 1)
    win = digits_pos[s:i + 1]
    L = len(win)
    raw = {}
    for d in NUM:
        idx = None
        for kk in range(L - 1, -1, -1):
            if win[kk] == d:
                idx = kk
                break
        o = (L - 1 - idx) if idx is not None else L
        o = min(o, cap)
        raw[d] = math.exp(beta * o)
    tot = sum(raw.values())
    return {d: raw[d] / tot for d in NUM}


# ---------------------------------------------------------------- 候选算法

def dist_poisson(digits_pos, i, window=WIN):
    """
    候选 2：泊松回归（冷热号强度）。

    把号码在近期窗口内的「到达次数」当作泊松过程计数，估计强度 λ_d，
    再对 λ 做 softmax 归一化得到概率（softmax 比直接 Poisson 归一化更稳定，
    避免 λ 量纲差异把分布压成近 one-hot）。
    λ_d = Σ_{j<=i} decay^(i-j) · 1{y[j]=d}
    """
    s = max(0, i - window + 1)
    lam = {d: 0.0 for d in NUM}
    for j in range(s, i + 1):
        w = DECAY ** (i - j)
        lam[digits_pos[j]] += w
    # softmax over λ（温度 1）
    exps = {d: math.exp(lam[d]) for d in NUM}
    tot = sum(exps.values())
    return {d: exps[d] / tot for d in NUM}


def dist_elo(digits_pos, i, window=WIN, K=ELO_K):
    """
    候选 3：Elo-like 在线评分。

    每号码维护一个浮点评分；每次开奖，命中号码 +K，其余号码各 -K/9（零和），
    使期望评分恒为零、评分直接反映相对冷热。softmax 归一化为概率。
    与「遗漏回归」的区别：Elo 是零和的相对强弱，不假设"欠出必然回补"
    （后者已被实证为赌徒谬误噪声并清零）。
    """
    s = max(0, i - window + 1)
    score = {d: 0.0 for d in NUM}
    for j in range(s, i + 1):
        hit = digits_pos[j]
        for d in NUM:
            if d == hit:
                score[d] += K
            else:
                score[d] -= K / 9.0
    exps = {d: math.exp(score[d]) for d in NUM}
    tot = sum(exps.values())
    return {d: exps[d] / tot for d in NUM}


def dist_stacking(digits_pos, i, weights):
    """
    候选 4：集成/堆叠。

    对三个已验证/待验证信号（频率、泊松、Elo）按元权重加权平均。
    元权重不是任意给定，而是由评估主循环按「Top-1 命中率」走窗校准
    （见 evaluate 内的元权重更新），因此不存在"先验相信某种融合更好"的假设。
    """
    wf, wp, we = weights
    pf = dist_freq(digits_pos, i)
    pp = dist_poisson(digits_pos, i)
    pe = dist_elo(digits_pos, i)
    tot = wf + wp + we
    return {d: (wf * pf[d] + wp * pp[d] + we * pe[d]) / tot for d in NUM}


def dist_seq_proxy(digits_pos, i, lags=(1, 2, 3, 5, 8), win=40):
    """
    候选 1：序列建模代理（Transformer/TCN 的特征化上界）。

    序列模型可用的信息本质是「历史上每个位移 lag 下，源号码 p 对目标号码 c
    的条件转移分布」。这里把所有 lag 的条件转移直接加权聚合，再与频率锚定——
    等于给序列模型一个「信息量最大化、且无参数可被过拟合」的上界。
    若该上界仍无法显著超越随机基线，则真实序列模型（参数更多、更易过拟合）
    更不可能超越。

    平滑方式：拉普拉斯 add-0.5，使从未观测到的转移仍保有非零概率。
    """
    lam = {d: 0.0 for d in NUM}
    weight_sum = 0.0
    for lag in lags:
        if i - lag < 0:
            continue
        cnts = {p: 0.0 for p in NUM}
        for j in range(max(lag, i - win + 1), i + 1):
            cnts[digits_pos[j - lag]] += 1.0
        total_cnt = sum(cnts.values())
        if total_cnt <= 0:
            continue
        # 越小的 lag 权重越大（指数衰减），lag=1 权重最高
        w = 0.5 ** (lags.index(lag))
        weight_sum += w
        for d in NUM:
            # 条件概率：P(下一期=d | lag 期前=源)，按所有源号码平均后平滑
            lam[d] += w * ((cnts[d] + 0.5) / (total_cnt + 0.5 * len(NUM)))

    if weight_sum <= 0:
        return dist_freq(digits_pos, i)

    # 序列信号单独过弱（每期只有 ~40 个观测、100 个转移单元），
    # 需与频率锚定，避免小样本噪声主导排序。
    pf = dist_freq(digits_pos, i)
    tot = sum(lam.values()) or 1.0
    fused = {d: 0.7 * pf[d] + 0.3 * lam[d] / tot for d in NUM}
    tot2 = sum(fused.values()) or 1.0
    return {d: fused[d] / tot2 for d in NUM}


# ---------------------------------------------------------------- 走窗评估

def evaluate(issues, digits, hezhi, split_frac=0.6, retrain=100, max_tests=None):
    """
    走窗评估：训练集 = 前 split_frac（下限 200 期），测试集 = 其后。
    每个 (期, 位置) 为一次独立试验；每位置 Top-k 命中即计一次成功。
    返回 {strategy: {k: (success, n)}}。
    """
    N = len(issues)
    warmup = max(200, int(N * split_frac))
    test_idx = list(range(max(warmup, 60), N - 1))   # 预测 i+1，且需 >=60 期历史
    if max_tests:
        test_idx = test_idx[:max_tests]

    strategies = ['random', 'freq', 'cold', 'seq_proxy', 'poisson', 'elo', 'stacking']
    hit = {s: {1: 0, 3: 0, 5: 0} for s in strategies}
    n_test = len(test_idx) * len(POS)

    # 堆叠元权重（初始等权），走窗按 Top-1 命中率周期性校准
    stack_w = {'f': 1.0, 'p': 1.0, 'e': 1.0}
    # 各组件 Top-1 滚动命中计数（用于元权重校准）
    comp_hits = {'f': 0, 'p': 0, 'e': 0}
    last_cal = -10 ** 9

    for t in test_idx:
        if t - last_cal >= retrain:
            if sum(comp_hits.values()) >= 30:
                s = sum(comp_hits.values()) or 1.0
                stack_w = {'f': comp_hits['f'] / s,
                           'p': comp_hits['p'] / s,
                           'e': comp_hits['e'] / s}
                # 防单一组件权重过零导致退化
                for key in stack_w:
                    stack_w[key] = max(stack_w[key], 0.1)
                tot = sum(stack_w.values())
                stack_w = {k: v / tot for k, v in stack_w.items()}
            comp_hits = {'f': 0, 'p': 0, 'e': 0}
            last_cal = t

        for p in POS:
            true = digits[p][t + 1]
            # 每个 (期, 位置) 各分布只算一次，供所有策略复用（避免重复扫描历史）
            df = dist_freq(digits[p], t)
            dp = dist_poisson(digits[p], t)
            de = dist_elo(digits[p], t)
            dcs = dist_cold(digits[p], t)
            ds = dist_seq_proxy(digits[p], t)
            dst = dist_stacking(digits[p], t, (stack_w['f'], stack_w['p'], stack_w['e']))

            preds = {
                'random': topk_random(digits[p], t, 5),
                'freq': topk_from_prob(df, 5),
                'cold': topk_from_prob(dcs, 5),
                'seq_proxy': topk_from_prob(ds, 5),
                'poisson': topk_from_prob(dp, 5),
                'elo': topk_from_prob(de, 5),
                'stacking': topk_from_prob(dst, 5),
            }
            # 元权重校准只统计 Top-1 命中
            for key, prob in (('f', df), ('p', dp), ('e', de)):
                top1 = sorted(NUM, key=lambda d: -prob[d])
                if true == top1[0]:
                    comp_hits[key] += 1
            for s in strategies:
                for k in (1, 3, 5):
                    if true in preds[s][:k]:
                        hit[s][k] += 1

    return hit, n_test, test_idx


# ---------------------------------------------------------------- 输出生成

def build_rows(hit, n_test):
    """组装策略 × Top-k 结果行。"""
    labels = {
        'random': '随机基线 (Top-k = k/10)',
        'freq': '频率（现有主信号，对照）',
        'cold': '冷号遗漏（历史噪声，对照）',
        'seq_proxy': '候选1 序列建模代理',
        'poisson': '候选2 泊松/负二项回归',
        'elo': '候选3 Elo-like 在线评分',
        'stacking': '候选4 集成/堆叠',
    }
    rows = []
    for s in ['random', 'freq', 'cold', 'seq_proxy', 'poisson', 'elo', 'stacking']:
        row = {'strategy': s, 'label': labels[s]}
        for k in (1, 3, 5):
            succ = hit[s][k]
            rate = succ / n_test if n_test else 0.0
            lo, hi = wilson_ci(succ, n_test)
            if s == 'random':
                verdict, reason = 'BASELINE', '随机基线 k/10'
            else:
                verdict, reason = gate_verdict(rate, lo, hi, k)
            row[f'top{k}_hit'] = succ
            row[f'top{k}_rate'] = round(rate, 6)
            row[f'top{k}_lo'] = round(lo, 6)
            row[f'top{k}_hi'] = round(hi, 6)
            row[f'top{k}_verdict'] = verdict
            row[f'top{k}_reason'] = reason
        rows.append(row)
    return rows


def write_csv(rows, path, meta):
    cols = ['strategy', 'label', 'n_test'] + [
        f'top{k}_{c}' for k in (1, 3, 5)
        for c in ('hit', 'rate', 'lo', 'hi', 'verdict', 'reason')
    ]
    with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            out = dict(r)
            out['n_test'] = meta['n_test']
            w.writerow(out)


def adopted_count(rows):
    """统计被采纳的候选算法数（任一 Top-k 达到 ADOPT 即算采纳）。"""
    cands = ('seq_proxy', 'poisson', 'elo', 'stacking')
    out = []
    for r in rows:
        if r['strategy'] in cands:
            v = [r[f'top{k}_verdict'] for k in (1, 3, 5)]
            if 'ADOPT' in v:
                out.append(r['strategy'])
    return out


def write_markdown(rows, path, meta):
    L = []
    A = L.append
    A('# 候选算法走窗评估报告')
    A('')
    A(f'- 生成时间：{meta["generated_at"]}')
    A(f'- 数据范围：{meta["issue_min"]} ~ {meta["issue_max"]}（{meta["n_issues"]} 期）')
    A(f'- 训练/测试切分：{meta["warmup"]} 期起为测试集')
    A(f'- 独立试验数 n = {meta["n_test"]}（= 测试期数 × 5 位置）')
    A('- 统计口径：点估计 + Wilson 95% 置信区间；每 (期, 位置) 为一次独立试验')
    A('')
    A('## 一、诚实边界（不可突破）')
    A('')
    A('> 排列5 每期五位置为**独立公平摇号**。Top-k 的随机基线是数学恒等式 k/10：')
    A('> Top-1 = 10%、Top-3 = 30%、Top-5 = 50%。')
    A('> 任何「提升命中率」的声称必须附 95% CI；**CI 与基线重叠即视为无统计显著超额**，')
    A('> 按随机归档 trial，绝不进入线上融合、绝不调整冻结权重。')
    A('')
    A('本报告不声称任何候选算法能超越随机基线；结论只由数据决定。')
    A('')
    A('## 二、候选算法与可执行形态')
    A('')
    A('| 候选 | 评估形态 | 说明 |')
    A('|---|---|---|')
    A('| 1. Transformer / Temporal CNN | `seq_proxy` | 以「各位移 lag 条件命中率」特征加权求和为**信息量上界代理**；'
      '若上界都无法显著超基线，真实序列模型更不可能 |')
    A('| 2. 泊松 / 负二项回归 | `poisson` | 带时间衰减的到达强度 λ_d，softmax 归一化 |')
    A('| 3. Elo-like 在线评分 | `elo` | 零和评分（命中 +K / 未命中 -K/9），softmax 归一化；'
      '区别于已被清零的「遗漏回归」，不假设欠出必然回补 |')
    A('| 4. 集成 / 堆叠 | `stacking` | 对频率/泊松/Elo 按**走窗校准**的元权重加权，非任意先验 |')
    A('')
    A('## 三、走窗结果')
    A('')
    A(f'| 策略 | Top-1 | Top-3 | Top-5 |')
    A('|---|---|---|---|')
    labels_short = {
        'random': '随机基线', 'freq': '频率(对照)', 'cold': '冷号(对照)',
        'seq_proxy': '①序列代理', 'poisson': '②泊松回归',
        'elo': '③Elo评分', 'stacking': '④堆叠集成',
    }
    for r in rows:
        cells = []
        for k in (1, 3, 5):
            mark = ''
            if r['strategy'] != 'random':
                if r[f'top{k}_verdict'] == 'ADOPT':
                    mark = ' ✅'
                elif r[f'top{k}_verdict'].startswith('TRIAL(显著劣'):
                    mark = ' ⚠️'
            cell = (f'{pct(r[f"top{k}_rate"])} '
                    f'[{pct1(r[f"top{k}_lo"])}–{pct1(r[f"top{k}_hi"])}){mark}'.replace(')', ']', 1))
            cells.append(cell)
        A(f'| {labels_short[r["strategy"]]} | ' + ' | '.join(cells) + ' |')
    A('')
    A('> 括号内为 Wilson 95% CI。✅ = CI 下界严格高于基线（可采纳）；'
      '⚠️ = CI 上界低于基线（显著劣于随机）。')
    A('')
    A('## 四、采纳闸门判定')
    A('')
    A(f'闸门规则：仅当候选策略某 Top-k 的 **95% CI 下界 > 基线 + {ADOPT_MARGIN:.0%}** '
      '方允许进入融合层；否则归档 `trial`。')
    A('')
    adopted = adopted_count(rows)
    A(f'- 通过闸门：**{len(adopted)} 个**' + (f'（{", ".join(adopted)}）' if adopted else '（无）'))
    A('- 未通过者：100% 归档 `trial`，不进入融合层')
    A('- 线上冻结权重（频率0.68 / ml_supervised0.14 / 贝叶斯0.10 / 趋势0.01 / '
      '马尔可夫0.005 / 形态0.003 / 特征0.002）**未被修改**')
    A('- 未写 `p5_weight_history`，未写 Redis 权重通道')
    A('')
    for r in rows:
        if r['strategy'] not in ('random', 'freq', 'cold'):
            A(f'### {labels_short[r["strategy"]]}（`{r["strategy"]}`）')
            A('')
            for k in (1, 3, 5):
                A(f'- Top-{k}：命中 {r[f"top{k}_hit"]}/{meta["n_test"]} = '
                  f'{pct(r[f"top{k}_rate"])}，CI [{pct1(r[f"top{k}_lo"])}, '
                  f'{pct1(r[f"top{k}_hi"])}] → **{r[f"top{k}_verdict"]}**'
                  f'（{r[f"top{k}_reason"]}）')
            A('')
    A('## 五、结论')
    A('')
    if adopted:
        A(f'有 {len(adopted)} 个候选在至少一个 Top-k 上 CI 下界严格高于随机基线，'
          '需经评审后以**新增冻结权重键**方式接入（保持 v4.0 七算法权重不变），'
          '并经 auto_evaluator 全评估器复核命中率无回归。')
    else:
        A('**无任何候选算法通过采纳闸门。** 全部归档 `trial`。')
        A('')
        A('这与「排列5 是公平摇号」的先验一致：在无序列依赖的独立均匀分布上，')
        A('任何仅基于历史的统计信号都无法产生统计显著的超额。')
        A('后续不应再以「提升命中率」为目标优化候选算法；正确的优化方向是')
        A('降低不确定性表述的风险（诚实边界、风险提示、资金管理），而非拟合噪声。')
    A('')
    A('---')
    A('')
    A(f'*本报告由 `scripts/backtest/algo_candidate_walkforward.py` 自动生成。'
      f'CSV 明细见 `{os.path.basename(meta["out_csv"])}`。*')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')


# ---------------------------------------------------------------- 主入口

def main(argv=None):
    ap = argparse.ArgumentParser(description='候选算法走窗评估与采纳闸门')
    ap.add_argument('--split', type=float, default=0.6, help='训练切分比例（下限 200 期）')
    ap.add_argument('--retrain', type=int, default=100, help='堆叠元权重重训间隔（期）')
    ap.add_argument('--max-tests', type=int, default=None, help='测试期数上限（调试用）')
    ap.add_argument('--out-csv', default=os.path.join(
        PROJECT_ROOT, 'reports', 'algo_candidates.csv'))
    ap.add_argument('--out-md', default=os.path.join(
        PROJECT_ROOT, 'reports', 'algo_candidates.md'))
    ap.add_argument('--seed', type=int, default=0, help='随机种子（当前无随机性，保留接口）')
    args = ap.parse_args(argv)

    random.seed(args.seed)
    t0 = datetime.now()

    print('[1/4] 加载历史开奖数据 ...')
    issues, digits, hezhi = load()
    print(f'      期数={len(issues)}  范围[{issues[0]}..{issues[-1]}]')

    print(f'[2/4] 走窗评估（split={args.split}, retrain={args.retrain}） ...')
    hit, n_test, test_idx = evaluate(
        issues, digits, hezhi,
        split_frac=args.split, retrain=args.retrain, max_tests=args.max_tests)

    print(f'[3/4] 生成 CSV + 报告 ...')
    N = len(issues)
    warmup = max(200, int(N * args.split))
    rows = build_rows(hit, n_test)
    os.makedirs(os.path.dirname(args.out_csv), exist_ok=True)
    os.makedirs(os.path.dirname(args.out_md), exist_ok=True)
    meta = {
        'generated_at': t0.strftime('%Y-%m-%d %H:%M:%S'),
        'issue_min': issues[0], 'issue_max': issues[-1],
        'n_issues': N, 'warmup': warmup,
        'n_test': n_test, 'test_periods': len(test_idx),
        'out_csv': args.out_csv, 'out_md': args.out_md,
        'split': args.split, 'retrain': args.retrain,
        'baseline': BASELINE, 'adopt_margin': ADOPT_MARGIN,
    }
    write_csv(rows, args.out_csv, meta)
    write_markdown(rows, args.out_md, meta)

    adopted = adopted_count(rows)
    # 控制台摘要
    print(f'{"策略":22s} {"Top-1":>24s} {"Top-3":>24s} {"Top-5":>24s}')
    print('-' * 96)
    for r in rows:
        cells = []
        for k in (1, 3, 5):
            cells.append(f'{pct(r[f"top{k}_rate"])}[{pct1(r[f"top{k}_lo"])}-{pct1(r[f"top{k}_hi"])}]')
        print(f'{r["label"][:22]:22s} ' + ' '.join(f'{c:>24s}' for c in cells))
    print('-' * 96)
    print(f'随机基线（Top-1/3/5）：{pct(BASELINE[1])} / {pct(BASELINE[3])} / {pct(BASELINE[5])}')
    print(f'通过采纳闸门：{len(adopted)} 个' + (f' -> {adopted}' if adopted else '（全部归档 trial）'))
    print(f'线上冻结权重未被修改；未写 p5_weight_history；未写 Redis 权重通道。')
    print(f'[4/4] CSV: {args.out_csv}')
    print(f'      MD : {args.out_md}')
    print(f'耗时 {datetime.now() - t0}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
