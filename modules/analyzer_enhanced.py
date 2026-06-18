"""
七星彩增强版数据分析器模块

整合历史数据和走势图遗漏值数据，提供更精准的分析和预测
核心增强：
1. 遗漏值趋势分析 - 基于走势图实时遗漏值
2. 冷热转换预测 - 识别冷热号转换周期
3. 遗漏回补模型 - 预测高遗漏号码的回补时机
4. 走势斜率分析 - 识别上升/下降趋势
5. 综合概率模型 - 融合频率、遗漏、趋势多维度
"""

import numpy as np
from collections import defaultdict, Counter
from scipy import stats
import logging
import json

logger = logging.getLogger(__name__)


class QXCAnalyzerEnhanced:
    """
    七星彩增强版数据分析器
    
    整合历史数据和走势图遗漏值，提供多维度分析
    """
    
    def __init__(self, history_data, trend_data=None):
        """
        初始化分析器
        
        Args:
            history_data: 历史开奖数据列表
            trend_data: 走势图数据列表（含遗漏值）
        """
        self.history_data = history_data
        self.trend_data = trend_data or []
        self.numbers = [d['numbers'] for d in history_data]
        
        # 构建遗漏值查找表（从trend_data）
        self.omission_lookup = {}
        if trend_data:
            for item in trend_data:
                self.omission_lookup[item['issue']] = item.get('omissions', {})
        
        logger.info(f'七星彩分析器初始化完成，历史数据: {len(history_data)} 条，走势数据: {len(trend_data)} 条')
    
    def analyze_omission_trend(self, position=None, number=None, recent_periods=50):
        """
        分析遗漏值趋势 - 核心增强功能
        
        从走势图数据中提取每个位置每个号码的遗漏值序列
        分析遗漏值的变化趋势，识别即将回补的号码
        
        Args:
            position: 指定位置（0-6），None则分析所有位置
            number: 指定号码（前6位0-9，第7位0-14），None则分析所有号码
            recent_periods: 分析最近多少期
        
        Returns:
            遗漏值趋势分析结果
        """
        results = {}
        
        positions = [position] if position is not None else range(7)
        
        for pos in positions:
            pos_name = f'pos{pos + 1}'
            results[pos] = {}
            
            # 根据位置确定号码范围
            if pos < 6:
                num_range = range(10)  # 前6位0-9
            else:
                num_range = range(15)  # 第7位特别号0-14
            
            numbers = [number] if number is not None else num_range
            
            for num in numbers:
                # 提取该位置该号码的遗漏值序列
                omission_sequence = []
                issues = []
                
                for item in self.trend_data[-recent_periods:]:
                    omissions = item.get('omissions', {})
                    pos_omissions = omissions.get(pos_name, {})
                    
                    if num in pos_omissions:
                        omission_sequence.append(pos_omissions[num])
                        issues.append(item['issue'])
                
                if len(omission_sequence) < 5:
                    continue
                
                # 分析遗漏值趋势
                current_omission = omission_sequence[-1]
                
                # 计算遗漏值统计特征
                avg_omission = np.mean(omission_sequence)
                max_omission = max(omission_sequence)
                min_omission = min(omission_sequence)
                std_omission = np.std(omission_sequence)
                
                # 计算遗漏值斜率（趋势）
                if len(omission_sequence) >= 10:
                    x = np.arange(len(omission_sequence))
                    slope, intercept, r_value, p_value, std_err = stats.linregress(x, omission_sequence)
                else:
                    slope = 0
                    r_value = 0
                
                # 遗漏值分层判断
                omission_level = self._classify_omission_level(current_omission, avg_omission, max_omission)
                
                # 回补概率计算
                rebound_probability = self._calculate_rebound_probability(
                    current_omission, avg_omission, max_omission, slope
                )
                
                results[pos][num] = {
                    'current_omission': current_omission,
                    'average_omission': round(avg_omission, 2),
                    'max_omission': max_omission,
                    'min_omission': min_omission,
                    'std_omission': round(std_omission, 2),
                    'omission_level': omission_level,
                    'trend_slope': round(slope, 4),
                    'trend_r2': round(r_value ** 2, 4),
                    'rebound_probability': round(rebound_probability, 4),
                    'omission_sequence': omission_sequence[-20:]  # 最近20期序列
                }
        
        return results
    
    def _classify_omission_level(self, current, average, maximum):
        """
        遗漏值分层分类
        
        Args:
            current: 当前遗漏值
            average: 平均遗漏值
            maximum: 最大遗漏值
        
        Returns:
            分层级别：'cold'(冷号), 'cool'(温冷), 'warm'(温号), 'hot'(热号)
        """
        if maximum == 0:
            return 'unknown'
        
        ratio = current / maximum if maximum > 0 else 0
        
        if current >= average * 2 or ratio >= 0.7:
            return 'cold'
        elif current >= average * 1.5 or ratio >= 0.5:
            return 'cool'
        elif current <= average * 0.5 or ratio <= 0.2:
            return 'hot'
        else:
            return 'warm'
    
    def _calculate_rebound_probability(self, current, average, maximum, slope):
        """
        计算遗漏回补概率
        
        基于当前遗漏值、平均遗漏值、最大遗漏值和趋势斜率
        计算该号码在下一期出现的概率
        
        Args:
            current: 当前遗漏值
            average: 平均遗漏值
            maximum: 最大遗漏值
            slope: 遗漏值趋势斜率
        
        Returns:
            回补概率 (0-1)
        """
        if maximum == 0:
            return 0.1
        
        # 基础概率：基于遗漏值与平均值的偏离
        if current <= average:
            base_prob = 0.1 + (current / average) * 0.1 if average > 0 else 0.1
        else:
            # 遗漏值超过平均值，回补概率增加
            excess = current - average
            max_excess = maximum - average if maximum > average else 1
            base_prob = 0.2 + (excess / max_excess) * 0.5 if max_excess > 0 else 0.2
        
        # 趋势调整：斜率为正表示遗漏值在增加，应增加回补概率
        trend_adjustment = 0
        if slope > 0.5:
            trend_adjustment = 0.15  # 遗漏值快速增加，即将回补
        elif slope > 0.1:
            trend_adjustment = 0.08
        elif slope < -0.3:
            trend_adjustment = -0.1  # 遗漏值在减少，可能刚回补过
        
        # 最大遗漏值调整：接近历史最大遗漏时，概率大幅增加
        max_adjustment = 0
        if maximum > 0:
            near_max_ratio = current / maximum
            if near_max_ratio >= 0.8:
                max_adjustment = 0.2
            elif near_max_ratio >= 0.6:
                max_adjustment = 0.1
        
        probability = base_prob + trend_adjustment + max_adjustment
        return min(max(probability, 0.05), 0.95)  # 限制在0.05-0.95之间
    
    def analyze_cold_hot_transition(self, recent_periods=30):
        """
        分析冷热号转换周期
        
        识别哪些号码正在从冷转热，或从热转冷
        
        Args:
            recent_periods: 分析最近多少期
        
        Returns:
            冷热转换分析结果
        """
        transitions = {}
        
        for pos in range(7):
            pos_name = f'pos{pos + 1}'
            transitions[pos] = {}
            
            # 根据位置确定号码范围
            if pos < 6:
                num_range = range(10)
            else:
                num_range = range(15)
            
            for num in num_range:
                # 提取近期遗漏序列
                omission_sequence = []
                for item in self.trend_data[-recent_periods:]:
                    omissions = item.get('omissions', {})
                    pos_omissions = omissions.get(pos_name, {})
                    if num in pos_omissions:
                        omission_sequence.append(pos_omissions[num])
                
                if len(omission_sequence) < 10:
                    continue
                
                # 分前后两段比较
                mid = len(omission_sequence) // 2
                early_avg = np.mean(omission_sequence[:mid])
                late_avg = np.mean(omission_sequence[mid:])
                
                # 判断转换方向
                if early_avg > late_avg * 1.5:
                    transition = 'cold_to_hot'  # 遗漏值减少，冷转热
                    strength = (early_avg - late_avg) / early_avg if early_avg > 0 else 0
                elif late_avg > early_avg * 1.5:
                    transition = 'hot_to_cold'  # 遗漏值增加，热转冷
                    strength = (late_avg - early_avg) / late_avg if late_avg > 0 else 0
                else:
                    transition = 'stable'
                    strength = 0
                
                # 计算转换置信度
                confidence = min(strength * 2, 1.0) if strength > 0 else 0
                
                transitions[pos][num] = {
                    'transition_type': transition,
                    'strength': round(strength, 4),
                    'confidence': round(confidence, 4),
                    'early_avg_omission': round(early_avg, 2),
                    'late_avg_omission': round(late_avg, 2),
                    'current_omission': omission_sequence[-1] if omission_sequence else 0
                }
        
        return transitions
    
    def analyze_trend_slope(self, position=None, number=None, window=10):
        """
        分析走势斜率 - 识别上升/下降趋势
        
        Args:
            position: 指定位置
            number: 指定号码
            window: 计算斜率的窗口期数
        
        Returns:
            斜率分析结果
        """
        results = {}
        
        positions = [position] if position is not None else range(7)
        
        for pos in positions:
            pos_name = f'pos{pos + 1}'
            results[pos] = {}
            
            # 根据位置确定号码范围
            if pos < 6:
                num_range = range(10)
            else:
                num_range = range(15)
            
            numbers = [number] if number is not None else num_range
            
            for num in numbers:
                omission_sequence = []
                for item in self.trend_data:
                    omissions = item.get('omissions', {})
                    pos_omissions = omissions.get(pos_name, {})
                    if num in pos_omissions:
                        omission_sequence.append(pos_omissions[num])
                
                if len(omission_sequence) < window:
                    continue
                
                # 计算滑动窗口斜率
                slopes = []
                for i in range(len(omission_sequence) - window + 1):
                    x = np.arange(window)
                    y = omission_sequence[i:i+window]
                    slope, _, r_value, _, _ = stats.linregress(x, y)
                    slopes.append({
                        'slope': slope,
                        'r2': r_value ** 2,
                        'start_issue': i
                    })
                
                if not slopes:
                    continue
                
                # 最新斜率
                latest_slope = slopes[-1]['slope']
                latest_r2 = slopes[-1]['r2']
                
                # 斜率趋势判断
                if latest_slope > 0.3 and latest_r2 > 0.5:
                    trend_direction = 'rising_strong'
                elif latest_slope > 0.1:
                    trend_direction = 'rising_weak'
                elif latest_slope < -0.3 and latest_r2 > 0.5:
                    trend_direction = 'falling_strong'
                elif latest_slope < -0.1:
                    trend_direction = 'falling_weak'
                else:
                    trend_direction = 'flat'
                
                results[pos][num] = {
                    'latest_slope': round(latest_slope, 4),
                    'latest_r2': round(latest_r2, 4),
                    'trend_direction': trend_direction,
                    'avg_slope': round(np.mean([s['slope'] for s in slopes]), 4),
                    'slope_volatility': round(np.std([s['slope'] for s in slopes]), 4)
                }
        
        return results
    
    def calculate_enhanced_probability(self, position, number, 
                                       frequency_weight=0.25,
                                       omission_weight=0.35,
                                       trend_weight=0.25,
                                       transition_weight=0.15):
        """
        计算增强版综合概率
        
        融合频率、遗漏值、趋势、冷热转换四个维度
        
        Args:
            position: 位置
            number: 号码
            frequency_weight: 频率权重
            omission_weight: 遗漏权重
            trend_weight: 趋势权重
            transition_weight: 转换权重
        
        Returns:
            综合概率值
        """
        # 根据位置确定理论概率
        if position < 6:
            theory_prob = 0.1  # 前6位理论概率 1/10
        else:
            theory_prob = 1/15  # 第7位理论概率 1/15
        
        # 1. 频率概率（基于历史数据）
        pos_numbers = [n[position] for n in self.numbers]
        total = len(pos_numbers)
        count = pos_numbers.count(number)
        observed_prob = count / total if total > 0 else 0
        frequency_score = observed_prob / theory_prob if theory_prob > 0 else 0
        
        # 2. 遗漏概率（基于走势图数据）
        omission_data = self.analyze_omission_trend(position, number, recent_periods=50)
        if position in omission_data and number in omission_data[position]:
            omission_info = omission_data[position][number]
            rebound_prob = omission_info['rebound_probability']
            omission_level = omission_info['omission_level']
            
            # 根据遗漏级别调整
            level_multiplier = {
                'cold': 1.5,
                'cool': 1.2,
                'warm': 1.0,
                'hot': 0.8,
                'unknown': 1.0
            }
            omission_score = rebound_prob * level_multiplier.get(omission_level, 1.0)
        else:
            omission_score = 0.1
        
        # 3. 趋势概率
        trend_data = self.analyze_trend_slope(position, number)
        if position in trend_data and number in trend_data[position]:
            trend_info = trend_data[position][number]
            direction = trend_info['trend_direction']
            
            # 上升趋势（遗漏值增加）= 即将回补 = 高概率
            trend_scores = {
                'rising_strong': 1.5,
                'rising_weak': 1.2,
                'flat': 1.0,
                'falling_weak': 0.8,
                'falling_strong': 0.6
            }
            trend_score = trend_scores.get(direction, 1.0)
        else:
            trend_score = 1.0
        
        # 4. 冷热转换概率
        transition_data = self.analyze_cold_hot_transition()
        if position in transition_data and number in transition_data[position]:
            trans_info = transition_data[position][number]
            trans_type = trans_info['transition_type']
            confidence = trans_info['confidence']
            
            # 冷转热 = 高概率，热转冷 = 低概率
            if trans_type == 'cold_to_hot':
                transition_score = 1.0 + confidence * 0.5
            elif trans_type == 'hot_to_cold':
                transition_score = 1.0 - confidence * 0.3
            else:
                transition_score = 1.0
        else:
            transition_score = 1.0
        
        # 综合概率计算
        total_weight = frequency_weight + omission_weight + trend_weight + transition_weight
        
        combined_prob = (
            frequency_score * frequency_weight +
            omission_score * omission_weight +
            trend_score * trend_weight +
            transition_score * transition_weight
        ) / total_weight
        
        # 归一化到合理范围
        final_prob = min(max(combined_prob * theory_prob, 0.02), 0.3)
        
        return {
            'probability': round(final_prob, 4),
            'frequency_score': round(frequency_score, 4),
            'omission_score': round(omission_score, 4),
            'trend_score': round(trend_score, 4),
            'transition_score': round(transition_score, 4),
            'components': {
                'frequency': {'weight': frequency_weight, 'score': round(frequency_score, 4)},
                'omission': {'weight': omission_weight, 'score': round(omission_score, 4)},
                'trend': {'weight': trend_weight, 'score': round(trend_score, 4)},
                'transition': {'weight': transition_weight, 'score': round(transition_score, 4)}
            }
        }
    
    def generate_optimal_numbers(self, top_n=5):
        """
        生成最优号码推荐
        
        基于增强版概率模型，为每个位置推荐最优号码
        
        Args:
            top_n: 每个位置推荐的号码数量
        
        Returns:
            每个位置的推荐号码及概率
        """
        recommendations = {}
        
        for pos in range(7):
            pos_probs = []
            
            # 根据位置确定号码范围
            if pos < 6:
                num_range = range(10)
            else:
                num_range = range(15)
            
            for num in num_range:
                prob_result = self.calculate_enhanced_probability(pos, num)
                pos_probs.append({
                    'number': num,
                    'probability': prob_result['probability'],
                    'details': prob_result
                })
            
            # 按概率排序
            pos_probs.sort(key=lambda x: x['probability'], reverse=True)
            
            recommendations[pos] = {
                'top_numbers': pos_probs[:top_n],
                'all_numbers': pos_probs
            }
        
        return recommendations
    
    def analyze_comprehensive(self):
        """
        执行综合分析
        
        整合所有分析方法，生成完整的分析报告
        
        Returns:
            综合分析结果
        """
        logger.info('开始执行七星彩增强版综合分析')
        
        # 1. 遗漏值趋势分析
        omission_trend = self.analyze_omission_trend()
        
        # 2. 冷热转换分析
        transitions = self.analyze_cold_hot_transition()
        
        # 3. 走势斜率分析
        trend_slopes = self.analyze_trend_slope()
        
        # 4. 生成最优号码
        optimal = self.generate_optimal_numbers()
        
        # 5. 生成组合推荐
        combinations = self._generate_combinations(optimal)
        
        return {
            'omission_trend': omission_trend,
            'cold_hot_transitions': transitions,
            'trend_slopes': trend_slopes,
            'optimal_numbers': optimal,
            'recommended_combinations': combinations
        }
    
    def _generate_combinations(self, optimal_data, max_combinations=10):
        """
        基于最优号码生成组合推荐
        
        Args:
            optimal_data: 最优号码数据
            max_combinations: 最大组合数
        
        Returns:
            推荐组合列表
        """
        combinations = []
        
        # 取每个位置概率最高的2个号码（七星彩组合数太多，减少选择）
        top_numbers_per_pos = []
        for pos in range(7):
            top2 = [item['number'] for item in optimal_data[pos]['top_numbers'][:2]]
            top_numbers_per_pos.append(top2)
        
        # 生成组合（笛卡尔积的前N个）
        from itertools import product
        count = 0
        for combo in product(*top_numbers_per_pos):
            if count >= max_combinations:
                break
            
            # 计算组合的综合概率
            combo_prob = 1.0
            for pos, num in enumerate(combo):
                for item in optimal_data[pos]['all_numbers']:
                    if item['number'] == num:
                        combo_prob *= item['probability']
                        break
            
            combinations.append({
                'numbers': list(combo),
                'combined_probability': round(combo_prob, 6)
            })
            count += 1
        
        # 按概率排序
        combinations.sort(key=lambda x: x['combined_probability'], reverse=True)
        
        return combinations


def test_analyzer():
    """测试分析器功能"""
    # 模拟一些测试数据
    history_data = []
    trend_data = []
    
    # 生成模拟历史数据
    np.random.seed(42)
    for i in range(100):
        numbers = list(np.random.randint(0, 10, 6)) + [np.random.randint(0, 15)]
        history_data.append({
            'issue': f'2026{i:03d}',
            'numbers': numbers
        })
    
    # 生成模拟走势数据（含遗漏值）
    for i in range(50):
        issue = f'2026{i+50:03d}'
        numbers = list(np.random.randint(0, 10, 6)) + [np.random.randint(0, 15)]
        
        omissions = {}
        for pos_idx in range(7):
            pos_name = f'pos{pos_idx + 1}'
            pos_omissions = {}
            num_range = 10 if pos_idx < 6 else 15
            for num in range(num_range):
                if num == numbers[pos_idx]:
                    pos_omissions[num] = 0
                else:
                    pos_omissions[num] = np.random.randint(1, 30)
            omissions[pos_name] = pos_omissions
        
        trend_data.append({
            'issue': issue,
            'numbers': numbers,
            'omissions': omissions
        })
    
    # 创建分析器
    analyzer = QXCAnalyzerEnhanced(history_data, trend_data)
    
    # 测试遗漏值趋势分析
    print('=== 测试七星彩遗漏值趋势分析 ===')
    omission_result = analyzer.analyze_omission_trend(position=0, number=5)
    print(json.dumps(omission_result, indent=2, ensure_ascii=False))
    
    # 测试冷热转换分析
    print('\n=== 测试七星彩冷热转换分析 ===')
    transition_result = analyzer.analyze_cold_hot_transition()
    print(f'转换分析完成，数据量: {len(transition_result)}')
    
    # 测试综合概率
    print('\n=== 测试七星彩综合概率计算 ===')
    prob_result = analyzer.calculate_enhanced_probability(0, 5)
    print(json.dumps(prob_result, indent=2, ensure_ascii=False))
    
    # 测试最优号码生成
    print('\n=== 测试七星彩最优号码生成 ===')
    optimal = analyzer.generate_optimal_numbers(top_n=3)
    for pos, data in optimal.items():
        print(f'位置 {pos}: {[item["number"] for item in data["top_numbers"]]}')


if __name__ == '__main__':
    test_analyzer()
