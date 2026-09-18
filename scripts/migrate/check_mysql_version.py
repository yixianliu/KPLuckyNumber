# -*- coding: utf-8 -*-
"""检查 MySQL 版本并重新添加 CHECK 约束"""
import sys
sys.path.insert(0, r'd:\PythonProject\KPLuckyNumber')
from modules.database import P5Database

db = P5Database()
db.connect()

# 检查 MySQL 版本
db.cursor.execute("SELECT VERSION()")
version = db.cursor.fetchone()['VERSION()']
print(f"MySQL 版本: {version}")

# MySQL 5.7 不支持 CHECK 约束（仅解析但忽略）
# MySQL 8.0.16+ 才真正 enforce CHECK 约束
parts = version.split('.')
major = int(parts[0]) if parts else 0
minor = int(parts[1]) if len(parts) > 1 else 0

if major >= 8 and minor >= 16:
    print("MySQL 8.0.16+，CHECK 约束应生效")
elif major >= 8:
    print("MySQL 8.x 早期版本，CHECK 约束可能部分支持")
else:
    print("MySQL 5.7，CHECK 约束仅解析不执行（应用层校验）")

# 尝试重新添加约束
print("\n重新添加 CHECK 约束...")
constraints = [
    ("p5_history_data", "chk_hezhi_range", "CHECK (`hezhi` >= 0 AND `hezhi` <= 45)"),
    ("p5_history_data", "chk_span_range", "CHECK (`span` >= 0 AND `span` <= 9)"),
    ("p5_prediction_record", "chk_accuracy_rate", "CHECK (`accuracy_rate` >= 0 AND `accuracy_rate` <= 100)"),
]

for table, name, expr in constraints:
    try:
        db.cursor.execute(f"ALTER TABLE `{table}` ADD CONSTRAINT `{name}` {expr}")
        print(f"  ✓ {table}.{name} 已添加")
    except Exception as e:
        err_code = getattr(e, 'args', (None,))[0]
        print(f"  ✗ {table}.{name} 添加失败 ({err_code}): {e}")

db.disconnect()
