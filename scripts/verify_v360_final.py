"""v3.60 最终验证 - GBML 子进程隔离 + 50期回测"""
import sys, json, time
sys.path.insert(0, '.')
from modules.backtester import Backtester
from modules.predictor import P5Predictor
from version import APP_VERSION

print(f'=== v3.60 最终验证 ===')
print(f'版本: {APP_VERSION}')

bt = Backtester(P5Predictor())
t0 = time.time()
r = bt.run_backtest(start_index=60, test_count=50, eval_mode='recent', enable_ai=False)
elapsed = time.time() - t0

s = r.get('overall_stats', {})
total = s.get('total_tested', 0)
t1 = s.get('avg_top1_hit_rate', 0)
t3 = s.get('avg_top3_hit_rate', 0)
t5 = s.get('avg_top5_hit_rate', 0)
trend = s.get('trend_direction', 'N/A')
pos = s.get('position_top1_rates', {})

print(f'\n回测耗时: {elapsed:.1f}s')
print(f'Total: {total} 期')
print(f'Top-1: {t1:.2f}%  (baseline 10%)')
print(f'Top-3: {t3:.2f}%  (baseline 30%)')
print(f'Top-5: {t5:.2f}%  (baseline 50%)')
print(f'Trend: {trend}')
print('Position top1:')
for k, v in pos.items():
    print(f'  {k}: {v:.2f}%')

with open('reports/backtest_v360_final.json', 'w', encoding='utf-8') as f:
    json.dump(r, f, ensure_ascii=False, indent=2, default=str)
print('\n详细结果: reports/backtest_v360_final.json')
