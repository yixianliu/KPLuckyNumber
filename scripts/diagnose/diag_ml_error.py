# -*- coding: utf-8 -*-
"""运行时诊断脚本：检查 ml_predictor 错误根源"""
import sys
import os
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')
os.chdir(r'D:\PythonProject\KPLuckyNumber')

import traceback
from modules.ml_predictor import predict_next, _parse_history, POS

# 直接从数据库加载数据
import pymysql
from config import DB_CONFIG

conn = pymysql.connect(
    host=DB_CONFIG['host'],
    port=DB_CONFIG.get('port', 3306),
    user=DB_CONFIG['user'],
    password=DB_CONFIG['password'],
    database=DB_CONFIG['database'],
    charset='utf8mb4'
)
cur = conn.cursor()

# 获取历史数据（正序）
cur.execute('SELECT issue, wan, qian, bai, shi, ge, hezhi FROM p5_history_data WHERE is_valid=1 ORDER BY issue ASC LIMIT 100')
rows = cur.fetchall()
conn.close()

print(f'从数据库获取 {len(rows)} 行数据')

# 转换为字典格式（模拟 database.py 的行为）
sorted_data = []
for r in rows:
    row = {
        'issue': str(r[0]),
        'wan': r[1],
        'qian': r[2],
        'bai': r[3],
        'shi': r[4],
        'ge': r[5],
        'hezhi': r[6],
    }
    # 检查是否有 numbers 字段
    if 'numbers' not in row:
        row['numbers'] = [row['wan'], row['qian'], row['bai'], row['shi'], row['ge']]
    sorted_data.append(row)

print(f'转换后 {len(sorted_data)} 行')
print(f'第一行 keys: {sorted_data[0].keys() if sorted_data else "空"}')
print(f'第一行 numbers: {sorted_data[0].get("numbers") if sorted_data else "空"}')

# 测试 _parse_history
print('\n--- 测试 _parse_history ---')
try:
    issues, digits, hezhi = _parse_history(sorted_data)
    print(f'issues 数量: {len(issues)}')
    for p in POS:
        print(f'{p}: 长度={len(digits[p])}, 类型={type(digits[p]).__name__}, 前3项={digits[p][:3] if digits[p] else "空"}')
except Exception as e:
    print(f'_parse_history 异常: {e}')
    traceback.print_exc()

# 测试 predict_next
print('\n--- 测试 predict_next ---')
try:
    result = predict_next(sorted_data, target_issue='test')
    if result:
        print(f'预测结果: {len(result)} 个位置')
        for i, p in enumerate(POS):
            print(f'{p}: {result[i]}')
    else:
        print('predict_next 返回 None')
except Exception as e:
    print(f'predict_next 异常: {e}')
    traceback.print_exc()
