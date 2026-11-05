# -*- coding: utf-8 -*-
"""
p5_kv_store.field=NULL 唯一键失效 —— A 方案迁移脚本（草案，默认只读）

背景 / 根因
-----------
表 p5_kv_store 的 `field` 列可空，唯一索引 uk_key_field(`key`, field) 在 MySQL 中
无法约束 NULL（多个 NULL 视为互不相等）。因此 set() 里的
`INSERT ... ON DUPLICATE KEY UPDATE` 永远不会命中，每次写入都会新增一条物理行；
get() 旧实现无排序直接 fetchone()，可能读到最早插入的已过期行。

本脚本执行 A 方案：
    1) 回填 field：UPDATE ... SET field='' WHERE field IS NULL
    2) 去重：按 (`key`, COALESCE(field,'')) 分组，仅保留每组 MAX(id)（最新写入）
    3) 改列：ALTER TABLE ... MODIFY field VARCHAR(100) NOT NULL DEFAULT ''
此后 ON DUPLICATE KEY UPDATE 才能对无 field 的键真正生效。

风险等级
---------
    --dry-run（默认）  低：仅 SELECT，不写库
    --backup           低：CREATE TABLE ... AS SELECT（只增不改）
    --apply --yes      高：包含 UPDATE / DELETE 去重 / ALTER（DDL 隐式提交）

安全约束
---------
    - 必须显式传 --apply 且同时传 --yes，否则一律按 dry-run 处理并拒绝写库
    - --apply 前会自动创建带时间戳的备份表；备份失败则中止
    - 脚本不包含任何自动定时执行逻辑；本次交付仅做语法检查，未连接数据库运行

用法
----
    python scripts/migrate_kv_field_null_20261105.py                    # 只读盘点
    python scripts/migrate_kv_field_null_20261105.py --backup           # 仅备份
    python scripts/migrate_kv_field_null_20261105.py --apply --yes      # 正式迁移（高危）
"""

import argparse
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymysql  # noqa: E402

from config import DB_CONFIG  # noqa: E402

TABLE = 'p5_kv_store'
DUP_SUBQUERY = """
    SELECT `key`, COALESCE(field, '') AS logical_field, MAX(id) AS keep_id
    FROM {table}
    GROUP BY `key`, COALESCE(field, '')
""".format(table=TABLE)


def _connect():
    """按项目配置连接 MySQL（密码优先取环境变量 / config/.env）。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, 'config', '.env'))
    except Exception:
        pass  # 未安装 python-dotenv 时回退到 config 自带密码
    return pymysql.connect(
        host=DB_CONFIG.get('host'),
        port=int(DB_CONFIG.get('port', 3306)),
        user=DB_CONFIG.get('user'),
        password=os.getenv('DB_PASSWORD', DB_CONFIG.get('password', '')),
        database=DB_CONFIG.get('database'),
        charset=DB_CONFIG.get('charset', 'utf8mb4'),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        autocommit=False,
    )


def _count(cur, sql, params=None):
    cur.execute(sql, params or ())
    row = cur.fetchone() or {}
    return int(row.get('cnt') or 0)


def collect_plan(cur):
    """只读盘点：返回将回填 / 将删除的行数及样本。"""
    cur.execute('SELECT VERSION() AS v')
    version = (cur.fetchone() or {}).get('v', 'unknown')

    will_backfill = _count(
        cur, f'SELECT COUNT(*) AS cnt FROM {TABLE} WHERE field IS NULL')
    will_delete = _count(
        cur,
        f"""SELECT COUNT(*) AS cnt FROM {TABLE} k
            JOIN ({DUP_SUBQUERY}) t
              ON k.`key` = t.`key`
             AND COALESCE(k.field, '') = t.logical_field
            WHERE k.id < t.keep_id""")

    cur.execute(
        f"""SELECT `key`, COALESCE(field, '') AS logical_field, COUNT(*) AS dup
            FROM {TABLE}
            GROUP BY `key`, COALESCE(field, '')
            HAVING dup > 1
            ORDER BY dup DESC
            LIMIT 10""")
    samples = cur.fetchall()

    return {
        'version': version,
        'will_backfill': will_backfill,
        'will_delete': will_delete,
        'dup_samples': samples,
    }


def create_backup(conn, cur):
    """创建带时间戳的备份表；失败抛异常以中止后续写操作。"""
    suffix = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_table = f'{TABLE}_bak_{suffix}'
    cur.execute(f'CREATE TABLE {backup_table} AS SELECT * FROM {TABLE}')
    conn.commit()
    logger_print(f'[backup] 已创建备份表 {backup_table}')
    return backup_table


def apply_migration(conn, cur):
    """执行 A 方案（回填 → 去重 → ALTER）。调用方必须先完成备份与二次确认。"""
    logger_print('[apply] 1/3 回填 field=NULL → \'\'')
    cur.execute(f"UPDATE {TABLE} SET field = '' WHERE field IS NULL")
    backfilled = cur.rowcount

    logger_print('[apply] 2/3 去重（每组 `key`+logical_field 仅保留 MAX(id)）')
    cur.execute(
        f"""DELETE k FROM {TABLE} k
            JOIN ({DUP_SUBQUERY}) t
              ON k.`key` = t.`key`
             AND COALESCE(k.field, '') = t.logical_field
            WHERE k.id < t.keep_id""")
    deleted = cur.rowcount
    conn.commit()

    logger_print('[apply] 3/3 收紧列定义 field NOT NULL DEFAULT \'\'')
    # 注意：ALTER 在 MySQL 中会隐式提交，无法回滚；必须在上一步提交成功后再执行
    cur.execute(
        f"ALTER TABLE {TABLE} MODIFY field VARCHAR(100) NOT NULL DEFAULT ''")
    conn.commit()

    logger_print(f'[apply] 完成：回填 {backfilled} 行，去重删除 {deleted} 行')
    return backfilled, deleted


def verify(cur):
    """迁移后只读校验：不应再存在 NULL field 或重复逻辑键。"""
    null_field = _count(cur, f'SELECT COUNT(*) AS cnt FROM {TABLE} WHERE field IS NULL')
    dup_groups = _count(
        cur,
        f"""SELECT COUNT(*) AS cnt FROM (
                SELECT `key`, COALESCE(field, '') AS f
                FROM {TABLE}
                GROUP BY `key`, COALESCE(field, '')
                HAVING COUNT(*) > 1
            ) t""")
    logger_print(f'[verify] field IS NULL 行数={null_field}，重复逻辑键组数={dup_groups}')
    return null_field == 0 and dup_groups == 0


def logger_print(msg):
    print(msg, flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='p5_kv_store field=NULL 唯一键失效迁移（默认 dry-run，不写库）')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅统计将回填/将删除的行数（默认行为）')
    parser.add_argument('--backup', action='store_true',
                        help='仅创建 p5_kv_store 的带时间戳备份表')
    parser.add_argument('--apply', action='store_true',
                        help='执行回填 + 去重 + ALTER（高危，必须同时传 --yes）')
    parser.add_argument('--yes', action='store_true',
                        help='二次确认：允许 --apply 真正写库')
    args = parser.parse_args(argv)

    if args.apply and not args.yes:
        logger_print('[abort] --apply 必须同时传 --yes 进行二次确认；本次未写库')
        return 2

    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = collect_plan(cur)
            logger_print(f'[plan] MySQL={plan["version"]} 表={TABLE}')
            logger_print(f'[plan] 将回填 field=NULL 行数={plan["will_backfill"]}')
            logger_print(f'[plan] 将去重删除行数={plan["will_delete"]}')
            if plan['dup_samples']:
                logger_print(f'[plan] 重复样本(最多10组)={plan["dup_samples"]}')

            if not args.apply and not args.backup:
                logger_print('[dry-run] 未传 --backup/--apply，仅输出计划，未做任何写入')
                return 0

            backup_table = create_backup(conn, cur)

            if not args.apply:
                logger_print('[backup] 已按要求仅备份，未执行迁移')
                return 0

            backfilled, deleted = apply_migration(conn, cur)
            ok = verify(cur)
            logger_print(f'[apply] 回填 {backfilled} 行 / 去重 {deleted} 行 / '
                         f'备份表={backup_table} / 校验={"通过" if ok else "未通过"}')
            return 0 if ok else 3
    finally:
        conn.rollback()
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
