# -*- coding: utf-8 -*-
"""详细调试：检查训练过程"""
import sys
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

from modules.database import P5Database
from modules.self_evolution import _row_to_sorted
from modules.ml_predictor import _parse_history, _build_feature, POS

db = P5Database()
db.connect()
rows = db.get_history_data(limit=150, order='ASC')
db.disconnect()

sorted_data = _row_to_sorted(rows)
issues, digits, hezhi = _parse_history(sorted_data)

print(f'数据量: {len(issues)} 期')
print(f'wan 位前5个数字: {digits["wan"][:5]}')

# 测试特征构建
n = len(issues)
X_count = 0
for i in range(60, n):
    feat = _build_feature('wan', i, issues, digits, hezhi, {}, {}, {})
    if feat is not None:
        X_count += 1
        if X_count <= 3:
            print(f'i={i}: 特征长度={len(feat)}, 标签={digits["wan"][i]}')

print(f'\n有效特征样本数: {X_count} (需要 >= 100)')
