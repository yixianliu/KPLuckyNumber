# -*- coding: utf-8 -*-
"""启用 CHECK 约束 enforcement"""
import sys
sys.path.insert(0, r'd:\PythonProject\KPLuckyNumber')
from modules.database import P5Database

db = P5Database()
db.connect()

# 检查 innodb_check_constraints 状态
db.cursor.execute("SHOW VARIABLES LIKE 'innodb_check_constraints'")
rows = db.cursor.fetchall()
if rows:
    val = rows[0]['Value'] if isinstance(rows[0], dict) else rows[0][1]
    print(f"innodb_check_constraints: {val}")
else:
    print("innodb_check_constraints: 变量不存在（MySQL 8.0.12 默认 OFF）")
    val = "OFF"

# MySQL 8.0.12 的 CHECK 约束需要 innodb_check_constraints=ON
# 但该变量是只读的，需要通过 my.ini 配置
print("\n方案: 在应用层添加数据校验（替代数据库级 CHECK 约束）")
print("=" * 60)

# 测试当前约束状态
test_issue = "TEST_CHK_FINAL"
print("\n约束生效性测试:")

# hezhi 测试
try:
    db.cursor.execute(
        "INSERT INTO p5_history_data (issue, wan, qian, bai, shi, ge, hezhi, span) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (test_issue, 1, 2, 3, 4, 5, 999, 4)
    )
    print("  ✗ hezhi 范围约束未生效（允许了 and值=999）")
    db.cursor.execute("DELETE FROM p5_history_data WHERE issue=%s", (test_issue,))
except Exception as e:
    err_code = getattr(e, 'args', (None,))[0]
    if err_code == 3819:
        print("  ✓ hezhi 范围约束生效")
    else:
        print(f"  ? 错误 ({err_code})")

# span 测试
try:
    db.cursor.execute(
        "INSERT INTO p5_history_data (issue, wan, qian, bai, shi, ge, hezhi, span) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (test_issue + "_2", 1, 2, 3, 4, 5, 15, 99)
    )
    print("  ✗ span 范围约束未生效（允许了跨度=99）")
    db.cursor.execute("DELETE FROM p5_history_data WHERE issue=%s", (test_issue + "_2",))
except Exception as e:
    err_code = getattr(e, 'args', (None,))[0]
    if err_code == 3819:
        print("  ✓ span 范围约束生效")
    else:
        print(f"  ? 错误 ({err_code})")

db.disconnect()
print("\n建议: 如需完全启用 CHECK 约束，请在 my.ini 中添加:")
print("  [mysqld]")
print("  innodb_check_constraints=ON")
print("然后重启 MySQL 服务")
