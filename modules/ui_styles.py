# -*- coding: utf-8 -*-
"""ui_styles.py — Tkinter/ttk 中央样式工厂（v5.0 §2.1 阶段二 UI/UX 专项）。

职责：
    1. 统一色彩 token 解析 / 变亮变暗 / 按钮正常态还原（收敛 main.py 散落实现）；
    2. 统一 tk.Button 的 hover / disabled / active 三态（Tkinter 无原生 hover）；
    3. 统一注册 ttk 控件样式（TButton / TLabel / TFrame / TNotebook / TEntry）。

设计约束（对齐项目惯例）：
    * **纯函数优先**：色彩换算不依赖 GUI，可离线单测；
    * **import 零副作用**：不在模块级创建窗口 / 读配置 / 连 DB，tkinter 仅在函数内使用；
    * **主题入口唯一**：色值一律经 ``colors`` 字典（来自 ``ThemeManager.get_theme()``）
      解析，与 ``visualizer.set_theme`` 单一入口保持一致。按钮正常态只存 **主题键
      token**（或静态 hex），主题切换后按 token 重新取当前主题值，杜绝旧主题色泄漏。

适用对象区分（重要）：
    * 本模块 hover/state 系列面向 **tk.Button**（支持 ``bg``/``fg`` 直接 config）；
    * ``build_ttk_styles`` 面向 **ttk 控件**（ttk.Button 不支持 bg 直改，须经 Style）。

用法:
    from modules.ui_styles import (
        resolve_color_token, lighten_color, bind_hover,
        set_button_state, build_ttk_styles,
    )

    btn = tk.Button(parent, text='开始分析', bg=resolve_color_token('bg_card', COLORS))
    remember_button_normal(btn, 'bg_card', 'text_primary')
    bind_hover(btn, COLORS)
    set_button_state(btn, 'disabled', COLORS)
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

__all__ = [
    'resolve_color_token',
    'token_for_color',
    'build_color_slot_map',
    'SLOT_PRIORITY',
    'lighten_color',
    'darken_color',
    'readable_text_color',
    'default_hover_bg',
    'remember_button_normal',
    'apply_button_normal_style',
    'bind_hover',
    'set_button_state',
    'build_ttk_styles',
    'STATUS_RUNNING',
    'STATUS_DONE',
    'STATUS_FAILED',
    'STATUS_CANCELLED',
    'classify_progress_text',
    'format_duration',
    'format_progress_text',
    'format_stage_text',
    'ProgressEstimator',
]

# 按钮三态属性名（与 main.py / verify_btn_states_offscreen.py 既有契约保持一致）
ATTR_NORMAL_BG = '_normal_bg'
ATTR_NORMAL_FG = '_normal_fg'

# 语义槽优先级（v5.0 §2.3）：同一色值在一个主题内可能对应多个键
# （浅色主题 '#ffffff' 同时是 bg_secondary / bg_card / badge_active_fg），
# 反查「色值 → 语义槽」时按此顺序取首个命中，避免徽章、阴影、渐变等低频键
# 抢占控件常用的背景/文字槽。顺序即优先级，越靠前越优先。
SLOT_PRIORITY = (
    'bg_primary', 'bg_secondary', 'bg_card', 'bg_card_hover', 'bg_input', 'bg_panel',
    'text_primary', 'text_secondary', 'text_muted', 'text_disabled',
    'border', 'border_light', 'divider',
    'accent_p5', 'accent_p5_light', 'accent_p5_bright',
    'accent_ai', 'accent_ai_light', 'accent_ai_bright',
    'accent_danger', 'accent_info', 'accent_warning', 'success', 'warning',
)

# hover 高亮系数（变亮）；禁用态文字优先取主题 text_muted，回退固定灰
HOVER_LIGHTEN_FACTOR = 1.18
_FALLBACK_MUTED = '#9ca3af'


def build_color_slot_map(colors: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """构建「色值 → 语义槽键」映射（供主题反向映射与 token 反查共用）。

    按 SLOT_PRIORITY 取首个命中，兜底补齐优先级表未覆盖的键。
    已知残余局限：对主题内重色的低频键（bg_panel、gradient_end 等）无法还原其真实
    语义槽，会落到优先级更高的同色键上（视觉上同为深/浅底，无可见差异）。

    参数:
        colors: 主题配色字典；None 时返回空映射

    返回:
        {色值: 语义槽键}
    """
    mapping: Dict[str, str] = {}
    if not isinstance(colors, dict):
        return mapping
    for key in SLOT_PRIORITY:
        value = colors.get(key)
        if isinstance(value, str) and value not in mapping:
            mapping[value] = key
    for key, value in colors.items():  # 兜底补齐
        if isinstance(value, str) and value not in mapping:
            mapping[value] = key
    return mapping


# ---------------------------------------------------------------------------
# 色彩工具（纯函数，无 GUI 依赖）
# ---------------------------------------------------------------------------
def resolve_color_token(value: Any, colors: Optional[Dict[str, str]] = None) -> Any:
    """把「主题键 token / 静态 hex」解析为当前主题色值。

    参数:
        value: 主题键名（如 'bg_card'）或静态 hex（如 '#10b981'）
        colors: 当前主题色字典；为 None 时视作无主题上下文，原样返回

    返回:
        value 是 colors 的键 → 取当前主题值；否则（静态 hex 或未知键）原样返回。
    """
    try:
        if isinstance(value, str) and isinstance(colors, dict) and value in colors:
            return colors[value]
    except Exception:
        pass
    return value


def token_for_color(color: Any, colors: Optional[Dict[str, str]] = None) -> Any:
    """反查色值对应的主题键（hex/主题值 → token），命中则返回键名，否则原样返回。

    用途：按钮创建时常直接接收 ``COLORS['accent_p5']`` 这类**当前值**，若把该
    快照存进 ``_normal_bg``，主题切换后还原的仍是旧主题色（泄漏）。经本函数
    反查为 token（如 'accent_p5'）后，还原时按当前主题重新取值，即可跟随主题。

    参数:
        color: 色值（通常来自主题的当前值或静态 hex）
        colors: 当前主题色字典

    返回:
        token 键名（若该色值能在 colors 中反查到），否则返回原值。
    """
    try:
        if isinstance(color, str) and isinstance(colors, dict):
            slot = build_color_slot_map(colors).get(color)
            if slot is not None:
                return slot
    except Exception:
        pass
    return color


def lighten_color(hex_color: str, factor: float = HOVER_LIGHTEN_FACTOR) -> str:
    """按比例变亮十六进制颜色（factor > 1 变亮）。

    参数:
        hex_color: '#rrggbb'（允许带 '#'）
        factor: 亮度因子

    返回:
        变亮后的 '#rrggbb'；输入非法时原样返回，不抛异常。
    """
    return _scale_color(hex_color, factor, clamp_high=True)


def darken_color(hex_color: str, factor: float = 0.85) -> str:
    """按比例变暗十六进制颜色（factor < 1 变暗）。"""
    return _scale_color(hex_color, factor, clamp_high=True)


def _is_dark(hex_color: str) -> bool:
    """判断颜色是否为深底（亮度阈值 128），决定 hover 应变亮还是变暗。"""
    try:
        h = str(hex_color).lstrip('#')
        if len(h) != 6:
            return True
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) < 128
    except Exception:
        return True


def _relative_luminance(hex_color: str) -> float:
    """计算 sRGB 相对亮度（WCAG 2.x 公式，0=黑 / 1=白）。

    参数:
        hex_color: '#rrggbb' 色值

    返回:
        float: 相对亮度；解析失败按深色 0.0 处理（回退白字）
    """
    try:
        h = str(hex_color).lstrip('#')
        if len(h) != 6:
            return 0.0
        rgb = []
        for i in (0, 2, 4):
            c = int(h[i:i + 2], 16) / 255.0
            rgb.append(c / 12.92 if c <= 0.04045
                       else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    except Exception:
        return 0.0


def readable_text_color(background: str, colors: Optional[Dict[str, str]] = None) -> str:
    """按背景亮度选择可读性最高的前景色（v5.3 GUI-UX 对比度修复）。

    背景：大号操作按钮直接使用主题 ``text_primary`` 作前景——深色主题下
    该值为近白，叠加在琥珀色 ``#f59e0b`` 上对比度仅约 2.2:1，文字几乎糊掉。
    本函数按 WCAG 相对亮度择一：亮底用深墨色、深底用白色，保证两套主题
    在任意 accent 底色上都达到可读对比度。

    参数:
        background: 按钮背景色（'#rrggbb'，可经 ``colors`` token 解析）
        colors: 当前主题色字典（可选，用于把 token 解析为色值）

    返回:
        str: '#ffffff' 或 '#111827'（后者刻意区别于两套主题的
        ``text_primary``，避免离屏守卫把「静态深色前景」误判为旧主题残留）
    """
    bg = resolve_color_token(background, colors)
    # 0.35 为实测阈值：accent_p5(#059669)≈0.23 → 白字；accent_warning_light
    # (#f59e0b)≈0.43 → 深字；accent_ai(#7c3aed)≈0.11 → 白字。
    return '#ffffff' if _relative_luminance(bg) <= 0.35 else '#111827'


def default_hover_bg(colors, bg_token: Optional[str], normal_bg: Optional[str]):
    """推导 hover 高亮色：优先主题专供 hover 槽，否则按底色深浅自适应变亮/变暗。

    修复（v5.0 §2.3）：旧实现一律变亮，而浅色主题 bg_card 为 #ffffff，
    变亮系数对纯白恒等（min(255, …)），hover 无任何视觉反馈。现优先取主题的
    ``bg_card_hover`` 槽；缺该槽时按亮度方向自适应——暗底变亮、亮底变暗，
    保证 hover 恒有可见差异。

    参数:
        colors: 当前主题色字典
        bg_token: 正常态背景 token（用于派生 ``<token>_hover``）
        normal_bg: 正常态背景的已解析色值

    返回:
        hover 色值；无法推导时返回 None（调用方按「不改色」处理）
    """
    cs = colors if isinstance(colors, dict) else {}
    if bg_token:
        hover_token = f'{bg_token}_hover'
        if hover_token in cs:
            return cs[hover_token]
    if not normal_bg:
        return None
    return darken_color(normal_bg) if not _is_dark(normal_bg) else lighten_color(normal_bg)


def _scale_color(hex_color: str, factor: float, clamp_high: bool) -> str:
    """按比例缩放 RGB 分量；解析失败时原样返回（UI 样式绝不因颜色异常中断）。"""
    try:
        h = str(hex_color).lstrip('#')
        if len(h) != 6:
            return hex_color
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        r = min(255, int(r * factor))
        g = min(255, int(g * factor))
        b = min(255, int(b * factor))
        return f'#{r:02x}{g:02x}{b:02x}'
    except Exception:
        return hex_color


# ---------------------------------------------------------------------------
# tk.Button 三态（正常 / hover / 禁用）
# ---------------------------------------------------------------------------
def remember_button_normal(btn, bg_token: str, fg_token: str) -> None:
    """记录按钮「正常态」配色 token（供主题切换后还原，避免缓存 hex 快照）。"""
    try:
        setattr(btn, ATTR_NORMAL_BG, bg_token)
        setattr(btn, ATTR_NORMAL_FG, fg_token)
    except Exception:
        pass


def apply_button_normal_style(btn, colors: Optional[Dict[str, str]] = None) -> None:
    """按缓存 token 还原按钮正常态外观（主题切换后调用亦正确）。

    未记录 token 时静默跳过（不抛异常，UI 刷新不应因单个控件失败而中断）。
    """
    try:
        bg_token = getattr(btn, ATTR_NORMAL_BG, None)
        fg_token = getattr(btn, ATTR_NORMAL_FG, None)
        if bg_token is None or fg_token is None:
            return
        btn.config(bg=resolve_color_token(bg_token, colors),
                   fg=resolve_color_token(fg_token, colors))
    except Exception:
        pass


def bind_hover(btn, colors=None,
               hover_bg: Optional[str] = None,
               hover_fg: Optional[str] = None):
    """统一绑定 hover 三态（进入高亮 / 离开还原）。

    Tkinter 无原生 hover，故手动绑定 <Enter>/<Leave>。两处均做 **禁用守卫**：
    按钮处于 disabled 时不改色，避免「禁用却仍高亮」的误导视觉。

    主题动态性（v5.0 §2.3 修复）：colors 可传**字典**或**返回字典的可调用对象**。
    色值一律在事件回调内解析——旧实现在绑定瞬间解析并闭包固定 normal_bg，
    主题切换后 <Leave> 还原的仍是旧主题色（切换后一次鼠标离开即泄漏旧色）。
    传可调用对象（如 ``lambda: COLORS``）可保证每次事件都取当前主题。

    参数:
        btn: tk.Button（须已 remember_button_normal，否则退化为不改色）
        colors: 当前主题色字典，或返回该字典的可调用对象（推荐，随主题切换）
        hover_bg / hover_fg: 自定义高亮色（token 或 hex）；为 None 时由正常态色按系数推导

    返回:
        (on_enter, on_leave) 回调元组，便于程序化触发与单测
        （Tk 的 <Enter>/<Leave> 合成事件在部分平台不派发，直接调用回调更可靠）；
        未记录 token 或异常时返回 (None, None)。
    """
    try:
        bg_token = getattr(btn, ATTR_NORMAL_BG, None)
        fg_token = getattr(btn, ATTR_NORMAL_FG, None)
        if bg_token is None:
            return (None, None)

        def _current_colors() -> Dict[str, str]:
            try:
                resolved = colors() if callable(colors) else colors
            except Exception:
                return {}
            return resolved if isinstance(resolved, dict) else {}

        def _on_enter(_event=None):
            try:
                if str(btn['state']) == 'disabled':
                    return
                cs = _current_colors()
                normal = resolve_color_token(bg_token, cs)
                hi = (resolve_color_token(hover_bg, cs) if hover_bg is not None
                      else default_hover_bg(cs, bg_token, normal))
                if hi:
                    btn.config(bg=hi)
                if hover_fg is not None:
                    btn.config(fg=resolve_color_token(hover_fg, cs))
            except Exception:
                pass

        def _on_leave(_event=None):
            try:
                if str(btn['state']) == 'disabled':
                    return
                cs = _current_colors()
                normal = resolve_color_token(bg_token, cs)
                if normal:
                    btn.config(bg=normal)
                if fg_token:
                    btn.config(fg=resolve_color_token(fg_token, cs))
            except Exception:
                pass

        btn.bind('<Enter>', _on_enter)
        btn.bind('<Leave>', _on_leave)
        return _on_enter, _on_leave
    except Exception:
        return (None, None)


def set_button_state(btn, state: str, colors: Optional[Dict[str, str]] = None) -> None:
    """设置按钮启用/禁用，并同步视觉态。

    * NORMAL：恢复 state 并按 token 还原正常态配色；
    * DISABLED：置 state=disabled，文字降为 text_muted（背景保持，避免主题切换异常）。

    参数:
        btn: tk.Button
        state: 'normal' | 'disabled'（其余值按 Tk 原生语义直接下发）
        colors: 当前主题色字典
    """
    try:
        st = str(state).lower()
        if st == 'disabled':
            btn.config(state='disabled')
            muted = resolve_color_token('text_muted', colors) or _FALLBACK_MUTED
            try:
                btn.config(fg=muted)
            except Exception:
                pass
        elif st == 'normal':
            btn.config(state='normal')
            apply_button_normal_style(btn, colors)
        else:
            btn.config(state=state)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ttk 控件样式注册（ttk 不支持 bg 直改，须经 Style）
# ---------------------------------------------------------------------------
def build_ttk_styles(root, colors: Optional[Dict[str, str]] = None) -> Any:
    """统一注册 ttk 控件样式（TButton / TLabel / TFrame / TNotebook / TEntry）。

    说明：
        Tkinter 默认主题（vista / aqua）**不支持**修改按钮背景色，故优先切换到
        'clam' 主题以保证样式生效；切换失败（主题不可用）时降级继续，不抛异常。

    参数:
        root: tk.Tk / tk.Toplevel 根窗口
        colors: 当前主题色字典

    返回:
        ttk.Style 实例；依赖缺失或异常时返回 None（调用方需判空）。
    """
    try:
        import tkinter.ttk as ttk  # 懒加载：非 GUI 环境 import 本模块不受影响
    except Exception:
        return None

    colors = colors or {}
    try:
        style = ttk.Style(root)
        try:
            style.theme_use('clam')
        except Exception:
            pass

        bg = colors.get('bg_primary') or colors.get('bg_card') or '#1e293b'
        card = colors.get('bg_card', bg)
        fg = colors.get('text_primary', '#f8fafc')
        muted = colors.get('text_muted', _FALLBACK_MUTED)
        # 修复（v5.3 GUI-UX）：原取色链 'accent' / 'primary' 在主题色板中均不存在，
        # accent 恒回退为 text_primary；一旦据此配置选中页签背景，深色主题会出现
        # 「近白底 + 近白字」的不可见页签。现改为真实语义槽 accent_p5。
        accent = colors.get('accent_p5') or colors.get('accent') or fg
        hover = lighten_color(card)

        style.configure('TFrame', background=bg)
        style.configure('TLabel', background=bg, foreground=fg)
        style.configure('TLabelframe', background=bg, foreground=fg)
        style.configure('TLabelframe.Label', background=bg, foreground=fg)

        style.configure('TButton', background=card, foreground=fg,
                        borderwidth=0, focuscolor=accent, padding=(10, 4))
        style.map('TButton',
                  background=[('active', hover), ('disabled', card)],
                  foreground=[('disabled', muted), ('active', fg)])

        try:
            # P2 收口：Tab 内边距接入 SPACING token（md=12 横向 / sm=8 纵向），
            # 消除硬编码 (12,6)，与卡片间距栅格统一。
            from modules.ui_theme_spec import SPACING as _SP
            _tab_pad = (_SP.get('md', 12), _SP.get('sm', 6))
            style.configure('TNotebook', background=bg, borderwidth=0,
                            tabmargins=(0, 0, 0, 0))
            style.configure('TNotebook.Tab', background=card, foreground=fg,
                            borderwidth=0, padding=_tab_pad)
            # 选中页签：accent 实底 + 按亮度自适应前景（绿底白字 / 琥珀底深字），
            # 避免深色主题「近白底近白字」不可见，同时保证浅色主题对比度。
            style.map('TNotebook.Tab',
                      background=[('selected', accent),
                                  ('active', hover)],
                      foreground=[('selected', readable_text_color(accent)),
                                  ('active', fg)])
        except Exception:
            pass

        try:
            style.configure('TEntry', fieldbackground=card, foreground=fg,
                            borderwidth=1, insertcolor=fg)
        except Exception:
            pass

        try:
            style.configure('TCombobox', fieldbackground=card, background=card,
                            foreground=fg, arrowcolor=fg, borderwidth=1,
                            padding=(6, 3))
            style.map('TCombobox',
                      fieldbackground=[('readonly', card)],
                      foreground=[('readonly', fg)],
                      selectbackground=[('readonly', accent)],
                      selectforeground=[('readonly', readable_text_color(accent))])
        except Exception:
            pass

        return style
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 进度文案统一（v5.0 §2.1 第 3 项：阶段名 + 百分比 + 预计剩余）
# ---------------------------------------------------------------------------
STATUS_RUNNING = 'running'
STATUS_DONE = 'done'
STATUS_FAILED = 'failed'
STATUS_CANCELLED = 'cancelled'

# 状态判定关键词。判定顺序：100% 段先看完成/失败/取消，其余先取消再失败。
# 说明：'异常终止' 含 '异常' → 失败（非用户取消）；'已取消' 不含失败词，
# 故不会被误判。'数据加载完成' 之类的中间里程碑因百分比 <100 仍判为运行中。
_DONE_WORDS = ('完成', '成功', '已保存')
_FAILED_WORDS = ('失败', '异常', '错误', '未连接', '无数据', '数据不足',
                 '无法', '超时', '不可用')
_CANCELLED_WORDS = ('取消', '中止')

# 终态文案中的「通用状态词」——缺少主语，须回退为任务名，
# 避免出现「完成 100%」这类主语缺失的文案。
_GENERIC_PHASE_TEXTS = frozenset({
    '完成', '已完成', '任务完成', '流水线完成', '综合验证与分析完成',
    '完成(数据库未连接)', '开始', '初始化', '运行中', '分析中', '生成报告中',
})

_STATUS_WORD = {
    STATUS_DONE: '已完成',
    STATUS_FAILED: '失败',
    STATUS_CANCELLED: '已取消',
}


def classify_progress_text(text, value=0.0):
    """按进度文案关键词 + 百分比判定状态：done / failed / cancelled / running。

    上游 ``task_mgr.progress(value, text)`` 的既有文案**完全保持不变**，
    本函数只负责识别，不要求改写任何调用点。

    参数:
        text: 阶段文案（如「完成」「数据库连接失败」「已取消」「数据加载完成」）
        value: 百分比值；>=99.5 视为终态判定窗口（避免把「数据加载完成 25%」
            这类中间里程碑误判为完成）

    返回:
        STATUS_DONE / STATUS_FAILED / STATUS_CANCELLED / STATUS_RUNNING
    """
    try:
        v = float(value)
    except Exception:
        v = 0.0
    s = str(text or '')
    if v >= 99.5:
        if any(w in s for w in _DONE_WORDS):
            return STATUS_DONE
        if any(w in s for w in _FAILED_WORDS):
            return STATUS_FAILED
        if any(w in s for w in _CANCELLED_WORDS):
            return STATUS_CANCELLED
        return STATUS_DONE  # 100% 且无异常词 → 视为完成
    if any(w in s for w in _CANCELLED_WORDS):
        return STATUS_CANCELLED
    if any(w in s for w in _FAILED_WORDS):
        return STATUS_FAILED
    return STATUS_RUNNING


def format_duration(seconds):
    """秒数 → 人类可读剩余时长；非法/负值返回 None。"""
    try:
        s = int(round(float(seconds)))
    except Exception:
        # 非法输入降级：返回 None（调用方按「无法估算」展示，不中断 UI）
        return None
    if s < 0:
        return None
    if s < 60:
        return f'预计剩余约{s}秒'
    m, sec = divmod(s, 60)
    if m < 60:
        return f'预计剩余约{m}分{sec}秒' if sec else f'预计剩余约{m}分钟'
    h, m2 = divmod(m, 60)
    return f'预计剩余约{h}小时{m2}分'


def format_progress_text(value, text, task_name='', eta_seconds=None):
    """统一进度文案模板：阶段名 · 百分比 · 预计剩余（终态省略 ETA）。

    模板（统一 `·` 分隔）：
      运行中: ``阶段名 · 42% · 预计剩余约1分20秒``（ETA 不可估时显示「计算中」）
      已完成: ``任务名 · 已完成 · 100%``
      失败:   ``任务名 · 失败原因 · 0%``
      已取消: ``任务名 · 已取消 · NN%``

    参数:
        value: 百分比（0-100，越界自动裁剪）
        text: 阶段文案（来自 ``task_mgr.progress``）
        task_name: 任务名，用作终态主语，使「完成 100%」这类主语缺失文案可读
        eta_seconds: 预计剩余秒数；None 表示不可估（显示「计算中」，不编造数字）

    返回:
        规范化后的单行文案
    """
    try:
        pct = int(round(max(0.0, min(100.0, float(value)))))
    except Exception:
        pct = 0
    status = classify_progress_text(text, pct)
    phase = str(text or '').strip().rstrip('.。…·').strip()
    name = str(task_name or '').strip()

    if status == STATUS_RUNNING:
        subject = phase or name or '任务'
        eta_text = format_duration(eta_seconds) if eta_seconds is not None else '计算中'
        return f'{subject} · {pct}% · {eta_text}'

    # 通用状态词（如「完成」）缺少信息量：无论有无任务名，都规范化为标准状态词，
    # 避免出现「完成 100%」这类半句话。有信息量的原因（如「数据库连接失败」）保留。
    word = _STATUS_WORD.get(status, '已完成')
    subject = phase if phase and phase not in _GENERIC_PHASE_TEXTS else word
    if name:
        return f'{name} · {subject} · {pct}%'
    return f'{subject} · {pct}%'


def format_stage_text(index, total, name=''):
    """统一阶段化文案：``阶段 2/6 · 名称``（与进度模板共用 `·` 分隔符）。

    参数:
        index: 阶段索引（0 基）
        total: 阶段总数
        name: 阶段名称

    返回:
        规范化文案；参数非法时退化为名称本身
    """
    try:
        i = int(index) + 1
        t = max(1, int(total))
    except Exception:
        return str(name or '')
    head = f'阶段 {i}/{t}'
    n = str(name or '').strip()
    return f'{head} · {n}' if n else head


class ProgressEstimator:
    """预计剩余时间（ETA）估计器：按「已耗时 / 已完成比例」线性外推。

    诚实边界（重要）：这是**进度条自身的几何外推**，不是对任务真实耗时的预测。
    进度分布往往不均匀（如 10%→20% 卡很久、90%→100% 秒过），因此：
      * 仅在进度值 >= ``min_value`` 且有效样本数 >= ``min_samples`` 时输出；
      * 结果超界（负值或 > ``max_seconds``）一律返回 None；
      * 无法估计时返回 None，由调用方显示「计算中」而非编造数字。

    用法:
        est = ProgressEstimator()
        est.reset()           # 任务启动时
        eta = est.sample(42)  # 每次进度回调；返回剩余秒数或 None
    """

    def __init__(self, min_value=5.0, min_samples=2, max_seconds=6 * 60 * 60):
        self.min_value = min_value
        self.min_samples = min_samples
        self.max_seconds = max_seconds
        self.reset()

    def reset(self):
        """重置估计状态（新任务开始时调用）。"""
        self._first_ts = None
        self._samples = 0

    def sample(self, value, now=None):
        """记录一个进度样本并返回剩余秒数估计；无法估计时返回 None。"""
        if now is None:
            now = time.time()
        try:
            pct = max(0.0, min(100.0, float(value)))
        except Exception:
            # 非法进度值降级：返回 None（表示本帧无法估算，不中断 UI）
            return None
        if self._first_ts is None:
            self._first_ts = float(now)
            self._samples = 1
            return None
        self._samples += 1
        if pct <= 0 or pct < self.min_value:
            return None
        elapsed = max(0.0, float(now) - self._first_ts)
        if self._samples < self.min_samples or elapsed <= 0:
            return None
        ratio = min(pct / 100.0, 0.9999)
        eta = elapsed * (1.0 - ratio) / ratio
        if eta < 0 or eta > self.max_seconds:
            return None
        return eta
