# -*- coding: utf-8 -*-
"""检查 _row_to_sorted 的数据格式问题"""
import sys
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

from modules.database import P5Database
from modules.self_evolution import _row_to_sorted

db = P5Database()
db.connect()
rows = db.get_history_data(limit=5, order='ASC')
db.disconnect()

print(f'获取 {len(rows)} 行')
if rows:
    print(f'第一行类型: {type(rows[0]).__name__}')
    print(f'第一行 keys: {list(rows[0].keys())}')
    print(f'第一行内容: {rows[0]}')
    print()

    # 测试 _row_to_sorted
    sorted_data = _row_to_sorted(rows)
    print(f'_row_to_sorted 输出: {len(sorted_data)} 行')
    if sorted_data:
        print(f'第一行: {sorted_data[0]}')
        print(f'第一行 numbers: {sorted_data[0].get("numbers")}')
