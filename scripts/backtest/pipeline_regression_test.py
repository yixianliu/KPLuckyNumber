# -*- coding: utf-8 -*-
"""完整流水线回归测试脚本（P5 Pipeline Regression Test）

职责：
    验证 v3.67 三阶段修复在真实 DB 环境下的全链路正确性：
    1. DB 层一致性：p5_prediction_record 双口径物理列 + 重算口径一致
    2. 验证闭环：update_prediction_verification 正确写入 Top-1 + 宽松双口径
    3. 统计层：get_verification_stats 双口径字段完整，Top-1 ≈ 随机基线
    4. 完整流水线：run_four_step_pipeline 预测→入库→验证 端到端

模式：
    默认模式（无参数）       ：仅 DB 层一致性校验（安全，不触发 AI）
    --verify [issue]        ：验证指定待验证期号（需 DB 已有开奖数据）
    --run-pipeline [issue]  ：完整流水线 预测+入库+验证（需 AI 接口）

运行方式（系统 Python 含 pymysql）：
    python scripts/pipeline_regression_test.py
    python scripts/pipeline_regression_test.py --verify 2026248
    python scripts/pipeline_regression_test.py --run-pipeline

【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果。
本脚本仅验证代码逻辑正确性，不代表预测能力提升。请理性购彩，量力而行。
"""
import sys, os, json, time, argparse

# 项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# DB 连接参数（与 config.py 一致，系统 Python 读取 .env 或默认值）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
except Exception:
    pass

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'root'),
    'database': os.environ.get('DB_NAME', 'lucky_number'),
    'charset': 'utf8mb4',
}

# 检查 pymysql
try:
    import pymysql
    from pymysql.cursors import DictCursor
except ModuleNotFoundError:
    print("[FAIL] 系统 Python 缺少 pymysql，无法连 DB")
    print("  安装: python -m pip install pymysql")
    sys.exit(1)

PASS = 0
FAIL = 0

def check(desc: str, condition: bool, detail: str = ''):
    """检查一项，累计 PASS/FAIL"""
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {desc}")
    else:
        FAIL += 1
        print(f"  [FAIL] {desc}  {detail}")

def top1_recount(pred_json, actual: list) -> int:
    """按 Top-1 精确口径重算：pred[pos][0] == actual[i] 才命中"""
    pred = json.loads(pred_json) if pred_json else {}
    actual = [int(x) for x in actual]
    cnt = 0
    for i, pos in enumerate(['wan', 'qian', 'bai', 'shi', 'ge']):
        nums = pred.get(pos, [])
        if isinstance(nums, dict):
            nums = nums.get('numbers', [])
        if nums and int(nums[0]) == actual[i]:
            cnt += 1
    return cnt

def connect_db():
    """连接 MySQL 并创建 DictCursor"""
    conn = pymysql.connect(**DB_CONFIG, cursorclass=DictCursor)
    return conn

# ═══════════════════════════════════════════════════════════
# Phase 1: DB 层一致性校验
# ═══════════════════════════════════════════════════════════
def test_db_consistency():
    """校验 p5_prediction_record 双口径物理列 + 重算口径一致性"""
    print("\n═══ Phase 1: DB 层一致性校验 ═══")
    conn = connect_db()
    try:
        cur = conn.cursor()

        # 1.1 表结构含两列
        cur.execute("SHOW COLUMNS FROM p5_prediction_record LIKE 'top1%'")
        cols = {r['Field'] for r in cur.fetchall()}
        check("p5_prediction_record 含 top1_match_count 列", 'top1_match_count' in cols, str(cols))
        check("p5_prediction_record 含 top1_accuracy_rate 列", 'top1_accuracy_rate' in cols, str(cols))

        # 1.2 总量与分布
        cur.execute("""
            SELECT COUNT(*) AS total,
                   ROUND(AVG(top1_match_count), 2) AS top1_avg,
                   SUM(CASE WHEN top1_match_count=5 THEN 1 ELSE 0 END) AS top1_full,
                   ROUND(AVG(match_count), 2) AS loose_avg
            FROM p5_prediction_record WHERE verification_status='verified'
        """)
        dist = cur.fetchone()
        check("verified 记录总数 > 1000", dist['total'] > 1000, f"total={dist['total']}")
        check("Top-1 全中记录数 = 0（公平摇号）", dist['top1_full'] == 0, f"top1_full={dist['top1_full']}")
        check(f"Top-1 平均匹配 < 1.0（随机基线 0.5）", (dist['top1_avg'] or 0) < 1.0, f"top1_avg={dist['top1_avg']}")
        check(f"宽松口径平均匹配 ≈ 4.0（旧展示虚高）", 3.5 <= (dist['loose_avg'] or 0) <= 4.5, f"loose_avg={dist['loose_avg']}")

        # 1.3 抽样 20 条重算一致性
        cur.execute("""
            SELECT predicted_numbers, actual_numbers, top1_match_count
            FROM p5_prediction_record WHERE verification_status='verified'
            ORDER BY RAND() LIMIT 20
        """)
        sample = cur.fetchall()
        mismatches = 0
        for r in sample:
            actual = json.loads(r['actual_numbers']) if r['actual_numbers'] else []
            if not actual:
                continue
            expected = top1_recount(r['predicted_numbers'], actual)
            if expected != int(r['top1_match_count']):
                mismatches += 1
        check(f"抽样 20 条 Top-1 重算一致性（失配 {mismatches}/20）", mismatches == 0, f"mismatches={mismatches}")

        # 1.4 验证统计层双口径字段
        from modules.database import P5Database
        db = P5Database()
        if not db.connect():
            check("P5Database 连接成功", False, "connect 失败")
            return
        stats = db.get_verification_stats()
        db.disconnect()
        check("get_verification_stats 返回非空", bool(stats), "stats 为空")
        for key in ['top1_avg_accuracy', 'top1_full_matches', 'top1_wan_accuracy',
                     'top1_qian_accuracy', 'top1_bai_accuracy', 'top1_shi_accuracy', 'top1_ge_accuracy',
                     'strict_avg_accuracy', 'strict_full_matches']:
            check(f"统计字段 {key} 存在", key in stats, f"缺失 {key}")
        if stats:
            top1_avg_acc = stats.get('top1_avg_accuracy', 0)
            check(f"Top-1 avg_accuracy ≈ 随机基线 10%（当前 {top1_avg_acc}%）",
                  5 <= top1_avg_acc <= 15, f"top1_avg_accuracy={top1_avg_acc}")
            check(f"宽松 avg_accuracy ≈ 80%（当前 {stats.get('avg_accuracy',0)}%）",
                  70 <= stats.get('avg_accuracy', 0) <= 90, f"avg_accuracy={stats.get('avg_accuracy')}")
    finally:
        conn.close()

# ═══════════════════════════════════════════════════════════
# Phase 2: 验证闭环（update_prediction_verification）
# ═══════════════════════════════════════════════════════════
def test_verification_loop(target_issue: str = None):
    """验证指定期号的 update_prediction_verification 闭环正确性"""
    print(f"\n═══ Phase 2: 验证闭环（目标期号: {target_issue or 'auto'}）═══")
    conn = connect_db()
    try:
        cur = conn.cursor()

        # 2.1 找到 pending 记录
        if target_issue:
            cur.execute(
                "SELECT report_uuid, target_issue FROM p5_prediction_record "
                "WHERE verification_status='pending' AND target_issue=%s",
                (target_issue,)
            )
        else:
            cur.execute(
                "SELECT report_uuid, target_issue FROM p5_prediction_record "
                "WHERE verification_status='pending' ORDER BY target_issue DESC LIMIT 1"
            )
        row = cur.fetchone()
        if not row:
            check("找到待验证预测记录", False, f"无 pending 记录（target_issue={target_issue}）")
            print("  [SKIP] 无待验证记录，跳过验证闭环")
            return

        report_uuid = row['report_uuid']
        target_issue = row['target_issue']
        print(f"  [INFO] 待验证记录: target_issue={target_issue}, report_uuid={report_uuid}")

        # 2.2 检查开奖数据是否存在
        cur.execute("SELECT wan, qian, bai, shi, ge FROM p5_history_data WHERE issue=%s", (target_issue,))
        actual_row = cur.fetchone()
        if not actual_row:
            check(f"开奖数据 {target_issue} 已入库", False,
                  "库中无此期开奖数据——请先抓取或手动插入，再重跑验证")
            print("  [SKIP] 开奖数据尚未入库，验证闭环无法执行")
            return
        actual_numbers = [actual_row['wan'], actual_row['qian'], actual_row['bai'],
                          actual_row['shi'], actual_row['ge']]
        print(f"  [INFO] 开奖号码: {actual_numbers}")

        # 2.3 调用 update_prediction_verification
        from modules.database import P5Database
        db = P5Database()
        if not db.connect():
            check("P5Database 连接成功", False)
            return
        result = db.update_prediction_verification(
            report_uuid=report_uuid,
            target_issue=target_issue,
            actual_numbers=actual_numbers,
            actual_issue=target_issue,
        )
        db.disconnect()
        check("update_prediction_verification 返回 success", result.get('status') == 'success',
              f"result={result}")

        if result.get('status') != 'success':
            return

        # 2.4 校验返回的双口径
        check("返回值含 top1_match_count", 'top1_match_count' in result, f"result keys={list(result.keys())}")
        check("返回值含 top1_accuracy_rate", 'top1_accuracy_rate' in result, f"result keys={list(result.keys())}")

        # 2.5 重算一致性
        pred_recount = top1_recount(json.dumps({
            'wan': [], 'qian': [], 'bai': [], 'shi': [], 'ge': []
        }), actual_numbers)  # 占位，真实预测在下一步取
        # 从 DB 取 predicted_numbers 重算
        cur.execute("SELECT predicted_numbers FROM p5_prediction_record "
                    "WHERE report_uuid=%s AND target_issue=%s",
                    (report_uuid, target_issue))
        pred_row = cur.fetchone()
        pred = json.loads(pred_row['predicted_numbers']) if pred_row and pred_row['predicted_numbers'] else {}
        expected_top1 = top1_recount(json.dumps(pred), actual_numbers)
        check(f"Top-1 命中 {result.get('top1_match_count')}/5 与重算 {expected_top1}/5 一致",
              result.get('top1_match_count') == expected_top1,
              f"stored={result.get('top1_match_count')} recalced={expected_top1}")

        # 2.6 DB 验证状态更新
        cur.execute("SELECT verification_status, top1_match_count, top1_accuracy_rate "
                    "FROM p5_prediction_record WHERE report_uuid=%s AND target_issue=%s",
                    (report_uuid, target_issue))
        verified_row = cur.fetchone()
        check("DB verification_status 已更新为 verified",
              verified_row and verified_row['verification_status'] == 'verified',
              f"status={verified_row['verification_status'] if verified_row else 'NULL'}")

        # 2.7 验证明细写入
        cur.execute("SELECT COUNT(*) AS cnt FROM p5_verification_detail "
                    "WHERE issue=%s", (target_issue,))
        detail_cnt = cur.fetchone()['cnt']
        check("p5_verification_detail 已写入 5 条明细", detail_cnt >= 5, f"detail_cnt={detail_cnt}")

    finally:
        conn.close()

# ═══════════════════════════════════════════════════════════
# Phase 3: 完整流水线（预测 + 入库 + 验证）
# ═══════════════════════════════════════════════════════════
def test_full_pipeline(target_issue: str = None):
    """完整流水线 run_four_step_pipeline 端到端"""
    print(f"\n═══ Phase 3: 完整流水线（目标期号: {target_issue or 'auto'}）═══")

    if target_issue is None:
        # 自动推导：从 DB 最新期号 + 1
        conn = connect_db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT MAX(issue) AS max_issue FROM p5_history_data")
            row = cur.fetchone()
            if row and row['max_issue']:
                target_issue = str(int(row['max_issue']) + 1)
        finally:
            conn.close()
        print(f"  [INFO] 自动推导目标期号: {target_issue}")

    print(f"  [INFO] 启动 run_four_step_pipeline(target_issue={target_issue}, "
          f"include_backtest=False, include_feature_analysis=False)")
    print("  [INFO] 此步会调用 AI 接口（AGNES API），耗时约 3-5 分钟")
    print("  [INFO] 开始执行...")

    try:
        from modules.pipeline import run_four_step_pipeline
        start = time.time()
        result = run_four_step_pipeline(
            target_issue=target_issue,
            data_limit=60,
            include_backtest=False,
            include_feature_analysis=False,
            max_bayes_aux_calls=2,
        )
        elapsed = time.time() - start
        print(f"  [INFO] 流水线完成，耗时 {elapsed:.1f}s")
    except Exception as e:
        print(f"  [FAIL] 流水线异常: {e}")
        import traceback
        traceback.print_exc()
        return

    check("流水线返回 success", result.get('success', False), f"result.success={result.get('success')}, error={result.get('error')}")

    if not result.get('success'):
        return

    # 3.1 校验预测入库
    conn = connect_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT report_uuid, target_issue, predicted_numbers, verification_status "
                    "FROM p5_prediction_record WHERE target_issue=%s ORDER BY id DESC LIMIT 1",
                    (target_issue,))
        pred_row = cur.fetchone()
        check("预测记录已入库 p5_prediction_record", bool(pred_row), "无该期号记录")
        if pred_row:
            check("预测记录含 predicted_numbers", bool(pred_row['predicted_numbers']),
                  "predicted_numbers 为空")
            print(f"  [INFO] 预测记录: report_uuid={pred_row['report_uuid']}, status={pred_row['verification_status']}")

            # 3.2 如果该期已开奖，验证双口径
            cur.execute("SELECT wan, qian, bai, shi, ge FROM p5_history_data WHERE issue=%s", (target_issue,))
            actual_row = cur.fetchone()
            if actual_row:
                actual_numbers = [actual_row['wan'], actual_row['qian'], actual_row['bai'],
                                   actual_row['shi'], actual_row['ge']]
                print(f"  [INFO] 该期已开奖: {actual_numbers}")
                print(f"  [INFO] 验证闭环由 --verify {target_issue} 单独执行")
            else:
                print(f"  [INFO] 该期尚未开奖，预测记录保持 pending 状态")
    finally:
        conn.close()

# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description='P5 完整流水线回归测试脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='模式说明:\n'
               '  默认（无参数）    Phase1 DB层一致性\n'
               '  --verify [issue] Phase1+Phase2 验证闭环\n'
               '  --run-pipeline   Phase1+Phase3 完整流水线（需AI）\n'
               '  --full           Phase1+Phase2+Phase3 全部'
    )
    parser.add_argument('--verify', nargs='?', const='', default=None,
                        help='验证指定期号（不传则自动取最新 pending）')
    parser.add_argument('--run-pipeline', action='store_true',
                        help='执行完整流水线（需 AI 接口）')
    parser.add_argument('--full', action='store_true',
                        help='执行全部 Phase（DB + 验证 + 流水线）')
    args = parser.parse_args()

    print("=" * 60)
    print("P5 Pipeline Regression Test — v3.67 命中率口径修复回归")
    print("=" * 60)
    print(f"DB: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    print(f"目标: verify={args.verify or 'auto'}, pipeline={args.run_pipeline or args.full}")

    # Phase 1: 始终执行（安全）
    test_db_consistency()

    if args.verify is not None or args.full:
        test_verification_loop(args.verify if args.verify else None)

    if args.run_pipeline or args.full:
        test_full_pipeline(args.verify if args.verify else None)

    # 汇总
    print("\n" + "=" * 60)
    print(f"回归测试结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 60)
    if FAIL == 0:
        print("[PASS] 全部通过 ✓")
    else:
        print(f"[FAIL] {FAIL} 项失败，请检查上方日志")

    print("\n【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果。")
    print("本脚本仅验证代码逻辑正确性，不代表预测能力提升。请理性购彩，量力而行。")

    sys.exit(0 if FAIL == 0 else 1)

if __name__ == '__main__':
    main()
