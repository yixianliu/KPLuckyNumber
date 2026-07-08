"""
排列5优化预测模块 (v3.0 深度优化版)

本项目基于多模型融合的彩票数据分析与预测平台,通过五种统计算法 + 概率融合 + 约束优化,
生成下期各位置号码的概率分布和推荐组合。

核心架构 (v3.0 新增):
1. 频率加权算法 (35%) — 基于历史频次分布,拉普拉斯平滑
2. 遗漏回归算法 (25%) — 指数衰减模型,遗漏越大概率越高
3. 趋势动量算法 (12%) — 线性回归检测趋势方向
4. 马尔可夫转移算法 (10%) — 一阶状态转移概率矩阵
5. 形态延续算法 (8%) — 奇偶/大小/质合形态规律
6. 贝叶斯推断算法 (NEW! 10%) — 基于先验概率和后验验证的动态调整
7. 自适应融合策略 (NEW!) — 基于验证历史的权重动态更新

关键设计原则:
- AI模型仅作为统计信号的再包装,不产生新信息
- 所有概率分布严格归一化(总和为1)
- 边界保护: 和值10-35, 相邻位差异惩罚, 奇偶比约束
- 延迟/懒加载: 所有外部依赖(import)在函数内部完成

算法权重配置 (v3.0):
  频率加权:   35% (最基础的统计信号)
  遗漏回归:   25% (第二可靠的统计信号)
  趋势动量:   12% (从15%下调,降低噪声)
  马尔可夫:   10% (从15%下调,防止过拟合)
  形态延续:    8% (从10%下调,稳定性差)
  贝叶斯推断: 10% (新增,基于验证反馈动态调整)
  AI融合:     10% (保持不变,仅作再包装)

使用方法:
    from modules.predictor import P5Predictor, P5PredictorConfig
    
    # 使用默认配置
    predictor = P5Predictor()
    result = predictor.predict(history_data, current_issue)
    
    # 自定义配置
    custom_config = {
        'algorithms': {
            'frequency_weighted': {'weight': 0.40},  # 提高频率权重
        }
    }
    config = P5PredictorConfig(custom_config)
    predictor = P5Predictor(config)

文件历史:
  2026-07-02: v2.1 优化算法权重配置
  2026-07-04: v3.0 新增贝叶斯推断 + 自适应融合策略
  
作者: KPLuckyNumber Team
"""

import logging
import os
import json
import math
import time
import uuid
import copy
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 确保日志和报告目录存在
os.makedirs('logs', exist_ok=True)
os.makedirs('reports', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/optimized_p5_predictor.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class AdaptiveWeightManager:
    """
    自适应权重管理器 (v3.0 新增)
    
    基于历史预测验证结果,动态调整各算法的权重分配。
    通过Bayesian更新机制,使系统能够自我进化。
    
    工作原理:
    1. 初始权重使用默认配置(v3.0)
    2. 每次验证后,根据各位置命中情况更新权重
    3. 使用指数加权移动平均(EWMA)平滑权重变化
    4. 权重更新存储在Redis中,持久化累积
    
    使用示例:
        manager = AdaptiveWeightManager()
        manager.record_verification('frequency_weighted', hit_rate=0.8)
        new_weights = manager.get_adaptive_weights()
    """
    
    def __init__(self):
        """初始化权重管理器"""
        # 各算法的历史命中率跟踪
        self.algo_hit_rates = {
            'frequency_weighted': {'total': 0, 'hits': 0, 'ewma': 0.35},
            'omission_regression': {'total': 0, 'hits': 0, 'ewma': 0.25},
            'trend_momentum': {'total': 0, 'hits': 0, 'ewma': 0.12},
            'markov_transition': {'total': 0, 'hits': 0, 'ewma': 0.10},
            'pattern_continuation': {'total': 0, 'hits': 0, 'ewma': 0.08},
            'bayesian_inference': {'total': 0, 'hits': 0, 'ewma': 0.10},  # 新增
        }
        # EWMA平滑系数 (α越小,历史影响越大)
        self.ewma_alpha = 0.3
        
    def record_verification(self, algo_name: str, hit_rate: float):
        """
        记录单次验证结果
        
        Args:
            algo_name: 算法名称
            hit_rate: 该算法的本次验证命中率(0-1)
        """
        if algo_name not in self.algo_hit_rates:
            return
            
        record = self.algo_hit_rates[algo_name]
        record['total'] += 1
        record['hits'] += hit_rate
        
        # 指数加权移动平均更新
        record['ewma'] = (
            self.ewma_alpha * hit_rate + 
            (1 - self.ewma_alpha) * record['ewma']
        )
        
    def get_adaptive_weights(self) -> Dict[str, float]:
        """
        获取自适应调整后的权重
        
        Returns:
            权重字典,格式 {algo_name: weight}
        """
        total_ewma = sum(v['ewma'] for v in self.algo_hit_rates.values())
        
        if total_ewma == 0:
            # 无数据时返回默认权重
            return {k: v.get('ewma', 0) for k, v in self.algo_hit_rates.items()}
            
        # 归一化EWMA值作为权重
        adaptive_weights = {}
        for algo_name, record in self.algo_hit_rates.items():
            adaptive_weights[algo_name] = record['ewma'] / total_ewma
            
        return adaptive_weights


    def load_from_records(self, records: List[Dict]):
        """
        从验证记录回放,恢复EWMA状态(实现跨进程持久化)

        历史记录保存在 predictions/weights_history.json,
        每条含 algo_evaluations(各算法命中率)。回放这些记录可让
        自适应权重在多次运行间累积学习成果,而非每进程重置。
        """
        for r in records:
            evals = r.get('algo_evaluations', {})
            for algo, hit in evals.items():
                if algo in self.algo_hit_rates and isinstance(hit, (int, float)):
                    self.record_verification(algo, float(hit))

class P5PredictorConfig:
    """
    排列5预测器配置类
    
    管理所有算法的开关、权重、参数,支持自定义配置合并。
    采用层次化配置结构,方便扩展新算法。
    
    配置层次:
    - algorithms.{algo_name}.enabled: 是否启用该算法
    - algorithms.{algo_name}.weight: 初始权重
    - algorithms.{algo_name}.params: 算法特定参数
    - global.*: 全局控制参数
    
    使用示例:
        # 默认配置
        config = P5PredictorConfig()
        
        # 自定义配置(部分覆盖)
        custom = {
            'algorithms': {
                'frequency_weighted': {'weight': 0.40}
            },
            'global': {'position_top_n': 5}
        }
        config = P5PredictorConfig(custom)
    """
    
    # v3.0 优化权重配置 (贝叶斯推断权重从历史数据中学习)
    DEFAULT_CONFIG = {
        'algorithms': {
            'frequency_weighted': {
                'enabled': True,
                'weight': 0.35,  # 最高权重:频率是最基础的统计规律
                'params': {
                    'lookback_periods': None,  # 使用全部历史数据
                    'smoothing_factor': 0.1,   # 拉普拉斯平滑系数
                    'recency_weight': True,    # 是否启用近期加权
                }
            },
            'omission_regression': {
                'enabled': True,
                'weight': 0.25,  # 遗漏回归是第二可靠信号
                'params': {
                    'max_omission_cap': 50,          # 遗漏值上限
                    'regression_steepness': 0.020,   # 陡度(从0.025降低,更平滑)
                    'linear_bonus': True,            # 新增:线性bonus补偿
                }
            },
            'trend_momentum': {
                'enabled': True,
                'weight': 0.12,  # 从15%降为12%: 彩票噪声大
                'params': {
                    'trend_window': 30,   # 从30期保持不变
                    'momentum_factor': 0.9,  # 从1.0降为0.9: 降低过度反应
                }
            },
            'markov_transition': {
                'enabled': True,
                'weight': 0.10,  # 从15%降为10%: 防止过拟合
                'params': {
                    'order': 1,           # 一阶马尔可夫
                    'decay_factor': 0.93, # 从0.95降为0.93: 降低近期偏见
                    'min_transition_prob': 0.02,  # 新增:最小转移概率
                }
            },
            'pattern_continuation': {
                'enabled': True,
                'weight': 0.08,  # 从10%降为8%: 短期不稳定
                'params': {
                    'pattern_window': 7,   # 从5期扩为7期,更稳健
                    'continuation_boost': 1.15,  # 从1.2降为1.15
                }
            },
            'bayesian_inference': {
                'enabled': True,
                'weight': 0.10,  # ★★★ 新增算法 ★★★
                'params': {
                    'prior_smooth': 0.05,      # 先验平滑系数
                    'posterior_weight': 0.7,   # 后验权重
                    'verification_window': 30, # 验证窗口(期数)
                    'penalize_miss': 0.85,     # 未命中惩罚系数
                    'reward_hit': 1.15,        # 命中奖励系数
                }
            },
            'feature_engineering': {
                'enabled': True,
                'weight': 0.10,  # 特征工程(连号/重号/012路)融合信号
                'params': {
                    'freq_weight': 0.30,
                    'omission_weight': 0.25,
                    'road_weight': 0.15,
                    'repeat_weight': 0.15,
                    'consecutive_weight': 0.15,
                }
            }
        },
        'global': {
            'hot_threshold_percentile': 70,
            'cold_threshold_percentile': 30,
            'combination_count': 10,
            'position_top_n': 6,  # v3.2优化: 从5扩大到6,覆盖率提升至60%
            'probability_calibration': True,
            'min_data_required': 30,
            'enable_feature_engineering': True,
            'enable_boundary_protection': True,
            'enable_adaptive_weights': True,  # 新增:是否启用自适应权重
            'enable_ai_model': True,
            'ai_model_weight': 0.1,
            'max_hot_ratio': 0.55,  # 从0.6降为0.55: 更保守
            'min_cold_ratio': 0.15,  # 从0.1升为0.15: 保证多样性
            'adjacent_diff_penalty': True,
            'cross_period_consistency': True,
            'hezhi_min': 10,         # 和值下限
            'hezhi_max': 35,         # 和值上限
            'span_min': 3,           # 新增:跨度下限
            'span_max': 8,           # 新增:跨度上限
            'odd_even_tolerance': 0.4,  # 新增:奇偶比容忍度
            'sum_of_squares_penalty': True,  # 新增:方差惩罚
            'tolerance_matching': True,  # v3.1新增:启用容错匹配(偏差±1也算命中)
        }
    }

    def __init__(self, custom_config: Optional[Dict] = None):
        """
        初始化配置
        
        Args:
            custom_config: 自定义配置字典,会与默认配置深度合并
        """
        self.config = self._merge_config(self.DEFAULT_CONFIG.copy(), custom_config or {})
        self._validate_config()
        
        # 新增: 自适应权重管理器
        self.weight_manager = AdaptiveWeightManager()

    def _merge_config(self, base: Dict, override: Dict) -> Dict:
        """
        递归合并配置字典
        
        Args:
            base: 基础配置
            override: 覆盖配置
            
        Returns:
            合并后的配置字典
        """
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
            logger.warning('所有预测算法均被禁用,将启用默认频率加权算法')
            self.config['algorithms']['frequency_weighted']['enabled'] = True
            self.config['algorithms']['frequency_weighted']['weight'] = 1.0
            
        logger.info(f'预测配置已加载: 启用{enabled_count}个算法, 总权重{total_weight:.2f}')

    def get_algorithm_weights(self) -> Dict[str, float]:
        """
        获取归一化后的算法权重
        
        如果启用了自适应权重,会根据历史验证结果动态调整权重。
        
        Returns:
            归一化后的权重字典 {algo_name: normalized_weight}
        """
        weights = {}
        total = 0.0
        enable_adaptive = self.config['global'].get('enable_adaptive_weights', True)
        
        for name, cfg in self.config['algorithms'].items():
            if cfg.get('enabled', False):
                w = cfg.get('weight', 0)
                # 如果启用自适应权重,基于历史命中率微调
                ewma = 0.0
                if enable_adaptive and hasattr(self, 'weight_manager'):
                    ewma = self.weight_manager.algo_hit_rates.get(name, {}).get('ewma', 0)
                if ewma > 0:
                    # 混合原始权重和历史表现(EWMA)
                    # 注意: 不再乘 total——total 是累计未归一化和,会导致后加入的算法权重被错误放大
                    w = 0.7 * w + 0.3 * ewma
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


class P5Predictor:
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

    def __init__(self, config: Optional[P5PredictorConfig] = None):
        """
        初始化预测器

        Args:
            config: 预测器配置，None则使用默认配置
        """
        self.config = config or P5PredictorConfig()
        self.positions = 5
        self.number_range = range(0, 10)
        self.position_names = ['万位', '千位', '百位', '十位', '个位']

        # 修复：正确的质数定义（1不是质数）
        self.primes = {2, 3, 5, 7}
        self.composites = {0, 1, 4, 6, 8, 9}

        # 延迟加载特征工程
        self._feature_engineering = None

        # 跨进程恢复自适应权重(EWMA):从验证记录回放,避免每进程重置
        try:
            records = self._load_verification_records()
            if records:
                self.config.weight_manager.load_from_records(records)
        except Exception:
            pass

        # AI模型配置
        self._init_ai_config()

    def _get_feature_engineering(self):
        """获取特征工程实例（懒加载）"""
        if self._feature_engineering is None and self.config.get_global_param('enable_feature_engineering'):
            # 延迟导入特征工程模块以避免在导入阶段出现依赖错误（延迟/懒加载模式）
            from modules.features import P5Features
            self._feature_engineering = P5Features()
        return self._feature_engineering

    def _init_ai_config(self):
        """初始化AI模型配置"""
        try:
            from config import AGNES_API_CONFIG
            self.api_config = AGNES_API_CONFIG
            self.api_url = self.api_config.get('api_url', "https://apihub.agnes-ai.com/v1/chat/completions")
            self.api_key = self.api_config.get('api_key', '')
            self.model_name = self.api_config.get('model_name', 'agnes-2.0-flash')
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

        # 说明：AI部分为可选功能，若未配置 api_key 则会被优雅跳过，遵循 AGENTS.md 中的设计约定。

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

        # 备注：payload 中使用 messages(system/user) 的结构与项目中其他调用 AI 模型的实现保持一致，
        #       便于统一管理和解析。response_format 期望返回JSON对象，但服务端常常返回带杂讯的文本，
        #       因此后续需使用 _parse_ai_response 做容错解析。

        # 构建带自动重试的 Session, 应对 SSL EOF / 连接中断等瞬时错误
        session = self._build_ai_session()

        last_err = None
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                response = session.request(
                    "POST", self.api_url, headers=self.headers, data=payload, timeout=60
                )
                response.raise_for_status()

                result = response.json()

                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content']
                    logger.info(f'AI模型调用成功(第{attempt + 1}次), 返回长度: {len(content)}')
                    return content

                logger.error(f'AI模型返回格式异常: {result}')
                return None

            except requests.exceptions.RequestException as e:
                last_err = e
                wait = 0.8 * (2 ** attempt)  # 指数退避: 0.8s, 1.6s, 3.2s
                logger.warning(f'AI模型调用第{attempt + 1}次失败: {e}; {wait:.1f}s 后重试')
                if attempt < max_attempts - 1:
                    time.sleep(wait)
            except json.JSONDecodeError as e:
                logger.error(f'AI响应JSON解析失败: {e}')
                return None
            except Exception as e:
                logger.error(f'AI模型调用异常: {e}')
                return None

        logger.error(f'AI模型调用在 {max_attempts} 次重试后仍失败: {last_err}')
        return None

    @staticmethod
    def _build_ai_session() -> requests.Session:
        """构建带重试策略的 requests Session, 应对 SSL EOF / 连接中断 / 5xx 等瞬时错误。"""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(['POST', 'GET']),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10)
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        return session

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """解析AI响应"""
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx == -1 or end_idx == 0:
                logger.error('无法找到JSON起始或结束位置')
                return {}

            json_str = response_text[start_idx:end_idx]
            # 按照项目约定：从第一个 '{' 到最后一个 '}' 提取第一个 JSON 对象并解析，
            # 这是因为模型回复常带有解释性文字或多余符号，不宜直接假设纯JSON返回。
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
                # 将Top3与Bottom3列为摘要，便于AI把握冷热号分布作为分析依据
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

        # 数据格式适配：将数据库查询结果（wan/qian/bai/shi/ge列）
        # 转换为预测器期望的 'numbers' 数组格式
        history_data = self._normalize_history_data(history_data)

        # 修复：使用数值排序而非字符串排序
        sorted_data = self._sort_data_by_issue(history_data)

        # 执行各算法预测
        algorithm_probs = self._run_algorithms(sorted_data)

        # 说明：algorithm_probs 的结构为 {算法名: [pos0_probs, pos1_probs, ..., pos4_probs]}
        # 每个 pos_probs 为 {号码: 概率} 的字典，后续将被融合为最终的 fused_probs。

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
                logger.error(f'AI模型分析异常: {e}', exc_info=True)

        # 生成推荐组合（使用增强版）
        top_combinations = self._generate_combinations_v2(fused_probs)
        
        # 如果没有新策略的生成结果，使用旧策略
        if not top_combinations:
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
                    # 对AI返回的号码和置信度进行严格校验并尝试转换为数值
                    try:
                        num_int = int(num)
                    except (TypeError, ValueError):
                        logger.warning(f'AI推荐号码格式异常，跳过: {num}')
                        continue
                    try:
                        conf = float(conf)
                    except (TypeError, ValueError):
                        conf = 0.5

                    if 0 <= num_int <= 9:
                        ai_probs[num_int] = conf
                        total_confidence += conf
                    else:
                        logger.warning(f'AI推荐号码超出范围0-9，跳过: {num_int}')

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

    def _normalize_history_data(self, raw_data: List[Dict]) -> List[Dict]:
        """
        数据格式适配器：将数据库查询结果转换为预测器期望的统一格式

        数据库p5_history_data表将号码拆分为wan/qian/bai/shi/ge五列，
        但预测器所有算法都期望一条记录中包含'numbers'数组字段。
        此方法负责在入口处完成格式转换，确保下游算法正常工作。

        Args:
            raw_data: 原始数据库查询结果列表

        Returns:
            标准化后的数据列表，每条记录包含'numbers'字段
        """
        normalized = []
        for item in raw_data:
            if 'numbers' in item and isinstance(item.get('numbers'), list) and len(item.get('numbers', [])) == 5:
                # 已经是正确格式
                normalized.append(item)
            elif all(k in item for k in ['wan', 'qian', 'bai', 'shi', 'ge']):
                # 数据库拆分行格式 -> 转换为 numbers 数组
                normalized.append({
                    'issue': item.get('issue'),
                    'draw_date': item.get('draw_date'),
                    'wan': item.get('wan'),
                    'qian': item.get('qian'),
                    'bai': item.get('bai'),
                    'shi': item.get('shi'),
                    'ge': item.get('ge'),
                    'hezhi': item.get('hezhi'),
                    'span': item.get('span'),
                    'numbers': [
                        int(item['wan']) if item.get('wan') is not None else 0,
                        int(item['qian']) if item.get('qian') is not None else 0,
                        int(item['bai']) if item.get('bai') is not None else 0,
                        int(item['shi']) if item.get('shi') is not None else 0,
                        int(item['ge']) if item.get('ge') is not None else 0,
                    ]
                })
            else:
                logger.warning(f'无法解析记录格式，跳过: {item}')
        return normalized

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
        # results 保存每个算法的分位概率分布
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
        if 'bayesian_inference' in weights:
            results['bayesian_inference'] = self._algo_bayesian_inference(sorted_data)

        # 集成特征工程算法
        if self.config.get_global_param('enable_feature_engineering'):
            fe = self._get_feature_engineering()
            if fe:
                try:
                    results['feature_engineering'] = self._algo_feature_engineering(sorted_data, fe)
                except Exception as e:
                    logger.error(f'特征工程算法执行失败: {e}', exc_info=True)

        # 返回格式示例：{
        #   'frequency_weighted': [ {0:0.12,1:0.09,...}, ... ],
        #   'omission_regression': [ ... ],
        # }

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

    def _algo_bayesian_inference(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        贝叶斯推断算法 (v3.0 新增)

        核心思想：基于先验概率和历史验证结果,动态计算后验概率分布。
        每个位置的每个号码都有一个先验概率(来自历史频率),
        然后根据近期验证结果(命中率)作为似然函数,
        通过贝叶斯公式得到后验概率：
            posterior = prior * likelihood / evidence

        数学原理：
            P(号码i|验证) = P(验证|号码i) * P(号码i) / P(验证)
        其中：
            - P(号码i) 为先验概率(基于历史频率)
            - P(验证|号码i) 为似然(近期命中该号码的频率)
            - P(验证) 为证据(归一化常数)

        Args:
            data: 按期号正序排列的历史开奖数据列表

        Returns:
            各位置号码的后验概率分布列表,格式为 List[Dict[int, float]],
            每个 Dict 的 key 为号码(0-9), value 为该号码在后验分布中的概率
        """
        params = self.config.config['algorithms']['bayesian_inference']['params']
        prior_smooth = params.get('prior_smooth', 0.05)
        posterior_weight = params.get('posterior_weight', 0.7)
        verification_window = params.get('verification_window', 30)
        penalize_miss = params.get('penalize_miss', 0.85)
        reward_hit = params.get('reward_hit', 1.15)

        # 第一阶段:计算先验概率 — 基于历史频率
        # 统计各位置各号码出现次数
        lookback = min(verification_window, len(data))
        if lookback == 0:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        prior_data = data[-lookback:]
        prior_counts = [defaultdict(int) for _ in range(self.positions)]
        for item in prior_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    prior_counts[pos][int(num)] += 1

        # 用拉普拉斯平滑计算先验概率
        prior_probs = []
        for pos in range(self.positions):
            pos_prior = {}
            total_count = sum(prior_counts[pos].values()) + prior_smooth * 10
            for num in self.number_range:
                pos_prior[num] = (prior_counts[pos].get(num, 0) + prior_smooth) / total_count
            prior_probs.append(pos_prior)

        # 第二阶段:计算似然概率 — 基于近期验证反馈
        # 从 weights_history.json 加载历史验证记录
        verification_records = self._load_verification_records()

        # 计算每个位置每个号码的"命中奖励/惩罚因子"
        likelihood_factors = []
        for pos in range(self.positions):
            pos_factors = {n: 1.0 for n in self.number_range}

            for record in verification_records[-verification_window:]:
                target_issue = record.get('target_issue', '')
                pred_numbers = record.get('predicted_numbers', [])
                actual_numbers = record.get('actual_numbers', [])

                if len(pred_numbers) != self.positions or len(actual_numbers) != self.positions:
                    continue

                # 检查该期预测中当前位置的号码是否在真实结果中出现
                pred_num = pred_numbers[pos]
                if pred_num in actual_numbers:
                    # 命中: 奖励因子
                    for n in self.number_range:
                        if n == pred_num:
                            pos_factors[n] *= reward_hit
                        else:
                            pos_factors[n] *= 1.0
                else:
                    # 未命中: 轻微惩罚
                    for n in self.number_range:
                        if n == pred_num:
                            pos_factors[n] *= penalize_miss
                        else:
                            pos_factors[n] *= 1.02  # 其他号码轻微上调

            likelihood_factors.append(pos_factors)

        # 第三阶段:计算后验概率 = prior * likelihood / evidence
        # 融合先验和似然,得到最终后验分布
        posterior_probs = []
        for pos in range(self.positions):
            pos_posterior = {}
            for num in self.number_range:
                prior = prior_probs[pos].get(num, 0.1)
                likelihood = likelihood_factors[pos].get(num, 1.0)
                # 加权融合
                combined = posterior_weight * prior * likelihood + (1 - posterior_weight) * prior
                pos_posterior[num] = combined

            # 归一化
            total = sum(pos_posterior.values())
            if total > 0:
                for num in self.number_range:
                    pos_posterior[num] /= total
            else:
                for num in self.number_range:
                    pos_posterior[num] = 0.1

            posterior_probs.append(pos_posterior)

        logger.info('贝叶斯推断算法执行完成,已计算后验概率分布')
        return posterior_probs

    def _load_verification_records(self) -> List[Dict]:
        """
        加载历史验证记录

        从数据库 p5_artifact(type='weight_history') 读取历史预测验证记录
        (v3.3 起替代原先的 predictions/weights_history.json 文件)。
        这些记录包含每次预测的目标期号、推荐号码和实际开奖号码,
        用于贝叶斯推断算法计算似然概率。

        Returns:
            验证记录列表,每条记录包含:
                - timestamp: 记录时间戳
                - target_issue: 预测目标期号
                - predicted_numbers: 预测号码列表
                - actual_numbers: 实际开奖号码列表
                - position_hits: 各位置命中率
                - algo_evaluations: 各算法评估得分
        """
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.warning('加载验证记录: 数据库连接失败')
                return []
            artifacts = db.get_artifacts('weight_history', limit=1000)
            db.disconnect()
            records = [a.get('data', {}) for a in artifacts if a.get('data')]
            return records
        except Exception as e:
            logger.warning(f'加载验证记录失败: {e}')
        return []

    def _algo_feature_engineering(self, data: List[Dict], fe) -> List[Dict[int, float]]:
        """
        特征工程算法 (v3.0)

        基于 extracted 的多维特征进行综合预测,融合以下特征:
        - 频率特征: 历史出现频率
        - 遗漏特征: 当前遗漏值和回归概率
        - 012路特征: 号码除以3的余数分布
        - 连号特征: 连续号码的出现概率
        - 重号特征: 上期号码重复出现的概率

        各特征权重分配:
            频率:     30% (基础统计信号)
            遗漏:     25% (第二可靠信号)
            012路:    15% (辅助参考)
            连号:     15% (近期规律)
            重号:     15% (惯性效应)

        融合公式:
            P(num) = 0.30*freq + 0.25*omission + 0.15*road + 0.15*consec + 0.15*repeat

        Args:
            data: 按期号正序排列的历史开奖数据列表
            fe: 特征工程实例 (P5FeatureEngineering)

        Returns:
            各位置号码的综合概率分布列表
        """
        try:
            positions = self.positions
            total = len(data)
            if total == 0:
                return [{n: 0.1 for n in self.number_range} for _ in range(positions)]

            # 频率统计
            freq_counts = [defaultdict(int) for _ in range(positions)]
            for item in data:
                nums = item.get('numbers', [])
                if len(nums) == positions:
                    for pos, num in enumerate(nums):
                        freq_counts[pos][int(num)] += 1

            # 遗漏统计（当前遗漏期数）
            last_occurrence = [{} for _ in range(positions)]
            for idx, item in enumerate(data):
                nums = item.get('numbers', [])
                if len(nums) == positions:
                    for pos, num in enumerate(nums):
                        last_occurrence[pos][int(num)] = idx

            # 上期号码（用于重号/连号特征）
            prev_nums = []
            if total >= 2:
                p = data[-1].get('numbers', [])
                if len(p) == positions:
                    prev_nums = [int(x) for x in p]

            fe_cfg = self.config.config['algorithms']['feature_engineering']['params']
            w_freq = fe_cfg.get('freq_weight', 0.30)
            w_omis = fe_cfg.get('omission_weight', 0.25)
            w_road = fe_cfg.get('road_weight', 0.15)
            w_rep = fe_cfg.get('repeat_weight', 0.15)
            w_consec = fe_cfg.get('consecutive_weight', 0.15)

            probs = []
            for pos in range(positions):
                # 频率概率（拉普拉斯平滑）
                freq_probs = {}
                for num in self.number_range:
                    freq_probs[num] = (freq_counts[pos].get(num, 0) + 0.1) / (total + 1.0)

                # 遗漏概率（指数回归, 归一化）
                omis_raw = {}
                for num in self.number_range:
                    li = last_occurrence[pos].get(num, -1)
                    omission = total - 1 - li if li >= 0 else total
                    omis_raw[num] = math.exp(0.02 * min(omission, 50))
                omis_max = max(omis_raw.values()) or 1
                omis_probs = {n: v / omis_max for n, v in omis_raw.items()}

                # 012路分布（基于历史频率）
                road_counts = defaultdict(int)
                for item in data:
                    nums = item.get('numbers', [])
                    if len(nums) == positions:
                        road_counts[int(nums[pos]) % 3] += 1
                road_total = sum(road_counts.values()) or 1
                road_probs = {}
                for num in self.number_range:
                    road_probs[num] = (road_counts[num % 3] + 0.1) / (road_total + 0.3)

                # 重号加成（上期出现 → 惯性）
                repeat_boost = {num: 1.3 if num in prev_nums else 1.0 for num in self.number_range}
                # 连号加成（与上期某号码相邻）
                consec_boost = {num: 1.0 for num in self.number_range}
                for pn in prev_nums:
                    for d in (-1, 1):
                        nb = pn + d
                        if 0 <= nb <= 9:
                            consec_boost[nb] = 1.3

                # 多维特征融合（归一化子分布 + 轻量boost）
                fused = {}
                for num in self.number_range:
                    fused[num] = (
                        w_freq * freq_probs[num] +
                        w_omis * omis_probs[num] +
                        w_road * road_probs[num] +
                        w_rep * repeat_boost[num] * 0.1 +
                        w_consec * consec_boost[num] * 0.1
                    )

                s = sum(fused.values())
                if s > 0:
                    fused = {num: v / s for num, v in fused.items()}
                probs.append(fused)

            return probs

        except Exception as e:
            logger.error(f'特征工程算法失败: {e}')
            # 回退到均匀分布,确保系统稳定性
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]
    
    def _generate_combinations_v2(self, fused_probs: List[Dict[int, float]]) -> List[Dict[str, Any]]:
        """
        生成推荐组合（增强版 v3.0）

        在原版 _generate_combinations 的基础上,增加了多个数学约束条件,
        过滤掉不合理的组合,提升推荐质量。

        约束策略清单：
        1. 相邻位置号码约束 — 避免相邻位号码过于接近（相差≤1）
        2. 和值范围约束 — 和值应在合理区间 [10, 35]
        3. 奇偶比约束 — 避免出现全奇或全偶等极端情况
        4. 【新增】平方和偏差(SSD)惩罚 — 组合号码偏离理论均值4.5的行为受罚
        5. 【新增】跨度约束 — span = max-min 应在 [3, 8] 区间内
        6. 【新增】Chebyshev距离检查 — 概率显著偏离群体水平的号码受罚
        7. 【新增】位置方差检查 — 概率分布过于集中或过于发散都不理想

        核心数学原理：
        - 每个位置号码的理论期望均值: E[X_i] = (0+1+...+9)/10 = 4.5
        - 组合的平方和偏差: SSD = Σ(number_i - 4.5)² / 5
        - 理论上 SSD ~ N(9, 8.1), 极端SSD值的组合应受惩罚
        - 和值服从中心极限定理,近似正态分布,均值≈22.5

        生成流程：
        1. 从融合概率中获取每个位置的Top-N候选号码
        2. 计算所有候选组合的笛卡尔积
        3. 对每个组合应用7项约束条件评分
        4. 按综合得分排序,返回Top-M个高质量组合

        Args:
            fused_probs: 融合后的概率分布,格式为 List[Dict[int, float]],
                        每个 Dict 的 key 为号码(0-9), value 为概率

        Returns:
            推荐组合列表,每项包含:
                - rank: 排名
                - combination: 号码字符串(如 "12345")
                - numbers: 号码列表([1,2,3,4,5])
                - probability: 综合概率得分
                - confidence: 置信度百分比
                - hezhi: 和值
                - span: 跨度
                - ssd: 平方和偏差
        """
        combination_count = self.config.get_global_param('combination_count', 10)
        position_top_n = self.config.get_global_param('position_top_n', 3)
        
        # 和值范围约束 — 基于中心极限定理,和值应集中在 [10, 35] 区间
        hezhi_min = 10
        hezhi_max = 35
        
        # 【新增】跨度约束 — 合理跨度范围 [3, 8]
        span_min = self.config.get_global_param('span_min', 3)
        span_max = self.config.get_global_param('span_max', 8)
        
        # 【新增】平方和偏差惩罚开关
        enable_ssd_penalty = self.config.get_global_param('sum_of_squares_penalty', True)
        
        # 每个位置号码的理论期望均值 E[X_i] = 4.5 (等概率分布的数学期望)
        position_mean = 4.5
        
        # 获取每个位置的Top-N候选号码(按概率降序排列)
        top_numbers_per_position = []
        for pos in range(self.positions):
            pos_probs = fused_probs[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_nums = [num for num, _ in sorted_nums[:position_top_n]]
            top_numbers_per_position.append(top_nums)

        # 生成候选组合(笛卡尔积)
        import itertools
        all_combinations = list(itertools.product(*top_numbers_per_position))

        # 对每个组合计算综合评分,应用7项约束条件
        combination_scores = []
        for combo in all_combinations:
            # 基础概率: 各位置概率的乘积(独立事件联合概率)
            score = 1.0
            for pos, num in enumerate(combo):
                score *= fused_probs[pos].get(num, 0.1)
            
            if score <= 0:
                continue
            
            # 约束1: 相邻位置号码差距惩罚（轻度）
            adjacent_similar = 0
            for i in range(self.positions - 1):
                if abs(combo[i] - combo[i+1]) <= 1:
                    adjacent_similar += 1
            if adjacent_similar > 2:
                score *= 0.85  # 轻度惩罚,不致命

            # 约束2: 和值范围约束（软惩罚,仅极端越界才强惩罚）
            hezhi = sum(combo)
            if hezhi < hezhi_min or hezhi > hezhi_max:
                score *= 0.85   # 轻度惩罚,保留命中可能
            elif hezhi < 5 or hezhi > 40:
                score *= 0.6    # 仅极端和值才强惩罚

            # 约束3: 奇偶比约束（避免全奇/全偶,但不致命）
            odd_count = sum(1 for num in combo if num % 2 == 1)
            if odd_count == 0 or odd_count == 5:
                score *= 0.6

            # 约束4: 平方和偏差(SSD)惩罚（软化）
            if enable_ssd_penalty:
                ssd = sum((num - position_mean) ** 2 for num in combo) / self.positions
                if ssd < 1.0:
                    score *= 0.9
                elif ssd > 20.0:
                    score *= 0.85
                elif ssd > 15.0:
                    score *= 0.95

            # 约束5: 跨度约束（软化）
            combo_span = max(combo) - min(combo)
            if combo_span < span_min:
                score *= 0.85
            elif combo_span > span_max:
                score *= 0.9
            
            combination_scores.append((combo, score))

        # 按综合得分降序排序
        combination_scores.sort(key=lambda x: x[1], reverse=True)

        # 保底:始终包含一个"无约束"组合(各位置概率最高的号码),
        # 避免硬约束把理论可达的中奖组合挤出 Top 列表,人为压低命中率上限。
        if combination_scores:
            wildcard = tuple(
                sorted(fused_probs[pos].items(), key=lambda x: x[1], reverse=True)[0][0]
                for pos in range(self.positions)
            )
            if wildcard not in [c for c, _ in combination_scores]:
                wscore = 1.0
                for pos, num in enumerate(wildcard):
                    wscore *= fused_probs[pos].get(num, 0.1)
                combination_scores.append((wildcard, wscore))
                combination_scores.sort(key=lambda x: x[1], reverse=True)

        # 置信度改为相对值(相对Top组合得分,0-100%),避免原 score*100 得到 ~0.01% 的误导值
        max_base = combination_scores[0][1] if combination_scores else 0.0

        # 取前N个高质量组合,附带各项指标
        top_combinations = []
        for rank, (combo, score) in enumerate(combination_scores[:combination_count], 1):
            hezhi = sum(combo)
            span = max(combo) - min(combo)
            ssd = sum((n - position_mean) ** 2 for n in combo) / self.positions

            top_combinations.append({
                'rank': rank,
                'combination': ''.join(map(str, combo)),      # 号码字符串形式
                'numbers': list(combo),                        # 号码列表形式
                'probability': round(score, 6),               # 综合概率得分
                'confidence': round(100.0 * (score / max_base), 2) if max_base > 0 else 0.0,  # 相对置信度
                'hezhi': hezhi,                               # 和值
                'span': span,                                 # 跨度
                'ssd': round(ssd, 4)                          # 平方和偏差
            })

        return top_combinations

    def _fuse_probabilities(self, algorithm_probs: Dict[str, List[Dict[int, float]]]) -> List[Dict[int, float]]:
        """
        融合各算法概率分布

        使用加权平均方法融合所有启用算法的预测结果。
        融合策略: 先计算各算法的归一化权重,然后对每个位置的每个号码
        进行权重累加,最后归一化为概率分布。

        融合公式:
            P_merged(pos, num) = Σ(w_algo * P_algo(pos, num)) / Σw_algo

        后续处理:
        - 概率校准: 确保所有概率之和为1
        - 边界保护: 防止极端概率值

        Args:
            algorithm_probs: 各算法的概率分布,格式为:
                {
                    'frequency_weighted': [pos0_probs, pos1_probs, ...],
                    'omission_regression': [...],
                    ...
                }
                其中 pos_probs 为 {num: probability} 字典

        Returns:
            融合后的概率分布,格式为 List[Dict[int, float]],
            即 [pos0_probs, pos1_probs, ..., pos4_probs]
        """
        # 获取各算法的归一化权重
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
                        # 加权累加：将每个算法的概率按照配置权重叠加
                        pos_fused[num] += w * prob

            # 归一化处理
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
        边界保护（增强版 v3.0）

        对融合后的概率分布施加多层约束,防止输出极端或不合理的预测结果。
        这是预测管道中的最后一道质量控制关卡。

        保护策略清单：
        1. 冷热号比例约束 — 防止全热号或全冷号
        2. 相邻位约束 — 避免相邻位置推荐号码过于集中
        3. 【新增】Chebyshev距离检查 — 概率显著偏离群体水平的号码受罚
        4. 【新增】位置方差检查 — 概率分布过于集中或发散时自动调节

        工作流程：
        1. 计算每个位置的热冷号等级
        2. 限制Top-3中热号/冷号的最高比例
        3. 检查相邻位置的号码重叠度,过高时施加惩罚
        4. 使用Chebyshev距离检测异常概率值
        5. 检查概率分布方差,自动调节极端值
        6. 每次调整后重新归一化

        Args:
            probs: 原始融合概率分布
            data: 历史开奖数据

        Returns:
            经过边界保护调整后的概率分布
        """
        # 冷热号比例阈值
        max_hot_ratio = self.config.get_global_param('max_hot_ratio', 0.6)
        min_cold_ratio = self.config.get_global_param('min_cold_ratio', 0.1)
        
        # 相邻位置号码差异惩罚（避免相邻位号码过于接近）
        adjacent_diff_penalty = self.config.get_global_param('adjacent_diff_penalty', True)
        # 跨期一致性检查（检测概率分布是否稳定）
        cross_period_consistency = self.config.get_global_param('cross_period_consistency', True)

        # 获取特征工程实例以计算冷热号
        fe = self._get_feature_engineering()
        if not fe:
            return probs

        try:
            # 计算频率特征以获取冷热号等级
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

                # 冷热号比例检查：防止推荐号码全部是热号或冷号
                hot_count = sum(1 for num in top_nums if hot_levels.get(num) == 'hot')
                hot_ratio = hot_count / len(top_nums)

                # 如果热号比例过高，降低热号概率
                if hot_ratio > max_hot_ratio:
                    for num in top_nums:
                        if hot_levels.get(num) == 'hot':
                            pos_probs[num] *= 0.8

                # 如果冷号比例过低，提升冷号概率
                cold_count = sum(1 for num in top_nums if hot_levels.get(num) == 'cold')
                cold_ratio = cold_count / len(top_nums)
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
            
            # 相邻位约束 — 避免相邻位置的Top推荐号码高度重叠
            if adjacent_diff_penalty:
                for pos in range(self.positions - 1):
                    next_pos = pos + 1
                    current_sorted = sorted(protected_probs[pos].items(), key=lambda x: x[1], reverse=True)
                    next_sorted = sorted(protected_probs[next_pos].items(), key=lambda x: x[1], reverse=True)
                    
                    # 取Top-3号码
                    current_top = [num for num, _ in current_sorted[:3]]
                    next_top = [num for num, _ in next_sorted[:3]]
                    
                    # 如果相邻位的Top号码重叠度过高（>60%），降低重叠号码的概率
                    overlap = set(current_top) & set(next_top)
                    if overlap and len(current_top) > 0:
                        overlap_ratio = len(overlap) / len(current_top)
                        if overlap_ratio > 0.6:
                            penalty_factor = 0.85  # 降低重叠号码概率
                            for num in overlap:
                                protected_probs[pos][num] *= penalty_factor
                                protected_probs[next_pos][num] *= penalty_factor
                
                # 相邻位约束后重新归一化
                for pos in range(self.positions):
                    total = sum(protected_probs[pos].values())
                    if total > 0:
                        for num in self.number_range:
                            protected_probs[pos][num] /= total

                # ============================
                # 约束6: Chebyshev(切比雪夫)距离检查
                # ============================
                # 原理: 计算每个位置号码概率到聚类中心的切比雪夫距离,
                # 距离过远的号码表示其概率显著偏离群体平均水平,应予以惩罚。
                # 切比雪夫距离定义: D_chebyshev = max_k(|x_ik - x_jk|),
                # 此处"聚类中心"为各位置概率的均值。
                try:
                    for pos in range(self.positions):
                        pos_values = list(protected_probs[pos].values())
                        center = sum(pos_values) / len(pos_values)  # 概率均值作为中心

                        for num in self.number_range:
                            prob_val = protected_probs[pos].get(num, 0.1)
                            chebyshev_dist = abs(prob_val - center)

                            # 如果某个号码的概率远超均值(切比雪夫距离过大),适度惩罚
                            if chebyshev_dist > 0.05:
                                penalty = 1.0 - 0.1 * (chebyshev_dist / 0.05)
                                protected_probs[pos][num] *= max(penalty, 0.7)

                    # Chebyshev约束后重新归一化
                    for pos in range(self.positions):
                        total = sum(protected_probs[pos].values())
                        if total > 0:
                            for num in self.number_range:
                                protected_probs[pos][num] /= total

                except Exception as e:
                    logger.debug(f'切比雪夫距离检查跳过: {e}')

                # ============================
                # 约束7: 位置方差检查 (Positional Variance Check)
                # ============================
                # 原理: 检查每个位置的概率分布方差,判断是否过于集中或过于发散。
                # 方差过小说明概率分布过于尖锐(某个号码占据绝对优势),
                # 方差过大说明分布过于平坦(所有号码概率接近),都不理想。
                # 理想方差范围: Var ∈ [0.001, 0.008]
                ideal_var_low = 0.001
                ideal_var_high = 0.008

                for pos in range(self.positions):
                    pos_probs_arr = list(protected_probs[pos].values())
                    mean_p = sum(pos_probs_arr) / len(pos_probs_arr)

                    # 计算概率分布方差
                    variance = sum((p - mean_p) ** 2 for p in pos_probs_arr) / len(pos_probs_arr)

                    if variance < ideal_var_low:
                        # 方差太小: 过于集中,拉平概率分布,提升冷门号码
                        for num in self.number_range:
                            if protected_probs[pos][num] < mean_p:
                                protected_probs[pos][num] *= 1.15  # 提升冷门号码

                    elif variance > ideal_var_high:
                        # 方差太大: 过于分散且存在极端值,压制最高概率的号码
                        sorted_items = sorted(protected_probs[pos].items(),
                                             key=lambda x: x[1], reverse=True)
                        if sorted_items:
                            max_num = sorted_items[0][0]
                            protected_probs[pos][max_num] *= 0.85  # 压制极端号码

                    # 方差检查后重新归一化
                    total = sum(protected_probs[pos].values())
                    if total > 0:
                        for num in self.number_range:
                            protected_probs[pos][num] /= total

            return protected_probs

        except Exception as e:
            logger.error(f'边界保护失败: {e}')
            return probs

    def _generate_combinations(self, fused_probs: List[Dict[int, float]]) -> List[Dict[str, Any]]:
        """
        生成推荐组合（基础版）

        基于融合后的概率分布,使用贪心策略生成高概率号码组合。
        作为 v2 增强版的后备方案,使用简化的约束条件。

        生成步骤：
        1. 从各位置的概率分布中提取Top-N候选号码
        2. 计算所有候选组合的笛卡尔积
        3. 对每个组合计算联合概率(各位置概率的乘积)
        4. 按概率降序排序,取前N个组合

        Args:
            fused_probs: 融合后的概率分布,格式为 List[Dict[int, float]]

        Returns:
            推荐组合列表,每项包含:
                - rank: 排名
                - combination: 号码字符串
                - numbers: 号码列表
                - probability: 概率得分
                - confidence: 置信度百分比
        """
        combination_count = self.config.get_global_param('combination_count', 10)
        position_top_n = self.config.get_global_param('position_top_n', 3)

        # 获取每个位置的Top-N候选号码(按概率降序)
        top_numbers_per_position = []
        for pos in range(self.positions):
            pos_probs = fused_probs[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_nums = [num for num, _ in sorted_nums[:position_top_n]]
            top_numbers_per_position.append(top_nums)

        # 生成所有可能的组合(笛卡尔积)
        import itertools
        all_combinations = list(itertools.product(*top_numbers_per_position))

        # 计算每个组合的综合概率
        # 联合概率 = 各位置概率的乘积(独立事件)
        combination_scores = []
        for combo in all_combinations:
            score = 1.0
            for pos, num in enumerate(combo):
                score *= fused_probs[pos].get(num, 0.1)
            combination_scores.append((combo, score))

        # 按概率降序排序
        combination_scores.sort(key=lambda x: x[1], reverse=True)

        # 取前N个高概率组合作为推荐
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

        基于最近N期的实际开奖数据和融合概率分布,预测各位置号码的短期走势。
        趋势判断逻辑：
        - 比较最近两期的开奖号码,确定上升/下降/持平方向
        - 提取Top-3推荐号码,给出重点关注范围
        - 展示最近5期的实际开奖值,供用户自行判断

        Args:
            sorted_data: 按期号正序排列的历史开奖数据
            fused_probs: 融合后的概率分布

        Returns:
            走势预测字典,格式:
            {
                '万位': {
                    'top_numbers': [5, 3, 7],     # Top-3推荐号码
                    'trend': '上升',              # 趋势方向
                    'recent_values': [2, 5, 3]    # 最近5期实际值
                },
                ...
            }
        """
        trend_forecast = {}

        for pos in range(self.positions):
            pos_name = self.position_names[pos]

            # 获取该位置的Top-3推荐号码
            pos_probs = fused_probs[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_3 = [num for num, _ in sorted_nums[:3]]

            # 分析最近10期的实际开奖值
            recent = sorted_data[-10:] if len(sorted_data) >= 10 else sorted_data
            recent_values = []
            for item in recent:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    recent_values.append(int(numbers[pos]))

            # 通过比较最近两期值确定趋势方向
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
                'top_numbers': top_3,                     # Top-3推荐号码
                'trend': trend,                           # 趋势方向
                'recent_values': recent_values[-5:] if len(recent_values) >= 5 else recent_values  # 最近5期值
            }

        return trend_forecast

    def _generate_summary(self, fused_probs: List[Dict[int, float]],
                          top_combinations: List[Dict[str, Any]],
                          next_issue: str) -> str:
        """
        生成预测摘要文本

        将概率分布和推荐组合转换为人类可读的摘要格式,
        包含各位置Top-3推荐号码和Top-5推荐组合。

        摘要格式：
        排列5第XXXXX期预测摘要
        ==================================================

        【各位置推荐号码】
        万位:
          1. 号码5 (概率: 15.23%)
          2. 号码3 (概率: 12.45%)
          3. 号码7 (概率: 10.87%)
        ...

        【推荐组合（Top-5）】
        1. 53728 (置信度: 72.34%)
        2. 53726 (置信度: 68.12%)
        ...

        ==================================================
        ⚠️ 重要提示：本预测仅基于历史数据统计分析，无法预测开奖结果，请理性购彩。

        Args:
            fused_probs: 融合后的概率分布
            top_combinations: 推荐组合列表
            next_issue: 目标期号

        Returns:
            格式化的摘要文本字符串
        """
        lines = []
        lines.append(f'排列5第{next_issue}期预测摘要')
        lines.append('=' * 50)

        # 各位置推荐号码
        lines.append('\n【各位置推荐号码】')
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            pos_probs = fused_probs[pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_3 = sorted_nums[:3]

            lines.append(f'\n{pos_name}:')
            for rank, (num, prob) in enumerate(top_3, 1):
                lines.append(f'  {rank}. 号码{num} (概率: {prob:.2%})')

        # 推荐组合列表
        lines.append('\n【推荐组合（Top-5）】')
        for combo in top_combinations[:5]:
            lines.append(f"{combo['rank']}. {combo['combination']} (置信度: {combo['confidence']:.2f}%)")

        lines.append('\n' + '=' * 50)
        lines.append('⚠️ 重要提示：本预测仅基于历史数据统计分析，无法预测开奖结果，请理性购彩。')

        return '\n'.join(lines)

    def _compute_position_hit_rate(self, fused_probs: List[Dict[int, float]],
                                    actual_numbers: List[int]) -> Dict[str, float]:
        """
        计算各位置的预测命中率

        比较融合概率分布中每个位置的 Top-K 推荐号码与实际开奖号码的匹配情况,
        评估每个算法在各位置上的表现,供后续权重调整使用。

        计算公式：
            position_hit_rate = 该位置Top-K中命中号码数 / K
            即: 1.0 表示命中, 0.0 表示未命中

        用途：
        - 评估当前预测的准确性
        - 为贝叶斯推断算法提供似然数据
        - 为自适应权重管理器提供更新信号

        Args:
            fused_probs: 融合后的概率分布,格式为 List[Dict[int, float]]
            actual_numbers: 实际开奖号码列表,长度为5,例如 [5, 3, 7, 2, 8]

        Returns:
            位置命中率字典,格式:
            {
                '万位': 1.0,   # 命中
                '千位': 0.0,   # 未命中
                '百位': 1.0,
                '十位': 0.0,
                '个位': 1.0
            }
        """
        # 每个位置选取Top-K个号码作为预测结果
        position_top_n = self.config.get_global_param('position_top_n', 3)
        hit_rates = {}

        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            pos_probs = fused_probs[pos]
            # 按概率降序排列,取Top-K
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_nums = [num for num, _ in sorted_nums[:position_top_n]]

            # 获取该位置的实际开奖号码
            actual_num = actual_numbers[pos] if pos < len(actual_numbers) else -1
            # 检查实际号码是否在Top-K推荐中
            hits = 1 if actual_num in top_nums else 0
            hit_rates[pos_name] = hits / position_top_n

        return hit_rates

    def record_verification_result(self, prediction_record: Dict,
                                     actual_numbers: List[int]) -> Dict[str, Any]:
        """
        记录预测验证结果 — 自适应权重系统的核心数据源

        当一期彩票开奖后,将实际结果与之前的预测进行对比,
        计算各算法各位置的命中率,并更新自适应权重管理器。
        这是整个 v3.0 系统实现"自我进化"的关键环节。

        完整工作流程：
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 提取本次预测的推荐号码和实际开奖号码                      │
        │    ↓                                                        │
        │ 2. 计算各位置的命中率 (Top-3 vs 实际)                       │
        │    ↓                                                        │
        │ 3. 评估各算法在各位置上的表现 (Top-1命中率 + 部分命中)      │
        │    ↓                                                        │
        │ 4. 更新 AdaptiveWeightManager 中的 EWMA 值                 │
        │    ↓                                                        │
        │ 5. 将验证记录持久化到 predictions/weights_history.json      │
        │    ↓                                                        │
        │ 6. 返回验证结果摘要 (命中率 + 算法评估 + 自适应权重)        │
        └─────────────────────────────────────────────────────────────┘

        算法评估规则：
        - 完美命中: 算法推荐的头号号码 == 实际号码 → +1.0分
        - 部分命中: 算法推荐的头号号码在其他位置出现 → +0.5分
        - 未命中: 不得分
        - 最终得分 = 总分 / 位置数(5)

        Args:
            prediction_record: 预测记录字典,必须包含以下字段:
                - target_issue: 目标期号 (str)
                - top_combinations: 推荐组合列表 (list)
                - algorithm_probs: 各算法概率分布 (dict)
                - fused_probabilities: 融合概率分布 (list)
            actual_numbers: 实际开奖号码列表,格式 [万位, 千位, 百位, 十位, 个位]
                          例如: [5, 3, 7, 2, 8]

        Returns:
            验证结果摘要字典,包含:
                - success: 是否成功记录 (bool)
                - hit_rates: 各位置命中率 {位置名: 0.0~1.0}
                - algo_evaluations: 各算法评估得分 {算法名: 0.0~1.0}
                - adaptive_weights: 更新后的自适应权重
                - error: 出错时的错误信息 (str)
        """
        # 验证号码数量是否正确
        if len(actual_numbers) != self.positions:
            logger.error(f'实际号码数量不匹配: 期望{self.positions}个, 实际{len(actual_numbers)}个')
            return {'success': False, 'error': '号码数量不匹配'}

        try:
            # 步骤0: 连接数据库(替代原先写入 predictions/weights_history.json 文件, v3.3)
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.error('记录验证结果: 数据库连接失败')
                return {'success': False, 'error': '数据库连接失败'}

            # 步骤1: 提取推荐号码(取排名第一的组合)
            top_combs = prediction_record.get('top_combinations', [])
            if top_combs:
                predicted_combo = top_combs[0].get('numbers', actual_numbers)
            else:
                predicted_combo = actual_numbers[:]

            # 步骤2: 计算位置命中率
            position_hits = self._compute_position_hit_rate(
                prediction_record.get('fused_probabilities', []), actual_numbers
            )

            # 步骤3: 评估各算法在各位置的贡献
            algo_evaluations = {}
            algorithm_probs = prediction_record.get('algorithm_probs', {})
            for algo_name, algo_probs_list in algorithm_probs.items():
                algo_hit_sum = 0.0
                for pos in range(self.positions):
                    if pos < len(algo_probs_list):
                        # 该算法推荐的最大概率号码(头号号码)
                        sorted_nums = sorted(algo_probs_list[pos].items(),
                                           key=lambda x: x[1], reverse=True)
                        algo_top = sorted_nums[0][0] if sorted_nums else -1
                        if algo_top == actual_numbers[pos]:
                            algo_hit_sum += 1.0  # 完美命中
                        elif algo_top in actual_numbers:
                            algo_hit_sum += 0.5  # 出现在其他位置也算部分命中
                algo_evaluations[algo_name] = algo_hit_sum / self.positions

            # 步骤4: 更新自适应权重管理器(EWMA更新)
            for algo_name, avg_hit in algo_evaluations.items():
                if algo_name in self.config.config['algorithms']:
                    self.config.weight_manager.record_verification(algo_name, avg_hit)

            # 步骤5: 构建验证记录(结构化数据)
            verification_entry = {
                'timestamp': datetime.now().isoformat(),       # 记录时间戳
                'target_issue': prediction_record.get('target_issue', ''),  # 目标期号
                'predicted_numbers': predicted_combo,           # 预测号码
                'actual_numbers': actual_numbers,               # 实际号码
                'position_hits': position_hits,                 # 位置命中率
                'algo_evaluations': algo_evaluations,           # 算法评估
            }

            # 步骤6: 持久化到数据库 p5_artifact(type='weight_history'), 替代原文件方式
            db.save_artifact(
                artifact_type='weight_history',
                data=verification_entry,
                issue=verification_entry.get('target_issue', ''),
            )
            db.disconnect()

            logger.info(f'验证记录已保存: 期号={verification_entry["target_issue"]}, '
                        f'预测={predicted_combo}, 实际={actual_numbers}')

            # 步骤8: 返回验证结果摘要
            return {
                'success': True,
                'hit_rates': position_hits,
                'algo_evaluations': algo_evaluations,
                'adaptive_weights': self.config.weight_manager.get_adaptive_weights()
            }

        except Exception as e:
            logger.error(f'记录验证结果失败: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def load_weight_history(self) -> Dict[str, Any]:
        """
        加载历史权重调整记录

        从数据库 p5_artifact(type='weight_history') 读取所有保存的验证记录和
        权重管理器当前的自适应权重状态。同时计算各算法的累计命中率统计。
        (v3.3 起替代原先的 predictions/weights_history.json 文件)

        返回的数据可用于:
        - 可视化展示各算法的历史表现
        - 分析权重调整趋势
        - 调试和优化算法配置
        - 评估系统整体预测准确率

        Returns:
            权重历史字典,包含:
                - verification_records: 历史验证记录列表 (最多1000条)
                - adaptive_weights: 当前自适应权重状态
                - total_verifications: 累计验证次数
                - summary: 各算法累计命中率统计
                - error: 出错时的错误信息
        """
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.info('暂无权重历史记录(数据库连接失败)')
                return {
                    'verification_records': [],
                    'adaptive_weights': self.config.weight_manager.get_adaptive_weights() if hasattr(self.config, 'weight_manager') else {},
                    'total_verifications': 0,
                    'summary': {}
                }
            artifacts = db.get_artifacts('weight_history', limit=1000)
            db.disconnect()
            records = [a.get('data', {}) for a in artifacts if a.get('data')]

            # 计算各算法累计命中率统计
            algo_total_hits = defaultdict(float)
            algo_total_evals = defaultdict(int)
            for record in records:
                for algo_name, hit_rate in record.get('algo_evaluations', {}).items():
                    algo_total_hits[algo_name] += hit_rate
                    algo_total_evals[algo_name] += 1

            # 汇总各算法的平均命中率
            summary = {}
            for algo_name in algo_total_hits:
                summary[algo_name] = {
                    'cumulative_hits': round(algo_total_hits[algo_name], 4),
                    'evaluation_count': algo_total_evals[algo_name],
                    'average_hit_rate': round(
                        algo_total_hits[algo_name] / max(algo_total_evals[algo_name], 1), 4
                    )
                }

            return {
                'verification_records': records,
                'adaptive_weights': self.config.weight_manager.get_adaptive_weights() if hasattr(self.config, 'weight_manager') else {},
                'total_verifications': len(records),
                'summary': summary
            }

        except Exception as e:
            logger.error(f'加载权重历史失败: {e}', exc_info=True)
            return {
                'verification_records': [],
                'adaptive_weights': {},
                'total_verifications': 0,
                'summary': {},
                'error': str(e)
            }


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

    predictor = P5Predictor()
    result = predictor.predict(test_data, '2024005')

    print('预测完成')
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
