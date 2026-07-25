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
复活自学习闭环（B 行动续：写库）
=================================
用 v3.12 默认融合配置对最近 N 期真实已开奖做 walk-forward 预测，把每期验证结果
写入数据库，使自适应权重闭环真正生效：

1) p5_prediction_record: 用全新 report_uuid='v3.12-verify-loop' 写入，
   不污染原有的 992 条记录（唯一键 report_uuid+target_issue）。
2) p5_artifact(type='weight_history'): 写入每期 per-algo 命中率 algo_evaluations(0-1)，
   AdaptiveWeightManager.load_from_records 下次启动即回放 → EWMA 累积 → 权重自适应。

per-algo 命中率直接来自 P5Predictor.predict 返回的 per_algo_top_predictions
（每算法每位置 Top-5），与真实开奖逐位置比对，各算法命中率互不相同 → 闭环有区分度。

用法:
  python opt_revive_loop.py --count 10     # 小样本冒烟
  python opt_revive_loop.py --count 120    # 全量复活(默认)
"""
import sys, os, json, argparse, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql
import numpy as np

DB = dict(host='localhost', port=3306, user='root', password='root',
          database='lucky_number', charset='utf8mb4')
POS = ['wan', 'qian', 'bai', 'shi', 'ge']
REPORT_UUID = 'v3.12-verify-loop'


def db_conn():
    return pymysql.connect(**DB, cursorclass=pymysql.cursors.DictCursor)


def load_history():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT issue, wan, qian, bai, shi, ge FROM p5_history_data ORDER BY issue ASC")
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        r['numbers'] = [r['wan'], r['qian'], r['bai'], r['shi'], r['ge']]
    return rows


def revive_loop(history_data, start_index, test_count):
    from modules.predictor import P5Predictor
    p = P5Predictor()
    p.config.config['global']['enable_ai_model'] = False

    n = len(history_data)
    end = min(start_index + test_count, n)
    algo_names = list(p.config.config['algorithms'].keys())

    conn = db_conn()
    cur = conn.cursor()
    written_pred = 0
    written_art = 0
    t0 = time.time()
    tested = 0

    for i in range(start_index, end):
        train = history_data[:i]
        issue = history_data[i]['issue']
        actual = history_data[i]['numbers']
        res = p.predict(train, issue)
        fused = res.get('fused_probabilities', [])
        per_algo = res.get('per_algo_top_predictions', {})
        if not fused or len(fused) != 5 or not per_algo:
            continue

        # 融合 Top-6 生产口径
        pred_top6 = []
        match_count = 0
        pos_match = {}
        for pos in range(5):
            sp = sorted(fused[pos].items(), key=lambda x: x[1], reverse=True)
            top6 = [n for n, _ in sp[:6]]
            pred_top6.append(top6)
            hit = actual[pos] in top6
            pos_match[POS[pos]] = 1 if hit else 0
            if hit:
                match_count += 1

        # 每算法 per-algo 命中率(0-1)
        algo_hits = {}
        algo_evals = {}
        for algo, pos_preds in per_algo.items():
            hp = []
            for pos in range(5):
                pn = pos_preds.get(POS[pos], [])
                if actual[pos] in pn:
                    hp.append(POS[pos])
            hr = len(hp) / 5.0
            algo_hits[algo] = {'hit_positions': hp, 'hit_rate': hr, 'total_positions': 5}
            algo_evals[algo] = round(hr, 4)
        avg_hit_rate = float(np.mean(list(algo_evals.values()))) if algo_evals else 0.0

        # 写入 p5_prediction_record（新 report_uuid，ON DUPLICATE 覆盖同 uuid+issue）
        predicted_numbers = json.dumps({POS[pos]: pred_top6[pos] for pos in range(5)},
                                       ensure_ascii=False)
        combos = res.get('top_combinations', [])[:10]
        predicted_combinations = json.dumps(combos, ensure_ascii=False)
        conf = [float(c.get('confidence', 0)) for c in combos]
        actual_json = json.dumps(actual, ensure_ascii=False)
        acc_rate = round(avg_hit_rate * 100, 2)
        cur.execute(
            """INSERT INTO p5_prediction_record
               (report_uuid, target_issue, predicted_numbers, predicted_combinations,
                confidence_scores, actual_numbers, actual_issue, is_matched, match_count,
                match_details, wan_match, qian_match, bai_match, shi_match, ge_match,
                accuracy_rate, verification_status, verified_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified',NOW())
               ON DUPLICATE KEY UPDATE
                 predicted_numbers=VALUES(predicted_numbers),
                 predicted_combinations=VALUES(predicted_combinations),
                 confidence_scores=VALUES(confidence_scores),
                 actual_numbers=VALUES(actual_numbers),
                 is_matched=VALUES(is_matched), match_count=VALUES(match_count),
                 match_details=VALUES(match_details),
                 wan_match=VALUES(wan_match), qian_match=VALUES(qian_match),
                 bai_match=VALUES(bai_match), shi_match=VALUES(shi_match),
                 ge_match=VALUES(ge_match), accuracy_rate=VALUES(accuracy_rate),
                 verification_status='verified', verified_at=NOW()""",
            (REPORT_UUID, issue, predicted_numbers, predicted_combinations,
             json.dumps(conf, ensure_ascii=False), actual_json, issue,
             1 if match_count > 0 else 0, match_count,
             json.dumps({'match_count': match_count}, ensure_ascii=False),
             pos_match['wan'], pos_match['qian'], pos_match['bai'],
             pos_match['shi'], pos_match['ge'], acc_rate)
        )
        written_pred += 1

        # 写入 p5_artifact(type='weight_history')
        art_data = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'target_issue': issue,
            'predicted_numbers': {POS[pos]: pred_top6[pos] for pos in range(5)},
            'actual_numbers': actual,
            'avg_hit_rate': round(avg_hit_rate, 4),
            'algo_evaluations': algo_evals,
            'algo_hits': algo_hits,
        }
        cur.execute(
            """INSERT INTO p5_artifact (artifact_type, issue, ref_uuid, data_json, created_at)
               VALUES ('weight_history', %s, %s, %s, NOW())""",
            (issue, REPORT_UUID, json.dumps(art_data, ensure_ascii=False))
        )
        written_art += 1
        tested += 1
        if tested % 20 == 0:
            print(f"  进度 {tested}/{end-start_index}  ({(time.time()-t0):.0f}s)")

    conn.commit()
    conn.close()

    print(f"\n=== 自学习闭环复活：写入完成 ===")
    print(f"  测试期数: {tested}")
    print(f"  p5_prediction_record 新增(report_uuid={REPORT_UUID}): {written_pred}")
    print(f"  p5_artifact(weight_history) 新增: {written_art}")
    print(f"  耗时: {round(time.time()-t0,1)}s")

    # 验证闭环加载：新 P5Predictor 启动应自动回放
    print("\n--- 验证闭环加载（新 P5Predictor 实例）---")
    p2 = P5Predictor()
    wm = p2.config.weight_manager
    # 默认 v3.12 权重
    default_w = {a: p2.config.config['algorithms'][a]['weight'] for a in algo_names}
    # 回放后的 EWMA（权重）
    ewma = {a: wm.algo_hit_rates[a]['ewma'] for a in algo_names if a in wm.algo_hit_rates}
    print("  算法        默认权重  回放EWMA(命中率)")
    for a in algo_names:
        dv = default_w.get(a, 0)
        ev = ewma.get(a, 0)
        print(f"    {a:22s} {dv:7.4f}   {ev:7.4f}")
    return {
        'tested': tested, 'written_pred': written_pred, 'written_art': written_art,
        'default_weights': default_w, 'ewma_after_replay': ewma,
        'algo_evals_sample': algo_evals,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=120)
    args = ap.parse_args()
    hist = load_history()
    n = len(hist)
    start = max(0, n - args.count)
    count = min(args.count, n - start)

    print(f"=== 复活自学习闭环（v3.12, 最近 {count} 期, start={start}）===")
    out = revive_loop(hist, start, count)

    os.makedirs('reports/diagnostic', exist_ok=True)
    with open('reports/diagnostic/revive_loop.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已保存 reports/diagnostic/revive_loop.json")


if __name__ == '__main__':
    main()
