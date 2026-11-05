# -*- coding: utf-8 -*-
"""
database_utils.py - 数据库通用工具函数

职责：
    提供数据库操作的通用方法，减少重复代码，提升可维护性。
    所有位置走势数据操作都通过本模块的泛型方法实现。

使用方式：
    from modules.database_utils import (
        insert_position_trend_data,
        get_position_trend_data,
        get_position_stats,
    )
"""

import logging
import json
from typing import Dict, List, Any, Optional, Tuple, Set

# 安全标识符白名单（防止 SQL 注入）
ALLOWED_TABLES: Set[str] = {
    'p5_history_data', 'p5_trend_data', 'p5_ai_report', 'p5_prediction_record',
    'p5_verification_detail', 'p5_performance_stats', 'p5_wan_trend_data',
    'p5_qian_trend_data', 'p5_bai_trend_data', 'p5_shi_trend_data', 'p5_ge_trend_data',
    'p5_spjzs_data', 'p5_hzzst_data', 'p5_bayesian_result', 'p5_sum_end_trend_data',
    'p5_back_three_trend_data', 'p5_expert_recommendation', 'p5_weight_history',
    'p5_learning_history', 'p5_artifact', 'p5_evolution_version',
}

ALLOWED_COLUMNS: Set[str] = {
    'wan_number', 'qian_number', 'bai_number', 'shi_number', 'ge_number',
    # 常见的列名（若作为变量出现）
    'issue', 'draw_date', 'hot_level', 'is_odd', 'is_big', 'is_prime',
    'omission', 'source', 'trend_json',
}

ALLOWED_DATABASES: Set[str] = {
    'lucky_number',  # 根据 config.py 中的 DB_NAME 默认值
}

def assert_safe_table(name: str) -> None:
    """校验表名是否在安全白名单内，否则抛出 ValueError。"""
    if name not in ALLOWED_TABLES:
        raise ValueError(f"不安全的表名: {name}")

def assert_safe_column(name: str) -> None:
    """校验列名是否在安全白名单内，否则抛出 ValueError。"""
    if name not in ALLOWED_COLUMNS:
        raise ValueError(f"不安全的列名: {name}")

def assert_safe_database(name: str) -> None:
    """校验库名是否在安全白名单内，否则抛出 ValueError。"""
    if name not in ALLOWED_DATABASES:
        raise ValueError(f"不安全的库名: {name}")

logger = logging.getLogger(__name__)


def _get_position_info(position: str) -> Tuple[str, str]:
    """
    获取位置对应的表名和字段名

    Args:
        position: 位置标识 (wan/qian/bai/shi/ge)

    Returns:
        (table_name, number_field)

    安全性：
        表名和字段名均通过白名单严格校验，杜绝SQL注入风险。
        即使position被篡改，也会因不在白名单内而抛出 ValueError。
    """
    position = position.lower()
    table_map = {
        'wan': ('p5_wan_trend_data', 'wan_number'),
        'qian': ('p5_qian_trend_data', 'qian_number'),
        'bai': ('p5_bai_trend_data', 'bai_number'),
        'shi': ('p5_shi_trend_data', 'shi_number'),
        'ge': ('p5_ge_trend_data', 'ge_number'),
    }
    # 严格白名单校验，防止SQL注入
    if position not in table_map:
        raise ValueError(f"不支持的位置: {position}，支持: {list(table_map.keys())}")
    table, field = table_map[position]
    # 二次校验：确保返回值本身合法（仅包含字母、数字、下划线）
    import re
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table):
        raise ValueError(f"非法表名: {table}")
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', field):
        raise ValueError(f"非法字段名: {field}")
    return table, field


def insert_position_trend_data(cursor, position: str, data: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    通用位置走势数据插入

    Args:
        cursor: 数据库游标（通过 db.cursor 获取，需确保连接有效）
        position: 位置标识
        data: 数据列表

    Returns:
        (成功条数, 跳过条数)
    """
    if not data:
        return 0, 0

    table, number_col = _get_position_info(position)

    fields = ['issue', number_col, 'draw_date', 'is_odd', 'is_big', 'is_prime',
              'omission', 'hot_level', 'consecutive_count', 'trend_json', 'source']
    placeholders = ['%s'] * len(fields)

    sql = f'''
        INSERT INTO {table}
        ({', '.join(fields)})
        VALUES ({', '.join(placeholders)})
        ON DUPLICATE KEY UPDATE
            {number_col} = VALUES({number_col}),
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

    success_count = 0
    skip_count = 0
    insert_errors = []

    try:
        for item in data:
            issue = str(item.get('issue', ''))
            if not issue:
                skip_count += 1
                continue

            number = item.get(number_col, 0)
            if not (0 <= number <= 9):
                skip_count += 1
                continue

            try:
                cursor.execute(sql, (
                    issue,
                    number,
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
            except Exception as e:
                error_msg = str(e).lower()
                error_code = e.args[0] if e.args else 0
                # 检测连接中断错误
                is_connection_error = (
                    'invalidconnectionerror' in error_msg or
                    'MySQL server has gone away' in error_msg or
                    'lost connection' in error_msg or
                    error_code in (2006, 2013, 0)
                )
                if is_connection_error:
                    logger.error(f'插入{position}位走势数据失败: issue={issue}, 连接异常 (error_code={error_code})')
                else:
                    logger.error(f'插入{position}位走势数据失败: issue={issue}, {type(e).__name__}: {e}')
                insert_errors.append(issue)
                skip_count += 1
                continue

        if insert_errors:
            logger.warning(f'{position}位走势数据部分插入失败, 失败期号示例: {insert_errors[:5]}')

        logger.info(f'{position}位走势数据插入完成: 成功{success_count}条, 跳过{skip_count}条')
        # 显式提交，确保数据持久化（调用方使用事务时此处提交会被事务统一提交覆盖）
        if hasattr(cursor, 'connection') and cursor.connection:
            cursor.connection.commit()
        return success_count, skip_count
    except Exception as e:
        logger.error(f'插入{position}位走势数据失败: {type(e).__name__}: {e}')
        return 0, len(data)


def get_position_trend_data(cursor, position: str, limit: int = 1000) -> List[Dict[str, Any]]:
    """通用位置走势数据查询"""
    table, _ = _get_position_info(position)
    try:
        sql = f'SELECT * FROM {table} ORDER BY issue DESC LIMIT %s'
        cursor.execute(sql, (limit,))
        return cursor.fetchall()
    except Exception as e:
        logger.error(f'获取{position}位走势数据失败: {e}')
        return []


def get_position_trend_count(cursor, position: str) -> int:
    """获取位置走势数据总条数"""
    table, _ = _get_position_info(position)
    assert_safe_table(table)
    try:
        cursor.execute(f'SELECT COUNT(*) as count FROM {table}')
        result = cursor.fetchone()
        return result.get('count', 0) if result else 0
    except Exception as e:
        logger.error(f'获取{position}位走势数据总数失败: {e}')
        return 0


def get_position_trend_by_issue(cursor, position: str, issue: str) -> Optional[Dict[str, Any]]:
    """根据期号获取位置走势数据"""
    table, _ = _get_position_info(position)
    assert_safe_table(table)
    try:
        cursor.execute(f'SELECT * FROM {table} WHERE issue = %s', (issue,))
        return cursor.fetchone()
    except Exception as e:
        logger.error(f'根据期号获取{position}位走势数据失败: {e}')
        return None


def get_position_number_stats(cursor, position: str) -> Dict[str, Any]:
    """获取位置数字统计信息"""
    table, number_col = _get_position_info(position)
    try:
        sql = f'''
            SELECT
                {number_col},
                COUNT(*) as count,
                AVG(omission) as avg_omission,
                MIN(omission) as min_omission,
                MAX(omission) as max_omission
            FROM {table}
            GROUP BY {number_col}
            ORDER BY count DESC
        '''
        cursor.execute(sql)
        results = cursor.fetchall()

        stats = {}
        for row in results:
            stats[row[number_col]] = {
                'count': row['count'],
                'avg_omission': round(row['avg_omission'], 2) if row['avg_omission'] else 0,
                'min_omission': row['min_omission'],
                'max_omission': row['max_omission']
            }

        return stats
    except Exception as e:
        logger.error(f'获取{position}位数字统计失败: {e}')
        return {}


def batch_insert_position_data(cursor, position: str, data: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    批量插入位置数据（使用参数化查询）

    性能优化：使用 executemany 批量插入，比逐条插入快 3-5 倍

    Args:
        cursor: 数据库游标
        position: 位置标识
        data: 数据列表

    Returns:
        (成功条数, 跳过条数)
    """
    if not data:
        return 0, 0

    table, number_col = _get_position_info(position)

    # 过滤无效数据
    valid_data = []
    for item in data:
        issue = str(item.get('issue', ''))
        if not issue:
            continue
        number = item.get(number_col, 0)
        if not (0 <= number <= 9):
            continue
        valid_data.append({
            'issue': issue,
            'number': number,
            'draw_date': item.get('draw_date', ''),
            'is_odd': item.get('is_odd', None),
            'is_big': item.get('is_big', None),
            'is_prime': item.get('is_prime', None),
            'omission': item.get('omission', 0),
            'hot_level': item.get('hot_level', ''),
            'consecutive_count': item.get('consecutive_count', 0),
            'trend_json': json.dumps(item, ensure_ascii=False),
            'source': item.get('source', 'china_lottery')
        })

    if not valid_data:
        return 0, len(data)

    # 批量插入
    sql = f'''
        INSERT INTO {table}
        (issue, {number_col}, draw_date, is_odd, is_big, is_prime,
         omission, hot_level, consecutive_count, trend_json, source)
        VALUES (%(issue)s, %(number)s, %(draw_date)s, %(is_odd)s, %(is_big)s, %(is_prime)s,
                %(omission)s, %(hot_level)s, %(consecutive_count)s, %(trend_json)s, %(source)s)
        ON DUPLICATE KEY UPDATE
            {number_col} = VALUES({number_col}),
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

    try:
        cursor.executemany(sql, valid_data)
        success_count = len(valid_data)
        skip_count = len(data) - success_count
        logger.info(f'{position}位批量插入完成: 成功{success_count}条, 跳过{skip_count}条')
        return success_count, skip_count
    except Exception as e:
        logger.error(f'批量插入{position}位数据失败: {e}')
        return 0, len(data)


# ============================================================================
# C4 期号主链关联查询接口（数据关联分析，非破坏性新增）
# ----------------------------------------------------------------------------
# 目的：为「多维度数据联合分析」提供按期号对齐的统一查询层，消除散落 JOIN。
# 设计原则：
#   * 纯函数 + 参数化 + 白名单校验（沿用本模块安全惯例），不引入物理视图
#     （MySQL 8.0.12 对视图/CHECK 支持有限，纯 Python 层更稳、可移植）。
#   * 所有方法异常兜底返回空/None，绝不向上抛错，与既有 query 层语义一致。
#   * 不改动任何既有写入/读取路径，仅做加法，保证兼容性。
# ============================================================================

# 期号主链：核心表 + 关联表（表名均在 ALLOWED_TABLES 白名单内）
_ISSUE_CHAIN_TABLES: Tuple[str, ...] = (
    'p5_history_data',
    'p5_wan_trend_data', 'p5_qian_trend_data', 'p5_bai_trend_data',
    'p5_shi_trend_data', 'p5_ge_trend_data',
    'p5_spjzs_data', 'p5_hzzst_data', 'p5_sum_end_trend_data',
    'p5_back_three_trend_data',
)

# 各位置走势表 → 号码列
_POSITION_NUMBER_COL: Dict[str, str] = {
    'wan': 'wan_number', 'qian': 'qian_number', 'bai': 'bai_number',
    'shi': 'shi_number', 'ge': 'ge_number',
}


def get_issue_unified(cursor, issue: str) -> Optional[Dict[str, Any]]:
    """按期号对齐读取「主链全量」数据（开奖 + 五走势 + 特殊走势）。

    Args:
        cursor: 数据库游标（来自 P5Database.cursor 或独立连接）。
        issue: 目标期号（如 '2026290'）。

    Returns:
        统一字典，键为各表语义段：
            history: p5_history_data 行
            trends: {wan/qian/bai/shi/ge: {number, draw_date, omission, ...}}
            special: {spjzs, hzzst, sum_end, back_three}
        任一段缺失时置 None；整体异常返回 None。
    """
    try:
        issue = str(issue).strip()
        if not issue:
            return None
        result: Dict[str, Any] = {'history': None, 'trends': {}, 'special': {}}

        # 1) 历史开奖
        cursor.execute(
            'SELECT * FROM p5_history_data WHERE issue = %s', (issue,))
        result['history'] = cursor.fetchone()

        # 2) 五位置走势
        for pos, table in (('wan', 'p5_wan_trend_data'), ('qian', 'p5_qian_trend_data'),
                           ('bai', 'p5_bai_trend_data'), ('shi', 'p5_shi_trend_data'),
                           ('ge', 'p5_ge_trend_data')):
            cursor.execute(
                f'SELECT * FROM {table} WHERE issue = %s', (issue,))
            result['trends'][pos] = cursor.fetchone()

        # 3) 特殊走势
        for key, table in (('spjzs', 'p5_spjzs_data'), ('hzzst', 'p5_hzzst_data'),
                           ('sum_end', 'p5_sum_end_trend_data'),
                           ('back_three', 'p5_back_three_trend_data')):
            try:
                cursor.execute(
                    f'SELECT * FROM {table} WHERE issue = %s', (issue,))
                result['special'][key] = cursor.fetchone()
            except Exception:  # noqa: BLE001
                result['special'][key] = None

        return result
    except Exception as e:  # noqa: BLE001
        logger.warning('[DB-utils] get_issue_unified 失败 issue=%s: %s', issue, e)
        return None


def get_issue_chain_range(cursor, start_issue: str, end_issue: str) -> List[Dict[str, Any]]:
    """按期号范围批量读取主链（用于联合分析 / 特征窗口构建）。

    Args:
        cursor: 数据库游标。
        start_issue: 起始期号（含）。
        end_issue: 结束期号（含）。

    Returns:
        列表，每个元素是 get_issue_unified 的统一字典；异常返回 []。
    """
    if not start_issue or not end_issue:
        return []
    try:
        cursor.execute(
            'SELECT issue FROM p5_history_data WHERE issue BETWEEN %s AND %s '
            'ORDER BY issue ASC',
            (str(start_issue), str(end_issue)))
        issues = [r['issue'] for r in cursor.fetchall()] if cursor.description else []
        return [get_issue_unified(cursor, i) for i in issues if i]
    except Exception as e:  # noqa: BLE001
        logger.warning('[DB-utils] get_issue_chain_range 失败: %s', e)
        return []


def get_position_number_cross(cursor, position: str, limit: int = 200) -> List[Dict[str, Any]]:
    """按位置聚合号码-期号交叉分布（供多维联合分析/可视化）。

    Args:
        cursor: 数据库游标。
        position: 位置标识（wan/qian/bai/shi/ge），白名单校验。
        limit: 返回最大期数（按期号倒序）。

    Returns:
        列表：[{issue, number, omission, draw_date}, ...]；异常返回 []。
    """
    try:
        position = str(position).lower()
        if position not in _POSITION_NUMBER_COL:
            raise ValueError(f'非法位置: {position}')
        number_col = _POSITION_NUMBER_COL[position]
        table = f'p5_{position}_trend_data'
        assert_safe_table(table)
        assert_safe_column(number_col)
        sql = (
            f'SELECT issue, {number_col} AS number, omission, draw_date '
            f'FROM {table} ORDER BY issue DESC LIMIT %s'
        )
        cursor.execute(sql, (int(limit),))
        return [dict(r) for r in (cursor.fetchall() or [])]
    except Exception as e:  # noqa: BLE001
        logger.warning('[DB-utils] get_position_number_cross 失败 pos=%s: %s', position, e)
        return []


def _parse_issue_parts(iss: Any) -> Optional[Tuple[int, int]]:
    """安全解析期为 (年, 年内天数)。

    排列5 期号为 7 位数字 YYYYDDD（如 2024001 = 2024年 第 1 天），
    由 data_fetcher 以 datetime.now().strftime('%Y%j') 生成。解析失败返回
    None（由调用方过滤，避免误判）。
    """
    try:
        s = str(iss).strip()
        if len(s) != 7 or not s.isdigit():
            return None
        year = int(s[:4])
        day = int(s[4:])
        if year < 2000 or day < 1 or day > 366:
            return None
        return (year, day)
    except (ValueError, TypeError):
        return None


def _parse_issue_num(iss: Any) -> int:
    """安全解析期号为整数（用于兼容既有 pipeline 语义）。

    返回年*1000+day 的单调整数（2024001 → 2024001，2024999 → 2024999），
    便于同年内数值排序与差值计算。解析失败返回 0。
    """
    parts = _parse_issue_parts(iss)
    if parts is None:
        return 0
    return parts[0] * 1000 + parts[1]


def detect_issue_gaps(issue_list: List[Any]) -> Dict[str, Any]:
    """从期号列表检测中间缺失段与最大连续段（数据完整性审计）。

    纯计算、无 DB 依赖、可单测。期号按 (年, 年内天数) 解析；**同年**内
    day 差 > 1 判为缺失，**跨年**边界不判缺失（年末天数不定，宁可漏检，
    不可错检）。非格式期号被剔除。

    Args:
        issue_list: 期号列表（顺序无关，内部按 (年, 天内) 排序）。

    Returns:
        {missing_issues: [...], max_consecutive_run: int, gap_count: int}。
        missing_issues 为同年内缺失期号的整数表达（年*1000+day）升序列表。
        max_consecutive_run 为同年内最长连续段（跨年重置）。
    """
    parts = {
        _parse_issue_parts(i) for i in issue_list
        if i is not None and str(i).strip() != ''
    }
    parts = sorted(p for p in parts if p is not None)
    if len(parts) < 2:
        return {'missing_issues': [], 'max_consecutive_run': len(parts), 'gap_count': 0}

    missing = []
    max_run = 1
    cur_run = 1
    for j in range(1, len(parts)):
        prev_year, prev_day = parts[j - 1]
        year, day = parts[j]
        if year == prev_year:
            gap = day - prev_day
            if gap == 1:
                cur_run += 1
                max_run = max(max_run, cur_run)
            elif gap > 1:
                # 同年缺失
                for m in range(prev_day + 1, day):
                    missing.append(year * 1000 + m)
                cur_run = 1
            else:
                cur_run = 1
        else:
            # 跨年：不判缺失（年末天数不定），连续段重置
            cur_run = 1

    return {
        'missing_issues': missing,
        'max_consecutive_run': max_run,
        'gap_count': len(missing),
    }


def get_issue_consistency_report(cursor) -> Dict[str, Any]:
    """生成期号一致性报告（各主链表与 p5_history_data 的期号集合对齐校验）。

    Args:
        cursor: 数据库游标。

    Returns:
        {table: {count, orphan, min_issue, max_issue}}；异常返回 {}。
        p5_history_data 额外含 missing_issues / max_consecutive_run / gap_count
        （C5 数据完整性审计：区间内缺失期号与最长连续段）。
    """
    report: Dict[str, Any] = {}
    try:
        cursor.execute(
            'SELECT MIN(issue) AS mn, MAX(issue) AS mx, COUNT(*) AS c '
            'FROM p5_history_data')
        base = cursor.fetchone() or {}
        report['p5_history_data'] = {
            'count': base.get('c', 0),
            'min_issue': base.get('mn'),
            'max_issue': base.get('mx'),
            'orphan': 0,
        }
        # C5：读取实际期号集合做连续性审计（纯增量字段，不影响既有契约）
        try:
            cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue ASC')
            if cursor.description:
                issue_list = [r['issue'] for r in cursor.fetchall()]
                gap_info = detect_issue_gaps(issue_list)
                report['p5_history_data'].update(gap_info)
            else:
                report['p5_history_data'].update(
                    {'missing_issues': [], 'max_consecutive_run': 0, 'gap_count': 0})
        except Exception as e:  # noqa: BLE001
            logger.warning('[DB-utils] 期号连续性检测失败: %s', e)
            report['p5_history_data'].update(
                {'missing_issues': [], 'max_consecutive_run': 0, 'gap_count': 0})
        for table in _ISSUE_CHAIN_TABLES:
            if table == 'p5_history_data':
                continue
            try:
                cursor.execute(
                    f'SELECT MIN(issue) AS mn, MAX(issue) AS mx, COUNT(*) AS c '
                    f'FROM {table}')
                agg = cursor.fetchone() or {}
                cursor.execute(
                    f'SELECT COUNT(*) AS c FROM {table} '
                    f'WHERE issue NOT IN (SELECT issue FROM p5_history_data)')
                orph = (cursor.fetchone() or {}).get('c', 0)
                report[table] = {
                    'count': agg.get('c', 0),
                    'min_issue': agg.get('mn'),
                    'max_issue': agg.get('mx'),
                    'orphan': orph,
                }
            except Exception:  # noqa: BLE001
                report[table] = {'count': 0, 'min_issue': None, 'max_issue': None, 'orphan': -1}
        return report
    except Exception as e:  # noqa: BLE001
        logger.warning('[DB-utils] get_issue_consistency_report 失败: %s', e)
        return {}


# ============================================================================
# C3 应用层数据校验（MySQL 8.0.12 不支持原生 CHECK 约束，≥8.0.16 才生效）
# ----------------------------------------------------------------------------
# 务实替代：在写入/回测数据预检路径调用本组校验函数，保证范围合法性，
# 避免脏数据写入。函数纯计算、无 DB 依赖、异常兜底返回 False。
# ============================================================================

# 校验范围常量（与 database.sql 中拟加的 CHECK 约束一致）
_DIGIT_MIN, _DIGIT_MAX = 0, 9        # 单号码
_HEZHI_MIN, _HEZHI_MAX = 0, 45       # 五位和值
_SPAN_MIN, _SPAN_MAX = 0, 9          # 跨度
_ACC_MIN, _ACC_MAX = 0.0, 100.0      # 命中率


def validate_history_record(record: Dict[str, Any]) -> bool:
    """校验一条历史开奖记录的数字范围合法性（号码/和值/跨度）。

    校验项：
        - 五个号码均在 [0,9]；
        - hezhi（若提供）在 [0,45] 且等于五位之和；
        - span（若提供）在 [0,9] 且等于 max-min。

    Args:
        record: 含 wan/qian/bai/shi/ge（可含 hezhi/span）的字典。

    Returns:
        True 合法；False 非法（任一校验失败）。
    """
    try:
        nums = [record.get(k) for k in ('wan', 'qian', 'bai', 'shi', 'ge')]
        if any(n is None for n in nums):
            return False
        nums = [int(n) for n in nums]
        if any(n < _DIGIT_MIN or n > _DIGIT_MAX for n in nums):
            return False
        total = sum(nums)
        if record.get('hezhi') is not None:
            if int(record['hezhi']) != total:
                return False
        if record.get('span') is not None:
            span = max(nums) - min(nums)
            if int(record['span']) != span:
                return False
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def validate_accuracy_rate(value: Any) -> bool:
    """校验命中率字段范围 [0,100]。"""
    try:
        v = float(value)
        return _ACC_MIN <= v <= _ACC_MAX
    except (TypeError, ValueError):
        return False


def validate_position_numbers(position: str, numbers: List[Any]) -> bool:
    """校验某位置号码序列均在 [0,9] 且位号合法。"""
    try:
        position = str(position).lower()
        if position not in _POSITION_NUMBER_COL:
            return False
        return all(n is not None and int(n) >= _DIGIT_MIN and int(n) <= _DIGIT_MAX
                   for n in numbers)
    except (TypeError, ValueError):
        return False
