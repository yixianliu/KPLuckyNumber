"""
排列5 模型监控「建议型回灌」sink（§3.4 反馈→建议）

设计意图（诚实边界优先）
--------------------------
ModelMonitor 达到反馈阈值时，会把一段命中率/告警摘要通过 ``feedback_callback``
投出。本模块提供该回调的具体实现 ``monitor_feedback_callback``，以及供全局单例
注入的便捷接线函数 ``wire_model_monitor_feedback()``。

关键约束（与既有 v4.0 冻结权重、ONLINE_LEARNER_WRITEBACK 通道解耦）：
  * 本 sink 只做**建议型记录**：把每次监控洞察以 JSONL 形式追加到
    ``data/model_monitor_feedback.jsonl``，并维护一个内存「近期洞察」deque，
    供 auto_evaluator 在 GUI「自动评估」页复核。
  * 绝不调整任何权重、绝不写 ``p5_weight_history`` / Redis 自适应权重通道——
    那些仍由 ``config.ONLINE_LEARNER_WRITEBACK`` 门控，二者相互独立。
  * 排列5 为公平摇号：洞察只标注「属正常波动、不构成预测能力」，
    对照 ``RANDOM_BASELINE['top1']``，不声称超越随机基线。

线程安全
--------
回调由 ModelMonitor 的守护监控线程触发，故写入 JSONL 与更新内存 deque
均持锁进行；读取近期洞察用锁快照，避免半写状态。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# 洞察保留上限（内存 deque + 落盘 JSONL 行数上限，超限丢弃最旧）
_MAX_INSIGHTS = 50
# 洞察 JSONL 相对项目根目录的路径（data/ 为既有可写数据目录）
_FEEDBACK_JSONL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'model_monitor_feedback.jsonl',
)
# 随机基线（诚实边界）——单一来源，避免与 model_monitor 里的拷贝漂移
from modules.model_monitor import RANDOM_BASELINE  # noqa: E402


def _feedback_lock() -> threading.Lock:
    """惰性共享锁：模块内写入 / 读取共用同一把锁。"""
    global _LOCK
    try:
        return _LOCK
    except NameError:
        _LOCK = threading.Lock()
        return _LOCK


# 内存「近期洞察」（供 auto_evaluator 无 DB 依赖读取），受 _LOCK 保护
_INSIGHTS: Deque[Dict[str, Any]] = deque(maxlen=_MAX_INSIGHTS)


def _honest_insight(feedback_data: Dict[str, Any]) -> Dict[str, Any]:
    """把 ModelMonitor 的反馈数据转成一条**诚实洞察**记录。

    Args:
        feedback_data: ModelMonitor._prepare_feedback_data() 的产物。

    Returns:
        可 JSON 序列化的洞察 dict；输入异常时返回最小兜底记录。
    """
    try:
        metrics = feedback_data.get('metrics', {}) or {}
        hit = metrics.get('hit_rate', {}) or {}
        base = metrics.get('confidence', {}) or {}
        active_alerts = (feedback_data.get('alert_summary', {}) or {}).get(
            'active_alerts', [])
        vs_random = (feedback_data.get('baseline_comparison', {}) or {}).get(
            'hit_rate_vs_random', {})
        delta = vs_random.get('delta')
        baseline_top1 = RANDOM_BASELINE['top1']
        sample_count = int(feedback_data.get('sample_count', 0))

        current = hit.get('current')
        # 诚实定性：命中率即便高于基线也不宣称「超额收益」，只作正常波动标注
        if current is None:
            hit_note = '命中率尚无有效窗口数据'
        else:
            if delta is not None and delta >= 0.05:
                hit_note = (
                    f'命中率 {current:.3f} 高于随机基线 {baseline_top1:.3f}；'
                    '属正常波动，不构成预测能力，请勿据此加码')
            elif delta is not None and delta <= -0.05:
                hit_note = (
                    f'命中率 {current:.3f} 低于随机基线 {baseline_top1:.3f}；'
                    '属正常波动，不构成预测能力')
            else:
                hit_note = (
                    f'命中率 {current:.3f} 与随机基线 {baseline_top1:.3f} 相当；'
                    '在随机波动带内，无预测优势')

        return {
            'kind': 'model_monitor_insight',
            'timestamp': feedback_data.get('timestamp'),
            'sample_count': sample_count,
            'hit_rate': current,
            'hit_rate_trend': hit.get('trend'),
            'baseline_top1': baseline_top1,
            'hit_rate_vs_random_delta': delta,
            'honest_note': hit_note,
            'confidence_avg': base.get('avg'),
            'active_alerts': list(active_alerts) if active_alerts else [],
            'assertion': '无预测能力（排列5 公平摇号）',
        }
    except Exception as e:  # 兜底：任何异常都产出一条最小记录，绝不抛出
        return {
            'kind': 'model_monitor_insight',
            'honest_note': f'洞察生成异常(已降级): {e}',
            'assertion': '无预测能力（排列5 公平摇号）',
        }


def _append_jsonl(insight: Dict[str, Any]) -> None:
    """把一条洞察追加到 data/model_monitor_feedback.jsonl（持锁）。

    写入失败（目录不存在/权限）仅记 warning，不影响主监控线程；
    超限截断到最近 _MAX_INSIGHTS 行。
    """
    try:
        os.makedirs(os.path.dirname(_FEEDBACK_JSONL), exist_ok=True)
        with open(_FEEDBACK_JSONL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(insight, ensure_ascii=False, default=str) + '\n')
        # 超限截断（持锁，避免并发读改写交错）
        try:
            with open(_FEEDBACK_JSONL, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if len(lines) > _MAX_INSIGHTS:
                keep = lines[-_MAX_INSIGHTS:]
                with open(_FEEDBACK_JSONL, 'w', encoding='utf-8') as f:
                    f.writelines(keep)
        except Exception as _re:
            logger.debug('模型监控洞察 JSONL 截断跳过(非致命): %s', _re)
    except Exception as e:
        logger.warning('模型监控洞察落盘失败(不影响主流程): %s', e)


def monitor_feedback_callback(feedback_data: Dict[str, Any]) -> None:
    """ModelMonitor.feedback_callback 的具体实现（建议型回灌）。

    被 ModelMonitor 守护线程在达到 ``feedback_trigger_count`` 时调用。
    行为：生成诚实洞察 → 入内存 deque → 追加 JSONL。不触碰任何权重。
    """
    if not isinstance(feedback_data, dict) or not feedback_data:
        logger.debug('模型监控反馈数据为空，跳过建议型回灌')
        return
    insight = _honest_insight(feedback_data)
    lock = _feedback_lock()
    with lock:
        _INSIGHTS.append(insight)
    _append_jsonl(insight)
    logger.info(
        '[ModelMonitorFeedback] 建议型洞察已记录: sample=%s alerts=%s',
        insight.get('sample_count'), insight.get('active_alerts'),
    )


def wire_model_monitor_feedback() -> bool:
    """把本 sink 的回调注入全局 ModelMonitor 单例。

    惰性接线：由 main.py 启动后调用一次即可；未调用时监控线程仍工作，
    只是 ``feedback_callback`` 为 None（即「记录+状态」可用、不回灌洞察）。
    幂等：重复调用不会叠加。

    Returns:
        成功注入或已注入返回 True；注入异常返回 False（不阻断调用方）。
    """
    try:
        from modules.model_monitor import get_model_monitor
        monitor = get_model_monitor()
        if monitor.feedback_callback is None:
            monitor.feedback_callback = monitor_feedback_callback
            logger.info('模型监控建议型回灌已接入（feedback_callback 已注入）')
        return True
    except Exception as e:
        logger.warning('接入模型监控回灌失败(不影响主流程): %s', e)
        return False


def get_recent_insights(limit: int = 10) -> List[Dict[str, Any]]:
    """读取内存「近期洞察」（最旧→最新），供 auto_evaluator 无 DB 依赖消费。

    Args:
        limit: 最多返回最近多少条。

    Returns:
        洞察 dict 列表；无数据时返回空列表。
    """
    lock = _feedback_lock()
    with lock:
        items: List[Dict[str, Any]] = list(_INSIGHTS)
    return items[-max(1, limit):]


def load_insights_from_disk(limit: int = 10) -> List[Dict[str, Any]]:
    """从 data/model_monitor_feedback.jsonl 读取最近洞察（进程重启后补读）。

    与内存 deque 互补：内存只在同进程内累积；JSONL 跨进程持久化。
    文件不存在/损坏时返回空列表，不抛异常。
    """
    items: List[Dict[str, Any]] = []
    try:
        if not os.path.isfile(_FEEDBACK_JSONL):
            return items
        with open(_FEEDBACK_JSONL, 'r', encoding='utf-8') as f:
            for line in f.readlines()[-max(1, limit):]:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    items.append(obj)
        return items
    except Exception as e:
        logger.debug('读取模型监控洞察 JSONL 失败(非致命): %s', e)
        return []


def feedback_jsonl_path() -> str:
    """返回洞察 JSONL 的绝对路径（供 auto_evaluator 在报告中披露来源）。"""
    return _FEEDBACK_JSONL
