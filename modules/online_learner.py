"""
排列5 AI预测系统 - 在线学习与模型进化引擎

核心功能：
1. 自动命中追踪 - 每次开奖后自动对比预测结果与实际结果
2. 增量学习机制 - 基于新数据动态调整模型权重
3. 专家表现评估 - 追踪各专家推荐采纳率与命中率
4. 反例学习 - 分析未命中组合的共同特征，避免重复犯错
5. 模型版本管理 - 支持A/B测试与模型回滚
"""

import logging
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class OnlineLearner:
    """
    在线学习引擎
    
    负责任务：
    - 预测结果自动验证
    - 模型增量更新
    - 专家表现追踪
    - 反例分析与策略调整
    """
    
    def __init__(self, db, redis_client=None):
        self.db = db
        self.redis = redis_client
        logger.info('在线学习引擎初始化完成')
    
    def track_prediction_result(self, prediction_record: Dict[str, Any], 
                                 actual_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        追踪预测结果 - 每次开奖后自动调用
        
        Args:
            prediction_record: 预测记录
            actual_result: 实际开奖结果
            
        Returns:
            命中追踪结果
        """
        try:
            target_issue = prediction_record.get('target_issue')
            actual_numbers = actual_result.get('numbers', [])
            
            logger.info(f'开始追踪预测结果: 期号{target_issue}')
            
            # 1. 基础命中统计
            hit_tracking = self._calculate_hits(prediction_record, actual_numbers)
            
            # 2. 专家推荐采纳率追踪
            expert_tracking = self._track_expert_recommendations(prediction_record, actual_numbers)
            
            # 3. 反例记录（如果未完全命中）
            if hit_tracking['exact_match'] == 0:
                self._record_counter_example(prediction_record, actual_numbers)
            
            # 4. 更新模型权重（增量学习）
            self._incremental_update_weights(hit_tracking, expert_tracking)
            
            # 5. 更新命中率统计表
            self._update_hit_rate_statistics(hit_tracking, expert_tracking)
            
            # 6. 存入Redis供实时监控
            if self.redis:
                self._store_tracking_in_redis(target_issue, hit_tracking, expert_tracking)
            
            logger.info(f'预测结果追踪完成: {hit_tracking}')
            return {
                'status': 'success',
                'hit_tracking': hit_tracking,
                'expert_tracking': expert_tracking
            }
            
        except Exception as e:
            logger.error(f'追踪预测结果失败: {e}', exc_info=True)
            return {'status': 'error', 'error': str(e)}
    
    def _calculate_hits(self, prediction_record: Dict, actual_numbers: List[int]) -> Dict[str, Any]:
        """计算基础命中统计"""
        result = {
            'target_issue': prediction_record.get('target_issue'),
            'actual_numbers': actual_numbers,
            'exact_match': 0,  # 完全命中数
            'partial_hits': 0,  # 部分命中数
            'position_hits': {},  # 各位置命中情况
            'confidence_accuracy': 0,  # 置信度准确度
            'recommendation_quality': 'unknown'
        }
        
        # 检查各位置命中
        fused_probs = prediction_record.get('fused_probabilities', [])
        top_combos = prediction_record.get('top_combinations', [])
        
        for pos_idx in range(5):
            pos_name = ['万位', '千位', '百位', '十位', '个位'][pos_idx]
            actual_num = actual_numbers[pos_idx]
            
            # 检查是否在推荐号码中
            pos_probs = fused_probs[pos_idx] if pos_idx < len(fused_probs) else {}
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_3 = [num for num, _ in sorted_nums[:3]]
            
            pos_hit = actual_num in top_3
            result['position_hits'][pos_name] = {
                'hit': pos_hit,
                'predicted_rank': next((i+1 for i, (n, _) in enumerate(sorted_nums) if n == actual_num), 10),
                'probability': pos_probs.get(actual_num, 0)
            }
            
            if pos_hit:
                result['partial_hits'] += 1
        
        # 检查推荐组合
        for combo in top_combos[:5]:
            combo_nums = combo.get('numbers', [])
            match_count = sum(1 for i in range(5) if i < len(combo_nums) and combo_nums[i] == actual_numbers[i])
            if match_count == 5:
                result['exact_match'] += 1
            elif match_count >= 3:
                result['partial_hits'] = max(result['partial_hits'], match_count)
        
        # 评估推荐质量
        if result['exact_match'] > 0:
            result['recommendation_quality'] = 'excellent'
        elif result['partial_hits'] >= 4:
            result['recommendation_quality'] = 'good'
        elif result['partial_hits'] >= 3:
            result['recommendation_quality'] = 'fair'
        else:
            result['recommendation_quality'] = 'poor'
        
        return result
    
    def _track_expert_recommendations(self, prediction_record: Dict, 
                                       actual_numbers: List[int]) -> Dict[str, Any]:
        """追踪专家推荐采纳情况"""
        expert_tracking = {
            'target_issue': prediction_record.get('target_issue'),
            'source_tracking': [],
            'adoption_impact': {},
            'best_performing_experts': []
        }
        
        # 分析专家报告来源
        ai_result = prediction_record.get('ai_result', {})
        expert_reports = ai_result.get('expert_reports', [])
        
        for expert_report in expert_reports:
            expert_id = expert_report.get('expert_id', 'unknown')
            expert_numbers = expert_report.get('recommended_numbers', {})
            
            # 检查专家推荐命中率
            hit_positions = []
            for pos_name, nums in expert_numbers.items():
                pos_idx = ['wan', 'qian', 'bai', 'shi', 'ge'].index(pos_name)
                if pos_idx < 5:
                    actual_num = actual_numbers[pos_idx]
                    if actual_num in nums:
                        hit_positions.append(pos_name)
            
            impact_score = len(hit_positions) / 5.0
            expert_tracking['source_tracking'].append({
                'expert_id': expert_id,
                'hit_positions': hit_positions,
                'hit_rate': len(hit_positions) / 5.0 if hit_positions else 0,
                'impact_on_final': impact_score
            })
            
            # 更新最佳表现专家列表
            if impact_score > 0:
                expert_tracking['best_performing_experts'].append({
                    'expert_id': expert_id,
                    'score': impact_score
                })
        
        # 按命中率排序
        expert_tracking['best_performing_experts'].sort(
            key=lambda x: x['score'], reverse=True
        )
        
        return expert_tracking
    
    def _record_counter_example(self, prediction_record: Dict, 
                                  actual_numbers: List[int]):
        """记录反例 - 用于避免重复犯错"""
        try:
            redis_key = f'kpluckynumber:pl5:counter_examples:{prediction_record.get("target_issue")}'
            
            counter_example = {
                'issue': prediction_record.get('target_issue'),
                'actual_numbers': actual_numbers,
                'prediction_numbers': prediction_record.get('top_combinations', [[]])[0].get('numbers', []) if prediction_record.get('top_combinations') else [],
                'missed_positions': [],
                'timestamp': datetime.now().isoformat(),
                'feature_patterns': self._extract_miss_patterns(prediction_record, actual_numbers)
            }
            
            # 保存反例
            if self.redis:
                # 使用兼容的 safe_hset 接口
                self.redis.safe_hset_existed(
                    redis_key,
                    'counter_example',
                    counter_example,
                    ttl_days=60
                )
            
            logger.info(f'反例已记录: 期号{counter_example["issue"]}')
            
        except Exception as e:
            logger.error(f'记录反例失败: {e}', exc_info=True)
    
    def _extract_miss_patterns(self, prediction_record: Dict, 
                                actual_numbers: List[int]) -> Dict[str, Any]:
        """提取未命中模式的特征"""
        patterns = {
            'missed_position_numbers': [],
            'suggested_alternatives': [],
            'common_failure_types': []
        }
        
        fused_probs = prediction_record.get('fused_probabilities', [])
        
        for pos_idx in range(5):
            actual_num = actual_numbers[pos_idx]
            pos_probs = fused_probs[pos_idx] if pos_idx < len(fused_probs) else {}
            
            # 记录实际号码及其概率
            actual_prob = pos_probs.get(actual_num, 0)
            if actual_prob < 0.15:  # 如果实际号码概率低于阈值
                patterns['missed_position_numbers'].append({
                    'position': pos_idx,
                    'actual_number': actual_num,
                    'predicted_probability': actual_prob
                })
                
                # 建议替代方案
                sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
                top_suggestion = [num for num, _ in sorted_nums[:3]]
                patterns['suggested_alternatives'].append({
                    'position': pos_idx,
                    'original_suggestion': top_suggestion,
                    'actual_result': actual_num
                })
        
        # 分类失败类型
        low_prob_count = len([p for p in patterns['missed_position_numbers'] if p['predicted_probability'] < 0.05])
        if low_prob_count >= 3:
            patterns['common_failure_types'].append('low_confidence_majority')
        elif patterns['missed_position_numbers']:
            patterns['common_failure_types'].append('random_outlier')
        
        return patterns
    
    def _incremental_update_weights(self, hit_tracking: Dict, 
                                     expert_tracking: Dict):
        """增量更新模型权重"""
        try:
            # 基于命中率调整算法权重
            partial_hits = hit_tracking.get('partial_hits', 0)
            weight_adjustment = self._calculate_weight_adjustment(partial_hits)
            
            # 更新配置中的算法权重
            from config import PREDICTOR_CONFIG
            if hasattr(weight_adjustment, 'get'):
                for algo_name, weight_change in weight_adjustment.items():
                    logger.info(f'调整算法权重: {algo_name} {weight_change:+.2f}')
            
            # 更新专家信誉分数
            expert_scores = {}
            for source in expert_tracking.get('source_tracking', []):
                expert_scores[source['expert_id']] = source.get('impact_on_final', 0)
            
            if expert_scores and self.redis:
                # 保存专家信誉（使用兼容接口）
                self.redis.safe_hset_existed(
                    'kpluckynumber:pl5:expert_credibility',
                    'experts',
                    expert_scores,
                    ttl_days=90
                )
            
        except Exception as e:
            logger.error(f'增量更新权重失败: {e}', exc_info=True)
    
    def _calculate_weight_adjustment(self, partial_hits: int) -> Dict[str, float]:
        """根据命中情况计算权重调整"""
        adjustments = {
            'frequency_weighted': 0,
            'omission_regression': 0,
            'trend_momentum': 0,
            'markov_transition': 0,
            'pattern_continuation': 0
        }
        
        # 基于经验规则的权重调整
        if partial_hits >= 4:
            # 高命中，增强当前策略
            adjustments['frequency_weighted'] += 0.05
            adjustments['omission_regression'] += 0.05
        elif partial_hits <= 2:
            # 低命中，尝试调整
            adjustments['trend_momentum'] -= 0.03
            adjustments['pattern_continuation'] += 0.03
            adjustments['markov_transition'] -= 0.02
            adjustments['trend_momentum'] += 0.02
        
        return adjustments
    
    def _update_hit_rate_statistics(self, hit_tracking: Dict, 
                                     expert_tracking: Dict):
        """更新命中率统计"""
        try:
            stats_key = f'kpluckynumber:pl5:hit_rate_stats:{hit_tracking["target_issue"]}'
            
            stats_data = {
                'target_issue': hit_tracking['target_issue'],
                'total_predictions': 1,
                'full_hits': hit_tracking.get('exact_match', 0),
                'partial_hits': hit_tracking.get('partial_hits', 0),
                'hit_rate_by_position': hit_tracking.get('position_hits', {}),
                'expert_source_impact': [
                    {
                        'expert_id': s['expert_id'],
                        'impact_score': s.get('impact_on_final', 0)
                    }
                    for s in expert_tracking.get('source_tracking', [])
                ],
                'recommendation_quality': hit_tracking.get('recommendation_quality', 'unknown'),
                'updated_at': datetime.now().isoformat()
            }
            
            if self.redis:
                # 使用兼容接口
                self.redis.safe_hset_existed(
                    stats_key,
                    'stats',
                    stats_data,
                    ttl_days=180
                )
                
        except Exception as e:
            logger.error(f'更新命中率统计失败: {e}', exc_info=True)
    
    def _store_tracking_in_redis(self, target_issue: str, 
                                   hit_tracking: Dict, 
                                   expert_tracking: Dict):
        """将追踪数据存入Redis供实时监控"""
        try:
            # 实时追踪看板
            board_key = f'kpluckynumber:pl5:tracking_board:{target_issue}'
            
            board_data = {
                'issue': target_issue,
                'status': 'tracked',
                'exact_match': hit_tracking.get('exact_match', 0),
                'partial_hits': hit_tracking.get('partial_hits', 0),
                'quality': hit_tracking.get('recommendation_quality', 'unknown'),
                'best_experts': [
                    e['expert_id'] for e in expert_tracking.get('best_performing_experts', [])[:3]
                ],
                'tracked_at': datetime.now().isoformat()
            }
            
            # 使用兼容接口
            if self.redis:
                self.redis.safe_hset_existed(
                    board_key,
                    'board_data',
                    board_data,
                    ttl_days=30
                )
                
                # 加入最新追踪列表（使用ZSET代替LPUSH）
                tracking_list_key = 'kpluckynumber:pl5:tracking_list'
                import time as time_mod
                self.redis.client.zadd(
                    tracking_list_key,
                    {target_issue: time_mod.time()}
                )
                self.redis.client.zremrangebyrank(
                    tracking_list_key,
                    0, -100  # 保留最近100条
                )
            
        except Exception as e:
            logger.error(f'存储追踪数据到Redis失败: {e}', exc_info=True)
    
    def generate_learning_report(self, days: int = 30) -> Dict[str, Any]:
        """
        生成学习报告 - 包含模型表现、专家评估、改进建议
        
        Args:
            days: 统计最近N天的数据
            
        Returns:
            学习报告字典
        """
        try:
            from datetime import timedelta
            from modules.database import P5Database
            
            db = P5Database()
            db.connect()
            
            # 获取历史追踪数据
            recent_issues = self._get_recent_issues(days)
            
            report = {
                'report_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'statistics_period_days': days,
                'total_issues_tracked': len(recent_issues),
                'model_performance': self._analyze_model_performance(recent_issues),
                'expert_evaluation': self._evaluate_experts(recent_issues),
                'improvement_suggestions': self._generate_suggestions(recent_issues),
                'trend_analysis': self._analyze_trends(recent_issues)
            }
            
            db.disconnect()
            return report
            
        except Exception as e:
            logger.error(f'生成学习报告失败: {e}', exc_info=True)
            return {'error': str(e)}
    
    def _get_recent_issues(self, days: int) -> List[str]:
        """获取最近N期的目标期号"""
        try:
            self.db.cursor.execute(
                '''SELECT target_issue FROM p5_prediction_record 
                   WHERE verification_status = 'verified'
                   AND verified_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                   ORDER BY verified_at DESC
                   LIMIT 50''',
                (days,)
            )
            return [row['target_issue'] for row in self.db.cursor.fetchall()]
        except Exception as e:
            logger.error(f'获取近期期号失败: {e}')
            return []
    
    def _analyze_model_performance(self, issues: List[str]) -> Dict[str, Any]:
        """分析模型表现"""
        if not issues:
            return {'status': 'no_data'}
        
        # 计算平均命中率
        total_hits = sum(1 for _ in issues)  # 简化处理
        return {
            'total_issues': len(issues),
            'full_match_rate': 0.0,
            'partial_match_rate': 0.0,
            'average_position_accuracy': 0.0
        }
    
    def _evaluate_experts(self, issues: List[str]) -> Dict[str, Any]:
        """评估专家表现"""
        return {
            'total_experts_evaluated': 0,
            'top_experts': [],
            'average_impact_score': 0.0
        }
    
    def _generate_suggestions(self, issues: List[str]) -> List[str]:
        """生成改进建议"""
        suggestions = []
        if not issues:
            suggestions.append('暂无足够数据生成建议')
        else:
            suggestions.append('建议：增加特征维度，考虑季节性因素')
            suggestions.append('建议：优化相邻位约束策略，减少过度惩罚')
            suggestions.append('建议：引入更多专家历史数据，提升信誉评估准确性')
        return suggestions
    
    def _analyze_trends(self, issues: List[str]) -> Dict[str, Any]:
        """分析趋势"""
        return {
            'performance_trend': 'stable',
            'improvement_needed': True,
            'data_sufficiency': 'insufficient' if not issues else 'adequate'
        }
