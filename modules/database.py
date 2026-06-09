import pymysql
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_CONFIG

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
    def __init__(self):
        self.connection = None
        self.cursor = None
    
    def connect(self):
        try:
            self.connection = pymysql.connect(
                host=DB_CONFIG['host'],
                port=DB_CONFIG['port'],
                user=DB_CONFIG['user'],
                password=DB_CONFIG['password'],
                database=DB_CONFIG['database'],
                charset=DB_CONFIG['charset']
            )
            self.cursor = self.connection.cursor()
            logger.info('MySQL数据库连接成功')
            return True
        except Exception as e:
            logger.error(f'MySQL数据库连接失败: {e}')
            return False
    
    def disconnect(self):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        logger.info('MySQL数据库连接已关闭')
    
    def create_tables(self):
        try:
            sql_qxc_data = '''
            CREATE TABLE IF NOT EXISTS data (
                id INT AUTO_INCREMENT PRIMARY KEY,
                issue VARCHAR(20) NOT NULL UNIQUE,
                date VARCHAR(20) NOT NULL,
                num1 INT NOT NULL,
                num2 INT NOT NULL,
                num3 INT NOT NULL,
                num4 INT NOT NULL,
                num5 INT NOT NULL,
                num6 INT NOT NULL,
                special_num INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_issue (issue),
                INDEX idx_date (date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            '''
            self.cursor.execute(sql_qxc_data)
            
            sql_report = '''
            CREATE TABLE IF NOT EXISTS analysis_report (
                id INT AUTO_INCREMENT PRIMARY KEY,
                report_date VARCHAR(20) NOT NULL,
                report_content TEXT,
                frequency_chart LONGBLOB,
                probability_chart LONGBLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_report_date (report_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            '''
            self.cursor.execute(sql_report)
            
            self.connection.commit()
            logger.info('数据表创建成功')
            return True
        except Exception as e:
            logger.error(f'创建数据表失败: {e}')
            return False
    
    def insert_or_update_qxc_data(self, data):
        try:
            sql = '''
            INSERT INTO qxc_data (issue, date, num1, num2, num3, num4, num5, num6, special_num)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                date = VALUES(date),
                num1 = VALUES(num1),
                num2 = VALUES(num2),
                num3 = VALUES(num3),
                num4 = VALUES(num4),
                num5 = VALUES(num5),
                num6 = VALUES(num6),
                special_num = VALUES(special_num),
                updated_at = CURRENT_TIMESTAMP
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
            logger.info(f'成功插入/更新 {count} 条开奖数据')
            return count
        except Exception as e:
            logger.error(f'批量插入数据失败: {e}')
            return 0
    
    def insert_report(self, report_date, report_content, frequency_chart=None, probability_chart=None):
        try:
            sql = '''
            INSERT INTO analysis_report (report_date, report_content, frequency_chart, probability_chart)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                report_content = VALUES(report_content),
                frequency_chart = VALUES(frequency_chart),
                probability_chart = VALUES(probability_chart),
                created_at = CURRENT_TIMESTAMP
            '''
            self.cursor.execute(sql, (report_date, report_content, frequency_chart, probability_chart))
            self.connection.commit()
            logger.info('成功插入分析报告')
            return True
        except Exception as e:
            logger.error(f'插入报告失败: {e}')
            return False
    
    def query_all_qxc_data(self):
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
            
            logger.info(f'查询到 {len(data)} 条开奖数据')
            return data
        except Exception as e:
            logger.error(f'查询数据失败: {e}')
            return []
    
    def query_by_issue(self, issue):
        try:
            sql = 'SELECT * FROM qxc_data WHERE issue = %s'
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
    
    def get_qxc_data_count(self):
        try:
            sql = 'SELECT COUNT(*) FROM qxc_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f'统计数据失败: {e}')
            return 0
    
    def get_reports(self):
        try:
            sql = 'SELECT * FROM analysis_report ORDER BY created_at DESC'
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            reports = []
            for row in results:
                reports.append({
                    'id': row[0],
                    'report_date': row[1],
                    'report_content': row[2],
                    'frequency_chart': row[3],
                    'probability_chart': row[4],
                    'created_at': row[5]
                })
            
            logger.info(f'查询到 {len(reports)} 条分析报告')
            return reports
        except Exception as e:
            logger.error(f'查询报告失败: {e}')
            return []

if __name__ == '__main__':
    db = Database()
    if db.connect():
        db.create_tables()
        count = db.get_qxc_data_count()
        print(f'MySQL数据库中现有 {count} 条开奖数据')
        db.disconnect()
