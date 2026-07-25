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
A 计划：Top-N 加权命中评估信号验证脚本

目的：验证新的"加权命中分数"是否能比现有"位置覆盖率"在算法间产生更强区分度

核心思想：
- 现有 hit_rate: 只看是否在 Top-6 里，0/0.2/0.4/0.6/0.8/1.0
- 新 weighted_score: 位置命中按排名加权 (Top-1=5, Top-2=4, ..., Top-6=0)，范围 0~25
- 归一化到 0-1 后送入 EWMA，看 7 算法的 ewma 是否产生更大方差

约束：不动生产代码，仅做诊断 + 报告
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql
from modules.predictor import P5Predictor, AdaptiveWeightManager

# === 1. 加载历史数据 ===
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
    rows.reverse()  # 最老在前
    return rows

# === 2. 跑 walk-forward，每期采集每个算法 Top-6 排名 + 命中位置 ===
def collect_per_algo_ranking(history, window=60):
    """对每期调用 P5Predictor.predict，提取各算法的 Top-6 概率分布 + 实际中奖号"""
    rows = []
    n = len(history)
    start = max(0, n - window)
    positions = ['wan', 'qian', 'bai', 'shi', 'ge']

    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False

    algo_names = ['frequency_weighted', 'omission_regression', 'trend_momentum',
                  'markov_transition', 'pattern_continuation', 'bayesian_inference',
                  'feature_engineering']

    for i in range(start, n):
        train = history[:i]
        actual = history[i]['numbers']
        issue = history[i]['issue']
        try:
            res = p.predict(train, issue)
        except Exception as e:
            print(f'[{issue}] predict failed: {e}')
            continue
        per_algo = res.get('algorithm_probs', {})  # {algo: [{0:prob, 1:prob, ...}, ...x 5 positions]}
        if not per_algo:
            continue

        # 提取每算法 Top-6 per position
        algo_top6 = {}
        for algo in algo_names:
            preds = per_algo.get(algo, [])
            if len(preds) < 5:
                algo_top6[algo] = None
                continue
            # preds 长度==5，每项是位置的概率字典 {0:prob, 1:prob, ...}
            algo_top6[algo] = {}
            for pos_idx, pos_dict in enumerate(preds[:5]):
                pos = positions[pos_idx]
                # 排序: (数字, 概率) 按概率降序取 Top-6
                sorted_nums = sorted(pos_dict.items(), key=lambda x: -x[1])[:6]
                algo_top6[algo][pos] = sorted_nums  # [(num, prob), ...]
        rows.append({'issue': issue, 'actual': actual, 'algo_top6': algo_top6})
    return rows

# === 3. 计算两种评估指标 ===
def compute_metrics(rows):
    """对每期、每算法计算：
       - hit_rate: 位置覆盖率 (0-1)
       - weighted_score: Top-1=5, Top-2=4, ..., Top-6=0；归一化到 0-1
       - top1_hit: 第一位是否命中 (0/1)
    """
    algo_names = ['frequency_weighted', 'omission_regression', 'trend_momentum',
                  'markov_transition', 'pattern_continuation', 'bayesian_inference',
                  'feature_engineering']
    positions = ['wan', 'qian', 'bai', 'shi', 'ge']

    metrics = {algo: {'hit_rate': [], 'weighted_score': [], 'top1_hit': []} for algo in algo_names}

    for row in rows:
        algo_top6 = row['algo_top6']
        actual = row['actual']
        for algo in algo_names:
            top6 = algo_top6.get(algo)
            if top6 is None:
                continue
            positions_hit = 0
            weighted = 0
            t1 = 0
            for pi, pos in enumerate(positions):
                if pi >= len(actual):
                    continue
                if pos not in top6:
                    continue
                nums_in_order = [num for num, prob in top6[pos]]  # 按概率降序的数字列表
                act = actual[pi]
                # 找排名
                rank = None
                for rk, num in enumerate(nums_in_order):
                    if num == act:
                        rank = rk
                        break
                if rank is not None:
                    positions_hit += 1
                    # 加权: 排名0=5分，1=4分，...，5=0分
                    weighted += max(0, 5 - rank)
                    if rank == 0:
                        t1 = 1
            metrics[algo]['hit_rate'].append(positions_hit / 5)
            metrics[algo]['weighted_score'].append(weighted / 25)
            metrics[algo]['top1_hit'].append(t1 / 5)
    return metrics

# === 4. 在历史 rankings 上跑 EWMA，对比两种信号的区分度 ===
def ewma_diagnose(metrics):
    """对每种信号，分别跑 EWMA (alpha=0.3)，观察最终 7 算法的 ewma 是否产生更大方差"""
    algo_names = list(metrics.keys())

    # 聚合三种信号
    signals = {
        'hit_rate (现状,0-1覆盖率)': lambda a: metrics[a]['hit_rate'],
        'weighted_score (新,排名加权)': lambda a: metrics[a]['weighted_score'],
        'top1_hit (排名0=1,其他=0)': lambda a: metrics[a]['top1_hit'],
    }

    results = {}
    for signal_name, getter in signals.items():
        alpha = 0.3
        mgr_state = {algo: 0.0 for algo in algo_names}
        for sample_idx in range(len(getter(algo_names[0]))):
            for algo in algo_names:
                vals = getter(algo)
                if sample_idx < len(vals):
                    score = vals[sample_idx]
                    mgr_state[algo] = alpha * score + (1 - alpha) * mgr_state[algo]
        # 归一化得自适应权重
        total = sum(mgr_state.values())
        if total > 0:
            weights = {a: round(v / total, 4) for a, v in mgr_state.items()}
        else:
            weights = {a: 0.0 for a in mgr_state}
        ewma_vals = [v for v in mgr_state.values()]

        # 统计：均值、方差、CV(变异系数)、Top-1 算法、Top-2 算法
        import statistics
        avg = statistics.mean(ewma_vals)
        var = statistics.variance(ewma_vals) if len(ewma_vals) > 1 else 0
        std = var ** 0.5
        cv = std / avg if avg > 1e-9 else 0

        # 各算法均值（用作精度基线）
        algo_means = {}
        for a in algo_names:
            vals = getter(a)
            algo_means[a] = round(sum(vals) / len(vals), 4) if vals else 0

        # 各算法排序（按 EWMA）
        sorted_algos = sorted(weights.items(), key=lambda x: -x[1])

        results[signal_name] = {
            'stats': {'mean': round(avg, 5), 'std': round(std, 5), 'cv': round(cv, 5),
                      'top1_algo': sorted_algos[0][0], 'top1_pct': sorted_algos[0][1],
                      'top2_algo': sorted_algos[1][0], 'top2_pct': sorted_algos[1][1]},
            'weights': weights,
            'algo_means': algo_means,
            'sorted_algos': sorted_algos,
        }
    return results

# === 主流程 ===
if __name__ == '__main__':
    print(f'[{time.strftime("%H:%M:%S")}] 加载最近 120 期历史...')
    history = load_history(120)
    print(f'  共 {len(history)} 期')

    print(f'[{time.strftime("%H:%M:%S")}] walk-forward 收集每算法 Top-6 排名...')
    rows = collect_per_algo_ranking(history, window=60)
    print(f'  共 {len(rows)} 期有效')

    if not rows:
        print('  数据不足，退出')
        sys.exit(1)

    print(f'[{time.strftime("%H:%M:%S")}] 计算三种评估信号...')
    metrics = compute_metrics(rows)

    print(f'[{time.strftime("%H:%M:%S")}] EWMA 诊断对比...\n')
    results = ewma_diagnose(metrics)

    print('=' * 80)
    print('A 计划结论：评估信号区分度对比')
    print('=' * 80)
    print()
    for signal_name, data in results.items():
        s = data['stats']
        print(f'【{signal_name}】')
        print(f'  均值={s["mean"]:.4f}  标准差={s["std"]:.4f}  变异系数(CV)={s["cv"]:.4f}')
        print(f'  EWMA 学习后 Top-1 算法：{s["top1_algo"]} ({s["top1_pct"]:.1%})')
        print(f'  EWMA 学习后 Top-2 算法：{s["top2_algo"]} ({s["top2_pct"]:.1%})')
        print(f'  各算法 EWMA 均值排名：')
        for algo, pct in data['sorted_algos'][:3]:
            mean = data['algo_means'][algo]
            print(f'    - {algo:25s}  EWMA归一化={pct:.1%}  原始均值={mean:.4f}')
        print()

    # 保存结果供后续报告使用
    out = {
        'window': 60,
        'periods': len(rows),
        'metrics': {
            algo: {
                'hit_rate_avg': sum(metrics[algo]['hit_rate']) / len(metrics[algo]['hit_rate']) if metrics[algo]['hit_rate'] else 0,
                'weighted_score_avg': sum(metrics[algo]['weighted_score']) / len(metrics[algo]['weighted_score']) if metrics[algo]['weighted_score'] else 0,
                'top1_hit_avg': sum(metrics[algo]['top1_hit']) / len(metrics[algo]['top1_hit']) if metrics[algo]['top1_hit'] else 0,
            }
            for algo in metrics
        },
        'ewma_results': {
            signal_name: {
                'stats': data['stats'],
                'weights': data['weights'],
            }
            for signal_name, data in results.items()
        }
    }
    out_path = os.path.join('reports', 'diagnostic', 'plan_a_top1_weighted_signal.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'结果已保存到 {out_path}')

    # 决策辅助
    cv_old = results['hit_rate (现状,0-1覆盖率)']['stats']['cv']
    cv_new = results['weighted_score (新,排名加权)']['stats']['cv']
    cv_t1 = results['top1_hit (排名0=1,其他=0)']['stats']['cv']
    print()
    print('=' * 80)
    print('【A 计划可行性判定】')
    print('=' * 80)
    print(f'现状 hit_rate CV       = {cv_old:.4f}  (CV 越大 = 算法间差异越大 = EWMA 越能学)')
    print(f'新 weighted_score CV  = {cv_new:.4f}  '
          f'{"↑改进" if cv_new > cv_old * 1.2 else "≈一致" if cv_new > cv_old * 0.8 else "↓变差"}')
    print(f'新 top1_hit CV         = {cv_t1:.4f}  '
          f'{"↑改进" if cv_t1 > cv_old * 1.2 else "≈一致" if cv_t1 > cv_old * 0.8 else "↓变差"}')
    print()
    if cv_t1 > cv_old * 1.5:
        print('✅ 强烈推荐落地 top1_hit 作为新的 EWMA 评估信号')
    elif cv_t1 > cv_old * 1.1:
        print('✓  top1_hit 有提升，可考虑落地')
    else:
        print('✗  top1_hit 区分度提升有限，建议放弃')
    print()

    print('【A 计划落地方案 】')
    print('  把 AdaptiveWeightManager.record_verification(algo, hit_rate) 的 hit_rate 参数')
    print('  从"位置覆盖率"(0~1,5位)改为"Top-1精准度"(0~1,只看最高概率名是否中)。')
    print('  pipeline._calculate_algorithm_hits 同步更新:')
    print('    新增 inv_rank_score 字段 = Σ rank_top1(命中第k位→得 6-k 分) / (5*6) × 调整系数')
    print('    或更简单: 改为 top1_correct_rate = (top1命中位数/5)')
    print('  ★ 推荐最简方案: 直接把 hit_rate 改为 inv_rank_weighted(5->1) 排序加权')
