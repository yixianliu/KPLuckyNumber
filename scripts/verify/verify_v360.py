"""v3.60 最终验证 - Walk-Forward 50期回测"""
import sys, json
sys.path.insert(0, '.')
from modules.backtester import Backtester
from modules.predictor import P5Predictor

bt = Backtester(P5Predictor())
r = bt.run_backtest(start_index=60, test_count=50, eval_mode='recent', enable_ai=False)
s = r.get('overall_stats', {})
print('=== v3.60 Walk-Forward 回测（50期）===')
print(f'Total tested: {s.get("total_tested", 0)}')
print(f'Top-1: {s.get("avg_top1_hit_rate", 0):.2f}%  (baseline 10%)')
print(f'Top-3: {s.get("avg_top3_hit_rate", 0):.2f}%  (baseline 30%)')
print(f'Top-5: {s.get("avg_top5_hit_rate", 0):.2f}%  (baseline 50%)')
print(f'Trend: {s.get("trend_direction", "N/A")}')
print('Position top1:')
for k, v in s.get('position_top1_rates', {}).items():
    print(f'  {k}: {v:.2f}%')
