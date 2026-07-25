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
走势信号源 walk-forward 回测 (v3.15 增量)

用途: 用历史 N 期 walk-forward 量化 TrendAnalyzer 各信号源单独命中率 + 融合命中率,
      以数据回答"走势信号源是否有解释力 / 信号源自适应是否有价值"。

诚实基线: 排列5 公平摇号, Top-5 随机命中概率 = 5/10 = 50%。
v3.14 审计预期: 走势无法稳定超越随机, 本回测给出实证。

回测口径:
  - 仅评估基于 history 序列可计算的信号源 (frequency/omission/momentum/direction/sum_center)
  - 贝叶斯信号源依赖外部表截止期过滤, 回测跳过(标注)
  - Top-5 口径: 每位置取该信号源打分最高的5个数字, 实际开奖号在5中即命中

用法:
    python opt_trend_backtest.py            # 默认回测最近 100 期, 训练窗口 60
    python opt_trend_backtest.py 200         # 回测最近 200 期
    python opt_trend_backtest.py 100 40      # 100期, 训练窗口 40
"""
import sys
import os
import json
import logging
from datetime import datetime
from collections import defaultdict

logging.getLogger().setLevel(logging.WARNING)
for _n in ['modules', 'urllib3', 'matplotlib', 'PIL']:
    logging.getLogger(_n).setLevel(logging.WARNING)

from modules.database import P5Database
from modules.trend_analyzer import TrendAnalyzer, DEFAULT_SIGNAL_WEIGHTS

POSITIONS = ['wan', 'qian', 'bai', 'shi', 'ge']
RANDOM_BASELINE_TOP5 = 50.0  # 5/10


def run_backtest(test_count=100, period=60):
    db = P5Database()
    if not db.connect():
        print('数据库连接失败')
        return None
    try:
        all_hist = db.get_history_data(limit=None, order='ASC') or []
        total = len(all_hist)
        if total < period + 5:
            print(f'历史数据不足({total}期), 至少需 {period + 5} 期')
            return None
        start = max(period, total - test_count)
        end = total
        actual_test = end - start

        analyzer = TrendAnalyzer(db, enable_adapt=False)
        sig_hits = defaultdict(int)
        sig_total = defaultdict(int)
        fused_hits = 0
        fused_total = 0
        pos_sig_hits = {p: defaultdict(int) for p in POSITIONS}
        pos_sig_total = {p: defaultdict(int) for p in POSITIONS}
        pos_fused_hits = {p: 0 for p in POSITIONS}
        per_test = []

        for t in range(start, end):
            window_asc = all_hist[t - period:t]
            window_desc = list(reversed(window_asc))
            hezhi_recent = [sum(int(r.get(p, 0)) for p in POSITIONS) for r in window_desc[:15]]
            data = {
                'history': window_desc,
                'pos_trends': {},
                'hezhi_recent': hezhi_recent,
                'bayesian': None,
                'period': period,
            }
            actual_row = all_hist[t]
            actual_nums = {p: int(actual_row[p]) for p in POSITIONS
                           if actual_row.get(p) is not None}
            if len(actual_nums) < 5:
                continue

            weights = analyzer.weight_mgr.get_weights(set(DEFAULT_SIGNAL_WEIGHTS))
            per_pos = {}
            for p in POSITIONS:
                sig = analyzer.extract_signals(p, data)
                available = sig.get('available', set())
                scores = sig.get('scores', {})
                ad = actual_nums[p]
                pos_res = {}
                for sname in available:
                    sc = scores.get(sname, {})
                    top5 = [d for d, _ in sorted(sc.items(),
                               key=lambda x: x[1], reverse=True)[:5]]
                    hit = ad in top5
                    sig_hits[sname] += int(hit)
                    sig_total[sname] += 1
                    pos_sig_hits[p][sname] += int(hit)
                    pos_sig_total[p][sname] += 1
                    pos_res[sname] = int(hit)
                fused = {}
                for d in range(10):
                    fused[d] = sum(weights.get(s, 0) * scores[s].get(d, 0.0)
                                   for s in available)
                ftop5 = [d for d, _ in sorted(fused.items(),
                            key=lambda x: x[1], reverse=True)[:5]]
                fhit = ad in ftop5
                fused_hits += int(fhit)
                fused_total += 1
                pos_fused_hits[p] += int(fhit)
                per_pos[p] = {'actual': ad, 'sig_hits': pos_res, 'fused_hit': int(fhit)}
            per_test.append({'issue': actual_row.get('issue'), 'per_pos': per_pos})

        sig_rates = {s: round(sig_hits[s] / sig_total[s] * 100, 2) if sig_total[s] else 0.0
                     for s in DEFAULT_SIGNAL_WEIGHTS}
        fused_rate = round(fused_hits / fused_total * 100, 2) if fused_total else 0.0
        pos_fused_rates = {p: round(pos_fused_hits[p] / actual_test * 100, 2) for p in POSITIONS}
        pos_sig_rates = {p: {s: round(pos_sig_hits[p][s] / pos_sig_total[p][s] * 100, 2)
                             if pos_sig_total[p][s] else 0.0
                             for s in DEFAULT_SIGNAL_WEIGHTS}
                         for p in POSITIONS}

        print(f'\n=== 走势信号源 walk-forward 回测 (最近{actual_test}期, 训练窗口={period}, 总历史={total}) ===')
        print(f'  随机基线 Top-5: {RANDOM_BASELINE_TOP5:.1f}%  (5/10, 非容错口径)')
        print(f'  --- 各信号源单独 Top-5 命中率 ---')
        for s in sorted(DEFAULT_SIGNAL_WEIGHTS):
            if sig_total[s] == 0:
                print(f'  {s:12s}: (无样本, 贝叶斯未纳入回测)')
                continue
            diff = sig_rates[s] - RANDOM_BASELINE_TOP5
            tag = '↑' if diff > 1 else ('↓' if diff < -1 else '≈')
            print(f'  {s:12s}: {sig_rates[s]:6.2f}%  (vs随机 {diff:+.2f}%) {tag}  样本={sig_total[s]}')
        print(f'  --- 融合(默认权重, 非自适应) ---')
        diff_f = fused_rate - RANDOM_BASELINE_TOP5
        tag_f = '↑' if diff_f > 1 else ('↓' if diff_f < -1 else '≈')
        print(f'  {"融合":12s}: {fused_rate:6.2f}%  (vs随机 {diff_f:+.2f}%) {tag_f}  样本={fused_total}')
        print(f'  --- 各位置融合命中率 ---')
        for p in POSITIONS:
            print(f'  {p:4s}: {pos_fused_rates[p]:6.2f}%')
        all_rates = list(sig_rates.values()) + [fused_rate]
        within_random = all(abs(r - RANDOM_BASELINE_TOP5) <= 2 for r in all_rates if r > 0)
        print(f'\n  诚实结论: {"各信号源及融合命中率均在随机基线±2%内, 无稳定超越随机信号 (符合v3.14审计预期)" if within_random else "部分指标偏离随机基线±2%, 见上表"}')

        return {
            'time': datetime.now().isoformat(),
            'test_count': actual_test,
            'train_period': period,
            'total_history': total,
            'random_baseline_top5': RANDOM_BASELINE_TOP5,
            'note': 'Top-5口径=每位置打分最高5个数字含实际开奖号; 仅评估history可算的信号源, 贝叶斯未纳入',
            'signal_rates': sig_rates,
            'signal_samples': {s: sig_total[s] for s in DEFAULT_SIGNAL_WEIGHTS},
            'fused_rate': fused_rate,
            'fused_samples': fused_total,
            'pos_fused_rates': pos_fused_rates,
            'pos_signal_rates': pos_sig_rates,
            'per_test': per_test,
        }
    finally:
        db.disconnect()


if __name__ == '__main__':
    tc = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    pd = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    r = run_backtest(tc, pd)
    if r:
        os.makedirs('reports/backtest', exist_ok=True)
        with open('reports/backtest/trend_signal_backtest.json', 'w', encoding='utf-8') as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print('\n结果已保存: reports/backtest/trend_signal_backtest.json')
