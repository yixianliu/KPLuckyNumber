"""UI 视觉规范单一来源（v5.2 GUI-08）。

集中导出：
- ThemeManager：深浅主题色板与系统主题检测（自 main.py 原样迁入）；
- SLOT_NAMES：语义槽注册表（主题色板全部键名，供门禁测试消费）；
- 静态色常量族：两主题一致的静态用色（白字 / 命中提示 / 图表色板等）；
- SPACING：间距 token（4/8/12/16/24 栅格）；
- button_style_map：按钮三态样式工厂（按当前主题即时产出）。

设计说明：本模块是 main.py 全部字面 hex 的唯一归宿——main.py 经
``from modules.ui_theme_spec import (...)`` 再导出 ThemeManager 与静态
常量，使 ``main.ThemeManager`` / ``main.COLORS`` 的外部引用（审计脚本、
守护脚本、单元测试，含 ``is`` 身份比较）完全兼容。
"""

import logging
import platform


def _safe_subprocess_run(args, timeout=2, **kwargs):
    """
    安全执行 subprocess 命令，统一参数校验与超时控制。
    参数:
        args: list of str, 命令及其参数
        timeout: 超时秒数，默认 2 秒
        **kwargs: 其他传递给 subprocess.run 的参数
    返回:
        subprocess.CompletedProcess 对象的 stdout（已 strip）
    异常:
        将异常捕获并返回空字符串，以免影响主流程。
    """
    import subprocess
    # 参数校验：确保 args 是列表且所有元素为字符串
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        logging.warning(f"_safe_subprocess_run: 参数非法 {args}")
        return ""
    try:
        # 续行仅为 PEP8 行宽控制（与迁出前行为完全一致）
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, **kwargs)
        return result.stdout.strip()
    except Exception as e:
        logging.warning(f"_safe_subprocess_run: 命令执行失败 {args}: {e}")
        return ""


class ThemeManager:
    """主题管理器：自动检测系统主题并提供浅色/深色主题配色"""

    # 深色主题（原 Tailwind 风格）
    DARK_THEME = {
        'bg_primary': '#0f172a',
        'bg_secondary': '#1e293b',
        'bg_card': '#1e293b',
        'bg_card_hover': '#334155',
        'bg_input': '#1e1e2e',
        'bg_panel': '#020617',
        'accent_p5': '#059669',
        'accent_p5_light': '#10b981',
        'accent_p5_bright': '#34d399',
        'accent_ai': '#7c3aed',
        'accent_ai_light': '#8b5cf6',
        'accent_ai_bright': '#a78bfa',
        'accent_danger': '#dc2626',
        'accent_danger_light': '#ef4444',
        'accent_info': '#0891b2',
        'accent_info_light': '#06b6d4',
        'accent_warning': '#d97706',
        'accent_warning_light': '#f59e0b',
        'text_primary': '#f8fafc',
        'text_secondary': '#cbd5e1',
        'text_muted': '#64748b',
        'text_disabled': '#475569',
        'border': '#334155',
        'border_light': '#475569',
        'success': '#16a34a',
        'success_light': '#22c55e',
        'warning': '#d97706',
        'gradient_start': '#0f172a',
        'gradient_end': '#1e293b',
        'shadow': '#000000',
        'card_shadow': '#000000',
        'card_elevation': 0.04,
        'accent_pink': '#ec4899',
        'accent_pink_light': '#f472b6',
        'gradient_p5': ['#059669', '#10b981'],
        'gradient_ai': ['#7c3aed', '#a78bfa'],
        'surface_highlight': '#ffffff08',
        'divider': '#334155',
        'badge_active': '#059669',
        'badge_trial': '#64748b',
        'badge_rolledback': '#d97706',
        'badge_active_bg': '#059669',
        'badge_active_fg': '#ffffff',
        'badge_trial_bg': '#334155',
        'badge_trial_fg': '#94a3b8',
        'badge_rolledback_bg': '#451a03',
        'badge_rolledback_fg': '#f59e0b',
        # 按钮按下/激活态专供色（v5.2 GUI-06）：原为散落字面量 '#b45309' /
        # '#047857'，双主题同值，token 化后由 _update_widget_colors 统一接管
        'accent_p5_active': '#047857',
        'accent_warning_active': '#b45309',
        # CLI 日志文本框插入光标色（v5.2 GUI-06）：深色亮青 / 浅色深青
        # （浅色底面板下亮青对比度不足，故分主题取值）
        'cursor_cli': '#22d3ee',
    }

    # 浅色主题
    LIGHT_THEME = {
        'bg_primary': '#f1f5f9',
        'bg_secondary': '#ffffff',
        'bg_card': '#ffffff',
        'bg_card_hover': '#f8fafc',
        'bg_input': '#f8fafc',
        'bg_panel': '#f1f5f9',
        'accent_p5': '#059669',
        'accent_p5_light': '#10b981',
        'accent_p5_bright': '#059669',
        'accent_ai': '#7c3aed',
        'accent_ai_light': '#8b5cf6',
        'accent_ai_bright': '#7c3aed',
        'accent_danger': '#dc2626',
        'accent_danger_light': '#ef4444',
        'accent_info': '#0891b2',
        'accent_info_light': '#06b6d4',
        'accent_warning': '#d97706',
        'accent_warning_light': '#f59e0b',
        'text_primary': '#0f172a',
        'text_secondary': '#475569',
        'text_muted': '#94a3b8',
        'text_disabled': '#cbd5e1',
        'border': '#e2e8f0',
        'border_light': '#f1f5f9',
        'success': '#16a34a',
        'success_light': '#22c55e',
        'warning': '#d97706',
        'gradient_start': '#f8fafc',
        'gradient_end': '#f1f5f9',
        'shadow': '#000000',
        'card_shadow': '#00000010',
        'card_elevation': 0.06,
        'accent_pink': '#ec4899',
        'accent_pink_light': '#f472b6',
        'gradient_p5': ['#059669', '#10b981'],
        'gradient_ai': ['#7c3aed', '#a78bfa'],
        'surface_highlight': '#ffffff40',
        'divider': '#e2e8f0',
        'badge_active': '#059669',
        'badge_trial': '#64748b',
        'badge_rolledback': '#d97706',
        'badge_active_bg': '#059669',
        'badge_active_fg': '#ffffff',
        'badge_trial_bg': '#f1f5f9',
        'badge_trial_fg': '#64748b',
        'badge_rolledback_bg': '#fef3c7',
        'badge_rolledback_fg': '#d97706',
        # 按钮按下/激活态专供色（v5.2 GUI-06）：与深色主题同值
        'accent_p5_active': '#047857',
        'accent_warning_active': '#b45309',
        # CLI 日志文本框插入光标色（v5.2 GUI-06）：深色亮青改浅色深青
        'cursor_cli': '#0e7490',
    }

    @staticmethod
    def detect_system_theme() -> str:
        """检测系统主题设置，返回 'light' 或 'dark'"""
        try:
            # Windows 10/11 主题检测
            if platform.system() == 'Windows':
                try:
                    import winreg
                    reg_path = (
                        r'Software\Microsoft\Windows'
                        r'\CurrentVersion\Themes\Personalize')
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                        reg_path) as key:
                        value, _ = winreg.QueryValueEx(
                            key, 'AppsUseLightTheme')
                        return 'light' if value == 1 else 'dark'
                except (OSError, winreg.error):
                    pass

            elif platform.system() == 'Darwin':
                try:
                    # 续行仅为 PEP8 行宽控制（与迁出前行为完全一致）
                    stdout = _safe_subprocess_run(
                        ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                        timeout=2)
                    return 'dark' if stdout.strip() == 'Dark' else 'light'
                except Exception:
                    pass

            elif platform.system() == 'Linux':
                try:
                    # 续行仅为 PEP8 行宽控制（与迁出前行为完全一致）
                    stdout = _safe_subprocess_run(
                        ['gsettings', 'get',
                         'org.gnome.desktop.interface', 'gtk-theme'],
                        timeout=2)
                    theme_name = stdout.strip().strip("'")
                    return 'dark' if any(
                        kw in theme_name.lower()
                        for kw in ['dark', 'adwaita']
                    ) else 'light'
                except Exception:
                    pass
        except Exception:
            pass
        # 默认返回深色主题
        return 'dark'

    @classmethod
    def get_theme(cls, theme_name: str = None) -> dict:
        """获取主题配色方案"""
        if theme_name is None:
            theme_name = cls.detect_system_theme()
        return cls.DARK_THEME if theme_name == 'dark' else cls.LIGHT_THEME

    @classmethod
    def toggle_theme(cls, current_theme: dict) -> dict:
        """切换主题并返回新主题"""
        cur_bg = current_theme.get('bg_primary')
        dark_bg = cls.DARK_THEME['bg_primary']
        if current_theme is cls.DARK_THEME or cur_bg == dark_bg:
            return cls.LIGHT_THEME
        return cls.DARK_THEME


# 语义槽注册表：主题色板的全部键名（双主题取并集，保证两套色板键一致）
SLOT_NAMES = (frozenset(ThemeManager.DARK_THEME)
              | frozenset(ThemeManager.LIGHT_THEME))

# ---------------------------------------------------------------------------
# 静态色常量族（v5.2 GUI-08）：两主题一致的静态用色收编为命名常量，
# 各常量均已在 scripts/audit_ui_bindings.py 的 STATIC_HEX_WHITELIST 登记；
# main.py 全部替换后字面 hex 归零，审计兜底账目自然消解
# ---------------------------------------------------------------------------
# accent 底白字：两主题 accent 槽（accent_p5 / accent_ai / accent_warning）
# 均为饱和深色，白字在其上对比度稳定
WHITE = '#ffffff'
SUCCESS_STATIC = '#2e7d32'      # success 槽兜底：静态提示色，双主题一致
HIT_FG = '#2e7d32'              # 首选命中绿（预测详情对照行）
TOP3_FG = '#e65100'             # Top-3 含开奖橙
MISS_FG = '#b71c1c'             # 未含红
HIT_ROW_BG = '#c8e6c9'          # 命中格浅绿底
HIT_ROW_FG = '#1b5e20'          # 命中格深绿字
CONSENSUS_HIT_FG = '#d1fae5'    # 共识一致浅绿字
CONSENSUS_HIT_BG = '#065f46'    # 共识一致深绿底
ACCENT_P5_SOFT_FG = '#e0e7ff'   # accent_p5 底浅紫白字（副标题）

# matplotlib 图表静态色板：图表按数据整体重绘，非控件切换残留面
CHART_SYSTEM = '#e53e3e'        # 系统实测线红
CHART_SYSTEM_ERR = '#9b2c2c'    # 系统实测误差线暗红
CHART_RANDOM = '#3182ce'        # 随机基线蓝
CHART_RANDOM_ERR = '#2c5282'    # 随机基线误差线暗蓝
CHART_NOTE_TEXT = '#4a5568'     # 图表注释文字灰
CHART_NOTE_FACE = '#f7fafc'     # 图表注释框底色
CHART_NOTE_EDGE = '#cbd5e0'     # 图表注释框边色
CHART_BARS = ['#059669', '#10b981', '#34d399', '#6ee7b7', '#34d399']  # 柱状图五色

# 间距 token（4/8/12/16/24 栅格）：新增 spacing token 语义槽
SPACING = {'xs': 4, 'sm': 8, 'md': 12, 'lg': 16, 'xl': 24}


def button_style_map(colors):
    """按钮三态样式表工厂：按当前主题即时产出 primary/secondary/danger 配色。

    取代 main.py 内两处重复的 styles 字典字面量（_create_styled_button /
    _on_button_leave），主题切换后天然跟随，无旧主题残留。
    参数:
        colors: 当前主题色板（dict，一般传 main.COLORS）
    返回:
        dict: {按钮类型: {'bg': .., 'fg': .., 'active_bg': .., 'font': ..}}
    """
    return {
        'primary': {
            'bg': colors['accent_p5'],
            'fg': WHITE,
            'active_bg': colors['accent_p5_light'],
            'font': ('微软雅黑', 9, 'bold'),
        },
        'secondary': {
            'bg': colors['bg_card'],
            'fg': colors['text_secondary'],
            'active_bg': colors['bg_card_hover'],
            'font': ('微软雅黑', 9),
        },
        'danger': {
            'bg': colors['bg_card'],
            'fg': colors['accent_danger_light'],
            'active_bg': colors['accent_danger'],
            'font': ('微软雅黑', 9),
        },
    }
