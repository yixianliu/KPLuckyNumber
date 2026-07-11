"""
排列5数据库操作模块（完整版）

负责排列5数据的数据库连接、表结构管理、数据存储与查询
包含：历史数据表、走势数据表、AI分析报告表、预测验证记录表
"""

import pymysql
import logging
import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from contextlib import contextmanager

os.makedirs('logs', exist_ok=True)

# 说明：本模块负责数据库的全部操作。按照 AGENTS.md 的约定：
# - 在 connect() 中会尝试自动创建缺失的数据库（兼容新环境）
# - 在 create_tables() 中创建/兼容表结构时应保持向后兼容，不删除已有列
# 修改 schema 时请务必保留兼容性以保护历史数据。

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/database_p5.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def _safe_json_loads(raw):
    """安全解析 JSON 字符串, 失败返回原字符串或 None。"""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


class P5Database:
    """
    排列5数据库操作类（完整版）
    
    负责数据库连接、表结构管理、数据操作
    """
    # 该类封装了常用的数据库操作：连接/断开、事务、建表、插入/查询、报告入库、预测验证等。
    # 注意：为避免导入时失败，项目中有时使用延迟导入（在函数内部导入config或其他模块），本类保持该风格。
    
    def __init__(self):
        self.connection = None
        self.cursor = None
        self._in_transaction = False
    
    @contextmanager
    def transaction(self):
        """事务上下文管理器"""
        if not self.connection:
            self.connect()
        
        self._in_transaction = True
        try:
            yield self
            self.connection.commit()
            logger.debug('事务提交成功')
        except Exception as e:
            self.connection.rollback()
            logger.error(f'事务回滚: {e}')
            raise
        finally:
            self._in_transaction = False
    
    def connect(self):
        """连接MySQL数据库"""
        try:
            from config import DB_CONFIG
            db_name = DB_CONFIG['database']
            
            try:
                self.connection = pymysql.connect(
                    host=DB_CONFIG['host'],
                    user=DB_CONFIG['user'],
                    password=DB_CONFIG['password'],
                    database=db_name,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10,
                    read_timeout=30,
                    write_timeout=30
                )
                self.cursor = self.connection.cursor()
                logger.info('MySQL数据库连接成功（排列5）')
                return True
            except pymysql.err.OperationalError as e:
                error_code = e.args[0] if e.args else 0
                
                # 处理"MySQL server has gone away"错误(错误码2006)
                if error_code == 2006 or "MySQL server has gone away" in str(e):
                    logger.warning('MySQL连接已断开，尝试重连...')
                    try:
                        import time
                        time.sleep(1)  # 等待1秒后重试
                        self.connection = pymysql.connect(
                            host=DB_CONFIG['host'],
                            user=DB_CONFIG['user'],
                            password=DB_CONFIG['password'],
                            database=db_name,
                            charset='utf8mb4',
                            cursorclass=pymysql.cursors.DictCursor,
                            connect_timeout=10,
                            read_timeout=30,
                            write_timeout=30
                        )
                        self.cursor = self.connection.cursor()
                        logger.info('MySQL重连成功')
                        return True
                    except Exception as reconnect_error:
                        logger.error(f'MySQL重连失败: {reconnect_error}')
                        return False
                
                # 处理"Unknown database"错误
                if "Unknown database" in str(e):
                    logger.info(f'数据库 {db_name} 不存在，尝试自动创建...')
                    conn = pymysql.connect(
                        host=DB_CONFIG['host'],
                        user=DB_CONFIG['user'],
                        password=DB_CONFIG['password'],
                        charset='utf8mb4',
                        cursorclass=pymysql.cursors.DictCursor,
                        connect_timeout=10
                    )
                    cursor = conn.cursor()
                    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                    conn.commit()
                    cursor.close()
                    conn.close()
                    
                    self.connection = pymysql.connect(
                        host=DB_CONFIG['host'],
                        user=DB_CONFIG['user'],
                        password=DB_CONFIG['password'],
                        database=db_name,
                        charset='utf8mb4',
                        cursorclass=pymysql.cursors.DictCursor,
                        connect_timeout=10
                    )
                    self.cursor = self.connection.cursor()
                    logger.info(f'数据库 {db_name} 创建成功并已连接（排列5）')
                    return True
                raise
        except Exception as e:
            logger.error(f'MySQL数据库连接失败: {e}')
            return False
    
    def disconnect(self):
        """断开数据库连接"""
        if self.connection:
            self.connection.close()
            logger.info('MySQL数据库连接已关闭')

    def execute_with_reconnect(self, query, params=None):
        """
        执行SQL查询，自动处理连接超时并重连
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            查询结果，失败返回None
        """
        try:
            if params:
                return self.cursor.execute(query, params)
            else:
                return self.cursor.execute(query)
        except pymysql.err.OperationalError as e:
            error_code = e.args[0] if e.args else 0
            
            # 处理"MySQL server has gone away"
            if error_code == 2006 or "MySQL server has gone away" in str(e):
                logger.warning('检测到连接断开，尝试重连...')
                
                # 关闭旧游标和连接
                if self.cursor:
                    try:
                        self.cursor.close()
                    except:
                        pass
                
                # 重新连接
                if self.connect():
                    logger.info('重连成功，重试查询...')
                    # 重试一次
                    try:
                        if params:
                            return self.cursor.execute(query, params)
                        else:
                            return self.cursor.execute(query)
                    except Exception as retry_error:
                        logger.error(f'重连后查询仍失败: {retry_error}')
                        return None
                else:
                    logger.error('重连失败')
                    return None
            raise
    
    def create_tables(self):
        """创建排列5数据表（完整版）"""
        try:
            if not self.connection:
                self.connect()
            
            # 历史开奖数据表
            sql_history = '''
            CREATE TABLE IF NOT EXISTS p5_history_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                wan TINYINT NOT NULL COMMENT '万位号码(0-9)',
                qian TINYINT NOT NULL COMMENT '千位号码(0-9)',
                bai TINYINT NOT NULL COMMENT '百位号码(0-9)',
                shi TINYINT NOT NULL COMMENT '十位号码(0-9)',
                ge TINYINT NOT NULL COMMENT '个位号码(0-9)',
                hezhi INT NULL DEFAULT NULL COMMENT '和值',
                span INT NULL DEFAULT NULL COMMENT '跨度',
                odd_even_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '奇偶比',
                odd_even_pattern VARCHAR(10) NULL DEFAULT NULL COMMENT '奇偶模式',
                big_small_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '大小比',
                is_valid TINYINT(1) NOT NULL DEFAULT 1 COMMENT '数据有效性(1=有效,0=无效)',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5历史开奖数据表';
            '''
            self.cursor.execute(sql_history)
            
            # 走势图数据表
            sql_trend = '''
            CREATE TABLE IF NOT EXISTS p5_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                wan TINYINT NOT NULL COMMENT '万位号码',
                qian TINYINT NOT NULL COMMENT '千位号码',
                bai TINYINT NOT NULL COMMENT '百位号码',
                shi TINYINT NOT NULL COMMENT '十位号码',
                ge TINYINT NOT NULL COMMENT '个位号码',
                hezhi VARCHAR(10) NULL DEFAULT NULL COMMENT '和值',
                odd_even_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '奇偶比',
                big_small_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '大小比',
                prime_composite_ratio VARCHAR(10) NULL DEFAULT NULL COMMENT '质合比',
                wan_omission INT NULL DEFAULT 0 COMMENT '万位遗漏值',
                qian_omission INT NULL DEFAULT 0 COMMENT '千位遗漏值',
                bai_omission INT NULL DEFAULT 0 COMMENT '百位遗漏值',
                shi_omission INT NULL DEFAULT 0 COMMENT '十位遗漏值',
                ge_omission INT NULL DEFAULT 0 COMMENT '个位遗漏值',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '走势图JSON数据',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5走势图数据表';
            '''
            self.cursor.execute(sql_trend)
            
            # AI分析报告表
            sql_ai_report = '''
            CREATE TABLE IF NOT EXISTS p5_ai_report (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                report_date VARCHAR(20) NULL DEFAULT NULL COMMENT '报告日期',
                report_uuid VARCHAR(36) NULL DEFAULT NULL COMMENT '报告唯一标识',
                data_count INT NULL DEFAULT NULL COMMENT '分析数据条数',
                latest_issue VARCHAR(20) NULL DEFAULT NULL COMMENT '分析时的最新期号',
                next_issue VARCHAR(20) NULL DEFAULT NULL COMMENT '预测目标期号',
                trend_analysis LONGTEXT NULL DEFAULT NULL COMMENT '趋势分析结果(JSON)',
                probability_stats LONGTEXT NULL DEFAULT NULL COMMENT '概率统计数据(JSON)',
                recommended_numbers TEXT NULL DEFAULT NULL COMMENT '推荐号码列表(JSON)',
                recommended_combinations TEXT NULL DEFAULT NULL COMMENT '推荐组合列表(JSON)',
                confidence_scores TEXT NULL DEFAULT NULL COMMENT '置信度分数列表(JSON)',
                recommendation_reasons TEXT NULL DEFAULT NULL COMMENT '推荐理由',
                key_conclusions TEXT NULL DEFAULT NULL COMMENT '关键结论',
                risk_warning TEXT NULL DEFAULT NULL COMMENT '风险提示',
                report_content LONGTEXT NULL DEFAULT NULL COMMENT '完整报告内容',
                report_format VARCHAR(20) NULL DEFAULT 'TEXT' COMMENT '报告格式(JSON/HTML/TEXT)',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_report_uuid (report_uuid ASC) USING BTREE,
                INDEX idx_report_date (report_date ASC) USING BTREE,
                INDEX idx_latest_issue (latest_issue ASC) USING BTREE,
                INDEX idx_next_issue (next_issue ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5AI分析报告表';
            '''
            self.cursor.execute(sql_ai_report)

            # 安全扩展: 为 p5_ai_report 增加 report_type 列(区分 expert_article/trend_chart/final)。
            # 由于 CREATE TABLE IF NOT EXISTS 不会给已存在的表加列, 这里用 ALTER 补齐,
            # 忽略 "Duplicate column" (1060) 错误以兼容老表。
            try:
                self.cursor.execute(
                    "ALTER TABLE p5_ai_report "
                    "ADD COLUMN report_type VARCHAR(20) NULL DEFAULT 'final' "
                    "COMMENT '报告类型(expert_article/trend_chart/final)'"
                )
                logger.info('p5_ai_report.report_type 列已添加')
            except Exception as e:
                err_code = getattr(e, 'args', (None,))[0]
                if err_code == 1060:  # Duplicate column name
                    pass
                else:
                    logger.warning(f'添加 report_type 列跳过(非致命): {e}')
            
            # 预测验证记录表
            sql_prediction = '''
            CREATE TABLE IF NOT EXISTS p5_prediction_record (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                report_uuid VARCHAR(36) NOT NULL COMMENT '关联的报告UUID',
                target_issue VARCHAR(20) NOT NULL COMMENT '预测目标期号',
                predicted_numbers TEXT NULL DEFAULT NULL COMMENT '预测号码(JSON格式，各位置推荐)',
                predicted_combinations TEXT NULL DEFAULT NULL COMMENT '预测组合列表(JSON)',
                confidence_scores TEXT NULL DEFAULT NULL COMMENT '各位置置信度(JSON)',
                actual_numbers TEXT NULL DEFAULT NULL COMMENT '实际开奖号码(JSON)',
                actual_issue VARCHAR(20) NULL DEFAULT NULL COMMENT '实际开奖期号',
                is_matched TINYINT(1) NULL DEFAULT NULL COMMENT '是否完全猜中(1=是,0=否)',
                match_count INT NULL DEFAULT 0 COMMENT '命中位数(0-5)',
                match_details TEXT NULL DEFAULT NULL COMMENT '各位置命中详情(JSON)',
                wan_match TINYINT(1) NULL DEFAULT 0 COMMENT '万位是否命中',
                qian_match TINYINT(1) NULL DEFAULT 0 COMMENT '千位是否命中',
                bai_match TINYINT(1) NULL DEFAULT 0 COMMENT '百位是否命中',
                shi_match TINYINT(1) NULL DEFAULT 0 COMMENT '十位是否命中',
                ge_match TINYINT(1) NULL DEFAULT 0 COMMENT '个位是否命中',
                deviation_analysis TEXT NULL DEFAULT NULL COMMENT '偏差分析',
                accuracy_rate DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '准确率(命中位数/5*100)',
                verification_status VARCHAR(20) NULL DEFAULT 'pending' COMMENT '验证状态(pending/verified/failed)',
                verified_at TIMESTAMP NULL DEFAULT NULL COMMENT '验证时间',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_report_issue (report_uuid ASC, target_issue ASC) USING BTREE,
                INDEX idx_target_issue (target_issue ASC) USING BTREE,
                INDEX idx_verification_status (verification_status ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5预测验证记录表';
            '''
            self.cursor.execute(sql_prediction)
            
            # ★ 预测验证明细记录表 (新增)
            sql_verification_detail = '''
            CREATE TABLE IF NOT EXISTS p5_verification_detail (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                verification_id INT NOT NULL COMMENT '关联验证记录ID(p5_prediction_record.id)',
                issue VARCHAR(20) NOT NULL COMMENT '期号',
                position VARCHAR(10) NOT NULL COMMENT '位置(wan/qian/bai/shi/ge)',
                predicted_numbers TEXT NULL DEFAULT NULL COMMENT '预测号码(JSON数组)',
                actual_number INT NULL DEFAULT NULL COMMENT '实际开奖号码',
                is_hit TINYINT(1) NULL DEFAULT 0 COMMENT '是否命中(1=是,0=否)',
                tolerance_hit TINYINT(1) NULL DEFAULT 0 COMMENT '容错命中(偏差±1,1=是,0=否)',
                deviation INT NULL DEFAULT NULL COMMENT '偏差值(实际-预测)',
                algo_name VARCHAR(50) NULL DEFAULT NULL COMMENT '算法名称',
                confidence DECIMAL(5,4) NULL DEFAULT NULL COMMENT '置信度',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                INDEX idx_verification_id (verification_id ASC) USING BTREE,
                INDEX idx_issue_position (issue ASC, position ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5预测验证明细记录表';
            '''
            self.cursor.execute(sql_verification_detail)
            
            # 预测性能统计表
            sql_performance = '''
            CREATE TABLE IF NOT EXISTS p5_performance_stats (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                stat_date VARCHAR(20) NULL DEFAULT NULL COMMENT '统计日期',
                total_predictions INT NULL DEFAULT 0 COMMENT '总预测次数',
                total_matched INT NULL DEFAULT 0 COMMENT '完全猜中次数',
                total_partial_match INT NULL DEFAULT 0 COMMENT '部分命中次数',
                avg_match_count DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '平均命中位数',
                wan_accuracy DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '万位命中率',
                qian_accuracy DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '千位命中率',
                bai_accuracy DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '百位命中率',
                shi_accuracy DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '十位命中率',
                ge_accuracy DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '个位命中率',
                overall_accuracy DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '综合命中率',
                best_streak INT NULL DEFAULT 0 COMMENT '最长连中次数',
                current_streak INT NULL DEFAULT 0 COMMENT '当前连中次数',
                performance_json LONGTEXT NULL DEFAULT NULL COMMENT '详细性能数据(JSON)',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_stat_date (stat_date ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5预测性能统计表';
            '''
            self.cursor.execute(sql_performance)
            
            # 万位走势数据表
            sql_wan_trend = '''
            CREATE TABLE IF NOT EXISTS p5_wan_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                wan_number TINYINT NOT NULL COMMENT '万位数字(0-9)',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                is_odd TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
                is_big TINYINT(1) NULL DEFAULT NULL COMMENT '是否大数(>=5为大,1=是,0=否)',
                is_prime TINYINT(1) NULL DEFAULT NULL COMMENT '是否质数(1=是,0=否)',
                omission INT NULL DEFAULT 0 COMMENT '当前遗漏值',
                hot_level VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
                consecutive_count INT NULL DEFAULT 0 COMMENT '连续出现次数',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '万位走势JSON数据',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_wan_number (wan_number ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5万位走势图数据表';
            '''
            self.cursor.execute(sql_wan_trend)
            
            # 千位走势数据表
            sql_qian_trend = '''
            CREATE TABLE IF NOT EXISTS p5_qian_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                qian_number TINYINT NOT NULL COMMENT '千位数字(0-9)',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                is_odd TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
                is_big TINYINT(1) NULL DEFAULT NULL COMMENT '是否大数(>=5为大,1=是,0=否)',
                is_prime TINYINT(1) NULL DEFAULT NULL COMMENT '是否质数(1=是,0=否)',
                omission INT NULL DEFAULT 0 COMMENT '当前遗漏值',
                hot_level VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
                consecutive_count INT NULL DEFAULT 0 COMMENT '连续出现次数',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '千位走势JSON数据',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_qian_number (qian_number ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5千位走势图数据表';
            '''
            self.cursor.execute(sql_qian_trend)
            
            # 百位走势数据表
            sql_bai_trend = '''
            CREATE TABLE IF NOT EXISTS p5_bai_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                bai_number TINYINT NOT NULL COMMENT '百位数字(0-9)',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                is_odd TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
                is_big TINYINT(1) NULL DEFAULT NULL COMMENT '是否大数(>=5为大,1=是,0=否)',
                is_prime TINYINT(1) NULL DEFAULT NULL COMMENT '是否质数(1=是,0=否)',
                omission INT NULL DEFAULT 0 COMMENT '当前遗漏值',
                hot_level VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
                consecutive_count INT NULL DEFAULT 0 COMMENT '连续出现次数',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '百位走势JSON数据',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_bai_number (bai_number ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5百位走势图数据表';
            '''
            self.cursor.execute(sql_bai_trend)
            
            # 十位走势数据表
            sql_shi_trend = '''
            CREATE TABLE IF NOT EXISTS p5_shi_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                shi_number TINYINT NOT NULL COMMENT '十位数字(0-9)',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                is_odd TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
                is_big TINYINT(1) NULL DEFAULT NULL COMMENT '是否大数(>=5为大,1=是,0=否)',
                is_prime TINYINT(1) NULL DEFAULT NULL COMMENT '是否质数(1=是,0=否)',
                omission INT NULL DEFAULT 0 COMMENT '当前遗漏值',
                hot_level VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
                consecutive_count INT NULL DEFAULT 0 COMMENT '连续出现次数',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '十位走势JSON数据',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_shi_number (shi_number ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5十位走势图数据表';
            '''
            self.cursor.execute(sql_shi_trend)
            
            # 个位走势数据表
            sql_ge_trend = '''
            CREATE TABLE IF NOT EXISTS p5_ge_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                ge_number TINYINT NOT NULL COMMENT '个位数字(0-9)',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                is_odd TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
                is_big TINYINT(1) NULL DEFAULT NULL COMMENT '是否大数(>=5为大,1=是,0=否)',
                is_prime TINYINT(1) NULL DEFAULT NULL COMMENT '是否质数(1=是,0=否)',
                omission INT NULL DEFAULT 0 COMMENT '当前遗漏值',
                hot_level VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
                consecutive_count INT NULL DEFAULT 0 COMMENT '连续出现次数',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '个位走势JSON数据',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_ge_number (ge_number ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5个位走势图数据表';
            '''
            self.cursor.execute(sql_ge_trend)

            # ★ 兼容迁移: 旧版曾以"后三/和尾"schema 创建过 p5_spjzs_data / p5_hzzst_data,
            #   与当前"基于实际爬取5位号码"的 schema(wan/qian/bai/shi/ge/hezhi/...)不兼容。
            #   若旧表存在且不带 wan 列(即旧schema), 先 DROP 再交由下方 CREATE 重建,
            #   避免 CREATE TABLE IF NOT EXISTS 失效导致后续 INSERT 列不匹配。
            #   (仅当表为空/旧schema时重建, 已含 wan 列的正确表不受影响)
            for _t in ('p5_spjzs_data', 'p5_hzzst_data'):
                try:
                    self.cursor.execute(f'SELECT wan FROM {_t} LIMIT 0')
                except Exception:
                    # 表不存在 或 无 wan 列(旧schema) -> 重建
                    try:
                        self.cursor.execute(f'DROP TABLE IF EXISTS {_t}')
                        self.connection.commit()
                        logger.warning(f'检测到 {_t} 为旧schema/缺失, 已重建为新schema(基于爬取5位号码)')
                    except Exception as _e2:
                        logger.warning(f'重建 {_t} 失败(非致命): {_e2}')

            # 升平降走势数据表（一定牛 spjzs 图表, 通过 GraphQL get_trend_result 抓取）
            # 字段以爬取到的实际数据为准: issue_name(期号) + issue_number(5位号码) + 各位置升平降遗漏数组。
            # wan/qian/bai/shi/ge/hezhi/hewei/kuadu/avg 由 issue_number 本地派生(更稳健),
            # miss_json 保留升平降遗漏原始数据供深入分析。
            sql_spjzs_trend = '''
            CREATE TABLE IF NOT EXISTS p5_spjzs_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                numbers VARCHAR(10) NULL DEFAULT NULL COMMENT '5位开奖号码',
                wan TINYINT NULL DEFAULT NULL COMMENT '万位号码(0-9)',
                qian TINYINT NULL DEFAULT NULL COMMENT '千位号码(0-9)',
                bai TINYINT NULL DEFAULT NULL COMMENT '百位号码(0-9)',
                shi TINYINT NULL DEFAULT NULL COMMENT '十位号码(0-9)',
                ge TINYINT NULL DEFAULT NULL COMMENT '个位数字(0-9)',
                hezhi INT NULL DEFAULT NULL COMMENT '和数值',
                hewei TINYINT NULL DEFAULT NULL COMMENT '和尾值',
                kuadu TINYINT NULL DEFAULT NULL COMMENT '跨度值',
                avg DECIMAL(6,2) NULL DEFAULT NULL COMMENT '平均值(和值/5)',
                miss_json LONGTEXT NULL DEFAULT NULL COMMENT '升平降遗漏原始数据(JSON)',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_hezhi (hezhi ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5升平降走势图数据表';
            '''
            self.cursor.execute(sql_spjzs_trend)

            # 和值走势数据表（一定牛 hzzst 图表, 通过 GraphQL get_trend_result 抓取）
            # 字段以爬取到的实际数据为准: issue_name(期号) + issue_number(5位号码) + 和值/和尾遗漏数组。
            sql_hzzst_trend = '''
            CREATE TABLE IF NOT EXISTS p5_hzzst_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                numbers VARCHAR(10) NULL DEFAULT NULL COMMENT '5位开奖号码',
                wan TINYINT NULL DEFAULT NULL COMMENT '万位号码(0-9)',
                qian TINYINT NULL DEFAULT NULL COMMENT '千位号码(0-9)',
                bai TINYINT NULL DEFAULT NULL COMMENT '百位号码(0-9)',
                shi TINYINT NULL DEFAULT NULL COMMENT '十位号码(0-9)',
                ge TINYINT NULL DEFAULT NULL COMMENT '个位数字(0-9)',
                hezhi INT NULL DEFAULT NULL COMMENT '和值',
                kuadu TINYINT NULL DEFAULT NULL COMMENT '跨度',
                hewei TINYINT NULL DEFAULT NULL COMMENT '和尾',
                miss_json LONGTEXT NULL DEFAULT NULL COMMENT '和值/和尾走势遗漏原始数据(JSON)',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_hezhi (hezhi ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5和值走势图数据表';
            '''
            self.cursor.execute(sql_hzzst_trend)

            # 贝叶斯推断结果专用表 (v3.5 新增, 按 issue 增量持久化, 避免每次重算/调AI)
            sql_bayesian = '''
            CREATE TABLE IF NOT EXISTS p5_bayesian_result (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '计算所基于的最新开奖期号(唯一)',
                target_issue VARCHAR(20) NULL DEFAULT NULL COMMENT '预测目标期号',
                bayes_json MEDIUMTEXT NOT NULL COMMENT '各位置后验概率分布 JSON: List[Dict[int,float]] (万/千/百/十/个)',
                top_numbers_json VARCHAR(255) NULL DEFAULT NULL COMMENT '各位置概率最高的号码 JSON: [wan,qian,bai,shi,ge]',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5贝叶斯推断后验概率专用表(增量复用)';
            '''
            self.cursor.execute(sql_bayesian)

            # 和尾走势数据表
            sql_sum_end_trend = '''
            CREATE TABLE IF NOT EXISTS p5_sum_end_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                sum_end TINYINT NOT NULL COMMENT '和尾值(0-9)',
                sum_value INT NULL DEFAULT NULL COMMENT '和值',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                is_odd TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
                is_big TINYINT(1) NULL DEFAULT NULL COMMENT '是否大尾(>=5为大,1=是,0=否)',
                omission INT NULL DEFAULT 0 COMMENT '当前遗漏值',
                hot_level VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
                consecutive_count INT NULL DEFAULT 0 COMMENT '连续出现次数',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '和尾走势JSON数据',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_sum_end (sum_end ASC) USING BTREE,
                INDEX idx_sum_value (sum_value ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5和尾走势图数据表';
            '''
            self.cursor.execute(sql_sum_end_trend)
            
            # 后三走势数据表
            sql_back_three_trend = '''
            CREATE TABLE IF NOT EXISTS p5_back_three_trend_data (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号（唯一）',
                bai_number TINYINT NOT NULL COMMENT '百位数字(0-9)',
                shi_number TINYINT NOT NULL COMMENT '十位数字(0-9)',
                ge_number TINYINT NOT NULL COMMENT '个位数字(0-9)',
                back_three_value VARCHAR(10) NULL DEFAULT NULL COMMENT '后三数值(XXX)',
                sum_value INT NULL DEFAULT NULL COMMENT '后三和值',
                sum_end TINYINT NULL DEFAULT NULL COMMENT '后三和尾',
                draw_date VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
                is_odd TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
                is_big TINYINT(1) NULL DEFAULT NULL COMMENT '是否大数(>=5为大,1=是,0=否)',
                omission INT NULL DEFAULT 0 COMMENT '当前遗漏值',
                hot_level VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
                consecutive_count INT NULL DEFAULT 0 COMMENT '连续出现次数',
                trend_json LONGTEXT NULL DEFAULT NULL COMMENT '后三走势JSON数据',
                source VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE,
                INDEX idx_bai_number (bai_number ASC) USING BTREE,
                INDEX idx_shi_number (shi_number ASC) USING BTREE,
                INDEX idx_ge_number (ge_number ASC) USING BTREE,
                INDEX idx_sum_end (sum_end ASC) USING BTREE,
                INDEX idx_draw_date (draw_date ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5后三走势图数据表';
            '''
            self.cursor.execute(sql_back_three_trend)
            
            sql_expert_recommendation = '''
            CREATE TABLE IF NOT EXISTS p5_expert_recommendation (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                user_id INT NOT NULL COMMENT '专家用户ID',
                nick_name VARCHAR(50) NULL DEFAULT NULL COMMENT '专家昵称',
                head_url VARCHAR(255) NULL DEFAULT NULL COMMENT '专家头像URL',
                issue_name VARCHAR(20) NOT NULL COMMENT '预测期号名称',
                issue_no VARCHAR(20) NULL DEFAULT NULL COMMENT '期号编号',
                issue_end_time VARCHAR(30) NULL DEFAULT NULL COMMENT '期号截止时间',
                issue_open_time VARCHAR(30) NULL DEFAULT NULL COMMENT '开奖时间',
                summary LONGTEXT NULL DEFAULT NULL COMMENT '推荐总结',
                intro TEXT NULL DEFAULT NULL COMMENT '专家介绍',
                create_time VARCHAR(30) NULL DEFAULT NULL COMMENT '推荐创建时间',
                scheme_count INT NULL DEFAULT 0 COMMENT '方案数量',
                schemes_json LONGTEXT NULL DEFAULT NULL COMMENT '方案列表(JSON)',
                hit_ratio VARCHAR(20) NULL DEFAULT NULL COMMENT '专家命中率',
                hit_count INT NULL DEFAULT 0 COMMENT '命中次数',
                serial_hit_count INT NULL DEFAULT 0 COMMENT '连续命中次数',
                detail_url VARCHAR(255) NULL DEFAULT NULL COMMENT '专家详情URL',
                source VARCHAR(50) NULL DEFAULT 'china_lottery' COMMENT '数据来源',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_user_issue (user_id ASC, issue_name ASC) USING BTREE,
                INDEX idx_user_id (user_id ASC) USING BTREE,
                INDEX idx_issue_name (issue_name ASC) USING BTREE,
                INDEX idx_created_at (created_at ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5专家推荐数据表';
            '''
            self.cursor.execute(sql_expert_recommendation)
            
            sql_weight_history = '''
            CREATE TABLE IF NOT EXISTS p5_weight_history (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                algo_name VARCHAR(50) NOT NULL COMMENT '算法名称(frequency_weighted/omission_regression等)',
                position VARCHAR(10) NOT NULL COMMENT '位置(wan/qian/bai/shi/ge/all)',
                weight_type VARCHAR(20) NOT NULL DEFAULT 'prior' COMMENT '权重类型(prior/posterior/adaptive)',
                number_value TINYINT NULL DEFAULT NULL COMMENT '号码值(0-9,部分位置为NULL)',
                weight_value DECIMAL(10,6) NOT NULL COMMENT '权重值',
                prior_probability DECIMAL(10,6) NULL DEFAULT NULL COMMENT '先验概率(贝叶斯)',
                likelihood DECIMAL(10,6) NULL DEFAULT NULL COMMENT '似然值(贝叶斯)',
                posterior_probability DECIMAL(10,6) NULL DEFAULT NULL COMMENT '后验概率(贝叶斯)',
                evidence_count INT NULL DEFAULT 1 COMMENT '证据累计次数',
                validation_result VARCHAR(20) NULL DEFAULT NULL COMMENT '验证结果(hit/miss/partial)',
                match_count INT NULL DEFAULT 0 COMMENT '命中位数',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                INDEX idx_algo_position (algo_name, position) USING BTREE,
                INDEX idx_created_at (created_at DESC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5算法权重历史记录表';
            '''
            self.cursor.execute(sql_weight_history)
            
            # ★ 在线学习历史记录表 (新增)
            sql_learning_history = '''
            CREATE TABLE IF NOT EXISTS p5_learning_history (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                learning_type VARCHAR(50) NOT NULL COMMENT '学习类型(weight_update/pattern_discovery/expert_credit)',
                issue VARCHAR(20) NULL DEFAULT NULL COMMENT '关联期号',
                algo_name VARCHAR(50) NULL DEFAULT NULL COMMENT '算法名称',
                position VARCHAR(10) NULL DEFAULT NULL COMMENT '位置',
                old_value TEXT NULL DEFAULT NULL COMMENT '变更前值(JSON)',
                new_value TEXT NULL DEFAULT NULL COMMENT '变更后值(JSON)',
                change_reason TEXT NULL DEFAULT NULL COMMENT '变更原因',
                confidence DECIMAL(5,4) NULL DEFAULT NULL COMMENT '置信度',
                verified_result VARCHAR(20) NULL DEFAULT NULL COMMENT '验证结果(hit/miss/partial)',
                impact_score DECIMAL(5,2) NULL DEFAULT 0.00 COMMENT '影响评分(-100到100)',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                INDEX idx_learning_type (learning_type ASC) USING BTREE,
                INDEX idx_issue (issue ASC) USING BTREE,
                INDEX idx_created_at (created_at DESC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5在线学习历史记录表';
            '''
            self.cursor.execute(sql_learning_history)

            # ★ 运行时产物统一存储表 (v3.3 新增)
            # 用于替代所有运行时生成的 JSON 文件(在线学习报告/验证报告/贝叶斯结果/权重历史/
            # 自适应权重/预测结果/特征分析/回测报告等), 统一持久化到数据库, 并保留元信息。
            sql_artifact = '''
            CREATE TABLE IF NOT EXISTS p5_artifact (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                artifact_type VARCHAR(40) NOT NULL COMMENT '数据类型(weight_history/adaptive_weights/prediction/feature_analysis/backtest_report/bayesian_result/learning_report/verification_report等)',
                issue VARCHAR(20) NULL DEFAULT NULL COMMENT '关联期号',
                ref_uuid VARCHAR(36) NULL DEFAULT NULL COMMENT '关联UUID(如report_uuid)',
                data_json MEDIUMTEXT NOT NULL COMMENT '完整内容JSON',
                meta_json MEDIUMTEXT NULL COMMENT '元信息JSON(来源/算法版本/数据量等)',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '生成时间',
                PRIMARY KEY (id) USING BTREE,
                INDEX idx_type (artifact_type ASC) USING BTREE,
                INDEX idx_issue (issue ASC) USING BTREE,
                INDEX idx_ref (ref_uuid ASC) USING BTREE,
                INDEX idx_created (created_at DESC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5运行时产物统一存储表';
            '''
            self.cursor.execute(sql_artifact)

            self.connection.commit()
            logger.info('排列5数据表创建成功（历史数据、走势数据、AI报告、预测验证、验证明细、性能统计、万位走势、千位走势、百位走势、十位走势、和尾走势、后三走势、专家推荐、权重历史、学习历史、运行时产物）')
            return True
        except Exception as e:
            logger.error(f'创建数据表失败: {e}')
            return False
    
    # ============================================================
    # 历史数据操作
    # ============================================================
    
    def insert_history_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入历史开奖数据（智能去重）
        
        Args:
            data: 历史数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            # 获取已有期号
            self.cursor.execute('SELECT issue FROM p5_history_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_history_data 
            (issue, draw_date, wan, qian, bai, shi, ge, hezhi, span, 
             odd_even_ratio, odd_even_pattern, big_small_ratio, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue or issue in existing_issues:
                    skip_count += 1
                    continue
                
                numbers = item.get('numbers', [])
                if len(numbers) != 5:
                    skip_count += 1
                    continue
                
                # 数据校验
                try:
                    wan, qian, bai, shi, ge = [int(n) for n in numbers]
                    if not all(0 <= n <= 9 for n in [wan, qian, bai, shi, ge]):
                        skip_count += 1
                        continue
                except (ValueError, TypeError):
                    skip_count += 1
                    continue
                
                hezhi = sum([wan, qian, bai, shi, ge])
                span = max([wan, qian, bai, shi, ge]) - min([wan, qian, bai, shi, ge])
                
                odd_count = sum(1 for n in [wan, qian, bai, shi, ge] if n % 2 == 1)
                odd_even_ratio = f"{odd_count}:{5-odd_count}"
                odd_even_pattern = ''.join(['奇' if n % 2 == 1 else '偶' for n in [wan, qian, bai, shi, ge]])
                
                big_count = sum(1 for n in [wan, qian, bai, shi, ge] if n >= 5)
                big_small_ratio = f"{big_count}:{5-big_count}"
                
                self.cursor.execute(sql, (
                    issue, item.get('date', ''), wan, qian, bai, shi, ge,
                    hezhi, span, odd_even_ratio, odd_even_pattern, big_small_ratio,
                    item.get('source', 'spider')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'历史数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入历史数据失败: {e}')
            return 0, len(data)
    
    def get_latest_history_issue(self) -> Optional[str]:
        """获取数据库中最新的历史开奖期号"""
        try:
            sql = 'SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            return result['issue'] if result else None
        except Exception as e:
            logger.error(f'获取最新历史期号失败: {e}')
            return None
    
    def get_latest_trend_issue(self) -> Optional[str]:
        """获取数据库中最新的走势数据期号"""
        try:
            sql = 'SELECT issue FROM p5_trend_data ORDER BY issue DESC LIMIT 1'
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            return result['issue'] if result else None
        except Exception as e:
            logger.error(f'获取最新走势期号失败: {e}')
            return None
    
    def get_history_data(self, limit: int = 500, order_by: str = 'issue DESC',
                         order: str = None) -> List[Dict[str, Any]]:
        """
        获取历史开奖数据

        Args:
            limit: 返回数量限制; 为 None 时返回全部
            order_by: 排序方式(内部固定列, 非用户输入)
            order: 兼容旧调用, 'ASC'/'DESC' 简写, 会被转换为 order_by
        """
        try:
            # 兼容旧调用: order='ASC' -> 'issue ASC'
            ob = order_by
            if order:
                direction = 'ASC' if str(order).upper() == 'ASC' else 'DESC'
                ob = f'issue {direction}'

            sql = 'SELECT * FROM p5_history_data WHERE is_valid = 1'
            params = []
            if limit is not None:
                sql += f' ORDER BY {ob} LIMIT %s'
                params.append(limit)
            else:
                sql += f' ORDER BY {ob}'

            # 使用带自动重连的查询, 避免长时运行后 "MySQL server has gone away"
            self.execute_with_reconnect(sql, params if params else None)
            rows = self.cursor.fetchall() or []

            # 统一补上 numbers 字段: 数据库将号码拆分为 wan/qian/bai/shi/ge 五列,
            # 但 features / backtester / predictor 都依赖 row['numbers'] 数组格式。
            # 在此出口处归一化, 确保所有消费者拿到符合契约的数据
            # (predictor 的 _normalize_history_data 对已有 numbers 的行是"保留"逻辑, 完全兼容)。
            for row in rows:
                if not row:
                    continue
                nums = row.get('numbers')
                if isinstance(nums, list) and len(nums) == 5:
                    continue  # 已是正确格式, 跳过
                if all(k in row for k in ('wan', 'qian', 'bai', 'shi', 'ge')):
                    row['numbers'] = [
                        int(row['wan']) if row.get('wan') is not None else 0,
                        int(row['qian']) if row.get('qian') is not None else 0,
                        int(row['bai']) if row.get('bai') is not None else 0,
                        int(row['shi']) if row.get('shi') is not None else 0,
                        int(row['ge']) if row.get('ge') is not None else 0,
                    ]
            return rows
        except Exception as e:
            logger.error(f'获取历史数据失败: {e}')
            return []
    
    def get_history_count(self) -> int:
        """获取历史数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_history_data WHERE is_valid = 1')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取历史数据总数失败: {e}')
            return 0
    
    def get_history_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取历史数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_history_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取历史数据失败: {e}')
            return None
    
    # ============================================================
    # 走势数据操作
    # ============================================================
    
    def insert_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入走势数据（智能去重）
        
        Args:
            data: 走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_trend_data 
            (issue, wan, qian, bai, shi, ge, hezhi, odd_even_ratio, 
             big_small_ratio, prime_composite_ratio, trend_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue or issue in existing_issues:
                    skip_count += 1
                    continue
                
                trend = item.get('trend', {})
                numbers = item.get('numbers', [])
                
                wan = int(trend.get('wan', numbers[0] if numbers else 0))
                qian = int(trend.get('qian', numbers[1] if len(numbers) > 1 else 0))
                bai = int(trend.get('bai', numbers[2] if len(numbers) > 2 else 0))
                shi = int(trend.get('shi', numbers[3] if len(numbers) > 3 else 0))
                ge = int(trend.get('ge', numbers[4] if len(numbers) > 4 else 0))
                
                self.cursor.execute(sql, (
                    issue, wan, qian, bai, shi, ge,
                    item.get('hezhi', ''),
                    item.get('odd_even_ratio', ''),
                    item.get('big_small_ratio', ''),
                    item.get('prime_composite_ratio', ''),
                    json.dumps(item, ensure_ascii=False)
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入走势数据失败: {e}')
            return 0, len(data)
    
    def get_trend_data(self, limit: int = 120) -> List[Dict[str, Any]]:
        """获取走势数据"""
        try:
            sql = 'SELECT * FROM p5_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取走势数据失败: {e}')
            return []
    
    # ============================================================
    # AI分析报告操作
    # ============================================================
    
    def insert_ai_report(self, report_content, data_count, latest_issue, next_issue=None,
                         trend_analysis=None, probability_stats=None,
                         recommended_numbers=None, recommended_combinations=None,
                         confidence_scores=None, recommendation_reasons=None,
                         key_conclusions=None, risk_warning=None, report_format='TEXT',
                         report_type='final'):
        """插入排列5AI分析报告

        Args:
            report_type: 报告类型, 用于区分 'expert_article'(专家文章预测报告) /
                         'trend_chart'(走势图数据预测报告) / 'final'(最终预测, 默认)
        """
        try:
            report_uuid = str(uuid.uuid4())
            report_date = datetime.now().strftime('%Y-%m-%d')

            sql = '''
            INSERT INTO p5_ai_report (
                report_date, report_uuid, data_count, latest_issue, next_issue,
                trend_analysis, probability_stats, recommended_numbers,
                recommended_combinations, confidence_scores, recommendation_reasons,
                key_conclusions, risk_warning, report_content, report_format, report_type
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            '''

            params = (
                report_date, report_uuid, data_count, latest_issue, next_issue,
                trend_analysis, probability_stats, recommended_numbers,
                recommended_combinations, confidence_scores, recommendation_reasons,
                key_conclusions, risk_warning, report_content, report_format, report_type
            )

            try:
                self.execute_with_reconnect(sql, params)
                self.connection.commit()
            except Exception as e:
                err_code = getattr(e, 'args', (None, None))[0]
                # 1054 = Unknown column 'report_type' —— 老表尚未加该列, 运行时动态补齐
                if err_code == 1054:
                    self._ensure_report_type_column()
                    self.execute_with_reconnect(sql, params)
                    self.connection.commit()
                else:
                    raise

            logger.info(f'成功插入排列5AI分析报告(report_type={report_type}), UUID: {report_uuid}')
            return report_uuid
        except Exception as e:
            logger.error(f'插入排列5AI分析报告失败: {e}')
            return None

    def _ensure_report_type_column(self):
        """运行时安全补齐 p5_ai_report.report_type 列(兼容老表, 幂等)。

        早期版本的表没有 report_type 列, 而 create_tables 仅在初始化命令时调用,
        流水线运行时不会触发, 故这里在插入失败时自愈式补列。
        """
        try:
            self.execute_with_reconnect(
                "ALTER TABLE p5_ai_report "
                "ADD COLUMN report_type VARCHAR(20) NULL DEFAULT 'final' "
                "COMMENT '报告类型(expert_article/trend_chart/final)'"
            )
            logger.info('p5_ai_report.report_type 列已动态补齐')
        except Exception as e:
            err_code = getattr(e, 'args', (None, None))[0]
            if err_code == 1060:  # Duplicate column name, 已存在则忽略
                pass
            else:
                logger.warning(f'动态补齐 report_type 列跳过(非致命): {e}')
    
    def get_latest_ai_report(self):
        """获取最新的AI分析报告"""
        try:
            sql = 'SELECT * FROM p5_ai_report ORDER BY created_at DESC LIMIT 1'
            self.cursor.execute(sql)
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'获取最新AI分析报告失败: {e}')
            return None
    
    def get_all_ai_reports(self, limit=10):
        """获取AI分析报告列表"""
        try:
            sql = 'SELECT * FROM p5_ai_report ORDER BY created_at DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取AI分析报告列表失败: {e}')
            return []
    
    def get_report_count(self) -> int:
        """获取AI分析报告总数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_ai_report')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取报告数量失败: {e}')
            return 0
    
    # ============================================================
    # 预测验证记录操作
    # ============================================================
    
    def insert_prediction_record(self, report_uuid: str, target_issue: str,
                                  predicted_numbers: str, predicted_combinations: str,
                                  confidence_scores: str) -> bool:
        """插入预测记录"""
        try:
            sql = '''
            INSERT INTO p5_prediction_record 
            (report_uuid, target_issue, predicted_numbers, predicted_combinations, confidence_scores)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            predicted_numbers = VALUES(predicted_numbers),
            predicted_combinations = VALUES(predicted_combinations),
            confidence_scores = VALUES(confidence_scores)
            '''
            self.cursor.execute(sql, (report_uuid, target_issue, predicted_numbers, 
                                       predicted_combinations, confidence_scores))
            self.connection.commit()
            logger.info(f'预测记录插入成功: {target_issue}')
            return True
        except Exception as e:
            logger.error(f'插入预测记录失败: {e}')
            return False
    
    def update_prediction_verification(self, report_uuid: str, target_issue: str,
                                        actual_numbers: List[int], actual_issue: str) -> Dict[str, Any]:
        """
        更新预测验证结果
        
        Args:
            report_uuid: 报告UUID
            target_issue: 目标期号
            actual_numbers: 实际开奖号码 [wan, qian, bai, shi, ge]
            actual_issue: 实际开奖期号
        
        Returns:
            验证结果字典
        """
        try:
            # 获取预测记录
            self.cursor.execute(
                'SELECT * FROM p5_prediction_record WHERE report_uuid = %s AND target_issue = %s',
                (report_uuid, target_issue)
            )
            record = self.cursor.fetchone()
            
            if not record:
                logger.warning(f'未找到预测记录: {report_uuid}/{target_issue}')
                return {'status': 'error', 'message': '预测记录不存在'}
            
            # 解析预测号码
            predicted = json.loads(record['predicted_numbers'])
            
            # 启用容错匹配机制(v3.1优化)
            tolerance_enabled = True  # 允许号码偏差±1也算命中
            
            def check_match(actual_num, pred_nums):
                """
                检查匹配(支持容错机制)
                
                规则:
                - 严格匹配: actual_num in pred_nums
                - 容错匹配: abs(actual_num - pred_num) <= 1
                """
                if not isinstance(pred_nums, list):
                    return False
                
                # 先尝试严格匹配
                if int(actual_num) in pred_nums:
                    return True
                
                # 容错匹配:检查是否有号码在±1范围内
                if tolerance_enabled:
                    for pred_num in pred_nums:
                        if abs(int(actual_num) - int(pred_num)) <= 1:
                            return True
                
                return False
            
            # 比对结果(支持容错)
            wan_match = check_match(actual_numbers[0], predicted.get('wan', []))
            qian_match = check_match(actual_numbers[1], predicted.get('qian', []))
            bai_match = check_match(actual_numbers[2], predicted.get('bai', []))
            shi_match = check_match(actual_numbers[3], predicted.get('shi', []))
            ge_match = check_match(actual_numbers[4], predicted.get('ge', []))
            
            match_count = sum([wan_match, qian_match, bai_match, shi_match, ge_match])
            is_matched = 1 if match_count == 5 else 0
            accuracy_rate = round(match_count / 5 * 100, 2)
            
            match_details = {
                'wan': {'predicted': predicted.get('wan', []), 'actual': actual_numbers[0], 'matched': wan_match},
                'qian': {'predicted': predicted.get('qian', []), 'actual': actual_numbers[1], 'matched': qian_match},
                'bai': {'predicted': predicted.get('bai', []), 'actual': actual_numbers[2], 'matched': bai_match},
                'shi': {'predicted': predicted.get('shi', []), 'actual': actual_numbers[3], 'matched': shi_match},
                'ge': {'predicted': predicted.get('ge', []), 'actual': actual_numbers[4], 'matched': ge_match}
            }
            
            deviation = []
            positions = ['wan', 'qian', 'bai', 'shi', 'ge']
            for i, pos in enumerate(positions):
                if not match_details[pos]['matched']:
                    pred_nums = predicted.get(pos, [])
                    deviation.append(f"{pos}: 预测{pred_nums} vs 实际{actual_numbers[i]}")
            
            # 写入p5_verification_detail表(每位置一条记录)
            for i, pos in enumerate(positions):
                actual_num = actual_numbers[i]
                pred_nums = predicted.get(pos, [])
                matched = wan_match if pos == 'wan' else qian_match if pos == 'qian' else bai_match if pos == 'bai' else shi_match if pos == 'shi' else ge_match
                
                # 计算容错命中和偏差
                tolerance_hit = 0
                dev_val = None
                if not matched and tolerance_enabled and pred_nums:
                    min_diff = min(abs(actual_num - int(p)) for p in pred_nums)
                    if min_diff == 1:
                        tolerance_hit = 1
                        dev_val = actual_num - int(pred_nums[0])
                
                # 获取置信度(简化处理)
                confidence = 0.0
                
                self.cursor.execute('''
                    INSERT INTO p5_verification_detail 
                    (verification_id, issue, position, predicted_numbers, actual_number, 
                     is_hit, tolerance_hit, deviation, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    record['id'], target_issue, pos,
                    json.dumps(pred_nums, ensure_ascii=False), actual_num,
                    1 if matched else 0, tolerance_hit, dev_val, confidence
                ))
            
            self.connection.commit()
            logger.info(f'验证详情已写入p5_verification_detail表: 期号{target_issue}')
            
            sql = '''
            UPDATE p5_prediction_record SET
                actual_numbers = %s,
                actual_issue = %s,
                is_matched = %s,
                match_count = %s,
                match_details = %s,
                wan_match = %s,
                qian_match = %s,
                bai_match = %s,
                shi_match = %s,
                ge_match = %s,
                deviation_analysis = %s,
                accuracy_rate = %s,
                verification_status = 'verified',
                verified_at = NOW()
            WHERE report_uuid = %s AND target_issue = %s
            '''
            
            self.cursor.execute(sql, (
                json.dumps(actual_numbers),
                actual_issue,
                is_matched,
                match_count,
                json.dumps(match_details, ensure_ascii=False),
                wan_match, qian_match, bai_match, shi_match, ge_match,
                '; '.join(deviation) if deviation else '无偏差',
                accuracy_rate,
                report_uuid, target_issue
            ))
            self.connection.commit()
            
            logger.info(f'预测验证完成: {target_issue}, 命中{match_count}/5, 准确率{accuracy_rate}%')
            
            return {
                'status': 'success',
                'target_issue': target_issue,
                'match_count': match_count,
                'is_matched': is_matched,
                'accuracy_rate': accuracy_rate,
                'match_details': match_details
            }
        except Exception as e:
            logger.error(f'更新预测验证失败: {e}')
            return {'status': 'error', 'message': str(e)}
    
    def get_pending_predictions(self) -> List[Dict[str, Any]]:
        """获取待验证的预测记录"""
        try:
            sql = '''
            SELECT * FROM p5_prediction_record 
            WHERE verification_status = 'pending'
            ORDER BY target_issue DESC
            '''
            self.cursor.execute(sql)
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取待验证预测失败: {e}')
            return []
    
    def get_verification_stats(self) -> Dict[str, Any]:
        """获取验证统计信息"""
        try:
            sql = '''
            SELECT 
                COUNT(*) as total,
                SUM(is_matched) as total_matched,
                AVG(match_count) as avg_match,
                AVG(accuracy_rate) as avg_accuracy,
                SUM(wan_match) as wan_hits,
                SUM(qian_match) as qian_hits,
                SUM(bai_match) as bai_hits,
                SUM(shi_match) as shi_hits,
                SUM(ge_match) as ge_hits
            FROM p5_prediction_record 
            WHERE verification_status = 'verified'
            '''
            self.cursor.execute(sql)
            result = self.cursor.fetchone()
            
            total = result.get('total', 0) or 0
            if total == 0:
                return {'total': 0, 'message': '暂无验证数据'}
            
            return {
                'total': total,
                'total_matched': result.get('total_matched', 0) or 0,
                'avg_match': round(result.get('avg_match', 0) or 0, 2),
                'avg_accuracy': round(result.get('avg_accuracy', 0) or 0, 2),
                'wan_accuracy': round((result.get('wan_hits', 0) or 0) / total * 100, 2),
                'qian_accuracy': round((result.get('qian_hits', 0) or 0) / total * 100, 2),
                'bai_accuracy': round((result.get('bai_hits', 0) or 0) / total * 100, 2),
                'shi_accuracy': round((result.get('shi_hits', 0) or 0) / total * 100, 2),
                'ge_accuracy': round((result.get('ge_hits', 0) or 0) / total * 100, 2),
                'overall_accuracy': round((result.get('avg_accuracy', 0) or 0), 2)
            }
        except Exception as e:
            logger.error(f'获取验证统计失败: {e}')
            return {}
    
    # ============================================================
    # 性能统计操作
    # ============================================================
    
    def update_performance_stats(self) -> bool:
        """更新性能统计"""
        try:
            stats = self.get_verification_stats()
            if stats.get('total', 0) == 0:
                return False
            
            stat_date = datetime.now().strftime('%Y-%m-%d')
            
            sql = '''
            INSERT INTO p5_performance_stats 
            (stat_date, total_predictions, total_matched, avg_match_count,
             wan_accuracy, qian_accuracy, bai_accuracy, shi_accuracy, ge_accuracy,
             overall_accuracy, performance_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            total_predictions = VALUES(total_predictions),
            total_matched = VALUES(total_matched),
            avg_match_count = VALUES(avg_match_count),
            wan_accuracy = VALUES(wan_accuracy),
            qian_accuracy = VALUES(qian_accuracy),
            bai_accuracy = VALUES(bai_accuracy),
            shi_accuracy = VALUES(shi_accuracy),
            ge_accuracy = VALUES(ge_accuracy),
            overall_accuracy = VALUES(overall_accuracy),
            performance_json = VALUES(performance_json),
            updated_at = NOW()
            '''
            
            self.cursor.execute(sql, (
                stat_date,
                stats['total'],
                stats['total_matched'],
                stats['avg_match'],
                stats['wan_accuracy'],
                stats['qian_accuracy'],
                stats['bai_accuracy'],
                stats['shi_accuracy'],
                stats['ge_accuracy'],
                stats['overall_accuracy'],
                json.dumps(stats, ensure_ascii=False)
            ))
            self.connection.commit()
            logger.info('性能统计更新成功')
            return True
        except Exception as e:
            logger.error(f'更新性能统计失败: {e}')
            return False
    
    def get_performance_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        """获取性能历史记录"""
        try:
            sql = 'SELECT * FROM p5_performance_stats ORDER BY stat_date DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取性能历史失败: {e}')
            return []
    
    # ============================================================
    # 万位走势数据操作
    # ============================================================
    
    def insert_wan_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入万位走势数据（智能去重）
        
        Args:
            data: 万位走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_wan_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_wan_trend_data 
            (issue, wan_number, draw_date, is_odd, is_big, is_prime, 
             omission, hot_level, consecutive_count, trend_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                wan_number = VALUES(wan_number),
                draw_date = VALUES(draw_date),
                is_odd = VALUES(is_odd),
                is_big = VALUES(is_big),
                is_prime = VALUES(is_prime),
                omission = VALUES(omission),
                hot_level = VALUES(hot_level),
                consecutive_count = VALUES(consecutive_count),
                trend_json = VALUES(trend_json),
                source = VALUES(source)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                
                wan_number = item.get('wan_number', 0)
                if not (0 <= wan_number <= 9):
                    skip_count += 1
                    continue
                
                self.cursor.execute(sql, (
                    issue,
                    wan_number,
                    item.get('draw_date', ''),
                    item.get('is_odd', None),
                    item.get('is_big', None),
                    item.get('is_prime', None),
                    item.get('omission', 0),
                    item.get('hot_level', ''),
                    item.get('consecutive_count', 0),
                    json.dumps(item, ensure_ascii=False),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'万位走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入万位走势数据失败: {e}')
            return 0, len(data)
    
    def get_wan_trend_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取万位走势数据"""
        try:
            sql = 'SELECT * FROM p5_wan_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取万位走势数据失败: {e}')
            return []
    
    def get_wan_trend_count(self) -> int:
        """获取万位走势数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_wan_trend_data')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取万位走势数据总数失败: {e}')
            return 0
    
    def get_wan_trend_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取万位走势数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_wan_trend_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取万位走势数据失败: {e}')
            return None
    
    def get_wan_number_stats(self) -> Dict[str, Any]:
        """获取万位数字统计信息"""
        try:
            sql = '''
            SELECT 
                wan_number,
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM p5_wan_trend_data
            GROUP BY wan_number
            ORDER BY count DESC
            '''
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            stats = {}
            for row in results:
                stats[row['wan_number']] = {
                    'count': row['count'],
                    'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                    'min_omission': row['min_omission'],
                    'max_omission': row['max_omission']
                }
            
            return stats
        except Exception as e:
            logger.error(f'获取万位数字统计失败: {e}')
            return {}
    
    # ============================================================
    # 千位走势数据操作
    # ============================================================
    
    def insert_qian_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入千位走势数据（智能去重）
        
        Args:
            data: 千位走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_qian_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_qian_trend_data 
            (issue, qian_number, draw_date, is_odd, is_big, is_prime, 
             omission, hot_level, consecutive_count, trend_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                qian_number = VALUES(qian_number),
                draw_date = VALUES(draw_date),
                is_odd = VALUES(is_odd),
                is_big = VALUES(is_big),
                is_prime = VALUES(is_prime),
                omission = VALUES(omission),
                hot_level = VALUES(hot_level),
                consecutive_count = VALUES(consecutive_count),
                trend_json = VALUES(trend_json),
                source = VALUES(source)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                
                qian_number = item.get('qian_number', 0)
                if not (0 <= qian_number <= 9):
                    skip_count += 1
                    continue
                
                self.cursor.execute(sql, (
                    issue,
                    qian_number,
                    item.get('draw_date', ''),
                    item.get('is_odd', None),
                    item.get('is_big', None),
                    item.get('is_prime', None),
                    item.get('omission', 0),
                    item.get('hot_level', ''),
                    item.get('consecutive_count', 0),
                    json.dumps(item, ensure_ascii=False),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'千位走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入千位走势数据失败: {e}')
            return 0, len(data)
    
    def get_qian_trend_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取千位走势数据"""
        try:
            sql = 'SELECT * FROM p5_qian_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取千位走势数据失败: {e}')
            return []
    
    def get_qian_trend_count(self) -> int:
        """获取千位走势数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_qian_trend_data')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取千位走势数据总数失败: {e}')
            return 0
    
    def get_qian_trend_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取千位走势数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_qian_trend_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取千位走势数据失败: {e}')
            return None
    
    def get_qian_number_stats(self) -> Dict[str, Any]:
        """获取千位数字统计信息"""
        try:
            sql = '''
            SELECT 
                qian_number,
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM p5_qian_trend_data
            GROUP BY qian_number
            ORDER BY count DESC
            '''
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            stats = {}
            for row in results:
                stats[row['qian_number']] = {
                    'count': row['count'],
                    'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                    'min_omission': row['min_omission'],
                    'max_omission': row['max_omission']
                }
            
            return stats
        except Exception as e:
            logger.error(f'获取千位数字统计失败: {e}')
            return {}
    
    # ============================================================
    # 百位走势数据操作
    # ============================================================
    
    def insert_bai_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入百位走势数据（智能去重）
        
        Args:
            data: 百位走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_bai_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_bai_trend_data 
            (issue, bai_number, draw_date, is_odd, is_big, is_prime, 
             omission, hot_level, consecutive_count, trend_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                bai_number = VALUES(bai_number),
                draw_date = VALUES(draw_date),
                is_odd = VALUES(is_odd),
                is_big = VALUES(is_big),
                is_prime = VALUES(is_prime),
                omission = VALUES(omission),
                hot_level = VALUES(hot_level),
                consecutive_count = VALUES(consecutive_count),
                trend_json = VALUES(trend_json),
                source = VALUES(source)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                
                bai_number = item.get('bai_number', 0)
                if not (0 <= bai_number <= 9):
                    skip_count += 1
                    continue
                
                self.cursor.execute(sql, (
                    issue,
                    bai_number,
                    item.get('draw_date', ''),
                    item.get('is_odd', None),
                    item.get('is_big', None),
                    item.get('is_prime', None),
                    item.get('omission', 0),
                    item.get('hot_level', ''),
                    item.get('consecutive_count', 0),
                    json.dumps(item, ensure_ascii=False),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'百位走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入百位走势数据失败: {e}')
            return 0, len(data)
    
    def get_bai_trend_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取百位走势数据"""
        try:
            sql = 'SELECT * FROM p5_bai_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取百位走势数据失败: {e}')
            return []
    
    def get_bai_trend_count(self) -> int:
        """获取百位走势数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_bai_trend_data')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取百位走势数据总数失败: {e}')
            return 0
    
    def get_bai_trend_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取百位走势数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_bai_trend_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取百位走势数据失败: {e}')
            return None
    
    def get_bai_number_stats(self) -> Dict[str, Any]:
        """获取百位数字统计信息"""
        try:
            sql = '''
            SELECT 
                bai_number,
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM p5_bai_trend_data
            GROUP BY bai_number
            ORDER BY count DESC
            '''
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            stats = {}
            for row in results:
                stats[row['bai_number']] = {
                    'count': row['count'],
                    'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                    'min_omission': row['min_omission'],
                    'max_omission': row['max_omission']
                }
            
            return stats
        except Exception as e:
            logger.error(f'获取百位数字统计失败: {e}')
            return {}
    
    # ============================================================
    # 十位走势数据操作
    # ============================================================
    
    def insert_shi_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入十位走势数据（智能去重）
        
        Args:
            data: 十位走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_shi_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_shi_trend_data 
            (issue, shi_number, draw_date, is_odd, is_big, is_prime, 
             omission, hot_level, consecutive_count, trend_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                shi_number = VALUES(shi_number),
                draw_date = VALUES(draw_date),
                is_odd = VALUES(is_odd),
                is_big = VALUES(is_big),
                is_prime = VALUES(is_prime),
                omission = VALUES(omission),
                hot_level = VALUES(hot_level),
                consecutive_count = VALUES(consecutive_count),
                trend_json = VALUES(trend_json),
                source = VALUES(source)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                
                shi_number = item.get('shi_number', 0)
                if not (0 <= shi_number <= 9):
                    skip_count += 1
                    continue
                
                self.cursor.execute(sql, (
                    issue,
                    shi_number,
                    item.get('draw_date', ''),
                    item.get('is_odd', None),
                    item.get('is_big', None),
                    item.get('is_prime', None),
                    item.get('omission', 0),
                    item.get('hot_level', ''),
                    item.get('consecutive_count', 0),
                    json.dumps(item, ensure_ascii=False),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'十位走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入十位走势数据失败: {e}')
            return 0, len(data)
    
    def get_shi_trend_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取十位走势数据"""
        try:
            sql = 'SELECT * FROM p5_shi_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取十位走势数据失败: {e}')
            return []
    
    def get_shi_trend_count(self) -> int:
        """获取十位走势数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_shi_trend_data')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取十位走势数据总数失败: {e}')
            return 0
    
    def get_shi_trend_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取十位走势数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_shi_trend_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取十位走势数据失败: {e}')
            return None
    
    def get_shi_number_stats(self) -> Dict[str, Any]:
        """获取十位数字统计信息"""
        try:
            sql = '''
            SELECT 
                shi_number,
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM p5_shi_trend_data
            GROUP BY shi_number
            ORDER BY count DESC
            '''
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            stats = {}
            for row in results:
                stats[row['shi_number']] = {
                    'count': row['count'],
                    'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                    'min_omission': row['min_omission'],
                    'max_omission': row['max_omission']
                }
            
            return stats
        except Exception as e:
            logger.error(f'获取十位数字统计失败: {e}')
            return {}
    
    # ============================================================
    # 个位走势数据操作
    # ============================================================
    
    def insert_ge_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入个位走势数据（智能去重）
        
        Args:
            data: 个位走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_ge_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_ge_trend_data 
            (issue, ge_number, draw_date, is_odd, is_big, is_prime, 
             omission, hot_level, consecutive_count, trend_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                ge_number = VALUES(ge_number),
                draw_date = VALUES(draw_date),
                is_odd = VALUES(is_odd),
                is_big = VALUES(is_big),
                is_prime = VALUES(is_prime),
                omission = VALUES(omission),
                hot_level = VALUES(hot_level),
                consecutive_count = VALUES(consecutive_count),
                trend_json = VALUES(trend_json),
                source = VALUES(source)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                
                ge_num = item.get('ge_number', 0)
                if not (0 <= ge_num <= 9):
                    skip_count += 1
                    continue
                
                self.cursor.execute(sql, (
                    issue,
                    ge_num,
                    item.get('draw_date', ''),
                    item.get('is_odd', None),
                    item.get('is_big', None),
                    item.get('is_prime', None),
                    item.get('omission', 0),
                    item.get('hot_level', ''),
                    item.get('consecutive_count', 0),
                    json.dumps(item, ensure_ascii=False),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'个位走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入个位走势数据失败: {e}')
            return 0, len(data)
    
    def get_ge_trend_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取个位走势数据"""
        try:
            sql = 'SELECT * FROM p5_ge_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取个位走势数据失败: {e}')
            return []
    
    def get_ge_trend_count(self) -> int:
        """获取个位走势数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_ge_trend_data')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取个位走势数据总数失败: {e}')
            return 0
    
    def get_ge_trend_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取个位走势数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_ge_trend_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取个位走势数据失败: {e}')
            return None

    # ============================================================
    # 升平降走势 / 和值走势（一定牛 spjzs / hzzst 图表）
    # ============================================================

    def insert_spjzs_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入升平降走势数据（智能去重, 幂等）。

        Args:
            data: 已归一化的升平降走势数据列表(每条含 issue/numbers/wan../hezhi/.../miss_json)
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        success_count = 0
        skip_count = 0
        try:
            self.cursor.execute('SELECT issue FROM p5_spjzs_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            sql = '''
            INSERT INTO p5_spjzs_data
            (issue, numbers, wan, qian, bai, shi, ge, hezhi, hewei, kuadu, avg, miss_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                numbers = VALUES(numbers), wan = VALUES(wan), qian = VALUES(qian),
                bai = VALUES(bai), shi = VALUES(shi), ge = VALUES(ge),
                hezhi = VALUES(hezhi), hewei = VALUES(hewei), kuadu = VALUES(kuadu),
                avg = VALUES(avg), miss_json = VALUES(miss_json), source = VALUES(source)
            '''
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                try:
                    wan = int(item.get('wan'))
                    if not (0 <= wan <= 9):
                        skip_count += 1
                        continue
                except (TypeError, ValueError):
                    skip_count += 1
                    continue
                self.cursor.execute(sql, (
                    issue,
                    item.get('numbers', ''),
                    item.get('wan'), item.get('qian'), item.get('bai'),
                    item.get('shi'), item.get('ge'),
                    item.get('hezhi'), item.get('hewei'), item.get('kuadu'),
                    item.get('avg'), item.get('miss_json'),
                    item.get('source', 'ydniu_spjzs'),
                ))
                success_count += 1
            self.connection.commit()
            logger.info(f'升平降走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入升平降走势数据失败: {e}')
            return 0, len(data)

    def insert_hzzst_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入和值走势数据（智能去重, 幂等）。

        Args:
            data: 已归一化的和值走势数据列表
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        success_count = 0
        skip_count = 0
        try:
            self.cursor.execute('SELECT issue FROM p5_hzzst_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            sql = '''
            INSERT INTO p5_hzzst_data
            (issue, numbers, wan, qian, bai, shi, ge, hezhi, kuadu, hewei, miss_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                numbers = VALUES(numbers), wan = VALUES(wan), qian = VALUES(qian),
                bai = VALUES(bai), shi = VALUES(shi), ge = VALUES(ge),
                hezhi = VALUES(hezhi), kuadu = VALUES(kuadu), hewei = VALUES(hewei),
                miss_json = VALUES(miss_json), source = VALUES(source)
            '''
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                try:
                    wan = int(item.get('wan'))
                    if not (0 <= wan <= 9):
                        skip_count += 1
                        continue
                except (TypeError, ValueError):
                    skip_count += 1
                    continue
                self.cursor.execute(sql, (
                    issue,
                    item.get('numbers', ''),
                    item.get('wan'), item.get('qian'), item.get('bai'),
                    item.get('shi'), item.get('ge'),
                    item.get('hezhi'), item.get('kuadu'), item.get('hewei'),
                    item.get('miss_json'),
                    item.get('source', 'ydniu_hzzst'),
                ))
                success_count += 1
            self.connection.commit()
            logger.info(f'和值走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入和值走势数据失败: {e}')
            return 0, len(data)

    def get_latest_spjzs_issue(self) -> Optional[str]:
        """获取升平降走势表最新期号"""
        try:
            self.cursor.execute('SELECT issue FROM p5_spjzs_data ORDER BY issue DESC LIMIT 1')
            result = self.cursor.fetchone()
            return result['issue'] if result else None
        except Exception as e:
            logger.error(f'获取升平降走势最新期号失败: {e}')
            return None

    def get_latest_hzzst_issue(self) -> Optional[str]:
        """获取和值走势表最新期号"""
        try:
            self.cursor.execute('SELECT issue FROM p5_hzzst_data ORDER BY issue DESC LIMIT 1')
            result = self.cursor.fetchone()
            return result['issue'] if result else None
        except Exception as e:
            logger.error(f'获取和值走势最新期号失败: {e}')
            return None

    def get_spjzs_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取升平降走势数据(期号倒序)"""
        try:
            self.cursor.execute('SELECT * FROM p5_spjzs_data ORDER BY issue DESC LIMIT %s', (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取升平降走势数据失败: {e}')
            return []

    def get_hzzst_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取和值走势数据(期号倒序)"""
        try:
            self.cursor.execute('SELECT * FROM p5_hzzst_data ORDER BY issue DESC LIMIT %s', (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取和值走势数据失败: {e}')
            return []

    def get_ge_number_stats(self) -> Dict[str, Any]:
        """获取个位数字统计信息"""
        try:
            sql = '''
            SELECT 
                ge_number,
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM p5_ge_trend_data
            GROUP BY ge_number
            ORDER BY count DESC
            '''
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            stats = {}
            for row in results:
                stats[row['ge_number']] = {
                    'count': row['count'],
                    'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                    'min_omission': row['min_omission'],
                    'max_omission': row['max_omission']
                }
            
            return stats
        except Exception as e:
            logger.error(f'获取个位数字统计失败: {e}')
            return {}
    
    # ============================================================
    # 和尾走势数据操作
    # ============================================================
    
    def insert_sum_end_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入和尾走势数据（智能去重）
        
        Args:
            data: 和尾走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_sum_end_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_sum_end_trend_data 
            (issue, sum_end, sum_value, draw_date, is_odd, is_big, 
             omission, hot_level, consecutive_count, trend_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                sum_end = VALUES(sum_end),
                sum_value = VALUES(sum_value),
                draw_date = VALUES(draw_date),
                is_odd = VALUES(is_odd),
                is_big = VALUES(is_big),
                omission = VALUES(omission),
                hot_level = VALUES(hot_level),
                consecutive_count = VALUES(consecutive_count),
                trend_json = VALUES(trend_json),
                source = VALUES(source)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                
                sum_end = item.get('sum_end', 0)
                if not (0 <= sum_end <= 9):
                    skip_count += 1
                    continue
                
                self.cursor.execute(sql, (
                    issue,
                    sum_end,
                    item.get('sum_value', None),
                    item.get('draw_date', ''),
                    item.get('is_odd', None),
                    item.get('is_big', None),
                    item.get('omission', 0),
                    item.get('hot_level', ''),
                    item.get('consecutive_count', 0),
                    json.dumps(item, ensure_ascii=False),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'和尾走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入和尾走势数据失败: {e}')
            return 0, len(data)
    
    def get_sum_end_trend_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取和尾走势数据"""
        try:
            sql = 'SELECT * FROM p5_sum_end_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取和尾走势数据失败: {e}')
            return []
    
    def get_sum_end_trend_count(self) -> int:
        """获取和尾走势数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_sum_end_trend_data')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取和尾走势数据总数失败: {e}')
            return 0
    
    def get_sum_end_trend_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取和尾走势数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_sum_end_trend_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取和尾走势数据失败: {e}')
            return None
    
    def get_sum_end_stats(self) -> Dict[str, Any]:
        """获取和尾统计信息"""
        try:
            sql = '''
            SELECT 
                sum_end,
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM p5_sum_end_trend_data
            GROUP BY sum_end
            ORDER BY count DESC
            '''
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            stats = {}
            for row in results:
                stats[row['sum_end']] = {
                    'count': row['count'],
                    'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                    'min_omission': row['min_omission'],
                    'max_omission': row['max_omission']
                }
            
            return stats
        except Exception as e:
            logger.error(f'获取和尾统计失败: {e}')
            return {}
    
    # ============================================================
    # 后三走势数据操作
    # ============================================================
    
    def insert_back_three_trend_data(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入后三走势数据（智能去重）
        
        Args:
            data: 后三走势数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT issue FROM p5_back_three_trend_data')
            existing_issues = {row['issue'] for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_back_three_trend_data 
            (issue, bai_number, shi_number, ge_number, back_three_value, 
             sum_value, sum_end, draw_date, is_odd, is_big, 
             omission, hot_level, consecutive_count, trend_json, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                bai_number = VALUES(bai_number),
                shi_number = VALUES(shi_number),
                ge_number = VALUES(ge_number),
                back_three_value = VALUES(back_three_value),
                sum_value = VALUES(sum_value),
                sum_end = VALUES(sum_end),
                draw_date = VALUES(draw_date),
                is_odd = VALUES(is_odd),
                is_big = VALUES(is_big),
                omission = VALUES(omission),
                hot_level = VALUES(hot_level),
                consecutive_count = VALUES(consecutive_count),
                trend_json = VALUES(trend_json),
                source = VALUES(source)
            '''
            
            for item in data:
                issue = str(item.get('issue', ''))
                if not issue:
                    skip_count += 1
                    continue
                
                bai_num = item.get('bai_number', 0)
                shi_num = item.get('shi_number', 0)
                ge_num = item.get('ge_number', 0)
                
                if not (0 <= bai_num <= 9 and 0 <= shi_num <= 9 and 0 <= ge_num <= 9):
                    skip_count += 1
                    continue
                
                self.cursor.execute(sql, (
                    issue,
                    bai_num,
                    shi_num,
                    ge_num,
                    item.get('back_three_value', ''),
                    item.get('sum_value', None),
                    item.get('sum_end', None),
                    item.get('draw_date', ''),
                    item.get('is_odd', None),
                    item.get('is_big', None),
                    item.get('omission', 0),
                    item.get('hot_level', ''),
                    item.get('consecutive_count', 0),
                    json.dumps(item, ensure_ascii=False),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'后三走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入后三走势数据失败: {e}')
            return 0, len(data)
    
    def get_back_three_trend_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取后三走势数据"""
        try:
            sql = 'SELECT * FROM p5_back_three_trend_data ORDER BY issue DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f'获取后三走势数据失败: {e}')
            return []
    
    def get_back_three_trend_count(self) -> int:
        """获取后三走势数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_back_three_trend_data')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取后三走势数据总数失败: {e}')
            return 0
    
    def get_back_three_trend_by_issue(self, issue: str) -> Optional[Dict[str, Any]]:
        """根据期号获取后三走势数据"""
        try:
            self.cursor.execute('SELECT * FROM p5_back_three_trend_data WHERE issue = %s', (issue,))
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f'根据期号获取后三走势数据失败: {e}')
            return None
    
    def get_back_three_sum_end_stats(self) -> Dict[str, Any]:
        """获取后三和尾统计信息"""
        try:
            sql = '''
            SELECT 
                sum_end,
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM p5_back_three_trend_data
            GROUP BY sum_end
            ORDER BY count DESC
            '''
            self.cursor.execute(sql)
            results = self.cursor.fetchall()
            
            stats = {}
            for row in results:
                stats[row['sum_end']] = {
                    'count': row['count'],
                    'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                    'min_omission': row['min_omission'],
                    'max_omission': row['max_omission']
                }
            
            return stats
        except Exception as e:
            logger.error(f'获取后三和尾统计失败: {e}')
            return {}
    
    # ============================================================
    # 专家推荐数据操作
    # ============================================================
    
    def insert_expert_recommendation(self, data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量插入专家推荐数据（智能去重）
        
        Args:
            data: 专家推荐数据列表
        
        Returns:
            (成功条数, 跳过条数)
        """
        if not data:
            return 0, 0
        
        success_count = 0
        skip_count = 0
        
        try:
            self.cursor.execute('SELECT user_id, issue_name FROM p5_expert_recommendation')
            existing = {(row['user_id'], row['issue_name']) for row in self.cursor.fetchall()}
            
            sql = '''
            INSERT INTO p5_expert_recommendation 
            (user_id, nick_name, head_url, issue_name, issue_no, issue_end_time, 
             issue_open_time, summary, intro, create_time, scheme_count, schemes_json,
             hit_ratio, hit_count, serial_hit_count, detail_url, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                nick_name = VALUES(nick_name),
                head_url = VALUES(head_url),
                issue_no = VALUES(issue_no),
                issue_end_time = VALUES(issue_end_time),
                issue_open_time = VALUES(issue_open_time),
                summary = VALUES(summary),
                intro = VALUES(intro),
                create_time = VALUES(create_time),
                scheme_count = VALUES(scheme_count),
                schemes_json = VALUES(schemes_json),
                hit_ratio = VALUES(hit_ratio),
                hit_count = VALUES(hit_count),
                serial_hit_count = VALUES(serial_hit_count),
                detail_url = VALUES(detail_url),
                updated_at = NOW()
            '''
            
            for item in data:
                user_id = item.get('user_id', 0)
                issue_name = item.get('issue_name', '')
                
                if not user_id or not issue_name:
                    skip_count += 1
                    continue
                
                if (user_id, issue_name) in existing:
                    skip_count += 1
                    continue
                
                schemes = item.get('schemes', [])
                schemes_json = json.dumps(schemes, ensure_ascii=False)
                scheme_count = len(schemes)
                
                expert_info = item.get('expert_info', {})
                
                self.cursor.execute(sql, (
                    user_id,
                    item.get('nick_name', ''),
                    expert_info.get('head_url', '') or item.get('head_url', ''),
                    issue_name,
                    item.get('issue_no', ''),
                    item.get('issue_end_time', ''),
                    item.get('issue_open_time', ''),
                    item.get('summary', ''),
                    item.get('intro', ''),
                    item.get('create_time', ''),
                    scheme_count,
                    schemes_json,
                    expert_info.get('hit_ratio', '') or item.get('hit_ratio', ''),
                    expert_info.get('hit_count', 0) or item.get('hit_count', 0),
                    expert_info.get('serial_hit_count', 0) or item.get('serial_hit_count', 0),
                    expert_info.get('detail_url', '') or item.get('detail_url', ''),
                    item.get('source', 'china_lottery')
                ))
                success_count += 1
            
            self.connection.commit()
            logger.info(f'专家推荐数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
            return success_count, skip_count
        except Exception as e:
            logger.error(f'插入专家推荐数据失败: {e}')
            return 0, len(data)
    
    def _parse_schemes_json(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """解析记录中的schemes_json字段"""
        for record in records:
            schemes_json = record.get('schemes_json', '')
            if schemes_json:
                try:
                    record['schemes'] = json.loads(schemes_json)
                except:
                    record['schemes'] = []
            else:
                record['schemes'] = []
        return records
    
    def get_expert_recommendation_by_user(self, user_id: int) -> List[Dict[str, Any]]:
        """根据专家ID获取推荐记录"""
        try:
            sql = 'SELECT * FROM p5_expert_recommendation WHERE user_id = %s ORDER BY issue_name DESC'
            self.cursor.execute(sql, (user_id,))
            records = self.cursor.fetchall()
            return self._parse_schemes_json(records)
        except Exception as e:
            logger.error(f'获取专家推荐记录失败: {e}')
            return []
    
    def get_expert_recommendation_by_issue(self, issue_name: str) -> List[Dict[str, Any]]:
        """根据期号获取专家推荐记录"""
        try:
            sql = 'SELECT * FROM p5_expert_recommendation WHERE issue_name = %s ORDER BY user_id'
            self.cursor.execute(sql, (issue_name,))
            records = self.cursor.fetchall()
            return self._parse_schemes_json(records)
        except Exception as e:
            logger.error(f'根据期号获取专家推荐记录失败: {e}')
            return []
    
    def get_latest_expert_recommendations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最新专家推荐记录"""
        try:
            sql = 'SELECT * FROM p5_expert_recommendation ORDER BY created_at DESC LIMIT %s'
            self.cursor.execute(sql, (limit,))
            records = self.cursor.fetchall()
            return self._parse_schemes_json(records)
        except Exception as e:
            logger.error(f'获取最新专家推荐记录失败: {e}')
            return []
    
    def get_expert_recommendation_count(self) -> int:
        """获取专家推荐数据总条数"""
        try:
            self.cursor.execute('SELECT COUNT(*) as count FROM p5_expert_recommendation')
            result = self.cursor.fetchone()
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f'获取专家推荐数据总数失败: {e}')
            return 0
    
    # ============================================================
    # 在线学习引擎支持方法
    # ============================================================
    
    def get_prediction_with_details(self, report_uuid: str, target_issue: str) -> Optional[Dict[str, Any]]:
        """
        获取预测记录的详细信息（供在线学习引擎使用）
        
        Returns:
            预测记录字典，包含预测号码、组合等详细信息
        """
        try:
            sql = '''
            SELECT * FROM p5_prediction_record 
            WHERE report_uuid = %s AND target_issue = %s
            '''
            self.cursor.execute(sql, (report_uuid, target_issue))
            record = self.cursor.fetchone()
            
            if record:
                # 解析JSON字段
                record['predicted_numbers'] = json.loads(record['predicted_numbers']) if record.get('predicted_numbers') else {}
                record['predicted_combinations'] = json.loads(record['predicted_combinations']) if record.get('predicted_combinations') else []
                record['confidence_scores'] = json.loads(record['confidence_scores']) if record.get('confidence_scores') else {}
            
            return record
            
        except Exception as e:
            logger.error(f'获取预测详情失败: {e}')
            return None
    
    def get_verified_predictions(self, days: int = 30, limit: int = 50) -> List[Dict[str, Any]]:
        """
        获取已验证的预测记录（供学习报告生成使用）
        
        Args:
            days: 查询最近N天的数据
            limit: 返回记录数限制
            
        Returns:
            预测记录列表
        """
        try:
            sql = '''
            SELECT * FROM p5_prediction_record 
            WHERE verification_status = 'verified'
            AND verified_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY verified_at DESC
            LIMIT %s
            '''
            self.cursor.execute(sql, (days, limit))
            return self.cursor.fetchall()
            
        except Exception as e:
            logger.error(f'获取已验证预测失败: {e}')
            return []
    
    def update_prediction_verification_batch(self, verification_results: List[Dict[str, Any]]) -> int:
        """
        批量更新预测验证结果（供在线学习引擎增量学习使用）
        
        Args:
            verification_results: 验证结果列表，每项包含：
                - report_uuid: 报告UUID
                - target_issue: 目标期号
                - actual_numbers: 实际号码列表
                - match_count: 命中位数
                
        Returns:
            成功更新的记录数
        """
        try:
            updated_count = 0
            for result in verification_results:
                report_uuid = result.get('report_uuid')
                target_issue = result.get('target_issue')
                actual_numbers = result.get('actual_numbers', [])
                match_count = result.get('match_count', 0)
                
                if report_uuid and target_issue and actual_numbers:
                    is_matched = 1 if match_count == 5 else 0
                    accuracy_rate = round(match_count / 5 * 100, 2)
                    
                    # 更新验证状态
                    sql = '''
                    UPDATE p5_prediction_record 
                    SET 
                        actual_numbers = %s,
                        actual_issue = %s,
                        is_matched = %s,
                        match_count = %s,
                        accuracy_rate = %s,
                        verification_status = 'verified',
                        verified_at = NOW()
                    WHERE report_uuid = %s AND target_issue = %s
                    '''
                    
                    self.cursor.execute(sql, (
                        json.dumps(actual_numbers, ensure_ascii=False),
                        target_issue,
                        is_matched,
                        match_count,
                        accuracy_rate,
                        report_uuid,
                        target_issue
                    ))
                    
                    if self.cursor.rowcount > 0:
                        updated_count += 1
            
            self.connection.commit()
            logger.info(f'批量更新验证结果成功: {updated_count}条')
            return updated_count
            
        except Exception as e:
            self.connection.rollback()
            logger.error(f'批量更新验证结果失败: {e}')
            return 0
    
    # ============================================================
    # v3.0 自适应权重持久化
    # ============================================================
    
    def insert_weight_history(self, algo_name, position, weight_value, weight_type='prior',
                              number_value=None, prior_prob=None, likelihood=None,
                              posterior_prob=None, evidence_count=1, validation_result=None,
                              match_count=0):
        """插入单条权重历史记录"""
        try:
            sql = '''
            INSERT INTO p5_weight_history 
            (algo_name, position, weight_type, number_value, weight_value, 
             prior_probability, likelihood, posterior_probability, 
             evidence_count, validation_result, match_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''
            self.cursor.execute(sql, (
                algo_name, position, weight_type, number_value, weight_value,
                prior_prob, likelihood, posterior_prob,
                evidence_count, validation_result, match_count
            ))
            self.connection.commit()
            logger.info(f'权重历史记录插入成功: algo={algo_name}, position={position}, weight={weight_value}')
            return self.cursor.lastrowid
        except Exception as e:
            self.connection.rollback()
            logger.error(f'插入权重历史记录失败: {e}')
            return None
    
    def get_weight_history(self, algo_name=None, position=None, limit=100, days=30):
        """获取权重历史记录"""
        try:
            sql = 'SELECT * FROM p5_weight_history WHERE 1=1'
            params = []
            
            if algo_name:
                sql += ' AND algo_name = %s'
                params.append(algo_name)
            
            if position:
                sql += ' AND position = %s'
                params.append(position)
            
            if days:
                sql += ' AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)'
                params.append(days)
            
            sql += ' ORDER BY created_at DESC LIMIT %s'
            params.append(limit)
            
            self.cursor.execute(sql, params)
            results = self.cursor.fetchall()
            logger.info(f'查询权重历史记录: {len(results)}条')
            return results
        except Exception as e:
            logger.error(f'查询权重历史记录失败: {e}')
            return []
    
    def get_algorithm_performance(self, days=30):
        """获取各算法性能统计"""
        try:
            sql = '''
            SELECT algo_name, validation_result, match_count, COUNT(*) as total,
                   AVG(match_count) as avg_match 
            FROM p5_weight_history 
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) 
            GROUP BY algo_name, validation_result
            '''
            self.cursor.execute(sql, (days,))
            results = self.cursor.fetchall()
            logger.info(f'算法性能统计查询成功: {len(results)}条')
            return results
        except Exception as e:
            logger.error(f'获取算法性能统计失败: {e}')
            return []
    
    def save_adaptive_weights(self, weights_json, version='v3.0'):
        """保存当前自适应权重配置到数据库 p5_artifact(type='adaptive_weights') (v3.3 起替代本地文件)"""
        try:
            config = {
                'version': version,
                'updated_at': datetime.now().isoformat(),
                'weights': weights_json if isinstance(weights_json, dict) else json.loads(weights_json)
            }
            ok = self.save_artifact('adaptive_weights', config, meta={'version': version})
            if ok:
                logger.info(f'自适应权重配置已保存(数据库): version={version}')
            return ok
        except Exception as e:
            logger.error(f'保存自适应权重配置失败: {e}')
            return False
    
    def load_adaptive_weights(self, version='v3.0'):
        """加载自适应权重配置 (v3.3: 优先从数据库 p5_artifact 读取, 不再依赖本地文件)"""
        try:
            artifact = self.get_latest_artifact('adaptive_weights')
            if not artifact:
                logger.warning('数据库无常权重配置记录')
                return None
            data = artifact.get('data') or {}
            if data.get('version') != version:
                logger.warning(f'权重配置版本不匹配: 期望={version}, 实际={data.get("version")}')
                return None
            logger.info(f'自适应权重配置加载成功: version={version}')
            return data.get('weights', {})
        except Exception as e:
            logger.error(f'加载自适应权重配置失败: {e}')
            return None

    # ============================================================
    # 运行时产物统一存储 (v3.3 新增, 替代所有运行时 JSON 文件)
    # ============================================================

    def save_artifact(self, artifact_type: str, data: Any, issue: str = None,
                      ref_uuid: str = None, meta: Dict[str, Any] = None) -> bool:
        """
        统一持久化运行时产物到 p5_artifact 表(替代原先写入磁盘的 JSON 文件)。

        Args:
            artifact_type: 数据类型, 如 'weight_history'/'adaptive_weights'/'prediction'/
                           'feature_analysis'/'backtest_report'/'bayesian_result'/
                           'learning_report'/'verification_report'
            data: 完整内容(dict/list/可序列化对象)
            issue: 关联期号(可选)
            ref_uuid: 关联UUID(可选, 如 report_uuid)
            meta: 元信息(dict, 如来源/算法版本/数据量)
        Returns:
            是否保存成功
        """
        try:
            data_json = json.dumps(data, ensure_ascii=False, default=str)
            meta_json = json.dumps(meta, ensure_ascii=False, default=str) if meta is not None else None
            sql = (
                'INSERT INTO p5_artifact (artifact_type, issue, ref_uuid, data_json, meta_json) '
                'VALUES (%s, %s, %s, %s, %s)'
            )
            params = (artifact_type, issue, ref_uuid, data_json, meta_json)
            try:
                self.execute_with_reconnect(sql, params)
                self.connection.commit()
            except Exception as e:
                err_code = getattr(e, 'args', (None, None))[0]
                if err_code == 1146:  # Table doesn't exist
                    self._ensure_artifact_table()
                    self.execute_with_reconnect(sql, params)
                    self.connection.commit()
                else:
                    raise
            return True
        except Exception as e:
            logger.error(f'保存产物失败(type={artifact_type}): {e}')
            return False

    def _ensure_artifact_table(self):
        """运行时安全补齐 p5_artifact 表(幂等, 兼容未执行 create_tables 的旧库)。"""
        try:
            self.execute_with_reconnect('''
            CREATE TABLE IF NOT EXISTS p5_artifact (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                artifact_type VARCHAR(40) NOT NULL,
                issue VARCHAR(20) NULL DEFAULT NULL,
                ref_uuid VARCHAR(36) NULL DEFAULT NULL,
                data_json MEDIUMTEXT NOT NULL,
                meta_json MEDIUMTEXT NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id) USING BTREE,
                INDEX idx_type (artifact_type ASC) USING BTREE,
                INDEX idx_issue (issue ASC) USING BTREE,
                INDEX idx_ref (ref_uuid ASC) USING BTREE,
                INDEX idx_created (created_at DESC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5运行时产物统一存储表'
            ''')
        except Exception as e:
            logger.warning(f'补齐 p5_artifact 表失败(非致命): {e}')

    def get_artifacts(self, artifact_type: str = None, issue: str = None,
                      ref_uuid: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        查询运行时产物(按生成时间倒序)。

        Returns: [{id, artifact_type, issue, ref_uuid, created_at, data, meta}, ...]
        """
        try:
            conds = []
            params = []
            if artifact_type:
                conds.append('artifact_type = %s')
                params.append(artifact_type)
            if issue:
                conds.append('issue = %s')
                params.append(issue)
            if ref_uuid:
                conds.append('ref_uuid = %s')
                params.append(ref_uuid)
            where = (' WHERE ' + ' AND '.join(conds)) if conds else ''
            sql = (
                'SELECT id, artifact_type, issue, ref_uuid, data_json, meta_json, created_at '
                f'FROM p5_artifact{where} ORDER BY created_at DESC LIMIT %s'
            )
            params.append(limit)
            self.execute_with_reconnect(sql, params)
            rows = self.cursor.fetchall()
            result = []
            for r in rows:
                result.append({
                    'id': r.get('id'),
                    'artifact_type': r.get('artifact_type'),
                    'issue': r.get('issue'),
                    'ref_uuid': r.get('ref_uuid'),
                    'created_at': r.get('created_at'),
                    'data': _safe_json_loads(r.get('data_json')),
                    'meta': _safe_json_loads(r.get('meta_json')),
                })
            return result
        except Exception as e:
            err_code = getattr(e, 'args', (None, None))[0]
            if err_code == 1146:  # Table doesn't exist — 兜底自动建表
                logger.warning('p5_artifact 表不存在, 自动补齐...')
                try:
                    self._ensure_artifact_table()
                except Exception:
                    pass
                return []
            logger.error(f'查询产物失败: {e}')
            return []

    def get_latest_artifact(self, artifact_type: str, issue: str = None,
                            ref_uuid: str = None) -> Optional[Dict[str, Any]]:
        """获取指定类型最新一条产物, 无则返回 None。"""
        items = self.get_artifacts(artifact_type=artifact_type, issue=issue, ref_uuid=ref_uuid, limit=1)
        return items[0] if items else None

    # ============================================================
    # 贝叶斯推断结果持久化 (幂等增量写入, v3.3 新增)
    # ============================================================

    def save_bayesian_result(self, issue: str, bayesian_data: Dict[str, Any],
                             target_issue: str = None) -> bool:
        """
        幂等保存贝叶斯推断计算结果到 p5_artifact(type='bayesian_result')。

        如果同一期号已存在记录则跳过写入(update_if_newer=False), 避免重复计算产物堆积。

        Args:
            issue: 关联期号(当前最新期号)
            bayesian_data: 贝叶斯推断结果(dict), 包含各位置后验概率、先验、似然等
            target_issue: 预测目标期号(可选)

        Returns:
            是否保存成功(True/False)
        """
        try:
            # 幂等检查: 同一 issue 已存在则跳过
            existing = self.get_latest_artifact('bayesian_result', issue=issue)
            if existing is not None:
                logger.info(f'期号 {issue} 贝叶斯结果已存在, 跳过幂等写入')
                return False

            meta = {'target_issue': target_issue} if target_issue else {}
            ok = self.save_artifact('bayesian_result', bayesian_data, issue=issue, meta=meta)
            if ok:
                logger.info(f'贝叶斯推断结果已保存: issue={issue}')
            return ok
        except Exception as e:
            logger.error(f'保存贝叶斯结果失败: {e}')
            return False

    def get_bayesian_result(self, issue: str = None) -> Optional[Dict[str, Any]]:
        """
        获取最近的贝叶斯推断结果(可按 issue 过滤)。

        Args:
            issue: 指定期号, 若为 None 则返回最新一条

        Returns:
            贝叶斯推断数据 dict 或 None
        """
        artifact = self.get_latest_artifact('bayesian_result', issue=issue)
        return artifact.get('data') if artifact else None

    def get_bayesian_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取历史贝叶斯推断结果列表(供趋势分析用)。

        Returns:
            结果列表 [{issue, data, created_at}, ...]
        """
        artifacts = self.get_artifacts(artifact_type='bayesian_result', limit=limit)
        return [{
            'id': a.get('id'),
            'issue': a.get('issue'),
            'created_at': a.get('created_at'),
            'data': a.get('data'),
        } for a in artifacts]

    # ============================================================
    # 贝叶斯推断结果专用表 p5_bayesian_result (v3.5 新增, 增量复用)
    # ============================================================

    def insert_bayesian_result(self, issue: str, bayes_list: Any,
                               target_issue: str = None) -> bool:
        """
        幂等保存贝叶斯后验概率到专用表 p5_bayesian_result (按 issue 唯一)。

        同一 issue 已存在则覆盖更新(保证最新计算可见), 不会重复堆积。
        下次同 issue 的预测可直接读取, 跳过 P5Predictor 重算与 AI 交互。

        Args:
            issue: 计算所基于的最新开奖期号
            bayes_list: 后验概率 List[Dict[int,float]] (5个位置, 每位置 {号码:概率})
            target_issue: 预测目标期号(可选)
        Returns:
            是否成功
        """
        try:
            if not bayes_list:
                return False
            bayes_json = json.dumps(bayes_list, ensure_ascii=False, default=str)
            # 各位置概率最高的号码(便于直观查看)
            top_numbers = []
            for pos_d in bayes_list:
                if isinstance(pos_d, dict) and pos_d:
                    top_numbers.append(int(max(pos_d, key=lambda k: pos_d[k])))
                else:
                    top_numbers.append(None)
            top_json = json.dumps(top_numbers, ensure_ascii=False)
            sql = (
                'INSERT INTO p5_bayesian_result (issue, target_issue, bayes_json, top_numbers_json) '
                'VALUES (%s, %s, %s, %s) '
                'ON DUPLICATE KEY UPDATE '
                'target_issue = VALUES(target_issue), '
                'bayes_json = VALUES(bayes_json), '
                'top_numbers_json = VALUES(top_numbers_json)'
            )
            self.execute_with_reconnect(sql, (str(issue), target_issue, bayes_json, top_json))
            self.connection.commit()
            logger.info(f'贝叶斯结果已写入专用表: issue={issue}, top={top_numbers}')
            return True
        except Exception as e:
            logger.error(f'写入 p5_bayesian_result 失败: {e}')
            return False

    def get_bayesian_result_row(self, issue: str) -> Optional[List[Dict[str, float]]]:
        """
        从专用表读取指定 issue 的贝叶斯后验概率(增量复用)。
        返回 List[Dict[int,float]] 或 None。
        """
        try:
            self.cursor.execute(
                'SELECT bayes_json FROM p5_bayesian_result WHERE issue = %s', (str(issue),))
            row = self.cursor.fetchone()
            if not row:
                return None
            return _safe_json_loads(row.get('bayes_json'))
        except Exception as e:
            logger.warning(f'读取 p5_bayesian_result 失败: {e}')
            return None

    def get_bayesian_visual_summary(self, issue: str) -> Optional[Dict]:
        """
        获取贝叶斯结果的可展示摘要（用于GUI报表展示）。

        Returns:
            {
                'issue': str,
                'target_issue': str,
                'top_numbers': List[int],
                'position_details': List[{number: probability, top3: List}]
            } or None
        """
        try:
            row = self.get_bayesian_result_row(issue)
            if not row:
                return None
            
            # 获取完整记录
            self.cursor.execute(
                'SELECT issue, target_issue, top_numbers_json FROM p5_bayesian_result WHERE issue = %s',
                (str(issue),)
            )
            full_row = self.cursor.fetchone()
            if not full_row:
                return None
            
            import json as _json
            top_numbers = _json.loads(full_row.get('top_numbers_json', '[]'))
            
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            position_details = []
            
            for i, pos_dict in enumerate(row):
                if isinstance(pos_dict, dict):
                    # 排序取 Top-3
                    sorted_probs = sorted(pos_dict.items(), key=lambda x: float(x[1]), reverse=True)[:3]
                    position_details.append({
                        'position': pos_names[i],
                        'top_number': top_numbers[i] if i < len(top_numbers) else None,
                        'top3': [{'number': int(k), 'probability': float(v)} for k, v in sorted_probs]
                    })
                else:
                    position_details.append({
                        'position': pos_names[i],
                        'top_number': top_numbers[i] if i < len(top_numbers) else None,
                        'top3': []
                    })
            
            return {
                'issue': full_row.get('issue'),
                'target_issue': full_row.get('target_issue'),
                'top_numbers': top_numbers,
                'position_details': position_details
            }
        except Exception as e:
            logger.warning(f'获取贝叶斯可视化摘要失败: {e}')
            return None



def test_database():
    """测试数据库功能"""
    db = P5Database()
    
    print('=== 测试数据库连接 ===')
    if db.connect():
        print('数据库连接成功')
        
        print('\n=== 测试创建表 ===')
        if db.create_tables():
            print('所有表创建成功')
        
        print('\n=== 测试历史数据操作 ===')
        test_data = [
            {'issue': '2024001', 'date': '2024-01-01', 'numbers': [1,2,3,4,5], 'source': 'test'},
            {'issue': '2024002', 'date': '2024-01-02', 'numbers': [5,4,3,2,1], 'source': 'test'}
        ]
        success, skip = db.insert_history_data(test_data)
        print(f'插入结果: 成功{success}条, 跳过{skip}条')
        
        count = db.get_history_count()
        print(f'历史数据总数: {count}')
        
        latest_issue = db.get_latest_history_issue()
        print(f'最新期号: {latest_issue}')
        
        print('\n=== 测试AI报告操作 ===')
        report_uuid = db.insert_ai_report(
            report_content='测试报告内容',
            data_count=100,
            latest_issue='2024001',
            next_issue='2024002',
            recommended_numbers='[["1","2"],["3","4"],["5","6"],["7","8"],["9","0"]]',
            recommended_combinations='["12345"]',
            confidence_scores='[0.8,0.7,0.6,0.5,0.4]'
        )
        print(f'报告UUID: {report_uuid}')
        
        print('\n=== 测试预测验证 ===')
        if report_uuid:
            db.insert_prediction_record(
                report_uuid=report_uuid,
                target_issue='2024002',
                predicted_numbers='{"wan":["1","2"],"qian":["3","4"],"bai":["5","6"],"shi":["7","8"],"ge":["9","0"]}',
                predicted_combinations='["12345","67890"]',
                confidence_scores='[0.8,0.7,0.6,0.5,0.4]'
            )
            
            result = db.update_prediction_verification(
                report_uuid=report_uuid,
                target_issue='2024002',
                actual_numbers=[1, 3, 5, 7, 9],
                actual_issue='2024002'
            )
            print(f'验证结果: {result}')
        
        stats = db.get_verification_stats()
        print(f'验证统计: {stats}')
        
        db.disconnect()
    else:
        print('数据库连接失败')


if __name__ == '__main__':
    test_database()