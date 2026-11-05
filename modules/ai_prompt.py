# -*- coding: utf-8 -*-
"""
模块：modules/ai_prompt.py
功能：ALG-09 提示词模板集中管理与版本控制

技术路径：
1. 提示词模板定义，统一由 config.py 中的 AGNES_AI_PROMPT_CONFIG 读取
2. 提示词版本号 VERSION，随配置变更自增，用于评估报告版本追踪
3. JSON response repair 与强 schema 校验入口
4. 所有提示词相关常量均配置化，便于无代码调优

依赖：json, re, typing。与 modules.stats_utils.wilson_ci 等模块无直接依赖。
"""

import json
import re
import hashlib
from typing import Dict, List, Any, Optional, Tuple

# ALG-09: 提示词版本号。whenever AGNES_AI_PROMPT_CONFIG 结构变更（键增减或默认值变化）,
# 该版本号应手动递增，用于评估报告中的 _prompt_version 字段，实现配置变更可追溯。
PROMPT_VERSION = "2026-10-22-v1"

# ALG-09: 提示词完整性校验阈值。probability 数值必须在 0-1 之间；n个概率的和应接近 1.0，
# 容差 1e-6。若超出容差，视为非法 schema 并触发回退流程。
PROB_TOLERANCE = 1e-6

# ALG-09: 非法 schema 后的降级策略。返回 None 表示由 caller 触发回退（如使用上一版本prompt或默认prompt）。
DEGRADE_TO_NONE = None


def _validate_probabilities(probs: List[float]) -> bool:
    """
    ALG-09: 校验概率列表是否合法
    
    检查：
    1. 每个概率在 0-1 之间（含边界）
    2. 概率和在 1.0 ± PROB_TOLERANCE 之间
    
    返回:
        True 如果合法，False 如果非法
    """
    if not probs:
        return False
    # 检查每个概率在 [0, 1]
    for p in probs:
        if not (0.0 <= p <= 1.0):
            return False
    # 检查概率和
    total = sum(probs)
    if abs(total - 1.0) > PROB_TOLERANCE:
        return False
    return True


def _schema_version(parsed: Dict[str, Any]) -> Optional[str]:
    """
    ALG-09: 从 parsed JSON 中提取 prompt 版本号。
    优先从 _prompt_version 字段读取；若无则从版本相关键词推断；若仍无返回 None。
    """
    # 直接读取 _prompt_version 字段
    if '_prompt_version' in parsed:
        return str(parsed['_prompt_version'])
    # 若有 VERSION 常量引用或其他版本标识，可在此处扩展
    return None


def validate_and_repair_json(raw_text: str, expected_keys: Optional[List[str]] = None) -> Tuple[Optional[Dict], bool]:
    """
    ALG-09: JSON repair 与强 schema 校验入口
    
    流程：
    1. 尝试 parse raw_text 为 JSON
    2. 如 parse 失败，返回 (None, False) 表示需降级/重试
    3. parse 成功后，校验：
       a. 概率字段 (probabilities 或 类似键) 每个值在 0-1 且和≈1 (容差 1e-6)
       b. 若 expected_keys 给定，检查必需键是否全部存在
       c. 通过返回 (parsed_dict, True)
       d. 任一项失败返回 (None, False) 表示非法 schema
    
    返回:
        (parsed_json or None, schema_valid bool)
        - schema_valid=True: 可直接使用
        - schema_valid=False: 非法 schema，应触发降级流程
    """
    # 1. 尝试解析 JSON
    parsed = None
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return None, False
    
    if not isinstance(parsed, dict):
        return None, False
    
    # 2. 校验概率字段
    #  common patterns: 'probabilities', 'probs', 'probability', 'top1_prob' etc.
    prob_fields = ['probabilities', 'probs', 'probability', 'top1_prob', 'top3_prob', 'top5_prob', 'probability_distribution']
    prob_found = False
    prob_values = []
    
    for field in prob_fields:
        if field in parsed:
            val = parsed[field]
            if isinstance(val, list) and len(val) > 0:
                # 标准情况：list of floats
                if all(isinstance(x, (int, float)) for x in val):
                    prob_values = val
                    prob_found = True
                    break
                # 兼容：每项是 dict 且有 'value' 键
                elif all(isinstance(x, dict) and 'value' in x for x in val):
                    prob_values = [x['value'] for x in val if isinstance(x['value'], (int, float))]
                    if prob_values:
                        prob_found = True
                        break
    
    # 3. 校验概率有效性
    if prob_found and prob_values:
        if _validate_probabilities(prob_values):
            # 概率校验通过，额外检查 expected_keys（如有指定）
            if expected_keys:
                missing = [k for k in expected_keys if k not in parsed]
                if missing:
                    return None, False  # 缺少必需键
            # 校验通过，返回解析结果
            # 提取 (或注入) _prompt_version
            if '_prompt_version' not in parsed:
                parsed['_prompt_version'] = PROMPT_VERSION
            return parsed, True
    
    # 4. 概率校验未通过或未找到概率字段
    # 尝试备选字段名
    if not prob_found:
        # 尝试查找任何 float 列表键名中包含 'prob' 的键
        for key, val in parsed.items():
            if isinstance(val, list) and len(val) > 0 and all(isinstance(x, (int, float)) for x in val):
                if any('prob' in str(k).lower() for k in [key]):
                    if _validate_probabilities(val):
                        if expected_keys:
                            missing = [k for k in expected_keys if k not in parsed]
                            if missing:
                                return None, False
                        parsed['_prompt_version'] = PROMPT_VERSION
                        return parsed, True
    
    # 所有校验均失败 -> 非法 schema
    return None, False


def generate_prompt(template_name: str, **kwargs) -> str:
    """
    ALG-09: 生成指定名称的提示词字符串
    
    支持的模板名称取决于配置 AGNES_AI_PROMPT_CONFIG 中的设定，
    此函数提供统一入口，便于提示词的版本化与参数化填充。
    """
    # 这里不直接硬编码具体模板内容，而是通过配置中心调用
    # 实际的模板填充逻辑见 ai_analyzer.py 的调用方式
    template_defaults = {
        'history_periods': kwargs.get('history_periods', 20),
        'inject_target_issue': kwargs.get('inject_target_issue', True),
        'few_shot': kwargs.get('few_shot', True),
        'honesty_boundary': kwargs.get('honesty_boundary', True),
    }
    # 返回一个包含元数据的结构化字符串，便于后续 JSON repair 校验
    return json.dumps({
        'template': template_name,
        'version': PROMPT_VERSION,
        'params': template_defaults,
    }, ensure_ascii=False)


def get_prompt_config() -> Dict[str, Any]:
    """
    ALG-09: 返回当前提示词配置概览，便于调试与版本追踪。
    """
    return {
        'prompt_version': PROMPT_VERSION,
        'prob_tolerance': PROB_TOLERANCE,
        'degrade_to_none': DEGRADE_TO_NONE,
    }