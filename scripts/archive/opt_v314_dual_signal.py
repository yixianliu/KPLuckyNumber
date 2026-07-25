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
v3.14 双信号 walk-forward 真实对比

设计：
- 共 N=80 期，前 LEARN 期(默认30)只注入 record_verification 不评估，后 EVAL 期(50)只评估不学习
- 三种配置在相同历史数据 + 相同 LEARN 流程下公平对比：
    A: enable_adaptive_weights=False        (v3.13 静态基线)
    B: enable_adaptive_weights=True, metric='top1_hit' (v3.14 新)
    C: enable_adaptive_weights=True, metric='hit_rate' (回退测试)
- 注入的信号 per-algo 真实反馈：每个算法的 Top-1 精准度 + 覆盖率分别计算后喂入

这是 v3.14 落地后的"判决实验"，决定是否真正"开启"自适应。
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymysql
from modules.predictor import P5Predictor
import numpy as np


def load_history(n_periods=120):
    conn = pymysql.connect(host='localhost', port=3306, user='root', password='root',
                           database='lucky_number', charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute('SELECT issue, wan, qian, bai, shi, ge FROM p5_history_data ORDER BY issue DESC LIMIT %s', (n_periods,))
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        r['numbers'] = [r['wan'], r['qian'], r['bai'], r['shi'], r['ge']]
    rows.reverse()
    return rows


def evaluate_one(predictor: P5Predictor, history, start, end):
    """对 [start, end) 区间跑预测并统计 Top-1/3/5/6 + match_count"""
    pos_hit = [0]*5
    t3 = t5 = t6 = 0
    mc_list = []
    tested = 0
    for i in range(start, end):
        train = history[:i]
        actual = history[i]['numbers']
        try:
            res = predictor.predict(train, history[i]['issue'])
        except Exception:
            continue
        fp = res.get('fused_probabilities', [])
        if not fp or len(fp) != 5:
            continue
        tested += 1
        mc = 0
        for pos in range(5):
            sn = sorted(fp[pos].items(), key=lambda x: -x[1])  # 概率降序
            nums = [num for num, _ in sn]
            if sn[0][0] == actual[pos]:
                pos_hit[pos] += 1
                mc += 1
            if actual[pos] in nums[:3]: t3 += 1
            if actual[pos] in nums[:5]: t5 += 1
            if actual[pos] in nums[:6]: t6 += 1
        mc_list.append(mc)
    if tested == 0:
        return None
    return {
        'n': tested,
        'top1': round(sum(pos_hit) / tested / 5 * 100, 2),
        'top3': round(t3 / tested / 5 * 100, 2),
        'top5': round(t5 / tested / 5 * 100, 2),
        'top6': round(t6 / tested / 5 * 100, 2),
        'avg_match_count': round(sum(mc_list) / tested, 3),
    }


def learn_one_round(predictor: P5Predictor, history, idx):
    """对第 idx 期计算每个算法的 per-algo Top-1+覆盖率 信号，喂入 weight_manager"""
    train = history[:idx]
    actual = history[idx]['numbers']
    try:
        res = predictor.predict(train, history[idx]['issue'])
    except Exception:
        return False
    algo_names = ['frequency_weighted', 'omission_regression', 'trend_momentum',
                  'markov_transition', 'pattern_continuation', 'bayesian_inference',
                  'feature_engineering']
    per_algo = res.get('algorithm_probs', {})
    if not per_algo:
        return False
    for algo in algo_names:
        preds = per_algo.get(algo, [])
        if len(preds) < 5: continue
        t1_count = 0  # Top-1 命中位置数
        cov_count = 0  # 在 Top-6 范围的位置数
        for pos_idx, pos_dict in enumerate(preds[:5]):
            if pos_idx >= len(actual):
                continue
            sorted_nums = sorted(pos_dict.items(), key=lambda x: -x[1])[:6]
            nums = [num for num, _ in sorted_nums]
            act = actual[pos_idx]
            if nums and nums[0] == act: t1_count += 1
            if act in nums: cov_count += 1
        top1_hit = t1_count / 5.0
        cov_rate = cov_count / 5.0
        predictor.config.weight_manager.record_verification(algo, cov_rate, top1_hit=top1_hit)
    return True


def main(learn_periods=30, eval_periods=50, n_total=140):
    history = load_history(n_total)
    n = len(history)
    print(f'[加载] 数据库共 {n} 期')
    total_window = learn_periods + eval_periods
    start = max(0, n - total_window)
    learn_start = start
    learn_end = start + learn_periods
    eval_start = learn_end
    eval_end = start + total_window
    print(f'[区间] 学习期[{learn_start},{learn_end})={learn_periods} 评测期[{eval_start},{eval_end})={eval_periods}')

    eval_modes = {
        'A. 静态(v3.13 基线)': dict(enable_adaptive_weights=False, enable_ai_model=False),
        'B. 自适应-top1_hit(v3.14 新)': dict(enable_adaptive_weights=True, adaptive_metric='top1_hit', enable_ai_model=False),
        'C. 自适应-hit_rate(回退测试)': dict(enable_adaptive_weights=True, adaptive_metric='hit_rate', enable_ai_model=False),
    }

    all_results = {}
    for mode_name, overrides in eval_modes.items():
        p = P5Predictor()
        for k, v in overrides.items():
            p.config.config['global'][k] = v

        # 阶段1: 学习期 (warm-up)
        learn_count = 0
        for i in range(learn_start, learn_end):
            if learn_one_round(p, history, i):
                learn_count += 1

        # 阶段2: 评测期
        eval_res = evaluate_one(p, history, eval_start, eval_end)
        if eval_res is None:
            all_results[mode_name] = {'learn_count': learn_count, 'eval': 'NO DATA'}
            continue

        final_weights = p.config.get_algorithm_weights()
        all_results[mode_name] = {
            'learn_count': learn_count,
            'eval': eval_res,
            'final_weights': {k: round(v, 4) for k, v in final_weights.items()},
            'final_ewma_state': {
                algo: {
                    'ewma': round(rec['ewma'], 4),
                    'ewma_t1': round(rec['ewma_t1'], 4),
                    't1_total': rec['t1_total'],
                    't1_hits': round(rec['t1_hits'], 3),
                }
                for algo, rec in p.config.weight_manager.algo_hit_rates.items()
            }
        }

    # 打印汇总
    print('\n' + '=' * 88)
    print('v3.14 双信号自适应 vs v3.13 静态基线 — Walk-forward 真实对比')
    print('=' * 88)
    print(f"{'模式':<35} {'Top-1':>7} {'Top-3':>7} {'Top-5':>7} {'Top-6':>7} {'avg_mc':>7} {'n':>5}")
    print('-' * 88)
    for mode_name, r in all_results.items():
        ie = r['eval']
        if ie == 'NO DATA':
            print(f'{mode_name:<35} (无数据)')
            continue
        print(f"{mode_name:<35} {ie['top1']:>7}% {ie['top3']:>7}% {ie['top5']:>7}% "
              f"{ie['top6']:>7}% {ie['avg_match_count']:>7} {ie['n']:>5}")

    # 保存
    out_path = os.path.join('reports', 'diagnostic', 'v314_dual_signal_compare.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f'\n详细结果已保存: {out_path}')

    # 决策建议
    print('\n=== 决策辅助 ===')
    base_t1 = all_results['A. 静态(v3.13 基线)']['eval']['top1']
    new_t1 = all_results['B. 自适应-top1_hit(v3.14 新)']['eval']['top1']
    diff = round(new_t1 - base_t1, 2)
    print(f'Top-1 差值(B-A): {diff:+.2f}%')
    base_t6 = all_results['A. 静态(v3.13 基线)']['eval']['top6']
    new_t6 = all_results['B. 自适应-top1_hit(v3.14 新)']['eval']['top6']
    diff6 = round(new_t6 - base_t6, 2)
    print(f'Top-6 差值(B-A): {diff6:+.2f}%')
    if new_t1 > base_t1 + 0.5:
        print('✅ Top-1 显著提升, 推荐默认开启 v3.14 双信号自适应')
    elif new_t1 >= base_t1 - 0.2:
        print('✓  Top-1 与基线持平或微优, 自适应启用安全')
    else:
        print('❌ Top-1 退化, 不要开自适应, 退回 v3.13 静态')


if __name__ == '__main__':
    print(time.strftime('[%H:%M:%S] 启动'))
    main(learn_periods=30, eval_periods=50, n_total=120)
    print(time.strftime('[%H:%M:%S] 完成'))
