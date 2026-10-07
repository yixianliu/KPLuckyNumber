# -*- coding: utf-8 -*-
"""
storage.py — 统一存储后端协议与工厂函数（v4.0 新增）

解决 cache.py / redis_storage_manager.py / mysql_storage_manager.py
三套 `safe_hset` 语义不一致的问题：
- cache.py / redis_storage_manager.py: field 已存在时静默跳过（丢数据）
- mysql_storage_manager.py: upsert 覆盖写（保留最新）

统一语义：**set_hash 始终覆盖写（upsert）**，不再静默跳过。
兼容旧调用：保留 `safe_hset_existed` 别名一个版本。

用法：
    from modules.storage import get_storage
    storage = get_storage()  # 依 config.STORAGE_BACKEND 返回 RedisBackend 或 MySQLBackend
    storage.set_hash(key, field, value, ttl_days=7)
"""

import os
import sys
import json
import logging
from typing import Protocol, Any, Optional
from datetime import timedelta

logger = logging.getLogger(__name__)


class StorageBackend(Protocol):
    """统一存储后端协议"""
    
    def set_hash(self, key: str, field: str, value: Any, ttl_days: int = 7) -> None:
        """设置 Hash 字段（始终覆盖写，不静默跳过）"""
        ...
    
    def get_hash(self, key: str, field: str) -> Any:
        """获取 Hash 字段"""
        ...
    
    def get_hash_all(self, key: str) -> dict:
        """获取 Hash 全部字段"""
        ...


class RedisBackend:
    """Redis 存储后端实现"""
    
    def __init__(self):
        from modules.cache import RedisCache
        self._cache = RedisCache()
        if not self._cache.is_connected():
            logger.warning('[Storage] RedisBackend 初始化：Redis 未连接')
    
    def set_hash(self, key: str, field: str, value: Any, ttl_days: int = 7) -> None:
        """始终覆盖写（修复旧版静默跳过）"""
        try:
            if not self._cache.is_connected():
                logger.error('[Storage] Redis 未连接')
                return
            
            # 统一序列化
            serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            
            # 直接 hset（覆盖写）
            self._cache.client.hset(key, field, serialized)
            
            # 设置过期时间
            if ttl_days:
                self._cache.client.expire(key, ttl_days * 86400)
            
            logger.debug('[Storage] Redis 写入: %s.%s', key, field)
            
        except Exception as e:  # noqa: BLE001
            logger.error('[Storage] Redis 写入失败: %s', e)
    
    def get_hash(self, key: str, field: str) -> Any:
        try:
            if not self._cache.is_connected():
                return None
            val = self._cache.client.hget(key, field)
            if val is None:
                return None
            # 尝试反序列化
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
        except Exception as e:  # noqa: BLE001
            logger.error('[Storage] Redis 读取失败: %s', e)
            return None
    
    def get_hash_all(self, key: str) -> dict:
        try:
            if not self._cache.is_connected():
                return {}
            data = self._cache.client.hgetall(key)
            result = {}
            for k, v in data.items():
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    result[k] = v
            return result
        except Exception as e:  # noqa: BLE001
            logger.error('[Storage] Redis 全读失败: %s', e)
            return {}


class MySQLBackend:
    """MySQL 存储后端实现"""
    
    def __init__(self):
        from modules.mysql_storage_manager import MySQLStorageManager
        self._manager = MySQLStorageManager()
    
    def set_hash(self, key: str, field: str, value: Any, ttl_days: int = 7) -> None:
        """始终覆盖写（MySQL 本身就是 upsert）"""
        try:
            ttl_seconds = ttl_days * 86400 if ttl_days else None
            self._manager.safe_hset(key, field, value, ttl=ttl_seconds)
            logger.debug('[Storage] MySQL 写入: %s.%s', key, field)
        except Exception as e:  # noqa: BLE001
            logger.error('[Storage] MySQL 写入失败: %s', e)
    
    def get_hash(self, key: str, field: str) -> Any:
        try:
            return self._manager.safe_hget(key, field)
        except Exception as e:  # noqa: BLE001
            logger.error('[Storage] MySQL 读取失败: %s', e)
            return None
    
    def get_hash_all(self, key: str) -> dict:
        try:
            return self._manager.safe_hgetall(key)
        except Exception as e:  # noqa: BLE001
            logger.error('[Storage] MySQL 全读失败: %s', e)
            return {}


def get_storage() -> StorageBackend:
    """根据配置返回存储后端实例"""
    try:
        from config import STORAGE_BACKEND
        backend = STORAGE_BACKEND.lower() if isinstance(STORAGE_BACKEND, str) else 'redis'
    except Exception:
        backend = 'redis'
    
    if backend == 'mysql':
        logger.info('[Storage] 使用 MySQL 存储后端')
        return MySQLBackend()
    else:
        logger.info('[Storage] 使用 Redis 存储后端')
        return RedisBackend()


# =====================================================================
# 兼容层：保留 safe_hset_existed 别名（一个版本后移除）
# =====================================================================

# 旧版 cache.py / redis_storage_manager.py 调用 safe_hset_existed 期望"存在即跳过"语义
# 新版统一为覆盖写，这里提供兼容别名（仅供过渡期使用）
def safe_hset_existed(key: str, field: str, value: Any, ttl_days: int = 7) -> bool:
    """兼容别名：旧版"存在即跳过"语义（已废弃，下一版本移除）"""
    logger.warning('[Storage] safe_hset_existed 已废弃，请迁移到 get_storage().set_hash()')
    storage = get_storage()
    # 兼容模式：检查是否存在，存在则跳过（旧语义）
    existing = storage.get_hash(key, field)
    if existing is not None:
        return False
    storage.set_hash(key, field, value, ttl_days)
    return True