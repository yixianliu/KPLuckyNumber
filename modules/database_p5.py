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
                    connect_timeout=10
                )
                self.cursor = self.connection.cursor()
                logger.info('MySQL数据库连接成功（排列5）')
                return True
            except pymysql.err.OperationalError as e:
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
            
            self.connection.commit()
            logger.info('排列5数据表创建成功（历史数据、走势数据、AI报告、预测验证、性能统计、万位走势、千位走势、百位走势、十位走势、和尾走势、后三走势、专家推荐）')
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
    
    def get_history_data(self, limit: int = 500, order_by: str = 'issue DESC') -> List[Dict[str, Any]]:
        """
        获取历史开奖数据
        
        Args:
            limit: 返回数量限制
            order_by: 排序方式
        """
        try:
            sql = f'SELECT * FROM p5_history_data WHERE is_valid = 1 ORDER BY {order_by} LIMIT %s'
            self.cursor.execute(sql, (limit,))
            return self.cursor.fetchall()
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
                         key_conclusions=None, risk_warning=None, report_format='TEXT'):
        """插入排列5AI分析报告"""
        try:
            report_uuid = str(uuid.uuid4())
            report_date = datetime.now().strftime('%Y-%m-%d')

            sql = '''
            INSERT INTO p5_ai_report (
                report_date, report_uuid, data_count, latest_issue, next_issue,
                trend_analysis, probability_stats, recommended_numbers,
                recommended_combinations, confidence_scores, recommendation_reasons,
                key_conclusions, risk_warning, report_content, report_format
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            '''

            self.cursor.execute(sql, (
                report_date, report_uuid, data_count, latest_issue, next_issue,
                trend_analysis, probability_stats, recommended_numbers,
                recommended_combinations, confidence_scores, recommendation_reasons,
                key_conclusions, risk_warning, report_content, report_format
            ))
            self.connection.commit()
            logger.info(f'成功插入排列5AI分析报告, UUID: {report_uuid}')
            return report_uuid
        except Exception as e:
            logger.error(f'插入排列5AI分析报告失败: {e}')
            return None
    
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
            
            # 比对结果
            wan_match = int(actual_numbers[0]) in predicted.get('wan', [])
            qian_match = int(actual_numbers[1]) in predicted.get('qian', [])
            bai_match = int(actual_numbers[2]) in predicted.get('bai', [])
            shi_match = int(actual_numbers[3]) in predicted.get('shi', [])
            ge_match = int(actual_numbers[4]) in predicted.get('ge', [])
            
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