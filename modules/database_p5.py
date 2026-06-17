"""
排列5数据库操作模块

负责排列5数据的数据库连接、表结构管理、数据插入/查询等操作
包含历史数据表、走势图表、详细报告表、最优报告表
"""

import pymysql
import logging
import json
import os
from datetime import datetime

# 确保日志目录存在
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/database_p5.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class P5Database:
    """
    排列5数据库操作类
    
    负责数据库连接、表结构管理、数据插入/查询等操作
    """
    
    def __init__(self):
        self.connection = None
        self.cursor = None
    
    def connect(self):
        """连接MySQL数据库"""
        try:
            from config import DB_CONFIG
            self.connection = pymysql.connect(
                host=DB_CONFIG['host'],
                user=DB_CONFIG['user'],
                password=DB_CONFIG['password'],
                database=DB_CONFIG['database'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            self.cursor = self.connection.cursor()
            logger.info('MySQL数据库连接成功（排列5）')
            return True
        except Exception as e:
            logger.error(f'MySQL数据库连接失败: {e}')
            return False
    
    def disconnect(self):
        """断开数据库连接"""
        if self.connection:
            self.connection.close()
        logger.info('MySQL数据库连接已关闭')
    
    def create_tables(self):
        """创建排列5专用数据表"""
        try:
            # 确保数据库已连接
            if not self.connection:
                self.connect()
            
            # 1. 历史开奖数据表
            sql_history = '''
            CREATE TABLE IF NOT EXISTS p5_history_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一标识）',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                num_wan INT NULL DEFAULT NULL COMMENT '万位号码',
                num_qian INT NULL DEFAULT NULL COMMENT '千位号码',
                num_bai INT NULL DEFAULT NULL COMMENT '百位号码',
                num_shi INT NULL DEFAULT NULL COMMENT '十位号码',
                num_ge INT NULL DEFAULT NULL COMMENT '个位号码',
                hezhi INT NULL DEFAULT NULL COMMENT '和值',
                hezhi_feature VARCHAR(10) NULL DEFAULT NULL COMMENT '和值特征',
                odd_even_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '奇偶比例',
                odd_even_pattern VARCHAR(50) NULL DEFAULT NULL COMMENT '奇偶形态',
                span INT NULL DEFAULT NULL COMMENT '跨度',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_hezhi (hezhi ASC) USING BTREE,
                INDEX idx_span (span ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5历史开奖数据表';
            '''
            self.cursor.execute(sql_history)
            
            # 2. 走势图数据表
            sql_trend = '''
            CREATE TABLE IF NOT EXISTS p5_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（关联开奖数据）',
                num_wan INT NULL DEFAULT NULL COMMENT '万位号码',
                num_qian INT NULL DEFAULT NULL COMMENT '千位号码',
                num_bai INT NULL DEFAULT NULL COMMENT '百位号码',
                num_shi INT NULL DEFAULT NULL COMMENT '十位号码',
                num_ge INT NULL DEFAULT NULL COMMENT '个位号码',
                hezhi VARCHAR(10) NULL DEFAULT NULL COMMENT '和值',
                odd_even_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '奇偶比',
                big_small_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '大小比',
                prime_composite_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '质合比',
                trend_values TEXT NULL DEFAULT NULL COMMENT '走势图详细数据JSON',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_draw_date (issue ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5走势图数据表';
            '''
            self.cursor.execute(sql_trend)
            
            # 3. 详细分析报告表
            sql_detailed_report = '''
            CREATE TABLE IF NOT EXISTS p5_detailed_report (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                report_date VARCHAR(20) NULL DEFAULT NULL COMMENT '报告日期',
                report_uuid VARCHAR(36) NULL DEFAULT NULL COMMENT '报告唯一标识',
                raw_data_snapshot LONGTEXT NULL DEFAULT NULL COMMENT '原始数据快照',
                calculation_steps LONGTEXT NULL DEFAULT NULL COMMENT '计算步骤记录',
                analysis_params TEXT NULL DEFAULT NULL COMMENT '分析参数配置',
                frequency_analysis LONGTEXT NULL DEFAULT NULL COMMENT '频率分析结果',
                probability_analysis LONGTEXT NULL DEFAULT NULL COMMENT '概率分析结果',
                interval_analysis LONGTEXT NULL DEFAULT NULL COMMENT '间隔分析结果',
                hezhi_analysis LONGTEXT NULL DEFAULT NULL COMMENT '和值分析结果',
                odd_even_analysis LONGTEXT NULL DEFAULT NULL COMMENT '奇偶分析结果',
                span_analysis LONGTEXT NULL DEFAULT NULL COMMENT '跨度分析结果',
                big_small_analysis LONGTEXT NULL DEFAULT NULL COMMENT '大小分析结果',
                trend_analysis LONGTEXT NULL DEFAULT NULL COMMENT '走势分析结果',
                total_samples INT NULL DEFAULT NULL COMMENT '分析样本数',
                confidence_level DECIMAL(5,2) NULL DEFAULT NULL COMMENT '置信水平',
                report_content LONGTEXT NULL DEFAULT NULL COMMENT '报告内容',
                frequency_chart LONGBLOB NULL DEFAULT NULL COMMENT '频率分布图',
                probability_chart LONGBLOB NULL DEFAULT NULL COMMENT '概率分布图',
                trend_chart LONGBLOB NULL DEFAULT NULL COMMENT '趋势分析图',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_report_uuid (report_uuid ASC) USING BTREE,
                INDEX idx_report_date (report_date ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5详细分析报告表';
            '''
            self.cursor.execute(sql_detailed_report)
            
            # 4. 最终最优报告表
            sql_final_report = '''
            CREATE TABLE IF NOT EXISTS p5_final_report (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                detailed_report_id INT NULL DEFAULT NULL COMMENT '关联详细报告ID',
                report_date VARCHAR(20) NULL DEFAULT NULL COMMENT '报告日期',
                report_uuid VARCHAR(36) NULL DEFAULT NULL COMMENT '报告唯一标识',
                recommended_numbers VARCHAR(50) NULL DEFAULT NULL COMMENT '推荐号码组合',
                confidence_score DECIMAL(5,2) NULL DEFAULT NULL COMMENT '置信分数',
                analysis_summary TEXT NULL DEFAULT NULL COMMENT '分析摘要',
                key_conclusions TEXT NULL DEFAULT NULL COMMENT '关键结论',
                core_metrics TEXT NULL DEFAULT NULL COMMENT '核心指标',
                decision_recommendations TEXT NULL DEFAULT NULL COMMENT '决策建议',
                report_content TEXT NULL DEFAULT NULL COMMENT '报告内容',
                frequency_chart LONGBLOB NULL DEFAULT NULL COMMENT '频率分布图',
                probability_chart LONGBLOB NULL DEFAULT NULL COMMENT '概率分布图',
                status ENUM('draft', 'validated', 'published') NULL DEFAULT 'draft' COMMENT '报告状态',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_report_uuid (report_uuid ASC) USING BTREE,
                INDEX idx_final_report_date (report_date ASC) USING BTREE,
                INDEX idx_detailed_report_id (detailed_report_id ASC) USING BTREE,
                CONSTRAINT fk_p5_detailed_report FOREIGN KEY (detailed_report_id) 
                    REFERENCES p5_detailed_report(id) ON DELETE CASCADE ON UPDATE RESTRICT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5最终最优报告表';
            '''
            self.cursor.execute(sql_final_report)
            
            self.connection.commit()
            logger.info('排列5数据表创建成功')
            return True
        except Exception as e:
            logger.error(f'创建数据表失败: {e}')
            return False
    
    def insert_history_data(self, data):
        """
        批量插入历史开奖数据
        
        Args:
            data: 历史数据列表
        
        Returns:
            成功插入的记录数
        """
        if not self.connection:
            self.connect()
        
        inserted_count = 0
        try:
            sql = '''
            INSERT INTO p5_history_data 
            (issue, draw_date, num_wan, num_qian, num_bai, num_shi, num_ge, 
             hezhi, hezhi_feature, odd_even_ratio, odd_even_pattern, span)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            draw_date = VALUES(draw_date),
            num_wan = VALUES(num_wan),
            num_qian = VALUES(num_qian),
            num_bai = VALUES(num_bai),
            num_shi = VALUES(num_shi),
            num_ge = VALUES(num_ge),
            hezhi = VALUES(hezhi),
            hezhi_feature = VALUES(hezhi_feature),
            odd_even_ratio = VALUES(odd_even_ratio),
            odd_even_pattern = VALUES(odd_even_pattern),
            span = VALUES(span),
            updated_at = CURRENT_TIMESTAMP
            '''
            
            for item in data:
                try:
                    numbers = item['numbers']
                    self.cursor.execute(sql, (
                        item['issue'],
                        item['date'],
                        numbers[0] if len(numbers) > 0 else None,
                        numbers[1] if len(numbers) > 1 else None,
                        numbers[2] if len(numbers) > 2 else None,
                        numbers[3] if len(numbers) > 3 else None,
                        numbers[4] if len(numbers) > 4 else None,
                        item.get('hezhi'),
                        item.get('hezhi_feature'),
                        item.get('odd_even_ratio'),
                        item.get('odd_even_pattern'),
                        item.get('span')
                    ))
                    inserted_count += 1
                except Exception as e:
                    logger.error(f'插入数据失败 {item.get("issue")}: {e}')
            
            self.connection.commit()
            logger.info(f'成功插入/更新 {inserted_count} 条历史数据')
            return inserted_count
        except Exception as e:
            logger.error(f'批量插入历史数据失败: {e}')
            self.connection.rollback()
            return 0
    
    def insert_trend_data(self, data):
        """
        批量插入走势图数据
        
        Args:
            data: 走势图数据列表
        
        Returns:
            成功插入的记录数
        """
        if not self.connection:
            self.connect()
        
        inserted_count = 0
        try:
            sql = '''
            INSERT INTO p5_trend_data 
            (issue, num_wan, num_qian, num_bai, num_shi, num_ge, 
             hezhi, odd_even_ratio, big_small_ratio, prime_composite_ratio, trend_values)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            num_wan = VALUES(num_wan),
            num_qian = VALUES(num_qian),
            num_bai = VALUES(num_bai),
            num_shi = VALUES(num_shi),
            num_ge = VALUES(num_ge),
            hezhi = VALUES(hezhi),
            odd_even_ratio = VALUES(odd_even_ratio),
            big_small_ratio = VALUES(big_small_ratio),
            prime_composite_ratio = VALUES(prime_composite_ratio),
            trend_values = VALUES(trend_values),
            updated_at = CURRENT_TIMESTAMP
            '''
            
            for item in data:
                try:
                    numbers = item.get('numbers', [])
                    trend = item.get('trend', {})
                    trend_values_json = json.dumps(trend, ensure_ascii=False) if trend else None
                    
                    self.cursor.execute(sql, (
                        item['issue'],
                        numbers[0] if len(numbers) > 0 else None,
                        numbers[1] if len(numbers) > 1 else None,
                        numbers[2] if len(numbers) > 2 else None,
                        numbers[3] if len(numbers) > 3 else None,
                        numbers[4] if len(numbers) > 4 else None,
                        item.get('hezhi'),
                        item.get('odd_even_ratio'),
                        item.get('big_small_ratio'),
                        item.get('prime_composite_ratio'),
                        trend_values_json
                    ))
                    inserted_count += 1
                except Exception as e:
                    logger.error(f'插入走势数据失败 {item.get("issue")}: {e}')
            
            self.connection.commit()
            logger.info(f'成功插入/更新 {inserted_count} 条走势数据')
            return inserted_count
        except Exception as e:
            logger.error(f'批量插入走势数据失败: {e}')
            self.connection.rollback()
            return 0
    
    def get_history_data(self, limit=None, order='DESC'):
        """
        查询历史开奖数据
        
        Args:
            limit: 限制返回数量
            order: 排序方式（ASC/DESC）
        
        Returns:
            历史数据列表
        """
        if not self.connection:
            self.connect()
        
        try:
            sql = f'''
            SELECT * FROM p5_history_data 
            ORDER BY issue {order}
            '''
            if limit:
                sql += f' LIMIT {limit}'
            
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            # 转换为统一格式
            data = []
            for row in results:
                numbers = [
                    row['num_wan'],
                    row['num_qian'],
                    row['num_bai'],
                    row['num_shi'],
                    row['num_ge']
                ]
                data.append({
                    'issue': row['issue'],
                    'date': row['draw_date'],
                    'numbers': numbers,
                    'hezhi': row['hezhi'],
                    'hezhi_feature': row['hezhi_feature'],
                    'odd_even_ratio': row['odd_even_ratio'],
                    'odd_even_pattern': row['odd_even_pattern'],
                    'span': row['span']
                })
            
            return data
        except Exception as e:
            logger.error(f'查询历史数据失败: {e}')
            return []
    
    def get_trend_data(self, limit=None, order='DESC'):
        """
        查询走势图数据
        
        Args:
            limit: 限制返回数量
            order: 排序方式（ASC/DESC）
        
        Returns:
            走势数据列表
        """
        if not self.connection:
            self.connect()
        
        try:
            sql = f'''
            SELECT * FROM p5_trend_data 
            ORDER BY issue {order}
            '''
            if limit:
                sql += f' LIMIT {limit}'
            
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            # 转换为统一格式
            data = []
            for row in results:
                numbers = [
                    row['num_wan'],
                    row['num_qian'],
                    row['num_bai'],
                    row['num_shi'],
                    row['num_ge']
                ]
                trend = json.loads(row['trend_values']) if row['trend_values'] else {}
                data.append({
                    'issue': row['issue'],
                    'numbers': numbers,
                    'trend': trend,
                    'hezhi': row['hezhi'],
                    'odd_even_ratio': row['odd_even_ratio'],
                    'big_small_ratio': row['big_small_ratio'],
                    'prime_composite_ratio': row['prime_composite_ratio']
                })
            
            return data
        except Exception as e:
            logger.error(f'查询走势数据失败: {e}')
            return []
    
    def get_latest_issue(self):
        """获取最新的期号"""
        if not self.connection:
            self.connect()
        
        try:
            sql = 'SELECT MAX(issue) as latest_issue FROM p5_history_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            return result['latest_issue'] if result else None
        except Exception as e:
            logger.error(f'获取最新期号失败: {e}')
            return None
    
    def get_data_count(self):
        """获取数据总数"""
        if not self.connection:
            self.connect()
        
        try:
            sql = 'SELECT COUNT(*) as count FROM p5_history_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            return result['count'] if result else 0
        except Exception as e:
            logger.error(f'获取数据总数失败: {e}')
            return 0
    
    def save_detailed_report(self, report_data):
        """
        保存详细分析报告
        
        Args:
            report_data: 报告数据字典
        
        Returns:
            报告ID
        """
        if not self.connection:
            self.connect()
        
        try:
            import uuid
            report_uuid = str(uuid.uuid4())
            
            sql = '''
            INSERT INTO p5_detailed_report 
            (report_date, report_uuid, frequency_analysis, probability_analysis, 
             interval_analysis, hezhi_analysis, odd_even_analysis, span_analysis,
             big_small_analysis, trend_analysis, total_samples, report_content,
             frequency_chart, probability_chart, trend_chart)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''
            
            self.cursor.execute(sql, (
                datetime.now().strftime('%Y-%m-%d'),
                report_uuid,
                report_data.get('frequency_analysis'),
                report_data.get('probability_analysis'),
                report_data.get('interval_analysis'),
                report_data.get('hezhi_analysis'),
                report_data.get('odd_even_analysis'),
                report_data.get('span_analysis'),
                report_data.get('big_small_analysis'),
                report_data.get('trend_analysis'),
                report_data.get('total_samples'),
                report_data.get('report_content'),
                report_data.get('frequency_chart'),
                report_data.get('probability_chart'),
                report_data.get('trend_chart')
            ))
            
            self.connection.commit()
            report_id = self.cursor.lastrowid
            logger.info(f'详细报告保存成功，ID: {report_id}, UUID: {report_uuid}')
            return report_id
        except Exception as e:
            logger.error(f'保存详细报告失败: {e}')
            self.connection.rollback()
            return None
    
    def save_final_report(self, report_data, detailed_report_id=None):
        """
        保存最终最优报告
        
        Args:
            report_data: 报告数据字典
            detailed_report_id: 关联的详细报告ID
        
        Returns:
            报告ID
        """
        if not self.connection:
            self.connect()
        
        try:
            import uuid
            report_uuid = str(uuid.uuid4())
            
            sql = '''
            INSERT INTO p5_final_report 
            (detailed_report_id, report_date, report_uuid, recommended_numbers,
             confidence_score, analysis_summary, key_conclusions, core_metrics,
             decision_recommendations, report_content, frequency_chart, probability_chart, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''
            
            self.cursor.execute(sql, (
                detailed_report_id,
                datetime.now().strftime('%Y-%m-%d'),
                report_uuid,
                report_data.get('recommended_numbers'),
                report_data.get('confidence_score'),
                report_data.get('analysis_summary'),
                report_data.get('key_conclusions'),
                report_data.get('core_metrics'),
                report_data.get('decision_recommendations'),
                report_data.get('report_content'),
                report_data.get('frequency_chart'),
                report_data.get('probability_chart'),
                'published'
            ))
            
            self.connection.commit()
            report_id = self.cursor.lastrowid
            logger.info(f'最终报告保存成功，ID: {report_id}, UUID: {report_uuid}')
            return report_id
        except Exception as e:
            logger.error(f'保存最终报告失败: {e}')
            self.connection.rollback()
            return None
    
    def get_reports(self, report_type='all', limit=10):
        """
        查询报告列表
        
        Args:
            report_type: 报告类型（all/detailed/final）
            limit: 限制返回数量
        
        Returns:
            报告列表
        """
        if not self.connection:
            self.connect()
        
        reports = []
        
        try:
            if report_type in ['all', 'final']:
                sql = f'''
                SELECT * FROM p5_final_report 
                ORDER BY created_at DESC 
                LIMIT {limit}
                '''
                self.cursor.execute(sql)
                final_reports = self.cursor.fetchall()
                for r in final_reports:
                    r['report_type'] = 'final'
                    reports.append(r)
            
            if report_type in ['all', 'detailed']:
                sql = f'''
                SELECT * FROM p5_detailed_report 
                ORDER BY created_at DESC 
                LIMIT {limit}
                '''
                self.cursor.execute(sql)
                detailed_reports = self.cursor.fetchall()
                for r in detailed_reports:
                    r['report_type'] = 'detailed'
                    reports.append(r)
            
            # 按创建时间排序
            reports.sort(key=lambda x: x['created_at'], reverse=True)
            return reports[:limit]
        except Exception as e:
            logger.error(f'查询报告失败: {e}')
            return []
    
    def get_statistics(self):
        """
        获取数据统计信息
        
        Returns:
            统计信息字典
        """
        if not self.connection:
            self.connect()
        
        try:
            stats = {}
            
            # 历史数据统计
            sql = 'SELECT COUNT(*) as count FROM p5_history_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            stats['history_count'] = result['count'] if result else 0
            
            # 走势数据统计
            sql = 'SELECT COUNT(*) as count FROM p5_trend_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            stats['trend_count'] = result['count'] if result else 0
            
            # 报告统计
            sql = 'SELECT COUNT(*) as count FROM p5_detailed_report'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            stats['detailed_report_count'] = result['count'] if result else 0
            
            sql = 'SELECT COUNT(*) as count FROM p5_final_report'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            stats['final_report_count'] = result['count'] if result else 0
            
            # 最新期号
            stats['latest_issue'] = self.get_latest_issue()
            
            # 和值范围统计
            sql = 'SELECT MIN(hezhi) as min_hezhi, MAX(hezhi) as max_hezhi, AVG(hezhi) as avg_hezhi FROM p5_history_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            if result:
                stats['min_hezhi'] = result['min_hezhi']
                stats['max_hezhi'] = result['max_hezhi']
                stats['avg_hezhi'] = round(result['avg_hezhi'], 2) if result['avg_hezhi'] else 0
            
            # 跨度范围统计
            sql = 'SELECT MIN(span) as min_span, MAX(span) as max_span, AVG(span) as avg_span FROM p5_history_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            if result:
                stats['min_span'] = result['min_span']
                stats['max_span'] = result['max_span']
                stats['avg_span'] = round(result['avg_span'], 2) if result['avg_span'] else 0
            
            return stats
        except Exception as e:
            logger.error(f'获取统计信息失败: {e}')
            return {}


def test_database():
    """测试数据库功能"""
    db = P5Database()
    
    # 连接数据库
    print('=== 测试数据库连接 ===')
    if db.connect():
        print('数据库连接成功')
        
        # 创建表
        print('\n=== 创建数据表 ===')
        if db.create_tables():
            print('数据表创建成功')
        
        # 获取统计信息
        print('\n=== 数据统计 ===')
        stats = db.get_statistics()
        for key, value in stats.items():
            print(f'{key}: {value}')
        
        # 断开连接
        db.disconnect()
    else:
        print('数据库连接失败')


if __name__ == '__main__':
    test_database()
