# -*- coding: utf-8 -*-
"""
MySQL数据存储管理器 - 替代Redis存储机制

本模块实现将原有Redis数据存储机制全面迁移至MySQL数据库服务器。

设计原则：
1. 保持与 RedisKeyManager 相同的键命名空间语义
2. 使用MySQL的JSON列存储结构化数据
3. 统一的TTL管理通过created_at + expire_at实现
4. 向下兼容 CacheClient 和 RedisKeyManager 接口

数据表结构：
- p5_raw_data: 原始开奖数据
- p5_expert_report: 专家文章分析
- p5_trend_analysis: 走势分析结果
- p5_integrated_report: 综合报告
- p5_prediction_result: 预测结果
- p5_features: 特征数据
- p5_counter_examples: 反例数据
- p5_hit_rate_stats: 命中率统计
- p5_tracking_board: 追踪看板
- p5_model_params: 模型参数
- p5_user_config: 用户配置
- p5_algorithm_config: 算法配置
"""

import logging
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class MySQLStorageManager:
    """
    MySQL存储管理器 - 替代Redis存储

    负责：
    - 类似 RedisKeyManager 的键命名空间规划
    - 数据结构存储与查询
    - 过期策略管理
    - 数据完整性校验
    """

    # 命名空间前缀 (与Redis保持一致)
    NAMESPACE_PREFIX = 'kpluckynumber:pl5:'

    def __init__(self, db_client=None):
        """
        初始化MySQL存储管理器

        Args:
            db_client: P5Database 实例，如果为None则按需创建
        """
        self.db = db_client
        self._ensure_tables()

    def _ensure_tables(self):
        """确保所需表结构已创建"""
        if self.db is None:
            try:
                from modules.database import P5Database
                self.db = P5Database()
                self.db.connect()
            except Exception as e:
                logger.error(f"初始化MySQL存储管理器失败: {e}")
                return

        try:
            self.db.create_kv_table()
        except Exception as e:
            logger.error(f"创建存储表失败: {e}")

    def _get_table_for_key(self, key: str) -> Tuple[str, str]:
        """
        根据Redis key模式确定MySQL表名和主键

        Returns:
            (table_name, primary_key_value)
        """
        if key.startswith(self.NAMESPACE_PREFIX + 'raw:'):
            issue = key.replace(self.NAMESPACE_PREFIX + 'raw:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'expert_report:'):
            article_id = key.replace(self.NAMESPACE_PREFIX + 'expert_report:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'trend_analysis:'):
            issue = key.replace(self.NAMESPACE_PREFIX + 'trend_analysis:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'integrated_report:'):
            issue = key.replace(self.NAMESPACE_PREFIX + 'integrated_report:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'prediction:'):
            issue = key.replace(self.NAMESPACE_PREFIX + 'prediction:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'features:'):
            issue = key.replace(self.NAMESPACE_PREFIX + 'features:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'counter_examples:'):
            issue = key.replace(self.NAMESPACE_PREFIX + 'counter_examples:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'tracking_board:'):
            issue = key.replace(self.NAMESPACE_PREFIX + 'tracking_board:', '')
            return 'p5_kv_store', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'model_params:'):
            return 'p5_model_params', 'version'
        elif key.startswith(self.NAMESPACE_PREFIX + 'model_version:'):
            return 'p5_model_params', 'key:model_version:current'
        elif key.startswith(self.NAMESPACE_PREFIX + 'hit_rate:'):
            return 'p5_hit_rate_stats', f"key:{key}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'user_config:'):
            user_id = key.replace(self.NAMESPACE_PREFIX + 'user_config:', '')
            return 'p5_user_config', f"user_id:{user_id}"
        elif key.startswith(self.NAMESPACE_PREFIX + 'algorithm_config:'):
            return 'p5_algorithm_config', 'key:algorithm_config:current'
        else:
            return 'p5_kv_store', f"key:{key}"

    # ==================== 存储接口 ====================

    def safe_hset(self, key: str, field: str, value: Any,
                  ttl: Optional[int] = None) -> bool:
        """
        安全的Hash设置 - 保存到MySQL

        Args:
            key: Redis Key
            field: Hash字段 (用于存储多个字段)
            value: 要存储的值
            ttl: 过期时间(秒)，None表示不过期

        Returns:
            是否成功
        """
        try:
            if self.db is None:
                logger.error("数据库未连接")
                return False

            table_name, primary_key = self._get_table_for_key(key)
            expire_at = None
            if ttl:
                expire_at = datetime.now() + timedelta(seconds=ttl)

            # 将 value 序列化为 JSON
            value_json = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value

            # 使用INSERT...ON DUPLICATE KEY UPDATE
            sql = f'''
                INSERT INTO {table_name}
                (key, field, value_json, expire_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    value_json = VALUES(value_json),
                    expire_at = VALUES(expire_at)
            '''

            cursor = self.db.cursor()
            cursor.execute(sql, (key, field, value_json, expire_at))
            self.db.connection.commit()

            logger.info(f"数据写入成功: {key}.{field}")
            return True

        except Exception as e:
            logger.error(f"写入MySQL失败: {e}")
            return False

    def safe_hset_existed(self, key: str, field: str, value: Any,
                          ttl: Optional[int] = None) -> bool:
        """
        安全的Hash设置-允许更新已存在字段

        Args:
            key: Redis Key
            field: Hash字段
            value: 要存储的值
            ttl: 过期时间(秒)

        Returns:
            是否成功
        """
        return self.safe_hset(key, field, value, ttl)

    def get(self, key: str, field: Optional[str] = None) -> Optional[Any]:
        """
        获取数据

        Args:
            key: Redis Key
            field: 指定field (返回单个field的值) 或 None (返回整个value)

        Returns:
            保存的值
        """
        try:
            if self.db is None:
                logger.error("数据库未连接")
                return None

            table_name, _ = self._get_table_for_key(key)

            sql = f"SELECT value_json, expire_at FROM {table_name} WHERE key = %s"
            params = [key]

            if field:
                sql += " AND field = %s"
                params.append(field)
            else:
                params.append(None)

            cursor = self.db.cursor()
            cursor.execute(sql, params)
            result = cursor.fetchone()

            if result:
                value_json = result.get('value_json')
                expire_at = result.get('expire_at')

                # 检查是否过期
                if expire_at and isinstance(expire_at, str):
                    expire_at = datetime.fromisoformat(expire_at.replace(' ', 'T'))

                if expire_at and datetime.now() > expire_at:
                    logger.warning(f"Key已过期: {key}")
                    return None

                return json.loads(value_json) if value_json else None

            logger.info(f"未找到数据: {key}")
            return None

        except Exception as e:
            logger.error(f"获取MySQL数据失败: {e}")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        设置值 (字符串类型)

        Args:
            key: Key
            value: 要存储的值
            ttl: 过期时间(秒)

        Returns:
            是否成功
        """
        try:
            table_name, _ = self._get_table_for_key(key)
            expire_at = None
            if ttl:
                expire_at = datetime.now() + timedelta(seconds=ttl)

            value_json = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value

            sql = f'''
                INSERT INTO {table_name} (key, field, value_json, expire_at)
                VALUES (%s, NULL, %s, %s)
                ON DUPLICATE KEY UPDATE
                    value_json = VALUES(value_json),
                    expire_at = VALUES(expire_at)
            '''

            cursor = self.db.cursor()
            cursor.execute(sql, (key, value_json, expire_at))
            self.db.connection.commit()

            logger.info(f"数据写入成功: {key}")
            return True

        except Exception as e:
            logger.error(f"写入MySQL失败: {e}")
            return False

    def delete(self, key: str) -> bool:
        """删除键"""
        try:
            if self.db is None:
                return False

            table_name, _ = self._get_table_for_key(key)
            sql = f"DELETE FROM {table_name} WHERE key = %s"

            cursor = self.db.cursor()
            cursor.execute(sql, (key,))
            self.db.connection.commit()

            return True

        except Exception as e:
            logger.error(f"删除MySQL数据失败: {e}")
            return False

    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        try:
            if self.db is None:
                return False

            table_name, _ = self._get_table_for_key(key)
            sql = f"SELECT 1 FROM {table_name} WHERE key = %s AND (expire_at IS NULL OR expire_at > %s)"

            cursor = self.db.cursor()
            cursor.execute(sql, (key, datetime.now()))
            result = cursor.fetchone()

            return result is not None

        except Exception as e:
            logger.error(f"检查MySQL数据失败: {e}")
            return False

    # ==================== 存储方法 ====================

    def save_raw_data(self, issue: str, data: Dict[str, Any], expire_days: int = 7) -> bool:
        """保存原始数据到MySQL"""
        ttl = expire_days * 86400
        return self.set(f"{self.NAMESPACE_PREFIX}raw:{issue}", data, ttl)

    def get_raw_data(self, issue: str) -> Optional[Dict[str, Any]]:
        """获取原始数据"""
        return self.get(f"{self.NAMESPACE_PREFIX}raw:{issue}")

    def save_expert_data(self, expert_name: str, data: Dict[str, Any], expire_days: int = 3) -> bool:
        """保存专家数据"""
        ttl = expire_days * 86400
        return self.set(f"{self.NAMESPACE_PREFIX}expert_report:{expert_name}", data, ttl)

    def get_expert_data(self, expert_name: str) -> Optional[Dict[str, Any]]:
        """获取专家数据"""
        return self.get(f"{self.NAMESPACE_PREFIX}expert_report:{expert_name}")

    def save_ai_analysis(self, issue: str, data: Dict[str, Any], expire_days: int = 7) -> bool:
        """保存AI分析数据"""
        ttl = expire_days * 86400
        return self.set(f"{self.NAMESPACE_PREFIX}ai:{issue}", data, ttl)

    def get_ai_analysis(self, issue: str) -> Optional[Dict[str, Any]]:
        """获取AI分析数据"""
        return self.get(f"{self.NAMESPACE_PREFIX}ai:{issue}")

    def save_combined_analysis(self, issue: str, data: Dict[str, Any], expire_days: int = 14) -> bool:
        """保存综合分析数据"""
        ttl = expire_days * 86400
        return self.set(f"{self.NAMESPACE_PREFIX}integrated_report:{issue}", data, ttl)

    def get_combined_analysis(self, issue: str) -> Optional[Dict[str, Any]]:
        """获取综合分析数据"""
        return self.get(f"{self.NAMESPACE_PREFIX}integrated_report:{issue}")

    def save_trend_analysis(self, issue: str, data: Dict[str, Any], expire_days: int = 7) -> bool:
        """保存走势AI分析数据"""
        ttl = expire_days * 86400
        return self.set(f"{self.NAMESPACE_PREFIX}trend_analysis:{issue}", data, ttl)

    def get_trend_analysis(self, issue: str) -> Optional[Dict[str, Any]]:
        """获取走势AI分析数据"""
        return self.get(f"{self.NAMESPACE_PREFIX}trend_analysis:{issue}")

    def save_article_data(self, article_id: str, data: Dict[str, Any], expire_days: int = 7) -> bool:
        """保存单篇文章数据"""
        ttl = expire_days * 86400
        return self.set(f"{self.NAMESPACE_PREFIX}article:{article_id}", data, ttl)

    def get_article_data(self, article_id: str) -> Optional[Dict[str, Any]]:
        """获取单篇文章数据"""
        return self.get(f"{self.NAMESPACE_PREFIX}article:{article_id}")

    def save_prediction_result(self, issue: str, data: Dict[str, Any], expire_days: int = 90) -> bool:
        """保存预测结果"""
        ttl = expire_days * 86400
        return self.set(f"{self.NAMESPACE_PREFIX}prediction:{issue}", data, ttl)

    def get_prediction_result(self, issue: str) -> Optional[Dict[str, Any]]:
        """获取预测结果"""
        return self.get(f"{self.NAMESPACE_PREFIX}prediction:{issue}")

    # ==================== 统计方法 ====================

    def get_kv_store_stats(self) -> Dict[str, Any]:
        """获取存储统计信息"""
        try:
            sql = """
                SELECT 
                    COUNT(*) as total_keys,
                    SUM(CASE WHEN expire_at IS NOT NULL AND expire_at > NOW() THEN 1 ELSE 0 END) as active_keys,
                    SUM(CASE WHEN expire_at IS NOT NULL AND expire_at <= NOW() THEN 1 ELSE 0 END) as expired_keys
                FROM p5_kv_store
            """
            cursor = self.db.cursor()
            cursor.execute(sql)
            return cursor.fetchone() or {}
        except Exception as e:
            logger.error(f"获取存储统计失败: {e}")
            return {}

    def cleanup_expired(self, batch_size: int = 100) -> int:
        """清理过期数据"""
        try:
            if self.db is None:
                return 0

            sql = "DELETE FROM p5_kv_store WHERE expire_at IS NOT NULL AND expire_at <= NOW() LIMIT %s"
            cursor = self.db.cursor()
            cursor.execute(sql, (batch_size,))
            self.db.connection.commit()

            deleted_count = cursor.rowcount
            if deleted_count > 0:
                logger.info(f"已清理 {deleted_count} 条过期数据")

            return deleted_count

        except Exception as e:
            logger.error(f"清理过期数据失败: {e}")
            return 0


class KVStoreTableMixin:
    """
    为P5Database添加通用 key-value 存储表
    """

    def create_kv_table(self):
        """创建通用的 key-value 存储表"""
        try:
            if not self.connection:
                self.connect()

            sql = '''
                CREATE TABLE IF NOT EXISTS p5_kv_store (
                    id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                    key VARCHAR(255) NOT NULL COMMENT '存储键',
                    field VARCHAR(100) NULL DEFAULT NULL COMMENT '字段(用于Hash存储)',
                    value_json LONGTEXT NULL DEFAULT NULL COMMENT '存储值(JSON)',
                    expire_at DATETIME NULL DEFAULT NULL COMMENT '过期时间',
                    created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    PRIMARY KEY (id) USING BTREE,
                    UNIQUE INDEX uk_key_field (key ASC, field ASC) USING BTREE,
                    INDEX idx_key (key ASC) USING BTREE,
                    INDEX idx_expire_at (expire_at ASC) USING BTREE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通用键值存储表(替代Redis)';
            '''
            self.cursor.execute(sql)

            # 为特定用途创建专用表
            self._create_specialized_tables()

        except Exception as e:
            logger.error(f"创建KV存储表失败: {e}")

    def _create_specialized_tables(self):
        """创建特定用途的专用表"""

        # 用户配置表
        sql_user_config = '''
            CREATE TABLE IF NOT EXISTS p5_user_config (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                user_id VARCHAR(50) NOT NULL COMMENT '用户ID',
                config_key VARCHAR(100) NOT NULL COMMENT '配置键',
                config_value TEXT NULL DEFAULT NULL COMMENT '配置值(JSON)',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_user_key (user_id ASC, config_key ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户配置表';
        '''
        try:
            self.cursor.execute(sql_user_config)
        except Exception as e:
            logger.error(f"创建用户配置表失败: {e}")

        # 算法配置表
        sql_algo_config = '''
            CREATE TABLE IF NOT EXISTS p5_algorithm_config (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                config_key VARCHAR(100) NOT NULL COMMENT '配置键',
                config_value JSON NULL DEFAULT NULL COMMENT '配置值',
                version VARCHAR(20) NULL DEFAULT '1.0' COMMENT '版本',
                is_active TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否活跃',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_config_key (config_key ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='算法配置表';
        '''
        try:
            self.cursor.execute(sql_algo_config)
        except Exception as e:
            logger.error(f"创建算法配置表失败: {e}")

        # 命中率统计表
        sql_hit_rate = '''
            CREATE TABLE IF NOT EXISTS p5_hit_rate_stats (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                stat_type VARCHAR(20) NOT NULL COMMENT '统计类型(daily/weekly/monthly)',
                stat_date VARCHAR(20) NOT NULL COMMENT '统计日期',
                stats_json JSON NULL DEFAULT NULL COMMENT '统计数据(JSON)',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_type_date (stat_type ASC, stat_date ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='命中率统计表';
        '''
        try:
            self.cursor.execute(sql_hit_rate)
        except Exception as e:
            logger.error(f"创建命中率统计表失败: {e}")

        # 追踪看板表
        sql_tracking = '''
            CREATE TABLE IF NOT EXISTS p5_tracking_board (
                id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                issue VARCHAR(20) NOT NULL COMMENT '期号',
                tracking_data JSON NULL DEFAULT NULL COMMENT '追踪数据(JSON)',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                PRIMARY KEY (id) USING BTREE,
                UNIQUE INDEX uk_issue (issue ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='追踪看板表';
        '''
        try:
            self.cursor.execute(sql_tracking)
        except Exception as e:
            logger.error(f"创建追踪看板表失败: {e}")

    def get_or_create_config(self, config_key: str, default_value: Any = None) -> Any:
        """获取配置，如果不存在则创建"""
        try:
            sql = "SELECT config_value FROM p5_algorithm_config WHERE config_key = %s AND is_active = 1"
            self.cursor.execute(sql, (config_key,))
            result = self.cursor.fetchone()

            if result:
                return json.loads(result['config_value']) if result['config_value'] else default_value

            if default_value is not None:
                sql = '''
                    INSERT INTO p5_algorithm_config (config_key, config_value)
                    VALUES (%s, %s)
                '''
                self.cursor.execute(sql, (config_key, json.dumps(default_value, ensure_ascii=False)))
                self.connection.commit()

            return default_value

        except Exception as e:
            logger.error(f"获取/创建配置失败: {e}")
            return default_value

    def update_config(self, config_key: str, config_value: Any) -> bool:
        """更新配置"""
        try:
            sql = '''
                INSERT INTO p5_algorithm_config (config_key, config_value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)
            '''
            self.cursor.execute(sql, (config_key, json.dumps(config_value, ensure_ascii=False)))
            self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"更新配置失败: {e}")
            return False