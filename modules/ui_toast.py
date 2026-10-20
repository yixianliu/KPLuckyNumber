"""Toast 浮层提示契约单一来源（v5.2 GUI-10，方案 §331）。

集中导出：
- TOAST_DEFAULT_DURATION_MS：历史默认停留时长（2200ms），保持既有观感；
- TOAST_MAX_DURATION_MS：停留时长硬上限（5000ms），验收契约
  「Toast 自动消失 ≤ 5 s」的唯一权威值；
- clamp_toast_duration：时长入参钳制纯函数（非数字/越界安全回落）；
- toast_icon_for：按背景色解析图标前缀的纯函数（与 main._show_toast
  原内联三元行为等价，析出后可单测）。

设计说明：main.py 的 _show_toast 转调本模块两个纯函数，浮层窗口
生命周期（单实例/淡入淡出/自动销毁）仍留在 GUI 层，避免为契约闸
门引入 tkinter 依赖。排列五开奖为完全独立随机事件，本模块仅涉及
提示文案展示时长，不参与任何号码生成或统计口径。
"""

import math

# Toast 默认停留时长（毫秒）——与 v5.0 以来全部调用点惯例一致
TOAST_DEFAULT_DURATION_MS = 2200

# Toast 停留时长硬上限（毫秒）——方案 §331 验收「自动消失 ≤ 5 s」
TOAST_MAX_DURATION_MS = 5000

# 淡出前最短驻留时长（毫秒）——防止过短停留导致用户来不及阅读
TOAST_MIN_HOLD_MS = 600


def clamp_toast_duration(duration_ms, max_ms=TOAST_MAX_DURATION_MS):
    """把调用方传入的 Toast 停留时长钳制到安全区间。

    规则：
    - 非数字入参（None / 字符串 / bool / NaN）安全回落默认值；
    - 负数钳制为 0（立即进入淡出，由调用方与最短驻留取大）；
    - 超过 max_ms 钳制为 max_ms，兑现「自动消失 ≤ 5 s」契约；
    - max_ms 自身非法时回落 TOAST_MAX_DURATION_MS。

    参数:
        duration_ms: 调用方请求的停留时长（毫秒），可为任意类型
        max_ms: 允许的停留上限（毫秒），默认方案契约值 5000

    返回:
        int: 钳制后的停留时长（毫秒），保证可直接喂给 after()
    """
    # max_ms 非法时回落契约上限，避免把钳制能力自身击穿
    if isinstance(max_ms, bool) or not isinstance(max_ms, (int, float)):
        max_ms = TOAST_MAX_DURATION_MS
    elif isinstance(max_ms, float) and math.isnan(max_ms):
        max_ms = TOAST_MAX_DURATION_MS

    # 非数字（含 bool 与 NaN）安全回落默认停留时长
    if isinstance(duration_ms, bool):
        return TOAST_DEFAULT_DURATION_MS
    if not isinstance(duration_ms, (int, float)):
        return TOAST_DEFAULT_DURATION_MS
    if isinstance(duration_ms, float) and math.isnan(duration_ms):
        return TOAST_DEFAULT_DURATION_MS

    # 无穷大一律按上限处理，规避 after() 拿到 inf 的边界
    if duration_ms == math.inf:
        return int(max_ms)
    if duration_ms == -math.inf:
        return 0

    value = int(duration_ms)
    if value < 0:
        return 0
    if value > max_ms:
        return int(max_ms)
    return value


def toast_icon_for(bg, colors):
    """按 Toast 背景色解析图标前缀，与 _show_toast 原内联三元等价。

    映射规则：
    - success → '✓'（成功）
    - accent_danger / accent_danger_light → '✗'（失败/错误）
    - accent_info → 'ℹ'（信息）
    - 其余（含空值、未知色）→ '⚠'（警示兜底）

    参数:
        bg: Toast 背景色（主题十六进制字符串），调用方已保证非空，
            但纯函数层对空值/None 统一走兜底图标，不抛异常
        colors: 主题色板字典（形如 main.COLORS），按键名取值

    返回:
        str: 单字符图标
    """
    # colors 缺省时按空色板处理，按键取不到值自然落入兜底分支
    palette = colors or {}
    if not bg:
        return '⚠'
    if bg == palette.get('success'):
        return '✓'
    if bg in (palette.get('accent_danger'),
              palette.get('accent_danger_light')):
        return '✗'
    if bg == palette.get('accent_info'):
        return 'ℹ'
    return '⚠'
