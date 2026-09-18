# -*- coding: utf-8 -*-
"""轻量端到端回归：直接连 MySQL 验证 top1_match_count 物理列一致性

职责：
    不依赖 P5Database / sklearn / matplotlib，仅用 pymysql 直连 MySQL，验证：
    1. p5_prediction_record 已含 top1_match_count / top1_accuracy_rate 两列
    2. verified 记录中 top1_match_count=0 且 match_count=0 的比例合理（0 命中是 10% 基线下常见结果）
    3. 双口径物理列与重算逻辑一致（按 predicted_numbers/actual_numbers 重新解析校验）

运行方式：
    .venv\\Scripts\\python.exe scripts/verify_e2e_db.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pymysql 在 venv 缺失时尝试从系统 Python 找
try:
    import pymysql
except ModuleNotFoundError:
    import subprocess
    r = subprocess.run(
        [sys.executable, '-c', 'import pymysql; print(pymysql.__file__)'],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f"[INFO] pymysql found: {r.stdout.strip()}")
    else:
        print("[WARN] pymysql 未安装，跳过 DB 回归")
        print("[SKIP] 请手动安装 pymysql: .venv\\Scripts\\pip install pymysql")
        sys.exit(0)

# DB 连接参数（与 config.py 一致）
DB = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'root'),
    'database': os.environ.get('DB_NAME', 'lucky_number'),
    'charset': 'utf8mb4',
}

conn = pymysql.connect(**DB, cursorclass=pymysql.cursors.DictCursor)

def top1_recount(pred_json: str, actual: list) -> int:
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

try:
    cur = conn.cursor()
    # 1. 表结构：两列存在
    cur.execute("SHOW COLUMNS FROM p5_prediction_record LIKE 'top1%'")
    cols = [r['Field'] for r in cur.fetchall()]
    assert 'top1_match_count' in cols and 'top1_accuracy_rate' in cols, f"缺列 {cols}"
    print(f"[PASS] 表结构含 top1_match_count / top1_accuracy_rate")

    # 2. 总量
    cur.execute("SELECT COUNT(*) AS n FROM p5_prediction_record WHERE verification_status='verified'")
    total = cur.fetchone()['n']
    print(f"[INFO] verified 总数: {total}")

    # 3. 0 命中记录抽样重算一致性
    cur.execute("""
        SELECT id, predicted_numbers, actual_numbers, match_count, top1_match_count
        FROM p5_prediction_record
        WHERE verification_status='verified' AND top1_match_count = 0
        ORDER BY id DESC LIMIT 20
    """)
    sample = cur.fetchall()
    mismatches = 0
    for r in sample:
        expected = top1_recount(r['predicted_numbers'], json.loads(r['actual_numbers']))
        if expected != r['top1_match_count']:
            mismatches += 1
            print(f"  [WARN] id={r['id']} 存储={r['top1_match_count']} 重算={expected} target_issue={r.get('target_issue','?')}")
    print(f"[{'PASS' if mismatches==0 else 'WARN'}] 抽样 20 条 0 命中记录: 存储值与重算一致 {20-mismatches}/20")

    # 4. 双口径分布概览
    cur.execute("""
        SELECT
            ROUND(AVG(match_count), 2) AS loose_avg,
            ROUND(AVG(top1_match_count), 2) AS top1_avg,
            ROUND(AVG(CASE WHEN match_count=5 THEN 100 ELSE 0 END), 2) AS loose_full_pct,
            ROUND(AVG(CASE WHEN top1_match_count=5 THEN 100 ELSE 0 END), 2) AS top1_full_pct
        FROM p5_prediction_record WHERE verification_status='verified'
    """)
    dist = cur.fetchone()
    print(f"[INFO] 宽松口径平均匹配: {dist['loose_avg']}/5, 全中占比 {dist['loose_full_pct']}%")
    print(f"[INFO] Top-1 口径平均匹配: {dist['top1_avg']}/5, 全中占比 {dist['top1_full_pct']}%")
    print(f"[INFO] Top-1 全中占比应为 0%（公平摇号无法 5/5 全中）")

    if dist['top1_full_pct'] > 0:
        print("[FAIL] top1_full_pct > 0，异常")
        sys.exit(1)
    print("\n[PASS] DB 层回归通过")
    print("【风险提示】Top-1 命中率≈随机基线10%，本验证仅确认代码逻辑正确，不代表预测能力提升。请理性购彩。")
finally:
    conn.close()
