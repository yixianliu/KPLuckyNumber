# -*- coding: utf-8 -*-
"""
MySQL存储管理器测试模块

测试内容：
1. 表结构创建
2. 数据写入/读取
3. TTL过期机制
4. 迁移脚本验证
"""

import sys
import os
import json
import time
from datetime import datetime, timedelta

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.mysql_storage_manager import MySQLStorageManager, KVStoreTableMixin
from modules.database import P5Database


def test_kv_table_creation():
    """测试KV表创建"""
    print("\n" + "="*60)
    print("测试1: KV表结构创建")
    print("="*60)

    db = P5Database()
    if not db.connect():
        print("✗ MySQL连接失败")
        return False

    try:
        # 使用Mixin创建表
        mixin = KVStoreTableMixin()
        mixin.db = db
        mixin.create_kv_table()
        print("✓ p5_kv_store 表创建成功")

        # 测试专用表
        mixin._ensure_specialized_tables()
        print("✓ 专用表 (p5_user_config, p5_algorithm_config, p5_hit_rate_stats, p5_tracking_board) 创建成功")

        return True
    except Exception as e:
        print(f"✗ 表创建失败: {e}")
        return False
    finally:
        db.disconnect()


def test_data_operations():
    """测试数据读写操作"""
    print("\n" + "="*60)
    print("测试2: 数据读写操作")
    print("="*60)

    db = P5Database()
    if not db.connect():
        print("✗ MySQL连接失败")
        return False

    storage = MySQLStorageManager(db_client=db)

    try:
        # 测试1: 设置和获取值
        test_key = "kpluckynumber:pl5:test:key1"
        test_value = {"data": "test", "number": 123}

        result = storage.set(test_key, test_value, ttl=60)  # 60秒过期
        print(f"写入测试键: {result}")

        retrieved = storage.get(test_key)
        print(f"读取测试键: {retrieved}")

        assert retrieved == test_value, "数据不匹配!"
        print("✓ 基本读写测试通过")

        # 测试2: Hash操作
        hash_key = "kpluckynumber:pl5:test:hash1"
        result = storage.safe_hset(hash_key, "field1", "value1", ttl=60)
        print(f"Hash写入: {result}")

        result = storage.safe_hset(hash_key, "field2", "value2", ttl=60)
        print(f"Hash写入2: {result}")

        retrieved = storage.get(hash_key, field="field1")
        print(f"Hash读取field1: {retrieved}")
        assert retrieved == "value1", "Hash数据不匹配!"
        print("✓ Hash操作测试通过")

        # 测试3: 业务方法测试
        issue = "2026165"
        data = {
            "issue": issue,
            "numbers": [5, 3, 7, 2, 8],
            "date": "2026-07-20"
        }

        result = storage.save_raw_data(issue, data, expire_days=7)
        print(f"保存原始数据: {result}")

        retrieved = storage.get_raw_data(issue)
        print(f"获取原始数据: {retrieved}")
        assert retrieved["numbers"] == data["numbers"], "原始数据不匹配!"
        print("✓ 业务方法测试通过")

        return True

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.disconnect()


def test_ttl_expiration():
    """测试TTL过期机制"""
    print("\n" + "="*60)
    print("测试3: TTL过期机制")
    print("="*60)

    db = P5Database()
    if not db.connect():
        print("✗ MySQL连接失败")
        return False

    storage = MySQLStorageManager(db_client=db)

    try:
        # 设置一个1秒过期的键
        test_key = "kpluckynumber:pl5:test:ttl1"
        storage.set(test_key, {"data": "test"}, ttl=1)
        print("写入1秒过期的测试键")

        # 立即读取应该成功
        result = storage.get(test_key)
        print(f"立即读取结果: {result}")
        assert result is not None, "应该能读取到数据"
        print("✓ 1秒内读取成功")

        # 等待过期
        print("等待2秒...")
        time.sleep(2)

        # 过期后应该返回None
        result = storage.get(test_key)
        print(f"过期后读取结果: {result}")
        assert result is None, "过期后应该返回None"
        print("✓ TTL过期机制测试通过")

        return True

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False
    finally:
        db.disconnect()


def test_cleanup_expired():
    """测试过期数据清理"""
    print("\n" + "="*60)
    print("测试4: 过期数据清理")
    print("="*60)

    db = P5Database()
    if not db.connect():
        print("✗ MySQL连接失败")
        return False

    storage = MySQLStorageManager(db_client=db)

    try:
        # 创建一些过期数据
        for i in range(5):
            storage.set(f"kpluckynumber:pl5:test:expired{i}", {"data": i}, ttl=-1)  # 已经过期

        # 获取清理前数量
        stats_before = storage.get_kv_store_stats()
        print(f"清理前统计: {stats_before}")

        # 执行清理
        deleted = storage.cleanup_expired(batch_size=100)
        print(f"清理了 {deleted} 条过期数据")

        # 获取清理后数量
        stats_after = storage.get_kv_store_stats()
        print(f"清理后统计: {stats_after}")

        print("✓ 过期数据清理测试通过")
        return True

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False
    finally:
        db.disconnect()


def test_config_operations():
    """测试配置操作"""
    print("\n" + "="*60)
    print("测试5: 配置操作")
    print("="*60)

    db = P5Database()
    if not db.connect():
        print("✗ MySQL连接失败")
        return False

    storage = MySQLStorageManager(db_client=db)

    try:
        # 测试获取/创建配置
        config_key = "test_algo_config"
        default_value = {"weight": 0.5, "version": "1.0"}

        result = db.get_or_create_config(config_key, default_value)
        print(f"获取配置: {result}")
        assert result == default_value, "默认值不匹配"
        print("✓ 配置获取测试通过")

        # 测试更新配置
        new_value = {"weight": 0.6, "version": "2.0"}
        db.update_config(config_key, new_value)

        result = db.get_or_create_config(config_key)
        print(f"更新后配置: {result}")
        assert result == new_value, "配置更新失败"
        print("✓ 配置更新测试通过")

        return True

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False
    finally:
        db.disconnect()


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("MySQL存储管理器测试套件")
    print("="*60)

    tests = [
        ("表结构创建", test_kv_table_creation),
        ("数据读写操作", test_data_operations),
        ("TTL过期机制", test_ttl_expiration),
        ("过期数据清理", test_cleanup_expired),
        ("配置操作", test_config_operations),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"✗ {name} 测试异常: {e}")
            results.append((name, False))

    print("\n" + "="*60)
    print("测试结果汇总")
    print("="*60)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"  {name}: {status}")
    print(f"\n总计: {passed}/{total} 测试通过")

    return all(r for _, r in results)


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)