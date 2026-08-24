# -*- coding: utf-8 -*-
"""检查 CHECK 约束实际状态"""
import sys
sys.path.insert(0, r'd:\PythonProject\KPLuckyNumber')
from modules.database import P5Database

db = P5Database()
db.connect()

print("CHECK 约束验证")
print("=" * 60)

# 方法1: SHOW CREATE TABLE 查看约束定义
for table in ['p5_history_data', 'p5_prediction_record']:
    db.cursor.execute(f"SHOW CREATE TABLE `{table}`")
    row = db.cursor.fetchone()
    create_stmt = row['Create Table']
    constraints = []
    for line in create_stmt.split('\n'):
        if 'CHECK' in line.upper() and 'CONSTRAINT' in line.upper():
            constraints.append(line.strip())
    if constraints:
        print(f"\n{table} CHECK 约束:")
        for c in constraints:
            print(f"  ✓ {c}")
    else:
        print(f"\n{table}: 未找到 CHECK 约束定义")

# 方法2: 实际插入测试验证约束生效
print("\n" + "=" * 60)
print("约束生效性测试")
print("=" * 60)

test_cases = [
    ("hezhi范围", "TEST_CHK_001", 1, 2, 3, 4, 5, 999, 4, "hezhi=999应被拒绝"),
    ("span范围", "TEST_CHK_002", 1, 2, 3, 4, 5, 15, 99, "span=99应被拒绝"),
    ("准确率范围", "TEST_CHK_003", "UUID-TEST", "TEST_ISSUE",
     '{"wan":[1,2]}', '[]', '{"wan":0.5}', None, None,
     None, 0, None, 0, 0, 0, 0, 0, None, 150.00, 'pending', None,
     "accuracy_rate=150应被拒绝"),
]

for name, issue, wan, qian, bai, shi, ge, hezhi, span, *rest in test_cases:
    try:
        if name in ("hezhi范围", "span范围"):
            db.cursor.execute(
                "INSERT INTO p5_history_data (issue, wan, qian, bai, shi, ge, hezhi, span) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (issue, wan, qian, bai, shi, ge, hezhi, span)
            )
            print(f"  ✗ {name}: 约束未生效（允许了违法数据）")
            db.cursor.execute("DELETE FROM p5_history_data WHERE issue=%s", (issue,))
        else:
            db.cursor.execute("""
                INSERT INTO p5_prediction_record
                (report_uuid, target_issue, predicted_numbers, predicted_combinations,
                 confidence_scores, actual_numbers, actual_issue, is_matched, match_count,
                 match_details, wan_match, qian_match, bai_match, shi_match, ge_match,
                 deviation_analysis, accuracy_rate, verification_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (*rest[:18],))
            print(f"  ✗ {name}: 约束未生效（允许了违法数据）")
            db.cursor.execute("DELETE FROM p5_prediction_record WHERE target_issue='TEST_ISSUE'")
    except Exception as e:
        err_code = getattr(e, 'args', (None,))[0]
        if err_code == 3819:  # Check constraint violation
            print(f"  ✓ {name}: 约束生效（正确拒绝违法数据）")
        elif err_code == 1062:  # Duplicate entry (测试数据已存在)
            print(f"  ~ {name}: 测试数据已存在，跳过")
        else:
            print(f"  ? {name}: 其他错误 ({err_code}): {e}")

db.disconnect()
print("\n验证完成")
