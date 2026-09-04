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
from typing import Dict, List, Any, Optional, Tuple

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
