# -*- coding: utf-8 -*-
"""
剪贴板构建器（纯函数，可离线单元测试）。
职责：根据 final_report 生成可复制的预测文本（含预测期号），
供 main.py LotteryGUI._build_prediction_clipboard / _direct_extract_clipboard 委托使用。
无 Tkinter / GUI 依赖，可在 headless / pytest 环境直接调用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# 与 main.py 保持一致的内部常量（避免跨模块耦合）
DISPLAY_POS_KEYS = ['wan', 'qian', 'bai', 'shi', 'ge']
DISPLAY_POS_NAMES = ['万位', '千位', '百位', '十位', '个位']
_POS_NAMES_FULL = ['万位', '千位', '百位', '十位', '个位']  # 贝叶斯分段按位索引用（保留5位分析）


def compress_combo(combo: Any) -> str:
    """将 5 位预测组合返回为显示串（v3.64：保留完整5位）。

    核心数据不动，仅用于展示/复制层。幂等：对已是5位的串无副作用。
    """
    if not combo:
        return ''
    s = str(combo)
    return s[:5] if len(s) >= 5 else s


def build_prediction_clipboard(final_report: Dict[str, Any],
                               clipboard_meta: Optional[Dict[str, Any]] = None) -> str:
    """根据四步流水线 final_report 生成结构化的可复制预测摘要（含预测期号）。

    参数
    ----
    final_report : dict
        流水线产出的 final_report 字典。
    clipboard_meta : dict | None
        兜底元数据（例如 self._clipboard_meta），用于在 final_report 缺少
        target_issue/next_issue 时仍能提供预测期号。若均缺失则返回 '未知'。

    返回
    ----
    str
        格式化的可复制文本，首行必含「预测期号: XXXXXXXX」。
    """
    if not isinstance(final_report, dict):
        return ""

    # 阶段九：复制文本首行必须含「预测期号」，适配微信/QQ 直接发送
    target_issue = (final_report.get('target_issue') or
                    final_report.get('next_issue') or
                    (clipboard_meta or {}).get('target_issue') or
                    '未知')

    lines = ["【排列5 预测号码】",
             f"预测期号: {target_issue}",
             f"生成时间: {_now_str()}",
             ""]

    # 1. 走势图数据预测
    trend = final_report.get('trend_prediction') or {}
    if isinstance(trend, dict) and trend:
        lines.append("一、走势图数据预测 (Top 候选)")
        for pos_key, pos_name in zip(DISPLAY_POS_KEYS, DISPLAY_POS_NAMES):
            # `or {}` 而非 dict.get 默认值：键存在但值为 None 时不会触发默认值，
            # 直接 .get('numbers') 会 AttributeError。此处防御式归一为空 dict。
            nums = (trend.get(pos_key) or {}).get('numbers') or []
            if nums:
                lines.append(f"  {pos_name}: {' '.join(str(n) for n in nums)}")
        combos = final_report.get('recommended_combinations', [])
        if isinstance(combos, list) and combos:
            lines.append("  推荐组合:")
            for i, c in enumerate(combos[:10], 1):
                if isinstance(c, dict):
                    _comb = compress_combo(c.get('combination', ''))
                    _reason = c.get('reason', '')
                    _line = f"    {i}. {_comb}"
                    if _reason:
                        _line += f"  — {_reason}"
                    lines.append(_line)
        lines.append("")

    # 2. 专家文章预测
    article = final_report.get('article_prediction') or {}
    if isinstance(article, dict) and article:
        lines.append("二、专家文章预测 (Top 候选)")
        for pos_key, pos_name in zip(DISPLAY_POS_KEYS, DISPLAY_POS_NAMES):
            nums = (article.get(pos_key) or {}).get('numbers') or []
            if nums:
                lines.append(f"  {pos_name}: {' '.join(str(n) for n in nums)}")
        ac = final_report.get('article_recommendations', [])
        if isinstance(ac, list) and ac:
            lines.append("  专家推荐组合:")
            for i, c in enumerate(ac[:5], 1):
                if isinstance(c, dict):
                    lines.append(f"    {i}. {compress_combo(c.get('combination', ''))} "
                                 f"(共识度: {c.get('consensus_degree', 0):.2f})")
        lines.append("")

    # 3. 贝叶斯后验概率 Top-4
    bi = final_report.get('bayesian_inference')
    if isinstance(bi, list) and bi:
        lines.append("三、贝叶斯后验概率 Top-4")
        for i, pos_dict in enumerate(bi[:5]):
            if isinstance(pos_dict, dict) and pos_dict:
                top_nums = sorted(pos_dict.items(),
                                  key=lambda x: float(x[1]), reverse=True)[:4]
                probs = ", ".join(f"{k}({float(v):.3f})" for k, v in top_nums)
                lines.append(f"  {_POS_NAMES_FULL[i]}: {probs}")

    return "\n".join(lines)


def _now_str() -> str:
    """返回当前时间的格式化字符串（供构建剪贴板使用，便于单元测试下mock）。"""
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')