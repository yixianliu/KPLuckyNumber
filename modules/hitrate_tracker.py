"""
排列5命中率统计与管理系统

负责追踪预测命中率、统计性能趋势、自动生成命中率分析报告。
核心功能：
1. 历史预测命中率统计 - 按日期/期号/算法版本统计命中率
2. 实时性能看板 - 计算Top-1/Top-3/Top-5命中率、位置命中率
3. 命中率趋势分析 - 识别命中率随时间的变化趋势
4. 自动统计更新 - 在预测验证后自动更新性能统计表
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class HitRateTracker:
    """
    命中率追踪器
    
    负责统计预测命中率、生成性能报告
    """
    
    POSITION_NAMES = ['万位', '千位', '百位', '十位', '个位']
    POSITION_KEYS = ['wan', 'qian', 'bai', 'shi', 'ge']
    
    def __init__(self, db_instance):
        """
        初始化命中率追踪器
        
        Args:
            db_instance: 数据库实例(P5Database)
        """
        self.db = db_instance
        logger.info('命中率追踪器初始化完成')
    
    def get_hit_rate_statistics(self, 
                                 start_date: Optional[str] = None,
                                 end_date: Optional[str] = None,
                                 limit: int = 100) -> Dict[str, Any]:
        """
        获取命中率统计
        
        Args:
            start_date: 起始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            limit: 最大查询期数
            
        Returns:
            统计字典
        """
        try:
            # 构建WHERE条件
            where_clause = "WHERE verification_status = 'verified'"
            params = []
            
            if start_date:
                where_clause += " AND verified_at >= %s"
                params.append(start_date)
            if end_date:
                where_clause += " AND verified_at <= %s"
                params.append(end_date)
            
            # 查询已验证的预测记录
            sql = f"""
            SELECT 
                target_issue,
                actual_issue,
                actual_numbers,
                match_count,
                accuracy_rate,
                wan_match,
                qian_match,
                bai_match,
                shi_match,
                ge_match,
                verified_at
            FROM p5_prediction_record
            {where_clause}
            ORDER BY verified_at DESC
            LIMIT %s
            """
            params.append(limit)
            
            self.db.cursor.execute(sql, tuple(params))
            records = self.db.cursor.fetchall()
            
            if not records:
                logger.warning('无已验证的预测记录')
                return {
                    'total_predictions': 0,
                    'hit_rates': {},
                    'position_hit_rates': {},
                    'trend': []
                }
            
            # 计算统计指标
            total = len(records)
            full_matches = sum(1 for r in records if r['match_count'] == 5)
            partial_matches = sum(1 for r in records if 0 < r['match_count'] < 5)
            no_matches = sum(1 for r in records if r['match_count'] == 0)
            
            avg_match_count = round(sum(r['match_count'] for r in records) / total, 2)
            avg_accuracy = round(sum(r['accuracy_rate'] for r in records) / total, 2)
            
            # 位置命中率
            position_hits = {pos: 0 for pos in self.POSITION_KEYS}
            for record in records:
                for pos in self.POSITION_KEYS:
                    if record.get(f'{pos}_match'):
                        position_hits[pos] += 1
            
            position_hit_rates = {
                pos: round(count / total * 100, 2)
                for pos, count in position_hits.items()
            }
            
            # 命中率趋势（按5期分组）
            trend = self._calculate_trend(records)
            
            result = {
                'total_predictions': total,
                'full_matches': full_matches,
                'partial_matches': partial_matches,
                'no_matches': no_matches,
                'full_match_rate': round(full_matches / total * 100, 2),
                'avg_match_count': avg_match_count,
                'avg_accuracy': avg_accuracy,
                'position_hit_rates': position_hit_rates,
                'trend': trend
            }
            
            logger.info(f'命中率统计完成: 总预测{total}期, 完全命中{full_matches}期({result["full_match_rate"]}%)')
            return result
            
        except Exception as e:
            logger.error(f'获取命中率统计失败: {e}', exc_info=True)
            return {'error': str(e)}
    
    def get_position_hit_rate_by_period(self, target_issue: str) -> Dict[str, Any]:
        """
        获取特定期数的位置命中率详情
        
        Args:
            target_issue: 目标期号
            
        Returns:
            位置命中率详情
        """
        try:
            sql = """
            SELECT 
                target_issue,
                actual_numbers,
                match_count,
                accuracy_rate,
                wan_match, qian_match, bai_match, shi_match, ge_match,
                verified_at
            FROM p5_prediction_record
            WHERE target_issue = %s AND verification_status = 'verified'
            """
            self.db.cursor.execute(sql, (target_issue,))
            record = self.db.cursor.fetchone()
            
            if not record:
                return {'error': f'未找到期号{target_issue}的验证记录'}
            
            return {
                'issue': record['target_issue'],
                'verified_at': record['verified_at'].strftime('%Y-%m-%d %H:%M:%S') if record['verified_at'] else None,
                'match_count': record['match_count'],
                'accuracy_rate': record['accuracy_rate'],
                'position_hits': {
                    pos: {
                        'hit': bool(record.get(f'{pos}_match')),
                        'actual': int(record['actual_numbers'][i]) if record['actual_numbers'] else None
                    }
                    for i, pos in enumerate(self.POSITION_KEYS)
                }
            }
            
        except Exception as e:
            logger.error(f'获取位置命中率失败: {e}', exc_info=True)
            return {'error': str(e)}
    
    def _calculate_trend(self, records: List[Dict]) -> List[Dict[str, Any]]:
        """
        计算命中率趋势（每5期一组）
        
        Args:
            records: 预测记录列表
            
        Returns:
            趋势数据列表
        """
        trend = []
        batch_size = 5
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            avg_match = round(sum(r['match_count'] for r in batch) / len(batch), 2)
            avg_acc = round(sum(r['accuracy_rate'] for r in batch) / len(batch), 2)
            
            trend.append({
                'batch_start': i + 1,
                'batch_end': min(i + batch_size, len(records)),
                'periods': len(batch),
                'avg_match_count': avg_match,
                'avg_accuracy': avg_acc,
                'full_hits': sum(1 for r in batch if r['match_count'] == 5)
            })
        
        return trend
    
    def update_performance_stats(self, stats_date: Optional[str] = None) -> bool:
        """
        更新性能统计表
        
        Args:
            stats_date: 统计日期，默认今天
            
        Returns:
            是否成功
        """
        try:
            if not stats_date:
                stats_date = datetime.now().strftime('%Y-%m-%d')
            
            # 查询当天所有已验证记录
            next_day = (datetime.strptime(stats_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            
            sql = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN is_matched = 1 THEN 1 ELSE 0 END) as full_matches,
                SUM(CASE WHEN match_count > 0 AND is_matched = 0 THEN 1 ELSE 0 END) as partial_matches,
                AVG(match_count) as avg_match_count,
                AVG(accuracy_rate) as avg_accuracy,
                SUM(CASE WHEN wan_match = 1 THEN 1 ELSE 0 END) / COUNT(*) * 100 as wan_acc,
                SUM(CASE WHEN qian_match = 1 THEN 1 ELSE 0 END) / COUNT(*) * 100 as qian_acc,
                SUM(CASE WHEN bai_match = 1 THEN 1 ELSE 0 END) / COUNT(*) * 100 as bai_acc,
                SUM(CASE WHEN shi_match = 1 THEN 1 ELSE 0 END) / COUNT(*) * 100 as shi_acc,
                SUM(CASE WHEN ge_match = 1 THEN 1 ELSE 0 END) / COUNT(*) * 100 as ge_acc
            FROM p5_prediction_record
            WHERE verification_status = 'verified'
            AND DATE(verified_at) = %s
            """
            
            self.db.cursor.execute(sql, (stats_date,))
            result = self.db.cursor.fetchone()
            
            if not result or result['total'] == 0:
                logger.info(f'日期 {stats_date} 无验证记录，跳过性能统计更新')
                return True
            
            # 计算连中次数（简化版）
            streak_sql = """
            SELECT match_count FROM p5_prediction_record
            WHERE verification_status = 'verified' AND DATE(verified_at) = %s
            ORDER BY verified_at DESC
            """
            self.db.cursor.execute(streak_sql, (stats_date,))
            streak_records = self.db.cursor.fetchall()
            
            current_streak = 0
            max_streak = 0
            for rec in streak_records:
                if rec['match_count'] == 5:
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                else:
                    current_streak = 0
            
            # 更新或插入性能统计
            upsert_sql = """
            INSERT INTO p5_performance_stats 
            (stat_date, total_predictions, total_matched, total_partial_match, 
             avg_match_count, avg_accuracy, wan_accuracy, qian_accuracy, bai_accuracy, 
             shi_accuracy, ge_accuracy, overall_accuracy, best_streak, current_streak)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                total_predictions = VALUES(total_predictions),
                total_matched = VALUES(total_matched),
                total_partial_match = VALUES(total_partial_match),
                avg_match_count = VALUES(avg_match_count),
                wan_accuracy = VALUES(wan_accuracy),
                qian_accuracy = VALUES(qian_accuracy),
                bai_accuracy = VALUES(bai_accuracy),
                shi_accuracy = VALUES(shi_accuracy),
                ge_accuracy = VALUES(ge_accuracy),
                overall_accuracy = VALUES(overall_accuracy),
                best_streak = VALUES(best_streak),
                current_streak = VALUES(current_streak),
                created_at = NOW()
            """
            
            self.db.cursor.execute(upsert_sql, (
                stats_date,
                result['total'],
                result['full_matches'],
                result['partial_matches'],
                result['avg_match_count'] or 0,
                result['avg_accuracy'] or 0,
                result['wan_acc'] or 0,
                result['qian_acc'] or 0,
                result['bai_acc'] or 0,
                result['shi_acc'] or 0,
                result['ge_acc'] or 0,
                result['avg_accuracy'] or 0,
                max_streak,
                streak_records[0]['match_count'] == 5 if streak_records else 0
            ))
            
            self.db.connection.commit()
            logger.info(f'性能统计更新成功: {stats_date}')
            return True
            
        except Exception as e:
            logger.error(f'更新性能统计失败: {e}', exc_info=True)
            return False
    
    def generate_hit_rate_report(self, days: int = 30) -> str:
        """
        生成命中率分析报告
        
        Args:
            days: 最近N天的数据
            
        Returns:
            报告文本
        """
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            end_date = datetime.now().strftime('%Y-%m-%d')
            
            stats = self.get_hit_rate_statistics(
                start_date=start_date,
                end_date=end_date,
                limit=1000
            )
            
            if 'error' in stats or stats.get('total_predictions', 0) == 0:
                return f"命中率报告生成失败：最近{days}天无足够验证数据"
            
            report_lines = [
                "=" * 60,
                "排列5预测命中率分析报告",
                f"统计周期：最近{days}天",
                f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "=" * 60,
                "",
                f"总预测期数：{stats['total_predictions']} 期",
                f"完全命中：{stats['full_matches']} 期 ({stats['full_match_rate']}%)",
                f"部分命中：{stats['partial_matches']} 期",
                f"未命中：{stats['no_matches']} 期",
                f"平均命中位数：{stats['avg_match_count']}/5",
                f"平均准确率：{stats['avg_accuracy']}%",
                "",
                "【各位置命中率】",
            ]
            
            for pos in self.POSITION_KEYS:
                rate = stats.get('position_hit_rates', {}).get(pos, 0)
                name = dict(zip(self.POSITION_KEYS, self.POSITION_NAMES))[pos]
                bar = '█' * int(rate / 2)
                report_lines.append(f"  {name:4s}：{rate:6.2f}% {bar}")
            
            report_lines.extend([
                "",
                "【命中率趋势（每5期）】",
            ])
            
            for batch in stats.get('trend', [])[:10]:
                report_lines.append(
                    f"  第{batch['batch_start']}-{batch['batch_end']}期：平均命中{batch['avg_match_count']}位，"
                    f"完全命中{batch['full_hits']}次"
                )
            
            report_lines.append("")
            report_lines.append("=" * 60)
            report_lines.append("⚠️ 重要提示：彩票开奖具有随机性，历史命中率不代表未来表现")
            report_lines.append("请理性购彩，切勿过度依赖预测结果")
            report_lines.append("=" * 60)
            
            report = '\n'.join(report_lines)
            logger.info(f'命中率报告生成成功（{stats["total_predictions"]}期数据）')
            return report
            
        except Exception as e:
            logger.error(f'生成命中率报告失败: {e}', exc_info=True)
            return f"报告生成失败: {str(e)}"
