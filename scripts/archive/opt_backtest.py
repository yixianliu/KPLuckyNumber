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
优化对比回测工具 (Phase 2)

用途: 在算法/策略/流程/自学习优化的前后, 用同一 walk-forward 回测
      量化 Top-1/3/5 命中率、综合得分, 确保每次改动都以数据说话。

用法:
    python opt_backtest.py            # 默认回测最近 80 期
    python opt_backtest.py 120        # 回测最近 120 期
"""
import sys
import logging
import json
from datetime import datetime

logging.getLogger().setLevel(logging.WARNING)
for _n in ['modules', 'urllib3', 'matplotlib', 'PIL']:
    logging.getLogger(_n).setLevel(logging.WARNING)

from modules.predictor import P5Predictor
from modules.backtester import Backtester
from modules.database import P5Database


def run_current(test_count=80, label='当前配置'):
    """用当前默认配置回测(禁用AI以保证可复现与速度)。"""
    p = P5Predictor()
    p.ai_available = False
    p.config.config.setdefault('global', {})['enable_ai_model'] = False

    db = P5Database()
    db.connect()
    db.cursor.execute('SELECT COUNT(*) as c FROM p5_history_data')
    total = db.cursor.fetchone()['c']
    db.disconnect()
    start_index = max(50, total - test_count)

    bt = Backtester(p)
    res = bt.run_backtest(start_index=start_index, test_count=test_count)
    if res.get('status') != 'success':
        print(f'{label}: 回测失败 {res.get("message")}')
        return None
    s = res['overall_stats']
    print(f'\n=== {label} (回测最近{res["total_tested"]}期, 起始index={start_index}, 总期数={total}) ===')
    print(f'  Top-1 命中率 : {s["avg_top1_hit_rate"]:.2f}%')
    print(f'  Top-3 命中率 : {s["avg_top3_hit_rate"]:.2f}%')
    print(f'  Top-5 命中率 : {s["avg_top5_hit_rate"]:.2f}%')
    print(f'  校准分(Brier): {s["avg_calibration_score"]:.2f}')
    print(f'  综合得分     : {s["avg_overall_score"]:.2f}')
    print(f'  完全命中率   : {s["full_match_rate"]:.2f}%')
    print(f'  趋势方向     : {s["trend_direction"]}')
    return {'stats': s, 'total': total, 'start_index': start_index, 'tested': res['total_tested']}


if __name__ == '__main__':
    tc = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    r = run_current(tc)
    if r:
        import os
        os.makedirs('reports/backtest', exist_ok=True)
        out = {
            'time': datetime.now().isoformat(),
            'test_count': tc,
            'total_history': r['total'],
            'start_index': r['start_index'],
            'tested': r['tested'],
            'stats': r['stats'],
        }
        with open('reports/backtest/opt_baseline.json', 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print('\n结果已保存: reports/backtest/opt_baseline.json')
