import sqlite3
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/database.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path='data/qxc_data.db'):
        self.db_path = db_path
        self.connection = None
        self.cursor = None
        self._ensure_dir()
    
    def _ensure_dir(self):
        dir_path = os.path.dirname(self.db_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)
    
    def connect(self):
        try:
            self.connection = sqlite3.connect(self.db_path)
            self.cursor = self.connection.cursor()
            logger.info('数据库连接成功')
            return True
        except Exception as e:
            logger.error(f'数据库连接失败: {e}')
            return False
    
    def disconnect(self):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        logger.info('数据库连接已关闭')
    
    def create_table(self):
        try:
            sql = '''
            CREATE TABLE IF NOT EXISTS qxc_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue TEXT NOT NULL UNIQUE,
                date TEXT NOT NULL,
                num1 INTEGER NOT NULL,
                num2 INTEGER NOT NULL,
                num3 INTEGER NOT NULL,
                num4 INTEGER NOT NULL,
                num5 INTEGER NOT NULL,
                num6 INTEGER NOT NULL,
                special_num INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            '''
            self.cursor.execute(sql)
            
            try:
                self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_issue ON qxc_data(issue)')
                self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON qxc_data(date)')
            except Exception:
                pass
            
            self.connection.commit()
            logger.info('数据表创建成功')
            return True
        except Exception as e:
            logger.error(f'创建数据表失败: {e}')
            return False
    
    def insert_or_update(self, data):
        try:
            sql = '''
            INSERT OR REPLACE INTO qxc_data (issue, date, num1, num2, num3, num4, num5, num6, special_num, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            '''
            
            count = 0
            for item in data:
                try:
                    numbers = list(map(int, item['numbers']))
                    self.cursor.execute(sql, (
                        item['issue'],
                        item['date'],
                        numbers[0],
                        numbers[1],
                        numbers[2],
                        numbers[3],
                        numbers[4],
                        numbers[5],
                        numbers[6]
                    ))
                    count += 1
                except Exception as e:
                    logger.error(f'插入数据失败: {item["issue"]}, 错误: {e}')
            
            self.connection.commit()
            logger.info(f'成功插入/更新 {count} 条数据')
            return count
        except Exception as e:
            logger.error(f'批量插入数据失败: {e}')
            return 0
    
    def query_all(self):
        try:
            sql = 'SELECT * FROM qxc_data ORDER BY issue DESC'
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            data = []
            for row in results:
                data.append({
                    'id': row[0],
                    'issue': row[1],
                    'date': row[2],
                    'numbers': [row[3], row[4], row[5], row[6], row[7], row[8], row[9]]
                })
            
            logger.info(f'查询到 {len(data)} 条数据')
            return data
        except Exception as e:
            logger.error(f'查询数据失败: {e}')
            return []
    
    def query_by_issue(self, issue):
        try:
            sql = 'SELECT * FROM qxc_data WHERE issue = ?'
            self.cursor.execute(sql, (issue,))
            row = self.cursor.fetchone()
            
            if row:
                return {
                    'id': row[0],
                    'issue': row[1],
                    'date': row[2],
                    'numbers': [row[3], row[4], row[5], row[6], row[7], row[8], row[9]]
                }
            return None
        except Exception as e:
            logger.error(f'查询期号失败: {e}')
            return None
    
    def get_count(self):
        try:
            sql = 'SELECT COUNT(*) FROM qxc_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f'统计数据失败: {e}')
            return 0

if __name__ == '__main__':
    db = Database()
    if db.connect():
        db.create_table()
        count = db.get_count()
        print(f'数据库中现有 {count} 条数据')
        db.disconnect()
