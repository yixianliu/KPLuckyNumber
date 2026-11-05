"""
排列5 预测验证系统模块

功能：
1. 跟踪每一期预测结果（从数据库读取待验证预测记录）
2. 在实际开奖结果公布后，自动比对预测与实际结果
3. 计算命中率、偏差分析、准确率指标（按位置统计）
4. 建立预测性能评估档案（含历史趋势）

调用路径：
    main.py → Validator → P5Database（读写预测验证数据）

数据库依赖表：
    - p5_prediction_record: 预测记录源（含待验证状态）
    - p5_verification_detail: 验证结果明细表
    - p5_performance_stats: 性能统计汇总表
    - p5_history_data: 历史开奖数据（用于获取实际号码）
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

from paths import LOGS_DIR

os.makedirs(LOGS_DIR, exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(LOGS_DIR + '/prediction_validator.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class Validator:
    """
    排列5预测验证器

    职责：
    - 验证待验证预测记录（比对预测号码与实际开奖号码）
    - 按位置（万/千/百/十/个）统计命中率
    - 生成性能评估报告（含整体统计、各位置命中率、趋势图表数据）
    - 提供完整验证流程入口

    验证精度计算：
    - 总命中位数: 统计所有预测中各位置命中的总数
    - 平均命中位数: 总命中位数 / 总预测次数
    - 平均准确率: (总命中位数 / (总预测次数 × 5)) × 100%
    - 各位置命中率: 该位置命中次数 / 总预测次数 × 100%
    """

    def __init__(self):
        """初始化验证器，数据库连接采用延迟加载模式"""
        self.db = None

    def _load_database(self):
        """
        延迟加载数据库模块
        遵循项目延迟导入模式，避免导入时因MySQL不可用而失败
        """
        if self.db is None:
            from modules.database import P5Database
            self.db = P5Database()
    
    def verify_prediction(self, report_uuid: str, target_issue: str,
                           actual_numbers: List[int]) -> Dict[str, Any]:
        """
        验证单条预测记录

        流程:
        1. 先尝试从数据库历史数据中获取该期的实际开奖号码
        2. 若数据库中有，则覆盖传入的actual_numbers
        3. 调用数据库层update_prediction_verification()执行比对
        4. 更新全局性能统计表

        Args:
            report_uuid: 报告唯一标识符
            target_issue: 目标期号（如"2026165"）
            actual_numbers: 实际开奖号码 [wan, qian, bai, shi, ge]，每个0-9

        Returns:
            验证结果字典，包含status/message/各位置命中情况
        """
        self._load_database()
        
        if not self.db.connect():
            return {'status': 'error', 'message': '数据库连接失败'}
        
        try:
            # 先从数据库获取该期历史数据
            history = self.db.get_history_by_issue(target_issue)
            if history:
                actual_numbers = [history['wan'], history['qian'], history['bai'],
                                  history['shi'], history['ge']]
            
            result = self.db.update_prediction_verification(
                report_uuid=report_uuid,
                target_issue=target_issue,
                actual_numbers=actual_numbers,
                actual_issue=target_issue
            )
            
            # 更新性能统计
            self.db.update_performance_stats()
            
            return result
        except Exception as e:
            logger.error(f'验证预测失败: {e}')
            return {'status': 'error', 'message': str(e)}
        finally:
            self.db.disconnect()
    
    def verify_all_pending(self) -> List[Dict[str, Any]]:
        """
        批量验证所有待验证的预测记录

        流程:
        1. 从数据库获取所有status='pending'的预测记录
        2. 逐条查询对应期号的实际开奖数据
        3. 若已开奖（有历史数据），执行验证并更新状态
        4. 若未开奖，跳过该条记录
        5. 全部完成后更新全局性能统计

        Returns:
            验证结果列表，每项包含各位置命中详情
        """
        self._load_database()
        
        if not self.db.connect():
            return []
        
        try:
            # 全量验证：显式传 limit=None，避免默认 500 条上限静默漏验历史积压
            pending = self.db.get_pending_predictions(limit=None)
            results = []
            
            for record in pending:
                target_issue = record['target_issue']
                
                # 查询该期是否有实际开奖数据
                history = self.db.get_history_by_issue(target_issue)
                if history:
                    actual_numbers = [history['wan'], history['qian'], history['bai'],
                                      history['shi'], history['ge']]
                    
                    result = self.db.update_prediction_verification(
                        report_uuid=record['report_uuid'],
                        target_issue=target_issue,
                        actual_numbers=actual_numbers,
                        actual_issue=target_issue
                    )
                    results.append(result)
                    logger.info(f'自动验证完成: {target_issue}')
                else:
                    logger.info(f'目标期号 {target_issue} 尚未开奖，跳过验证')
            
            # 更新性能统计
            if results:
                self.db.update_performance_stats()
            
            return results
        except Exception as e:
            logger.error(f'批量验证失败: {e}')
            return []
        finally:
            self.db.disconnect()
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """
        获取预测性能统计数据

        返回数据结构:
        {
            'status': 'success',
            'current_stats': {
                'total': 总预测次数,
                'total_matched': 完全猜中次数（5位全中）,
                'avg_match': 平均命中位数（0-5）,
                'avg_accuracy': 平均准确率（%),
                'wan_accuracy': 万位命中率（%),
                'qian_accuracy': 千位命中率（%),
                'bai_accuracy': 百位命中率（%),
                'shi_accuracy': 十位命中率（%),
                'ge_accuracy': 个位命中率（%),
                'overall_accuracy': 整体命中率（%)
            },
            'history': [最近30天的每日统计记录]
        }

        Returns:
            性能统计字典
        """
        self._load_database()
        
        if not self.db.connect():
            return {'status': 'error', 'message': '数据库连接失败'}
        
        try:
            stats = self.db.get_verification_stats()
            history = self.db.get_performance_history(limit=30)
            
            return {
                'status': 'success',
                'current_stats': stats,
                'history': history
            }
        except Exception as e:
            logger.error(f'获取性能统计失败: {e}')
            return {'status': 'error', 'message': str(e)}
        finally:
            self.db.disconnect()
    
    def get_pending_predictions(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取待验证的预测记录列表（按数值期号倒序）

        从数据库查询status='pending'的AI预测记录，
        这些记录已生成预测但尚未与实际开奖结果比对。

        Args:
            limit: 返回条数上限。默认 None（不限），保持 GUI 展示的既有全量语义；
                需要限制内存/传输量时可显式传入（DB 层默认 500）。

        Returns:
            待验证预测列表，每项包含report_uuid/target_issue/预测号码等
        """
        self._load_database()
        
        if not self.db.connect():
            return []
        
        try:
            return self.db.get_pending_predictions(limit=limit)
        except Exception as e:
            logger.error(f'获取待验证预测失败: {e}')
            return []
        finally:
            self.db.disconnect()
    
    def generate_performance_report(self) -> str:
        """
        生成人类可读的性能评估报告文本

        报告包含5个部分:
        一、整体统计 - 总预测次数/完全猜中/平均命中位数/平均准确率
        二、各位置命中率 - 万位~个位各位置独立命中率
        三、综合评估 - 整体命中率和质量评级（优秀/良好/一般/待提升）
        四、命中率分级 - 根据overall_accuracy对应4个等级
        五、性能趋势 - 最近7天的命中率变化趋势

        评级标准:
        - overall_accuracy >= 60%: 优秀
        - overall_accuracy >= 40%: 良好
        - overall_accuracy >= 20%: 一般
        - overall_accuracy <  20%: 待提升

        Returns:
            格式化的报告文本
        """
        stats_result = self.get_performance_stats()
        
        if stats_result.get('status') != 'success':
            return '获取性能统计失败'
        
        stats = stats_result.get('current_stats', {})
        
        if stats.get('total', 0) == 0:
            return '暂无验证数据，请等待开奖后进行验证。'
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        report = f"""排列5 AI预测性能评估报告
生成时间: {timestamp}
========================================

一、整体统计
----------------------------------------
总预测次数: {stats['total']}
宽松口径·完全猜中次数（集合包含+±1容错，非预测力）: {stats['total_matched']}
平均命中位数: {stats['avg_match']}/5
宽松口径平均准确率（集合包含+±1容错，非预测力）: {stats['avg_accuracy']}%
说明: 真实预测力请以 Top-1 精确口径为准（随机基线 10%，Top-3 基线 30%），
      上列宽松口径天然高于 Top-1，不可当作命中率提升结论。

二、各位置命中率（宽松口径·非预测力）
----------------------------------------
万位命中率: {stats['wan_accuracy']}%
千位命中率: {stats['qian_accuracy']}%
百位命中率: {stats['bai_accuracy']}%
十位命中率: {stats['shi_accuracy']}%
个位命中率: {stats['ge_accuracy']}%

三、综合评估（宽松口径·非预测力）
----------------------------------------
宽松口径整体命中率: {stats['overall_accuracy']}%

四、宽松口径等级参考（非预测力）
----------------------------------------
"""
        
        overall = stats['overall_accuracy']
        if overall >= 60:
            level = '优秀'
        elif overall >= 40:
            level = '良好'
        elif overall >= 20:
            level = '一般'
        else:
            level = '待提升'
        
        report += f"宽松口径参考等级: {level}\n"
        report += ("说明: 上列等级按宽松口径计算，真实预测力请以 Top-1 精确口径"
                   "（随机基线 10%）为准\n")
        
        report += """
五、性能趋势
----------------------------------------
"""
        
        history = stats_result.get('history', [])
        if history:
            report += "最近7天命中率趋势:\n"
            for h in history[:7]:
                report += f"  {h['stat_date']}: {h['overall_accuracy']}% ({h['total_predictions']}次)\n"
        else:
            report += "暂无历史趋势数据\n"
        
        report += """
========================================
说明：
- 宽松口径·完全猜中：预测集合包含实际号且偏差≤1（非预测力）
- 宽松口径命中率：命中位数 / 5 * 100%（非预测力）
- 真实预测力：Top-1 精确口径（每期仅首推号），随机基线 10%
- 数据基于历史预测验证结果统计
========================================
"""
        
        return report
    
    def run_full_validation(self) -> Dict[str, Any]:
        """
        执行完整验证流程（GUI调用的统一入口）

        步骤:
        1. verify_all_pending() - 验证所有待验证预测
        2. get_performance_stats() - 获取最新统计数据
        3. generate_performance_report() - 生成可读报告

        Returns:
            {
                'status': 'success',
                'verified_count': 本次验证的记录数,
                'verified_results': [逐条验证详情],
                'stats': {当前性能统计数据},
                'report': '格式化的报告文本'
            }
        """
        logger.info('开始执行完整验证流程')
        
        # 验证所有待验证预测
        verified_results = self.verify_all_pending()
        
        # 获取最新统计
        stats_result = self.get_performance_stats()
        
        # 生成报告
        report = self.generate_performance_report()
        
        logger.info(f'验证完成，本次验证{len(verified_results)}条记录')
        
        return {
            'status': 'success',
            'verified_count': len(verified_results),
            'verified_results': verified_results,
            'stats': stats_result.get('current_stats', {}),
            'report': report
        }


# ============================================================
# 独立测试入口：可运行 python -m modules.prediction_validator
# ============================================================

def test_validator():
    """测试预测验证器的完整功能链路"""
    validator = Validator()

    print('=== 测试获取待验证预测 ===')
    pending = validator.get_pending_predictions()
    print(f'待验证预测数量: {len(pending)}')

    print('\n=== 测试执行验证 ===')
    result = validator.run_full_validation()
    print(f'验证状态: {result["status"]}')
    print(f'验证数量: {result["verified_count"]}')

    print('\n=== 性能统计 ===')
    stats = result.get('stats', {})
    if stats.get('total', 0) > 0:
        print(f'总预测次数: {stats["total"]}')
        print(f'完全猜中: {stats["total_matched"]}')
        print(f'平均准确率: {stats["avg_accuracy"]}%')
    else:
        print('暂无验证数据')

    print('\n=== 性能报告 ===')
    print(result.get('report', ''))

    return result


if __name__ == '__main__':
    test_validator()
