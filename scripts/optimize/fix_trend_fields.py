# -*- coding: utf-8 -*-
"""
全量修复位置走势表字段缺失问题

修复目标：
  1. p5_history_data 中 draw_date 为空的记录 -> 从外部源回填 + 智能推算
  2. p5_wan/qian/bai/shi/ge_trend_data 中 draw_date/hot_level/trend_json 缺失 -> 全量修复

用法：python scripts/fix_trend_fields.py
"""
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Dict

sys.path.insert(0, '.')
from modules.database import P5Database
from modules.data_fetcher import P5Spider

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

POSITION_MAP = {
    'wan': ('p5_wan_trend_data', 'wan_number'),
    'qian': ('p5_qian_trend_data', 'qian_number'),
    'bai': ('p5_bai_trend_data', 'bai_number'),
    'shi': ('p5_shi_trend_data', 'shi_number'),
    'ge':   ('p5_ge_trend_data',   'ge_number'),
}


def get_db_stats(db):
    """获取各表字段缺失统计"""
    stats = {}
    for pos, (table, _) in POSITION_MAP.items():
        db.cursor.execute(f"""
            SELECT
                COUNT(*)                                                AS total,
                SUM(CASE WHEN draw_date IS NULL OR draw_date = '' THEN 1 ELSE 0 END) AS null_date,
                SUM(CASE WHEN hot_level  IS NULL OR hot_level  = '' THEN 1 ELSE 0 END) AS null_hot,
                SUM(CASE WHEN trend_json IS NULL OR trend_json = '' THEN 1 ELSE 0 END) AS null_trend
            FROM `{table}`
        """)
        r = db.cursor.fetchone()
        total = int(r['total']) if r else 0
        stats[pos] = {
            'total': total,
            'null_date': int(r['null_date']) if r and r.get('null_date') is not None else 0,
            'null_hot': int(r['null_hot']) if r and r.get('null_hot') is not None else 0,
            'null_trend': int(r['null_trend']) if r and r.get('null_trend') is not None else 0,
        }
    db.cursor.execute("""
        SELECT
            COUNT(*)                                                  AS total,
            SUM(CASE WHEN draw_date IS NULL OR draw_date = '' THEN 1 ELSE 0 END) AS null_date
        FROM p5_history_data
    """)
    r = db.cursor.fetchone()
    total = int(r['total']) if r else 0
    stats['history'] = {
        'total': total,
        'null_date': int(r['null_date']) if r and r.get('null_date') is not None else 0,
    }
    return stats


def print_stats(stats, label=''):
    prefix = f'[{label}] ' if label else ''
    logger.info(f'{prefix}=== 字段完整性统计 ===')
    for pos, s in stats.items():
        t = s['total']
        nd = s.get('null_date', 0)
        nh = s.get('null_hot', 0)
        nt = s.get('null_trend', 0)
        pct_d = f'{nd*100//t if t else 0}%'
        pct_h = f'{nh*100//t if t else 0}%'
        pct_t = f'{nt*100//t if t else 0}%'
        logger.info(f'  {pos.upper():8s} total={t:5d}  null_date={nd:4d}({pct_d})  null_hot={nh:4d}({pct_h})  null_trend={nt:4d}({pct_t})')


def fix_history_draw_date(db):
    """修复 p5_history_data 的 draw_date：多源爬取 + 智能推算"""
    logger.info('=== Step 1: 修复 p5_history_data draw_date ===')

    db.cursor.execute("""
        SELECT COUNT(*) FROM p5_history_data
        WHERE draw_date IS NULL OR draw_date = ''
    """)
    null_count = db.cursor.fetchone()['COUNT(*)']
    logger.info(f'待修复: {null_count} 条')

    if null_count == 0:
        return 0

    # 爬取多源数据获取日期
    spider = P5Spider()
    date_map = {}

    # 源1: 55128 历史数据（120期）
    logger.info('爬取 55128.cn 历史数据...')
    try:
        data = spider.crawl_history_data(max_records=120)
        for item in data:
            d = item.get('draw_date') or item.get('date', '')
            if item.get('issue') and d:
                date_map[item['issue']] = d
        logger.info(f'55128 提供: {len(date_map)} 条日期')
    except Exception as e:
        logger.warning(f'55128 爬取失败: {e}')

    # 源2: 中华彩讯走势图（500期）
    logger.info('爬取中华彩讯走势图数据...')
    try:
        trend_data = spider.crawl_trend_data(record=500)
        extra = 0
        for item in trend_data:
            d = item.get('draw_date') or item.get('date', '')
            if item.get('issue') and d and item['issue'] not in date_map:
                date_map[item['issue']] = d
                extra += 1
        logger.info(f'中华彩讯额外提供: {extra} 条日期')
    except Exception as e:
        logger.warning(f'中华彩讯爬取失败: {e}')

    logger.info(f'日期映射总计: {len(date_map)} 条')

    # 直接匹配回填
    db.cursor.execute("""
        SELECT issue FROM p5_history_data
        WHERE draw_date IS NULL OR draw_date = ''
        ORDER BY issue DESC
    """)
    null_issues = [str(r['issue']) for r in (db.cursor.fetchall() or [])]

    matched = 0
    for issue in null_issues:
        if issue in date_map:
            db.cursor.execute(
                'UPDATE p5_history_data SET draw_date = %s WHERE issue = %s',
                (date_map[issue], issue)
            )
            matched += 1
    logger.info(f'直接匹配回填: {matched}/{len(null_issues)} 条')

    # 智能推算：用已知日期+期号差计算剩余缺失
    db.cursor.execute("SELECT issue, draw_date FROM p5_history_data WHERE draw_date IS NOT NULL AND draw_date != '' ORDER BY issue ASC")
    known_pairs = [(int(r['issue']), r['draw_date']) for r in (db.cursor.fetchall() or [])]
    logger.info(f'已知日期对: {len(known_pairs)} 条')

    db.cursor.execute("""
        SELECT issue FROM p5_history_data
        WHERE draw_date IS NULL OR draw_date = ''
        ORDER BY issue ASC
    """)
    still_null = [str(r['issue']) for r in (db.cursor.fetchall() or [])]
    logger.info(f'剩余未修复: {len(still_null)} 条，尝试智能推算...')

    est_count = 0
    for issue_str in still_null:
        issue_int = int(issue_str)
        prev_known = None
        next_known = None
        for k_issue, k_date in known_pairs:
            if k_issue < issue_int:
                prev_known = (k_issue, k_date)
            elif k_issue > issue_int and next_known is None:
                next_known = (k_issue, k_date)

        calculated_date = None
        if prev_known:
            days_diff = issue_int - prev_known[0]
            try:
                calculated_date = (datetime.strptime(prev_known[1], '%Y-%m-%d') + timedelta(days=days_diff)).strftime('%Y-%m-%d')
            except Exception:
                pass
        elif next_known:
            days_diff = next_known[0] - issue_int
            try:
                calculated_date = (datetime.strptime(next_known[1], '%Y-%m-%d') - timedelta(days=days_diff)).strftime('%Y-%m-%d')
            except Exception:
                pass

        if calculated_date:
            db.cursor.execute(
                'UPDATE p5_history_data SET draw_date = %s WHERE issue = %s',
                (calculated_date, issue_str)
            )
            est_count += 1

    logger.info(f'智能推算回填: {est_count} 条')
    return matched + est_count


def fix_position_draw_date(db):
    """修复位置走势表 draw_date（从 p5_history_data 关联回填）"""
    logger.info('=== Step 2: 修复位置走势表 draw_date ===')

    db.cursor.execute("""
        SELECT issue, draw_date FROM p5_history_data
        WHERE draw_date IS NOT NULL AND draw_date != ''
        ORDER BY issue DESC LIMIT 3000
    """)
    date_map = {str(r['issue']): str(r['draw_date']) for r in (db.cursor.fetchall() or [])}
    logger.info(f'日期映射: {len(date_map)} 条')

    if not date_map:
        logger.warning('无有效日期映射，跳过')
        return 0

    total_updated = 0
    for pos, (table, num_col) in POSITION_MAP.items():
        try:
            db.cursor.execute(
                f"SELECT issue FROM `{table}` WHERE draw_date IS NULL OR draw_date = '' ORDER BY issue DESC LIMIT 3000"
            )
            rows = db.cursor.fetchall() or []
            updated = 0
            for row in rows:
                issue = str(row['issue'])
                if issue in date_map:
                    new_date = date_map[issue]
                    # 同步更新 trend_json
                    db.cursor.execute(f"SELECT trend_json FROM `{table}` WHERE issue = %s", (issue,))
                    trend_row = db.cursor.fetchone()
                    if trend_row and trend_row.get('trend_json'):
                        try:
                            tj = json.loads(trend_row['trend_json'])
                            tj['draw_date'] = new_date
                            db.cursor.execute(
                                f"UPDATE `{table}` SET draw_date = %s, trend_json = %s WHERE issue = %s",
                                (new_date, json.dumps(tj, ensure_ascii=False), issue)
                            )
                        except Exception:
                            db.cursor.execute(
                                f"UPDATE `{table}` SET draw_date = %s WHERE issue = %s",
                                (new_date, issue)
                            )
                    else:
                        # trend_json 为空时重建
                        db.cursor.execute(f"SELECT * FROM `{table}` WHERE issue = %s", (issue,))
                        full = db.cursor.fetchone()
                        if full:
                            rebuilt = {
                                'issue': full.get('issue', ''),
                                f'{pos}_number': full.get(num_col),
                                'draw_date': new_date,
                                'is_odd': full.get('is_odd'),
                                'is_big': full.get('is_big'),
                                'is_prime': full.get('is_prime'),
                                'omission': full.get('omission', 0),
                                'hot_level': full.get('hot_level', ''),
                                'consecutive_count': full.get('consecutive_count', 0),
                                'source': full.get('source', 'china_lottery'),
                            }
                            db.cursor.execute(
                                f"UPDATE `{table}` SET draw_date = %s, trend_json = %s WHERE issue = %s",
                                (new_date, json.dumps(rebuilt, ensure_ascii=False), issue)
                            )
                        else:
                            db.cursor.execute(
                                f"UPDATE `{table}` SET draw_date = %s WHERE issue = %s",
                                (new_date, issue)
                            )
                    updated += 1
            total_updated += updated
            logger.info(f'{pos}位走势表 draw_date 修复: {updated}/{len(rows)} 条')
        except Exception as e:
            logger.error(f'{pos}位 draw_date 修复失败: {e}')

    logger.info(f'位置走势表 draw_date 修复总计: {total_updated} 条')
    return total_updated


def fix_position_hot_level(db):
    """全量重新计算所有位置走势表的 hot_level"""
    logger.info('=== Step 3: 修复位置走势表 hot_level ===')

    for pos, (table, num_col) in POSITION_MAP.items():
        try:
            db.cursor.execute(
                f'SELECT `{num_col}` AS n, COUNT(*) AS cnt FROM `{table}` GROUP BY `{num_col}`'
            )
            rows = db.cursor.fetchall() or []
            counts = {}
            total_records = 0
            for row in rows:
                n = int(row['n'])
                cnt = int(row['cnt'])
                counts[n] = cnt
                total_records += cnt

            if total_records == 0:
                logger.warning(f'{pos}位走势表无数据，跳过')
                continue

            avg_count = total_records / 10.0
            level_map = {}
            for n, cnt in counts.items():
                if cnt > avg_count * 1.2:
                    level_map[n] = 'hot'
                elif cnt < avg_count * 0.8:
                    level_map[n] = 'cold'
                else:
                    level_map[n] = 'warm'

            for n, level in level_map.items():
                db.cursor.execute(
                    f"UPDATE `{table}` SET hot_level = %s WHERE `{num_col}` = %s",
                    (level, n)
                )
                # 同步 trend_json
                db.cursor.execute(
                    f"SELECT issue, trend_json FROM `{table}` WHERE `{num_col}` = %s",
                    (n,)
                )
                for tr in (db.cursor.fetchall() or []):
                    try:
                        tj_str = tr.get('trend_json') or ''
                        if tj_str:
                            tj = json.loads(tj_str)
                            tj['hot_level'] = level
                            db.cursor.execute(
                                f"UPDATE `{table}` SET trend_json = %s WHERE issue = %s",
                                (json.dumps(tj, ensure_ascii=False), tr['issue'])
                            )
                    except Exception:
                        pass

            hot_cnt = sum(1 for l in level_map.values() if l == 'hot')
            warm_cnt = sum(1 for l in level_map.values() if l == 'warm')
            cold_cnt = sum(1 for l in level_map.values() if l == 'cold')
            logger.info(
                f'{pos}位 hot_level 修复: hot={hot_cnt}, warm={warm_cnt}, cold={cold_cnt}, 总记录={total_records}'
            )
        except Exception as e:
            logger.error(f'{pos}位 hot_level 修复失败: {e}')


def fix_trend_json_null(db):
    """修复 trend_json 为空的记录"""
    logger.info('=== Step 4: 修复 trend_json 空值 ===')
    total = 0
    for pos, (table, num_col) in POSITION_MAP.items():
        try:
            db.cursor.execute(
                f"SELECT issue FROM `{table}` WHERE trend_json IS NULL OR trend_json = ''"
            )
            rows = db.cursor.fetchall() or []
            for row in rows:
                db.cursor.execute(f"SELECT * FROM `{table}` WHERE issue = %s", (row['issue'],))
                full = db.cursor.fetchone()
                if full:
                    rebuilt = {
                        'issue': full.get('issue', ''),
                        f'{pos}_number': full.get(num_col),
                        'draw_date': full.get('draw_date', ''),
                        'is_odd': full.get('is_odd'),
                        'is_big': full.get('is_big'),
                        'is_prime': full.get('is_prime'),
                        'omission': full.get('omission', 0),
                        'hot_level': full.get('hot_level', ''),
                        'consecutive_count': full.get('consecutive_count', 0),
                        'source': full.get('source', 'china_lottery'),
                    }
                    db.cursor.execute(
                        f"UPDATE `{table}` SET trend_json = %s WHERE issue = %s",
                        (json.dumps(rebuilt, ensure_ascii=False), row['issue'])
                    )
                    total += 1
            if total:
                logger.info(f'{pos}位 trend_json 修复: {total} 条')
        except Exception as e:
            logger.error(f'{pos}位 trend_json 修复失败: {e}')
    logger.info(f'trend_json 修复总计: {total} 条')
    return total


def main():
    logger.info('=' * 60)
    logger.info('位置走势表字段全量修复脚本')
    logger.info('=' * 60)

    db = P5Database()
    if not db.connect():
        logger.error('数据库连接失败')
        return

    try:
        # 修复前统计
        before_stats = get_db_stats(db)
        print_stats(before_stats, '修复前')

        # Step 1-4
        fix_history_draw_date(db)
        fix_position_draw_date(db)
        fix_position_hot_level(db)
        fix_trend_json_null(db)

        db.cursor.execute('COMMIT')

        # 修复后统计
        after_stats = get_db_stats(db)
        print_stats(after_stats, '修复后')

        logger.info('=' * 60)
        logger.info('修复完成')
        logger.info('=' * 60)

    except Exception as e:
        db.cursor.execute('ROLLBACK')
        logger.error(f'修复过程异常: {e}')
        raise
    finally:
        db.disconnect()


if __name__ == '__main__':
    main()
