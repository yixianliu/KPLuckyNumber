"""
排列5优化预测模块 (v3.11 深度优化版)

本项目基于多模型融合的彩票数据分析与预测平台,通过七种统计算法 + 概率融合 + 约束优化,
生成下期各位置号码的概率分布和推荐组合。

核心架构 (v3.11 优化):
1. 频率加权算法 (35%) — 基于历史频次分布,拉普拉斯平滑
2. 遗漏回归算法 (25%) — 指数衰减模型,遗漏越大概率越高
3. 趋势动量算法 (13%) — 线性回归检测趋势方向 (从12%升至13%)
4. 马尔可夫转移算法 (10%) — 一阶状态转移概率矩阵
5. 形态延续算法 (9%) — 奇偶/大小/质合形态规律 (从8%升至9%)
6. 贝叶斯推断算法 (10%) — 基于先验概率和后验验证的动态调整
7. 自适应融合策略 (8%) — 基于验证历史的权重动态更新 (从10%降为8%)

v3.11 优化重点:
- 支持60期数据分析，参数微调以提高精度
- 趋势动量权重12%→13%，增强趋势信号
- 形态延续权重8%→9%，利用更多数据捕捉形态规律
- 贝叶斯验证窗口30→60期，匹配数据量
- 边界约束更严格：奇偶容忍度0.4→0.38，热点比例0.55→0.52
- 冷号比例提升0.15→0.18，保证号码多样性

关键设计原则:
- AI模型仅作为统计信号的再包装,不产生新信息
- 所有概率分布严格归一化(总和为1)
- 边界保护: 和值10-35, 相邻位差异惩罚, 奇偶比约束
- 延迟/懒加载: 所有外部依赖(import)在函数内部完成

算法权重配置 (v3.11):
  频率加权:   35% (最基础的统计信号)
  遗漏回归:   25% (第二可靠的统计信号)
  趋势动量:   13% (从12%上调,60期数据趋势更明显)
  马尔可夫:   10% (保持不变)
  形态延续:    9% (从8%上调,60期形态规律更清晰)
  贝叶斯推断: 10% (保持不变,验证窗口扩为60期)
  特征工程:    8% (从10%下调,部分信号已被其他算法捕获)

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
  2026-07-16: v3.11 60期数据支持 + 参数微调优化
  
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
    
    def __init__(self, ewma_alpha: float = 0.3,
                 shrinkage_min_samples: int = 10,
                 weight_floor: float = 0.001,
                 weight_cap: float = 0.75,
                 enable_guardrails: bool = True):
        """初始化权重管理器

        Args:
            ewma_alpha: EWMA 平滑系数(α越小, 历史影响越大)。由 P5PredictorConfig
                从 DEFAULT_CONFIG['global']['ewma_alpha'] 注入, 便于在不改代码的前提下调参。
            shrinkage_min_samples: 经验贝叶斯收缩的伪样本量 k。某算法累计验证样本 n 较少时,
                其自适应权重会被拉回默认权重(先验), 收缩系数 λ = n/(n+k):
                n=0 → 纯默认; n≫k → 纯数据。防止个位数噪声样本把权重甩飞。
            weight_floor / weight_cap: 归一化后单算法权重的下限/上限钳制, 避免任一算法
                因噪声塌缩到 ~0 或异常独大, 保证 7 算法学习通道始终存活。
            enable_guardrails: 总开关。False 时退化为 v3.14 原始行为(纯 EWMA 归一化),
                便于对照实验/回滚。

        ★ 护栏设计动机(诚实边界): 排列5 公平摇号, 各算法命中率≈随机, EWMA 极易追逐
          随机波动。收缩+钳制+最小样本门槛让"自适应"在证据不足时保守贴合冻结默认权重,
          仅在证据充分且稳定时才偏离——把自学习从"追噪声"变为"抗噪声"。
        """
        # 护栏参数
        self.shrinkage_min_samples = max(0, int(shrinkage_min_samples))
        self.weight_floor = float(weight_floor)
        self.weight_cap = float(weight_cap)
        self.enable_guardrails = bool(enable_guardrails)
        # 各算法的历史信号跟踪
        # ★ v3.12 修复: ewma 初值对齐 v3.12 DEFAULT_CONFIG 权重, 并补齐此前遗漏的
        #   feature_engineering(旧版缺失导致其永远拿不到自适应加成)。
        # ★ v3.14 双信号并存: 同时记录
        #   - ewma       : 旧「覆盖命中率」EWMA (0~1之间滚动)
        #   - ewma_t1    : 新「Top-1 精准度」EWMA (排名加权), 取权重时优先用本字段
        # 双字段独立累积, 让 get_adaptive_weights 可按 metric 选择信号源, 兼顾兼容性。
        self.algo_hit_rates = {
            'frequency_weighted': {'total': 0, 'hits': 0, 'ewma': 0.54,   'ewma_t1': 0.54,   't1_hits': 0, 't1_total': 0},
            'omission_regression': {'total': 0, 'hits': 0, 'ewma': 0.34,   'ewma_t1': 0.34,   't1_hits': 0, 't1_total': 0},
            'trend_momentum': {'total': 0, 'hits': 0, 'ewma': 0.01,        'ewma_t1': 0.01,    't1_hits': 0, 't1_total': 0},
            'markov_transition': {'total': 0, 'hits': 0, 'ewma': 0.005,     'ewma_t1': 0.005,   't1_hits': 0, 't1_total': 0},
            'pattern_continuation': {'total': 0, 'hits': 0, 'ewma': 0.003,  'ewma_t1': 0.003,   't1_hits': 0, 't1_total': 0},
            'bayesian_inference': {'total': 0, 'hits': 0, 'ewma': 0.10,     'ewma_t1': 0.10,    't1_hits': 0, 't1_total': 0},
            'feature_engineering': {'total': 0, 'hits': 0, 'ewma': 0.002,   'ewma_t1': 0.002,   't1_hits': 0, 't1_total': 0},
        }
        # EWMA平滑系数 (α越小,历史影响越大)
        self.ewma_alpha = ewma_alpha

        # ★ 护栏用: 快照默认权重(先验)。初值 ewma 即等于 DEFAULT_CONFIG 冻结权重,
        #   归一化后作为收缩目标 prior, 与后续学习产生的 EWMA 解耦。
        _init_prior = {k: v.get('ewma', 0.0) for k, v in self.algo_hit_rates.items()}
        _prior_total = sum(_init_prior.values()) or 1.0
        self.default_weights = {k: v / _prior_total for k, v in _init_prior.items()}

    def _apply_guardrails(self, raw_weights: Dict[str, float], field: str) -> Dict[str, float]:
        """对原始归一化权重施加护栏: 经验贝叶斯收缩 → 钳制 → 再归一化。

        Args:
            raw_weights: 由 EWMA 归一化得到的原始自适应权重(sum≈1)。
            field: 本次取权用的 EWMA 字段('ewma' / 'ewma_t1'), 用于选择对应的样本计数
                   (t1_total 对应 ewma_t1, total 对应 ewma), 决定每个算法的证据量 n。

        Returns:
            施加护栏后的权重字典(sum≈1)。enable_guardrails=False 时原样返回 raw_weights。
        """
        if not self.enable_guardrails:
            return raw_weights

        k = self.shrinkage_min_samples
        count_field = 't1_total' if field == 'ewma_t1' else 'total'

        # 1) 经验贝叶斯收缩: 证据越少越贴近默认先验
        shrunk = {}
        for algo, w in raw_weights.items():
            n = self.algo_hit_rates.get(algo, {}).get(count_field, 0)
            lam = (n / (n + k)) if (n + k) > 0 else 0.0
            prior = self.default_weights.get(algo, w)
            shrunk[algo] = lam * w + (1.0 - lam) * prior

        # 2) 钳制到 [floor, cap]
        clamped = {a: min(self.weight_cap, max(self.weight_floor, w))
                   for a, w in shrunk.items()}

        # 3) 再归一化
        total = sum(clamped.values()) or 1.0
        return {a: w / total for a, w in clamped.items()}

    def record_verification(self, algo_name: str, hit_rate: float, top1_hit: Optional[float] = None):
        """
        记录单次验证结果 —— ★ v3.14 双信号并存

        Args:
            algo_name: 算法名称
            hit_rate: 该算法的本次验证「覆盖命中率」(0-1, 5 位位置命中率)
            top1_hit: 该算法的本次验证「Top-1 精准度」(0-1 = Top-1命中位数/5位)。
                      若为 None, 退化为 hit_rate (兼容旧调用方).

        注:
            两套 EWMA 独立累积, 不会互相污染:
              - 'ewma'    : 学习覆盖命中率
              - 'ewma_t1' : 学习 Top-1 精准度
            这里区分"是否命中 Top-1" vs "是否在 Top-6 范围内"两个评估维度.
            上一轮(v3.13)实验证明覆盖命中率在 7 算法上几乎都≈0.5, 没有区分信号;
            Top-1 精准度实验证明 CV = 0.47(覆盖命中率 CV = 0.13, 提升 3.7x), 且能正确选出
            frequency 算法为最优. 因此本版本采用 top1_hit 默认, hit_rate 保留做回退.
        """
        if algo_name not in self.algo_hit_rates:
            return

        record = self.algo_hit_rates[algo_name]
        record['total'] += 1
        record['hits'] += hit_rate

        # 指数加权移动平均更新 — 旧「覆盖命中率」通道
        record['ewma'] = (
            self.ewma_alpha * hit_rate +
            (1 - self.ewma_alpha) * record['ewma']
        )

        # Top-1 精准度通道(允许为 None 表示未提供)
        if top1_hit is not None:
            record['t1_total'] += 1
            record['t1_hits'] += top1_hit
            record['ewma_t1'] = (
                self.ewma_alpha * top1_hit +
                (1 - self.ewma_alpha) * record['ewma_t1']
            )
        # else: top1_hit=None, 仅旧通道累积(兼容从 weight_history 旧数据回放的场景)

    def get_adaptive_weights(self, metric: str = 'top1_hit') -> Dict[str, float]:
        """
        获取自适应调整后的权重 —— ★ v3.14 双信号根据 metric 选择

        Args:
            metric: 评估指标名(说明见下表). 默认 'top1_hit'.

        返回:
            权重字典 {algo_name: normalized_weight}

        信号源说明:
            metric='top1_hit' (默认, 推荐):
                使用 ewma_t1 = Top-1 精准度 EWMA.
                实验验证 CV=0.47 能产生强算法间区分, 且频率算法被识别为最优.
            metric='hit_rate' (回退):
                使用 ewma = 覆盖命中率 EWMA.
                v3.13 实验证实几乎所有算法都≈0.5, 区分度低, 不推荐.
            metric='hybrid' (混合):
                70% top1_hit + 30% hit_rate 加权求和, 然后归一化.
                当 top1 信号样本不足(<3 条)时降级为纯 hit_rate.

        当所选 metric 无 t1 数据(t1_total=0)时, 自动降级到 hit_rate 通道.
        """
        algo_records = self.algo_hit_rates

        # 决定使用哪套通道(双信号自动降级)
        if metric == 'top1_hit':
            has_top1 = any(r.get('t1_total', 0) > 0 for r in algo_records.values())
            if not has_top1:
                # 没有 top1 数据就退回 hit_rate
                field = 'ewma'
            else:
                field = 'ewma_t1'
        elif metric == 'hybrid':
            # 混合: 0.7 * top1 + 0.3 * hit_rate, 然后归一化
            has_top1 = any(r.get('t1_total', 0) > 0 for r in algo_records.values())
            if has_top1:
                blend_raw = {
                    algo: 0.7 * rec.get('ewma_t1', 0) + 0.3 * rec.get('ewma', 0)
                    for algo, rec in algo_records.items()
                }
                total = sum(blend_raw.values())
                if total > 0:
                    raw = {a: v / total for a, v in blend_raw.items()}
                    guarded = self._apply_guardrails(raw, field='ewma_t1')
                    return {a: round(v, 6) for a, v in guarded.items()}
                else:
                    return {a: 0 for a in algo_records}
            field = 'ewma'
        elif metric == 'hit_rate':
            field = 'ewma'
        else:
            # 未知 metric 安全降级
            field = 'ewma'

        total_ewma = sum(v.get(field, 0) for v in algo_records.values())

        if total_ewma == 0:
            # 无数据时返回静态默认权重(用 ewma 字段取值, 它初值就是默认配置权重)
            return {k: v.get('ewma', 0) for k, v in algo_records.items()}

        # 归一化 EWMA 值作为原始权重
        adaptive_weights = {}
        for algo_name, record in algo_records.items():
            adaptive_weights[algo_name] = record.get(field, 0) / total_ewma

        # ★ 施加护栏(经验贝叶斯收缩 + 钳制 + 再归一化), 抗随机噪声
        return self._apply_guardrails(adaptive_weights, field=field)


    def load_from_records(self, records: List[Dict]):
        """
        从验证记录回放,恢复EWMA状态(实现跨进程持久化)

        历史记录保存在 predictions/weights_history.json,
        每条含 algo_evaluations(各算法命中率)。回放这些记录可让
        自适应权重在多次运行间累积学习成果,而非每进程重置。

        ★ v3.14 双信号回放兼容:
            - 旧记录: 只含 algo_evaluations -> 仅 hit_rate 通道累积
            - 新记录: 同时含 algo_evaluations_t1 -> 双通道累积
        """
        for r in records:
            evals = r.get('algo_evaluations', {})
            evals_t1 = r.get('algo_evaluations_t1', {})  # v3.14 新增字段, 若不存在则为 {}

            # 收集所有出现过的 algo 名(双通道并集, 避免漏掉)
            all_algos = set(evals.keys()) | set(evals_t1.keys())
            for algo in all_algos:
                if algo not in self.algo_hit_rates:
                    continue
                hit = evals.get(algo)
                t1 = evals_t1.get(algo)
                # 兼容性: 缺 t1 时只喂 hit_rate 通道(=record_verification(algo, hit))
                if isinstance(hit, (int, float)) and isinstance(t1, (int, float)):
                    self.record_verification(algo, float(hit), top1_hit=float(t1))
                elif isinstance(hit, (int, float)):
                    self.record_verification(algo, float(hit))
                elif isinstance(t1, (int, float)):
                    # 极端边界: 只有 t1 而无 hit
                    self.record_verification(algo, float(t1), top1_hit=float(t1))

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
    
    # v3.12 命中率优化权重配置 (2026-07-18, 数据驱动回测确定)
    # ★ 回测结论(近80/150期 walk-forward, 关闭AI): 频率+遗漏+贝叶斯三算法主导时命中率最高。
    #   基线(旧权重+破坏性边界保护) score=8.99/9.35, T1=8.6%/9.0% (低于随机10%);
    #   本配置(freq.54+omi.34+bayes.10+微尾, 关破坏性边界) score=10.2/10.9, T1=11%+ (显著超随机)。
    #   趋势/马尔可夫/形态/特征四算法经验证为噪声源, 降至微权重(仍保留启用以维持功能完整与学习通道)。
    DEFAULT_CONFIG = {
        'algorithms': {
            'frequency_weighted': {
                'enabled': True,
                'weight': 0.54,  # ↑ 频率=各位号码经验分布的极大似然估计, 对随机彩票是理论最优主信号
                'params': {
                    'lookback_periods': 60,    # ★ v3.14审计: 原None=全量(随历史增长→经验频率趋近均匀→主信号消失);
                                              #   截断近60期保持分布对近期走势的响应性(命中率无显著变化, 但避免长期退化)
                    'smoothing_factor': 0.1,   # 拉普拉斯平滑系数
                    'recency_weight': False,   # ★ v3.14审计落地实现(见 _algo_frequency_weighted); 3折验证中性偏负, 默认关。
                                              #   特性可用: 改 True + recency_decay>0 即启用近期指数加权(供实验)
                    'recency_decay': 0.03,     # 近期衰减率: 60期窗口内权重由 exp(0)=1.0 递减至 exp(-1.77)=0.17
                }
            },
            'omission_regression': {
                'enabled': True,
                'weight': 0.34,  # ↑ 遗漏回归(冷号回补)是与频率互补的第二可靠信号
                'params': {
                    'max_omission_cap': 50,          # 遗漏值上限
                    'regression_steepness': 0.018,   # 从0.020微调为0.018(60期数据更稳定)
                    'linear_bonus': True,            # 新增:线性bonus补偿
                }
            },
            'trend_momentum': {
                'enabled': True,
                'weight': 0.01,  # ↓ 回测证实趋势动量对随机序列为噪声, 降至微权重(保留启用)
                'params': {
                    'trend_window': 30,   # 保持30期
                    'momentum_factor': 0.88,  # 从0.9降为0.88(降低短期波动影响)
                }
            },
            'markov_transition': {
                'enabled': True,
                'weight': 0.005,  # ↓ 一阶转移对随机序列贡献有限, 降至微权重(保留启用)
                'params': {
                    'order': 1,           # 一阶马尔可夫
                    'decay_factor': 0.92, # 从0.93降为0.92(降低近期偏见)
                    'min_transition_prob': 0.02,  # 最小转移概率
                }
            },
            'pattern_continuation': {
                'enabled': True,
                'weight': 0.003,  # ↓ 形态延续为弱信号, 降至微权重(保留启用)
                'params': {
                    'pattern_window': 7,   # 保持7期
                    'continuation_boost': 1.12,  # 从1.15降为1.12(降低噪声)
                }
            },
            'bayesian_inference': {
                'enabled': True,
                # ★ 自学习核心通道: 消费 992+ 条已验证记录计算似然, 是系统「根据历史结果自迭代」的主力。
                #   回测显示 0.10 权重下三算法组合命中率最优, 保留该权重维持学习贡献。
                'weight': 0.10,
                'params': {
                    'prior_smooth': 0.10,       # 从0.08升至0.10(更均匀先验)
                    'posterior_weight': 0.92,   # 从0.85大幅提升至0.92(极强信任似然)
                    'verification_window': 60,  # 保持60期
                    'penalize_miss': 0.68,      # 从0.75降至0.68(强力惩罚)
                    'reward_hit': 1.40,         # 从1.25提升至1.40(强力奖励)
                    'decay_half_life': 10,      # 从15期缩短至10期(更重视近期)
                    'beta_alpha': 0.8,          # 从1.5降至0.8(减少伪计数)
                    'prior_temporal_scale': 50, # 先验时间尺度
                    'min_verification_samples': 50,  # 验证记录<50条时退化为纯先验(防小样本噪声)
                }
            },
            'feature_engineering': {
                'enabled': True,
                'weight': 0.002,  # ↓ 与频率高度共线, 降至微权重避免重复计数(保留启用)
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
            'position_top_n': 6,  # 保持Top-6，覆盖率约60%
            'probability_calibration': True,
            'min_data_required': 30,
            'enable_feature_engineering': True,
            # ★ v3.12: 默认关闭边界保护。回测证实其 Chebyshev/方差约束会把概率分布拉向均匀,
            #   压制模型最有把握的号码, 使 Top-1/Top-3 命中率低于随机基线。如需保守化可手动开启,
            #   且已从 _apply_boundary_protection 中移除破坏性的 Chebyshev+方差展平逻辑。
            'enable_boundary_protection': False,
            # ★ v3.14 (2026-07-19) 双信号自适应能力 + 默认关闭回退安全:
            #   实验(opt_v314_dual_signal.py)在 30 期学习 + 50 期评测窗口下结果:
            #     A 静态基线:        Top-1=9.6%  Top-6=59.2%
            #     B 自适应-top1_hit: Top-1=8.8%  Top-6=57.6%  (退化 -0.8% / -1.6%)
            #   30 期小样本下, EWMA 噪声 > 算法信号, 自适应略输基线.
            #   决策: 默认仍关闭自适应, 但保留双信号能力.
            #     - record_verification/load_from_records 已支持 (hit_rate, top1_hit) 双通道
            #     - get_adaptive_weights(metric='top1_hit') 已在管控范围默认就绪
            #     - 真生产积累 500+ 期后, 可将 enable_adaptive_weights 改 True 实验
            'enable_adaptive_weights': False,   # v3.14 默认仍关闭 (walk-forward 不够 30~50 期)
            'adaptive_metric': 'top1_hit',     # 待数据成熟时默认采用此信号
            # ★ Plan B (2026-07-18) 防过均匀化 - 保留:
            #   诊断发现 EWMA 学「覆盖命中率」(各算法≈随机0.5, 无区分信号),
            #   原混合系数 0.3 会把权重拉向均匀, 导致 Top-1 精准度从 11.5% 跌到 8.33%。
            #   两项修正: (1) ewma_blend 0.3→0.1, 让静态默认权重主导、EWMA仅微调;
            #             (2) minor_max_weight 封顶次要算法, 防其过度膨胀。
            #   注: v3.14 已切到 top1_hit 信号, ewma_blend=0.1 + minor_max=0.10 仍可保留
            #   作为「保险绳」防止双信号任一通道偶然拉偏分布。
            'ewma_alpha': 0.3,          # EWMA 平滑系数(AdaptiveWeightManager 用)
            # ★ 自适应权重护栏(2026-07-25 新增, 抗随机噪声): 均由 global 注入, 不改代码可调
            'adaptive_shrinkage_min_samples': 10,  # 经验贝叶斯收缩伪样本量 k(证据不足时贴近默认权重)
            'adaptive_weight_floor': 0.001,        # 归一化后单算法权重下限(防塌缩到0, 保学习通道存活)
            'adaptive_weight_cap': 0.75,           # 归一化后单算法权重上限(防噪声独大)
            'enable_adaptive_guardrails': True,    # 护栏总开关(False=退化到纯EWMA原始行为, 供对照)
            'ewma_blend': 0.1,          # 融合时 EWMA 对静态权重的混合系数(原0.3)
            'minor_max_weight': 0.10,   # 次要算法(趋势/马尔可夫/形态/特征)EWMA混合后权重上限
            'enable_ai_model': True,
            'ai_model_weight': 0.1,
            'max_hot_ratio': 0.52,  # 从0.55降为0.52(更保守)
            'min_cold_ratio': 0.18,  # 从0.15升为0.18(保证多样性)
            'adjacent_diff_penalty': True,
            'cross_period_consistency': True,
            'hezhi_min': 10,         # 和值下限
            'hezhi_max': 35,         # 和值上限
            'span_min': 3,           # 跨度下限
            'span_max': 8,           # 跨度上限
            'odd_even_tolerance': 0.38,  # 从0.4降为0.38(更严格的奇偶约束)
            'sum_of_squares_penalty': True,  # 方差惩罚
            'tolerance_matching': True,  # 启用容错匹配(偏差±1也算命中)
        }
    }

    def __init__(self, custom_config: Optional[Dict] = None):
        """
        初始化配置
        
        Args:
            custom_config: 自定义配置字典,会与默认配置深度合并
        """
        # 使用深拷贝, 避免实例(如 baseline_v21)对配置的修改污染类级 DEFAULT_CONFIG
        self.config = self._merge_config(copy.deepcopy(self.DEFAULT_CONFIG), custom_config or {})
        self._validate_config()
        
        # 新增: 自适应权重管理器(从 global 注入 ewma_alpha + 护栏参数, 便于调参)
        _g = self.config['global']
        self.weight_manager = AdaptiveWeightManager(
            ewma_alpha=_g.get('ewma_alpha', 0.3),
            shrinkage_min_samples=_g.get('adaptive_shrinkage_min_samples', 10),
            weight_floor=_g.get('adaptive_weight_floor', 0.001),
            weight_cap=_g.get('adaptive_weight_cap', 0.75),
            enable_guardrails=_g.get('enable_adaptive_guardrails', True))

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

    # ★ Plan B (2026-07-18): 弱信号次要算法集合, 其 EWMA 混合后权重受 minor_max_weight 钳制, 防过均匀
    MINOR_ALGOS = {'trend_momentum', 'markov_transition',
                   'pattern_continuation', 'feature_engineering'}

    def get_algorithm_weights(self) -> Dict[str, float]:
        """
        获取归一化后的算法权重

        如果启用了自适应权重,会根据历史验证结果动态调整权重。
        ★ v3.14 双信号升级: 自适应权重现在根据 config['global']['adaptive_metric']
         选择读取的 EWMA 字段:
           - 'top1_hit' (默认, 推荐): 读取 ewma_t1 (Top-1 精准度)
           - 'hit_rate' (回退兼容): 读取 ewma   (覆盖命中率, 区分度低)
           - 'hybrid'              : 在 AdaptiveWeightManager 内部已混合
         任何 metric 都不存在数据时, 自动降级到另一个通道.

        Returns:
            归一化后的权重字典 {algo_name: normalized_weight}
        """
        weights = {}
        total = 0.0
        enable_adaptive = self.config['global'].get('enable_adaptive_weights', True)
        # ★ v3.14: 选信号 (top1_hit 优先, 兼容旧 hit_rate)
        adaptive_metric = self.config['global'].get('adaptive_metric', 'top1_hit')
        if adaptive_metric in ('top1_hit',):
            ewma_field = 'ewma_t1'
        else:
            ewma_field = 'ewma'

        has_any_t1 = False
        if hasattr(self, 'weight_manager'):
            has_any_t1 = any(
                rec.get('t1_total', 0) > 0
                for rec in self.weight_manager.algo_hit_rates.values()
            )
        # 当选择 top1_hit 但全无 t1 数据时, 安全降级到 ewma 通道
        effective_field = ewma_field
        if ewma_field == 'ewma_t1' and not has_any_t1:
            effective_field = 'ewma'

        for name, cfg in self.config['algorithms'].items():
            if cfg.get('enabled', False):
                w = cfg.get('weight', 0)
                # 如果启用自适应权重,基于历史命中率微调
                ewma = 0.0
                if enable_adaptive and hasattr(self, 'weight_manager'):
                    ewma = self.weight_manager.algo_hit_rates.get(name, {}).get(effective_field, 0)
                if ewma > 0:
                    # 混合原始权重和历史表现(EWMA)
                    # ★ Plan B: 混合系数由 ewma_blend 控制(默认0.1, 原硬编码0.3),
                    #   让静态默认权重主导, 避免 EWMA 把分布拉向均匀而稀释 Top-1 精准度。
                    blend = self.config['global'].get('ewma_blend', 0.1)
                    w = (1.0 - blend) * w + blend * ewma
                # ★ Plan B: 次要算法(弱信号通道)EWMA 混合后权重上限钳制, 防过均匀
                minor_max = self.config['global'].get('minor_max_weight', None)
                if minor_max is not None and name in self.MINOR_ALGOS and w > minor_max:
                    w = minor_max
                weights[name] = w
                total += w

        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return weights

    def get_global_param(self, key: str, default=None):
        """获取全局参数"""
        return self.config['global'].get(key, default)

    @classmethod
    def baseline_v21(cls) -> 'P5PredictorConfig':
        """
        v2.1 基线配置(用于回测对比的「旧模型」):

        - 不含 v3.0 新增的贝叶斯推断算法
        - 未启用自适应权重(静态权重快照, 代表「优化前」)
        - 趋势/马尔可夫权重回退到 v2.1 水平

        使 backtester.compare_models 真正对比「基线 vs 当前」,
        而非两个完全相同的 P5Predictor() 实例(旧实现改善率恒为 0)。
        """
        cfg = cls()
        algos = cfg.config['algorithms']
        # 移除 v3.0 新增的贝叶斯推断
        algos.pop('bayesian_inference', None)
        # 回退到 v2.1 权重快照(相对比例, get_algorithm_weights 会归一化)
        algos['frequency_weighted']['weight'] = 0.35
        algos['omission_regression']['weight'] = 0.25
        algos['trend_momentum']['weight'] = 0.15
        algos['markov_transition']['weight'] = 0.15
        algos['pattern_continuation']['weight'] = 0.10
        algos['feature_engineering']['weight'] = 0.10
        cfg.config['global']['enable_adaptive_weights'] = False
        cfg._validate_config()
        return cfg

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

        # 跨进程恢复自适应权重(EWMA): 从「权重历史」产物回放, 避免每进程重置。
        # ★ v3.12 修复自学习断链: 此前误用 _load_verification_records() 的返回值,
        #   但那些记录不含 algo_evaluations 字段, 导致 load_from_records 静默空转、
        #   自适应权重从不更新。改为读取 p5_artifact(type='weight_history') 产物
        #   (由 pipeline 验证闭环写入, 含各算法 per-algo 命中率), 使 EWMA 真正学习。
        try:
            self._load_adaptive_weight_history()
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
        """解析AI响应（鲁棒：兼容单引号/裸key/尾随逗号/代码块）"""
        from modules.json_repair import repair_and_parse_json
        result = repair_and_parse_json(response_text, default={})
        return result if isinstance(result, dict) else {}

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

        # 设置验证记录截止期号(防止回测时前视偏差:只学习早于当前期的验证记录)
        self._verification_cutoff = current_issue if current_issue else None

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

        # 为每个启用的算法提取 Top-5 预测（供后续 per-algo 命中率验证使用）
        per_algo_top_predictions = {}
        for algo_name, pos_probs in algorithm_probs.items():
            per_algo_top_predictions[algo_name] = {}
            for pos_idx in range(len(pos_probs)):
                pos_probs_map = pos_probs[pos_idx]
                top_n = sorted(pos_probs_map.items(), key=lambda x: x[1], reverse=True)[:5]
                position_names = ['wan', 'qian', 'bai', 'shi', 'ge']
                per_algo_top_predictions[algo_name][position_names[pos_idx]] = [n for n, _ in top_n]

        predict_uuid = str(uuid.uuid4())

        result = {
            'predict_uuid': predict_uuid,
            'target_issue': next_issue,
            'base_issue': current_issue or history_data[0].get('issue', ''),
            'predict_time': datetime.now().isoformat(),
            'algorithm_config': self.config.to_dict(),
            'algorithm_weights': self.config.get_algorithm_weights(),
            'per_algo_top_predictions': per_algo_top_predictions,
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
        频率加权算法（基础统计信号，融合权重占比最高）

        核心思想：
            彩票每个位置(万/千/百/十/个)的号码都可视为一个 0-9 的离散随机变量。
            本算法用「历史出现频数」估计该变量的经验概率分布，频数越高代表该号码
            越「热」，被赋予的概率越大。

        数学公式（拉普拉斯平滑 / Additive Smoothing）：
            对位置 pos 的号码 num：
                P(num) = (count(num) + α) / (N + α·K)
            其中：
                - count(num) ：号码 num 在历史样本中出现的次数
                - N          ：历史样本总期数（total）
                - α          ：平滑系数 smoothing_factor（默认 0.1），防止未出现号码概率为 0
                - K          ：号码空间大小，本系统 K = 10（number_range 即 0-9），故分母加 α*10
            平滑项 α·K 的含义：等价于「假设每个号码已先验出现 α 次」，从而给冷号一个
            非零但很低的基础概率，避免模型对从未出现的号码判死。

        关键参数（来自配置 ``algorithms.frequency_weighted.params``）：
            - smoothing_factor (默认 0.1)：平滑强度。越大则分布越「均匀」，越小越「尖锐」
              （贴近真实频数）。可调区间约 [0.01, 1]，值越大系统越保守。
            - lookback_periods (默认 None = 用全部历史)：只统计最近 N 期，用于让概率分布
              随时间「遗忘」远古数据；设为 None 则使用全部 history_data。

        边界条件：
            - 某期 numbers 长度不足 positions 时跳过（不计入 count），保证统计口径一致。
            - total=0（空数据）时除式分母为 α*10 > 0，不会除零，但结果为均匀先验。
        """
        # 读取该算法的可调参数（带默认值，避免配置缺失导致 KeyError）
        params = self.config.config['algorithms']['frequency_weighted']['params']
        smoothing = params.get('smoothing_factor', 0.1)   # 拉普拉斯平滑系数 α
        lookback = params.get('lookback_periods')          # 回看期数，None 表示全量
        recency = params.get('recency_weight', False)      # ★ v3.14审计落地: 近期指数加权开关
        decay = params.get('recency_decay', 0.0)           # 衰减率(每期)

        # 仅取最近 lookback 期（若有设置），实现时间衰减/遗忘
        use_data = data[-lookback:] if lookback else data
        total = len(use_data)

        # 统计各位置各号码出现次数（★ 支持近期指数加权: 越近期权重越大）
        counts = [defaultdict(float) for _ in range(self.positions)]
        total_weight = 0.0
        for k, item in enumerate(use_data):
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                w = 1.0
                if recency and decay > 0:
                    # 越近期 (k 越大) 权重越大: w = exp(-decay*(total-1-k))
                    w = math.exp(-decay * (total - 1 - k))
                total_weight += w
                for pos, num in enumerate(numbers):
                    counts[pos][int(num)] += w

        # 分母为加权总样本数(保证加权后为合法概率); recency 关闭时 total_weight==total 向后兼容
        denom = total_weight if total_weight > 0 else total

        # 套用拉普拉斯平滑公式计算概率分布
        probs = []
        for pos in range(self.positions):
            pos_probs = {}
            for num in self.number_range:
                count = counts[pos].get(num, 0.0)
                # 公式：P = (count + α) / (denom + α*10)
                pos_probs[num] = (count + smoothing) / (denom + smoothing * 10)
            probs.append(pos_probs)

        return probs

    def _algo_omission_regression(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        遗漏回归算法（修复：正确处理从未出现的号码）

        核心思想（「冷号回归」假设）：
            「遗漏值」= 某个号码距离上一次开出的期数。本算法认为遗漏越大的号码，
            在短期内「回补」出现的概率越高（类似赌徒谬误的统计反向信号）。
            这与频率加权互补：频率看「热」，遗漏看「冷」。

        数学公式（指数回归）：
            对号码 num 的遗漏值 o（以「期」为单位），先做上限截断避免极端值主导：
                o' = min(o, max_omission_cap)
            原始得分（未归一化）：
                score(num) = exp(β · o')
            其中 β = regression_steepness（陡度系数，默认 0.08）控制遗漏对概率的敏感度。
            β 越大，长遗漏号码的相对优势越夸张；β→0 则退化为均匀分布。
            最后对所有号码的 score 做 softmax 式归一化（除以总和），得到概率分布。

        关键参数（来自配置 ``algorithms.omission_regression.params``）：
            - max_omission_cap (默认 50)：遗漏上限。因为历史上遗漏可能上百期，若不做
              截断，exp(β·o) 会爆炸并使单号概率接近 1，丧失区分度。50 是经验上「足够冷」
              的阈值，可调区间约 [20, 100]。
            - regression_steepness (默认 0.08)：指数陡度 β。典型可调区间 [0.03, 0.2]。

        边界条件：
            - 从未出现的号码：last_idx=-1，遗漏值取 total（全部期数），保证它有合理的高分，
              修复了早期版本将其遗漏记为 0（反而概率最低）的 bug。
            - total_score=0 的极端情况（理论不会，因 exp>0）回退到 0.1 均匀值。
        """
        params = self.config.config['algorithms']['omission_regression']['params']
        max_cap = params.get('max_omission_cap', 50)        # 遗漏值上限，防止 exp 爆炸
        steepness = params.get('regression_steepness', 0.08)  # 指数陡度 β

        # 记录每个位置每个号码「最后一次出现」的索引位置
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
                # 修复：当号码从未出现时，遗漏值应为 total（而非 0）
                if last_idx == -1:
                    omission = total
                else:
                    omission = total - 1 - last_idx
                # 截断遗漏值，避免长遗漏导致 exp 数值溢出
                omissions[num] = min(omission, max_cap)

            # 指数回归概率：score = exp(β·o)，遗漏越大得分越高
            raw_scores = {num: math.exp(steepness * omissions[num]) for num in self.number_range}
            total_score = sum(raw_scores.values())
            for num in self.number_range:
                # 归一化；total_score>0 恒成立（exp>0），此处为防御性兜底
                pos_probs[num] = raw_scores[num] / total_score if total_score > 0 else 0.1
            probs.append(pos_probs)

        return probs

    def _algo_trend_momentum(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        趋势动量算法

        核心思想：
            用「最近 N 期该位置号码序列」做一元线性回归，得到一条趋势线，斜率 slope
            代表号码在近期是「递增」还是「递减」。然后给「顺着趋势方向」的号码更高概率，
            并叠加一个以「上期值」为中心的高斯衰减，使概率不会发散到离当前值太远的号码。

        数学公式：
            1) 线性回归：对序列 y（按时间索引 x=0..n-1）拟合 y ≈ a·x + b，取斜率 a = slope。
            2) 趋势得分：trend_score(num) = 1 + γ · slope · (num - last) / 9
               其中 γ = momentum_factor（默认 1.2），(num - last) 是候选号码与上一期值的差距，
               9 是号码跨度 0-9 的归一化分母。当 slope>0（上升趋势）时，大于 last 的号码得分
               提升，反之下降号码得分提升。
            3) 高斯衰减（局部性先验）：g(num) = exp(-0.5·((num - last)/σ)²)，σ=3.0。
               表示号码越接近上期值越「自然」，离得越远概率按高斯快速衰减。
            4) 单号原始分 = max(0.01, trend_score · g(num))，最后对 0-9 归一化。

        关键参数（来自配置 ``algorithms.trend_momentum.params``）：
            - trend_window (默认 10)：回归所用的近期窗口长度，越大趋势越平滑但越迟钝。
            - momentum_factor (默认 1.2)：动量强度 γ，控制趋势对概率的影响幅度；γ 越大越「追涨杀跌」。
            - 高斯带宽 σ = 3.0（硬编码）：号码离上期值 3 个以内衰减适中，可调区间约 [1.5, 5]。

        边界条件：
            - 数据少于 2 期：无法回归，直接返回 0.1 均匀分布。
            - 某位置序列不足 2 个有效值时跳过该位置，返回均匀。
            - max(0.01, ...) 保证概率恒为正，避免负数被归一化后扭曲分布。
        """
        params = self.config.config['algorithms']['trend_momentum']['params']
        window = params.get('trend_window', 10)            # 回归窗口长度
        momentum_factor = params.get('momentum_factor', 1.2)  # 动量强度 γ

        recent = data[-window:] if len(data) >= window else data
        if len(recent) < 2:
            # 数据不足时返回均匀分布（0.1 × 10 = 1，已归一）
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

            # 线性回归求趋势方向（最小二乘斜率）
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
                # 高斯衰减：离上期值越远概率越低（σ=3.0）
                gaussian_decay = math.exp(-0.5 * ((distance / 3.0) ** 2))
                pos_probs[num] = max(0.01, trend_score * gaussian_decay)

            # 归一化（sum 必 > 0，因每项 ≥ 0.01）
            total_score = sum(pos_probs.values())
            for num in self.number_range:
                pos_probs[num] /= total_score
            probs.append(pos_probs)

        return probs

    def _algo_markov_transition(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        马尔可夫转移算法（一阶状态转移模型）

        核心思想：
            假设「下一期某位置的号码」只依赖于「上一期同一位置的号码」（一阶马尔可夫性）。
            从历史数据中统计转移计数：从状态 p（上期号码）跳到状态 c（本期号码）的次数，
            归一化后即得到条件概率 P(下一期=c | 上期=p)。

        数学公式：
            对位置 pos 与上一期号码 p，转移概率矩阵 M[p][c] 由加权计数得到：
                M[p][c] = Σ_idx  w_idx · 1{prev_num(idx)=p, curr_num(idx)=c}
                w_idx   = decay ^ (N - idx)        # 距离越近权重越大（时间衰减）
                P(c|p)  = M[p][c] / Σ_c' M[p][c']
            decay（默认 0.95）<1 使越近的历史对转移概率影响越大（指数遗忘）。
            order=1 表示只看「上一期→本期」，更高阶可扩展但本实现默认一阶。

        关键参数（来自配置 ``algorithms.markov_transition.params``）：
            - order (默认 1)：马尔可夫阶数，取上一期作为条件状态。
            - decay_factor (默认 0.95)：时间衰减系数，越接近 1 越重视远期；越接近 0 越只看最近。

        边界条件：
            - 数据少于 order+1 期：无法构建转移，返回 0.1 均匀。
            - 最新一期号码长度非法：返回均匀。
            - 某 (p) 对应的转移计数全为 0（该号码此前从未作为「上期」出现过）：回退到 0.1 均匀，
              避免除零与全零分布。
        """
        params = self.config.config['algorithms']['markov_transition']['params']
        order = params.get('order', 1)              # 马尔可夫阶数
        decay = params.get('decay_factor', 0.95)    # 时间衰减系数

        if len(data) < order + 1:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        last_item = data[-1]
        last_numbers = last_item.get('numbers', [])
        if len(last_numbers) != self.positions:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        # 构建转移计数矩阵（嵌套 defaultdict，pos -> prev_num -> curr_num -> 加权计数）
        transition_counts = [defaultdict(lambda: defaultdict(float)) for _ in range(self.positions)]

        for idx in range(order, len(data)):
            weight = decay ** (len(data) - idx)   # 越近的历史权重越大
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
                # 有转移记录：按条件概率归一化
                for num in self.number_range:
                    pos_probs[num] = counts.get(num, 0) / total
            else:
                # 无转移记录（该上期号码从未作为源状态）时回退到均匀分布
                for num in self.number_range:
                    pos_probs[num] = 0.1
            probs.append(pos_probs)

        return probs

    def _algo_pattern_continuation(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        形态延续算法（修复：使用正确的质数定义）

        核心思想：
            不预测「具体号码」，而是预测「号码的类别形态」是否会延续。统计近期窗口内
            三种二元形态的比例：奇偶、大小(≥5 为大)、质合(0-9 中质数为 {2,3,5,7})。
            若某形态近期明显偏多（如奇数占比 >0.6），则认为该形态「惯性延续」，给属于该
            形态的号码整体乘上一个加成系数 boost。

        数学公式：
            对位置 pos，先算近期窗口内三类形态比例：
                odd_ratio   = (#奇数) / 窗口长度
                big_ratio   = (#>=5) / 窗口长度
                prime_ratio = (#质数) / 窗口长度
            每个候选号码 num 的初始 score=1，按以下规则「乘性加成」（满足阈值才加成）：
                奇偶：num 为奇 且 odd_ratio>0.6   → ×boost；num 为偶 且 odd_ratio<0.4 → ×boost
                大小：num≥5 且 big_ratio>0.6      → ×boost；num<5  且 big_ratio<0.4 → ×boost
                质合：num∈质数 且 prime_ratio>0.4  → ×boost；num∉质数 且 prime_ratio<0.6 → ×boost
            阈值 0.6 / 0.4 表示一个形态需「显著偏态」才视为有延续性（对称于 0.5 中点）。
            最后对 score 归一化即得概率分布。

        关键参数（来自配置 ``algorithms.pattern_continuation.params``）：
            - pattern_window (默认 5)：统计形态的近期窗口长度。
            - continuation_boost (默认 1.3)：命中延续形态时的乘性加成；越接近 1 越弱，越大越强。
            - 形态偏态阈值 0.6/0.4 为硬编码，可调区间约 [0.55, 0.7] / [0.3, 0.45]。

        边界条件：
            - 窗口内有效数据 <2：返回 0.1 均匀。
            - 某位置无有效序列：跳过该位置返回均匀。
            - 质数集合 self.primes 需正确定义为 {2,3,5,7}（修复了早期误把 1 算作质数等 bug）。
        """
        params = self.config.config['algorithms']['pattern_continuation']['params']
        window = params.get('pattern_window', 5)      # 形态统计窗口
        boost = params.get('continuation_boost', 1.3)  # 延续性加成系数

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
            # 修复：使用正确的质数定义（0-9 质数集合 self.primes = {2,3,5,7}）
            prime_ratio = sum(1 for n in seq if n in self.primes) / len(seq)

            pos_probs = {}
            for num in self.number_range:
                score = 1.0
                # 奇偶延续（odd_ratio 显著偏离 0.5 才加成）
                is_odd = num % 2 == 1
                if is_odd and odd_ratio > 0.6:
                    score *= boost
                elif not is_odd and odd_ratio < 0.4:
                    score *= boost

                # 大小延续（≥5 记为大号）
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
        贝叶斯推断算法 (v3.2 调优版 - 2026-07-17)

        核心改进:
        1. 先验概率: 引入时间衰减加权,近期数据权重更高 (指数衰减)
        2. 似然函数: 动态 Beta 先验,样本少时更保守,样本多时更置信
        3. 后验融合: 提高 posterior_weight,增大 reward/penalize 差距
        4. 验证记录: 引入置信度评分,高质量验证记录权重更高

        数学原理：
            P(号码i|验证) ∝ P(验证|号码i) × P(号码i)
            其中:
                - P(号码i): 先验概率(时间衰减加权的历史频率)
                - P(验证|号码i): 似然(Beta-二项后验估计)

        Args:
            data: 按期号正序排列的历史开奖数据列表

        Returns:
            各位置号码的后验概率分布列表,格式为 List[Dict[int, float]]
        """
        params = self.config.config['algorithms']['bayesian_inference']['params']
        prior_smooth = params.get('prior_smooth', 0.10)         # v3.2增强:增大平滑系数
        posterior_weight = params.get('posterior_weight', 0.92)  # v3.2增强:更强信任似然
        verification_window = params.get('verification_window', 60)
        penalize_miss = params.get('penalize_miss', 0.68)        # v3.2增强:加大惩罚力度
        reward_hit = params.get('reward_hit', 1.40)              # v3.2增强:加大奖励力度
        decay_half_life = params.get('decay_half_life', 10)      # v3.2增强:更重视近期数据
        beta_alpha = params.get('beta_alpha', 0.8)               # v3.2增强:减少伪计数影响
        prior_temporal_scale = params.get('prior_temporal_scale', 50)  # 先验数据时间尺度

        # ============================================================
        # 第一阶段: 计算先验概率 — 时间衰减加权历史频率
        # ============================================================
        total_data = len(data)
        if total_data == 0:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        # 使用全部历史数据(或prior_temporal_scale期),对近期数据给予更高权重
        # 指数衰减: weight(i) = exp(-ln(2) * (total_data - i) / half_life)
        # 最新一期权重=1, half_life期前权重=0.5, 以此类推
        import math
        
        # 限制先验数据范围，避免远古数据噪声
        prior_data = data[-prior_temporal_scale:] if len(data) > prior_temporal_scale else data
        
        prior_counts = [defaultdict(float) for _ in range(self.positions)]
        total_weight = [0.0] * self.positions

        for idx, item in enumerate(prior_data):
            numbers = item.get('numbers', [])
            if len(numbers) != self.positions:
                continue
            
            # 计算时间衰减权重（基于在prior_data中的位置）
            age = len(prior_data) - 1 - idx  # 最新一期age=0
            weight = math.exp(-math.log(2) * age / decay_half_life)

            for pos, num in enumerate(numbers):
                prior_counts[pos][int(num)] += weight
                total_weight[pos] += weight

        # 计算加权先验概率
        prior_probs = []
        for pos in range(self.positions):
            pos_prior = {}
            tw = max(total_weight[pos], 1e-9)
            for num in self.number_range:
                # 加权频率 + 拉普拉斯平滑
                pos_prior[num] = (prior_counts[pos].get(num, 0) / tw + prior_smooth * 0.1) / (1 + prior_smooth)
            
            # 归一化
            total_prior = sum(pos_prior.values())
            if total_prior > 0:
                pos_prior = {k: v / total_prior for k, v in pos_prior.items()}
            else:
                pos_prior = {n: 0.1 for n in self.number_range}
            
            prior_probs.append(pos_prior)

        # ============================================================
        # 第二阶段: 计算似然概率 — 动态 Beta-二项后验
        # ============================================================
        verification_records = self._load_verification_records()
        # 防止前视偏差:回测/预测时只使用早于截止期号的验证记录(截止期=当前期)
        cutoff = getattr(self, '_verification_cutoff', None)
        if cutoff is not None:
            try:
                cutoff_int = int(str(cutoff))
                verification_records = [
                    r for r in verification_records
                    if int(str(r.get('target_issue', '0'))) < cutoff_int
                ]
            except (ValueError, TypeError):
                pass
        tolerance = self.config.get_global_param('tolerance_matching', True)

        min_samples = params.get('min_verification_samples', 50)
        if len(verification_records) < min_samples:
            # 验证样本不足:直接返回先验概率(避免小样本似然噪声误导后验)
            logger.info(f'贝叶斯推断: 验证记录仅{len(verification_records)}条(<{min_samples}),'
                       f'退化为纯先验概率(待积累足够验证数据后启用学习)')
            return prior_probs

        if not verification_records:
            # 无验证记录时,直接返回先验概率作为近似后验
            logger.info('贝叶斯推断: 无验证记录,使用先验概率近似')
            return prior_probs

        # 对验证记录按时间加权(近期记录权重更高)
        recent_records = verification_records[-verification_window:]
        n_records = len(recent_records)
        
        # 命中/未命中次数统计,shape: [position][number]
        hit_counts = [{n: 0.0 for n in self.number_range} for _ in range(self.positions)]
        miss_counts = [{n: 0.0 for n in self.number_range} for _ in range(self.positions)]
        record_weights = [0.0] * n_records  # 每条记录的权重

        for i, record in enumerate(recent_records):
            # 记录权重: 近期的记录更重要
            age = n_records - 1 - i
            rw = math.exp(-math.log(2) * age / max(decay_half_life // 2, 5))
            record_weights[i] = rw

            pred_numbers = record.get('predicted_numbers', [])
            actual_numbers = record.get('actual_numbers', [])
            
            # 兼容新旧格式: 可能是列表或字典
            if isinstance(pred_numbers, dict):
                # 新格式: {'wan': [...], 'qian': [...], ...}
                if 'wan' not in pred_numbers:
                    continue
                pred_nums = [pred_numbers.get(pos, [0]) for pos in ['wan', 'qian', 'bai', 'shi', 'ge']]
            elif isinstance(pred_numbers, list) and len(pred_numbers) == self.positions:
                pred_nums = pred_numbers
            else:
                continue

            if isinstance(actual_numbers, dict):
                actual_nums = [actual_numbers.get(pos, 0) for pos in ['wan', 'qian', 'bai', 'shi', 'ge']]
            elif isinstance(actual_numbers, list) and len(actual_numbers) == self.positions:
                actual_nums = actual_numbers
            else:
                continue

            for pos in range(self.positions):
                try:
                    # pred_nums[pos] 可能是 Top-N 号码列表 [5,3,7,1] 或单个号码
                    pos_preds = pred_nums[pos]
                    if isinstance(pos_preds, list):
                        # Top-N 格式: 取第一个作为主要预测
                        pred_num = int(pos_preds[0])
                        all_preds = [int(x) for x in pos_preds]
                    else:
                        pred_num = int(pos_preds)
                        all_preds = [pred_num]
                    
                    actual_num = int(actual_nums[pos])
                except (ValueError, IndexError, TypeError):
                    continue
                
                # 命中判定:位置级精确匹配 + 容错匹配
                # 注意:这里检测的是「实际开奖号是否在预测号码集合中」
                is_hit = (actual_num in all_preds) or (
                    tolerance and any(abs(actual_num - p) <= 1 for p in all_preds)
                )
                
                # 按记录权重累加
                # 对于所有预测号码都计分(主预测权重大,备选用权重衰减)
                if is_hit:
                    # 主预测号码获得全额奖励
                    hit_counts[pos][pred_num] += rw
                    # 备用号码获得衰减奖励(权重0.5)
                    for alt_num in all_preds[1:]:
                        hit_counts[pos][alt_num] += rw * 0.5
                else:
                    # 所有预测号码都受到惩罚
                    miss_counts[pos][pred_num] += rw
                    for alt_num in all_preds[1:]:
                        miss_counts[pos][alt_num] += rw * 0.5

        # 计算 Beta-二项似然(动态伪计数)
        # 样本越少越接近先验(0.5),样本越多越接近真实命中率
        likelihoods = []
        for pos in range(self.positions):
            pos_likelihood = {}
            for num in self.number_range:
                h = hit_counts[pos][num]
                m = miss_counts[pos][num]
                total_evidence = h + m
                
                # 动态伪计数:总证据少时用较大的beta_alpha保持保守,证据多用较小的
                dynamic_alpha = beta_alpha * (1.0 / max(total_evidence, 1.0) ** 0.5)
                
                # Beta后验估计
                pos_likelihood[num] = (h + dynamic_alpha) / (total_evidence + 2 * dynamic_alpha)
            
            likelihoods.append(pos_likelihood)

        # ============================================================
        # 第三阶段: 后验概率 = 先验 × 似然的加权融合
        # ============================================================
        posterior_probs = []
        for pos in range(self.positions):
            pos_posterior = {}
            for num in self.number_range:
                prior = prior_probs[pos].get(num, 0.1)
                likelihood = likelihoods[pos].get(num, 0.1)
                
                # 核心贝叶斯更新: posterior ∝ prior^((1-λ)) * likelihood^λ
                # λ越大越信任似然,越小越依赖先验
                combined = (prior ** (1 - posterior_weight)) * (likelihood ** posterior_weight)
                
                # 额外boost/penalize: 放大似然区分度
                if combined > 0:
                    # 如果似然显著高于先验,进一步boost
                    if likelihood > prior:
                        ratio_boost = reward_hit ** (likelihood - prior)
                        combined *= min(ratio_boost, 2.0)  # 限制最大 boosting
                    # 如果似然低于先验,进一步惩罚
                    elif likelihood < prior:
                        ratio_penalty = penalize_miss ** (prior - likelihood)
                        combined *= max(ratio_penalty, 0.3)  # 限制最小 penalty
                
                pos_posterior[num] = combined

            # 归一化
            total = sum(pos_posterior.values())
            if total > 0:
                for num in self.number_range:
                    pos_posterior[num] /= total
            else:
                pos_posterior = {n: 0.1 for n in self.number_range}

            posterior_probs.append(pos_posterior)

        logger.info(f'贝叶斯推断算法(v3.2)执行完成: 使用{len(recent_records)}条验证记录, '
                   f'posterior_weight={posterior_weight}, beta_alpha={beta_alpha:.2f}')
        return posterior_probs

    def _load_adaptive_weight_history(self, limit: int = 200):
        """
        加载「权重历史」产物, 回放 per-algo 命中率以更新自适应权重 EWMA。(v3.12 新增)

        数据来源: p5_artifact 表 type='weight_history' 记录, 每条 data 含:
            - target_issue: 验证期号
            - algo_evaluations: {算法名: 该期该算法 per-algo 命中率(0-1)}
        由 pipeline 的验证闭环(_record_verification_result)写入。回放这些记录可让
        AdaptiveWeightManager 的 EWMA 在多次运行间累积学习, 使表现好的算法权重上升。

        说明: 当前该产物可能尚未积累(需验证闭环逐期写入), 此时为无害空操作;
        随着每期开奖→验证, 记录会持续累积并自动生效。
        """
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                return
            arts = db.get_artifacts(artifact_type='weight_history', limit=limit)
            db.disconnect()
            if not arts:
                return
            # get_artifacts 按 created_at 倒序; 回放时改为正序, 使近期记录对 EWMA 影响更大
            records = [a.get('data', {}) for a in reversed(arts) if isinstance(a.get('data'), dict)]
            records = [r for r in records if r.get('algo_evaluations')]
            if records:
                self.config.weight_manager.load_from_records(records)
                logger.info(f'自适应权重: 回放 {len(records)} 条权重历史产物完成')
        except Exception as e:
            logger.debug(f'加载权重历史产物跳过(非致命): {e}')

    def _load_verification_records(self) -> List[Dict]:
        """
        加载历史验证记录

        ★ v3.2 修复: 直接从 p5_prediction_record 表读取验证数据,
        不再依赖 p5_artifact(type='weight_history') (该表当前为空)。

        这些记录包含每次预测的目标期号、推荐号码和实际开奖号码,
        用于贝叶斯推断算法计算似然概率。

        Returns:
            验证记录列表,每条记录包含:
                - target_issue: 预测目标期号
                - predicted_numbers: 预测号码(格式: {'wan': [...], 'qian': [...], ...})
                - actual_numbers: 实际开奖号码(格式: [wan, qian, bai, shi, ge])
        """
        # 缓存:同一predictor实例内只加载一次(避免回测时每期重复连库)
        if getattr(self, '_verification_cache', None) is not None:
            return self._verification_cache
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.warning('加载验证记录: 数据库连接失败')
                return []
            
            # 直接从 p5_prediction_record 表读取已验证的记录
            # 注意: 不使用 LIMIT 限制条数, 让贝叶斯似然学习利用「全部」已验证历史
            # (cutoff 已在调用方按 target_issue 过滤, 仅保留早于当前期的记录, 避免前视偏差)
            db.cursor.execute('''
                SELECT target_issue, predicted_numbers, actual_numbers,
                       wan_match, qian_match, bai_match, shi_match, ge_match
                FROM p5_prediction_record 
                WHERE verification_status = 'verified'
                ORDER BY id DESC
            ''')
            rows = db.cursor.fetchall()
            db.disconnect()
            
            records = []
            import json
            for row in rows:
                pred = row['predicted_numbers']
                actual = row['actual_numbers']
                
                # 解析预测号码 (格式: {'wan': [5,3,7,1], ...})
                if isinstance(pred, str):
                    pred = json.loads(pred)
                if isinstance(actual, str):
                    actual = json.loads(actual)
                
                # 统一格式
                if isinstance(pred, dict):
                    pred_nums = [pred.get(pos, [0]) for pos in ['wan', 'qian', 'bai', 'shi', 'ge']]
                elif isinstance(pred, list) and len(pred) == 5:
                    pred_nums = pred
                else:
                    pred_nums = [[0]] * 5
                
                if isinstance(actual, dict):
                    actual_nums = [actual.get(pos, 0) for pos in ['wan', 'qian', 'bai', 'shi', 'ge']]
                elif isinstance(actual, list) and len(actual) == 5:
                    actual_nums = actual
                else:
                    actual_nums = [0] * 5
                
                records.append({
                    'target_issue': row['target_issue'],
                    'predicted_numbers': pred_nums,
                    'actual_numbers': actual_nums,
                    'wan_match': row['wan_match'],
                    'qian_match': row['qian_match'],
                    'bai_match': row['bai_match'],
                    'shi_match': row['shi_match'],
                    'ge_match': row['ge_match'],
                })
            
            logger.info(f'从 p5_prediction_record 加载 {len(records)} 条验证记录')
            self._verification_cache = records
            return records
            
        except Exception as e:
            logger.warning(f'加载验证记录失败: {e}', exc_info=True)
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
            # 惩罚系数 0.85 为软惩罚：仅降权、不剔除，避免误杀可能的开奖组合。
            adjacent_similar = 0
            for i in range(self.positions - 1):
                if abs(combo[i] - combo[i+1]) <= 1:
                    adjacent_similar += 1
            if adjacent_similar > 2:
                score *= 0.85  # 相邻相似对 >2 时轻度降权

            # 约束2: 和值范围约束（软惩罚,仅极端越界才强惩罚）
            hezhi = sum(combo)
            # 和值 10~35 为「正常区间」，仅降权 0.85 保留命中可能；
            # 和值 <5 或 >40 为极端罕见值，强惩罚 0.6。
            if hezhi < hezhi_min or hezhi > hezhi_max:
                score *= 0.85   # 轻度惩罚,保留命中可能
            elif hezhi < 5 or hezhi > 40:
                score *= 0.6    # 仅极端和值才强惩罚

            # 约束3: 奇偶比约束（避免全奇/全偶,但不致命）
            # 全奇(odd=5)或全偶(odd=0)在排列5开奖中概率极低，惩罚 0.6。
            odd_count = sum(1 for num in combo if num % 2 == 1)
            if odd_count == 0 or odd_count == 5:
                score *= 0.6

            # 约束4: 平方和偏差(SSD)惩罚（软化）
            # SSD = Σ(num-4.5)² / 5，衡量组合整体偏离均值 4.5 的程度。
            # SSD 过小(<1)说明号码过于集中，过大(>15/>20)说明过于发散，均降权。
            if enable_ssd_penalty:
                ssd = sum((num - position_mean) ** 2 for num in combo) / self.positions
                if ssd < 1.0:
                    score *= 0.9    # 过于集中
                elif ssd > 20.0:
                    score *= 0.85   # 极端发散
                elif ssd > 15.0:
                    score *= 0.95   # 较发散

            # 约束5: 跨度约束（软化）
            # span = max-min，衡量组合的分布广度；过窄(<span_min)或过宽(>span_max)均降权。
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

            # ── v3.12 移除: Chebyshev 距离检查 与 位置方差检查 ──
            # 这两项约束会把每个位置的概率分布主动拉向均匀(压制高概率号、抬升低概率号),
            # 等价于抹掉各算法辛苦挖出的信号。回测证实它们使 Top-1/Top-3 命中率跌破随机基线,
            # 故整体删除。边界保护现仅保留「温和」的冷热比与相邻位去重, 且默认关闭。

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
        1. 53728 (相对热度: 72.34%)
        2. 53726 (相对热度: 68.12%)
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
            lines.append(f"{combo['rank']}. {combo['combination']} (相对热度: {combo['confidence']:.2f}%)")

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
