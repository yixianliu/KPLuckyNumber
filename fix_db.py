from modules.database_p5 import P5Database

db = P5Database()
db.connect()

# 添加缺失的列
try:
    db.cursor.execute("ALTER TABLE p5_ai_report ADD COLUMN next_issue VARCHAR(20) NULL DEFAULT NULL COMMENT '预测目标期号'")
    db.connection.commit()
    print('next_issue column added')
except Exception as e:
    print(f'Column add error (may already exist): {e}')

# 检查p5_prediction_record表是否存在
db.cursor.execute("SHOW TABLES LIKE 'p5_prediction_record'")
if not db.cursor.fetchone():
    print('p5_prediction_record table missing, recreating all tables...')
    db.create_tables()
else:
    print('p5_prediction_record table exists')

db.disconnect()
print('Fix done')
