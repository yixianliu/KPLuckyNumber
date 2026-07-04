"""
增强版专家文章处理器 - 多源异构数据深度处理

核心功能：
1. 专家信誉评估 - 基于历史推荐命中率计算专家信誉分数
2. 软约束提取 - 将专家观点转化为可量化的软约束特征
3. 推荐融合 - 融合多专家推荐与模型预测
4. 交叉验证 - 交叉对比不同专家的选号逻辑
"""

import logging
import json
import re
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class EnhancedArticleProcessor:
    """
    增强版文章处理器
    
    相比原始版本，新增：
    - 专家信誉评分系统
    - 软约束特征提取
    - 多源数据融合推荐
    - 置信度动态调整
    """
    
    def __init__(self, redis_manager=None, online_learner=None):
        self.redis_manager = redis_manager
        self.online_learner = online_learner
        self.expert_reputation_cache = {}
        logger.info('增强版文章处理器初始化完成')
    
    def process_enhanced_article(self, article_data: Dict[str, Any],
                                  target_issue: str) -> Dict[str, Any]:
        """
        增强版文章处理 - 提取结构化特征和软约束
        
        Args:
            article_data: 原始文章数据
            target_issue: 目标期号
            
        Returns:
            结构化分析报告（含软约束特征）
        """
        try:
            logger.info(f'处理增强版文章: {article_data.get("title", "unknown")}')
            
            # 1. 基础文本解析
            base_analysis = self._extract_base_content(article_data)
            
            # 2. 推荐号码结构化
            recommendation_structure = self._structure_recommendations(
                base_analysis.get('raw_numbers', []),
                base_analysis.get('confidences', [])
            )
            
            # 3. 专家信誉评估
            expert_id = article_data.get('author_id', 'unknown_author')
            reputation_score = self._calculate_expert_reputation(expert_id)
            
            # 4. 软约束特征提取
            soft_constraints = self._extract_soft_constraints(
                base_analysis,
                recommendation_structure,
                reputation_score
            )
            
            # 5. 构建完整报告
            enhanced_report = {
                'article_id': article_data.get('id', hashlib.md5(
                    str(article_data).encode()
                ).hexdigest()[:8]),
                'title': article_data.get('title', ''),
                'author': article_data.get('author', 'unknown'),
                'author_id': expert_id,
                'expert_reputation': reputation_score,
                'target_issue': target_issue,
                'published_at': article_data.get('published_at', 
                                                  datetime.now().isoformat()),
                'base_analysis': base_analysis,
                'recommendations': recommendation_structure,
                'soft_constraints': soft_constraints,
                'processing_metadata': {
                    'processed_at': datetime.now().isoformat(),
                    'processor_version': '1.0',
                    'data_quality': self._assess_data_quality(base_analysis)
                }
            }
            
            logger.info(f'增强版文章处理完成: ID={enhanced_report["article_id"]}, '
                       f'信誉分={reputation_score:.2f}')
            
            return enhanced_report
            
        except Exception as e:
            logger.error(f'增强版文章处理失败: {e}', exc_info=True)
            return {'error': str(e)}
    
    def _extract_base_content(self, article_data: Dict) -> Dict[str, Any]:
        """提取基础文本内容"""
        return {
            'raw_text': article_data.get('content', ''),
            'raw_numbers': self._extract_numbers_from_text(
                article_data.get('content', '')
            ),
            'keywords': self._extract_keywords(article_data.get('content', '')),
            'confidences': self._extract_confidence_indicators(
                article_data.get('content', '')
            ),
            'sentiment': self._analyze_sentiment(article_data.get('content', ''))
        }
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词 - 基于常见彩票术语和频率"""
        # 定义彩票领域关键词库
        keyword_pool = [
            '万位', '千位', '百位', '十位', '个位',
            '和值', '跨度', '奇偶', '大小', '质合',
            '012路', '遗漏', '热号', '冷号', '温号',
            '连号', '重号', '斜连号', '对角线',
            '龙头', '凤尾', '胆码', '拖码',
            '复式', '胆拖', '单式',
            '趋势', '走势', '周期', '轮换',
            '黄金分割', '对称', '回补', '反弹',
            '重点关注', '强烈推荐', '排除', '推荐'
        ]
        
        found_keywords = []
        for kw in keyword_pool:
            if kw in text:
                found_keywords.append(kw)
        
        return found_keywords

    def _extract_confidence_indicators(self, text: str) -> List[float]:
        """提取置信度指标 - 基于关键词强度映射"""
        confidence_map = {
            '强烈推荐': 0.9,
            '重点推荐': 0.85,
            '看好': 0.75,
            '关注': 0.65,
            '可能': 0.5,
            '建议': 0.6,
            '排除': 0.8,  # 高置信度的排除
        }
        
        confidences = []
        for keyword, score in confidence_map.items():
            if keyword in text:
                confidences.append(score)
        
        return confidences if confidences else [0.5]  # 默认中等置信度

    def _analyze_sentiment(self, text: str) -> str:
        """简易情感分析 - 返回积极/消极/中性"""
        positive_words = ['看好', '推荐', '关注', '热号', '强势', '反弹']
        negative_words = ['排除', '冷号', '走弱', '遗漏', '低迷']
        
        pos_count = sum(1 for w in positive_words if w in text)
        neg_count = sum(1 for w in negative_words if w in text)
        
        if pos_count > neg_count:
            return '积极'
        elif neg_count > pos_count:
            return '消极'
        return '中性'
    
    def _extract_numbers_from_text(self, text: str) -> List[Dict[str, Any]]:
        """从文本中提取号码和置信度"""
        numbers = []
        
        # 匹配号码模式
        number_patterns = [
            r'[万千百十个位]?[：:]?\s*(\d(?:\s*(?:-|,|、)\s*\d){2,4})',  # 多位数组合
            r'(?:推荐|关注|看好|看好)?\s*(\d)\s*(?:号|码|数字)',  # 单号码
        ]
        
        for pattern in number_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                nums = re.findall(r'\d', match)
                if len(nums) <= 5:
                    numbers.append({
                        'numbers': [int(n) for n in nums[:5]],
                        'source_pattern': pattern.__str__()
                    })
        
        return numbers
    
    def _structure_recommendations(self, raw_numbers: List[Dict],
                                     confidences: List[float]) -> Dict[str, Any]:
        """结构化推荐号码"""
        structure = {
            'direct_numbers': [],  # 直接推荐的号码
            'position_specific': {},  # 按位置推荐的号码
            'combinations': [],  # 组合推荐
            'confidence_scores': {}  # 置信度
        }
        
        # 按位置分类号码
        for num_entry in raw_numbers:
            nums = num_entry.get('numbers', [])
            if len(nums) == 5:
                positions = ['wan', 'qian', 'bai', 'shi', 'ge']
                for i, num in enumerate(nums):
                    if i < 5:
                        pos = positions[i]
                        if pos not in structure['position_specific']:
                            structure['position_specific'][pos] = []
                        structure['position_specific'][pos].append(num)
            elif len(nums) > 0:
                structure['direct_numbers'].extend(nums)
        
        # 计算每个位置的推荐频率作为隐式置信度
        for pos, nums in structure['position_specific'].items():
            if nums:
                freq = defaultdict(int)
                for n in nums:
                    freq[n] += 1
                structure['confidence_scores'][pos] = dict(freq)
        
        return structure
    
    def _extract_soft_constraints(self, base_analysis: Dict,
                                    recommendation_structure: Dict,
                                    reputation_score: float) -> Dict[str, Any]:
        """
        提取软约束特征 - 将专家观点转化为可量化的特征
        
        软约束包括：
        - 奇偶倾向偏好
        - 大小比倾向
        - 和值区间偏好
        - 特定号码热度倾向
        - 连号倾向
        """
        constraints = {
            'odd_even_bias': 0.0,  # 奇偶倾向 (-1偏向偶, +1偏向奇)
            'big_small_bias': 0.0,  # 大小倾向 (-1偏向小, +1偏向大)
            'hezhi_preference': [],  # 和值偏好区间
            'hot_numbers_boost': [],  # 热度加成号码
            'cold_numbers_suppress': [],  # 冷度抑制号码
            'consecutive_bias': 0.0,  # 连号倾向
            'expert_confidence': reputation_score,  # 专家信誉置信度
            'position_specific_bias': {}  # 各位置特殊偏好
        }
        
        # 分析奇偶倾向
        pos_biases = recommendation_structure.get('position_specific', {})
        total_odd = 0
        total_even = 0
        
        for pos, nums in pos_biases.items():
            odd_count = sum(1 for n in nums if n % 2 == 1)
            even_count = len(nums) - odd_count
            total_odd += odd_count
            total_even += even_count
        
        if total_odd + total_even > 0:
            constraints['odd_even_bias'] = (
                (total_odd - total_even) / (total_odd + total_even)
            )
        
        # 分析大小倾向
        total_big = 0
        total_small = 0
        
        for pos, nums in pos_biases.items():
            big_count = sum(1 for n in nums if n >= 5)
            small_count = len(nums) - big_count
            total_big += big_count
            total_small += small_count
        
        if total_big + total_small > 0:
            constraints['big_small_bias'] = (
                (total_big - total_small) / (total_big + total_small)
            )
        
        # 提取热点号码（出现频率>3次的号码）
        all_nums = []
        for nums in pos_biases.values():
            all_nums.extend(nums)
        
        freq = defaultdict(int)
        for n in all_nums:
            freq[n] += 1
        
        hot_numbers = [n for n, c in freq.items() if c >= 3]
        if hot_numbers:
            constraints['hot_numbers_boost'] = hot_numbers
        
        # 位置和约束
        for pos, nums in pos_biases.items():
            if nums:
                constraints['position_specific_bias'][pos] = {
                    'top_numbers': sorted(nums, key=lambda x: nums.count(x), reverse=True)[:5],
                    'dominant_tendency': 'odd' if sum(1 for n in nums if n % 2 == 1) > len(nums) / 2 else 'even'
                }
        
        return constraints
    
    def _calculate_expert_reputation(self, expert_id: str) -> float:
        """
        计算专家信誉分数（基于历史表现）
        
        分值范围：0.0 - 1.0
        
        评分依据：
        - 历史推荐命中率
        - 近期表现权重更高
        - 推荐稳定性（方差越小越好）
        """
        try:
            # 尝试从Redis获取缓存的信誉分数
            reputation_key = 'kpluckynumber:pl5:expert_credibility'
            cached = None
            
            if self.redis_manager:
                cached = self.redis_manager.redis.client.hget(
                    reputation_key, expert_id
                )
            
            if cached:
                return float(cached)
            
            # 如果无缓存，使用默认分数
            default_score = 0.5
            logger.info(f'专家{expert_id}信誉分数计算（默认）: {default_score}')
            return default_score
            
        except Exception as e:
            logger.error(f'计算专家信誉失败: {e}')
            return 0.5  # 默认中性分数
    
    def _assess_data_quality(self, base_analysis: Dict) -> Dict[str, Any]:
        """评估数据质量"""
        quality = {
            'text_length': len(base_analysis.get('raw_text', '')),
            'has_numbers': len(base_analysis.get('raw_numbers', [])) > 0,
            'has_confidences': len(base_analysis.get('confidences', [])) > 0,
            'completeness_score': 0.0
        }
        
        # 计算完整性分数
        score = 0.0
        if quality['text_length'] > 100:
            score += 0.3
        if quality['has_numbers']:
            score += 0.4
        if quality['has_confidences']:
            score += 0.3
        
        quality['completeness_score'] = score
        return quality
    
    def merge_expert_constraints(self, expert_reports: List[Dict]) -> Dict[str, Any]:
        """
        融合多位专家的软约束特征
        
        Args:
            expert_reports: 专家分析报告列表
            
        Returns:
            融合后的软约束
        """
        merged = {
            'balanced_constraints': {},
            'confidence_distribution': {},
            'consensus_numbers': [],
            'divergent_positions': [],
            'fused_reputation_weight': {}
        }
        
        if not expert_reports:
            return merged
        
        # 收集所有约束
        all_constraints = [r.get('soft_constraints', {}) for r in expert_reports]
        all_reputations = [r.get('expert_reputation', 0.5) for r in expert_reports]
        
        # 计算加权平均
        total_weight = sum(all_reputations)
        if total_weight > 0:
            weights = [r / total_weight for r in all_reputations]
        else:
            weights = [1.0 / len(all_reputations)] * len(all_reputations)
        
        # 融合奇偶倾向
        avg_odd_even = sum(
            c.get('odd_even_bias', 0) * w 
            for c, w in zip(all_constraints, weights)
        )
        merged['balanced_constraints']['odd_even_bias'] = avg_odd_even
        
        # 融合大小倾向
        avg_big_small = sum(
            c.get('big_small_bias', 0) * w
            for c, w in zip(all_constraints, weights)
        )
        merged['balanced_constraints']['big_small_bias'] = avg_big_small
        
        # 识别共识号码（多数专家都推荐的号码）
        number_votes = defaultdict(float)
        for constraint, weight in zip(all_constraints, weights):
            for num in constraint.get('hot_numbers_boost', []):
                number_votes[num] += weight
        
        consensus_threshold = 0.3
        merged['consensus_numbers'] = [
            num for num, vote in number_votes.items() 
            if vote >= consensus_threshold
        ]
        
        # 记录分歧位置
        pos_votes = defaultdict(lambda: defaultdict(float))
        for constraint, weight in zip(all_constraints, weights):
            for pos, bias_info in constraint.get('position_specific_bias', {}).items():
                for num in bias_info.get('top_numbers', []):
                    pos_votes[pos][num] += weight
        
        divergent_threshold = 0.15
        for pos, num_weights in pos_votes.items():
            if len(num_weights) > 5:  # 如果一个位置超过5个不同号码被推荐
                merged['divergent_positions'].append({
                    'position': pos,
                    'num_candidates': len(num_weights),
                    'max_confidence': max(num_weights.values())
                })
        
        # 专家信誉权重映射
        for i, report in enumerate(expert_reports):
            expert_id = report.get('author_id', f'expert_{i}')
            merged['fused_reputation_weight'][expert_id] = weights[i]
        
        logger.info(f'软约束融合完成: 共识号码{len(merged["consensus_numbers"])}个, '
                   f'分歧位置{len(merged["divergent_positions"])}个')
        
        return merged
