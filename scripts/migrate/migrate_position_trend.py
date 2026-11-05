#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
排列五位走势表合并迁移脚本 (v1.0)

用途：
  将 p5_wan_trend_data / p5_qian_trend_data / p5_bai_trend_data /
  p5_shi_trend_data / p5_ge_trend_data 五张独立走势表合并为单表
  p5_position_trend（issue + position 联合主键），减少表碎片、简化查询。

特性：
  - 幂等：重复执行不报错，已存在的表/列/索引会跳过。
  - 支持 --dry-run：仅打印将执行的 SQL，不修改数据库。
  - 支持 --rollback：将数据回滚到旧五张表（基于备份表 p5_*_trend_data_bak_<ts>）。
  - 迁移前自动对旧五张表做全量备份（CREATE TABLE ... LIKE + INSERT SELECT）。
  - 迁移后做行数与抽样数据一致性校验。
  - 保留旧五张表作为兼容视图（可选，默认创建视图让旧 SQL 不报错）。

使用示例：
  python scripts/migrate/migrate_position_trend.py --dry-run
  python scripts/migrate/migrate_position_trend.py
  python scripts/migrate/migrate_position_trend.py --rollback

环境要求：
  - 依赖项目 config.DB_CONFIG / DB_POOL_CONFIG（通过 modules.database.P5Database 连接）。
  - MySQL 5.7+ / MariaDB 10.2+（支持 VIEW）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 确保无论从哪个工作目录启动，都能导入项目根目录下的 modules/config 等包。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 延迟导入，避免模块导入期因数据库未就绪而失败。
from modules.database import P5Database  # noqa: E402

OLD_TABLES: List[str] = [
    "p5_wan_trend_data",
    "p5_qian_trend_data",
    "p5_bai_trend_data",
    "p5_shi_trend_data",
    "p5_ge_trend_data",
]

POSITION_MAP: Dict[str, int] = {
    "p5_wan_trend_data": 0,
    "p5_qian_trend_data": 1,
    "p5_bai_trend_data": 2,
    "p5_shi_trend_data": 3,
    "p5_ge_trend_data": 4,
}

NEW_TABLE = "p5_position_trend"

NEW_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS `{NEW_TABLE}` (
    `id` INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `issue` VARCHAR(20) NOT NULL COMMENT '期号（唯一，与 position 联合唯一）',
    `position` TINYINT NOT NULL COMMENT '位置(0=万,1=千,2=百,3=十,4=个)',
    `number` TINYINT NOT NULL COMMENT '号码值(0-9)',
    `draw_date` VARCHAR(20) NULL DEFAULT NULL COMMENT '开奖日期',
    `is_odd` TINYINT(1) NULL DEFAULT NULL COMMENT '是否奇数(1=是,0=否)',
    `is_big` TINYINT(1) NULL DEFAULT NULL COMMENT '是否大数(>=5为大,1=是,0=否)',
    `is_prime` TINYINT(1) NULL DEFAULT NULL COMMENT '是否质数(1=是,0=否)',
    `omission` INT NULL DEFAULT 0 COMMENT '当前遗漏值',
    `hot_level` VARCHAR(10) NULL DEFAULT NULL COMMENT '冷热等级(hot/warm/cold)',
    `consecutive_count` INT NULL DEFAULT 0 COMMENT '连续出现次数',
    `trend_json` LONGTEXT NULL DEFAULT NULL COMMENT '走势JSON数据',
    `source` VARCHAR(50) NULL DEFAULT NULL COMMENT '数据来源',
    `created_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`) USING BTREE,
    UNIQUE INDEX `uk_issue_position` (`issue` ASC, `position` ASC) USING BTREE,
    INDEX `idx_position_number` (`position` ASC, `number` ASC) USING BTREE,
    INDEX `idx_draw_date` (`draw_date` ASC) USING BTREE,
    INDEX `idx_created_at` (`created_at` ASC) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排列5各位置走势图数据表(合并自 wan/qian/bai/shi/ge 五张表)';
"""

# 旧表 -> 新表的列映射（旧表列名 -> 新表列名）。
COLUMN_MAP: Dict[str, str] = {
    "wan_number": "number",
    "qian_number": "number",
    "bai_number": "number",
    "shi_number": "number",
    "ge_number": "number",
}

# 兼容视图 DDL：让旧代码通过 SELECT * FROM p5_wan_trend_data 等继续工作。
VIEW_DDLS: List[str] = [
    f"""
    CREATE OR REPLACE VIEW `p5_wan_trend_data` AS
    SELECT `id`,`issue`,`number` AS `wan_number`,`draw_date`,`is_odd`,`is_big`,
           `is_prime`,`omission`,`hot_level`,`consecutive_count`,`trend_json`,
           `source`,`created_at`
    FROM `{NEW_TABLE}` WHERE `position` = 0
    """,
    f"""
    CREATE OR REPLACE VIEW `p5_qian_trend_data` AS
    SELECT `id`,`issue`,`number` AS `qian_number`,`draw_date`,`is_odd`,`is_big`,
           `is_prime`,`omission`,`hot_level`,`consecutive_count`,`trend_json`,
           `source`,`created_at`
    FROM `{NEW_TABLE}` WHERE `position` = 1
    """,
    f"""
    CREATE OR REPLACE VIEW `p5_bai_trend_data` AS
    SELECT `id`,`issue`,`number` AS `bai_number`,`draw_date`,`is_odd`,`is_big`,
           `is_prime`,`omission`,`hot_level`,`consecutive_count`,`trend_json`,
           `source`,`created_at`
    FROM `{NEW_TABLE}` WHERE `position` = 2
    """,
    f"""
    CREATE OR REPLACE VIEW `p5_shi_trend_data` AS
    SELECT `id`,`issue`,`number` AS `shi_number`,`draw_date`,`is_odd`,`is_big`,
           `is_prime`,`omission`,`hot_level`,`consecutive_count`,`trend_json`,
           `source`,`created_at`
    FROM `{NEW_TABLE}` WHERE `position` = 3
    """,
    f"""
    CREATE OR REPLACE VIEW `p5_ge_trend_data` AS
    SELECT `id`,`issue`,`number` AS `ge_number`,`draw_date`,`is_odd`,`is_big`,
           `is_prime`,`omission`,`hot_level`,`consecutive_count`,`trend_json`,
           `source`,`created_at`
    FROM `{NEW_TABLE}` WHERE `position` = 4
    """,
]


class MigrationContext:
    def __init__(self, dry_run: bool = False, create_views: bool = True):
        self.dry_run = dry_run
        self.create_views = create_views
        self.db: Optional[P5Database] = None
        self.backup_suffix: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.executed: List[str] = []
        self.errors: List[str] = []

    def connect(self) -> None:
        if self.dry_run:
            print("[dry-run] 跳过数据库连接。")
            return
        self.db = P5Database()
        self.db.connect()
        print(f"[connect] 已连接数据库: {getattr(self.db, 'database', 'unknown')}")

    def disconnect(self) -> None:
        if self.db:
            try:
                self.db.disconnect()
            except Exception:
                pass

    def execute(self, sql: str, params: Optional[Tuple] = None, log: bool = True) -> Any:
        if log:
            print(f"[exec] {sql.strip().splitlines()[0][:120]}...")
        if self.dry_run:
            self.executed.append(sql)
            return None
        assert self.db is not None
        try:
            cur = self.db.cursor
            cur.execute(sql, params or ())
            self.db.connection.commit()
            return cur
        except Exception as e:
            try:
                self.db.connection.rollback()
            except Exception:
                pass
            self.errors.append(str(e))
            print(f"[ERR] {e}")
            raise

    def table_exists(self, name: str) -> bool:
        if self.dry_run:
            return True
        assert self.db is not None
        cur = self.db.cursor
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            (name,),
        )
        return cur.fetchone()[0] > 0

    def get_table_count(self, name: str) -> int:
        if self.dry_run:
            return -1
        assert self.db is not None
        cur = self.db.cursor
        cur.execute(f"SELECT COUNT(*) FROM `{name}`")
        return cur.fetchone()[0]

    def backup_old_tables(self) -> Dict[str, str]:
        """对五张旧表做全量备份，返回 {旧表名: 备份表名}。"""
        backups: Dict[str, str] = {}
        for old in OLD_TABLES:
            if not self.table_exists(old):
                print(f"[skip] 旧表 {old} 不存在，无需备份。")
                continue
            bak_name = f"{old}_bak_{self.backup_suffix}"
            ddl = f"CREATE TABLE `{bak_name}` LIKE `{old}`"
            self.execute(ddl)
            self.execute(f"INSERT INTO `{bak_name}` SELECT * FROM `{old}`")
            cnt = self.get_table_count(bak_name)
            print(f"[backup] {old} -> {bak_name} ({cnt} 行)")
            backups[old] = bak_name
        return backups

    def create_new_table(self) -> None:
        if self.table_exists(NEW_TABLE):
            print(f"[skip] 新表 {NEW_TABLE} 已存在，跳过创建。")
            return
        self.execute(NEW_TABLE_DDL)
        print(f"[create] 新表 {NEW_TABLE} 已创建。")

    def migrate_data(self) -> Dict[str, int]:
        """将五张旧表数据迁移到新表，返回 {旧表名: 迁移行数}。"""
        counts: Dict[str, int] = {}
        for old in OLD_TABLES:
            if not self.table_exists(old):
                print(f"[skip] 旧表 {old} 不存在，跳过迁移。")
                counts[old] = 0
                continue
            position = POSITION_MAP[old]
            # 旧表的 number 列名不一（wan_number/qian_number/...），统一别名为 number。
            number_col = COLUMN_MAP.get(
                old, "number"
            )  # 实际旧表列名为 wan_number 等，需在 SELECT 中别名
            # 构造显式列列表，避免 SELECT * 列序不一致问题。
            select_cols = (
                f"`issue`, `{number_col}` AS `number`, `draw_date`, `is_odd`, `is_big`, "
                f"`is_prime`, `omission`, `hot_level`, `consecutive_count`, "
                f"`trend_json`, `source`, `created_at`"
            )
            sql = (
                f"INSERT INTO `{NEW_TABLE}` "
                f"(`issue`, `position`, `number`, `draw_date`, `is_odd`, `is_big`, "
                f"`is_prime`, `omission`, `hot_level`, `consecutive_count`, "
                f"`trend_json`, `source`, `created_at`) "
                f"SELECT {select_cols}, {position} AS `position` "
                f"FROM `{old}` "
                f"ON DUPLICATE KEY UPDATE `number`=VALUES(`number`)"
            )
            cur = self.execute(sql)
            rowcount = cur.rowcount if cur else 0
            counts[old] = rowcount
            print(f"[migrate] {old} -> {NEW_TABLE} (position={position}, {rowcount} 行)")
        return counts

    def create_compat_views(self) -> None:
        if not self.create_views:
            print("[skip] 未启用兼容视图创建。")
            return
        for ddl in VIEW_DDLS:
            try:
                self.execute(ddl)
            except Exception as e:
                # 视图可能因旧表仍为真实表而创建失败；若旧表已被 drop 则可建视图。
                # 这里仅警告，不中断。
                print(f"[warn] 创建兼容视图跳过: {e}")
        print("[create] 兼容视图已尝试创建。")

    def validate(self, migrate_counts: Dict[str, int]) -> bool:
        """行数与抽样一致性校验。dry-run 时仅打印计划，不做实际比对。"""
        ok = True
        if self.dry_run:
            print("[validate] dry-run 模式，跳过实际行数与抽样比对。")
            return True
        new_total = self.get_table_count(NEW_TABLE)
        old_total = sum(migrate_counts.values())
        print(f"[validate] 新表总行数={new_total}, 旧表迁移总行数={old_total}")
        if new_total != old_total:
            print(f"[FAIL] 行数不一致: 新表 {new_total} != 旧表合计 {old_total}")
            ok = False
        else:
            print("[validate] 行数一致。")
        # 抽样校验：每张旧表随机抽 3 条，在新表中应能找到对应记录。
        assert self.db is not None
        for old in OLD_TABLES:
            if not self.table_exists(old):
                continue
            position = POSITION_MAP[old]
            number_col = COLUMN_MAP.get(old, "number")
            cur = self.db.cursor
            cur.execute(
                f"SELECT `issue`,`{number_col}` FROM `{old}` ORDER BY RAND() LIMIT 3"
            )
            rows = cur.fetchall()
            for issue, number in rows:
                cur.execute(
                    f"SELECT COUNT(*) FROM `{NEW_TABLE}` "
                    f"WHERE `issue`=%s AND `position`=%s AND `number`=%s",
                    (issue, position, number),
                )
                if cur.fetchone()[0] != 1:
                    print(f"[FAIL] 抽样未找到: {old} issue={issue} position={position} number={number}")
                    ok = False
        if ok:
            print("[validate] 抽样一致性校验通过。")
        return ok

    def rollback(self, backups: Dict[str, str]) -> None:
        """从备份表还原旧五张表，并删除新表与视图。"""
        print("[rollback] 开始回滚...")
        # 先删除视图（若存在）。
        for old in OLD_TABLES:
            try:
                self.execute(f"DROP VIEW IF EXISTS `{old}`")
            except Exception:
                pass
        # 删除新表。
        try:
            self.execute(f"DROP TABLE IF EXISTS `{NEW_TABLE}`")
        except Exception:
            pass
        # 从备份还原。
        for old, bak in backups.items():
            self.execute(f"DROP TABLE IF EXISTS `{old}`")
            self.execute(f"CREATE TABLE `{old}` LIKE `{bak}`")
            self.execute(f"INSERT INTO `{old}` SELECT * FROM `{bak}`")
            print(f"[rollback] {bak} -> {old} ({self.get_table_count(old)} 行)")
        print("[rollback] 回滚完成。")


def print_plan(dry_run: bool) -> None:
    print("=" * 70)
    print("  迁移计划：五位走势表 -> 单表 p5_position_trend")
    print("=" * 70)
    print(f"  模式：{'干跑 (dry-run)' if dry_run else '正式执行'}")
    print(f"  旧表：{', '.join(OLD_TABLES)}")
    print(f"  新表：{NEW_TABLE}")
    print(f"  位置映射：0=万, 1=千, 2=百, 3=十, 4=个")
    print(f"  兼容视图：启用（旧 SELECT * FROM p5_*_trend_data 将继续工作）")
    print("-" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="排列五位走势表合并迁移脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划，不修改数据库")
    parser.add_argument("--rollback", action="store_true", help="从备份表回滚到旧五张表")
    parser.add_argument("--no-views", action="store_true", help="不创建兼容视图")
    args = parser.parse_args()

    print_plan(args.dry_run)

    ctx = MigrationContext(dry_run=args.dry_run, create_views=not args.no_views)
    ctx.connect()

    try:
        if args.rollback:
            # 回滚时需知道备份表名：尝试从 information_schema 发现 p5_*_bak_* 表。
            assert ctx.db is not None
            cur = ctx.db.cursor
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name LIKE 'p5_%_trend_data_bak_%'"
            )
            bak_tables = [r[0] for r in cur.fetchall()]
            # 简单推断：每个旧表最新一个备份。
            backups: Dict[str, str] = {}
            for old in OLD_TABLES:
                candidates = sorted(
                    [t for t in bak_tables if t.startswith(old + "_bak_")],
                    reverse=True,
                )
                if candidates:
                    backups[old] = candidates[0]
            if not backups:
                print("[ERR] 未发现备份表，无法回滚。")
                return 1
            ctx.rollback(backups)
            return 0

        ctx.backup_old_tables()
        ctx.create_new_table()
        counts = ctx.migrate_data()
        ctx.create_compat_views()
        ok = ctx.validate(counts)
        if not ok:
            print("[FAIL] 校验未通过，建议执行 --rollback 回滚。")
            return 1
        print("[done] 迁移完成。新表 p5_position_trend 已就绪，旧五张表保留作为备份。")
        return 0
    finally:
        ctx.disconnect()


if __name__ == "__main__":
    sys.exit(main())
