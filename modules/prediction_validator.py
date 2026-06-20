"""
排列5预测验证系统模块

功能：
1. 跟踪每一期AI预测结果
2. 在实际开奖结果公布后，自动比对预测与实际结果
3. 计算命中率、偏差分析、准确率指标
4. 建立完整的AI预测性能评估档案
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/prediction_validator.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5PredictionValidator:
    """
    排列5预测验证器
    
    负责预测结果的验证、统计分析和性能评估
    """
    
    def __init__(self):
        self.db = None
    
    def _load_database(self):
        """延迟加载数据库"""
        if self.db is None:
            from modules.database_p5 import P5Database
            self.db = P5Database()
    
    def verify_prediction(self, report_uuid: str, target_issue: str,
                           actual_numbers: List[int]) -> Dict[str, Any]:
        """
        验证指定预测记录
        
        Args:
            report_uuid: 报告UUID
            target_issue: 目标期号
            actual_numbers: 实际开奖号码 [wan, qian, bai, shi, ge]
        
        Returns:
            验证结果字典
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
        验证所有待验证的预测记录
        
        Returns:
            验证结果列表
        """
        self._load_database()
        
        if not self.db.connect():
            return []
        
        try:
            pending = self.db.get_pending_predictions()
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
        获取预测性能统计
        
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
    
    def get_pending_predictions(self) -> List[Dict[str, Any]]:
        """
        获取待验证的预测列表
        
        Returns:
            待验证预测列表
        """
        self._load_database()
        
        if not self.db.connect():
            return []
        
        try:
            return self.db.get_pending_predictions()
        except Exception as e:
            logger.error(f'获取待验证预测失败: {e}')
            return []
        finally:
            self.db.disconnect()
    
    def generate_performance_report(self) -> str:
        """
        生成性能评估报告
        
        Returns:
            报告文本
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
完全猜中次数: {stats['total_matched']}
平均命中位数: {stats['avg_match']}/5
平均准确率: {stats['avg_accuracy']}%

二、各位置命中率
----------------------------------------
万位命中率: {stats['wan_accuracy']}%
千位命中率: {stats['qian_accuracy']}%
百位命中率: {stats['bai_accuracy']}%
十位命中率: {stats['shi_accuracy']}%
个位命中率: {stats['ge_accuracy']}%

三、综合评估
----------------------------------------
整体命中率: {stats['overall_accuracy']}%

四、命中率分级
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
        
        report += f"预测准确率等级: {level}\n"
        report += f"命中率说明: 命中位数占总位数的百分比\n"
        
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
📊 说明：
- 完全猜中：5个位置全部命中
- 命中率：命中位数 / 5 * 100%
- 数据基于历史预测验证结果统计
========================================
"""
        
        return report
    
    def run_full_validation(self) -> Dict[str, Any]:
        """
        执行完整验证流程
        
        1. 验证所有待验证的预测
        2. 更新性能统计
        3. 生成性能报告
        
        Returns:
            验证结果汇总
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


def test_validator():
    """测试预测验证器"""
    validator = P5PredictionValidator()
    
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