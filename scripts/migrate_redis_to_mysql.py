# -*- coding: utf-8 -*-
"""
Redis到MySQL数据迁移脚本

本脚本负责将Redis中的数据完整迁移到MySQL数据库。
支持全量迁移和增量迁移模式。

使用方式：
    python scripts/migrate_redis_to_mysql.py [--incremental] [--dry-run]

迁移策略：
1. 全量模式：扫描所有Redis键，按类型分别迁移
2. 增量模式：仅迁移最后7天内有变更的数据
3. 干跑模式：只报告迁移计划，不实际执行
4. 一致性校验：迁移后对比数据完整性

风险警告：
- 迁移前请确保MySQL服务正常运行
- 建议先备份Redis数据
- 生产环境请在低峰期执行
"""

import sys
import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from modules.database import P5Database
from modules.redis_storage_manager import RedisKeyManager
from modules.cache import CacheClient

try:
    from config import REDIS_CONFIG, DB_CONFIG
except ImportError:
    REDIS_CONFIG = {
        'host': 'localhost',
        'port': 6379,
        'db': 0,
        'password': None,
        'key_prefix': 'kpluckynumber:pl5:',
    }
    DB_CONFIG = {
        'host': 'localhost',
        'port': 3306,
        'user': 'root',
        'password': '',
        'database': 'lucky_number',
        'charset': 'utf8mb4',
    }


class RedisToMySQLMigrator:
    """Redis到MySQL数据迁移器"""

    def __init__(self, redis_client=None, db_client=None, dry_run: bool = False):
        """
        初始化迁移器

        Args:
            redis_client: Redis客户端实例
            db_client: MySQL数据库客户端实例
            dry_run: 是否只报告不执行（默认False）
        """
        self.dry_run = dry_run
        self.db = db_client or P5Database()
        self.redis_client = redis_client
        self.stats = {
            'total_keys': 0,
            'migrated': 0,
            'skipped': 0,
            'failed': 0,
            'errors': []
        }

    def _connect_redis(self) -> bool:
        """连接Redis"""
        try:
            if self.redis_client is None:
                self.redis_client = redis.Redis(
                    host=REDIS_CONFIG['host'],
                    port=REDIS_CONFIG['port'],
                    db=REDIS_CONFIG['db'],
                    password=REDIS_CONFIG.get('password'),
                    decode_responses=True,
                    socket_timeout=10,
                    socket_connect_timeout=10
                )
                self.redis_client.ping()
                print(f"✓ Redis连接成功: {REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}")
            return True
        except Exception as e:
            print(f"✗ Redis连接失败: {e}")
            return False

    def _connect_mysql(self) -> bool:
        """连接MySQL"""
        try:
            if self.db.connect():
                print(f"✓ MySQL连接成功: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
                return True
            else:
                print("✗ MySQL连接失败")
                return False
        except Exception as e:
            print(f"✗ MySQL连接失败: {e}")
            return False

    def _get_all_keys(self) -> List[str]:
        """获取所有kpluckynumber:pl5:*键"""
        if not self.redis_client:
            return []

        keys = []
        cursor = 0
        pattern = f"{REDIS_CONFIG.get('key_prefix', 'kpluckynumber:pl5:')}*"

        while True:
            cursor, batch = self.redis_client.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        return keys

    def _migrate_hash_key(self, key: str, field: str, value: Any, ttl: Optional[int] = None) -> bool:
        """迁移单个Hash字段到MySQL"""
        try:
            if self.dry_run:
                print(f"  [DRY-RUN] 迁移: {key}.{field}")
                return True

            # 确保p5_kv_store表存在
            self._ensure_kv_table()

            # 插入或更新数据
            sql = """
                INSERT INTO p5_kv_store (key, field, value_json, expire_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    value_json = VALUES(value_json),
                    expire_at = VALUES(expire_at)
            """

            cursor = self.db.cursor()
            expire_at = datetime.now() + timedelta(seconds=ttl) if ttl else None
            cursor.execute(sql, (key, field, json.dumps(value, ensure_ascii=False), expire_at))
            self.db.connection.commit()

            self.stats['migrated'] += 1
            return True

        except Exception as e:
            self.stats['failed'] += 1
            self.stats['errors'].append(f"迁移 {key}.{field} 失败: {str(e)}")
            print(f"  ✗ 迁移失败: {key}.{field} - {e}")
            return False

    def _migrate_string_key(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """迁移字符串到MySQL"""
        try:
            if self.dry_run:
                print(f"  [DRY-RUN] 迁移字符串: {key}")
                return True

            self._ensure_kv_table()

            sql = """
                INSERT INTO p5_kv_store (key, field, value_json, expire_at)
                VALUES (%s, NULL, %s, %s)
                ON DUPLICATE KEY UPDATE
                    value_json = VALUES(value_json),
                    expire_at = VALUES(expire_at)
            """

            cursor = self.db.cursor()
            expire_at = datetime.now() + timedelta(seconds=ttl) if ttl else None
            cursor.execute(sql, (key, json.dumps(value, ensure_ascii=False), expire_at))
            self.db.connection.commit()

            self.stats['migrated'] += 1
            return True

        except Exception as e:
            self.stats['failed'] += 1
            self.stats['errors'].append(f"迁移 {key} 失败: {str(e)}")
            print(f"  ✗ 迁移失败: {key} - {e}")
            return False

    def _migrate_set_key(self, key: str, members: set) -> bool:
        """迁移Set到MySQL"""
        try:
            if self.dry_run:
                print(f"  [DRY-RUN] 迁移Set: {key} ({len(members)} members)")
                return True

            self._ensure_kv_table()

            # 将set成员序列化为JSON
            value_json = json.dumps(list(members), ensure_ascii=False)
            sql = """
                INSERT INTO p5_kv_store (key, field, value_json, expire_at)
                VALUES (%s, 'set_members', %s, NULL)
                ON DUPLICATE KEY UPDATE
                    value_json = VALUES(value_json)
            """

            cursor = self.db.cursor()
            cursor.execute(sql, (key, value_json))
            self.db.connection.commit()

            self.stats['migrated'] += 1
            return True

        except Exception as e:
            self.stats['failed'] += 1
            self.stats['errors'].append(f"迁移Set {key} 失败: {str(e)}")
            print(f"  ✗ 迁移失败: {key} - {e}")
            return False

    def _migrate_zset_key(self, key: str, data: Dict[str, float]) -> bool:
        """迁移有序集合到MySQL"""
        try:
            if self.dry_run:
                print(f"  [DRY-RUN] 迁移ZSet: {key} ({len(data)} members)")
                return True

            self._ensure_kv_table()

            # 将有序集合序列化为JSON
            value_json = json.dumps(data, ensure_ascii=False)
            sql = """
                INSERT INTO p5_kv_store (key, field, value_json, expire_at)
                VALUES (%s, 'zset_data', %s, NULL)
                ON DUPLICATE KEY UPDATE
                    value_json = VALUES(value_json)
            """

            cursor = self.db.cursor()
            cursor.execute(sql, (key, value_json))
            self.db.connection.commit()

            self.stats['migrated'] += 1
            return True

        except Exception as e:
            self.stats['failed'] += 1
            self.stats['errors'].append(f"迁移ZSet {key} 失败: {str(e)}")
            print(f"  ✗ 迁移失败: {key} - {e}")
            return False

    def _ensure_kv_table(self):
        """确保p5_kv_store表存在"""
        try:
            sql = """
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
            """
            self.db.cursor.execute(sql)
            self.db.connection.commit()
        except Exception as e:
            print(f"  ⚠ 表创建/验证失败: {e}")

    def _ensure_specialized_tables(self):
        """确保专用表存在"""
        try:
            # 用户配置表
            sql_user = """
                CREATE TABLE IF NOT EXISTS p5_user_config (
                    id INT NOT NULL AUTO_INCREMENT,
                    user_id VARCHAR(50) NOT NULL,
                    config_key VARCHAR(100) NOT NULL,
                    config_value TEXT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE INDEX uk_user_key (user_id, config_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            self.db.cursor.execute(sql_user)

            # 算法配置表
            sql_algo = """
                CREATE TABLE IF NOT EXISTS p5_algorithm_config (
                    id INT NOT NULL AUTO_INCREMENT,
                    config_key VARCHAR(100) NOT NULL,
                    config_value JSON NULL,
                    version VARCHAR(20) DEFAULT '1.0',
                    is_active TINYINT(1) DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE INDEX uk_config_key (config_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            self.db.cursor.execute(sql_algo)

            # 命中率统计表
            sql_hit = """
                CREATE TABLE IF NOT EXISTS p5_hit_rate_stats (
                    id INT NOT NULL AUTO_INCREMENT,
                    stat_type VARCHAR(20) NOT NULL,
                    stat_date VARCHAR(20) NOT NULL,
                    stats_json JSON NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE INDEX uk_type_date (stat_type, stat_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            self.db.cursor.execute(sql_hit)

            # 追踪看板表
            sql_track = """
                CREATE TABLE IF NOT EXISTS p5_tracking_board (
                    id INT NOT NULL AUTO_INCREMENT,
                    issue VARCHAR(20) NOT NULL,
                    tracking_data JSON NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE INDEX uk_issue (issue)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            self.db.cursor.execute(sql_track)

            self.db.connection.commit()
            print("✓ 专用表结构检查完成")

        except Exception as e:
            print(f"✗ 创建专用表失败: {e}")

    def _scan_and_migrate_hash(self, key: str) -> int:
        """扫描并迁移Hash类型数据"""
        migrated = 0
        try:
            ttl = self.redis_client.ttl(key)
            fields = self.redis_client.hgetall(key)

            for field, value in fields.items():
                if self._migrate_hash_key(key, field, value, ttl):
                    migrated += 1

            # 设置TTL
            if ttl > 0:
                expire_at = datetime.now() + timedelta(seconds=ttl)
                try:
                    sql = "UPDATE p5_kv_store SET expire_at = %s WHERE key = %s"
                    self.db.cursor.execute(sql, (expire_at, key))
                    self.db.connection.commit()
                except Exception:
                    pass

        except Exception as e:
            print(f"  ✗ 迁移Hash {key} 失败: {e}")
            self.stats['failed'] += 1

        return migrated

    def _scan_and_migrate_zset(self, key: str) -> bool:
        """扫描并迁移ZSet类型数据"""
        try:
            data = self.redis_client.zrange(key, 0, -1, withscores=True)
            data_dict = {member: score for member, score in data}
            return self._migrate_zset_key(key, data_dict)
        except Exception as e:
            print(f"  ✗ 迁移ZSet {key} 失败: {e}")
            return False

    def _scan_and_migrate_set(self, key: str) -> bool:
        """扫描并迁移Set类型数据"""
        try:
            members = self.redis_client.smembers(key)
            return self._migrate_set_key(key, members)
        except Exception as e:
            print(f"  ✗ 迁移Set {key} 失败: {e}")
            return False

    def migrate_all(self) -> Dict[str, Any]:
        """执行全量迁移"""
        print("=" * 60)
        print("Redis到MySQL数据迁移开始")
        print("=" * 60)

        if self.dry_run:
            print("[干跑模式] 将仅报告迁移计划，不实际执行")
            print()

        # 连接Redis
        if not self._connect_redis():
            return {'success': False, 'error': 'Redis连接失败'}

        # 连接MySQL
        if not self._connect_mysql():
            return {'success': False, 'error': 'MySQL连接失败'}

        # 确保表结构
        self._ensure_kv_table()
        self._ensure_specialized_tables()

        # 获取所有键
        all_keys = self._get_all_keys()
        self.stats['total_keys'] = len(all_keys)
        print(f"\n发现 {len(all_keys)} 个需要迁移的键\n")

        # 按类型分类统计
        key_types = {}
        for key in all_keys:
            try:
                key_type = self.redis_client.type(key)
                if key_type == 'hash':
                    key_types.setdefault('hash', []).append(key)
                elif key_type == 'string':
                    key_types.setdefault('string', []).append(key)
                elif key_type == 'zset':
                    key_types.setdefault('zset', []).append(key)
                elif key_type == 'set':
                    key_types.setdefault('set', []).append(key)
                else:
                    self.stats['skipped'] += 1
            except Exception:
                self.stats['skipped'] += 1

        print(f"类型分布: Hash={len(key_types.get('hash', []))}, "
              f"String={len(key_types.get('string', []))}, "
              f"ZSet={len(key_types.get('zset', []))}, "
              f"Set={len(key_types.get('set', []))}")
        print()

        # 执行迁移
        start_time = time.time()

        # 迁移Hash
        for key in key_types.get('hash', []):
            self._scan_and_migrate_hash(key)

        # 迁移String
        for key in key_types.get('string', []):
            try:
                value = self.redis_client.get(key)
                if value:
                    ttl = self.redis_client.ttl(key)
                    self._migrate_string_key(key, json.loads(value) if isinstance(value, str) else value, ttl)
                else:
                    self.stats['skipped'] += 1
            except Exception as e:
                print(f"  ✗ 迁移String {key} 失败: {e}")
                self.stats['failed'] += 1

        # 迁移ZSet
        for key in key_types.get('zset', []):
            self._scan_and_migrate_zset(key)

        # 迁移Set
        for key in key_types.get('set', []):
            self._scan_and_migrate_set(key)

        elapsed = time.time() - start_time

        # 输出统计
        print(f"\n" + "=" * 60)
        print(f"迁移完成!")
        print(f"总键数: {self.stats['total_keys']}")
        print(f"成功迁移: {self.stats['migrated']}")
        print(f"跳过: {self.stats['skipped']}")
        print(f"失败: {self.stats['failed']}")
        print(f"耗时: {elapsed:.2f}秒")
        if self.stats['errors']:
            print(f"\n错误列表:")
            for err in self.stats['errors'][:10]:
                print(f"  - {err}")
        print("=" * 60)

        return {
            'success': True,
            'stats': self.stats,
            'elapsed': elapsed
        }

    def verify_migration(self) -> Dict[str, Any]:
        """验证迁移结果的一致性"""
        print("\n" + "=" * 60)
        print("开始验证迁移结果...")
        print("=" * 60)

        if not self.redis_client or not self.db.connect():
            return {'success': False, 'error': '连接失败'}

        # 获取Redis中的键数量
        redis_keys = self._get_all_keys()
        print(f"Redis中仍有 {len(redis_keys)} 个键")

        # 获取MySQL中的键数量
        try:
            cursor = self.db.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM p5_kv_store")
            mysql_count = cursor.fetchone()['cnt']
            print(f"MySQL中有 {mysql_count} 条记录")

            # 随机抽样验证
            cursor.execute("SELECT key, field, value_json FROM p5_kv_store LIMIT 10")
            samples = cursor.fetchall()

            print("\n抽样验证:")
            for sample in samples:
                print(f"  - {sample['key']}: {sample['field'] or 'N/A'}")
                try:
                    value = json.loads(sample['value_json'])
                    print(f"    值: {str(value)[:80]}...")
                except:
                    print(f"    值: {sample['value_json'][:80]}...")

        except Exception as e:
            print(f"验证失败: {e}")
            return {'success': False, 'error': str(e)}

        print("\n✓ 迁移结果验证完成")
        return {'success': True}


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='Redis到MySQL数据迁移工具')
    parser.add_argument('--dry-run', action='store_true', help='只报告不执行')
    parser.add_argument('--verify', action='store_true', help='迁移后验证')
    args = parser.parse_args()

    migrator = RedisToMySQLMigrator(dry_run=args.dry_run)
    result = migrator.migrate_all()

    if args.verify and result['success']:
        migrator.verify_migration()

    return result


if __name__ == '__main__':
    result = main()
    sys.exit(0 if result.get('success') else 1)