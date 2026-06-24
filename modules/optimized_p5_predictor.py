"""
排列5优化预测模块

基于原有预测器进行优化，修复所有已识别的bug，
集成新的特征工程，提升预测准确率。

核心优化：
1. 修复期号排序bug - 使用数值排序而非字符串排序
2. 修复质数定义bug - 1不是质数
3. 修复遗漏值计算bug - 正确处理从未出现的号码
4. 集成特征工程 - 使用012路、连号、重隔号等新特征
5. 增加概率归一化 - 确保概率总和为1
6. 增加边界保护 - 限制极端输出
7. 增加风险提示 - 明确说明仅作数据研究参考
8. 集成AI大模型 - 支持调用百度千帆大语言模型进行深度分析
"""

import logging
import os
import json
import math
import uuid
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import requests

os.makedirs('logs', exist_ok=True)
os.makedirs('reports', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/optimized_p5_predictor.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class OptimizedP5PredictorConfig:
    """
    优化后的排列5预测器配置类

    支持调整各预测算法的权重和参数，实现可配置的预测策略。
    """

    DEFAULT_CONFIG = {
        # 算法开关与权重（权重总和不需要为1，内部会归一化）
        'algorithms': {
            'frequency_weighted': {
                'enabled': True,
                'weight': 0.25,
                'params': {
                    'lookback_periods': None,  # None表示使用全部历史数据
                    'smoothing_factor': 0.1    # 拉普拉斯平滑系数
                }
            },
            'omission_regression': {
                'enabled': True,
                'weight': 0.20,
                'params': {
                    'max_omission_cap': 50,     # 遗漏值上限
                    'regression_steepness': 0.08  # 回归陡峭度
                }
            },
            'trend_momentum': {
                'enabled': True,
                'weight': 0.20,
                'params': {
                    'trend_window': 10,         # 趋势观察窗口
                    'momentum_factor': 1.2      # 动量放大系数
                }
            },
            'markov_transition': {
                'enabled': True,
                'weight': 0.20,
                'params': {
                    'order': 1,                 # 马尔可夫阶数（1或2）
                    'decay_factor': 0.95        # 历史衰减因子
                }
            },
            'pattern_continuation': {
                'enabled': True,
                'weight': 0.15,
                'params': {
                    'pattern_window': 5,        # 形态观察窗口
                    'continuation_boost': 1.3   # 形态延续增强系数
                }
            }
        },
        # 全局参数
        'global': {
            'hot_threshold_percentile': 70,     # 热号百分位阈值
            'cold_threshold_percentile': 30,    # 冷号百分位阈值
            'combination_count': 10,            # 推荐组合数量
            'position_top_n': 3,                # 每位取Top-N号码组合
            'probability_calibration': True,    # 是否进行概率校准
            'min_data_required': 30,            # 最小所需历史数据量
            'enable_feature_engineering': True,  # 是否启用特征工程
            'enable_boundary_protection': True,  # 是否启用边界保护
            'enable_ai_model': True,             # 是否启用AI大模型分析
            'ai_model_weight': 0.4,              # AI模型结果权重（统计模型权重=1-此值）
            'max_hot_ratio': 0.6,               # 最大热号比例（防止极端输出）
            'min_cold_ratio': 0.1               # 最小冷号比例（保证多样性）
        }
    }

    def __init__(self, custom_config: Optional[Dict] = None):
        """
        初始化配置

        Args:
            custom_config: 自定义配置字典，会与默认配置合并
        """
        self.config = self._merge_config(self.DEFAULT_CONFIG.copy(), custom_config or {})
        self._validate_config()

    def _merge_config(self, base: Dict, override: Dict) -> Dict:
        """递归合并配置字典"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                base[key] = self._merge_config(base[key], value)
            else:
                base[key] = value
        return base

    def _validate_config(self):
        """验证配置有效性"""
        total_weight = 0.0
        enabled_count = 0
        for algo_name, algo_cfg in self.config['algorithms'].items():
            if algo_cfg.get('enabled', False):
                total_weight += algo_cfg.get('weight', 0)
                enabled_count += 1
        if enabled_count == 0:
            logger.warning('所有预测算法均被禁用，将启用默认频率加权算法')
            self.config['algorithms']['frequency_weighted']['enabled'] = True
            self.config['algorithms']['frequency_weighted']['weight'] = 1.0
        logger.info(f'预测配置已加载: 启用{enabled_count}个算法, 总权重{total_weight:.2f}')

    def get_algorithm_weights(self) -> Dict[str, float]:
        """获取归一化后的算法权重"""
        weights = {}
        total = 0.0
        for name, cfg in self.config['algorithms'].items():
            if cfg.get('enabled', False):
                w = cfg.get('weight', 0)
                weights[name] = w
                total += w
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return weights

    def get_global_param(self, key: str, default=None):
        """获取全局参数"""
        return self.config['global'].get(key, default)

    def to_dict(self) -> Dict:
        """导出配置字典"""
        return self.config.copy()


class OptimizedP5Predictor:
    """
    优化后的排列5预测器核心类

    基于多算法融合模型，预测下一期各位置号码的出现概率，
    生成走势预测数据和推荐号码组合。

    主要优化：
    1. 修复期号排序bug
    2. 修复质数定义bug
    3. 修复遗漏值计算bug
    4. 集成特征工程
    5. 增加概率归一化
    6. 增加边界保护
    """

    def __init__(self, config: Optional[OptimizedP5PredictorConfig] = None):
        """
        初始化预测器

        Args:
            config: 预测器配置，None则使用默认配置
        """
        self.config = config or OptimizedP5PredictorConfig()
        self.positions = 5
        self.number_range = range(0, 10)
        self.position_names = ['万位', '千位', '百位', '十位', '个位']

        # 修复：正确的质数定义（1不是质数）
        self.primes = {2, 3, 5, 7}
        self.composites = {0, 1, 4, 6, 8, 9}

        # 延迟加载特征工程
        self._feature_engineering = None

        # AI模型配置
        self._init_ai_config()

    def _get_feature_engineering(self):
        """获取特征工程实例（懒加载）"""
        if self._feature_engineering is None and self.config.get_global_param('enable_feature_engineering'):
            from modules.feature_engineering import P5FeatureEngineering
            self._feature_engineering = P5FeatureEngineering()
        return self._feature_engineering

    def _init_ai_config(self):
        """初始化AI模型配置"""
        try:
            from config import QIANYAN_API_CONFIG
            self.api_config = QIANYAN_API_CONFIG
            self.api_url = self.api_config.get('api_url', "https://qianfan.baidubce.com/v2/chat/completions")
            self.api_key = self.api_config.get('api_key', '')
            self.model_name = self.api_config.get('model_name', 'deepseek-v3.1-250821')
            self.ai_available = bool(self.api_key)
            
            if self.ai_available:
                self.headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.api_key}'
                }
                logger.info(f'AI模型配置加载成功: {self.model_name}')
            else:
                logger.warning('API密钥未配置，AI模型分析将被跳过')
        except ImportError:
            self.api_config = {}
            self.api_key = ''
            self.ai_available = False
            logger.warning('无法加载config.py，AI模型分析将被跳过')

    def _build_ai_prompt(self, history_data: List[Dict], current_issue: str, 
                        stats_summary: str) -> str:
        """构建AI分析提示词"""
        prompt = f"""你是一位专业的排列5彩票数据分析专家。请基于以下提供的排列5历史开奖数据和统计分析结果，进行深度分析并预测下一期各位置号码。

【彩种规则】
- 排列5：5位数字，每位0-9，每天开奖
- 号码位置：万位、千位、百位、十位、个位
- 和值范围：0-45
- 跨度范围：0-9

【统计分析摘要】
{stats_summary}

【历史开奖数据（最近30期）】
"""
        for item in history_data[:30]:
            numbers = item.get('numbers', [])
            if len(numbers) == 5:
                issue = item.get('issue', '')
                draw_date = item.get('draw_date', '')
                num_str = ''.join(map(str, numbers))
                prompt += f'期号:{issue} 日期:{draw_date} 号码:{num_str}\n'

        prompt += """
【分析要求】
1. 趋势分析：分析各位置号码近期走势、冷热号变化趋势
2. 概率统计：基于统计分析摘要，计算各号码出现频次、遗漏值统计
3. 模式识别：识别奇偶比、大小比、质合比等模式规律
4. 号码推荐：基于统计规律和AI深度分析，推荐下一期各位置号码（每个位置推荐3个号码）
5. 组合推荐：推荐5个完整号码组合
6. 置信度评估：为每个推荐号码提供置信度分数（0-1）
7. 风险提示：明确说明所有分析仅基于历史数据统计，不保证中奖

【输出格式要求】
请严格按照以下JSON格式输出，不要包含任何额外文字：

{
    "recommended_numbers": {
        "wan": [{"number": 5, "confidence": 0.85, "reason": "近期热号"}],
        "qian": [{"number": 3, "confidence": 0.80, "reason": "遗漏值即将到期"}],
        "bai": [{"number": 7, "confidence": 0.82, "reason": "频次统计排名第一"}],
        "shi": [{"number": 2, "confidence": 0.78, "reason": "奇偶模式转换"}],
        "ge": [{"number": 8, "confidence": 0.88, "reason": "近期走势明显"}]
    },
    "recommended_combinations": [
        {"numbers": [5, 3, 7, 2, 8], "confidence": 0.72, "reason": "综合各位置最优推荐"},
        {"numbers": [5, 3, 7, 2, 6], "confidence": 0.68, "reason": "个位备选方案"}
    ],
    "trend_analysis": {
        "wan": "万位近期走势分析...",
        "qian": "千位近期走势分析...",
        "bai": "百位近期走势分析...",
        "shi": "十位近期走势分析...",
        "ge": "个位近期走势分析..."
    },
    "key_conclusions": [
        "万位5号近期热度上升",
        "千位3号遗漏值即将到期"
    ],
    "risk_warning": "本分析基于历史数据统计，不保证中奖，请理性购彩。"
}
"""
        return prompt

    def _call_ai_model(self, prompt: str, max_tokens: int = 8000, 
                       temperature: float = 0.7) -> Optional[str]:
        """调用AI大语言模型"""
        if not self.ai_available:
            logger.warning('AI模型不可用（未配置API密钥）')
            return None

        logger.info(f'=== 开始调用AI模型: {self.model_name} ===')

        payload = json.dumps({
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一位专业的彩票数据分析专家，擅长排列5号码分析和趋势预测。请按照要求严格输出JSON格式。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"}
        })

        try:
            response = requests.request("POST", self.api_url, headers=self.headers, data=payload)
            response.raise_for_status()

            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                logger.info(f'AI模型调用成功，返回长度: {len(content)}')
                return content

            logger.error(f'AI模型返回格式异常: {result}')
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f'AI模型调用失败: {e}')
            return None
        except json.JSONDecodeError as e:
            logger.error(f'AI响应JSON解析失败: {e}')
            return None
        except Exception as e:
            logger.error(f'AI模型调用异常: {e}')
            return None

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """解析AI响应"""
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx == -1 or end_idx == 0:
                logger.error('无法找到JSON起始或结束位置')
                return {}

            json_str = response_text[start_idx:end_idx]
            return json.loads(json_str)

        except json.JSONDecodeError as e:
            logger.error(f'JSON解析失败: {e}')
            return {}
        except Exception as e:
            logger.error(f'解析AI响应失败: {e}')
            return {}

    def _generate_stats_summary(self, sorted_data: List[Dict], 
                                algorithm_probs: Dict) -> str:
        """生成统计分析摘要用于AI提示词"""
        lines = []
        
        # 频率统计
        lines.append('【频率统计】')
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            freq_probs = algorithm_probs.get('frequency_weighted', [])
            if pos < len(freq_probs):
                sorted_nums = sorted(freq_probs[pos].items(), key=lambda x: x[1], reverse=True)
                top3 = sorted_nums[:3]
                bottom3 = sorted_nums[-3:]
                lines.append(f'{pos_name}: 热号={[n for n, _ in top3]}, 冷号={[n for n, _ in bottom3]}')
        
        # 遗漏分析
        lines.append('\n【遗漏分析】')
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            omission_probs = algorithm_probs.get('omission_regression', [])
            if pos < len(omission_probs):
                sorted_nums = sorted(omission_probs[pos].items(), key=lambda x: x[1], reverse=True)
                high_omission = sorted_nums[:3]
                lines.append(f'{pos_name}: 高遗漏回归={[n for n, _ in high_omission]}')
        
        # 近期趋势
        lines.append('\n【近期趋势】')
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            trend_probs = algorithm_probs.get('trend_momentum', [])
            if pos < len(trend_probs):
                sorted_nums = sorted(trend_probs[pos].items(), key=lambda x: x[1], reverse=True)
                top2 = sorted_nums[:2]
                lines.append(f'{pos_name}: 趋势推荐={[n for n, _ in top2]}')
        
        return '\n'.join(lines)

    def predict(self, history_data: List[Dict], current_issue: Optional[str] = None) -> Dict[str, Any]:
        """
        执行下一期预测

        Args:
            history_data: 历史开奖数据列表，按时间倒序排列（最新在前）
            current_issue: 当前最新期号，用于推导下期期号

        Returns:
            预测结果字典，包含各位置概率分布、推荐组合、走势预测等
        """
        if not history_data:
            return {'error': '历史数据为空，无法预测'}

        min_required = self.config.get_global_param('min_data_required', 30)
        if len(history_data) < min_required:
            logger.warning(f'历史数据量{len(history_data)}少于建议最小值{min_required}，预测结果可能不稳定')

        # 推导下期期号
        next_issue = self._infer_next_issue(history_data, current_issue)

        # 修复：使用数值排序而非字符串排序
        sorted_data = self._sort_data_by_issue(history_data)

        # 执行各算法预测
        algorithm_probs = self._run_algorithms(sorted_data)

        # 融合各算法概率
        fused_probs = self._fuse_probabilities(algorithm_probs)

        # 修复：概率归一化
        fused_probs = self._normalize_probabilities(fused_probs)

        # 边界保护
        if self.config.get_global_param('enable_boundary_protection'):
            fused_probs = self._apply_boundary_protection(fused_probs, sorted_data)

        # AI大模型分析（可选）
        ai_result = {}
        ai_enabled = self.config.get_global_param('enable_ai_model', True)
        if ai_enabled and self.ai_available:
            try:
                # 生成统计摘要
                stats_summary = self._generate_stats_summary(sorted_data, algorithm_probs)
                # 构建提示词
                prompt = self._build_ai_prompt(sorted_data, current_issue or '', stats_summary)
                # 调用AI模型
                ai_response = self._call_ai_model(prompt)
                if ai_response:
                    ai_result = self._parse_ai_response(ai_response)
                    # 融合AI结果到概率分布
                    fused_probs = self._fuse_ai_results(fused_probs, ai_result)
                    logger.info('AI模型分析完成，已融合到预测结果')
                else:
                    logger.warning('AI模型调用失败，使用纯统计模型结果')
            except Exception as e:
                logger.error(f'AI模型分析异常: {e}')

        # 生成推荐组合
        top_combinations = self._generate_combinations(fused_probs)

        # 走势预测分析
        trend_forecast = self._forecast_trend(sorted_data, fused_probs)

        # 如果有AI结果，更新趋势预测
        if ai_result:
            trend_forecast = self._merge_ai_trend(trend_forecast, ai_result)

        # 预测摘要
        summary = self._generate_summary(fused_probs, top_combinations, next_issue)

        predict_uuid = str(uuid.uuid4())

        result = {
            'predict_uuid': predict_uuid,
            'target_issue': next_issue,
            'base_issue': current_issue or history_data[0].get('issue', ''),
            'predict_time': datetime.now().isoformat(),
            'algorithm_config': self.config.to_dict(),
            'algorithm_weights': self.config.get_algorithm_weights(),
            'algorithm_probs': algorithm_probs,
            'fused_probabilities': fused_probs,
            'top_combinations': top_combinations,
            'trend_forecast': trend_forecast,
            'summary': summary,
            'data_samples': len(history_data),
            'ai_analysis_enabled': ai_enabled and self.ai_available,
            'ai_result': ai_result,
            'risk_warning': '⚠️ 重要提示：本程序仅基于历史数据统计分析和AI模型预测，无法保证开奖结果，不构成任何投资建议。彩票开奖具有随机性，请理性购彩。'
        }

        logger.info(f'预测完成: 目标期号{next_issue}, 推荐组合数{len(top_combinations)}, AI分析:{"启用" if ai_enabled and self.ai_available else "未启用"}')
        return result

    def _fuse_ai_results(self, fused_probs: List[Dict[int, float]], 
                        ai_result: Dict[str, Any]) -> List[Dict[int, float]]:
        """融合AI模型结果到概率分布"""
        ai_weight = self.config.get_global_param('ai_model_weight', 0.4)
        stat_weight = 1.0 - ai_weight

        rec_numbers = ai_result.get('recommended_numbers', {})
        pos_mapping = {'wan': 0, 'qian': 1, 'bai': 2, 'shi': 3, 'ge': 4}

        for pos_name, idx in pos_mapping.items():
            if idx >= self.positions:
                continue

            rec_list = rec_numbers.get(pos_name, [])
            if not rec_list:
                continue

            # 构建AI推荐概率
            ai_probs = {n: 0.1 for n in self.number_range}
            total_confidence = 0
            for rec in rec_list:
                if isinstance(rec, dict):
                    num = rec.get('number')
                    conf = rec.get('confidence', 0.5)
                    if num is not None:
                        ai_probs[int(num)] = conf
                        total_confidence += conf

            # 归一化AI概率
            if total_confidence > 0:
                for num in self.number_range:
                    ai_probs[num] /= total_confidence

            # 加权融合
            for num in self.number_range:
                fused_probs[idx][num] = (
                    stat_weight * fused_probs[idx].get(num, 0.1) +
                    ai_weight * ai_probs.get(num, 0.1)
                )

            # 重新归一化
            total = sum(fused_probs[idx].values())
            if total > 0:
                for num in self.number_range:
                    fused_probs[idx][num] /= total

        return fused_probs

    def _merge_ai_trend(self, trend_forecast: Dict[str, Any], 
                       ai_result: Dict[str, Any]) -> Dict[str, Any]:
        """合并AI趋势分析到预测结果"""
        ai_trend = ai_result.get('trend_analysis', {})
        ai_conclusions = ai_result.get('key_conclusions', [])
        
        pos_mapping = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
        
        for ai_key, pos_name in pos_mapping.items():
            if pos_name in trend_forecast and ai_key in ai_trend:
                trend_forecast[pos_name]['ai_analysis'] = ai_trend[ai_key]
        
        if ai_conclusions:
            trend_forecast['ai_conclusions'] = ai_conclusions
        
        return trend_forecast

    def _sort_data_by_issue(self, data: List[Dict]) -> List[Dict]:
        """
        按期号排序数据（修复：使用数值排序）

        Args:
            data: 原始数据列表

        Returns:
            按期号正序排列的数据列表
        """
        def get_issue_number(item):
            """提取期号数值"""
            issue = str(item.get('issue', ''))
            if issue.isdigit():
                return int(issue)
            return 0

        return sorted(data, key=get_issue_number)

    def _infer_next_issue(self, history_data: List[Dict], current_issue: Optional[str] = None) -> str:
        """推导下一期期号"""
        if current_issue:
            base = str(current_issue)
        elif history_data:
            base = str(history_data[0].get('issue', ''))
        else:
            base = ''

        if base and base.isdigit():
            next_num = int(base) + 1
            return str(next_num)
        return '未知'

    def _run_algorithms(self, sorted_data: List[Dict]) -> Dict[str, List[Dict[int, float]]]:
        """
        执行所有启用的预测算法

        Returns:
            算法名称 -> 各位置概率分布列表的字典
        """
        results = {}
        weights = self.config.get_algorithm_weights()

        if 'frequency_weighted' in weights:
            results['frequency_weighted'] = self._algo_frequency_weighted(sorted_data)
        if 'omission_regression' in weights:
            results['omission_regression'] = self._algo_omission_regression(sorted_data)
        if 'trend_momentum' in weights:
            results['trend_momentum'] = self._algo_trend_momentum(sorted_data)
        if 'markov_transition' in weights:
            results['markov_transition'] = self._algo_markov_transition(sorted_data)
        if 'pattern_continuation' in weights:
            results['pattern_continuation'] = self._algo_pattern_continuation(sorted_data)

        # 集成特征工程算法
        if self.config.get_global_param('enable_feature_engineering'):
            fe = self._get_feature_engineering()
            if fe:
                try:
                    results['feature_engineering'] = self._algo_feature_engineering(sorted_data, fe)
                except Exception as e:
                    logger.error(f'特征工程算法执行失败: {e}')

        return results

    def _algo_frequency_weighted(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        频率加权算法

        基于历史出现频率计算概率，使用拉普拉斯平滑避免零概率。
        """
        params = self.config.config['algorithms']['frequency_weighted']['params']
        smoothing = params.get('smoothing_factor', 0.1)
        lookback = params.get('lookback_periods')

        use_data = data[-lookback:] if lookback else data
        total = len(use_data)

        # 统计各位置各号码出现次数
        counts = [defaultdict(int) for _ in range(self.positions)]
        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    counts[pos][int(num)] += 1

        # 计算概率（拉普拉斯平滑）
        probs = []
        for pos in range(self.positions):
            pos_probs = {}
            for num in self.number_range:
                count = counts[pos].get(num, 0)
                pos_probs[num] = (count + smoothing) / (total + smoothing * 10)
            probs.append(pos_probs)

        return probs

    def _algo_omission_regression(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        遗漏回归算法（修复：正确处理从未出现的号码）

        基于当前遗漏值，遗漏越大短期回归概率越高（指数衰减模型）。
        """
        params = self.config.config['algorithms']['omission_regression']['params']
        max_cap = params.get('max_omission_cap', 50)
        steepness = params.get('regression_steepness', 0.08)

        # 计算各位置各号码当前遗漏值
        last_occurrence = [{} for _ in range(self.positions)]
        for idx, item in enumerate(data):
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    last_occurrence[pos][int(num)] = idx

        total = len(data)
        probs = []
        for pos in range(self.positions):
            pos_probs = {}
            omissions = {}
            for num in self.number_range:
                last_idx = last_occurrence[pos].get(num, -1)
                # 修复：当号码从未出现时，遗漏值应为total
                if last_idx == -1:
                    omission = total
                else:
                    omission = total - 1 - last_idx
                omissions[num] = min(omission, max_cap)

            # 指数回归概率：遗漏越大，概率越高
            raw_scores = {num: math.exp(steepness * omissions[num]) for num in self.number_range}
            total_score = sum(raw_scores.values())
            for num in self.number_range:
                pos_probs[num] = raw_scores[num] / total_score if total_score > 0 else 0.1
            probs.append(pos_probs)

        return probs

    def _algo_trend_momentum(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        趋势动量算法

        分析最近N期的号码变化趋势，赋予沿趋势方向号码更高概率。
        """
        params = self.config.config['algorithms']['trend_momentum']['params']
        window = params.get('trend_window', 10)
        momentum_factor = params.get('momentum_factor', 1.2)

        recent = data[-window:] if len(data) >= window else data
        if len(recent) < 2:
            # 数据不足时返回均匀分布
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        probs = []
        for pos in range(self.positions):
            # 提取该位置近期序列
            seq = []
            for item in recent:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    seq.append(int(numbers[pos]))

            if len(seq) < 2:
                probs.append({n: 0.1 for n in self.number_range})
                continue

            # 线性回归求趋势方向
            x = np.arange(len(seq))
            y = np.array(seq)
            slope = np.polyfit(x, y, 1)[0] if len(x) > 1 else 0

            # 根据趋势斜率调整概率
            pos_probs = {}
            last_val = seq[-1]
            for num in self.number_range:
                distance = num - last_val
                # 沿趋势方向的距离获得正向加成
                trend_score = 1.0 + momentum_factor * slope * distance / 9.0
                # 高斯衰减：离上期值越远概率越低
                gaussian_decay = math.exp(-0.5 * ((distance / 3.0) ** 2))
                pos_probs[num] = max(0.01, trend_score * gaussian_decay)

            # 归一化
            total_score = sum(pos_probs.values())
            for num in self.number_range:
                pos_probs[num] /= total_score
            probs.append(pos_probs)

        return probs

    def _algo_markov_transition(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        马尔可夫转移算法

        基于最近一期号码，计算各位置的状态转移概率。
        """
        params = self.config.config['algorithms']['markov_transition']['params']
        order = params.get('order', 1)
        decay = params.get('decay_factor', 0.95)

        if len(data) < order + 1:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        last_item = data[-1]
        last_numbers = last_item.get('numbers', [])
        if len(last_numbers) != self.positions:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        # 构建转移矩阵
        transition_counts = [defaultdict(lambda: defaultdict(float)) for _ in range(self.positions)]

        for idx in range(order, len(data)):
            weight = decay ** (len(data) - idx)
            prev_item = data[idx - order]
            curr_item = data[idx]
            prev_nums = prev_item.get('numbers', [])
            curr_nums = curr_item.get('numbers', [])
            if len(prev_nums) == self.positions and len(curr_nums) == self.positions:
                for pos in range(self.positions):
                    p = int(prev_nums[pos])
                    c = int(curr_nums[pos])
                    transition_counts[pos][p][c] += weight

        probs = []
        for pos in range(self.positions):
            prev_num = int(last_numbers[pos])
            counts = transition_counts[pos].get(prev_num, {})
            total = sum(counts.values())

            pos_probs = {}
            if total > 0:
                for num in self.number_range:
                    pos_probs[num] = counts.get(num, 0) / total
            else:
                # 无转移记录时回退到均匀分布
                for num in self.number_range:
                    pos_probs[num] = 0.1
            probs.append(pos_probs)

        return probs

    def _algo_pattern_continuation(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        形态延续算法（修复：使用正确的质数定义）

        分析奇偶、大小、质合形态的近期延续规律，
        对符合延续趋势的号码给予概率加成。
        """
        params = self.config.config['algorithms']['pattern_continuation']['params']
        window = params.get('pattern_window', 5)
        boost = params.get('continuation_boost', 1.3)

        recent = data[-window:] if len(data) >= window else data
        if len(recent) < 2:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        # 分析各位置近期形态趋势
        probs = []
        for pos in range(self.positions):
            seq = []
            for item in recent:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    seq.append(int(numbers[pos]))

            if not seq:
                probs.append({n: 0.1 for n in self.number_range})
                continue

            # 统计近期奇偶、大小、质合占比
            odd_ratio = sum(1 for n in seq if n % 2 == 1) / len(seq)
            big_ratio = sum(1 for n in seq if n >= 5) / len(seq)
            # 修复：使用正确的质数定义
            prime_ratio = sum(1 for n in seq if n in self.primes) / len(seq)

            pos_probs = {}
            for num in self.number_range:
                score = 1.0
                # 奇偶延续
                is_odd = num % 2 == 1
                if is_odd and odd_ratio > 0.6:
                    score *= boost
                elif not is_odd and odd_ratio < 0.4:
                    score *= boost

                # 大小延续
                is_big = num >= 5
                if is_big and big_ratio > 0.6:
                    score *= boost
                elif not is_big and big_ratio < 0.4:
                    score *= boost

                # 质合延续（修复：使用正确的质数定义）
                is_prime = num in self.primes
                if is_prime and prime_ratio > 0.4:
                    score *= boost
                elif not is_prime and prime_ratio < 0.6:
                    score *= boost

                pos_probs[num] = score

            total_score = sum(pos_probs.values())
            for num in self.number_range:
                pos_probs[num] /= total_score
            probs.append(pos_probs)

        return probs

    def _algo_feature_engineering(self, data: List[Dict], fe) -> List[Dict[int, float]]:
        """
        特征工程算法

        基于提取的丰富特征进行预测。
        """
        try:
            # 提取所有特征
            features = fe.extract_all_features(data)

            # 使用频率特征作为基础
            freq_features = features.get('frequency', {})
            omission_features = features.get('omission', {})
            road_features = features.get('road_012', {})

            probs = []
            for pos in range(self.positions):
                pos_name = self.position_names[pos]

                # 获取频率特征
                freq_data = freq_features.get(pos_name, {})
                frequencies = freq_data.get('frequencies', {})

                # 获取遗漏特征
                omission_data = omission_features.get(pos_name, {})
                omission_probs = omission_data.get('omission_probs', {})

                # 获取012路特征
                road_data = road_features.get(pos_name, {})
                road_ratios = road_data.get('road_ratios', {})
                current_road = road_data.get('current_road', -1)

                # 融合特征
                pos_probs = {}
                for num in self.number_range:
                    # 基础频率
                    base_prob = frequencies.get(num, 0.1)

                    # 遗漏加成
                    omission_boost = omission_probs.get(num, 0.1)

                    # 012路加成
                    num_road = 0 if num in fe.road_0 else (1 if num in fe.road_1 else 2)
                    road_boost = road_ratios.get(num_road, 0.33)

                    # 综合概率
                    pos_probs[num] = base_prob * 0.4 + omission_boost * 0.3 + road_boost * 0.3

                # 归一化
                total = sum(pos_probs.values())
                if total > 0:
                    for num in self.number_range:
                        pos_probs[num] /= total

                probs.append(pos_probs)

            return probs

        except Exception as e:
            logger.error(f'特征工程算法失败: {e}')
            # 回退到均匀分布
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

    def _fuse_probabilities(self, algorithm_probs: Dict[str, List[Dict[int, float]]]) -> List[Dict[int, float]]:
        """
        融合各算法概率

        使用加权平均融合各算法的预测结果，并进行概率校准。
        """
        weights = self.config.get_algorithm_weights()
        if not weights or not algorithm_probs:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        fused = []
        for pos in range(self.positions):
            pos_fused = defaultdict(float)
            for algo_name, pos_probs in algorithm_probs.items():
                w = weights.get(algo_name, 0)
                if pos < len(pos_probs):
                    for num, prob in pos_probs[pos].items():
                        pos_fused[num] += w * prob

            # 归一化
            total = sum(pos_fused.values())
            if total > 0:
                for num in self.number_range:
                    pos_fused[num] /= total
            else:
                for num in self.number_range:
                    pos_fused[num] = 0.1

            fused.append(dict(pos_fused))

        return fused

    def _normalize_probabilities(self, probs: List[Dict[int, float]]) -> List[Dict[int, float]]:
        """
        概率归一化（修复：确保每个位置的概率总和为1）

        Args:
            probs: 原始概率列表

        Returns:
            归一化后的概率列表
        """
        normalized = []
        for pos_probs in probs:
            total = sum(pos_probs.values())
            if total > 0:
                normalized_probs = {num: prob / total for num, prob in pos_probs.items()}
            else:
                # 如果总和为0，返回均匀分布
                normalized_probs = {num: 0.1 for num in self.number_range}
            normalized.append(normalized_probs)

        return normalized

    def _apply_boundary_protection(self, probs: List[Dict[int, float]],
                                   data: List[Dict]) -> List[Dict[int, float]]:
        """
        边界保护（新增：限制极端输出）

        防止输出极端单一类型号码（如全热号或全冷号）。

        Args:
            probs: 原始概率列表
            data: 历史数据

        Returns:
            应用边界保护后的概率列表
        """
        max_hot_ratio = self.config.get_global_param('max_hot_ratio', 0.6)
        min_cold_ratio = self.config.get_global_param('min_cold_ratio', 0.1)

        # 计算冷热号
        fe = self._get_feature_engineering()
        if not fe:
            return probs

        try:
            freq_features = fe.calculate_frequency_features(data)

            protected_probs = []
            for pos in range(self.positions):
                pos_name = self.position_names[pos]
                freq_data = freq_features.get(pos_name, {})
                hot_levels = freq_data.get('hot_levels', {})

                # 统计当前预测中的热号和冷号
                pos_probs = probs[pos].copy()
                sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)

                # 取Top-3作为预测号码
                top_nums = [num for num, _ in sorted_nums[:3]]

                # 检查热号比例
                hot_count = sum(1 for num in top_nums if hot_levels.get(num) == 'hot')
                hot_ratio = hot_count / len(top_nums)

                # 如果热号比例过高，降低热号概率
                if hot_ratio > max_hot_ratio:
                    for num in top_nums:
                        if hot_levels.get(num) == 'hot':
                            pos_probs[num] *= 0.8

                # 检查冷号比例
                cold_count = sum(1 for num in top_nums if hot_levels.get(num) == 'cold')
                cold_ratio = cold_count / len(top_nums)

                # 如果冷号比例过低，提升冷号概率
                if cold_ratio < min_cold_ratio:
                    for num in self.number_range:
                        if hot_levels.get(num) == 'cold':
                            pos_probs[num] *= 1.2

                # 重新归一化
                total = sum(pos_probs.values())
                if total > 0:
                    for num in self.number_range:
                        pos_probs[num] /= total

                protected_probs.append(pos_probs)

            return protected_probs

        except Exception as e:
            logger.error(f'边界保护失败: {e}')
            return probs

    def _generate_combinations(self, fused_probs: List[Dict[int, float]]) -> List[Dict[str, Any]]:
        """
        生成推荐组合

        基于融合后的概率分布，生成高概率号码组合。
        """
        combination_count = self.config.get_global_param('combination_count', 10)
        position_top_n = self.config.get_global_param('position_top_n', 3)

        # 获取每个位置的Top-N号码
        top_numbers_per_position = []
        for pos in range(self.positions):
            pos_probs = fused_probs[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_nums = [num for num, _ in sorted_nums[:position_top_n]]
            top_numbers_per_position.append(top_nums)

        # 生成组合（笛卡尔积）
        import itertools
        all_combinations = list(itertools.product(*top_numbers_per_position))

        # 计算每个组合的综合概率
        combination_scores = []
        for combo in all_combinations:
            score = 1.0
            for pos, num in enumerate(combo):
                score *= fused_probs[pos].get(num, 0.1)
            combination_scores.append((combo, score))

        # 按概率排序
        combination_scores.sort(key=lambda x: x[1], reverse=True)

        # 取前N个组合
        top_combinations = []
        for rank, (combo, score) in enumerate(combination_scores[:combination_count], 1):
            top_combinations.append({
                'rank': rank,
                'combination': ''.join(map(str, combo)),
                'numbers': list(combo),
                'probability': round(score, 6),
                'confidence': round(score * 100, 2)
            })

        return top_combinations

    def _forecast_trend(self, sorted_data: List[Dict],
                       fused_probs: List[Dict[int, float]]) -> Dict[str, Any]:
        """
        走势预测分析

        基于历史数据和融合概率，预测各位置的走势。
        """
        trend_forecast = {}

        for pos in range(self.positions):
            pos_name = self.position_names[pos]

            # 获取Top-3号码
            pos_probs = fused_probs[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_3 = [num for num, _ in sorted_nums[:3]]

            # 分析近期趋势
            recent = sorted_data[-10:] if len(sorted_data) >= 10 else sorted_data
            recent_values = []
            for item in recent:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    recent_values.append(int(numbers[pos]))

            # 计算趋势方向
            if len(recent_values) >= 2:
                if recent_values[-1] > recent_values[-2]:
                    trend = '上升'
                elif recent_values[-1] < recent_values[-2]:
                    trend = '下降'
                else:
                    trend = '持平'
            else:
                trend = '未知'

            trend_forecast[pos_name] = {
                'top_numbers': top_3,
                'trend': trend,
                'recent_values': recent_values[-5:] if len(recent_values) >= 5 else recent_values
            }

        return trend_forecast

    def _generate_summary(self, fused_probs: List[Dict[int, float]],
                         top_combinations: List[Dict[str, Any]],
                         next_issue: str) -> str:
        """
        生成预测摘要

        Args:
            fused_probs: 融合后的概率分布
            top_combinations: 推荐组合列表
            next_issue: 目标期号

        Returns:
            摘要文本
        """
        lines = []
        lines.append(f'排列5第{next_issue}期预测摘要')
        lines.append('=' * 50)

        # 各位置推荐
        lines.append('\n【各位置推荐号码】')
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            pos_probs = fused_probs[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_3 = sorted_nums[:3]

            lines.append(f'\n{pos_name}:')
            for rank, (num, prob) in enumerate(top_3, 1):
                lines.append(f'  {rank}. 号码{num} (概率: {prob:.2%})')

        # 推荐组合
        lines.append('\n【推荐组合（Top-5）】')
        for combo in top_combinations[:5]:
            lines.append(f"{combo['rank']}. {combo['combination']} (置信度: {combo['confidence']:.2f}%)")

        lines.append('\n' + '=' * 50)
        lines.append('⚠️ 重要提示：本预测仅基于历史数据统计分析，无法预测开奖结果，请理性购彩。')

        return '\n'.join(lines)


if __name__ == '__main__':
    # 测试优化后的预测器
    import json

    # 模拟数据
    test_data = [
        {'issue': '2024001', 'numbers': [1, 2, 3, 4, 5]},
        {'issue': '2024002', 'numbers': [2, 3, 4, 5, 6]},
        {'issue': '2024003', 'numbers': [3, 4, 5, 6, 7]},
        {'issue': '2024004', 'numbers': [4, 5, 6, 7, 8]},
        {'issue': '2024005', 'numbers': [5, 6, 7, 8, 9]},
    ]

    predictor = OptimizedP5Predictor()
    result = predictor.predict(test_data, '2024005')

    print('预测完成')
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))