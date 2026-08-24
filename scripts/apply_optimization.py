# -*- coding: utf-8 -*-
"""数据库索引优化与冗余清理（通过Python直接执行）"""
import sys
sys.path.insert(0, r'd:\PythonProject\KPLuckyNumber')
from modules.database import P5Database

db = P5Database()
if not db.connect():
    print("数据库连接失败")
    sys.exit(1)

try:
    print("=" * 70)
    print("数据库索引优化与冗余清理")
    print("=" * 70)

    # 1. 删除冗余备份表
    print("\n【步骤1】删除冗余备份表")
    try:
        db.execute_with_reconnect("DROP TABLE IF EXISTS `p5_prediction_record_bak_20260719`")
        print("  ✓ p5_prediction_record_bak_20260719 已删除")
    except Exception as e:
        print(f"  ⚠ 删除备份表失败（可能已不存在）: {e}")

    # 2. 为 p5_history_data 添加 DESC 索引
    print("\n【步骤2】添加 p5_history_data DESC 索引")
    try:
        db.execute_with_reconnect("ALTER TABLE `p5_history_data` ADD INDEX `idx_issue_desc` (`issue` DESC) USING BTREE")
        print("  ✓ idx_issue_desc 索引已添加")
    except Exception as e:
        err_code = getattr(e, 'args', (None,))[0]
        if err_code == 1061:
            print("  ⚠ idx_issue_desc 索引已存在，跳过")
        else:
            print(f"  ⚠ 添加索引失败: {e}")

    # 3. 为 p5_artifact 添加复合索引
    print("\n【步骤3】添加 p5_artifact 复合索引")
    try:
        db.execute_with_reconnect("ALTER TABLE `p5_artifact` ADD INDEX `idx_type_issue` (`artifact_type`, `issue`) USING BTREE")
        print("  ✓ idx_type_issue 索引已添加")
    except Exception as e:
        err_code = getattr(e, 'args', (None,))[0]
        if err_code == 1061:
            print("  ⚠ idx_type_issue 索引已存在，跳过")
        else:
            print(f"  ⚠ 添加索引失败: {e}")

    # 4. 为 p5_prediction_record 添加复合索引
    print("\n【步骤4】添加 p5_prediction_record 复合索引")
    try:
        db.execute_with_reconnect("ALTER TABLE `p5_prediction_record` ADD INDEX `idx_status_issue` (`verification_status`, `target_issue`) USING BTREE")
        print("  ✓ idx_status_issue 索引已添加")
    except Exception as e:
        err_code = getattr(e, 'args', (None,))[0]
        if err_code == 1061:
            print("  ⚠ idx_status_issue 索引已存在，跳过")
        else:
            print(f"  ⚠ 添加索引失败: {e}")

    # 5. 优化五位置走势表索引（合并单列索引为联合索引）
    print("\n【步骤5】优化五位置走势表索引")
    for pos, col in [('wan', 'wan_number'), ('qian', 'qian_number'), ('bai', 'bai_number'), ('shi', 'shi_number'), ('ge', 'ge_number')]:
        table = f'p5_{pos}_trend_data'
        old_idx = f'idx_{pos}_number'
        new_idx = f'idx_issue_{pos}'
        try:
            # 先尝试删除旧索引
            db.execute_with_reconnect(f"ALTER TABLE `{table}` DROP INDEX `{old_idx}`")
            # 再添加联合索引
            db.execute_with_reconnect(f"ALTER TABLE `{table}` ADD INDEX `{new_idx}` (`issue`, `{col}`) USING BTREE")
            print(f"  ✓ {table}: {old_idx} → {new_idx}")
        except Exception as e:
            err_code = getattr(e, 'args', (None,))[0]
            if err_code == 1091:
                print(f"  ⚠ {table}: 旧索引 {old_idx} 不存在，尝试添加新索引")
                try:
                    db.execute_with_reconnect(f"ALTER TABLE `{table}` ADD INDEX `{new_idx}` (`issue`, `{col}`) USING BTREE")
                    print(f"  ✓ {table}: 已添加 {new_idx}")
                except Exception as e2:
                    err2 = getattr(e2, 'args', (None,))[0]
                    if err2 == 1061:
                        print(f"  ⚠ {table}: {new_idx} 已存在，跳过")
                    else:
                        print(f"  ⚠ {table}: 添加索引失败: {e2}")
            elif err_code == 1061:
                print(f"  ⚠ {table}: {new_idx} 已存在，跳过")
            else:
                print(f"  ⚠ {table}: 优化索引失败: {e}")

    # 6. 添加 CHECK 约束（MySQL 8.0.16+）
    print("\n【步骤6】添加 CHECK 约束")
    checks = [
        ("p5_history_data", "chk_hezhi_range", "CHECK (`hezhi` >= 0 AND `hezhi` <= 45)"),
        ("p5_history_data", "chk_span_range", "CHECK (`span` >= 0 AND `span` <= 9)"),
        ("p5_prediction_record", "chk_accuracy_rate", "CHECK (`accuracy_rate` >= 0 AND `accuracy_rate` <= 100)"),
    ]
    for table, constraint, check_expr in checks:
        try:
            db.execute_with_reconnect(f"ALTER TABLE `{table}` ADD CONSTRAINT `{constraint}` {check_expr}")
            print(f"  ✓ {table}.{constraint} 已添加")
        except Exception as e:
            err_code = getattr(e, 'args', (None,))[0]
            if err_code in (1061, 3806):  # 已存在 或 MySQL 8.0.16+ 的 CHECK 约束
                print(f"  ⚠ {table}.{constraint} 已存在，跳过")
            else:
                print(f"  ⚠ {table}.{constraint} 添加失败（可能MySQL版本不支持）: {e}")

    # 7. 更新统计信息
    print("\n【步骤7】更新表统计信息")
    tables = ['p5_history_data', 'p5_ai_report', 'p5_prediction_record',
              'p5_verification_detail', 'p5_wan_trend_data', 'p5_qian_trend_data',
              'p5_bai_trend_data', 'p5_shi_trend_data', 'p5_ge_trend_data',
              'p5_spjzs_data', 'p5_hzzst_data', 'p5_artifact']
    for table in tables:
        try:
            db.execute_with_reconnect(f"ANALYZE TABLE `{table}`")
            print(f"  ✓ {table} 统计信息已更新")
        except Exception as e:
            print(f"  ⚠ {table} 统计更新失败: {e}")

    print()
    print("=" * 70)
    print("优化完成！")
    print("=" * 70)

finally:
    db.disconnect()
