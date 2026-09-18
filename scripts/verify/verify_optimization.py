# -*- coding: utf-8 -*-
"""数据库优化验证"""
import sys
sys.path.insert(0, r'd:\PythonProject\KPLuckyNumber')
from modules.database import P5Database

db = P5Database()
if not db.connect():
    print("数据库连接失败")
    sys.exit(1)

try:
    # 1. 获取数据库统计
    print("=" * 70)
    print("数据库统计")
    print("=" * 70)
    stats = db.get_database_stats()
    if 'error' in stats:
        print(f"获取统计信息失败: {stats['error']}")
        sys.exit(1)
    print(f"总表数: {stats['total_tables']}")
    print(f"总记录数: {stats['total_records']}")
    print(f"总大小: {stats['total_size_kb']} KB")
    print()
    for t in stats['table_stats']:
        print(f"  {t['table_name']:<40} {t['record_count']:>6}行  {t['total_kb']:>8}KB")

    # 2. 数据一致性检查
    print()
    print("=" * 70)
    print("数据一致性检查")
    print("=" * 70)
    consistency = db.check_data_consistency()
    if 'error' in consistency:
        print(f"一致性检查失败: {consistency['error']}")
        sys.exit(1)
    print(f"一致性: {'✓ 正常' if consistency['consistent'] else '✗ 存在孤立记录'}")
    print(f"孤立记录数: {consistency['orphan_records']}")
    h = consistency['history']
    print(f"  p5_history_data: [{h['min_issue']}, {h['max_issue']}] count={h['count']}")
    for k, v in consistency['trend_tables'].items():
        print(f"  {k}: [{v['min_issue']}, {v['max_issue']}] count={v['count']}")

    # 3. 清理孤立记录
    if not consistency['consistent'] and consistency['orphan_records'] > 0:
        print()
        print("=" * 70)
        print(f"清理孤立记录... (共 {consistency['orphan_records']} 条)")
        cleaned = db.clean_orphan_trend_records()
        print(f"已清理 {cleaned} 条孤立记录")
        # 重新检查
        consistency2 = db.check_data_consistency()
        print(f"清理后一致性: {'✓ 正常' if consistency2['consistent'] else '✗ 仍有问题'}")
        print(f"清理后孤立记录数: {consistency2['orphan_records']}")

    print()
    print("=" * 70)
    print("验证完成")
finally:
    db.disconnect()
