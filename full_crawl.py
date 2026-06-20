from modules.spider_p5 import P5Spider
from modules.database_p5 import P5Database
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 清空数据库并重新爬取
db = P5Database()
db.connect()
db.create_tables()

# 清空现有数据
db.cursor.execute('DELETE FROM p5_history_data')
db.cursor.execute('DELETE FROM p5_trend_data')
db.cursor.execute('DELETE FROM p5_ai_report')
db.cursor.execute('DELETE FROM p5_prediction_record')
db.connection.commit()
print('数据库已清空')

# 全量爬取120条数据
spider = P5Spider()
result = spider.full_crawl_and_save(max_records=120)
print(f'爬取完成: 历史数据新增{result[0]}条, 跳过{result[1]}条, 走势数据新增{result[2]}条, 跳过{result[3]}条')

# 验证数据
history_count = db.get_history_count()
db.cursor.execute('SELECT COUNT(*) as count FROM p5_trend_data')
trend_result = db.cursor.fetchone()
trend_count = trend_result['count'] if trend_result else 0

print(f'\n数据库统计:')
print(f'  历史数据: {history_count} 条')
print(f'  走势数据: {trend_count} 条')

# 显示最新和最旧数据
db.cursor.execute('SELECT issue, draw_date, wan, qian, bai, shi, ge FROM p5_history_data ORDER BY issue DESC LIMIT 3')
latest = db.cursor.fetchall()
print(f'\n最新3条数据:')
for row in latest:
    print(f'  期号: {row["issue"]}, 日期: {row["draw_date"]}, 号码: [{row["wan"]}, {row["qian"]}, {row["bai"]}, {row["shi"]}, {row["ge"]}]')

db.cursor.execute('SELECT issue, draw_date, wan, qian, bai, shi, ge FROM p5_history_data ORDER BY issue ASC LIMIT 3')
oldest = db.cursor.fetchall()
print(f'\n最旧3条数据:')
for row in oldest:
    print(f'  期号: {row["issue"]}, 日期: {row["draw_date"]}, 号码: [{row["wan"]}, {row["qian"]}, {row["bai"]}, {row["shi"]}, {row["ge"]}]')

db.disconnect()
print('\n完成!')
