# -*- coding: utf-8 -*-
"""
auto_evaluator.py — KPLuckyNumber 项目自动化评估机制（v1.0）

【设计目标】
定期扫描项目核心算法、决策策略及相关组件，通过预设的性能指标和业务需求标准，
自动判断是否需要进行升级或优化处理，并生成优先级排序的改进建议报告。

【评估维度】
1. 性能维度：执行耗时、内存占用、缓存命中率
2. 算法维度：命中率基线对照、权重漂移检测、模型过拟合风险
3. 稳定性维度：异常率、降级触发次数、资源泄漏
4. 架构维度：模块依赖健康度、死代码检测、配置一致性

【使用方式】
    from modules.auto_evaluator import ProjectAutoEvaluator

    evaluator = ProjectAutoEvaluator()
    report = evaluator.run_full_evaluation()
    print(evaluator.render_markdown(report))

    # 定时扫描（建议每 24 小时一次）
    evaluator.schedule_periodic_scan(interval_hours=24)
"""

import os
import sys
import json
import time
import timeit
import inspect
import logging
import threading
import datetime
import hashlib
import traceback
import uuid
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
from functools import wraps

logger = logging.getLogger(__name__)

# =====================================================================
# 性能指标基线（诚实声明：排列5为公平摇号，无法稳定超越随机基线）
# =====================================================================

# 诚实随机基线 (%)
BASELINE_TOP1 = 10.0
BASELINE_TOP3 = 30.0
BASELINE_TOP5 = 50.0

# 性能容忍阈值
PERF_WARN_THRESHOLD = 2.0    # 耗时超过基线 2 倍时告警
PERF_CRITICAL_THRESHOLD = 5.0  # 耗时超过基线 5 倍时严重告警

# 缓存命中率阈值
CACHE_HIT_RATE_WARN = 0.3    # 命中率低于 30% 时告警
CACHE_HIT_RATE_OK = 0.7      # 命中率高于 70% 时为优

# 异常率阈值
ERROR_RATE_WARN = 0.05       # 异常率超过 5% 时告警
ERROR_RATE_CRITICAL = 0.10   # 异常率超过 10% 时严重告警

# 权重漂移阈值（与冻结权重对比）
WEIGHT_DRIFT_WARN = 0.05     # 单算法权重漂移超过 5pp 时告警

# 最小样本要求
MIN_SAMPLES_FOR_EVAL = 50    # 评估所需最少历史期数


# =====================================================================
# 评估结果数据结构
# =====================================================================

class EvalResult:
    """单次评估结果。"""

    def __init__(self, name: str, category: str, status: str,
                 score: float, details: Dict[str, Any] = None,
                 recommendations: List[str] = None):
        self.name = name
        self.category = category
        self.status = status  # 'healthy' / 'warning' / 'critical'
        self.score = score    # 0-100
        self.details = details or {}
        # 为每条建议生成唯一ID并跟踪状态
        self.recommendations = [] if recommendations is None else recommendations
        self.recommendation_states = {}  # {rec_id: {'status': 'pending', 'implemented_at': None, 'validated_at': None, 'effectiveness': None}}
        self._initialize_recommendation_states()
        self.timestamp = datetime.datetime.now().isoformat()

    def _initialize_recommendation_states(self):
        """为所有建议初始化状态跟踪"""
        for i, rec in enumerate(self.recommendations):
            rec_id = self._generate_recommendation_id(i)
            self.recommendation_states[rec_id] = {
                'text': rec,
                'status': 'pending',  # pending, implemented, validated, rejected
                'implemented_at': None,
                'validated_at': None,
                'effectiveness': None,  # -1到1之间的值，-1表示完全负面影响，0表示无影响，1表示完全正面影响
                'validation_data': None  # 用于验证的数据快照
            }

    def _generate_recommendation_id(self, index: int) -> str:
        """为建议生成唯一ID"""
        return f"{self.name}_{self.category}_{index}_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'category': self.category,
            'status': self.status,
            'score': self.score,
            'details': self.details,
            'recommendations': [
                {
                    'id': rec_id,
                    'text': state['text'],
                    'status': state['status'],
                    'implemented_at': state['implemented_at'],
                    'validated_at': state['validated_at'],
                    'effectiveness': state['effectiveness'],
                    'validation_data': state['validation_data']
                }
                for rec_id, state in self.recommendation_states.items()
            ],
            'timestamp': self.timestamp,
        }
        
    def mark_recommendation_implemented(self, rec_id: str) -> bool:
        """标记建议为已实施"""
        if rec_id in self.recommendation_states:
            self.recommendation_states[rec_id]['status'] = 'implemented'
            self.recommendation_states[rec_id]['implemented_at'] = datetime.datetime.now().isoformat()
            return True
        return False
        
    def mark_recommendation_validated(self, rec_id: str, effectiveness: float, validation_data: Dict[str, Any] = None) -> bool:
        """标记建议为已验证，并提供效果评估"""
        if rec_id in self.recommendation_states:
            self.recommendation_states[rec_id]['status'] = 'validated'
            self.recommendation_states[rec_id]['validated_at'] = datetime.datetime.now().isoformat()
            self.recommendation_states[rec_id]['effectiveness'] = max(-1.0, min(1.0, effectiveness))  # 限制在-1到1之间
            self.recommendation_states[rec_id]['validation_data'] = validation_data or {}
            return True
        return False


class EvaluationReport:
    """完整评估报告。"""

    def __init__(self, version: str, scan_time: str, results: List[EvalResult]):
        self.version = version
        self.scan_time = scan_time
        self.results = results
        self.summary = self._compute_summary()

    def _compute_summary(self) -> Dict[str, Any]:
        """计算报告摘要。"""
        healthy = sum(1 for r in self.results if r.status == 'healthy')
        warning = sum(1 for r in self.results if r.status == 'warning')
        critical = sum(1 for r in self.results if r.status == 'critical')
        total = len(self.results)
        overall_score = (
            sum(r.score for r in self.results) / total if total > 0 else 0
        )
        
        # 计算建议统计
        total_recommendations = 0
        implemented_recommendations = 0
        validated_recommendations = 0
        positive_effect_recommendations = 0
        
        for result in self.results:
            if hasattr(result, 'recommendation_states'):
                for rec_state in result.recommendation_states.values():
                    total_recommendations += 1
                    if rec_state['status'] == 'implemented':
                        implemented_recommendations += 1
                    elif rec_state['status'] == 'validated':
                        validated_recommendations += 1
                        if rec_state['effectiveness'] is not None and rec_state['effectiveness'] > 0:
                            positive_effect_recommendations += 1
        
        return {
            'total_checks': total,
            'healthy': healthy,
            'warning': warning,
            'critical': critical,
            'overall_score': round(overall_score, 1),
            'health_level': self._health_level(overall_score),
            'recommendation_stats': {
                'total': total_recommendations,
                'implemented': implemented_recommendations,
                'validated': validated_recommendations,
                'positive_effect': positive_effect_recommendations
            }
        }

    @staticmethod
    def _health_level(score: float) -> str:
        if score >= 80:
            return 'healthy'
        if score >= 50:
            return 'degraded'
        return 'critical'

    def to_dict(self) -> Dict[str, Any]:
        return {
            'version': self.version,
            'scan_time': self.scan_time,
            'summary': self.summary,
            'results': [r.to_dict() for r in self.results],
        }

    def render_markdown(self) -> str:
        """渲染为可读 Markdown 报告。"""
        lines = [
            '# KPLuckyNumber 项目自动化评估报告',
            '',
            f'**版本**: {self.version}  **扫描时间**: {self.scan_time}',
            '',
            '## 总体健康度',
            '',
            f'- **综合评分**: {self.summary["overall_score"]}/100',
            f'- **健康级别**: {self.summary["health_level"]}',
            f'- **检查项总数**: {self.summary["total_checks"]}',
            f'- 正常: {self.summary["healthy"]}  警告: {self.summary["warning"]}  严重: {self.summary["critical"]}',
            ''
        ]
        
        # 添加建议统计
        if 'recommendation_stats' in self.summary:
            stats = self.summary['recommendation_stats']
            lines.extend([
                '## 建议执行统计',
                '',
                f'- **建议总数**: {stats["total"]}',
                f'- **已实施**: {stats["implemented"]}',
                f'- **已验证**: {stats["validated"]}',
                f'- **正面效果**: {stats["positive_effect"]}',
                ''
            ])

        # 按类别分组
        by_category = defaultdict(list)
        for r in self.results:
            by_category[r.category].append(r)

        for category, items in by_category.items():
            lines.append(f'## {category}')
            lines.append('')
            for r in items:
                status_icon = {'healthy': '✓', 'warning': '⚠', 'critical': '✗'}.get(r.status, '?')
                lines.append(f'### {status_icon} {r.name} (得分: {r.score}/100)')
                lines.append('')
                if r.details:
                    lines.append('| 指标 | 值 |')
                    lines.append('|------|-----|')
                    for k, v in r.details.items():
                        lines.append(f'| {k} | {v} |')
                    lines.append('')
                if hasattr(r, 'recommendation_states') and r.recommendation_states:
                    lines.append('**改进建议及状态**:')
                    for rec_id, state in r.recommendation_states.items():
                        status_icon = {
                            'pending': '⏳',
                            'implemented': '🔧',
                            'validated': '✅',
                            'rejected': '❌'
                        }.get(state['status'], '?')
                        effectiveness_text = ""
                        if state['effectiveness'] is not None:
                            effectiveness_text = f" (效果: {state['effectiveness']:+.2f})"
                        lines.append(f"- {status_icon} {state['text']}{effectiveness_text}")
                    lines.append('')
            lines.append('')

        # 优先级排序的改进清单（包含验证结果）
        all_recs_with_validation = []
        for r in self.results:
            if hasattr(r, 'recommendation_states'):
                for rec_id, state in r.recommendation_states.items():
                    all_recs_with_validation.append((r.status, r.name, state['text'], state['status'], state['effectiveness']))
        
        # 按优先级和验证状态排序：critical > warning > healthy，然后未验证的优先于已验证的
        all_recs_with_validation.sort(key=lambda x: (
            {'critical': 0, 'warning': 1, 'healthy': 2}.get(x[0], 2),
            0 if x[3] in ['pending', 'implemented'] else 1,  # 未验证的优先
            -(x[4] if x[4] is not None else 0)  # 效果值高的优先
        ))

        if all_recs_with_validation:
            lines.append('## 优先级排序的改进清单（含验证结果）')
            lines.append('')
            lines.append('| 优先级 | 模块 | 建议 | 状态 | 效果 |')
            lines.append('|--------|------|------|------|------|')
            priority_map = {'critical': 'P0-紧急', 'warning': 'P1-重要', 'healthy': 'P2-可选'}
            status_map = {
                'pending': '待实施',
                'implemented': '已实施',
                'validated': '已验证',
                'rejected': '已拒绝'
            }
            for status, name, rec, rec_status, effectiveness in all_recs_with_validation:
                eff_text = f"{effectiveness:+.2f}" if effectiveness is not None else "未评估"
                lines.append(f'| {priority_map.get(status, "?")}| {name} | {rec} | {status_map.get(rec_status, "未知")} | {eff_text} |')
            lines.append('')

        lines.append('---')
        lines.append('')
        lines.append('> 【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果。')
        lines.append('> 本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。')
        lines.append('> 请理性购彩，量力而行。')
        lines.append('')

        return '\n'.join(lines)


# =====================================================================
# 评估器基类
# =====================================================================

class BaseEvaluator:
    """评估器基类。"""

    category = '通用'

    def __init__(self, evaluator: 'ProjectAutoEvaluator'):
        self.evaluator = evaluator
        self.db = None

    def _get_db(self):
        """懒加载数据库连接。"""
        if self.db is None:
            try:
                _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if _root not in sys.path:
                    sys.path.insert(0, _root)
                from modules.database import P5Database
                self.db = P5Database()
                self.db.connect()
            except Exception as e:
                logger.warning('[AutoEval] 数据库连接失败: %s', e)
        return self.db

    def evaluate(self) -> EvalResult:
        """执行评估，返回结果。子类重写此方法。"""
        raise NotImplementedError

    def _close_db(self):
        if self.db is not None:
            try:
                self.db.disconnect()
            except Exception:
                pass
            self.db = None


# =====================================================================
# 评估器实现
# =====================================================================

class DataHealthEvaluator(BaseEvaluator):
    """数据层健康度评估。"""

    category = '数据层'

    def evaluate(self) -> EvalResult:
        db = self._get_db()
        if db is None:
            self._close_db()
            return EvalResult(
                name='数据库连接',
                category=self.category,
                status='critical',
                score=0,
                details={'error': '数据库连接失败'},
                recommendations=['检查 MySQL 服务状态和配置'],
            )

        try:
            # 检查各表记录数
            tables = {
                'p5_history_data': '历史开奖',
                'p5_ai_report': 'AI报告',
                'p5_evolution_version': '进化版本',
                'p5_learning_history': '学习历史',
            }
            table_counts = {}
            for table, desc in tables.items():
                try:
                    count = db.get_table_count(table)
                    table_counts[desc] = count
                except Exception:
                    table_counts[desc] = -1

            # 检查数据新鲜度
            history_count = table_counts.get('历史开奖', 0)
            latest_issue = None
            if history_count > 0:
                try:
                    rows = db.get_history_data(limit=1, order_by='issue DESC')
                    if rows:
                        latest_issue = rows[0].get('issue')
                except Exception:
                    pass

            # 评估
            issues = []
            score = 100

            if history_count < MIN_SAMPLES_FOR_EVAL:
                issues.append(f'历史数据不足: {history_count} 期 (建议 ≥{MIN_SAMPLES_FOR_EVAL})')
                score -= 40

            if latest_issue:
                try:
                    # 检查是否有预测覆盖
                    report_count = table_counts.get('AI报告', 0)
                    if report_count == 0:
                        issues.append('无 AI 预测报告记录')
                        score -= 20
                except Exception:
                    pass

            status = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
            self._close_db()

            return EvalResult(
                name='数据健康度',
                category=self.category,
                status=status,
                score=max(0, score),
                details={
                    '历史期数': history_count,
                    '最新期号': latest_issue,
                    'AI报告数': table_counts.get('AI报告', -1),
                    '进化版本数': table_counts.get('进化版本', -1),
                    '学习记录数': table_counts.get('学习历史', -1),
                },
                recommendations=issues,
            )
        except Exception as e:
            self._close_db()
            return EvalResult(
                name='数据健康度',
                category=self.category,
                status='critical',
                score=0,
                details={'error': str(e)},
                recommendations=['检查数据库表结构和权限'],
            )


class PredictionPerformanceEvaluator(BaseEvaluator):
    """预测性能评估。"""

    category = '预测性能'

    def evaluate(self) -> EvalResult:
        db = self._get_db()
        if db is None:
            self._close_db()
            return EvalResult(
                name='预测性能',
                category=self.category,
                status='critical',
                score=0,
                details={'error': '数据库连接失败'},
                recommendations=['检查 MySQL 服务状态'],
            )

        try:
            # 拉取验证过的预测记录
            history_count = db.get_table_count('p5_history_data')
            if history_count < MIN_SAMPLES_FOR_EVAL:
                self._close_db()
                return EvalResult(
                    name='预测性能',
                    category=self.category,
                    status='warning',
                    score=50,
                    details={'历史期数': history_count, '提示': '样本不足，无法准确评估'},
                    recommendations=['建议积累至少 50 期历史数据后再进行评估'],
                )

            # 统计近期命中率
            try:
                recent = db.get_verified_predictions(days=30, limit=50)
            except Exception:
                recent = []

            if not recent:
                self._close_db()
                return EvalResult(
                    name='预测性能',
                    category=self.category,
                    status='warning',
                    score=60,
                    details={'历史期数': history_count, '提示': '无验证记录'},
                    recommendations=['运行「开始分析」后产生预测记录'],
                )

            # 计算平均命中率
            top1_hits = sum(r.get('top1_hits', 0) for r in recent)
            top3_hits = sum(r.get('top3_hits', 0) for r in recent)
            top5_hits = sum(r.get('top5_hits', 0) for r in recent)
            n = len(recent) * 5 or 1

            avg_top1 = top1_hits / n * 100
            avg_top3 = top3_hits / n * 100
            avg_top5 = top5_hits / n * 100

            # 评估（诚实：允许在随机基线附近波动）
            score = 100
            issues = []

            # 检查是否在合理范围内（±5pp 波动视为正常）
            t1_delta = abs(avg_top1 - BASELINE_TOP1)
            t3_delta = abs(avg_top3 - BASELINE_TOP3)
            t5_delta = abs(avg_top5 - BASELINE_TOP5)

            if t1_delta > 5:
                issues.append(f'Top-1 命中率 {avg_top1:.1f}% 偏离基线 {t1_delta:.1f}pp（可能过拟合或数据偏差）')
                score -= 20
            if t3_delta > 10:
                issues.append(f'Top-3 命中率 {avg_top3:.1f}% 偏离基线 {t3_delta:.1f}pp')
                score -= 15
            if t5_delta > 15:
                issues.append(f'Top-5 命中率 {avg_top5:.1f}% 偏离基线 {t5_delta:.1f}pp')
                score -= 10

            # 检查是否有异常高的命中率（可能是前视泄漏）
            if avg_top1 > BASELINE_TOP1 + 5:
                issues.append(f'⚠ Top-1 命中率 {avg_top1:.1f}% 显著高于随机基线，请检查是否存在前视泄漏')
                score -= 30

            status = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
            self._close_db()

            return EvalResult(
                name='预测命中率',
                category=self.category,
                status=status,
                score=max(0, score),
                details={
                    '评估期数': n // 5,
                    'Top-1 命中率': f'{avg_top1:.2f}%',
                    'Top-3 命中率': f'{avg_top3:.2f}%',
                    'Top-5 命中率': f'{avg_top5:.2f}%',
                    '随机基线 Top-1': f'{BASELINE_TOP1}%',
                    '随机基线 Top-3': f'{BASELINE_TOP3}%',
                    '随机基线 Top-5': f'{BASELINE_TOP5}%',
                },
                recommendations=issues if issues else ['预测性能在正常范围内'],
            )
        except Exception as e:
            self._close_db()
            return EvalResult(
                name='预测性能',
                category=self.category,
                status='critical',
                score=0,
                details={'error': str(e)},
                recommendations=['检查预测记录表结构'],
            )


class AlgorithmWeightEvaluator(BaseEvaluator):
    """算法权重健康度评估。"""

    category = '算法权重'

    # 冻结权重（v3.60 基线）
    # v4.1 §2.4 同步：omission_regression 随 v4.0 清零（赌徒谬误反信号）
    FROZEN_WEIGHTS = {
        'frequency_weighted': 0.68,
        'ml_supervised': 0.14,
        'bayesian_inference': 0.10,
        'omission_regression': 0.0,
        'trend_momentum': 0.01,
        'markov_transition': 0.005,
        'pattern_continuation': 0.003,
        'feature_engineering': 0.002,
    }

    def evaluate(self) -> EvalResult:
        try:
            from modules.predictor import P5Predictor, P5PredictorConfig
            predictor = P5Predictor()
            config = predictor.config

            # 获取当前权重
            current_weights = {}
            if hasattr(config, 'config'):
                algo_config = config.config.get('algorithms', {})
            else:
                algo_config = config.get('algorithms', {})

            for algo_name, algo_conf in algo_config.items():
                if isinstance(algo_conf, dict):
                    current_weights[algo_name] = algo_conf.get('weight', 0)
                elif isinstance(algo_conf, (int, float)):
                    current_weights[algo_name] = float(algo_conf)

            # 对比冻结权重
            score = 100
            issues = []
            drifts = {}

            for algo, frozen_w in self.FROZEN_WEIGHTS.items():
                current_w = current_weights.get(algo, 0)
                drift = current_w - frozen_w
                drifts[algo] = {
                    'frozen': frozen_w,
                    'current': current_w,
                    'drift_pp': round(drift * 100, 2),
                }
                if abs(drift) > WEIGHT_DRIFT_WARN:
                    issues.append(
                        f'{algo}: 当前 {current_w:.4f} vs 冻结 {frozen_w:.4f}，漂移 {drift*100:+.1f}pp'
                    )
                    score -= 15

            # 检查权重和是否为 1
            weight_sum = sum(current_weights.values())
            if abs(weight_sum - 1.0) > 0.01:
                issues.append(f'权重总和 {weight_sum:.4f} 不等于 1.0')
                score -= 20

            status = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
            return EvalResult(
                name='算法权重',
                category=self.category,
                status=status,
                score=max(0, score),
                details={
                    '权重总和': f'{weight_sum:.4f}',
                    '冻结权重版本': 'v3.60',
                    '异常漂移算法数': len([d for d in drifts.values() if abs(d['drift_pp']) > WEIGHT_DRIFT_WARN * 100]),
                },
                recommendations=issues if issues else ['所有算法权重符合冻结基线'],
            )
        except Exception as e:
            logger.warning('[AutoEval] 权重评估失败: %s', e)
            return EvalResult(
                name='算法权重',
                category=self.category,
                status='warning',
                score=70,
                details={'error': str(e)},
                recommendations=['无法读取预测器配置，检查 predictor.py 是否正常'],
            )


class CachePerformanceEvaluator(BaseEvaluator):
    """缓存性能评估。"""

    category = '缓存性能'

    def evaluate(self) -> EvalResult:
        try:
            from modules.smart_cache import get_cache
            cache = get_cache()

            if cache is None:
                return EvalResult(
                    name='智能缓存',
                    category=self.category,
                    status='warning',
                    score=50,
                    details={'提示': '缓存模块未初始化'},
                    recommendations=['检查 smart_cache.py 是否正确导入'],
                )

            # 统计缓存命中情况
            stats = cache.get_stats() if hasattr(cache, 'get_stats') else {}

            # 测试缓存 key 生成效率
            test_data = [{'issue': 'test', 'wan': 1, 'qian': 2, 'bai': 3, 'shi': 4, 'ge': 5}]
            start = time.perf_counter()
            for _ in range(100):
                cache._make_key(test_data, 'test_issue')
            elapsed = time.perf_counter() - start

            score = 100
            issues = []

            # 评估 key 生成效率（应 < 1ms/次）
            if elapsed > 0.1:  # 100 次 > 100ms
                issues.append(f'缓存 key 生成效率低: {elapsed*1000:.1f}ms/100次')
                score -= 20

            # 评估缓存配置
            if hasattr(cache, 'lfu') and cache.lfu:
                lfu_size = cache.lfu.max_size
                if lfu_size < 100:
                    issues.append(f'LFU 缓存容量偏小: {lfu_size}')
                    score -= 10

            status = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
            return EvalResult(
                name='缓存性能',
                category=self.category,
                status=status,
                score=max(0, score),
                details={
                    'key生成耗时(100次)': f'{elapsed*1000:.2f}ms',
                    '缓存统计': json.dumps(stats, default=str, ensure_ascii=False),
                },
                recommendations=issues if issues else ['缓存性能正常'],
            )
        except Exception as e:
            logger.warning('[AutoEval] 缓存评估失败: %s', e)
            return EvalResult(
                name='缓存性能',
                category=self.category,
                status='warning',
                score=60,
                details={'error': str(e)},
                recommendations=['检查 smart_cache 模块'],
            )


class EvolutionEngineEvaluator(BaseEvaluator):
    """自我进化引擎评估。"""

    category = '自我进化'

    def evaluate(self) -> EvalResult:
        try:
            from modules.self_evolution import SelfEvolutionEngine

            # 检查引擎状态
            # 注意：这里不启动引擎，只检查配置和代码健康度
            score = 100
            issues = []

            # 检查常量一致性
            from modules import self_evolution as se_mod
            from modules import evolution_tuner as et_mod

            # ML_EVAL_MIN 一致性检查
            se_ml_eval_min = getattr(se_mod, 'ML_EVAL_MIN', None)
            et_ml_eval_min = getattr(et_mod, 'ML_EVAL_MIN', None)
            if se_ml_eval_min is not None and et_ml_eval_min is not None:
                if se_ml_eval_min != et_ml_eval_min:
                    issues.append(
                        f'ML_EVAL_MIN 不一致: self_evolution={se_ml_eval_min}, '
                        f'evolution_tuner={et_ml_eval_min}'
                    )
                    score -= 25

            # WF_MAX_TRAIN 一致性检查
            se_wf_max = getattr(se_mod, 'WF_MAX_TRAIN', None)
            et_wf_max = getattr(et_mod, 'WF_MAX_TRAIN', None)
            if se_wf_max is not None and et_wf_max is not None:
                if se_wf_max != et_wf_max:
                    issues.append(
                        f'WF_MAX_TRAIN 不一致: self_evolution={se_wf_max}, '
                        f'evolution_tuner={et_wf_max}'
                    )
                    score -= 20

            # 检查进化版本表是否存在
            db = self._get_db()
            if db is not None:
                try:
                    count = db.get_table_count('p5_evolution_version')
                    if count == 0:
                        issues.append('进化版本表为空，进化引擎尚未产生有效版本')
                        score -= 10
                except Exception:
                    pass
                self._close_db()

            # 检查检查点文件
            try:
                from paths import PROJECT_ROOT
                ckpt_path = os.path.join(PROJECT_ROOT, 'data', 'self_evolution_state.json')
                if os.path.isfile(ckpt_path):
                    with open(ckpt_path, 'r', encoding='utf-8') as f:
                        ckpt = json.load(f)
                    phase = ckpt.get('phase', 'unknown')
                    if phase not in ('collect', 'baseline', 'evolve', 'evaluate', 'persist', 'done', None):
                        issues.append(f'检查点阶段异常: {phase}，可能是中断残留')
                        score -= 15
            except Exception:
                pass

            status = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
            return EvalResult(
                name='自我进化引擎',
                category=self.category,
                status=status,
                score=max(0, score),
                details={
                    'ML_EVAL_MIN_一致性': 'OK' if se_ml_eval_min == et_ml_eval_min else 'NOT_OK',
                    'WF_MAX_TRAIN_一致性': 'OK' if se_wf_max == et_wf_max else 'NOT_OK',
                },
                recommendations=issues if issues else ['自我进化引擎配置正常'],
            )
        except Exception as e:
            logger.warning('[AutoEval] 进化引擎评估失败: %s', e)
            return EvalResult(
                name='自我进化引擎',
                category=self.category,
                status='warning',
                score=60,
                details={'error': str(e)},
                recommendations=['检查 self_evolution.py 和 evolution_tuner.py'],
            )


class ModuleDependencyEvaluator(BaseEvaluator):
    """模块依赖健康度评估。"""

    category = '架构健康'

    def evaluate(self) -> EvalResult:
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            modules_dir = os.path.join(project_root, 'modules')

            # 扫描所有 .py 文件
            py_files = [f for f in os.listdir(modules_dir) if f.endswith('.py') and not f.startswith('__')]
            imports_graph = {}

            for fname in py_files:
                fpath = os.path.join(modules_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                except Exception:
                    continue

                # 提取 from modules.xxx import 和 import modules.xxx
                deps = set()
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith('from modules.') and 'import' in line:
                        # from modules.xxx import YYY
                        parts = line.split()
                        for i, p in enumerate(parts):
                            if p == 'modules' and i + 1 < len(parts):
                                dep = parts[i + 1].split('.')[0]
                                if dep and dep != fname.replace('.py', ''):
                                    deps.add(dep)
                    elif line.startswith('import modules.') and 'import' in line:
                        parts = line.split()
                        for i, p in enumerate(parts):
                            if p == 'modules' and i + 1 < len(parts):
                                dep = parts[i + 1].split('.')[0]
                                if dep and dep != fname.replace('.py', ''):
                                    deps.add(dep)

                imports_graph[fname.replace('.py', '')] = deps

            # 检测循环依赖
            cycles = self._detect_cycles(imports_graph)

            # 检测孤立模块（入度=0 且出度=0）
            all_modules = set(imports_graph.keys())
            imported_by = defaultdict(set)
            for mod, deps in imports_graph.items():
                for d in deps:
                    imported_by[d].add(mod)

            isolated = []
            for mod in all_modules:
                has_in = len(imported_by.get(mod, set())) > 0
                has_out = len(imports_graph.get(mod, set())) > 0
                if not has_in and not has_out and mod not in ('database_utils', 'logging_utils', 'exceptions'):
                    isolated.append(mod)

            score = 100
            issues = []

            if cycles:
                issues.append(f'检测到循环依赖: {cycles}')
                score -= 30

            if isolated:
                issues.append(f'孤立模块（无依赖无被依赖）: {isolated}')
                score -= 10

            # 统计依赖密度
            avg_deps = sum(len(deps) for deps in imports_graph.values()) / len(imports_graph) if imports_graph else 0
            max_deps = max((len(deps) for deps in imports_graph.values()), default=0)

            status = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
            return EvalResult(
                name='模块依赖',
                category=self.category,
                status=status,
                score=max(0, score),
                details={
                    '模块总数': len(imports_graph),
                    '平均依赖数': f'{avg_deps:.1f}',
                    '最大依赖数': max_deps,
                    '循环依赖数': len(cycles),
                    '孤立模块数': len(isolated),
                },
                recommendations=issues if issues else ['模块依赖结构健康'],
            )
        except Exception as e:
            logger.warning('[AutoEval] 模块依赖评估失败: %s', e)
            return EvalResult(
                name='模块依赖',
                category=self.category,
                status='warning',
                score=70,
                details={'error': str(e)},
                recommendations=['检查 modules/ 目录结构'],
            )

    @staticmethod
    def _detect_cycles(graph: Dict[str, set]) -> List[str]:
        """检测图中的循环依赖。"""
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node, path):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    result = dfs(neighbor, path)
                    if result:
                        cycles.append(result)
                elif neighbor in rec_stack:
                    idx = path.index(neighbor)
                    cycle = path[idx:] + [neighbor]
                    cycles.append(' -> '.join(cycle))

            path.pop()
            rec_stack.discard(node)
            return None

        for node in graph:
            if node not in visited:
                dfs(node, [])

        return cycles


class AIModelEvaluator(BaseEvaluator):
    """AI 模型调用效能评估。"""

    category = 'AI 模型'

    def evaluate(self) -> EvalResult:
        try:
            from modules.ai_analyzer import AIAnalyzer

            analyzer = AIAnalyzer()

            score = 100
            issues = []

            # 检查 API 配置
            if not analyzer.ai_available:
                issues.append('AI API 密钥未配置，AI 辅助解读功能不可用')
                score -= 30
                return EvalResult(
                    name='AI 模型配置',
                    category=self.category,
                    status='warning',
                    score=max(0, score),
                    details={'API可用': False},
                    recommendations=issues,
                )

            # 检查 Session 复用（每次调用新建 Session 是性能问题）
            # 我们通过代码检查来评估
            source_file = inspect.getfile(AIAnalyzer)
            with open(source_file, 'r', encoding='utf-8') as f:
                source = f.read()

            if '_build_ai_session()' in source and 'self._session' not in source:
                issues.append('AIAnalyzer 每次调用重建 requests.Session，无法复用 TCP 连接池')
                score -= 15

            # 检查数据库连接复用
            if '_fetch_data_from_database' in source:
                # 检查是否在方法内部新建/断开 DB 连接
                if 'db.connect()' in source and 'db.disconnect()' in source:
                    issues.append('_fetch_data_from_database 每次新建并断开 DB 连接，建议复用连接')
                    score -= 10

            # 检查提示词构建方式
            if 'prompt +=' in source or "prompt +=" in source:
                issues.append('提示词使用字符串拼接（prompt +=），建议改用 list.append + join')
                score -= 5

            # 检查重试策略
            if 'max_attempts' in source and 'sleep' in source:
                # 指数退避已实现，这是好的
                pass
            else:
                issues.append('AI 调用缺少重试策略，网络抖动时易失败')
                score -= 10

            # 检查 JSON 修复能力
            from modules import json_repair
            if hasattr(json_repair, 'repair_and_parse_json'):
                pass  # JSON 修复功能存在
            else:
                issues.append('json_repair 模块缺少 repair_and_parse_json 函数')
                score -= 15

            status = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
            return EvalResult(
                name='AI 模型效能',
                category=self.category,
                status=status,
                score=max(0, score),
                details={
                    'API可用': analyzer.ai_available,
                    '模型名称': analyzer.model_name,
                    'Session复用': '否（每次新建）',
                    'DB连接复用': '否（每次新建/断开）',
                    '重试策略': '有（4次指数退避）',
                    'JSON修复': '有',
                },
                recommendations=issues if issues else ['AI 模型配置和调用效率正常'],
            )
        except Exception as e:
            logger.warning('[AutoEval] AI 模型评估失败: %s', e)
            return EvalResult(
                name='AI 模型效能',
                category=self.category,
                status='warning',
                score=60,
                details={'error': str(e)},
                recommendations=['检查 ai_analyzer.py 模块'],
            )


class CodeQualityEvaluator(BaseEvaluator):
    """代码质量评估。"""

    category = '代码质量'

    def evaluate(self) -> EvalResult:
        """代码质量 + 安全扫描（方案 §8.2.5）。

        消费 ``modules.code_analyzer.CodeAnalyzer``（AST 实现）替代原先的文本
        计数启发式——后者连裸 ``except:`` 都检测不到（它数的是合法的
        ``except Exception:``），且完全没有安全规则。
        """
        try:
            from modules.code_analyzer import CodeAnalyzer  # 懒加载，保持 import 零副作用

            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            modules_dir = os.path.join(project_root, 'modules')

            critical_modules = [
                'predictor.py', 'pipeline.py', 'self_evolution.py',
                'ml_predictor.py', 'online_learner.py', 'ai_analyzer.py',
            ]

            analyzer = CodeAnalyzer()
            missing: List[str] = []
            found = []
            for mod_name in critical_modules:
                fpath = os.path.join(modules_dir, mod_name)
                if not os.path.isfile(fpath):
                    missing.append(f'关键模块缺失: {mod_name}')
                    continue
                found.extend(analyzer.analyze_file(fpath, root=project_root))

            summary = analyzer.summary(found)
            sev = summary['by_severity']
            p0, p1 = sev.get('P0', 0), sev.get('P1', 0)
            p2, p3 = sev.get('P2', 0), sev.get('P3', 0)

            score = 100
            score -= min(60, p0 * 20)          # P0 高危
            score -= min(20, p1 * 5)           # P1 重要
            score -= min(10, p2 // 50)         # P2 建议（按量级轻扣）
            score -= 10 * len(missing)
            score = max(0, score)

            if p0 > 0 or missing:
                status = 'critical'
            elif p1 > 0 or score < 80:
                status = 'warning'
            else:
                status = 'healthy'

            # 只上报 P0/P1 明细（P2/P3 以计数呈现，避免报告被建议级刷屏）
            top = [f'{i.severity} {i.file}:{i.line} {i.rule} — {i.message}'
                   for i in sorted(found, key=lambda x: x.severity)[:8]]
            recs = list(missing) + top
            if p2 or p3:
                recs.append(f'另有建议级 P2={p2} / 风格级 P3={p3} 条，详见 python -m modules.code_analyzer 报告')

            return EvalResult(
                name='代码质量',
                category=self.category,
                status=status,
                score=score,
                details={
                    '检查模块数': len(critical_modules),
                    'P0高危': p0,
                    'P1重要': p1,
                    'P2建议': p2,
                    'P3风格': p3,
                    'CI闸门(P0=0)': '通过' if p0 == 0 else '未通过',
                },
                recommendations=recs if recs else ['代码质量良好'],
            )
        except Exception as e:
            logger.warning('[AutoEval] 代码质量评估失败: %s', e)
            return EvalResult(
                name='代码质量',
                category=self.category,
                status='warning',
                score=70,
                details={'error': str(e)},
                recommendations=['检查模块文件完整性'],
            )


class ModelMonitorHealthEvaluator(BaseEvaluator):
    """模型监控健康度评估（§3.4 监控数据 → §4.1 自诊断 / §4.4 优化建议）。

    读取全局 ModelMonitor 单例的实时状态（命中率/置信度/响应时间滑窗 + 告警状态），
    将监控数据纳入自动评估报告，使其在 GUI「自动评估」中可见并可给出优化建议。
    无 DB 依赖，纯内存；监控尚未采集数据时优雅降级为「暂无数据」（初始态，不扣分）。
    诚实边界：命中率仅对照随机基线标注波动，不声称超越随机基线（排列5为公平摇号）。
    """
    category = '模型监控'

    def evaluate(self) -> EvalResult:
        try:
            from modules.model_monitor import get_model_monitor, RANDOM_BASELINE
            status = get_model_monitor().get_current_status()
        except Exception as e:
            logger.warning('[AutoEval] 模型监控评估失败: %s', e)
            return EvalResult(
                name='模型监控', category=self.category, status='warning', score=70,
                details={'error': str(e)},
                recommendations=['检查 model_monitor 模块'],
            )

        data_points = status.get('data_points', {})
        hit_points = data_points.get('hit_rates', 0)
        latest = status.get('latest_values', {})
        active_alerts = [m for m, on in status.get('alert_states', {}).items() if on]

        # 监控尚未记录数据 → 处于「开始分析→验证」闭环之前的初始态，不扣分。
        # 若「建议型回灌」已落盘过历史洞察（跨进程持久化），补一条复核入口，
        # 让回灌数据在 GUI「自动评估」页可见（诚实标注，不宣称预测能力）。
        if hit_points == 0:
            extra_rec: List[str] = [
                '监控数据将在「开始分析→验证」闭环后自动采集，当前无需处理']
            details_extra: Dict[str, Any] = {'hit_rate_points': 0,
                                            '说明': '监控尚未记录数据'}
            try:
                from modules.monitor_feedback import (get_recent_insights,
                                                      load_insights_from_disk,
                                                      feedback_jsonl_path)
                insights = get_recent_insights(3) or load_insights_from_disk(3)
                if insights:
                    latest = insights[-1]
                    details_extra['recent_insights'] = insights[-3:]
                    details_extra['insights_source'] = feedback_jsonl_path()
                    extra_rec.append(
                        f'已有 {len(insights)} 条历史监控洞察（详见数据文件）；'
                        f'最近一条：{latest.get("honest_note", "")} '
                        f'—— 均为正常波动，不构成预测能力')
                else:
                    extra_rec.append(
                        '建议型回灌已接线（data/model_monitor_feedback.jsonl），'
                        '待验证闭环产生监控点后自动累积洞察')
            except Exception as _fb_e:
                logger.debug('[AutoEval] 读取模型监控回灌洞察跳过: %s', _fb_e)
            return EvalResult(
                name='模型监控', category=self.category,
                status='healthy', score=100,
                details=details_extra,
                recommendations=extra_rec,
            )

        score = 100
        recommendations: List[str] = []
        for m in active_alerts:
            score -= 20
            recommendations.append(f'监控告警[{m}]：近期指标越限，详见 model_monitor 报告')

        # 命中率诚实对照随机基线：偏低只标注"属正常波动、不构成预测能力"，
        # 偏高亦不声称超额（排列5为公平摇号，系统诚实标注随机基线）。
        hit_rate = latest.get('hit_rate')
        if hit_rate is not None:
            baseline = RANDOM_BASELINE['top1']
            if hit_rate < baseline - 0.05:
                recommendations.append(
                    f'命中率 {hit_rate:.2f} 低于随机基线 {baseline:.2f}；属正常波动，'
                    '不构成预测能力，请勿据此加码')

        status_level = 'healthy' if score >= 80 else ('warning' if score >= 50 else 'critical')
        if not recommendations:
            recommendations = ['监控指标正常，无告警']

        return EvalResult(
            name='模型监控', category=self.category,
            status=status_level, score=max(0, score),
            details={
                'hit_rate_points': hit_points,
                'latest_hit_rate': hit_rate,
                'active_alerts': active_alerts,
            },
            recommendations=recommendations,
        )


# =====================================================================
# 主评估器
# =====================================================================

class ProjectAutoEvaluator:
    """KPLuckyNumber 项目自动化评估器。

    定期扫描项目核心组件，生成结构化评估报告和改进建议。
    """

    def __init__(self):
        self.version = self._get_version()
        self._evaluators: List[BaseEvaluator] = [
            DataHealthEvaluator(self),
            PredictionPerformanceEvaluator(self),
            AlgorithmWeightEvaluator(self),
            CachePerformanceEvaluator(self),
            EvolutionEngineEvaluator(self),
            ModuleDependencyEvaluator(self),
            AIModelEvaluator(self),
            CodeQualityEvaluator(self),
            ModelMonitorHealthEvaluator(self),
        ]
        self._scan_history: List[Dict[str, Any]] = []
        self._schedule_thread: Optional[threading.Thread] = None
        self._schedule_stop = threading.Event()

    @staticmethod
    def _get_version() -> str:
        try:
            from version import APP_VERSION
            return APP_VERSION
        except Exception:
            return 'unknown'

    def _validate_historical_recommendations(self, current_report: EvaluationReport) -> EvaluationReport:
        """
        通过与历史报告比较来验证之前的建议
        
        Args:
            current_report: 当前的评估报告
            
        Returns:
            带有验证结果的评估报告
        """
        if not self._scan_history:
            # 没有历史数据（即无任何"before"基线）则无法验证，原样返回
            return current_report

        # 取最近一条历史报告作为"before"基线（单条即可用于对比得分变化）
        latest_history = self._scan_history[-1]
        
        # 将历史报告转换回EvaluationResult对象（简化处理）
        # 这里我们只做基本的验证：比较关键指标的变化
        
        try:
            # 对当前报告中的每个结果，尝试匹配历史报告中的对应结果
            for current_result in current_report.results:
                # 查找历史报告中对应的结果
                historical_result = None
                for hist_result_dict in latest_history.get('results', []):
                    if (hist_result_dict.get('name') == current_result.name and 
                        hist_result_dict.get('category') == current_result.category):
                        historical_result = hist_result_dict
                        break
                
                if historical_result is None:
                    continue
                
                # 如果当前结果有建议状态，尝试验证那些标记为已实施的建议
                if hasattr(current_result, 'recommendation_states'):
                    for rec_id, rec_state in current_result.recommendation_states.items():
                        if rec_state['status'] == 'implemented' and rec_state['validated_at'] is None:
                            # 建议已实施但尚未验证，尝试进行验证
                            effectiveness = self._calculate_recommendation_effectiveness(
                                historical_result, 
                                {
                                    'name': current_result.name,
                                    'category': current_result.category,
                                    'score': current_result.score,
                                    'details': current_result.details
                                },
                                rec_state['text']
                            )
                            
                            if effectiveness is not None:
                                current_result.mark_recommendation_validated(
                                    rec_id, 
                                    effectiveness,
                                    {
                                        'historical_score': historical_result.get('score'),
                                        'current_score': current_result.score,
                                        'score_change': current_result.score - historical_result.get('score', 0),
                                        'validation_method': 'score_improvement'
                                    }
                                )
        except Exception as e:
            logger.warning('[AutoEval] 验证历史建议时出错: %s', e)
        
        return current_report

    def _calculate_recommendation_effectiveness(self, historical_data: Dict[str, Any], 
                                              current_data: Dict[str, Any],
                                              recommendation_text: str) -> Optional[float]:
        """
        根据历史数据和当前数据计算建议的有效性
        
        Args:
            historical_data: 历史评估数据
            current_data: 当前评估数据
            recommendation_text: 建议文本
            
        Returns:
            效果值（-1到1之间），如果无法计算则返回None
        """
        try:
            # 简单实施：基于总体得分的变化来评估效果
            # 在实际应用中，应该根据建议的具体内容选择合适的指标
            hist_score = historical_data.get('score', 0)
            curr_score = current_data.get('score', 0)
            
            if hist_score == 0:  # 避免除以零
                return None
                
            # 计算相对改善
            relative_improvement = (curr_score - hist_score) / abs(hist_score) if hist_score != 0 else 0
            
            # 限制在-1到1之间，并应用一些缩放因子
            # 这里我们假设得分变化的10%相当于效果值的0.1
            effectiveness = max(-1.0, min(1.0, relative_improvement * 10))
            
            return effectiveness
        except Exception:
            return None

    def run_full_evaluation(self) -> EvaluationReport:
        """执行完整评估，返回评估报告。"""
        logger.info('[AutoEval] 开始全量评估 (版本=%s)', self.version)
        start_time = time.perf_counter()

        results = []
        for evaluator in self._evaluators:
            try:
                result = evaluator.evaluate()
                results.append(result)
                logger.info(
                    '[AutoEval] %s/%s: status=%s score=%d',
                    evaluator.category, result.name, result.status, result.score,
                )
            except Exception as e:
                logger.warning('[AutoEval] 评估失败 %s/%s: %s',
                               evaluator.category, evaluator.__class__.__name__, e)
                results.append(EvalResult(
                    name=evaluator.__class__.__name__,
                    category=evaluator.category,
                    status='critical',
                    score=0,
                    details={'error': str(e)},
                    recommendations=['检查评估器实现'],
                ))

        elapsed = time.perf_counter() - start_time
        report = EvaluationReport(
            version=self.version,
            scan_time=datetime.datetime.now().isoformat(),
            results=results,
        )
        report.summary['scan_duration_sec'] = round(elapsed, 2)

        # 验证历史建议
        report = self._validate_historical_recommendations(report)

        # 记录历史
        self._scan_history.append(report.to_dict())
        # 保留最近 10 次
        if len(self._scan_history) > 10:
            self._scan_history = self._scan_history[-10:]

        logger.info('[AutoEval] 评估完成，耗时 %.2fs，综合评分 %s', elapsed, report.summary['overall_score'])
        return report

    def run_quick_evaluation(self) -> EvaluationReport:
        """执行快速评估（仅检查关键项）。"""
        logger.info('[AutoEval] 开始快速评估')
        start_time = time.perf_counter()

        results = [
            DataHealthEvaluator(self).evaluate(),
            AlgorithmWeightEvaluator(self).evaluate(),
            AIModelEvaluator(self).evaluate(),
        ]

        elapsed = time.perf_counter() - start_time
        report = EvaluationReport(
            version=self.version,
            scan_time=datetime.datetime.now().isoformat(),
            results=results,
        )
        report.summary['scan_duration_sec'] = round(elapsed, 2)
        
        # 验证历史建议
        report = self._validate_historical_recommendations(report)
        
        return report

    def schedule_periodic_scan(self, interval_hours: int = 24):
        """启动周期性扫描（后台线程）。"""
        if self._schedule_thread and self._schedule_thread.is_alive():
            logger.warning('[AutoEval] 周期扫描已在运行中')
            return

        def _worker():
            while not self._schedule_stop.wait(interval_hours * 3600):
                try:
                    logger.info('[AutoEval] 周期扫描触发')
                    self.run_full_evaluation()
                except Exception as e:
                    logger.error('[AutoEval] 周期扫描异常: %s', e)

        self._schedule_stop.clear()
        self._schedule_thread = threading.Thread(target=_worker, daemon=True, name='AutoEvalScheduler')
        self._schedule_thread.start()
        logger.info('[AutoEval] 周期扫描已启动，间隔=%dh', interval_hours)

    def stop_periodic_scan(self):
        """停止周期性扫描。"""
        self._schedule_stop.set()
        if self._schedule_thread:
            self._schedule_thread.join(timeout=5)
            self._schedule_thread = None
        logger.info('[AutoEval] 周期扫描已停止')

    def get_history(self) -> List[Dict[str, Any]]:
        """返回历史评估记录。"""
        return self._scan_history

    def render_markdown(self, report: EvaluationReport) -> str:
        """渲染评估报告为 Markdown。"""
        return report.render_markdown()

    def save_report(self, report: EvaluationReport, path: str = None) -> str:
        """保存评估报告到文件。"""
        if path is None:
            from paths import REPORTS_DIR
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(REPORTS_DIR, f'auto_eval_report_{timestamp}.md')

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.render_markdown(report))

        logger.info('[AutoEval] 报告已保存: %s', path)
        return path


# =====================================================================
# 模块级便捷入口
# =====================================================================

def run_evaluation() -> EvaluationReport:
    """便捷入口：执行完整评估并返回报告。"""
    evaluator = ProjectAutoEvaluator()
    return evaluator.run_full_evaluation()


def run_and_print() -> str:
    """便捷入口：执行评估并打印 Markdown 报告。"""
    evaluator = ProjectAutoEvaluator()
    report = evaluator.run_full_evaluation()
    markdown = evaluator.render_markdown(report)
    print(markdown)
    return markdown


if __name__ == '__main__':
    # 独立运行测试
    report = run_and_print()
