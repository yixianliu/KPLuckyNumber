# -*- coding: utf-8 -*-
"""深度诊断：检查 draw_date 缺失的根因"""
import sys
sys.path.insert(0, '.')
from modules.database import P5Database

db = P5Database()
if not db.connect():
    print('DB connect failed')
    exit(1)

# 1. 检查 p5_history_data 的 draw_date 情况
print('=== p5_history_data draw_date 统计 ===')
db.cursor.execute('''
    SELECT
        COUNT(*)                                AS total,
        SUM(CASE WHEN draw_date IS NULL OR draw_date = '' THEN 1 ELSE 0 END) AS null_date
    FROM p5_history_data
''')
r = db.cursor.fetchone()
print(f'  total={r["total"]}, null_date={r["null_date"]}')

# 2. 抽样查看 history 的 draw_date
print('\n=== p5_history_data 最近5条 ===')
db.cursor.execute('SELECT issue, draw_date FROM p5_history_data ORDER BY issue DESC LIMIT 5')
for row in db.cursor.fetchall():
    print(f'  issue={row["issue"]}, draw_date={row["draw_date"]!r}')

# 3. 检查万位走势表最近5条
print('\n=== p5_wan_trend_data 最近5条 ===')
db.cursor.execute('SELECT issue, draw_date, hot_level, trend_json FROM p5_wan_trend_data ORDER BY issue DESC LIMIT 5')
for row in db.cursor.fetchall():
    print(f'  issue={row["issue"]}, draw_date={row["draw_date"]!r}, hot_level={row["hot_level"]!r}, trend_json_len={len(str(row["trend_json"])) if row["trend_json"] else 0}')

# 4. 检查万位走势表最新期号与历史表最新期号的差异
print('\n=== 最新期号对比 ===')
db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
h = db.cursor.fetchone()
db.cursor.execute('SELECT issue FROM p5_wan_trend_data ORDER BY issue DESC LIMIT 1')
w = db.cursor.fetchone()
print(f'  history latest={h["issue"] if h else "NONE"}, wan_trend latest={w["issue"] if w else "NONE"}')

# 5. 测试 _fix 逻辑手动执行
print('\n=== 测试 date_map 构建 ===')
db.cursor.execute("SELECT issue, draw_date FROM p5_history_data WHERE draw_date IS NOT NULL AND draw_date != '' ORDER BY issue DESC LIMIT 5")
rows = db.cursor.fetchall() or []
date_map = {str(r['issue']): str(r['draw_date']) for r in rows if r.get('issue') and r.get('draw_date')}
print(f'  date_map sample (top 5): {list(date_map.items())[:5]}')

# 6. 测试 UPDATE 是否有效
print('\n=== 测试 UPDATE draw_date ===')
test_issue = list(date_map.keys())[0] if date_map else None
if test_issue:
    db.cursor.execute(f'SELECT issue, draw_date FROM p5_wan_trend_data WHERE issue = %s', (test_issue,))
    before = db.cursor.fetchone()
    print(f'  before: issue={before["issue"]}, draw_date={before["draw_date"]!r}')
    db.cursor.execute(f"UPDATE p5_wan_trend_data SET draw_date = %s WHERE issue = %s", (date_map[test_issue], test_issue))
    db.cursor.execute(f'SELECT issue, draw_date FROM p5_wan_trend_data WHERE issue = %s', (test_issue,))
    after = db.cursor.fetchone()
    print(f'  after:  issue={after["issue"]}, draw_date={after["draw_date"]!r}')

db.disconnect()
