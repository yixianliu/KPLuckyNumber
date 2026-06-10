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
        logging.FileHandler('logs/database.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Database:
    """
    数据库操作类
    
    负责数据库连接、表结构管理、数据插入/查询等操作
    """
    
    def __init__(self):
        self.connection = None
        self.cursor = None
    
    def connect(self):
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
            logger.info('MySQL数据库连接成功')
            return True
        except Exception as e:
            logger.error(f'MySQL数据库连接失败: {e}')
            return False
    
    def disconnect(self):
        if self.connection:
            self.connection.close()
        logger.info('MySQL数据库连接已关闭')
    
    def create_tables(self):
        try:
            # 确保数据库已连接
            if not self.connection:
                self.connect()
            
            # 删除旧的走势图表（如果存在且结构不兼容）
            self._drop_incompatible_tables()
            
            # 主数据表 - 存储开奖数据
            sql_qxc_data = '''
            CREATE TABLE IF NOT EXISTS qxc_history_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NULL DEFAULT NULL COMMENT '期号（唯一标识）',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                num1 INT NULL DEFAULT NULL COMMENT '第一位号码',
                num2 INT NULL DEFAULT NULL COMMENT '第二位号码',
                num3 INT NULL DEFAULT NULL COMMENT '第三位号码',
                num4 INT NULL DEFAULT NULL COMMENT '第四位号码',
                num5 INT NULL DEFAULT NULL COMMENT '第五位号码',
                num6 INT NULL DEFAULT NULL COMMENT '第六位号码',
                special_num INT NULL DEFAULT NULL COMMENT '特别号码',
                hezhi VARCHAR(10) NULL DEFAULT NULL COMMENT '和值',
                hezhi_type VARCHAR(10) NULL DEFAULT NULL COMMENT '和值类型（奇偶）',
                odd_even_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '奇偶比例',
                odd_even_pattern TEXT NULL DEFAULT NULL COMMENT '奇偶模式',
                span VARCHAR(10) NULL DEFAULT NULL COMMENT '跨度',
                created_at TIMESTAMP NULL DEFAULT NULL COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT NULL COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX issue (issue ASC) USING BTREE,
                INDEX idx_issue (issue ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='七星彩历史开奖数据表';
            '''
            self.cursor.execute(sql_qxc_data)
            
            # 走势图数据表
            sql_trend_data = '''
            CREATE TABLE IF NOT EXISTS qxc_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NULL DEFAULT NULL COMMENT '期号（关联开奖数据）',
                trend_values TEXT NULL DEFAULT NULL COMMENT '走势图数据JSON',
                created_at TIMESTAMP NULL DEFAULT NULL COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT NULL COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX issue (issue ASC) USING BTREE,
                INDEX idx_issue (issue ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='七星彩走势图数据表';
            '''
            self.cursor.execute(sql_trend_data)
            
            # 详细报告表 - 存储完整原始数据、中间计算过程及详细参数
            sql_detailed_report = '''
            CREATE TABLE IF NOT EXISTS qxc_detailed_report (
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
                total_samples INT NULL DEFAULT NULL COMMENT '分析样本数',
                confidence_level DECIMAL(5,2) NULL DEFAULT NULL COMMENT '置信水平',
                report_content LONGTEXT NULL DEFAULT NULL COMMENT '报告内容',
                frequency_chart LONGBLOB NULL DEFAULT NULL COMMENT '频率分布图',
                probability_chart LONGBLOB NULL DEFAULT NULL COMMENT '概率分布图',
                created_at TIMESTAMP NULL DEFAULT NULL COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT NULL COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX report_uuid (report_uuid ASC) USING BTREE,
                INDEX idx_report_date (report_date ASC) USING BTREE,
                INDEX idx_report_uuid (report_uuid ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='七星彩详细分析报告表';
            '''
            self.cursor.execute(sql_detailed_report)
            
            # 最终报告表 - 仅存储经过验证的最终结果数据
            sql_final_report = '''
            CREATE TABLE IF NOT EXISTS qxc_final_report (
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
                status ENUM('draft', 'validated', 'published') NULL DEFAULT NULL COMMENT '报告状态',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX report_uuid (report_uuid ASC) USING BTREE,
                INDEX idx_final_report_date (report_date ASC) USING BTREE,
                INDEX idx_final_report_uuid (report_uuid ASC) USING BTREE,
                INDEX idx_detailed_report_id (detailed_report_id ASC) USING BTREE,
                CONSTRAINT fk_detailed_report FOREIGN KEY (detailed_report_id) 
                    REFERENCES qxc_detailed_report(id) ON DELETE CASCADE ON UPDATE RESTRICT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='七星彩最终最优报告表';
            '''
            self.cursor.execute(sql_final_report)
            
            # 添加表和字段注释（确保注释已添加）
            self._add_table_comments()
            
            self.connection.commit()
            logger.info('数据表创建成功（包含注释）')
            return True
        except Exception as e:
            logger.error(f'创建数据表失败: {e}')
            return False
    
    def _add_table_comments(self):
        """为已存在的表添加或更新注释"""
        try:
            # 更新表注释
            table_comments = {
                'qxc_history_data': '七星彩历史开奖数据表',
                'qxc_trend_data': '七星彩走势图数据表',
                'qxc_detailed_report': '七星彩详细分析报告表',
                'qxc_final_report': '七星彩最终最优报告表'
            }
            
            for table_name, comment in table_comments.items():
                sql = f"ALTER TABLE {table_name} COMMENT = '{comment}'"
                try:
                    self.cursor.execute(sql)
                except Exception as e:
                    logger.debug(f'更新表 {table_name} 注释失败（可能表不存在）: {e}')
            
            # 更新字段注释
            column_comments = {
                'qxc_history_data': [
                    ('id', '主键ID'),
                    ('issue', '期号（唯一标识）'),
                    ('draw_date', '开奖日期'),
                    ('num1', '第一位号码'),
                    ('num2', '第二位号码'),
                    ('num3', '第三位号码'),
                    ('num4', '第四位号码'),
                    ('num5', '第五位号码'),
                    ('num6', '第六位号码'),
                    ('special_num', '特别号码'),
                    ('hezhi', '和值'),
                    ('hezhi_type', '和值类型（奇偶）'),
                    ('odd_even_ratio', '奇偶比例'),
                    ('odd_even_pattern', '奇偶模式'),
                    ('span', '跨度'),
                    ('created_at', '创建时间'),
                    ('updated_at', '更新时间')
                ],
                'qxc_trend_data': [
                    ('id', '主键ID'),
                    ('issue', '期号（关联开奖数据）'),
                    ('trend_values', '走势图数据JSON'),
                    ('created_at', '创建时间'),
                    ('updated_at', '更新时间')
                ],
                'qxc_detailed_report': [
                    ('id', '主键ID'),
                    ('report_date', '报告日期'),
                    ('report_uuid', '报告唯一标识'),
                    ('raw_data_snapshot', '原始数据快照'),
                    ('calculation_steps', '计算步骤记录'),
                    ('analysis_params', '分析参数配置'),
                    ('frequency_analysis', '频率分析结果'),
                    ('probability_analysis', '概率分析结果'),
                    ('interval_analysis', '间隔分析结果'),
                    ('hezhi_analysis', '和值分析结果'),
                    ('odd_even_analysis', '奇偶分析结果'),
                    ('span_analysis', '跨度分析结果'),
                    ('total_samples', '分析样本数'),
                    ('confidence_level', '置信水平'),
                    ('report_content', '报告内容'),
                    ('frequency_chart', '频率分布图'),
                    ('probability_chart', '概率分布图'),
                    ('created_at', '创建时间'),
                    ('updated_at', '更新时间')
                ],
                'qxc_final_report': [
                    ('id', '主键ID'),
                    ('detailed_report_id', '关联详细报告ID'),
                    ('report_date', '报告日期'),
                    ('report_uuid', '报告唯一标识'),
                    ('recommended_numbers', '推荐号码组合'),
                    ('confidence_score', '置信分数'),
                    ('analysis_summary', '分析摘要'),
                    ('key_conclusions', '关键结论'),
                    ('core_metrics', '核心指标'),
                    ('decision_recommendations', '决策建议'),
                    ('report_content', '报告内容'),
                    ('frequency_chart', '频率分布图'),
                    ('probability_chart', '概率分布图'),
                    ('status', '报告状态')
                ]
            }
            
            for table_name, columns in column_comments.items():
                for column_name, comment in columns:
                    sql = f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} "
                    # 需要获取字段类型，这里简化处理
                    try:
                        self.cursor.execute(f"DESCRIBE {table_name} {column_name}")
                        desc = self.cursor.fetchone()
                        if desc:
                            col_type = desc['Type']
                            # 处理特殊情况
                            if column_name == 'id':
                                col_type = 'INT AUTO_INCREMENT PRIMARY KEY'
                            elif 'PRIMARY' in col_type:
                                col_type = col_type.replace('PRI', '').strip()
                            
                            sql_full = f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} {col_type} COMMENT '{comment}'"
                            self.cursor.execute(sql_full)
                    except Exception as e:
                        logger.debug(f'更新字段 {table_name}.{column_name} 注释失败: {e}')
            
            logger.info('表和字段注释更新成功')
        except Exception as e:
            logger.error(f'添加表注释失败: {e}')
    
    def _drop_incompatible_tables(self):
        """删除不兼容的旧表，以便重新创建正确的表结构"""
        try:
            # 检查qxc_trend_data表是否存在且缺少trend_values字段
            self.cursor.execute("DESCRIBE qxc_trend_data")
            columns = [col['Field'] for col in self.cursor.fetchall()]
            
            if 'trend_values' not in columns:
                logger.info('检测到不兼容的qxc_trend_data表结构，将删除重建')
                self.cursor.execute("DROP TABLE IF EXISTS qxc_trend_data")
                self.connection.commit()
        except Exception as e:
            # 表可能不存在，忽略错误
            pass
        
        try:
            # 检查qxc_detailed_report表是否存在且缺少report_uuid字段（旧结构）
            self.cursor.execute("DESCRIBE qxc_detailed_report")
            columns = [col['Field'] for col in self.cursor.fetchall()]
            
            if 'report_uuid' not in columns:
                logger.info('检测到不兼容的qxc_detailed_report表结构，将删除重建')
                self.cursor.execute("DROP TABLE IF EXISTS qxc_detailed_report")
                self.connection.commit()
        except Exception as e:
            # 表可能不存在，忽略错误
            pass
        
        try:
            # 检查qxc_final_report表是否存在且缺少detailed_report_id字段（旧结构）
            self.cursor.execute("DESCRIBE qxc_final_report")
            columns = [col['Field'] for col in self.cursor.fetchall()]
            
            if 'detailed_report_id' not in columns:
                logger.info('检测到不兼容的qxc_final_report表结构，将删除重建')
                self.cursor.execute("DROP TABLE IF EXISTS qxc_final_report")
                self.connection.commit()
        except Exception as e:
            # 表可能不存在，忽略错误
            pass
    
    def migrate_old_reports(self):
        """迁移历史报告数据到新表结构"""
        try:
            import uuid
            
            # 检查旧表是否存在
            self.cursor.execute("SHOW TABLES LIKE 'qxc_optimal_report'")
            has_optimal = self.cursor.fetchone()
            
            if has_optimal:
                logger.info('开始迁移历史报告数据')
                
                # 迁移详细报告数据（从旧表）
                self.cursor.execute("SHOW TABLES LIKE 'qxc_detailed_report'")
                has_detailed = self.cursor.fetchone()
                
                if has_detailed:
                    # 查询旧的详细报告数据
                    sql_select_detailed = '''
                    SELECT report_date, report_content, total_samples, 
                           frequency_analysis, probability_analysis, interval_analysis,
                           frequency_chart, probability_chart
                    FROM qxc_detailed_report
                    '''
                    self.cursor.execute(sql_select_detailed)
                    detailed_rows = self.cursor.fetchall()
                    
                    # 迁移到新结构
                    sql_insert_detailed = '''
                    INSERT INTO qxc_detailed_report 
                    (report_date, report_uuid, frequency_analysis, probability_analysis, 
                     interval_analysis, total_samples, report_content,
                     frequency_chart, probability_chart)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    '''
                    
                    for row in detailed_rows:
                        report_uuid = str(uuid.uuid4())
                        self.cursor.execute(sql_insert_detailed, (
                            row['report_date'],
                            report_uuid,
                            row['frequency_analysis'],
                            row['probability_analysis'],
                            row['interval_analysis'],
                            row['total_samples'],
                            row['report_content'],
                            row['frequency_chart'],
                            row['probability_chart']
                        ))
                
                # 迁移最优报告数据（从旧表）
                sql_select_optimal = '''
                SELECT report_date, report_content, recommended_numbers, 
                       confidence_score, analysis_summary,
                       frequency_chart, probability_chart
                FROM qxc_optimal_report
                '''
                self.cursor.execute(sql_select_optimal)
                optimal_rows = self.cursor.fetchall()
                
                # 迁移到新的最终报告表
                sql_insert_final = '''
                INSERT INTO qxc_final_report 
                (detailed_report_id, report_date, report_uuid, recommended_numbers,
                 confidence_score, analysis_summary, report_content,
                 frequency_chart, probability_chart, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                '''
                
                for row in optimal_rows:
                    # 获取对应的详细报告ID
                    self.cursor.execute(
                        'SELECT id FROM qxc_detailed_report WHERE report_date = %s',
                        (row['report_date'],)
                    )
                    detailed_result = self.cursor.fetchone()
                    detailed_report_id = detailed_result['id'] if detailed_result else None
                    
                    report_uuid = str(uuid.uuid4())
                    self.cursor.execute(sql_insert_final, (
                        detailed_report_id,
                        row['report_date'],
                        report_uuid,
                        row['recommended_numbers'],
                        row['confidence_score'],
                        row['analysis_summary'],
                        row['report_content'],
                        row['frequency_chart'],
                        row['probability_chart'],
                        'validated'
                    ))
                
                self.connection.commit()
                logger.info('历史报告数据迁移完成')
                
                # 删除旧表
                self.cursor.execute("DROP TABLE IF EXISTS qxc_optimal_report")
                self.connection.commit()
                logger.info('已删除旧的qxc_optimal_report表')
            
            return True
        except Exception as e:
            logger.error(f'数据迁移失败: {e}')
            return False
    
    def insert_or_update_history_data(self, data):
        """插入或更新历史开奖数据"""
        try:
            sql = '''
            INSERT INTO qxc_history_data 
            (issue, draw_date, num1, num2, num3, num4, num5, num6, special_num, 
             hezhi, hezhi_type, odd_even_ratio, odd_even_pattern, span)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                draw_date = VALUES(draw_date),
                num1 = VALUES(num1),
                num2 = VALUES(num2),
                num3 = VALUES(num3),
                num4 = VALUES(num4),
                num5 = VALUES(num5),
                num6 = VALUES(num6),
                special_num = VALUES(special_num),
                hezhi = VALUES(hezhi),
                hezhi_type = VALUES(hezhi_type),
                odd_even_ratio = VALUES(odd_even_ratio),
                odd_even_pattern = VALUES(odd_even_pattern),
                span = VALUES(span),
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
                        numbers[6],
                        item.get('hezhi', ''),
                        item.get('hezhi_type', ''),
                        item.get('odd_even_ratio', ''),
                        item.get('odd_even_pattern', ''),
                        item.get('span', '')
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
    
    def insert_or_update_trend_data(self, data):
        """插入或更新走势图数据"""
        try:
            import json
            
            sql = '''
            INSERT INTO qxc_trend_data (issue, trend_values)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                trend_values = VALUES(trend_values),
                updated_at = CURRENT_TIMESTAMP
            '''
            
            count = 0
            for item in data:
                try:
                    trend_json = json.dumps(item.get('trend', []))
                    self.cursor.execute(sql, (item['issue'], trend_json))
                    count += 1
                except Exception as e:
                    logger.error(f'插入走势图数据失败: {item["issue"]}, 错误: {e}')
            
            self.connection.commit()
            logger.info(f'成功插入/更新 {count} 条走势图数据')
            return count
        except Exception as e:
            logger.error(f'批量插入走势图数据失败: {e}')
            return 0
    
    def insert_detailed_report(self, report_content, total_samples, frequency_analysis, 
                              probability_analysis, interval_analysis,
                              hezhi_analysis=None, odd_even_analysis=None, span_analysis=None,
                              raw_data_snapshot=None, calculation_steps=None, analysis_params=None,
                              confidence_level=None, frequency_chart=None, probability_chart=None):
        """插入详细报告"""
        try:
            import uuid
            report_date = datetime.now().strftime('%Y-%m-%d')
            report_uuid = str(uuid.uuid4())
            
            sql = '''
            INSERT INTO qxc_detailed_report 
            (report_date, report_uuid, raw_data_snapshot, calculation_steps, analysis_params,
             frequency_analysis, probability_analysis, interval_analysis,
             hezhi_analysis, odd_even_analysis, span_analysis,
             total_samples, confidence_level, report_content,
             frequency_chart, probability_chart)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                raw_data_snapshot = VALUES(raw_data_snapshot),
                calculation_steps = VALUES(calculation_steps),
                analysis_params = VALUES(analysis_params),
                frequency_analysis = VALUES(frequency_analysis),
                probability_analysis = VALUES(probability_analysis),
                interval_analysis = VALUES(interval_analysis),
                hezhi_analysis = VALUES(hezhi_analysis),
                odd_even_analysis = VALUES(odd_even_analysis),
                span_analysis = VALUES(span_analysis),
                total_samples = VALUES(total_samples),
                confidence_level = VALUES(confidence_level),
                report_content = VALUES(report_content),
                frequency_chart = VALUES(frequency_chart),
                probability_chart = VALUES(probability_chart),
                updated_at = CURRENT_TIMESTAMP
            '''
            
            self.cursor.execute(sql, (
                report_date,
                report_uuid,
                raw_data_snapshot,
                calculation_steps,
                analysis_params,
                frequency_analysis,
                probability_analysis,
                interval_analysis,
                hezhi_analysis,
                odd_even_analysis,
                span_analysis,
                total_samples,
                confidence_level,
                report_content,
                frequency_chart,
                probability_chart
            ))
            self.connection.commit()
            logger.info('成功插入详细报告')
            return True
        except Exception as e:
            logger.error(f'插入详细报告失败: {e}')
            return False
    
    def insert_final_report(self, detailed_report_id, recommended_numbers, confidence_score, 
                           analysis_summary, key_conclusions=None, core_metrics=None, 
                           decision_recommendations=None, report_content=None,
                           frequency_chart=None, probability_chart=None, status='validated'):
        """插入最终报告"""
        try:
            import uuid
            report_date = datetime.now().strftime('%Y-%m-%d')
            report_uuid = str(uuid.uuid4())
            
            sql = '''
            INSERT INTO qxc_final_report 
            (detailed_report_id, report_date, report_uuid, recommended_numbers,
             confidence_score, analysis_summary, key_conclusions, core_metrics,
             decision_recommendations, report_content, frequency_chart, probability_chart, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                recommended_numbers = VALUES(recommended_numbers),
                confidence_score = VALUES(confidence_score),
                analysis_summary = VALUES(analysis_summary),
                key_conclusions = VALUES(key_conclusions),
                core_metrics = VALUES(core_metrics),
                decision_recommendations = VALUES(decision_recommendations),
                report_content = VALUES(report_content),
                frequency_chart = VALUES(frequency_chart),
                probability_chart = VALUES(probability_chart),
                status = VALUES(status)
            '''
            
            self.cursor.execute(sql, (
                detailed_report_id,
                report_date,
                report_uuid,
                recommended_numbers,
                confidence_score,
                analysis_summary,
                key_conclusions,
                core_metrics,
                decision_recommendations,
                report_content,
                frequency_chart,
                probability_chart,
                status
            ))
            self.connection.commit()
            logger.info('成功插入最终报告')
            return True
        except Exception as e:
            logger.error(f'插入最终报告失败: {e}')
            return False
    
    def insert_optimal_report(self, report_content, recommended_numbers, confidence_score, analysis_summary, 
                             frequency_chart=None, probability_chart=None):
        """插入最终最优报告（兼容旧接口）"""
        # 获取最新的详细报告ID
        try:
            sql = 'SELECT id FROM qxc_detailed_report ORDER BY id DESC LIMIT 1'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            detailed_report_id = result['id'] if result else None
            
            return self.insert_final_report(
                detailed_report_id=detailed_report_id,
                recommended_numbers=recommended_numbers,
                confidence_score=confidence_score,
                analysis_summary=analysis_summary,
                report_content=report_content,
                frequency_chart=frequency_chart,
                probability_chart=probability_chart,
                status='validated'
            )
        except Exception as e:
            logger.error(f'插入最优报告失败: {e}')
            return False
    
    def query_all_history_data(self):
        """查询所有历史数据"""
        try:
            sql = 'SELECT * FROM qxc_history_data ORDER BY issue DESC'
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            logger.info(f'查询到 {len(results)} 条开奖数据')
            return results
        except Exception as e:
            logger.error(f'查询数据失败: {e}')
            return []
    
    def get_history_data_count(self):
        """获取历史数据数量"""
        try:
            sql = 'SELECT COUNT(*) as count FROM qxc_history_data'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            return result['count'] if result else 0
        except Exception as e:
            logger.error(f'查询数据数量失败: {e}')
            return 0
    
    # ========== 兼容主程序调用的方法 ==========
    
    def query_all_qxc_data(self):
        """
        查询所有七星彩数据（兼容主程序调用）
        
        Returns:
            数据记录列表，每条记录包含numbers字段（列表格式）
        """
        try:
            sql = 'SELECT * FROM qxc_history_data ORDER BY issue DESC'
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            # 转换为分析器需要的格式
            formatted_results = []
            for row in results:
                numbers = [row['num1'], row['num2'], row['num3'], 
                           row['num4'], row['num5'], row['num6'], 
                           row['special_num']]
                formatted_results.append({
                    'issue': row['issue'],
                    'date': row['draw_date'],
                    'numbers': numbers,
                    'hezhi': row['hezhi'],
                    'hezhi_type': row['hezhi_type'],
                    'odd_even_ratio': row['odd_even_ratio'],
                    'odd_even_pattern': row['odd_even_pattern'],
                    'span': row['span']
                })
            
            logger.info(f'查询到 {len(formatted_results)} 条七星彩数据')
            return formatted_results
        except Exception as e:
            logger.error(f'查询七星彩数据失败: {e}')
            return []
    
    def get_qxc_data_count(self):
        """获取七星彩数据数量（兼容主程序调用）"""
        return self.get_history_data_count()
    
    def insert_or_update_qxc_data(self, data):
        """插入或更新七星彩数据（兼容主程序调用）"""
        return self.insert_or_update_history_data(data)
    
    def query_all(self):
        """查询所有数据（兼容旧代码）"""
        return self.query_all_qxc_data()
    
    def insert_or_update(self, data):
        """插入或更新数据（兼容旧代码）"""
        return self.insert_or_update_history_data(data)
    
    def insert_report(self, report_date, report_type, report_content, 
                     frequency_chart=None, probability_chart=None):
        """
        插入报告（兼容主程序调用）
        
        Args:
            report_date: 报告日期
            report_type: 报告类型（detailed/optimal）
            report_content: 报告内容
            frequency_chart: 频率图表字节流
            probability_chart: 概率图表字节流
        
        Returns:
            True表示成功，False表示失败
        """
        if report_type == 'optimal':
            # 提取推荐号码和置信度
            lines = report_content.split('\n')
            recommended_numbers = ''
            confidence_score = 0.0
            analysis_summary = ''
            
            for line in lines:
                if '推荐号码:' in line:
                    parts = line.split('推荐号码:')
                    if len(parts) > 1:
                        recommended_numbers = parts[1].strip()
                if '概率:' in line:
                    try:
                        parts = line.split('概率:')
                        if len(parts) > 1:
                            prob_str = parts[1].strip().replace('%', '')
                            confidence_score = float(prob_str) / 100
                    except:
                        pass
            
            return self.insert_optimal_report(
                report_content,
                recommended_numbers,
                confidence_score,
                analysis_summary,
                frequency_chart,
                probability_chart
            )
        else:
            # 详细报告
            return self.insert_detailed_report(
                report_content,
                self.get_qxc_data_count(),
                '',
                '',
                '',
                frequency_chart,
                probability_chart
            )
    
    def get_reports(self):
        """
        获取所有报告（兼容主程序调用）
        
        Returns:
            报告列表
        """
        reports = []
        
        # 获取最终报告（原最优报告）
        try:
            sql = 'SELECT * FROM qxc_final_report ORDER BY created_at DESC'
            self.cursor.execute(sql)
            final_reports = self.cursor.fetchall()
            for r in final_reports:
                r['report_type'] = 'optimal'
                reports.append(r)
        except Exception as e:
            logger.error(f'查询最终报告失败: {e}')
        
        # 获取详细报告
        try:
            sql = 'SELECT * FROM qxc_detailed_report ORDER BY created_at DESC'
            self.cursor.execute(sql)
            detailed_reports = self.cursor.fetchall()
            for r in detailed_reports:
                r['report_type'] = 'detailed'
                reports.append(r)
        except Exception as e:
            logger.error(f'查询详细报告失败: {e}')
        
        # 按创建时间排序
        reports.sort(key=lambda x: x['created_at'], reverse=True)
        return reports