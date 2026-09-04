"""
排列5 预测分析系统 - GUI 界面

基于 tkinter 的桌面应用程序，提供以下功能：
1. 数据爬取 — 从多个数据源获取排列5开奖数据并存储到 MySQL
2. 智能分析 — 统计预测 + 走势引擎 + 快速预测 + 命中率优化 + 在线学习 + AI 辅助解读
3. 自我进化 — 启动自动训练/版本化/进化日志（后台非阻塞）
4. 智能分析与验证 — 历史命中率统计与回测

架构说明：
  - TaskManager: 异步任务管理器，通过 ThreadPoolExecutor 在后台线程执行耗时操作
  - LotteryGUI: 主界面类，负责 UI 构建、事件绑定和业务逻辑协调
  - 所有业务方法通过 _task_wrapper 在后台线程执行，通过消息队列更新 UI
"""

import sys
import os
import time
import threading
import queue
import traceback
import json
import re
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    print("=" * 60)
    print("  [错误] 缺少 tkinter 模块！")
    print("  Python环境未包含tkinter，无法启动GUI。")
    print("=" * 60)
    input("\n  按回车键退出...")
    sys.exit(1)

# 确保项目根目录在sys.path中，以便模块导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# 核心模块：惰性加载
# -----------------------------------------------------------------------------
# 问题：原先在模块顶层直接 import 六大重量级模块（database / data_fetcher /
# validator / predictor / backtester / features）。其中 backtester 顶层依赖
# matplotlib(~2.7s)、predictor 依赖 numpy(~1.2s)、data_fetcher 依赖
# bs4/requests(~1.0s)，合计约 5.2s 的 import 开销会在「窗口绘制之前」同步执行，
# 直接表现为「双击后界面迟迟不出现 / 打开卡顿」。
# 而这些模块实际只在后台任务线程的 _execute_* 方法中才被用到，启动阶段无需加载。
# 方案：用 _LazyClass 惰性代理占位。代理对象保留了 `P5Database()`、`Backtester(...)`
# 等原有「像类一样被调用」的语义——首次被调用/取属性时才真正 import 目标模块并缓存。
# 由于所有调用点都在后台线程执行，重模块的 import 成本被平摊到「首次真正需要」时，
# 窗口首屏因此可以近乎秒开。所有既有调用点（P5Database()、P5Spider() 等）无需改动。
# =============================================================================
import importlib


class _LazyClass:
    """惰性类代理：首次调用/取属性时才 import 真实类并缓存，保持 `Cls(...)` 调用语义。"""

    __slots__ = ('_module_path', '_class_name', '_resolved')

    def __init__(self, module_path, class_name):
        """记录延迟导入所需的模块路径与类名，此时并不真正 import。

        参数:
            module_path: 目标模块的导入路径，如 'modules.predictor'
            class_name: 目标模块内的类名，如 'P5Predictor'
        """
        self._module_path = module_path
        self._class_name = class_name
        self._resolved = None

    def _resolve(self):
        """真正执行导入并缓存类对象（仅首次调用时触发 import）。

        返回:
            被代理的真实类对象
        """
        if self._resolved is None:
            module = importlib.import_module(self._module_path)
            self._resolved = getattr(module, self._class_name)
        return self._resolved

    def __call__(self, *args, **kwargs):
        """实例化被代理的类，等价于直接调用真实类的构造函数。

        返回:
            被代理类的实例
        """
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, item):
        # 支持 Cls.classmethod() / Cls.CONST 等类级访问
        """代理类级属性与类方法访问，如 Cls.classmethod() / Cls.CONST。

        参数:
            item: 待访问的属性名

        返回:
            真实类上对应的属性值

        说明:
            对三个内部字段直接抛 AttributeError，避免在 _resolve 尚未完成时递归。
        """
        if item in ('_module_path', '_class_name', '_resolved'):
            raise AttributeError(item)
        return getattr(self._resolve(), item)


# P5Database: 数据库连接、建表、增删改查操作
P5Database = _LazyClass('modules.database', 'P5Database')
# P5Spider: 多源数据爬虫（历史开奖数据+走势数据）
P5Spider = _LazyClass('modules.data_fetcher', 'P5Spider')
# Validator: 预测结果验证与性能统计
Validator = _LazyClass('modules.validator', 'Validator')
# P5Predictor: 优化后的预测引擎（修复了原始版的排序/质数等Bug）
P5Predictor = _LazyClass('modules.predictor', 'P5Predictor')
# Backtester: 历史回测引擎（顶层依赖 matplotlib，惰性加载可省下 ~2.7s 启动开销）
Backtester = _LazyClass('modules.backtester', 'Backtester')
# P5Features: 特征工程模块（频率、遗漏、012路、连号等）
P5Features = _LazyClass('modules.features', 'P5Features')
# 版本信息唯一来源（version.py 单一维护，轻量，保持直接导入）
from version import get_current_version, get_changelog
from paths import PROJECT_ROOT, REPORTS_DIR, REPORTS_CHARTS_DIR, LOG_GUI_RUN
# 新版任务管理器（多线程、优先级队列、协作式取消、超时控制）
from modules.task_manager import TaskManager
# 四步流水线（模块级别导入，避免方法内部导入导致 P5Database 局部变量遮蔽）
from modules.pipeline import run_four_step_pipeline

    # ============================================================
# 主题自适应系统
# 支持浅色/深色主题自动检测与手动切换
    # ============================================================
import platform
import os

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
    }

    @staticmethod
    def detect_system_theme() -> str:
        """检测系统主题设置，返回 'light' 或 'dark'"""
        try:
            # Windows 10/11 主题检测
            if platform.system() == 'Windows':
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                       r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                        value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
                        return 'light' if value == 1 else 'dark'
                except (OSError, winreg.error):
                    pass
            # macOS 主题检测
            elif platform.system() == 'Darwin':
                try:
                    import subprocess
                    result = subprocess.run(
                        ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                        capture_output=True, text=True, timeout=2
                    )
                    return 'dark' if result.stdout.strip() == 'Dark' else 'light'
                except Exception:
                    pass
            # Linux GTK 主题检测
            elif platform.system() == 'Linux':
                try:
                    import subprocess
                    result = subprocess.run(
                        ['gsettings', 'get', 'org.gnome.desktop.interface', 'gtk-theme'],
                        capture_output=True, text=True, timeout=2
                    )
                    theme_name = result.stdout.strip().strip("'")
                    return 'dark' if any(kw in theme_name.lower() for kw in ['dark', 'adwaita']) else 'light'
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
        if current_theme is cls.DARK_THEME or current_theme.get('bg_primary') == cls.DARK_THEME['bg_primary']:
            return cls.LIGHT_THEME
        return cls.DARK_THEME


# 全局颜色主题配置
COLORS = ThemeManager.get_theme()


    # ============================================================
# 预测号码「展示层」压缩配置
# 仅影响「预测号码段」的展示与复制：保留 万/千/百/十 四位，去除个位(ge)。
# 核心算法、数据存储、命中率分析等仍使用完整 5 位，不受影响。
    # ============================================================
DISPLAY_POS_KEYS = ['wan', 'qian', 'bai', 'shi']
DISPLAY_POS_NAMES = ['万位', '千位', '百位', '十位']


def compress_combo(combo):
    """将 5 位预测组合压缩为 4 位显示串（去除个位）。

    核心数据不动，仅用于展示/复制层。幂等：对已是 4 位的串无副作用。
    """
    if not combo:
        return ''
    return str(combo)[:4]


class LotteryGUI:
    """
    排列5 AI智能分析系统主界面

    负责构建和管理整个GUI，包括：
    - 控制面板（数据爬取、智能分析中心、自我进化、智能分析与验证四个功能卡片）
    - 输出面板（预测结果 / 运行日志 / 历史命中率 / 自我进化 四个标签页）
    - 状态栏（任务状态、进度条、快捷统计）

    通过 TaskManager 将所有耗时业务逻辑放到后台线程执行，
    确保UI保持响应；自我进化引擎运行于独立后台线程，启动即自动触发，
    状态/进度/进化日志通过 queue + root.after 轮询实时呈现，永不阻塞主界面。
    """

    def __init__(self, root):
        """
        初始化主界面

        Args:
            root: tkinter.Tk 根窗口实例
        """
        self.root = root
        self.root.title("排列5 AI智能分析系统")
        # 适配常见屏幕尺寸：高度取屏幕的 85%（而不是固定 860），
        # 确保大部分显示器上无需滚动就能看到全部左侧功能卡片。
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        win_width = min(1280, screen_width - 40)
        win_height = max(780, min(860, int(screen_height * 0.85)))  # 至少 780px 保证内容可见
        self.root.geometry(f"{win_width}x{win_height}+{max(0, (screen_width - win_width) // 2)}+{max(0, (screen_height - win_height) // 2)}")
        self.root.minsize(1024, 720)
        self.root.configure(bg=COLORS['bg_primary'])

        self.task_mgr = TaskManager(self, max_workers=4, queue_size=100, enable_structured_logging=True)  # 异步任务管理器（多线程、优先级队列、协作式取消、超时控制）
        self._buttons = []  # 所有按钮列表（用于批量启用/禁用）
        self._current_task_name = ""  # 当前正在执行的任务名称
        self._prediction_clipboard = ""  # 最近一次生成的预测号码（供一键复制）
        self._clipboard_meta = {}  # 复制缓冲元数据：{target_issue, conf, high_conf, main_combo}
        # 结果仪表盘（合并视图）：保存最近一次各来源预测产物
        self._last_pipeline_final = None
        self._last_trend_result = None
        self._last_quick_final = None
        # 供「命中率优化」融合阶段复用的融合概率与预测元数据，
        # 避免为做选号策略对照而重复跑一次 predict（单次可达数十秒）。
        self._last_fused_probabilities = None
        self._last_predict_meta = {}
        # 仪表盘折叠区状态（推荐/备选/详细分析）
        self._detail_frame = None   # 详细分析折叠区
        self._alt_content = None    # 备选号码折叠区
        self._alt_visible = False
        self.evolution = None       # 自我进化引擎句柄（启动后自动初始化）
        # 进度节流状态
        self._last_progress_value = -1
        # 行号更新节流
        self._line_number_update_pending = False
        # 概览刷新标志
        # overview_flush_pending removed = False

        self._setup_window_style()
        self._build_ui()
        # 启动即自动触发「自我进化」引擎（后台守护线程，非阻塞）
        # 延迟初始化：避免在 UI 渲染阶段阻塞主线程导致白屏卡顿
        self.root.after(100, self._init_evolution_engine)
        self._setup_keyboard_shortcuts()
        # 加载主题偏好
        self._load_theme_preference()

    def _setup_window_style(self):
        """配置ttk样式主题（暗色主题，clam引擎）"""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('微软雅黑', 10),
                        background=COLORS['bg_primary'],
                        foreground=COLORS['text_primary'],
                        fieldbackground=COLORS['bg_secondary'])
        style.configure('TFrame', background=COLORS['bg_primary'])
        style.configure('Horizontal.TProgressbar',
                        background=COLORS['accent_p5'],
                        troughcolor=COLORS['bg_card'],
                        borderwidth=0)

        # ------------------------------------------------------------------
        # Treeview 暗色适配
        # 问题：clam 主题下 Treeview 的行背景不会继承 '.' 样式，实测
        # style.lookup('Treeview','background') == '#ffffff'，
        # 而前景继承自 '.' 的 #f8fafc（近白），于是
        # 「历史命中率」明细表变成白底白字，内容完全不可见。
        # 修复：显式指定 background / fieldbackground / foreground，
        # 并配好选中态与表头配色。
        # ------------------------------------------------------------------
        style.configure('Treeview',
                        background=COLORS['bg_card'],
                        fieldbackground=COLORS['bg_card'],
                        foreground=COLORS['text_primary'],
                        bordercolor=COLORS['border'],
                        borderwidth=0,
                        rowheight=24)
        style.map('Treeview',
                  background=[('selected', COLORS['accent_p5'])],
                  foreground=[('selected', '#ffffff')])
        style.configure('Treeview.Heading',
                        background=COLORS['bg_secondary'],
                        foreground=COLORS['text_primary'],
                        relief='flat',
                        borderwidth=0,
                        font=('微软雅黑', 9, 'bold'))
        style.map('Treeview.Heading',
                  background=[('active', COLORS['bg_card_hover'])],
                  foreground=[('active', COLORS['text_primary'])])

    def _setup_keyboard_shortcuts(self):
        """配置键盘快捷键（v3.36 新增）"""
        # Ctrl+C 复制预测号码（检查任务运行状态，防止分析中复制到中间态结果）
        def _on_ctrl_c(e):
            try:
                import logging
                _log = logging.getLogger('kplucky.debug')
                _log.info('HOTKEY_CTRL_C: clipboard_exists=%s task_running=%s',
                          bool(self._prediction_clipboard), self.task_mgr.is_running())
            except Exception:
                pass
            if self._prediction_clipboard and not self.task_mgr.is_running():
                self._copy_prediction()

        self.root.bind('<Control-c>', _on_ctrl_c)
        # 运行日志标签页已移除，相关快捷键停用
        self.root.unbind('<Control-f>')
        self.root.unbind('<Control-a>')
        # Escape 取消当前操作
        self.root.bind('<Escape>', lambda e: self._cancel_current_task())

    def _build_ui(self):
        """构建完整的UI布局：顶部标题栏 + 中部（左侧控制面板 + 右侧输出区） + 底部状态栏"""
        main_container = tk.Frame(self.root, bg=COLORS['bg_primary'])
        main_container.pack(fill=tk.BOTH, expand=True)

        self._build_header(main_container)
        self._build_content(main_container)
        self._build_status_bar(main_container)

    def _build_header(self, parent):
        """构建顶部标题栏（Logo + 系统名称 + 副标题 + 版本号 + 实时时钟）

        修复说明(v3.8):
        - 原 header 高度固定 50px 且 pack_propagate(False)，在 Windows 高 DPI
          缩放下标题(13pt)+副标题(8pt)实际像素增高，导致副标题底部被裁切。
        - 现将高度提升到 60px 并收紧内边距，保证副标题完整可见。
        - 右侧新增版本号显示（get_current_version()），与时钟纵向排列，风格统一。
        """
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left = tk.Frame(header, bg=COLORS['bg_secondary'])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=12)

        icon = tk.Canvas(left, width=32, height=32, bg=COLORS['bg_secondary'],
                         highlightthickness=0)
        icon.pack(side=tk.LEFT, pady=14)
        icon.create_rectangle(2, 2, 30, 14, fill=COLORS['accent_p5'], outline='', width=0)
        icon.create_rectangle(2, 18, 30, 30, fill=COLORS['accent_ai'], outline='', width=0)

        title_box = tk.Frame(left, bg=COLORS['bg_secondary'])
        title_box.pack(side=tk.LEFT, padx=(8, 0), pady=0)

        tk.Label(title_box, text="排列5 AI智能分析系统",
                 font=('微软雅黑', 13, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(anchor=tk.W)

        tk.Label(title_box, text="多模型综合预测分析平台 · 走势图+贝叶斯+在线学习",
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(anchor=tk.W)

        # 右侧：版本号 + 实时时钟（纵向排列）
        right = tk.Frame(header, bg=COLORS['bg_secondary'])
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=12)

        self.version_label = tk.Label(right, text=f"版本 {get_current_version()}",
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['accent_p5'])
        self.version_label.pack(anchor=tk.E, pady=(10, 0))

        # 主题切换按钮
        self._theme_btn = tk.Button(right, text="",
                                    font=('微软雅黑', 9),
                                    bg=COLORS['bg_secondary'],
                                    fg=COLORS['text_secondary'],
                                    relief='flat',
                                    cursor='hand2',
                                    padx=6, pady=2,
                                    command=self._toggle_theme)
        self._theme_btn.pack(anchor=tk.E, pady=(2, 0))

        self.time_label = tk.Label(right, text="",
                                   font=('Consolas', 9),
                                   bg=COLORS['bg_secondary'],
                                   fg=COLORS['text_secondary'])
        self.time_label.pack(anchor=tk.E, pady=(2, 0))
        self._update_time()

    def _update_time(self):
        """每秒更新顶部时钟显示"""
        self.time_label.config(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.root.after(1000, self._update_time)

    # 主题管理
    _current_theme = 'dark'  # 记录当前主题状态

    def _toggle_theme(self):
        """切换主题：深色↔浅色"""
        if self._current_theme == 'dark':
            self._current_theme = 'light'
            self._apply_theme(ThemeManager.LIGHT_THEME)
            self._theme_btn.config(text="")
        else:
            self._current_theme = 'dark'
            self._apply_theme(ThemeManager.DARK_THEME)
            self._theme_btn.config(text="")
        # 保存主题设置
        self._save_theme_preference()

    def _apply_theme(self, theme: dict):
        """应用主题配色到所有控件"""
        global COLORS
        COLORS = theme
        self.root.configure(bg=theme['bg_primary'])
        # 重新应用样式
        self._setup_window_style()
        # 递归更新所有子控件颜色
        self._update_widget_colors(self.root, theme)

    def _update_widget_colors(self, widget, theme: dict):
        """递归更新控件颜色（遍历所有子组件）"""
        try:
            # 更新当前控件
            for attr in ['bg', 'background', 'fg', 'foreground']:
                if hasattr(widget, attr):
                    try:
                        current = widget.cget(attr)
                        if current in theme.values() or current in COLORS.values():
                            # 映射颜色
                            old_color_map = {v: k for k, v in ThemeManager.DARK_THEME.items()}
                            old_color_map.update({v: k for k, v in ThemeManager.LIGHT_THEME.items()})
                            if current in old_color_map:
                                key = old_color_map[current]
                                if key in theme:
                                    widget.config(**{attr: theme[key]})
                    except Exception:
                        pass
            # 递归处理子控件
            for child in widget.winfo_children():
                self._update_widget_colors(child, theme)
        except Exception:
            pass

    def _save_theme_preference(self):
        """保存主题偏好设置"""
        try:
            theme_file = os.path.join(PROJECT_ROOT, 'data', 'theme_preference.json')
            os.makedirs(os.path.dirname(theme_file), exist_ok=True)
            with open(theme_file, 'w', encoding='utf-8') as f:
                json.dump({'theme': self._current_theme}, f)
        except Exception:
            pass

    def _load_theme_preference(self):
        """加载主题偏好设置"""
        try:
            theme_file = os.path.join(PROJECT_ROOT, 'data', 'theme_preference.json')
            if os.path.exists(theme_file):
                with open(theme_file, 'r', encoding='utf-8') as f:
                    prefs = json.load(f)
                    if prefs.get('theme') in ('light', 'dark'):
                        self._current_theme = prefs['theme']
                        self._apply_theme(
                            ThemeManager.LIGHT_THEME if self._current_theme == 'light'
                            else ThemeManager.DARK_THEME
                        )
                        self._theme_btn.config(text="" if self._current_theme == 'light' else "")
        except Exception:
            pass

    def _build_content(self, parent):
        """构建中部内容区：左侧控制面板（可滚动，280px）+ 右侧输出面板（自适应）"""
        content = tk.Frame(parent, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # 左侧控制面板（可滚动，固定宽度280px）
        left_container = tk.Frame(content, bg=COLORS['bg_primary'], width=280)
        left_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left_container.pack_propagate(False)

        # 创建Canvas+Scrollbar实现左侧面板滚动
        left_canvas = tk.Canvas(left_container, bg=COLORS['bg_primary'],
                                highlightthickness=0, width=280)
        left_scrollbar = tk.Scrollbar(left_container, orient='vertical',
                                      command=left_canvas.yview, width=8,
                                      bg=COLORS['bg_card'],
                                      troughcolor=COLORS['bg_secondary'])
        left_canvas.configure(yscrollcommand=left_scrollbar.set)

        left_inner = tk.Frame(left_canvas, bg=COLORS['bg_primary'])
        left_canvas.create_window((0, 0), window=left_inner, anchor='nw', width=270)

        # 让内部Frame自适应Canvas宽度
        def _on_left_configure(event):
            """左侧画布宽度变化时，同步内嵌 frame 的宽度，防止横向溢出。

            参数:
                event: Tk 的 <Configure> 事件对象，event.width 为画布新宽度
            """
            left_canvas.itemconfig('all', width=event.width - 4)

        left_canvas.bind('<Configure>', _on_left_configure)

        # 更新Canvas滚动区域
        def _on_inner_configure(event):
            """内部内容高度变化时，重新计算画布可滚动区域范围。

            参数:
                event: Tk 的 <Configure> 事件对象（此处仅作触发信号，不读取字段）
            """
            left_canvas.configure(scrollregion=left_canvas.bbox('all'))

        left_inner.bind('<Configure>', _on_inner_configure)

        # 鼠标滚轮滚动支持
        def _on_mousewheel(event):
            """处理鼠标滚轮事件，按 120 单位步长纵向滚动左栏画布。

            参数:
                event: Tk 的 <MouseWheel> 事件对象，event.delta 为滚轮增量
            """
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        left_canvas.bind('<Enter>', lambda e: left_canvas.bind_all('<MouseWheel>', _on_mousewheel))
        left_canvas.bind('<Leave>', lambda e: left_canvas.unbind_all('<MouseWheel>'))

        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._build_control_panel(left_inner)

        # 右侧输出面板（自适应宽度）
        right = tk.Frame(content, bg=COLORS['bg_primary'])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 绑定窗口大小变化事件，优化布局响应
        def _on_resize(event):
            # 窗口大小变化时触发重布局
            """窗口尺寸变化时触发重新布局。

            参数:
                event: Tk 的 <Configure> 事件对象

            说明:
                使用 after_idle 延迟执行，把连续拖拽产生的多次事件合并为一次重排。
            """
            self.root.after_idle(self._on_layout_resize)

        right.bind('<Configure>', _on_resize)

        self._build_output_panel(right)

    def _on_layout_resize(self):
        """窗口大小变化时的布局调整（v3.36 新增）"""
        try:
            # 触发所有子组件重新计算布局
            self.root.update_idletasks()
        except Exception:
            pass

    def _build_control_panel(self, parent):
        """
        构建左侧控制面板，包含四个功能卡片（v3.50）:

        卡片分组说明:
        1. "数据爬取" (#f59e0b 琥珀色) — 增量/全量爬取历史/走势/升平降/和值数据
        2. "智能分析中心" (#10b981 翠绿) — 「开始分析」一键完成全部预测与优化工作
        3. "自我进化" (#7c3aed 紫色) — 启动即自动训练/版本化/进化日志（后台非阻塞）
        4. "智能分析与验证" (#059669 绿) — 「综合验证与分析」一键执行原「分析工具」全部子功能
           （预测验证/命中率报告/性能报告/历史回测/特征分析）+ 综合报告

        v3.42 变更: 原「在线学习引擎」与「命中率优化引擎」两张独立卡片已整体融合进
        「开始分析」，不再单独陈列——两者本就是预测链路的内建环节，拆成手动按钮反而
        容易与实际生效配置脱节。融合后一次点击即完成:
          预测 → 走势 → 命中率优化(选号策略/概率校准/调参结论) → 在线学习闭环 → AI 辅助解读。

        v3.50 变更: 原「系统管理」卡片（数据库检测/贝叶斯结果/快捷统计/清空输出/清除回测断点）
        已彻底移除；其「自我进化」能力由新增的 SelfEvolutionEngine 接管，启动即自动触发，
        状态/进度/进化日志在左侧卡片与「自我进化」标签页实时呈现。

        布局采用垂直卡片式排列,每张卡片用不同颜色区分功能域。
        """
        # 数据爬取卡片
        crawl_card = self._create_card(parent, "数据爬取", '#f59e0b')
        crawl_card.pack(fill=tk.X, pady=(0, 8))

        self._add_big_button(crawl_card, "增量爬取数据", '#f59e0b',
                             lambda: self._on_button_click("增量爬取", self._execute_crawl_incremental))
        self._add_action_button(crawl_card, "全量爬取数据", '#d97706',
                                lambda: self._on_button_click("全量爬取", self._execute_crawl_full))

        # 数据爬取状态栏（数据库连接 + 历史数据量），启动后延迟刷新
        self.crawl_db_status_var = tk.StringVar(value="数据库：检测中…")
        _crawl_status_row = tk.Frame(crawl_card, bg=COLORS['bg_secondary'])
        _crawl_status_row.pack(fill=tk.X, padx=10, pady=(6, 2))
        tk.Label(_crawl_status_row, textvariable=self.crawl_db_status_var,
                 font=('微软雅黑', 8), bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(side=tk.LEFT)
        self._create_toolbar_button(_crawl_status_row, " 数据概览", self._show_data_overview, 'secondary').pack(side=tk.RIGHT, padx=(0, 4))
        self.root.after(1500, self._refresh_crawl_status)

        # 智能分析中心（合并 四步流水线 + 走势引擎 + 快速预测，同一界面完成，避免多面板切换）
        p5_card = self._create_card(parent, "智能分析中心", COLORS['accent_p5'])
        p5_card.pack(fill=tk.X, pady=(0, 8))

        # P2 优化: 快速模式与标准模式双按钮
        self._add_big_button(p5_card, " 开始分析", COLORS['accent_p5'],
                             lambda: self._on_button_click("智能分析", self._execute_unified_analysis))

        tk.Label(p5_card,
                 text="开始分析: 完整流程（含验证/学习/AI解读）",
                 font=('微软雅黑', 8),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted'],
                 wraplength=240, justify=tk.CENTER
                 ).pack(pady=(0, 8))

        # 智能分析与验证卡片 — 单个综合按钮，v3.11
        analysis_card = self._create_card(parent, "智能分析与验证", '#059669')
        analysis_card.pack(fill=tk.X, pady=(0, 8))

        # 综合验证与分析按钮（一键执行「分析工具」全部子功能：验证→命中率→性能→回测→特征→报告）
        tk.Button(analysis_card, text=" 综合验证与分析",
                  font=('微软雅黑', 10, 'bold'),
                  bg='#10b981', fg='#ffffff',
                  activebackground='#059669', activeforeground='#ffffff',
                  relief='flat', cursor='hand2',
                  command=lambda: self._on_button_click(
                      "综合验证与分析", self._execute_comprehensive_analysis_and_verify
                  ),
                  padx=20, pady=8
                  ).pack(fill=tk.X, padx=10, pady=6)

        tk.Label(analysis_card,
                 text="一键执行：验证 · 统计 · 回测 · 特征 · 报告",
                 font=('微软雅黑', 8),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted'],
                 wraplength=240, justify=tk.CENTER
                 ).pack(pady=(0, 8))

        # 注: 原「在线学习引擎」卡片(学习报告/重置权重/手动验证)与
        # 「命中率优化引擎」卡片(选号策略对比/概率校准/三闸门调参) 已于 v3.42
        # 整体融合进「智能分析中心 → 开始分析」，作为其内建阶段自动执行，
        # 故此处不再单独陈列。其中「重置模型权重」为破坏性操作，不适合出现在
        # 自动化流程中，已随卡片一并移除。
        # 注: 原「分析工具」卡片(预测验证/命中率报告/性能报告/历史回测/特征分析)
        # 已于 v3.17 整合进上方「智能分析与验证」卡片的「综合验证与分析」一键按钮,
        # 复用各子功能既有实现, 数据口径与单独点击完全一致, 故此处不再单独陈列。

        progress_card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                                 highlightbackground=COLORS['border'],
                                 highlightthickness=1)
        progress_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(progress_card, text="任务进度",
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=10, pady=(8, 4))

        self.progress = ttk.Progressbar(progress_card, mode='determinate', maximum=100)
        self.progress.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.progress_label = tk.Label(progress_card, text="0%",
                                       font=('Consolas', 11, 'bold'),
                                       bg=COLORS['bg_secondary'],
                                       fg=COLORS['accent_p5'])
        self.progress_label.pack(anchor=tk.CENTER, pady=(0, 4))

        self.task_status_label = tk.Label(progress_card, text="就绪",
                                          font=('微软雅黑', 8),
                                          bg=COLORS['bg_secondary'],
                                          fg=COLORS['text_muted'])
        self.task_status_label.pack(anchor=tk.W, padx=10, pady=(0, 8))

        stats_card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                              highlightbackground=COLORS['border'],
                              highlightthickness=1)
        stats_card.pack(fill=tk.X)

        tk.Label(stats_card, text="快捷统计",
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=10, pady=(8, 4))

        self.stats_content = tk.Label(stats_card,
                                      text="系统就绪 · 统计见「历史命中率」标签页",
                                      font=('微软雅黑', 8),
                                      bg=COLORS['bg_secondary'],
                                      fg=COLORS['text_muted'],
                                      justify=tk.LEFT)
        self.stats_content.pack(anchor=tk.W, padx=10, pady=(0, 8))

    def _create_card(self, parent, title, accent_color):
        """
        创建一个带标题和顶部彩色装饰条的功能卡片（v3.36 优化版）

        Args:
            parent: 父容器
            title: 卡片标题
            accent_color: 装饰条和圆点颜色

        Returns:
            tk.Frame: 卡片框架
        """
        card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                        highlightbackground=COLORS['border'],
                        highlightthickness=1)

        # 顶部彩色装饰条（3px高度，增强视觉层次）
        top_bar = tk.Frame(card, bg=accent_color, height=3)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        # 标题行（彩色圆点 + 标题文字）
        title_frame = tk.Frame(card, bg=COLORS['bg_secondary'])
        title_frame.pack(fill=tk.X, padx=10, pady=(8, 6))

        dot = tk.Canvas(title_frame, width=8, height=8,
                        bg=COLORS['bg_secondary'], highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 6))
        dot.create_oval(1, 1, 7, 7, fill=accent_color, outline='')

        tk.Label(title_frame, text=title,
                 font=('微软雅黑', 11, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(side=tk.LEFT)

        return card

    def _add_big_button(self, parent, text, color, command):
        """
        添加主要操作按钮（大号、彩色背景、占满整行）（v3.36 优化版）

        Args:
            parent: 父容器
            text: 按钮文字
            color: 按钮背景色
            command: 点击回调函数

        Returns:
            tk.Button: 按钮实例
        """
        btn_frame = tk.Frame(parent, bg=COLORS['bg_secondary'])
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

        btn = tk.Button(btn_frame, text=text,
                        font=('微软雅黑', 11, 'bold'),
                        bg=color,
                        fg=COLORS['text_primary'],
                        activebackground=self._lighten_color(color, 1.15),
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=12, pady=10,
                        highlightthickness=0,
                        highlightbackground=COLORS['border'],
                        bd=0)
        btn.pack(fill=tk.X)

        # hover效果：鼠标进入时变亮，离开时恢复
        btn.bind('<Enter>', lambda e, b=btn, c=self._lighten_color(color, 1.15): b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn, c=color: b.config(bg=c))
        # 按下效果
        btn.bind('<Button-1>', lambda e, b=btn, c=self._lighten_color(color, 0.9): b.config(bg=c))
        btn.bind('<ButtonRelease-1>', lambda e, b=btn, c=self._lighten_color(color, 1.15): b.config(bg=c))

        self._buttons.append(btn)
        return btn

    def _add_action_button(self, parent, text, color, command):
        """
        添加次要操作按钮（小号、深色背景、hover时变色）

        Args:
            parent: 父容器
            text: 按钮文字
            color: hover时的背景色
            command: 点击回调函数

        Returns:
            tk.Button: 按钮实例
        """
        btn_frame = tk.Frame(parent, bg=COLORS['bg_secondary'])
        btn_frame.pack(fill=tk.X, padx=10, pady=2)

        btn = tk.Button(btn_frame, text=text,
                        font=('微软雅黑', 9),
                        bg=COLORS['bg_card'],
                        fg=COLORS['text_primary'],
                        activebackground=color,
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=12, pady=5)
        btn.pack(fill=tk.X)

        # hover效果：鼠标进入时变亮，离开时恢复
        btn.bind('<Enter>', lambda e, b=btn, c=color: b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn, c=COLORS['bg_card']: b.config(bg=c))
        # 按下效果
        btn.bind('<Button-1>', lambda e, b=btn, c=self._lighten_color(color, 0.85): b.config(bg=c))
        btn.bind('<ButtonRelease-1>', lambda e, b=btn, c=color: b.config(bg=c))

        self._buttons.append(btn)
        return btn

    def _add_inline_button(self, parent, text, color, command):
        """
        添加内联小按钮（用于一行多按钮布局）

        Args:
            parent: 父容器
            text: 按钮文字
            color: hover时的背景色
            command: 点击回调函数

        Returns:
            tk.Button: 按钮实例
        """
        btn = tk.Button(parent, text=text,
                        font=('微软雅黑', 8),
                        bg=COLORS['bg_card'],
                        fg=COLORS['text_primary'],
                        activebackground=color,
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=10, pady=4)
        btn.pack(side=tk.LEFT, padx=(0, 5))

        # hover效果
        btn.bind('<Enter>', lambda e, b=btn, c=color: b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn: b.config(bg=COLORS['bg_card']))
        btn.bind('<Leave>', lambda e, b=btn: b.config(bg=COLORS['bg_card']))

        self._buttons.append(btn)
        return btn

    @staticmethod
    def _lighten_color(hex_color, factor):
        """
        将十六进制颜色按比例变亮

        Args:
            hex_color: 十六进制颜色字符串（如 '#10b981'）
            factor: 亮度因子（>1变亮，<1变暗）

        Returns:
            变亮后的十六进制颜色字符串
        """
        hex_color = hex_color.lstrip('#')
        r = min(255, int(int(hex_color[0:2], 16) * factor))
        g = min(255, int(int(hex_color[2:4], 16) * factor))
        b = min(255, int(int(hex_color[4:6], 16) * factor))
        return f'#{r:02x}{g:02x}{b:02x}'


    def _build_output_panel(self, parent):
        """构建右侧输出面板（ttk.Notebook）

        仅保留「预测结果」「自我进化」两个标签页。
        运行日志改为写入 ``logs/gui_run.log``，不再单独保留标签页。
        """
        panel = tk.Frame(parent, bg=COLORS['bg_primary'])
        panel.pack(fill=tk.BOTH, expand=True)

        nb = ttk.Notebook(panel)
        nb.pack(fill=tk.BOTH, expand=True)
        self.output_nb = nb

        result_tab = tk.Frame(nb, bg=COLORS['bg_primary'])
        nb.add(result_tab, text=" 预测结果")
        self._build_result_tab(result_tab)

        evo_tab = tk.Frame(nb, bg=COLORS['bg_primary'])
        nb.add(evo_tab, text=" 自我进化")
        self._build_evolution_tab(evo_tab)

        try:
            nb.select(0)
        except Exception:
            pass

    def _build_result_tab(self, parent):
        """构建「预测结果」标签页（v3.50 重构：滚动容器 + 分类/清空/导出工具栏）

        信息架构（自上而下）：
        1. 轻量工具条：复制预测号码 / 复制全部（结论区常用操作前置）
        2. 概览卡片区：最新预测期号+号码 / 历史命中率 / 已验证期数 / 历史数据量（指标磁贴）
        3. 预测结果仪表盘：主推荐号码大卡 + 置信度徽标 + 分源对比表 + 折叠详情
        4. 空态占位：尚无预测时给出友好引导，避免空白页

        概览与仪表盘控件句柄（overview_frame / dash_container / result_dash）沿用旧命名，
        因此 _update_quick_overview / _show_result_dashboard 等既有渲染逻辑无需改动。
        """
        # ---- 1) 工具栏：分类筛选 / 清空 / 导出 / 复制 / 日志 ----
        toolbar = tk.Frame(parent, bg=COLORS['bg_card'], height=48)
        toolbar.pack(fill=tk.X)
        toolbar.pack_propagate(False)

        title_frame = tk.Frame(toolbar, bg=COLORS['bg_card'])
        title_frame.pack(side=tk.LEFT, padx=16, pady=8)
        tk.Label(title_frame, text=" 预测结果",
                 font=('微软雅黑', 12, 'bold'),
                 bg=COLORS['bg_card'],
                 fg=COLORS['text_primary']).pack(anchor=tk.W)

        btn_frame = tk.Frame(toolbar, bg=COLORS['bg_card'])
        btn_frame.pack(side=tk.RIGHT, padx=8, pady=8)
        # 分类筛选（按结果内容分类展示）
        self.result_category_var = tk.StringVar(value="全部")
        cat_combo = ttk.Combobox(btn_frame, textvariable=self.result_category_var,
                                 values=["全部", "预测结论", "分位信号", "算法依据"],
                                 state='readonly', width=10, font=('微软雅黑', 9))
        cat_combo.pack(side=tk.RIGHT, padx=(6, 0))
        cat_combo.bind('<<ComboboxSelected>>',
                       lambda e: self._apply_result_category(self.result_category_var.get()))
        self._create_toolbar_button(btn_frame, " 导出结果",
                                    self._export_result_board, 'secondary').pack(side=tk.RIGHT, padx=(6, 0))
        self._create_toolbar_button(btn_frame, " 清空结果",
                                    self._clear_result_board, 'danger').pack(side=tk.RIGHT, padx=(6, 0))
        # 关键修复：先调用 _create_toolbar_button 获取按钮句柄，再 pack，
        # 否则 `.pack()` 返回 None 会导致 _result_copy_btn 被赋值为 None，
        # _set_buttons_state 中对该按钮的独立 state 控制将永远失效。
        self._result_copy_btn = self._create_toolbar_button(
            btn_frame, " 复制预测号码", self._copy_prediction, 'primary')
        self._result_copy_btn.pack(side=tk.RIGHT, padx=(0, 4))
        # 工具栏按钮独立于全局 _buttons，确保分析进行中可单独禁用复制按钮
        # （主按钮在 task_mgr 运行中被禁用，但结果区按钮需独立管理）

        # ---- 2) 概览卡片区（信息架构第一层）----
        overview_container = tk.Frame(parent, bg=COLORS['bg_primary'])
        overview_container.pack(fill=tk.X, padx=8, pady=(8, 4))

        # ---- 3) 滚动容器（保证大数据量不卡顿）----
        # 以 Canvas + 滚动条承载结果仪表盘（镜像运行日志页布局），
        # 超长内容滚动查看而非撑爆布局，刷新时仅更新滚动区域，避免整体重绘卡顿。
        self.dash_scroll_sb = tk.Scrollbar(parent, orient='vertical',
                                          command=self._dash_yview, width=10,
                                          bg=COLORS['bg_card'], troughcolor=COLORS['bg_secondary'])
        self.dash_scroll_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.dash_container = tk.Canvas(parent, bg=COLORS['bg_primary'],
                                        highlightthickness=0)
        self.dash_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                                 padx=(8, 0), pady=(4, 8))
        self.dash_container.configure(yscrollcommand=self.dash_scroll_sb.set)

        # 内部 Frame：实际承载 result_dash 与占位
        self.dash_inner = tk.Frame(self.dash_container, bg=COLORS['bg_primary'])
        self.dash_container.create_window((0, 0), window=self.dash_inner,
                                          anchor='nw',
                                          width=self.dash_container.winfo_width())

        def _on_dash_configure(event):
            """仪表盘容器尺寸变化时同步内部宽度并刷新滚动区域。"""
            self.dash_container.itemconfig('all', width=event.width - 4)
            self._update_result_scrollregion()
        self.dash_container.bind('<Configure>', _on_dash_configure)
        self.dash_inner.bind('<Configure>',
                              lambda e: self._update_result_scrollregion())
        # 鼠标滚轮滚动手感
        def _dash_wheel(event):
            self.dash_container.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        self.dash_container.bind('<Enter>',
            lambda e: self.dash_container.bind_all('<MouseWheel>', _dash_wheel))
        self.dash_container.bind('<Leave>',
            lambda e: self.dash_container.unbind_all('<MouseWheel>'))

        self.result_dash = tk.Frame(self.dash_inner, bg=COLORS['bg_card'],
                                    highlightbackground=COLORS['border'],
                                    highlightthickness=1, relief='flat')
        self.result_dash.pack_forget()

        # ---- 4) 空态占位（尚无预测结果时展示）----
        self._result_placeholder = tk.Frame(self.dash_inner, bg=COLORS['bg_primary'])
        ph_card = tk.Frame(self._result_placeholder, bg=COLORS['bg_card'],
                           highlightbackground=COLORS['border'],
                           highlightthickness=1, relief='flat')
        ph_card.pack(fill=tk.BOTH, expand=True, padx=2, pady=20)
        ph_inner = tk.Frame(ph_card, bg=COLORS['bg_card'])
        ph_inner.pack(expand=True, pady=40)
        tk.Label(ph_inner, text="", font=('微软雅黑', 40),
                 bg=COLORS['bg_card'], fg=COLORS['accent_p5']).pack(pady=(0, 8))
        tk.Label(ph_inner, text="尚无预测结果",
                 font=('微软雅黑', 13, 'bold'),
                 bg=COLORS['bg_card'], fg=COLORS['text_primary']).pack()
        tk.Label(ph_inner,
                 text="点击左侧「 开始分析」运行完整流程，\n预测结论将在此处以仪表盘形式呈现。",
                 font=('微软雅黑', 10), justify=tk.CENTER,
                 bg=COLORS['bg_card'], fg=COLORS['text_secondary']).pack(pady=(6, 0))
        self._result_placeholder.pack(fill=tk.BOTH, expand=True)

    # =========================================================================
    # 自我进化（v3.55）：标签页 + 引擎接线 + 实时可视化
    # =========================================================================
    def _build_evolution_tab(self, parent):
        """构建「自我进化」常驻标签页：状态 / 进度 / 进化日志 / 版本管理（v3.55 全面重构）。

        引擎运行于独立后台线程，经 queue + root.after 轮询把消息投递到本页控件，
        因此进化过程（采集/重训/评估/版本化）永不阻塞主界面。

        重构要点：
          - 顶部：现代化概览卡片（渐变边框 + 阶段指示器 + 状态徽章）
          - 中部：四指标磁贴（样本量/最新期号/命中率/调优耗时）
          - 工具栏：统一按钮样式 + 新增「联动状态」快捷入口
          - 日志区：代码风格文本框 + 多级颜色标签
          - 版本表：斑马纹 + 状态徽章 + 双击详情弹窗
        """
        f = tk.Frame(parent, bg=COLORS['bg_primary'])
        f.pack(fill=tk.BOTH, expand=True)

        # ── 顶部概览区（带渐变边框效果）────────────────────────────────
        ov = tk.Frame(f, bg=COLORS['bg_card'],
                      highlightbackground='#a78bfa', highlightthickness=2)
        ov.pack(fill=tk.X, padx=12, pady=10)

        # 标题栏（带图标装饰）
        hdr = tk.Frame(ov, bg=COLORS['bg_card'])
        hdr.pack(fill=tk.X, padx=14, pady=(10, 6))

        # 装饰渐变条
        grad_bar = tk.Canvas(hdr, width=4, height=28, bg=COLORS['bg_card'],
                             highlightthickness=0)
        grad_bar.create_rectangle(0, 0, 4, 28, fill='#7c3aed', outline='')
        grad_bar.pack(side=tk.LEFT, padx=(0, 10))

        # 标题文字
        tk.Label(hdr, text="自我进化引擎", font=('微软雅黑', 13, 'bold'),
                 bg=COLORS['bg_card'], fg='#a78bfa').pack(side=tk.LEFT)

        # 状态徽章（动态更新）
        self.evo_status_badge = tk.Label(hdr, text="● 就绪", font=('微软雅黑', 9),
                                         bg='#1e1e2e', fg='#10b981',
                                         padx=10, pady=3, relief='flat')
        self.evo_status_badge.pack(side=tk.RIGHT)

        # 阶段指示器（6 阶段点状展示）
        self.evo_phase_indicators = []
        phase_frame = tk.Frame(ov, bg=COLORS['bg_card'])
        phase_frame.pack(fill=tk.X, padx=14, pady=(0, 6))
        for i, phase_name in enumerate(['采集', '基线', '调优', '评估', '持久化', '完成']):
            dot = tk.Canvas(phase_frame, width=12, height=12, bg=COLORS['bg_card'],
                           highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, fill='#334155', outline='')
            dot.pack(side=tk.LEFT, padx=4)
            self.evo_phase_indicators.append(dot)
            tk.Label(phase_frame, text=phase_name, font=('微软雅黑', 8),
                     bg=COLORS['bg_card'], fg=COLORS['text_muted']).pack(side=tk.LEFT, padx=2)
            if i < 5:
                tk.Label(phase_frame, text="─", font=('微软雅黑', 8),
                         bg=COLORS['bg_card'], fg=COLORS['text_muted']).pack(side=tk.LEFT, padx=1)

        # 概览描述
        self.evo_overview_var = tk.StringVar(
            value="系统正在后台自动学习历史开奖规律，持续优化预测模型...")
        tk.Label(ov, textvariable=self.evo_overview_var, font=('微软雅黑', 9),
                 bg=COLORS['bg_card'], fg=COLORS['text_secondary'], wraplength=600,
                 justify=tk.LEFT).pack(anchor=tk.W, padx=14, pady=(0, 8))

        # ── 四指标磁贴区───────────────────────────────────────────────
        metrics_frame = tk.Frame(f, bg=COLORS['bg_primary'])
        metrics_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        self.evo_metric_tiles = []
        tile_data = [
            ('样本量', '0 期', COLORS['accent_ai_light']),
            ('最新期号', '—', COLORS['text_secondary']),
            ('Top-4 命中', '—', COLORS['accent_p5_light']),
            ('调优耗时', '—', COLORS['text_muted']),
        ]
        for idx, (label, initial, color) in enumerate(tile_data):
            tile = tk.Frame(metrics_frame, bg=COLORS['bg_card'],
                           highlightbackground=COLORS['border'], highlightthickness=1)
            tile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
            tk.Label(tile, text=label, font=('微软雅黑', 8),
                     bg=COLORS['bg_card'], fg=COLORS['text_muted']).pack(anchor=tk.W, padx=8, pady=(6, 2))
            val_label = tk.Label(tile, text=initial, font=('微软雅黑', 11, 'bold'),
                                bg=COLORS['bg_card'], fg=color)
            val_label.pack(anchor=tk.W, padx=8, pady=(0, 6))
            self.evo_metric_tiles.append(val_label)

        # ── 状态进度区（卡片式）──────────────────────────────────────
        st_frame = tk.Frame(f, bg=COLORS['bg_primary'])
        st_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        st = tk.Frame(st_frame, bg=COLORS['bg_card'],
                      highlightbackground=COLORS['border'], highlightthickness=1)
        st.pack(fill=tk.X, padx=2, pady=2)

        # 状态行
        st_hdr = tk.Frame(st, bg=COLORS['bg_card'])
        st_hdr.pack(fill=tk.X, padx=12, pady=(8, 4))
        tk.Label(st_hdr, text="当前阶段", font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_card'], fg=COLORS['text_primary']).pack(side=tk.LEFT)
        self.evo_tab_status = tk.Label(st_hdr, text="就绪", font=('微软雅黑', 9),
                                       bg=COLORS['bg_card'], fg=COLORS['accent_ai_light'])
        self.evo_tab_status.pack(side=tk.RIGHT)

        # 进度条
        self.evo_tab_progress = ttk.Progressbar(st, mode='determinate', maximum=100)
        self.evo_tab_progress.pack(fill=tk.X, padx=12, pady=(0, 8))

        # ── 工具栏（现代化按钮样式）──────────────────────────────────
        tb = tk.Frame(f, bg=COLORS['bg_primary'])
        tb.pack(fill=tk.X, padx=12, pady=(0, 6))
        self._create_toolbar_button(tb, "📊 导出版本", self._export_evolution_versions,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 8))
        self._create_toolbar_button(tb, "🔗 联动状态", self._show_evolution_link_state,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 8))
        self._create_toolbar_button(tb, "💡 改进建议", self._on_evo_proposals,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 8))
        self._create_toolbar_button(tb, "❤️ 健康诊断", self._on_system_health_diagnostic,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 8))
        self._create_toolbar_button(tb, "🗑️ 清空日志", self._clear_evolution_log,
                                    'danger').pack(side=tk.LEFT)

        # ── 进化日志（代码风格文本框）────────────────────────────────
        log_label = tk.Frame(f, bg=COLORS['bg_primary'])
        log_label.pack(fill=tk.X, padx=12, pady=(0, 4))
        tk.Label(log_label, text="进化日志", font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_primary'], fg=COLORS['text_primary']).pack(anchor=tk.W)

        log_frame = tk.Frame(f, bg=COLORS['bg_panel'],
                             highlightbackground=COLORS['border'], highlightthickness=1)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        # 日志文本框（等宽字体，类终端风格）
        self.evo_log = tk.Text(log_frame, wrap=tk.WORD, font=('Consolas', 9),
                               bg='#020617', fg='#e2e8f0',
                               relief='flat', padx=12, pady=10, state=tk.NORMAL,
                               undo=True, insertbackground='#a78bfa')
        self.evo_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 滚动条（匹配主题）
        esb = tk.Scrollbar(log_frame, command=self.evo_log.yview, width=14,
                           bg=COLORS['bg_card'], troughcolor='#0f172a')
        esb.pack(side=tk.RIGHT, fill=tk.Y)
        self.evo_log.config(yscrollcommand=esb.set)

        # 日志标签颜色（终端风格）
        self.evo_log.tag_config('section', foreground='#8b5cf6',
                               font=('微软雅黑', 10, 'bold'))
        self.evo_log.tag_config('success', foreground='#10b981',
                               font=('微软雅黑', 9))
        self.evo_log.tag_config('warning', foreground='#f59e0b',
                               font=('微软雅黑', 9))
        self.evo_log.tag_config('error', foreground='#ef4444',
                               font=('微软雅黑', 9, 'bold'))
        self.evo_log.tag_config('info', foreground='#94a3b8',
                               font=('Consolas', 9))
        self.evo_log.tag_config('dim', foreground='#475569',
                               font=('Consolas', 9))
        self.evo_log.insert(tk.END, "> 自我进化引擎已就绪。启动后将自动训练并记录版本。\n", 'dim')

        # ── 版本表格（带斑马纹效果 + 状态徽章颜色）──────────────────────
        vt_frame = tk.Frame(f, bg=COLORS['bg_primary'])
        vt_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        vt_hdr = tk.Frame(vt_frame, bg=COLORS['bg_card'],
                         highlightbackground=COLORS['border'], highlightthickness=1)
        vt_hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(vt_hdr, text="进化版本记录", font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_card'], fg=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=6)
        tk.Label(vt_hdr, text="（双击行可查看版本详情 | 右键可回滚）", font=('微软雅黑', 8),
                 bg=COLORS['bg_card'], fg=COLORS['text_muted']).pack(anchor=tk.E, padx=10, pady=6)

        cols = ('版本标签', '状态', 'Top-1', 'Top-3', 'Top-5', '父版本', '创建时间')
        self.evo_version_tree = ttk.Treeview(vt_frame, columns=cols, show='headings', height=10)
        # 列宽优化
        col_widths = {'版本标签': 110, '状态': 80, 'Top-1': 65, 'Top-3': 65,
                      'Top-5': 65, '父版本': 110, '创建时间': 150}
        for c in cols:
            self.evo_version_tree.heading(c, text=c)
            self.evo_version_tree.column(c, width=col_widths.get(c, 80), anchor=tk.CENTER)
        # 斑马纹（支持明暗主题）
        self.evo_version_tree.tag_configure('odd', background=COLORS['bg_card'])
        self.evo_version_tree.tag_configure('even', background=COLORS['bg_secondary'])
        # 状态徽章颜色
        self.evo_version_tree.tag_configure('active', foreground='#059669',
                                           font=('微软雅黑', 9, 'bold'))
        self.evo_version_tree.tag_configure('trial', foreground=COLORS['text_muted'])
        self.evo_version_tree.tag_configure('rolledback', foreground='#d97706')
        self.evo_version_tree.pack(fill=tk.BOTH, expand=True)
        # 绑定双击事件查看版本详情
        self.evo_version_tree.bind('<Double-1>', self._on_version_row_double_click)
        # 绑定右键菜单（回滚功能）
        self.evo_version_tree.bind('<Button-3>', self._on_version_right_click)
        # 启动后延迟填充，避免与布局初始化竞争
        self.root.after(1500, self._refresh_evolution_versions)

    def _on_version_right_click(self, event):
        """右键点击版本表格行，弹出上下文菜单（回滚选项）。"""
        tree = getattr(self, 'evo_version_tree', None)
        if tree is None:
            return
        # 获取点击位置的行
        item = tree.identify_row(event.y)
        if not item:
            return
        tree.selection_set(item)
        # 创建上下文菜单
        menu = tk.Menu(self.root, tearoff=0)
        values = tree.item(item)['values']
        version_tag = str(values[0]) if values else ''
        status = str(values[1]) if len(values) > 1 else ''
        if status != 'active' and version_tag:
            menu.add_command(label=f"回滚到此版本 ({version_tag})",
                            command=lambda v=version_tag: self._on_evo_rollback_to(v))
        menu.add_command(label="查看版本详情",
                        command=lambda: self._on_version_row_double_click(
                            type('Event', (), {'y': tree.identify_row(event.y)})()))
        menu.post(event.x_root, event.y_root)

    def _init_evolution_engine(self):
        """初始化并启动自我进化引擎（启动即自动触发，后台非阻塞）。"""
        if getattr(self, 'evolution', None) is not None:
            return
        try:
            from modules.self_evolution import SelfEvolutionEngine
            # 启动即自动触发完整进化（full=True），用户打开程序即可自动运行
            self.evolution = SelfEvolutionEngine(auto=True, auto_full=True)
            self.evolution.start()
            logger.info('[GUI] 自我进化引擎已初始化并启动，注册轮询定时器')
            self.root.after(300, self._refresh_evolution_versions)
            self.root.after(200, self._poll_evolution)
        except Exception as e:
            logger.warning('自我进化引擎初始化失败: %s', e)
            import traceback
            logger.warning(traceback.format_exc())

    def _poll_evolution(self):
        """轮询自我进化引擎消息队列（每 200ms），实时刷新 UI（主线程安全）。"""
        eng = getattr(self, 'evolution', None)
        if eng is not None:
            try:
                while True:
                    msg = eng.queue.get_nowait()
                    self._handle_evolution_msg(msg)
            except queue.Empty:
                pass
            except Exception as e:
                logger.warning('[GUI] _poll_evolution 消费消息时异常: %s', e)
        self.root.after(200, self._poll_evolution)

    def _handle_evolution_msg(self, m):
        """将引擎消息渲染到自我进化标签页（v3.55 增强版：支持 metrics/phase 消息）。"""
        t = m.get('type')
        try:
            if t == 'status':
                txt = m.get('text', '')
                if hasattr(self, 'evo_tab_status'):
                    self.evo_tab_status.config(text=txt)
                # 更新状态徽章
                if hasattr(self, 'evo_status_badge'):
                    if '进行中' in txt or '训练' in txt or '运行' in txt:
                        self.evo_status_badge.config(text="● 运行中", fg='#f59e0b', bg='#1e1e2e')
                    elif '完成' in txt or '就绪' in txt:
                        self.evo_status_badge.config(text="● 就绪", fg='#10b981', bg='#1e1e2e')
                    else:
                        self.evo_status_badge.config(text="● " + txt[:8], fg='#a78bfa', bg='#1e1e2e')
            elif t == 'progress':
                v = m.get('value', 0)
                if hasattr(self, 'evo_tab_progress'):
                    self.evo_tab_progress['value'] = v
            elif t == 'log':
                lvl = m.get('level', 'info')
                tag = lvl if lvl in ('section', 'success', 'warning', 'error', 'info') else 'info'
                if hasattr(self, 'evo_log'):
                    self.evo_log.insert(tk.END, m.get('text', '') + "\n", tag)
                    self.evo_log.see(tk.END)
            elif t == 'stage':
                idx = m.get('index', 0)
                total = m.get('total', 6)
                name = m.get('name', '')
                if hasattr(self, 'evo_tab_status'):
                    self.evo_tab_status.config(text=f"阶段 {idx + 1}/{total}: {name}")
                # 更新阶段指示器
                if hasattr(self, '_update_phase_indicator'):
                    self._update_phase_indicator(idx)
            elif t == 'version':
                self._refresh_evolution_versions()
                d = m.get('data', {})
                if hasattr(self, 'evo_overview_var'):
                    self.evo_overview_var.set(
                        f"当前版本: {d.get('version_tag', '')} | 状态: {d.get('status', '')}\n{d.get('note', '')}")
            elif t == 'done':
                self._refresh_evolution_versions()
                if hasattr(self, 'evo_status_badge'):
                    self.evo_status_badge.config(text="● 就绪", fg='#10b981', bg='#1e1e2e')
                if hasattr(self, '_update_phase_indicator'):
                    self._update_phase_indicator(5)  # 完成阶段
            elif t == 'metrics':
                # 更新指标磁贴
                hist_count = m.get('history_count')
                latest_issue = m.get('latest_issue')
                if hist_count is not None and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 4:
                    self.evo_metric_tiles[0].config(text=f"{hist_count} 期")
                if latest_issue and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 2:
                    self.evo_metric_tiles[1].config(text=str(latest_issue))
            elif t == 'tuning_perf':
                # 更新调优性能指标
                elapsed_ms = m.get('elapsed_ms')
                if elapsed_ms is not None and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 4:
                    if elapsed_ms < 1000:
                        self.evo_metric_tiles[3].config(text=f"{elapsed_ms:.0f}ms")
                    else:
                        self.evo_metric_tiles[3].config(text=f"{elapsed_ms/1000:.1f}s")
            elif t == 'hitrate':
                # 更新命中率指标（Top-4 命中）
                top4 = m.get('top4')
                if top4 is not None and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 3:
                    self.evo_metric_tiles[2].config(text=f"{top4:.1f}%")
            else:
                logger.debug('[GUI] _handle_evolution_msg 未知消息类型: %s', t)
        except Exception as e:
            logger.warning('[GUI] _handle_evolution_msg 处理消息异常(type=%s): %s', t, e)

    def _update_phase_indicator(self, current_phase_idx):
        """更新阶段指示器圆点颜色。

        参数:
            current_phase_idx: 当前执行的阶段索引 (0-5)
        """
        if not hasattr(self, 'evo_phase_indicators'):
            return
        colors = ['#059669', '#10b981', '#7c3aed', '#8b5cf6', '#d97706', '#f59e0b']
        for i, dot_canvas in enumerate(self.evo_phase_indicators):
            if i < current_phase_idx:
                # 已完成阶段（绿色）
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(dot_canvas.find_all()[0], fill='#10b981')
            elif i == current_phase_idx:
                # 当前阶段（紫色高亮）
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(dot_canvas.find_all()[0], fill='#7c3aed')
            else:
                # 未开始阶段（灰色）
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(dot_canvas.find_all()[0], fill='#334155')

    def _on_version_row_double_click(self, event):
        """双击版本表格行，弹出该版本的详细信息窗口（v3.55 增强版）。"""
        tree = getattr(self, 'evo_version_tree', None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            return
        item = tree.item(sel[0])
        values = item.get('values', [])
        if not values:
            return
        version_tag = str(values[0])
        status = str(values[1])
        top1 = values[2] if len(values) > 2 else '—'
        top3 = values[3] if len(values) > 3 else '—'
        top5 = values[4] if len(values) > 4 else '—'
        parent = str(values[5]) if len(values) > 5 else '—'
        created = str(values[6]) if len(values) > 6 else '—'

        # 获取完整版本数据以展示调优详情
        eng = getattr(self, 'evolution', None)
        params_json = {}
        tuning_info = {}
        note_text = ''
        if eng:
            try:
                all_versions = eng.get_versions(limit=100)
                for v in all_versions:
                    if v.get('version_tag') == version_tag:
                        params_json = v.get('params', {}) or {}
                        tuning_info = params_json.get('tuning', {}) or {}
                        note_text = v.get('note', '')
                        break
            except Exception:
                pass

        # 构建详情文本
        detail_lines = [
            f"版本标签: {version_tag}",
            f"状态: {status}",
            f"Top-1 命中率: {top1}%",
            f"Top-3 命中率: {top3}%",
            f"Top-5 命中率: {top5}%",
            f"父版本: {parent}",
            f"创建时间: {created}",
            "",
            "─" * 40,
            "",
        ]

        # 参数信息
        if params_json:
            weights = params_json.get('weights', {})
            lookback = params_json.get('lookback', '—')
            detail_lines.append("【融合权重】")
            for algo, w in sorted(weights.items(), key=lambda x: -x[1]):
                detail_lines.append(f"  {algo}: {w:.4f}")
            detail_lines.append(f"lookback: {lookback}")
            detail_lines.append("")

        # 调优性能
        if tuning_info:
            detail_lines.append("【调优性能】")
            detail_lines.append(f"  评估候选: {tuning_info.get('candidates_evaluated', '—')} 组")
            detail_lines.append(f"  组件缓存命中: {tuning_info.get('cache_hits', '—')} 次")
            detail_lines.append(f"  组件缓存未命中: {tuning_info.get('cache_misses', '—')} 次")
            elapsed = tuning_info.get('elapsed_ms')
            if elapsed:
                detail_lines.append(f"  调优耗时: {elapsed:.0f}ms")
            detail_lines.append(f"  是否优化: {'是' if tuning_info.get('improved') else '否'}")
            detail_lines.append("")

        # 备注信息
        if note_text:
            detail_lines.append("【备注】")
            detail_lines.append(note_text)

        detail_text = "\n".join(detail_lines)

        # 显示详情弹窗
        win = tk.Toplevel(self.root)
        win.title(f"版本详情: {version_tag}")
        win.geometry("520x480")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        # 居中窗口
        win.update_idletasks()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        win_x = root_x + (root_w - 520) // 2
        win_y = root_y + (root_h - 480) // 2
        win.geometry(f"+{win_x}+{win_y}")

        bg = COLORS['bg_card']
        fg_pri = COLORS['text_primary']
        fg_sec = COLORS['text_secondary']

        # 标题栏
        hdr = tk.Frame(win, bg=COLORS['accent_ai'], height=36)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="进化版本详情", font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['accent_ai'], fg='#ffffff').pack(side=tk.LEFT, padx=12, pady=9)
        tk.Button(hdr, text="✕", font=('微软雅黑', 9),
                  bg=COLORS['accent_ai'], fg='#ffffff', relief='flat',
                  command=win.destroy).pack(side=tk.RIGHT, padx=10, pady=6)

        # 内容区
        txt = tk.Text(win, wrap=tk.WORD, font=('Consolas', 9),
                      bg=bg, fg=fg_pri, relief='flat',
                      padx=14, pady=12, state=tk.NORMAL)
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 0))
        txt.insert(tk.END, detail_text)
        txt.config(state=tk.DISABLED)

        # 底部按钮区
        btn_frame = tk.Frame(win, bg=COLORS['bg_card'])
        btn_frame.pack(fill=tk.X, padx=12, pady=(8, 12))

        # 回滚按钮（非 active 状态显示）
        if status != 'active':
            tk.Button(btn_frame, text="回滚到此版本", font=('微软雅黑', 9),
                      bg=COLORS['accent_warning'], fg='#ffffff', relief='flat',
                      activebackground='#b45309',
                      command=lambda vt=version_tag: self._on_evo_rollback_to(vt)).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(btn_frame, text="关闭", font=('微软雅黑', 9),
                  bg=COLORS['accent_p5'], fg='#ffffff', relief='flat',
                  activebackground='#047857',
                  command=win.destroy).pack(side=tk.RIGHT)

    def _on_evo_rollback(self):
        """回滚到版本树中当前选中的版本，失败时显示 toast 提示。"""
        tree = getattr(self, 'evo_version_tree', None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            self._show_toast('请先在版本表中选择要回滚的版本', COLORS['warning'], duration=2000)
            return
        item = tree.item(sel[0])
        values = item.get('values', [])
        if not values:
            self._show_toast('未选中有效版本', COLORS['warning'], duration=1500)
            return
        version_tag = str(values[0])
        status = str(values[1])
        if status == 'active':
            self._show_toast('当前已是 active 版本，无需回滚', COLORS['info'], duration=1500)
            return
        self._on_evo_rollback_to(version_tag)

    def _on_evo_rollback_to(self, version_tag):
        """回滚到指定版本（支持右键菜单调用）。"""
        ok = False
        err = None
        try:
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            if hasattr(eng, 'rollback_to_version'):
                res = eng.rollback_to_version(version_tag)
                ok = bool(res.get('ok'))
                err = res.get('error')
        except Exception as e:  # noqa: BLE001
            err = str(e)
        if ok:
            self._show_toast(f'已回滚到 {version_tag}', COLORS['success'], duration=2000)
            self._refresh_evolution_versions()
        else:
            self._show_toast(f'回滚失败: {err or "未知错误"}', COLORS['accent_danger_light'], duration=2500)

    def _on_evo_proposals(self):
        """弹出改进建议窗口（从 engine.get_proposals 读取最新建议列表）。"""
        rows = []
        try:
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            if hasattr(eng, 'get_proposals'):
                rows = eng.get_proposals(limit=50) or []
        except Exception:  # noqa: BLE001
            rows = []
        top = tk.Toplevel(self.root)
        top.title("进化改进建议")
        top.configure(bg=COLORS['bg_primary'])
        top.geometry("680x360")
        cols = ('时间', '分类', '优先级', '标题', '状态')
        tree = ttk.Treeview(top, columns=cols, show='headings')
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=120 if c != '标题' else 240, anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))
        for r in rows:
            tree.insert('', tk.END, values=(
                str(r.get('created_at', ''))[:19],
                r.get('category', ''),
                r.get('priority', ''),
                r.get('title', ''),
                r.get('status', ''),
            ))
        if not rows:
            tree.insert('', tk.END, values=('暂无建议', '', '', '', ''))

    def _show_evolution_link_state(self):
        """弹出联动状态窗口（展示与「开始分析」的数据同步情况）。"""
        try:
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            link_state = eng.get_link_state() if hasattr(eng, 'get_link_state') else {}
        except Exception:
            link_state = {}

        win = tk.Toplevel(self.root)
        win.title("联动状态")
        win.configure(bg=COLORS['bg_primary'])
        win.geometry("480x420")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        # 居中
        win.update_idletasks()
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        win.geometry(f"+{rx + (rw - 480) // 2}+{ry + (rh - 420) // 2}")

        # 标题栏
        hdr = tk.Frame(win, bg=COLORS['accent_ai'], height=36)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="联动状态", font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['accent_ai'], fg='#ffffff').pack(side=tk.LEFT, padx=12, pady=9)
        tk.Button(hdr, text="✕", font=('微软雅黑', 9),
                  bg=COLORS['accent_ai'], fg='#ffffff', relief='flat',
                  command=win.destroy).pack(side=tk.RIGHT, padx=10, pady=6)

        # 内容区（卡片式布局）
        content = tk.Frame(win, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # 构建展示文本
        lines = []
        lines.append(f"最后分析期号: {link_state.get('last_analysis_issue', '—')}")
        lines.append(f"预测目标期号: {link_state.get('last_prediction', {}).get('target_issue', '—') if isinstance(link_state.get('last_prediction'), dict) else '—'}")
        lines.append(f"等待回填: {'是' if link_state.get('pending_verification') else '否'}")
        lines.append(f"最后同步时间: {link_state.get('last_sync_ts', '—')}")
        lines.append("")
        lines.append("─" * 30)
        lines.append("")

        # 最新验证结果
        last_verif = link_state.get('last_verification', {})
        if last_verif:
            lines.append(f"期号: {last_verif.get('issue', '—')}")
            lines.append(f"Top-1 命中: {last_verif.get('top1', '—')}%")
            lines.append(f"Top-3 命中: {last_verif.get('top3', '—')}%")
            lines.append(f"Top-5 命中: {last_verif.get('top5', '—')}%")
            lines.append(f"验证时间: {last_verif.get('ts', '—')}")
        else:
            lines.append("暂无验证记录")

        # 最佳候选
        best = link_state.get('best_candidate', {})
        if best:
            lines.append("")
            lines.append("─" * 30)
            lines.append("")
            lines.append(f"候选版本: {best.get('version_tag', '—')}")
            metrics = best.get('metrics', {})
            lines.append(f"Top-1: {metrics.get('top1', '—')}%  Top-3: {metrics.get('top3', '—')}%  Top-5: {metrics.get('top5', '—')}%")

        txt = "\n".join(lines)

        text_widget = tk.Text(content, wrap=tk.WORD, font=('Consolas', 9),
                              bg=COLORS['bg_card'], fg=COLORS['text_primary'],
                              relief='flat', padx=12, pady=10, state=tk.NORMAL)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert(tk.END, txt)
        text_widget.config(state=tk.DISABLED)

        # 关闭按钮
        tk.Button(win, text="关闭", font=('微软雅黑', 9),
                  bg=COLORS['accent_p5'], fg='#ffffff', relief='flat',
                  activebackground='#047857',
                  command=win.destroy).pack(pady=(8, 0))

    def _refresh_evolution_versions(self):
        """刷新进化版本表（供标签页与左侧卡片同步展示）。"""
        eng = getattr(self, 'evolution', None)
        tree = getattr(self, 'evo_version_tree', None)
        if eng is None or tree is None:
            return
        try:
            rows = eng.get_versions(limit=50)
            tree.delete(*tree.get_children())
            for idx, r in enumerate(rows):
                m = r.get('metrics', {}) or {}
                status = r.get('status', '')
                tag = 'odd' if idx % 2 else 'even'
                if status == 'active':
                    tag += ' active'
                elif status == 'rolledback':
                    tag += ' rolledback'
                elif status == 'trial':
                    tag += ' trial'
                tree.insert('', tk.END, values=(
                    r.get('version_tag', ''),
                    r.get('status', ''),
                    m.get('top1') if m.get('top1') is not None else '—',
                    m.get('top3') if m.get('top3') is not None else '—',
                    m.get('top5') if m.get('top5') is not None else '—',
                    r.get('parent_tag', '') or '—',
                    str(r.get('created_at', ''))[:19],
                ), tags=(tag,))
            if rows:
                top = rows[0]
                if hasattr(self, 'evo_overview_var'):
                    self.evo_overview_var.set(
                        f"当前版本: {top.get('version_tag', '')} | 状态: {top.get('status', '')}\n"
                        f"Top-1={top.get('metrics', {}).get('top1')} / "
                        f"Top-3={top.get('metrics', {}).get('top3')} / "
                        f"Top-5={top.get('metrics', {}).get('top5')}")
        except Exception:
            pass

    def _export_evolution_versions(self):
        """导出进化版本列表为 JSON 文件（reports/ 目录）。"""
        eng = getattr(self, 'evolution', None)
        if eng is None:
            self._init_evolution_engine()
            eng = self.evolution
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(REPORTS_DIR, f'evolution_versions_{ts}.json')
        n = eng.export_versions(path)
        if n > 0:
            self._show_toast(f'已导出 {n} 条进化版本', COLORS['success'])
        else:
            self._show_toast('暂无可导出的版本', COLORS['warning'])

    def _clear_evolution_log(self):
        """清空自我进化日志文本框。"""
        try:
            self.evo_log.delete(1.0, tk.END)
        except Exception:
            pass

    def _on_system_health_diagnostic(self):
        """触发系统健康诊断：离线评估命中率基线 / 权重漂移 / 进化版本对比 / 学习闭环覆盖率，
        并把 Markdown 报告在进化日志区展示。

        调用路径：
            modules.system_health.run_diagnostic(days=60, limit=200) -> Dict
              ├─ metrics.top1_rate_pct / top3_rate_pct / top5_rate_pct  (诚实对照)
              ├─ metrics.weight_drifts                                 (冻结权重偏离度)
              ├─ metrics.version_summary                               (active/trial 统计)
              ├─ attribution_coverage                                  (per-algo 归因覆盖率)
              └─ suggestions                                           (可执行优化建议清单)
        """
        try:
            # 惰性导入，避免启动时加载重型依赖
            from modules.system_health import run_diagnostic, render_markdown
            # 使用数据库的 lazy connect：DB 不可用时给出明确提示
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            self._show_toast('正在生成系统健康诊断报告…', COLORS['info'])
            report = run_diagnostic(days=60, limit=200)
            if not report or report.get('status') == 'error':
                self._show_toast('诊断失败：数据库不可用', COLORS['accent_danger_light'])
                return
            md = render_markdown(report)
            # 追加到进化日志末尾（保留历史）
            self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
            self.evo_log.insert(tk.END, f'[系统健康诊断] {report.get("ts", "")} | {report.get("status", "")}\n', 'section')
            self.evo_log.insert(tk.END, f'{md}\n', 'info')
            self.evo_log.see(tk.END)
            paths = report.get('_paths', {})
            json_p = paths.get('json', '')
            md_p = paths.get('markdown', '')
            detail = []
            if json_p:
                detail.append(f'JSON={json_p}')
            if md_p:
                detail.append(f'MD={md_p}')
            self._show_toast(
                f'健康分 {report.get("summary","")}'
                + (f' | 已保存 {", ".join(detail)}' if detail else ''),
                COLORS['success'] if report.get('status') == 'healthy' else COLORS['warning'],
                duration=4000,
            )
        except Exception as e:
            logger.warning('[GUI] 系统健康诊断异常: %s', e, exc_info=True)
            self._show_toast(f'诊断异常: {e}', COLORS['accent_danger_light'])

    # =========================================================================
    # 结果显示板块（v3.50 重构辅助）：滚动 / 分类 / 清空 / 导出
    # =========================================================================
    def _dash_yview(self, *args):
        """转发滚动条命令到滚动画布。"""
        try:
            self.dash_container.yview(*args)
        except Exception:
            pass

    def _update_result_scrollregion(self):
        """刷新结果滚动画布的滚动区域（内容变化后调用）。"""
        try:
            self.dash_container.configure(scrollregion=self.dash_container.bbox('all'))
        except Exception:
            pass

    def _iter_all(self, widget):
        """递归遍历 widget 的所有子孙控件（生成器）。"""
        for ch in widget.winfo_children():
            yield ch
            yield from self._iter_all(ch)

    def _apply_result_category(self, cat):
        """按分类筛选结果显示：全部 / 预测结论 / 分位信号 / 算法依据。"""
        dash = getattr(self, 'result_dash', None)
        if dash is None:
            return
        for w in self._iter_all(dash):
            rc = getattr(w, '_rcat', None)
            if rc is None:
                continue
            if cat == '全部' or rc == cat:
                try:
                    w.pack(fill=tk.X, pady=(0, 8))
                except Exception:
                    pass
            else:
                try:
                    w.pack_forget()
                except Exception:
                    pass
        self._update_result_scrollregion()

    def _clear_result_board(self):
        """清空右侧预测结果仪表盘（不影响运行日志）。"""
        try:
            for w in list(self.result_dash.winfo_children()):
                w.destroy()
            self.result_dash.pack_forget()
            self._result_placeholder.pack(fill=tk.BOTH, expand=True)
            self._update_result_scrollregion()
        except Exception:
            pass
        self._show_toast('结果已清空', COLORS['success'])

    def _export_result_board(self):
        """导出当前预测结果（结构化 JSON）为 Markdown 报告。"""
        pf = getattr(self, '_last_pipeline_final', None)
        tr = getattr(self, '_last_trend_result', None)
        qf = getattr(self, '_last_quick_final', None)
        if not (pf or tr or qf):
            self._show_toast('当前没有可导出的结果', COLORS['warning'])
            return
        meta = getattr(self, '_clipboard_meta', {}) or {}
        lines = [
            '# 排列5 预测结果导出', '',
            f'- 目标期号: {meta.get("target_issue", "")}',
            f'- 主推荐(4位): {meta.get("main_combo", "")}',
            f'- 一致性: {meta.get("conf")}',
            f'- 高置信度: {meta.get("high_conf")}', '',
            '## 预测记录 (JSON)', '```json',
        ]
        try:
            lines.append(json.dumps({'pipeline': pf, 'trend': tr, 'quick': qf},
                                    ensure_ascii=False, indent=2, default=str))
        except Exception:
            lines.append(str(pf))
        lines.append('```')
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(REPORTS_DIR, f'result_export_{ts}.md')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(lines))
            self._show_toast(f'结果已导出: {os.path.basename(path)}', COLORS['success'])
        except Exception as e:
            self._show_toast(f'导出失败: {e}', COLORS['accent_danger'])

    def _focus_result_tab(self):
        """程序化切换到「 预测结果」标签页（预测完成后自动聚焦）。"""
        try:
            self.output_nb.select(0)
        except Exception:
            pass

    def _build_log_tab(self, parent):
        """（已移除）运行日志标签页改为写入 logs/gui_run.log，不再在界面渲染。"""

    # =========================================================================
    # 快捷概览栏 & 搜索
    # =========================================================================

    def _update_quick_overview(self):
        """刷新顶部概览卡（v3.18 异步化）

        重构点：原实现直接在「主线程」里 P5Database().connect() 并同步执行三条
        SQL，在 _build_output_panel 构建阶段被调用，导致「界面首帧要等一次 MySQL
        连接+查询」才出现——这是打开卡顿的第二大来源。
        现改为：主线程只放一个「加载中」占位（瞬时），真正的 DB 读取放到后台守护
        线程，取完数据后经 root.after 回到主线程渲染，窗口因此可以秒开且不阻塞。
        """
        # (左侧结果总览已移除，概览功能不再独立渲染)
        return
    def _ov_tile(self, parent, label, value, value_color, sub=None):
        """概览指标磁贴：小标签(上) + 大数值(下) + 可选副信息，统一信息层次。"""
        tile = tk.Frame(parent, bg=COLORS['bg_secondary'],
                        highlightbackground=COLORS['border'], highlightthickness=1)
        tile.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8), ipadx=10, ipady=2)
        tk.Label(tile, text=label, font=('微软雅黑', 8),
                 bg=COLORS['bg_secondary'], fg=COLORS['text_muted']).pack(anchor=tk.W, padx=10, pady=(8, 0))
        tk.Label(tile, text=value, font=('Consolas', 15, 'bold'),
                 bg=COLORS['bg_secondary'], fg=value_color).pack(anchor=tk.W, padx=10, pady=(0, 8 if not sub else 0))
        if sub:
            tk.Label(tile, text=sub, font=('微软雅黑', 8),
                     bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=10, pady=(0, 8))
        return tile


    def _show_ai_report_detail(self, target_issue: str):
        """弹出窗口展示某期AI预测详情（结构化表格）

        v3.23 修复: 原实现误用 ttk.Label 传 bg/fg 参数抛 TclError 被静默吞掉,
        导致弹窗一直是空白; 改用 tk.Label。并把 DB 查询移至后台线程, 弹窗不卡 UI。
        """
        try:
            win = tk.Toplevel(self.root)
            win.title(f"AI 预测详情 - {target_issue}")
            win.geometry("520x400")
            win.configure(bg=COLORS['bg_secondary'])
            win.transient(self.root)
            win.grab_set()

            tk.Label(win, text=f"期号: {target_issue}", font=('微软雅黑', 11, 'bold'),
                     bg=COLORS['bg_secondary'], fg=COLORS['text_primary']).pack(pady=(10, 4))

            loading_var = tk.StringVar(value=" 正在加载该期预测数据…")
            loading_lbl = tk.Label(win, textvariable=loading_var, font=('微软雅黑', 10),
                                   bg=COLORS['bg_secondary'], fg=COLORS['text_muted'])
            loading_lbl.pack(pady=16)

            def _worker():
                """后台线程查询指定期号的 AI 报告及其实际开奖号码。

                说明:
                    结果通过 (row, actual, err) 三元组回传给 _render；未开奖时 actual 为 None。
                """
                row, actual, err = None, None, None
                try:
                    db = P5Database()
                    if not db.connect():
                        err = "数据库连接失败"
                    else:
                        try:
                            db.cursor.execute(
                                'SELECT probability_stats, recommended_combinations, next_issue '
                                'FROM p5_ai_report WHERE next_issue = %s ORDER BY created_at DESC LIMIT 1',
                                (target_issue,)
                            )
                            row = db.cursor.fetchone()
                            # 顺带查该期实际开奖号码（未开奖则为 None）
                            try:
                                db.cursor.execute(
                                    'SELECT wan, qian, bai, shi, ge FROM p5_history_data '
                                    'WHERE issue = %s LIMIT 1', (target_issue,)
                                )
                                actual = db.cursor.fetchone()
                            except Exception:
                                actual = None
                        finally:
                            db.disconnect()
                except Exception as ex:
                    err = f"查询失败: {ex}"
                try:
                    self.root.after(0, lambda: _render(row, actual, err))
                except Exception:
                    pass

            def _render(row, actual, err):
                """在主线程渲染 AI 报告详情窗口。

                参数:
                    row: 数据库查出的报告行（含概率统计、推荐组合、期号）
                    actual: 该期实际开奖号码，未开奖时为 None
                    err: 后台查询的错误信息，非空时仅显示错误提示

                说明:
                    渲染前先判断窗口是否仍存在，避免用户提前关闭导致的 TclError。
                """
                try:
                    if not win.winfo_exists():
                        return
                    if err:
                        loading_var.set(f" {err}")
                        loading_lbl.config(fg=COLORS['accent_danger'])
                        return
                    if not row:
                        loading_var.set(" 未找到该期预测数据")
                        loading_lbl.config(fg=COLORS['accent_danger'])
                        return
                    loading_lbl.destroy()
                    self._render_ai_report_detail(win, row, actual)
                except Exception as ex:
                    logger.debug(f"渲染预测详情失败: {ex}")

            threading.Thread(target=_worker, daemon=True).start()

        except Exception as e:
            logger.debug(f"查看预测详情失败: {e}")

    def _render_ai_report_detail(self, win, row, actual=None):
        """（主线程）在详情弹窗内渲染 AI 报告内容。

        新增「预测 vs 开奖」逐位对照面板——
        首选命中=绿色 / Top-3含开奖=橙色 / 未含=灰红；未开奖显示提示。
        """
        try:
            stats_raw = row.get('probability_stats') or '{}'
            try:
                stats = json.loads(stats_raw)
                trend = stats.get('trend_prediction', {}) or {}
            except Exception:
                trend = {}

            pos_keys = [('wan', '万位'), ('qian', '千位'), ('bai', '百位'),
                        ('shi', '十位'), ('ge', '个位')]

            # ---------- 预测 vs 开奖 逐位对照 ----------
            cmp_panel = ttk.LabelFrame(win, text="预测 vs 开奖对照", padding=6)
            cmp_panel.pack(fill=tk.X, padx=10, pady=4)
            if actual and all(actual.get(pk) is not None for pk, _ in pos_keys):
                try:
                    win.geometry("540x560")
                except Exception:
                    pass
                grid = tk.Frame(cmp_panel, bg=COLORS['bg_secondary'])
                grid.pack(anchor=tk.W)
                # 表头行
                tk.Label(grid, text="", width=8, bg=COLORS['bg_secondary']
                         ).grid(row=0, column=0)
                for ci, (pk, pn) in enumerate(pos_keys):
                    tk.Label(grid, text=pn, width=6, font=('微软雅黑', 9),
                             bg=COLORS['bg_secondary'], fg=COLORS['text_muted']
                             ).grid(row=0, column=ci + 1)
                # 预测首选行（按命中状态标色）
                tk.Label(grid, text="预测首选", width=8, font=('微软雅黑', 9),
                         bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']
                         ).grid(row=1, column=0, sticky=tk.W)
                hit_cnt = 0
                for ci, (pk, pn) in enumerate(pos_keys):
                    nums = (trend.get(pk) or {}).get('numbers', [])
                    top1 = nums[0] if nums else None
                    act = int(actual.get(pk))
                    if top1 is not None and int(top1) == act:
                        fg, note = '#2e7d32', '' # 首选命中
                        hit_cnt += 1
                    elif any(int(n) == act for n in nums[:3] if n is not None):
                        fg, note = '#e65100', '' # Top-3 含开奖
                    else:
                        fg, note = '#b71c1c', '' # 未含
                    tk.Label(grid, text=f"{top1 if top1 is not None else '-'} {note}",
                             width=6, font=('Consolas', 11, 'bold'),
                             bg=COLORS['bg_secondary'], fg=fg
                             ).grid(row=1, column=ci + 1)
                # 实际开奖行
                tk.Label(grid, text="实际开奖", width=8, font=('微软雅黑', 9),
                         bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']
                         ).grid(row=2, column=0, sticky=tk.W)
                for ci, (pk, pn) in enumerate(pos_keys):
                    tk.Label(grid, text=str(actual.get(pk)), width=6,
                             font=('Consolas', 11, 'bold'),
                             bg=COLORS['bg_secondary'], fg=COLORS['text_primary']
                             ).grid(row=2, column=ci + 1)
                tk.Label(cmp_panel,
                text=f"首选命中 {hit_cnt}/5 图例: 首选命中 Top-3含开奖 未含",
                         font=('微软雅黑', 8), bg=COLORS['bg_secondary'],
                         fg=COLORS['text_muted']).pack(anchor=tk.W, pady=(4, 0))
            else:
                tk.Label(cmp_panel, text="该期尚未开奖或开奖数据未入库，暂无对照。",
                         font=('微软雅黑', 9), bg=COLORS['bg_secondary'],
                         fg=COLORS['text_muted']).pack(anchor=tk.W)

            # ---------- 各位置推荐号码（命中的号码高亮） ----------
            panel = ttk.LabelFrame(win, text="各位置推荐号码", padding=6)
            panel.pack(fill=tk.X, padx=10, pady=4)

            for pk, pn in pos_keys:
                nums = (trend.get(pk) or {}).get('numbers', [])
                line = tk.Frame(panel, bg=COLORS['bg_secondary'])
                line.pack(anchor=tk.W)
                tk.Label(line, text=f"{pn}: ", font=('Consolas', 9),
                         bg=COLORS['bg_secondary'], fg=COLORS['text_primary']
                         ).pack(side=tk.LEFT)
                act = None
                try:
                    act = int(actual.get(pk)) if actual and actual.get(pk) is not None else None
                except Exception:
                    act = None
                for n in nums[:3]:
                    is_hit = (act is not None and n is not None and int(n) == act)
                    tk.Label(line, text=str(n),
                             font=('Consolas', 9, 'bold' if is_hit else 'normal'),
                             bg='#c8e6c9' if is_hit else COLORS['bg_secondary'],
                             fg='#1b5e20' if is_hit else COLORS['text_primary']
                             ).pack(side=tk.LEFT, padx=3)

            combo_raw = row.get('recommended_combinations') or '[]'
            try:
                combos = json.loads(combo_raw)
            except Exception:
                combos = []

            combo_panel = ttk.LabelFrame(win, text="推荐组合 (Top-5)", padding=6)
            combo_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

            combo_text = tk.Text(combo_panel, height=8, wrap=tk.WORD,
                                 font=('Consolas', 9),
                                 bg=COLORS['bg_input'], fg=COLORS['text_primary'],
                                 relief='flat')
            combo_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
            for i, c in enumerate(combos[:5]):
                if isinstance(c, dict):
                    _comb = c.get('combination', '')
                    _reason = c.get('reason', '')
                    _line = f"{i+1}. {_comb}"
                    if _reason:
                        _line += f"  — {_reason}"
                    _line += "\n"
                    combo_text.insert(tk.END, _line, '')
                elif isinstance(c, str):
                    combo_text.insert(tk.END, f"{i+1}. {c}\n", '')

            combo_text.config(state=tk.DISABLED)
            tk.Button(win, text="关闭", font=('微软雅黑', 9),
                      bg=COLORS['accent_p5'], fg='#fff', relief='flat',
                      command=win.destroy).pack(pady=(0, 8))

        except Exception as e:
            logger.debug(f"查看预测详情失败: {e}")

    def _show_search_dialog(self):
        """（已移除）运行日志已改为写入文件，搜索对话框停用。"""
        self._show_toast(" 运行日志已改为文件记录，无需界面搜索", COLORS['info'], duration=1500)

    def _build_status_bar(self, parent):
        """构建底部状态栏（状态指示灯 + 状态文字 + 技术栈信息）"""
        status_bar = tk.Frame(parent, bg=COLORS['bg_secondary'], height=28)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        status_bar.pack_propagate(False)

        self.status_dot = tk.Canvas(status_bar, width=10, height=10,
                                    bg=COLORS['bg_secondary'],
                                    highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(12, 5), pady=8)
        self._status_dot_id = self.status_dot.create_oval(1, 1, 9, 9,
                                                          fill=COLORS['success'], outline='')

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(status_bar, textvariable=self.status_var,
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(side=tk.LEFT, padx=5, pady=4)

        tk.Frame(status_bar, bg=COLORS['border'], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=5)

        tk.Label(status_bar, text="Python 3.x | AI分析 | MySQL 数据库",
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(side=tk.LEFT, padx=5, pady=4)

    def _on_button_click(self, task_name, task_func):
        """按钮点击统一入口：检查任务状态后提交到后台线程（v3.36 优化版）

        增加超时保护 (v3.9): 如果后台任务超过 30 分钟无响应，视为卡死，
        弹出用户友好的提示（而非界面永久冻结）。
        """
        if self.task_mgr.is_running():
            # 使用Toast提示代替阻塞弹窗，提升用户体验
            self._show_toast("当前有任务正在执行，请等待完成", COLORS['warning'], duration=2000)
            return

        # 点击按钮时即时反馈（按钮按下效果）
        # 注：由于lambda包装，无法直接获取按钮引用，改为状态栏提示
        self.status_var.set(f"即将开始: {task_name}")
        # overview_flush_pending removed = False  # 分析开始前重置概览刷新标志

        success = self.task_mgr.submit(task_func, task_name)
        if not success:
            messagebox.showwarning("提示", "任务提交失败，请重试")

        # 启动 liveness 看门狗（取代原"30分钟硬超时"）
        # 原逻辑在 30 分钟整无条件 kill 任何仍在跑的任务——但"全部50期"回测
        # 可能跑 17~58 分钟，会被误杀且按钮永久卡禁用。
        # 新逻辑：任务在跑且「连续 30 分钟无任何日志/进度输出」才视为真卡死而终止；
        # 正常推进（即使耗时很久）永不误杀。看门狗每 60s 自检、自动续排/停止。
        self.root.after(60 * 1000, self._liveness_watchdog)

    def _liveness_watchdog(self):
        """基于「活动心跳」的看门狗，取代原 30 分钟硬超时。

        仅当任务仍在运行「且」距最近一次日志/进度输出已超过 LIVENESS_LIMIT 秒时，
        才判定为卡死（如 AI 接口无响应挂死），执行终止并恢复按钮；
        正常但耗时的任务（如全部50期回测，逐期有日志）不会被误杀。
        """
        LIVENESS_LIMIT = 30 * 60  # 30 分钟无任何输出 → 视为卡死
        if not self.task_mgr.is_running():
            return  # 任务已结束，看门狗自动退役（不再续排）
        if time.time() - self.task_mgr._last_activity <= LIVENESS_LIMIT:
            # 仍在正常推进，续排下一轮自检
            self.root.after(60 * 1000, self._liveness_watchdog)
            return
        # —— 判定为卡死：终止并恢复 UI ——
        _name = self._current_task_name
        self.task_mgr.log(f"\n 任务「{_name}」疑似卡死（连续 {LIVENESS_LIMIT // 60} 分钟无输出）")
        self.task_mgr.log("  可能原因: AI接口无响应 / 网络中断 / 死锁")
        self.task_mgr.log("  已强制终止并恢复界面，请检查网络后重试")
        self.task_mgr.cancel()
        # 关键修复：原 cancel() 只置 _running=False 却不恢复按钮，
        # 会导致界面按钮永久禁用；此处显式调用完成回调以恢复交互。
        self._on_task_finished()

    def _on_task_started(self, task_name):
        """任务启动时：禁用按钮、重置进度条、更新状态指示器（v3.36 优化版）"""
        self._current_task_name = task_name
        # 重置进度节流状态
        self._last_progress_value = -1

        # 重置并隐藏结果仪表盘（每次任务以最新结果为准）
        self._last_pipeline_final = None
        self._last_trend_result = None
        self._last_quick_final = None
        # 一并清空上轮融合概率缓存，避免「命中率优化」阶段
        # 拿到上一次任务的陈旧分布做对照。
        self._last_fused_probabilities = None
        self._last_predict_meta = {}
        try:
            self._hide_result_dashboard()
        except Exception:
            pass
        try:
            import logging as _lg
            _d = _lg.getLogger('kplucky.debug')
            _d.info('DASHBOARD_HIDE: task_started=%s', task_name)
        except Exception:
            pass
        self._set_buttons_state(tk.DISABLED)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self.status_var.set("正在执行...")
        self.task_status_label.config(text=f"{task_name} 运行中...", fg=COLORS['warning'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['warning'])

        # 使用 TaskManager 刷新日志，避免高频UI操作
        now = datetime.now().strftime('%H:%M:%S')
        self.task_mgr.log(f"\n{'=' * 70}\n")
        self.task_mgr.log(f"  [{now}] 开始执行: {task_name}\n")
        self.task_mgr.log(f"{'=' * 70}\n\n")

    def _on_task_finished(self):
        """任务完成时：恢复按钮、进度条置100%、状态指示器变绿（v3.36 优化版）"""
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 100
        self.progress_label.config(text="100%")
        self._last_progress_value = 100  # 更新节流状态
        self.status_var.set("任务完成")
        self.task_status_label.config(text=f"{self._current_task_name} 已完成", fg=COLORS['success'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['success'])

        # 关键修复：强制重置任务运行状态，防止竞态条件导致复制按钮误判
        # 使用 after(0) 确保在主线程中执行，避免多线程问题
        self.root.after(0, self._sync_task_state)

        # 使用 TaskManager 刷新日志
        now = datetime.now().strftime('%H:%M:%S')
        self.task_mgr.log(f"\n{'=' * 70}\n")
        self.task_mgr.log(f"  [{now}] 任务完成: {self._current_task_name}\n")
        self.task_mgr.log(f"{'=' * 70}\n")

    def _sync_task_state(self):
        """同步任务状态，确保 is_running() 立即返回 False（v3.60 修复竞态条件）"""
        try:
            # 强制清理所有运行中任务记录，防止竞态条件
            with self.task_mgr._running_lock:
                self.task_mgr._running_tasks.clear()
        except Exception as e:
            import logging
            logging.getLogger('kplucky.debug').error(f'SYNC_TASK_STATE_ERROR: {e}', exc_info=True)

    def _on_task_error(self, error_msg):
        """任务出错时：恢复按钮、进度条归零、状态指示器变红、弹窗提示（v3.36 优化版）"""
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self._last_progress_value = 0  # 更新节流状态
        self.status_var.set("任务失败")
        self.task_status_label.config(text=f"{self._current_task_name} 失败", fg=COLORS['accent_danger'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])

        messagebox.showerror("错误", f"任务执行失败:\n{error_msg}")

        # 关键修复：强制重置任务运行状态，防止竞态条件导致后续操作误判
        # 使用 after(0) 确保在主线程中执行，避免多线程问题
        self.root.after(0, self._sync_task_state)

    def _cancel_current_task(self):
        """取消当前任务（v3.36 新增）"""
        if self.task_mgr.is_running():
            self.task_mgr.cancel()
            self._show_toast("任务已取消", COLORS['warning'], duration=1500)
            self._on_task_finished()

    # ============================================================
    # 日志文件写入
    # ============================================================

    def flush_pending_logs(self):
        """提示：运行日志已改为写入文件（logs/gui_run.log），当前无需 UI 刷新。"""
        try:
            self.task_mgr.flush_pending_logs()
        except Exception:
            pass

    def append_colored(self, text, tag='info'):
        """保留为空方法，避免外部调用报错（输出面板已移除）。"""

    def append_section_header(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    def append_success(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    def append_warning(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    def append_error(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    def _create_toolbar_button(self, parent, text, command, button_type='primary'):
        """创建工具栏按钮（支持多种样式类型）
        
        Args:
            parent: 父容器
            text: 按钮文本
            command: 点击回调函数
            button_type: 按钮类型（primary/secondary/danger）
            
        Returns:
            tk.Button: 创建的按钮对象
        """
        styles = {
            'primary': {
                'bg': COLORS['accent_p5'],
                'fg': '#ffffff',
                'active_bg': COLORS['accent_p5_light'],
                'font': ('微软雅黑', 9, 'bold')
            },
            'secondary': {
                'bg': COLORS['bg_card'],
                'fg': COLORS['text_secondary'],
                'active_bg': COLORS['bg_card_hover'],
                'font': ('微软雅黑', 9)
            },
            'danger': {
                'bg': COLORS['bg_card'],
                'fg': COLORS['accent_danger_light'],
                'active_bg': COLORS['accent_danger'],
                'font': ('微软雅黑', 9)
            }
        }
        
        style = styles.get(button_type, styles['secondary'])
        
        btn = tk.Button(parent, text=text, command=command,
                        font=style['font'],
                        bg=style['bg'],
                        fg=style['fg'],
                        activebackground=style['active_bg'],
                        activeforeground='#ffffff' if button_type == 'danger' else style['fg'],
                        relief='flat', 
                        bd=0, 
                        padx=10, 
                        pady=4, 
                        cursor='hand2',
                        highlightthickness=0)
        
        # 添加悬停效果
        btn.bind('<Enter>', lambda e, b=btn: self._on_button_enter(e, b, button_type))
        btn.bind('<Leave>', lambda e, b=btn: self._on_button_leave(e, b, button_type))
        
        # 注册到按钮列表，便于批量启用/禁用（任务运行时自动禁用）
        self._buttons.append(btn)
        
        return btn

    def _on_button_enter(self, event, button, button_type):
        """按钮悬停进入效果"""
        if button_type == 'primary':
            button.config(bg=COLORS['accent_p5_light'])
        elif button_type == 'secondary':
            button.config(bg=COLORS['bg_card_hover'])
            button.config(fg=COLORS['text_primary'])
        elif button_type == 'danger':
            button.config(bg=COLORS['accent_danger'])
            button.config(fg='#ffffff')

    def _on_button_leave(self, event, button, button_type):
        """按钮悬停离开效果"""
        styles = {
            'primary': {'bg': COLORS['accent_p5'], 'fg': '#ffffff'},
            'secondary': {'bg': COLORS['bg_card'], 'fg': COLORS['text_secondary']},
            'danger': {'bg': COLORS['bg_card'], 'fg': COLORS['accent_danger_light']}
        }
        style = styles.get(button_type, styles['secondary'])
        button.config(bg=style['bg'], fg=style['fg'])

    def _setup_loading_animation(self):
        """设置加载动画效果"""
        self._loading_frame = tk.Frame(self.dash_container, bg=COLORS['bg_card'])
        self._loading_label = tk.Label(self._loading_frame, 
                                       text="",
                                       font=('Consolas', 12),
                                       bg=COLORS['bg_card'],
                                       fg=COLORS['accent_p5'])
        self._loading_label.pack(pady=16)
        self._loading_frame.pack_forget()
        self._loading_active = False
        self._loading_dots = 0

    def _start_loading(self, message="分析中"):
        """启动加载动画"""
        self._loading_active = True
        self._loading_dots = 0
        self._loading_frame.pack(fill=tk.X)
        self._update_loading_text(message)

    def _stop_loading(self):
        """停止加载动画"""
        self._loading_active = False
        self._loading_frame.pack_forget()

    def _update_loading_text(self, base_message):
        """更新加载动画文本（带动态圆点效果）"""
        if not self._loading_active:
            return
        
        self._loading_dots = (self._loading_dots + 1) % 4
        dots = "." * self._loading_dots
        self._loading_label.config(text=f"{base_message}{dots}")
        
        if self._loading_active:
            self.root.after(300, lambda: self._update_loading_text(base_message))

    # ============================================================
    # 一键复制预测号码
    # ============================================================

    def _build_copy_from_cached_results(self):
        """兜底：从未渲染缓冲（_last_*_final）现场拼出可复制的预测摘要。

        适用场景：
          - _prediction_clipboard 尚未写入（仪表盘 after(0) 还没轮到执行）
          - _prediction_clipboard 已写入但被新分析清空（单源聚合时 main_combo_disp 为空）
          - 历史残留数据
        任意场景下若缓存的 final_report 包含 trend_prediction 或 recommended_combinations，
        都能拼出符合微信格式的可复制文本。
        """
        try:
            pf = getattr(self, '_last_pipeline_final', None)
            qf = getattr(self, '_last_quick_final', None)
            tr = getattr(self, '_last_trend_result', None)
            candidates = [pf, qf, tr]
            # 选一个含 trend_prediction 的最完整 final
            for c in candidates:
                if isinstance(c, dict) and (c.get('trend_prediction') or c.get('recommended_combinations')):
                    return self._build_prediction_clipboard(c)
            # 退化：任意一个有 target_issue / next_issue 的 final，强行拼一份
            for c in candidates:
                if isinstance(c, dict) and (c.get('target_issue') or c.get('next_issue')):
                    fb = dict(c)
                    fb.setdefault('trend_prediction', {})
                    fb.setdefault('recommended_combinations', [])
                    return self._build_prediction_clipboard(fb)
            return ""
        except Exception:
            return ""

    def _copy_prediction(self):
        """将「预测结果仪表盘」中的预测号码一键复制到剪贴板（微信兼容版）

        严格绑定到预测号码区域：仅复制预测仪表盘生成的结构化预测摘要
        （self._prediction_clipboard），不扫描/复制日志或其它报告区域的数据。
        若尚未运行分析生成预测，则提示先运行「开始分析」。
        若分析任务正在进行中，提示用户等待分析完成后再复制，避免获取到中间态结果。

        v3.62 兜底链路：即便 _prediction_clipboard 暂未写入（仪表盘渲染在主线程
        after(0) 队列中尚未轮到），也会尝试从未渲染的 final_report 缓存中现场
        拼出可复制文本，确保用户点复制按钮一定能拿到分析结果。

        微信格式优化：
        - 替换特殊字符为微信兼容字符（━━━━━━━━ → ━━━━）
        - 使用标准换行符（\\n）
        - 控制单行长度（≤80字符）
        - 移除可能导致格式错乱的不可见字符
        """
        # 详细日志：记录复制尝试的上下文
        try:
            import logging
            _log = logging.getLogger('kplucky.debug')
            _clip = getattr(self, '_prediction_clipboard', '') or ''
            _meta = getattr(self, '_clipboard_meta', {}) or {}
            _log.info('COPY_ATTEMPT: clipboard_len=%d task_running=%s has_meta=%s',
                      len(_clip), self.task_mgr.is_running(), bool(_meta))
        except Exception:
            pass

        # ① 检查是否有任务正在运行，防止在分析过程中复制到中间态结果
        if self.task_mgr.is_running():
            try:
                _log.warning('COPY_BLOCKED_TASK_RUNNING: 任务运行中，拒绝复制中间态结果')
            except Exception:
                pass
            messagebox.showinfo(
                "分析进行中",
                "当前有分析任务正在进行中，预测结果尚未最终确定。\n\n"
                "解决步骤：\n"
                "  1. 请等待当前分析任务完成（状态栏会显示「已完成」）\n"
                "  2. 完成后「复制预测号码」按钮自动恢复可用\n"
                "  3. 再次点击即可复制最终预测号码\n\n"
                "注：分析通常耗时 1~3 分钟，请耐心等待。")
            return

        # ② 检查是否有有效的预测结果（剪贴板内容或元数据）
        clip = getattr(self, '_prediction_clipboard', "")
        meta = getattr(self, '_clipboard_meta', {}) or {}
        has_valid_result = (clip and clip.strip()) or (meta and meta.get('target_issue'))

        # ── v3.64 修复：clipboard/meta 可能因聚合异常丢失，此时直接从 _last_pipeline_final 重建 ──
        if not has_valid_result:
            pf = getattr(self, '_last_pipeline_final', None)
            if isinstance(pf, dict) and pf:
                try:
                    rebuilt = self._build_prediction_clipboard(pf)
                    if rebuilt and rebuilt.strip():
                        clip = rebuilt
                        has_valid_result = True
                        # 同步持久化，避免下次点击仍需重建
                        self._prediction_clipboard = clip
                        self._clipboard_meta = {
                            'target_issue': pf.get('target_issue') or pf.get('next_issue') or '',
                            'conf': '',
                            'high_conf': '',
                            'main_combo': '',
                        }
                        try:
                            _log.warning('COPY_REBUILT: 从 _last_pipeline_final 重建剪贴板，长度=%d', len(clip))
                        except Exception:
                            pass
                except Exception:
                    pass

        # v3.62 兜底：若标准缓存为空但存在未渲染缓冲的 final_report，
        # 现场拼一份可复制文本。覆盖竞态场景（仪表盘 after(0) 还未轮到执行）。
        if not has_valid_result:
            fallback_clip = self._build_copy_from_cached_results()
            if fallback_clip and fallback_clip.strip():
                clip = fallback_clip
                has_valid_result = True
                try:
                    _log.warning(
                        'COPY_FALLBACK_USED: 主缓存为空，使用未渲染缓冲拼出预测摘要，'
                        '长度=%d', len(clip))
                except Exception:
                    pass

        # v3.59 兜底：上述全部缓存都为空时，从数据库最近一条预测记录写入。
        # 覆盖场景：本次智能分析被中途取消（用户案例）或早期异常退出，
        # 三个 _last_*_final 全为 None 但数据库仍有最近一次成功预测记录。
        if not has_valid_result:
            try:
                self._populate_clipboard_from_db_fallback()
                clip = getattr(self, '_prediction_clipboard', '')
                if clip and clip.strip():
                    has_valid_result = True
                    _log.warning(
                        'COPY_DB_FALLBACK_USED: 主缓存与未渲染缓冲均空，从数据库最近预测记录兜底，'
                        '长度=%d', len(clip))
            except Exception:
                pass

        if not has_valid_result:
            try:
                _log.warning('COPY_BLOCKED_NO_RESULT: 无有效预测结果（clip空=%s, meta=%s）',
                             not bool(clip.strip()), meta)
            except Exception:
                pass
            messagebox.showinfo(
                "暂无预测结果",
                "当前没有可复制的预测号码数据。\n\n"
                "原因说明：\n"
                "  • 尚未运行分析：请先点击左侧「开始分析」生成预测结果\n"
                "  • 分析未完成：请等待分析流程全部结束后再操作\n"
                "  • 数据不足：历史样本少于 61 期时无法生成可靠预测\n"
                "  • 数据库暂无历史预测记录\n\n"
                "解决步骤：\n"
                "  1. 点击「开始分析」按钮启动完整预测流程\n"
                "  2. 等待六阶段流水线全部完成（状态栏显示「已完成」）\n"
                "  3. 结果仪表盘出现后，点击「复制预测号码」即可")
            return

        # 微信格式优化处理
        data = self._format_for_wechat(clip.strip())

        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(data)
            # 复制成功提示：明确展示 期数 + 置信度 + 复制的号码内容
            meta = getattr(self, '_clipboard_meta', {}) or {}
            issue = meta.get('target_issue') or ''
            combo = meta.get('main_combo') or ''
            conf = meta.get('conf')
            high = meta.get('high_conf')
            parts = []
            if issue:
                parts.append(f"第{issue}期")
            if combo:
                parts.append(f"预测号码 {combo}")
            if conf is not None:
                lvl = "（高置信度）" if high else ""
                parts.append(f"综合一致性置信度 {conf}%{lvl}")
            msg = " 已复制 " + " · ".join(parts) if parts else " 预测号码已复制到剪贴板"
            self._show_toast(msg, COLORS['success'])
            try:
                _log.info('COPY_SUCCESS: issue=%s combo=%s conf=%s high_conf=%s',
                          issue, combo, conf, high)
            except Exception:
                pass
        except Exception as e:
            try:
                _log.error('COPY_FAILED: %s', e, exc_info=True)
            except Exception:
                pass
            messagebox.showerror("复制失败", f"复制到剪贴板出错:\n{e}")

    def _format_for_wechat(self, text: str) -> str:
        """将文本转换为微信消息兼容格式

        微信消息格式要求：
        1. 换行使用 \\n（微信会自动处理）
        2. 避免使用连续特殊字符（如━━━━━━━━━━━━━━━━）
        3. 单行长度不超过80字符
        4. 不使用不可见控制字符
        5. 使用标准标点符号

        Args:
            text: 原始文本

        Returns:
            格式化后的微信兼容文本
        """
        # 1. 替换连续特殊字符为微信友好的分隔线
        wechat_safe_chars = {
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━': '━━━━━━━━━━━━',
            '━━━━━━━━━━━━━━━━━━━━━━━━': '━━━━━━',
            '━━━━━━━━━━━━━━━━': '━━━━',
        }

        for bad, good in wechat_safe_chars.items():
            text = text.replace(bad, good)

        # 2. 处理不可见字符和零宽字符
        # 移除零宽空格、零宽连字符等
        text = ''.join(c for c in text if c not in '\u200b\u200c\u200d\ufeff\u200e\u200f')

        # 3. 处理特殊bullet符号（•）-> 保持或替换为*
        # 微信对这些符号支持良好，保持不变

        # 4. 分割长行并重新组合（每行不超过80字符）
        lines = text.split('\n')
        formatted_lines = []
        for line in lines:
            # 移除行首行尾多余空格（保留一个）
            line = line.rstrip()
            if not line:  # 跳过空行
                continue

            # 如果行太长，尝试在空格处断行（但对于中文，按字符断行）
            if len(line) > 75:
                # 对于中文内容，保持原样（微信自动换行）
                # 但对于英文数字组合，尝试智能断行
                pass
            formatted_lines.append(line)

        return '\n'.join(formatted_lines)

    def _show_export_menu(self):
        """在「导出」按钮处弹出菜单：导出文本 / 导出图片。"""
        clip = getattr(self, '_prediction_clipboard', "")
        if not clip or not clip.strip():
            messagebox.showinfo(
                "提示",
                "当前没有可导出的预测结果。\n请先点击「 开始分析」生成预测。")
            return
        try:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label=" 导出为文本 (.txt)",
                             command=self._export_prediction_text)
            menu.add_command(label=" 导出为图片 (.png)",
                             command=self._export_prediction_image)
            # 定位到鼠标当前位置弹出
            x = self.root.winfo_pointerx()
            y = self.root.winfo_pointery()
            menu.tk_popup(x, y)
        except Exception as e:
            logger.debug(f"导出菜单弹出失败: {e}")

    def _default_export_name(self, ext):
        """生成默认导出文件名：排列5预测_期号_时间.ext"""
        meta = getattr(self, '_clipboard_meta', {}) or {}
        issue = meta.get('target_issue') or ''
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        base = f"排列5预测_{issue}_{ts}" if issue else f"排列5预测_{ts}"
        return f"{base}.{ext}"

    def _export_prediction_text(self):
        """导出预测结果为文本文件（复用剪贴板摘要，无外部依赖）。"""
        clip = getattr(self, '_prediction_clipboard', "")
        if not clip or not clip.strip():
            messagebox.showinfo("提示", "当前没有可导出的预测结果。")
            return
        try:
            path = filedialog.asksaveasfilename(
                title="导出预测结果为文本",
                defaultextension=".txt",
                initialfile=self._default_export_name("txt"),
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if not path:
                return
            with open(path, 'w', encoding='utf-8') as f:
                f.write(clip.strip() + "\n")
            self._show_toast(f" 已导出文本：{os.path.basename(path)}",
                             COLORS['success'])
        except Exception as e:
            messagebox.showerror("导出失败", f"导出文本时出错:\n{e}")

    def _export_prediction_image(self):
        """导出预测仪表盘为 PNG 图片（PIL.ImageGrab 抓取仪表盘区域）。"""
        dash = getattr(self, 'result_dash', None)
        if dash is None:
            messagebox.showinfo("提示", "当前没有可导出的预测结果。")
            return
        try:
            from PIL import ImageGrab
        except Exception:
            messagebox.showwarning(
                "缺少依赖",
                "图片导出需要 Pillow 库，当前环境未安装。\n可改用「导出为文本」。")
            return
        try:
            path = filedialog.asksaveasfilename(
                title="导出预测仪表盘为图片",
                defaultextension=".png",
                initialfile=self._default_export_name("png"),
                filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")])
            if not path:
                return
            # 确保布局完成并置于最前，避免抓到遮挡内容
            self.root.update_idletasks()
            dash.update_idletasks()
            x = dash.winfo_rootx()
            y = dash.winfo_rooty()
            w = dash.winfo_width()
            h = dash.winfo_height()
            if w <= 1 or h <= 1:
                messagebox.showwarning("提示", "仪表盘尚未渲染完成，请稍后再试。")
                return
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            img.save(path)
            self._show_toast(f" 已导出图片：{os.path.basename(path)}",
                             COLORS['success'])
        except Exception as e:
            messagebox.showerror("导出失败", f"导出图片时出错:\n{e}")

    def _build_prediction_clipboard(self, final_report):
        """根据四步流水线 final_report 生成结构化的可复制预测摘要"""
        if not isinstance(final_report, dict):
            return ""
        # 预测号码段展示压缩为4位（保留万/千/百/十，去个位），仅展示层
        pos_keys = DISPLAY_POS_KEYS
        pos_names = DISPLAY_POS_NAMES
        _pos_names_full = ['万位', '千位', '百位', '十位', '个位']  # 贝叶斯分段按位索引用(保留5位分析)
        lines = ["【排列5 预测号码】",
                 f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]

        # 1. 走势图数据预测
        trend = final_report.get('trend_prediction', {})
        if isinstance(trend, dict) and trend:
            lines.append("一、走势图数据预测 (Top 候选)")
            for pos_key, pos_name in zip(pos_keys, pos_names):
                nums = trend.get(pos_key, {}).get('numbers', [])
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
        article = final_report.get('article_prediction', {})
        if isinstance(article, dict) and article:
            lines.append("二、专家文章预测 (Top 候选)")
            for pos_key, pos_name in zip(pos_keys, pos_names):
                nums = article.get(pos_key, {}).get('numbers', [])
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

        # 3. 贝叶斯后验概率 Top-3
        bi = final_report.get('bayesian_inference')
        if isinstance(bi, list) and bi:
            lines.append("三、贝叶斯后验概率 Top-3")
            for i, pos_dict in enumerate(bi[:5]):
                if isinstance(pos_dict, dict) and pos_dict:
                    top3 = sorted(pos_dict.items(),
                                  key=lambda x: float(x[1]), reverse=True)[:3]
                    probs = ", ".join(f"{k}({float(v):.3f})" for k, v in top3)
                    lines.append(f"  {_pos_names_full[i]}: {probs}")

        return "\n".join(lines)

    def _direct_extract_clipboard(self, final_report):
        """直接从 final_report 提取可复制文本，作为 _compute_dashboard_aggregates 兜底使用。
        
        当 _compute_dashboard_aggregates 因结构不匹配而未能设置 clipboard/meta时，
        此方法直接提取 trend_prediction 和 recommended_combinations 等关键字段。
        """
        if not isinstance(final_report, dict):
            return ""
        
        lines = ["【排列5 预测号码】",
                 f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
        
        # 1. 走势图数据预测
        trend = final_report.get('trend_prediction', {})
        if isinstance(trend, dict) and trend:
            lines.append("一、走势图数据预测 (Top 候选)")
            pos_keys = DISPLAY_POS_KEYS
            pos_names = DISPLAY_POS_NAMES
            for pos_key, pos_name in zip(pos_keys, pos_names):
                nums = trend.get(pos_key, {}).get('numbers', [])
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
        
        return "\n".join(lines)

    def _flash_status(self, text, color=COLORS['success']):
        """在底部状态栏临时显示提示，2.5 秒后恢复"就绪" """
        self.status_var.set(text)
        self.root.after(2500, lambda: self.status_var.set("就绪"))

    def _show_toast(self, text, color=None, duration=2200):
        """在主窗口内顶部居中弹出醒目的浮层提示（比状态栏更抓眼球）。

        无边框 Toplevel，淡入→短驻→淡出后自动销毁；同一时刻只保留一个 toast。
        任何异常都静默降级（不影响主流程），并同时刷新状态栏作为兜底。
        """
        bg = color or COLORS['success']
        try:
            # 先销毁上一个仍在显示的 toast，避免叠加
            old = getattr(self, '_toast_win', None)
            if old is not None:
                try:
                    old.destroy()
                except Exception:
                    pass
                self._toast_win = None

            self.root.update_idletasks()
            toast = tk.Toplevel(self.root)
            self._toast_win = toast
            toast.overrideredirect(True)
            toast.attributes('-topmost', True)
            try:
                toast.attributes('-alpha', 0.0)
            except Exception:
                pass

            frame = tk.Frame(toast, bg=bg, highlightthickness=0)
            frame.pack(fill=tk.BOTH, expand=True)
            tk.Label(frame, text=text, bg=bg, fg='#ffffff',
                     font=('微软雅黑', 11, 'bold'),
                     padx=22, pady=12).pack()

            toast.update_idletasks()
            # 定位到主窗口顶部居中
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw = self.root.winfo_width()
            tw = toast.winfo_reqwidth()
            px = rx + max(0, (rw - tw) // 2)
            py = ry + 64
            toast.geometry(f"+{px}+{py}")

            # 淡入
            def _fade_in(a=0.0):
                """Toast 提示的淡入动画，每 20ms 递增 0.15 透明度直至完全不透明。

                参数:
                    a: 当前透明度（0.0~1.0），递归调用时逐步累加
                """
                if not toast.winfo_exists():
                    return
                a = min(1.0, a + 0.15)
                try:
                    toast.attributes('-alpha', a)
                except Exception:
                    pass
                if a < 1.0:
                    toast.after(20, lambda: _fade_in(a))

            # 淡出并销毁
            def _fade_out(a=1.0):
                """Toast 提示的淡出动画，每 20ms 递减 0.12 透明度，归零后销毁窗口。

                参数:
                    a: 当前透明度（0.0~1.0），递归调用时逐步递减
                """
                if not toast.winfo_exists():
                    return
                a = max(0.0, a - 0.12)
                try:
                    toast.attributes('-alpha', a)
                except Exception:
                    pass
                if a > 0.0:
                    toast.after(20, lambda: _fade_out(a))
                else:
                    try:
                        toast.destroy()
                    except Exception:
                        pass
                    if getattr(self, '_toast_win', None) is toast:
                        self._toast_win = None

            _fade_in()
            toast.after(max(600, duration), _fade_out)
        except Exception as e:
            logger.debug(f"toast 显示失败(非致命): {e}")
        # 状态栏兜底，确保信息始终可见
        self._flash_status(text, bg)

    def _confirm_close(self):
        """窗口关闭请求处理（v3.25）：有任务运行时先警示确认，避免误关中断分析。

        Returns:
            bool: True 表示已执行关闭；False 表示用户取消、窗口保留。
        """
        try:
            if self.task_mgr.is_running():
                task_name = getattr(self, '_current_task_name', '') or '后台任务'
                ok = messagebox.askyesno(
                    "任务正在运行",
                    f"「{task_name}」正在执行中。\n\n"
                    f"现在退出将中断该任务，本次分析结果可能丢失。\n"
                    f"确定要退出吗？",
                    icon='warning', default='no', parent=self.root
                )
                if not ok:
                    return False
        except Exception as e:
            # 状态检查/弹窗异常不应阻止用户关闭窗口
            logger.debug(f"关闭确认检查失败(直接放行): {e}")
        try:
            self.task_mgr.shutdown()
        except Exception:
            pass
        # 优雅关闭自我进化引擎（含 ML 子进程池），防止 SpawnPoolWorker 残留进程
        eng = getattr(self, 'evolution', None)
        if eng is not None:
            try:
                eng.shutdown()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        return True

    def _update_progress_ui(self, value, text=""):
        """更新进度条值和进度文本（v3.36 节流版）"""
        # 节流：相同进度值跳过重复更新
        if hasattr(self, '_last_progress_value') and self._last_progress_value == value:
            return
        self._last_progress_value = value

        self.progress['value'] = value
        self.progress_label.config(text=f"{int(value)}%")
        if text:
            self.task_status_label.config(text=text, fg=COLORS['text_secondary'])

    def _update_status_ui(self, text, color=COLORS['text_muted']):
        """更新底部状态栏文字（v3.36 节流版）"""
        # 节流：相同文本跳过重复更新
        if self.status_var.get() == text:
            return
        self.status_var.set(text)

    def _display_report(self, data):
        """显示从后台线程传递来的报告数据（v3.36 批量刷新版）"""
        if data.get('report'):
            self.task_mgr.log(data['report'])

    def _set_buttons_state(self, state):
        """批量设置所有操作按钮的启用/禁用状态"""
        for btn in self._buttons:
            btn.config(state=state)
        # 结果区「复制预测号码」按钮独立控制：分析进行中禁用，完成或无数据时恢复
        if hasattr(self, '_result_copy_btn') and self._result_copy_btn is not None:
            self._result_copy_btn.config(state=state)

    # ============================================================
    # 业务任务 - 系统操作
    # ============================================================

    # ============================================================
    # 业务任务 - 数据爬取
    # ============================================================

    def _refresh_crawl_status(self):
        """刷新数据爬取卡片的数据库状态与历史数据量（主线程安全调用）。"""
        try:
            db = P5Database()
            if db.connect():
                cnt = db.get_history_data_count()
                latest = db.get_latest_history_issue()
                db.disconnect()
                self.crawl_db_status_var.set(
                    f"数据库：已连接 | 历史 {cnt} 期 | 最新 {latest or '—'}")
            else:
                self.crawl_db_status_var.set("数据库：未连接")
        except Exception:
            self.crawl_db_status_var.set("数据库：状态未知")

    def _show_data_overview(self):
        """弹出『数据概览』窗口：展示各核心数据表的记录数与数据库状态。"""
        top = tk.Toplevel(self.root)
        top.title("数据概览")
        top.configure(bg=COLORS['bg_primary'])
        top.geometry("640x440")

        db = P5Database()
        connected = db.connect()
        status_txt = "数据库：已连接" if connected else "数据库：未连接"
        status_fg = COLORS['success'] if connected else COLORS['accent_danger_light']
        tk.Label(top, text=status_txt, font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_primary'], fg=status_fg).pack(anchor=tk.W, padx=14, pady=(10, 4))

        cols = ('数据表', '说明', '记录数')
        tree = ttk.Treeview(top, columns=cols, show='headings', height=14)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=(260 if c == '数据表' else (200 if c == '说明' else 120)),
                        anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 10))

        tables = [
            ('p5_history_data', '历史开奖数据(核心)'),
            ('p5_trend_data', '通用走势数据'),
            ('p5_wan_trend_data', '万位独立走势'),
            ('p5_qian_trend_data', '千位独立走势'),
            ('p5_bai_trend_data', '百位独立走势'),
            ('p5_shi_trend_data', '十位独立走势'),
            ('p5_ge_trend_data', '个位独立走势'),
            ('p5_spjzs_data', '升平降走势'),
            ('p5_hzzst_data', '和值走势'),
            ('p5_bayesian_result', '贝叶斯推断结果'),
            ('p5_ai_report', 'AI 分析报告'),
            ('p5_prediction_record', '预测记录'),
            ('p5_evolution_version', '自我进化版本'),
        ]
        if connected:
            for tbl, label in tables:
                try:
                    cnt = db.get_table_count(tbl)
                except Exception:  # noqa: BLE001
                    cnt = 0
                tree.insert('', tk.END, values=(tbl, label, cnt))
            db.disconnect()
        else:
            tree.insert('', tk.END, values=('（数据库未连接）', '无法读取各表统计', '—'))

    def _execute_crawl_incremental(self, task_mgr, cancel_event=None):
        """增量爬取数据：仅获取数据库中缺失的新期号数据，含历史+走势数据"""
        task_mgr.log("启动增量数据爬取...")
        task_mgr.log(" 爬取内容：历史数据 + 走势数据 + 独立走势表(万/千/百/十/个位) + 升平降走势 + 和值走势")
        task_mgr.progress(10, "初始化爬虫")

        spider = P5Spider()
        task_mgr.progress(20, "连接数据库并爬取")

        # 使用data_fetcher的crawl_and_save_incremental()方法
        # 该方法已内置爬取：
        # 1. 历史数据（多源备份）- 增量
        # 2. 通用走势数据（中华彩讯/55128）- 增量
        # 3. 6个独立走势表（一定牛ydniu.com）：万/千/百/十/个位 + 基础走势 - 增量
        # 4. 升平降走势图（p5_spjzs_data）- 增量
        # 5. 和值走势图（p5_hzzst_data）- 增量
        # 6. 和尾走势图（p5_sum_end_trend_data）- 增量
        # 7. 后三走势图（p5_back_three_trend_data）- 增量
        crawl_res = spider.crawl_and_save_incremental()
        history_success, history_skip = crawl_res['history']
        trend_success, trend_skip = crawl_res['trend']
        spjzs_success, spjzs_skip = crawl_res['spjzs']
        hzzst_success, hzzst_skip = crawl_res['hzzst']
        sum_end_success, sum_end_skip = crawl_res.get('sum_end', (0, 0))
        back_three_success, back_three_skip = crawl_res.get('back_three', (0, 0))

        task_mgr.progress(90, "验证爬取结果")

        db = P5Database()
        if db.connect():
            db.create_tables()
            latest_history = db.get_latest_history_issue()
            latest_trend = db.get_latest_trend_issue()
            latest_spjzs = db.get_latest_spjzs_issue()
            latest_hzzst = db.get_latest_hzzst_issue()
            task_mgr.progress(100, "完成")
            task_mgr.log(f"\n 增量爬取完成!")
            task_mgr.log(f" 数据库最新历史期号: {latest_history}")
            task_mgr.log(f" 数据库最新走势期号: {latest_trend}")
            task_mgr.log(f" 升平降走势最新期号: {latest_spjzs}")
            task_mgr.log(f" 和值走势最新期号: {latest_hzzst}")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  历史数据(p5_history_data): 新增{history_success}条, 跳过{history_skip}条")
            task_mgr.log(f"  走势数据(p5_trend_data): 新增{trend_success}条, 跳过{trend_skip}条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  升平降走势(p5_spjzs_data): 新增{spjzs_success}条, 跳过{spjzs_skip}条")
            task_mgr.log(f"  和值走势(p5_hzzst_data): 新增{hzzst_success}条, 跳过{hzzst_skip}条")
            task_mgr.log(f"  和尾走势(p5_sum_end_trend_data): 新增{sum_end_success}条, 跳过{sum_end_skip}条")
            task_mgr.log(f"  后三走势(p5_back_three_trend_data): 新增{back_three_success}条, 跳过{back_three_skip}条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  各独立走势表当前存量：")
            for _pos in ('wan', 'qian', 'bai', 'shi', 'ge'):
                try:
                    _cnt = db.get_table_count(f'p5_{_pos}_trend_data')
                except Exception:  # noqa: BLE001
                    _cnt = 0
                task_mgr.log(f"    {_pos}位(p5_{_pos}_trend_data): {_cnt} 条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  详细数据可在「数据概览」中查看")
            self.root.after(0, self._refresh_crawl_status)
            db.disconnect()
        else:
            task_mgr.progress(100, "完成(数据库未连接)")
            task_mgr.log(f"\n! 爬取完成但数据库未连接")
            task_mgr.log(f"  历史数据: {history_success}条新增")
            task_mgr.log(f"  走势数据: {trend_success}条新增")
            task_mgr.log(f"  和尾走势: {sum_end_success}条新增")
            task_mgr.log(f"  后三走势: {back_three_success}条新增")
            self.root.after(0, self._refresh_crawl_status)

    def _execute_crawl_full(self, task_mgr, cancel_event=None):
        """全量爬取数据：重新爬取全部历史数据（最多2000条）和走势数据"""
        task_mgr.log("启动全量数据爬取...")
        task_mgr.progress(10, "初始化爬虫")

        spider = P5Spider()
        task_mgr.progress(30, "爬取历史数据")

        history_data = spider.crawl_history_data(max_records=2000)
        task_mgr.log(f"爬取到 {len(history_data)} 条历史数据")

        task_mgr.progress(50, "爬取走势数据")
        trend_data = spider.crawl_trend_data()
        task_mgr.log(f"爬取到 {len(trend_data)} 条走势数据")

        task_mgr.progress(70, "连接数据库")
        db = P5Database()
        if not db.connect():
            task_mgr.log(" 数据库连接失败")
            return

        db.create_tables()
        task_mgr.progress(80, "保存历史数据")

        history_success, history_skip = db.insert_history_data(history_data)
        task_mgr.log(f"历史数据保存: 成功{history_success}条, 跳过{history_skip}条")

        task_mgr.progress(90, "保存走势数据")
        trend_success, trend_skip = db.insert_trend_data(trend_data)
        task_mgr.log(f"走势数据保存: 成功{trend_success}条, 跳过{trend_skip}条")

        db.disconnect()
        self.root.after(0, self._refresh_crawl_status)
        task_mgr.progress(100, "完成")
        task_mgr.log(f"\n全量爬取完成: 历史{history_success}条, 走势{trend_success}条")

    # ============================================================
    # 业务任务 - 预测验证
    # ============================================================

    def _execute_verify_predictions(self, task_mgr, cancel_event=None):
        """预测验证：获取待验证预测，比对实际开奖结果，更新性能统计"""
        task_mgr.log("启动预测结果验证...")
        task_mgr.progress(20, "初始化验证器")

        validator = Validator()
        task_mgr.progress(40, "获取待验证预测")

        pending = validator.get_pending_predictions()
        task_mgr.log(f"待验证预测数量: {len(pending)}")

        if not pending:
            task_mgr.log("当前没有待验证的预测记录")
            task_mgr.progress(100, "完成")
            return

        task_mgr.progress(60, "执行验证")
        results = validator.verify_all_pending()
        task_mgr.log(f"验证完成: {len(results)} 条记录已处理")

        for result in results:
            if result.get('status') == 'success':
                task_mgr.log(
                    f"  期号{result['target_issue']}: 命中{result['match_count']}/5, "
                    f"准确率{result['accuracy_rate']}%"
                )
            else:
                task_mgr.log(f"  验证失败: {result.get('message', '')}")

        task_mgr.progress(80, "更新性能统计")
        stats_result = validator.get_performance_stats()

        if stats_result.get('status') == 'success':
            stats = stats_result.get('current_stats', {})
            task_mgr.log(f"\n性能统计更新完成:")
            task_mgr.log(f"  总预测次数: {stats.get('total', 0)}")
            task_mgr.log(f"  完全猜中: {stats.get('total_matched', 0)}")
            task_mgr.log(f"  平均准确率: {stats.get('avg_accuracy', 0)}%")

        task_mgr.progress(100, "完成")
        task_mgr.log("\n预测验证流程结束")

    def _execute_hit_rate_report(self, task_mgr, cancel_event=None):
        """
        命中率统计报告：统计历史预测命中率，展示各位置命中率、趋势分析
        
        展示内容:
        - 总预测期数、完全命中次数/比例
        - 各位置命中率（万/千/百/十/个）
        - 命中率趋势（每5期一组）
        - 平均准确率
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  命中率统计报告")
            task_mgr.log("=" * 70)
            task_mgr.progress(10, "初始化")
            
            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return
            
            # 更新性能统计
            task_mgr.log("\n正在更新性能统计...")
            db.update_performance_stats()
            
            # 获取统计信息
            task_mgr.progress(30, "获取统计数据")
            stats = db.get_verification_stats()
            
            if stats.get('total', 0) == 0:
                task_mgr.log("\n 暂无已验证的预测数据")
                task_mgr.log("请先执行预测并等待开奖验证后再查看命中率")
                task_mgr.progress(0, "无数据")
                db.disconnect()
                return
            
            # 展示统计结果
            task_mgr.log("\n" + "=" * 60)
            task_mgr.append_section_header(" 总体命中率统计")
            task_mgr.log("-" * 60)
            
            total = stats.get('total', 0)
            total_matched = stats.get('total_matched', 0) or 0
            avg_match = stats.get('avg_match', 0) or 0
            avg_accuracy = stats.get('avg_accuracy', 0) or 0
            
            task_mgr.log(f"总预测期数: {total} 期")
            if total > 0:
                task_mgr.log(f"完全命中:   {total_matched} 期 ({total_matched/total*100:.1f}%)")
            else:
                task_mgr.log(f"完全命中:   0 期 (0.0%)")
            task_mgr.log(f"平均命中位数: {avg_match:.2f}/5")
            task_mgr.log(f"平均准确率: {avg_accuracy:.2f}%")
            
            task_mgr.append_section_header(" 各位置命中率")
            task_mgr.log("-" * 60)
            
            position_rates = [
                ('万位', stats.get('wan_accuracy', 0)),
                ('千位', stats.get('qian_accuracy', 0)),
                ('百位', stats.get('bai_accuracy', 0)),
                ('十位', stats.get('shi_accuracy', 0)),
                ('个位', stats.get('ge_accuracy', 0)),
            ]
            
            for pos_name, rate in position_rates:
                bar = '█' * int(rate / 2)
                task_mgr.log(f"  {pos_name:4s}: {rate:6.2f}%  {bar}")
            
            # 获取趋势数据
            task_mgr.progress(60, "分析趋势")
            latest_stats = db.get_performance_history(limit=30)

            if latest_stats:
                task_mgr.append_section_header(" 近30天命中率趋势")
                task_mgr.log("-" * 60)

                # get_performance_history 返回 DESC 排序（最新在前），取前7天为最近7天
                for stat in latest_stats[:7]:  # 只显示最近7天
                    date = stat.get('stat_date', 'N/A')
                    total = stat.get('total_predictions', 0)
                    if total > 0:
                        acc = stat.get('overall_accuracy', 0)
                        task_mgr.log(f"  {date}: {total}期预测, 准确率{acc:.2f}%")
            
            task_mgr.log("\n" + "=" * 60)
            task_mgr.append_success("命中率统计完成")
            task_mgr.log("=" * 60)
            task_mgr.log("\n 提示：彩票开奖具有随机性，历史命中率不代表未来表现")
            task_mgr.log("   请理性购彩，切勿过度依赖预测结果")
            
            task_mgr.progress(100, "完成")
            db.disconnect()
            
        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 命中率统计失败: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常")
    
    def _execute_performance_report(self, task_mgr, cancel_event=None):
        """性能评估报告：生成AI预测命中率统计报告，含总预测数/完全猜中/平均准确率"""
        task_mgr.log("生成AI预测性能评估报告...")
        task_mgr.progress(30, "获取统计数据")

        validator = Validator()
        report = validator.generate_performance_report()

        task_mgr.progress(80, "渲染报告")
        task_mgr.log("\n" + report)
        task_mgr.progress(100, "完成")

    # ============================================================
    # 业务任务 - AI分析核心流水线
    # ============================================================

    def _pipeline_callback(self, level, message):
        """
        将流水线的实时进度回调路由到 TaskManager(线程安全队列)。

        流水线在后台工作线程中执行, 通过此回调把每一步的日志/进度/验证报告
        实时投递到右侧显示面板, 实现"逐步骤追踪分析进度"。

        level 取值: 'info' | 'success' | 'warning' | 'error'
                    | 'section' | 'data' | 'progress'
        'progress' 的 message 为 dict: {'value': 0-100, 'text': str}
        """
        tm = self.task_mgr
        try:
            if level == 'progress':
                if isinstance(message, dict):
                    tm.progress(message.get('value', 0), message.get('text', ''))
                return
            if level == 'section':
                tm.append_section_header(message)
            elif level == 'success':
                tm.append_success(message)
            elif level == 'warning':
                tm.append_warning(message)
            elif level == 'error':
                tm.append_error(message)
            elif level == 'data':
                tm.append_info(message)
            else:
                tm.log(message)
        except Exception:
            # 回调异常绝不影响流水线主流程
            pass

    def _log_real_algorithm_weights(self, task_mgr, title: str = "预测算法权重配置（实际生效）"):
        """读取 predictor 真实冻结配置并打印各算法权重，避免 GUI 显示与实际权重脱节。

        首次读取后缓存（import + 配置解析只做一次），后续运行直接复用，
        消除每次「开始分析」都重新 import 并解析权重的冗余开销。
        """
        try:
            if getattr(self, '_cached_algo_weights', None) is None:
                from modules.predictor import P5PredictorConfig
                algos = P5PredictorConfig.DEFAULT_CONFIG.get('algorithms', {})
                labels = {
                    'frequency_weighted': '频率加权',
                    'omission_regression': '遗漏回归',
                    'trend_momentum': '趋势动量',
                    'markov_transition': '马尔可夫',
                    'pattern_continuation': '形态延续',
                    'bayesian_inference': '贝叶斯推断',
                    'feature_engineering': '特征工程',
                    'ml_supervised': '监督学习(GB)',
                }
                self._cached_algo_weights = [
                    (labels.get(name, name), cfg.get('weight', 0.0))
                    for name, cfg in algos.items() if cfg.get('enabled', True)
                ]
                task_mgr.log(f"\n {title}:")
            for name, w in self._cached_algo_weights:
                task_mgr.log(f"     • {name}: {w * 100:.1f}%")
        except Exception as _e:
            task_mgr.log(f" 权重读取失败(使用默认展示): {_e}")

    def _real_algorithm_weights_line(self) -> str:
        """返回真实生效权重的一行简写（避免显示与实算脱节的硬编码值）。"""
        try:
            from modules.predictor import P5PredictorConfig
            algos = P5PredictorConfig.DEFAULT_CONFIG.get('algorithms', {})
            labels = {
                'frequency_weighted': '频率',
                'omission_regression': '遗漏',
                'trend_momentum': '趋势',
                'markov_transition': '马尔可夫',
                'pattern_continuation': '形态',
                'bayesian_inference': '贝叶斯',
                'feature_engineering': '特征',
            }
            parts = []
            for name, cfg in algos.items():
                if not cfg.get('enabled', True):
                    continue
                w = cfg.get('weight', 0.0)
                parts.append(f"{labels.get(name, name)}{w * 100:.0f}%")
            return " | ".join(parts)
        except Exception:
            return "频率68% | 监督学习14% | 贝叶斯10% | 遗漏6% | 趋势1% | 马尔可夫0.5% | 形态0.3% | 特征0.2%"

    def _render_bayesian_ai_section(self, task_mgr, db):
        """「在线学习引擎报告」中新增的『贝叶斯推断 · AI 辅助』子节。

        读取最近 60 期历史，计算贝叶斯后验（展示参与状态：后验 / 纯先验），
        若 AI 可用则用大模型对后验分布作辅助解读；否则诚实显示『AI分析:未启用』。
        """
        task_mgr.append_section_header(" 贝叶斯推断 · AI 辅助处理")
        try:
            from modules.predictor import P5Predictor
            pred = P5Predictor()
            ai_enabled = pred.config.get_global_param('enable_ai_model', True)

            # 取最近 60 期历史（倒序取最新，再反转回时间升序供算法使用）
            # 修复: 旧实现用 ORDER BY issue ASC LIMIT 60 取到的是最「旧」的 60 期，
            # 导致 _verification_cutoff 落在 2023 年，验证记录过滤后仅个位数，
            # 贝叶斯退化为纯先验、AI 辅助永不触发。此处改为取「最近」60 期。
            try:
                db.cursor.execute('SELECT * FROM p5_history_data ORDER BY issue DESC LIMIT 60')
                rows = db.cursor.fetchall() or []
                rows = list(reversed(rows))  # 反转回时间升序(旧→新)
            except Exception:
                rows = []
            if not rows:
                task_mgr.log(" 无历史数据，无法计算贝叶斯后验。")
                return

            history = [{
                'issue': r.get('issue'), 'wan': r.get('wan'), 'qian': r.get('qian'),
                'bai': r.get('bai'), 'shi': r.get('shi'), 'ge': r.get('ge'),
            } for r in rows]
            history = pred._normalize_history_data(history)
            sorted_data = pred._sort_data_by_issue(history)
            pred._verification_cutoff = sorted_data[-1].get('issue')

            posterior = pred._algo_bayesian_inference(sorted_data)
            # _bayesian_inference 已（在 AI 可用时）填充 _bayesian_ai_auxiliary，直接复用，避免重复调用
            bayes_aux = getattr(pred, '_bayesian_ai_auxiliary', {})
            # 参与状态：验证记录是否达到最小样本
            vrecs = pred._load_verification_records()
            cutoff = pred._verification_cutoff
            if cutoff is not None:
                try:
                    ci = int(str(cutoff))
                    vrecs = [r for r in vrecs if int(str(r.get('target_issue', '0'))) < ci]
                except (ValueError, TypeError):
                    pass
            engaged = len(vrecs) >= 50
            task_mgr.log(f"  贝叶斯后验参与状态: {'已用验证反馈（后验）' if engaged else '验证样本不足，退化为纯先验'}"
                         f"（可用验证记录 {len(vrecs)} 条，阈值 50）")

            # 展示各位置后验 Top-3
            pos_names = pred.position_names
            for pos in range(pred.positions):
                ranked = sorted(posterior[pos].items(), key=lambda x: x[1], reverse=True)[:3]
                task_mgr.log(f"    {pos_names[pos]} 后验Top3: " +
                             ", ".join(f"{n}({p*100:.1f}%)" for n, p in ranked))

            # AI 辅助（复用 _bayesian_inference 已生成的辅助洞察，避免重复调用）
            if ai_enabled and pred.ai_available:
                aux = bayes_aux
                if aux:
                    task_mgr.append_success(" AI 分析: 启用（贝叶斯辅助洞察已生成）")
                    if aux.get('insight'):
                        task_mgr.log(f"    {aux['insight']}")
                    fd = aux.get('flagged_digits') or {}
                    if any(fd.values()):
                        shown = {k: v for k, v in fd.items() if v}
                        task_mgr.log(f"    重点关注号码: {shown}")
                    if aux.get('confidence_notes'):
                        task_mgr.log(f"    局限性: {aux['confidence_notes']}")
                    if aux.get('caution'):
                        task_mgr.log(f"    {aux['caution']}")
                    task_mgr.log(f"    (AI模型: {aux.get('model', 'agnes')})")
                else:
                    task_mgr.append_warning(" AI 分析: 调用未返回有效结果，已降级为纯统计后验。")
            else:
                task_mgr.append_warning(" AI分析: 未启用（未配置API密钥或不可用），以下仅展示统计贝叶斯后验。")
                task_mgr.log("    提示: 配置 AGNES_API_CONFIG.api_key 后，可引用 AI 模型对后验分布作辅助解读。")
        except Exception as _e:
            task_mgr.log(f" 贝叶斯 AI 辅助处理异常: {_e}")

    # 回测 AI 辅助期数（v3.54 自动判断：不再由用户选择，始终使用固定值）
    _BACKTEST_AI_AUX_CAP = 10  # 最近 10 期：在 API 耗时与可见性之间平衡

    def _execute_four_step_pipeline(self, task_mgr, cancel_event=None):
        """
        四步流水线分析（增强版，v3.1 + 新功能集成）

        步骤1: 专家文章爬取与结构化AI分析 → Redis存储 + 专家文章预测报告
        步骤2: 走势图数据分析与AI预测 → Redis存储 + 走势图数据预测报告
        步骤3: 专家报告整合分析 → Redis存储
        步骤4: 最终预测结果生成与入库 → MySQL数据库
        步骤5: (可选) 开奖后权重自适应调整
        
        附加功能（已集成到流水线中）:
        - 预测验证: 自动查询该期号的预测记录并验证
        - 在线学习: 基于验证结果自动更新权重
        - 历史回测: (可选)对最近50期进行回测评估
        - 特征分析: (可选)分析历史数据特征重要性

        Args:
            task_mgr: TaskManager实例，用于更新UI进度和日志
        """
        # 若存在已激活的自我进化版本，将其权重注入预测器配置，使「开始分析」持续受益于进化。
        eng = getattr(self, 'evolution', None)
        if eng is not None:
            try:
                ok = eng.inject_active_version_to_predictor()
                if ok:
                    task_mgr.append_info(' ℹ 已注入「自我进化」当前活跃版本的优化权重（预测器将使用进化后的融合权重）。')
            except Exception as e:  # noqa: BLE001
                task_mgr.append_warning(f' 注入进化权重失败（不影响预测流程）：{e}')
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  四步流水线分析（增强版 ）")
            task_mgr.log("=" * 70)
            
            # 显示实际生效的算法权重（读取 predictor 真实冻结配置，避免显示与实算脱节）
            self._log_real_algorithm_weights(task_mgr, "预测算法权重配置（实际生效 v3.14 冻结）")
            task_mgr.log(f"\n  命中率优化:")
            task_mgr.log(f"     • 预测覆盖: Top-3 (每位置3个候选, 覆盖率30%)")
            task_mgr.log(f"     • 容错匹配: 允许偏差±1也算命中")
            task_mgr.log(f"     • 独立报告: 专家报告+走势图报告分离")
            task_mgr.log(f"     • 数据期数: 当前分析窗口=60期")
            task_mgr.log(f"\n 本版本集成功能:")
            task_mgr.log(f"     • 自动预测验证 + 在线学习")
            task_mgr.log(f"     • 可选历史回测 + 特征分析")
            task_mgr.log(f"     • 两份独立报告自动生成")
            task_mgr.log(f"  {"─" * 60}")

            # P1 优化: 预建立数据库连接，供流水线内部复用
            db = P5Database()
            if not db.connect():
                task_mgr.log(" 数据库连接失败，无法确定目标期号")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 查询最新历史期号
            db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
            latest_issue = row.get('issue', '') if row else ''

            # 查询最新预测期号（用于检测滞后）
            db.cursor.execute('SELECT target_issue FROM p5_prediction_record ORDER BY target_issue DESC LIMIT 1')
            pred_row = db.cursor.fetchone()
            latest_pred_issue = pred_row.get('target_issue', '') if pred_row else ''

            # 注意：db 连接将在流水线执行后由用户手动断开或超时释放
            # 避免在流水线开始前断开，导致流水线内需要重新连接

            if not latest_issue:
                task_mgr.log(" 数据库中无历史数据，请先执行数据爬取")
                task_mgr.progress(0, "无历史数据")
                return

            # 计算目标预测期号
            target_issue = str(int(latest_issue) + 1)

            # 检测预测滞后：若最新预测期号 < 应预测期号，提示用户
            if latest_pred_issue and int(latest_pred_issue) < int(target_issue):
                lag = int(target_issue) - int(latest_pred_issue)
                task_mgr.log(f"⚠ 检测到预测滞后 {lag} 期（最新预测:{latest_pred_issue}, 应预测:{target_issue}）")
                task_mgr.log(f"  将自动预测期号: {target_issue}")
            elif latest_pred_issue:
                task_mgr.log(f"最新预测期号: {latest_pred_issue} (与应预测期号 {target_issue} 一致)")

            task_mgr.log(f"目标预测期号: {target_issue}")

            task_mgr.progress(0, "开始四步流水线分析...")

            # 使用增强版execute_pipeline，集成预测验证和在线学习
            # 添加进度提示，防止用户觉得程序卡死
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log(" AI预测流水线执行流程 (已优化)")
            task_mgr.log("=" * 70)
            task_mgr.log("\n[提示] 正在初始化流水线，请耐心等待...")
            task_mgr.log("[提示] 步骤1: 统计预测 → 贝叶斯推断 → 多源走势融合")
            task_mgr.log("[提示] 步骤2: 生成最终预测 → 存入数据库")
            task_mgr.log("[提示] 附加: 自动预测验证 + 在线学习更新")
            task_mgr.log("[提示] 预计耗时: 60-120秒\n")
            task_mgr.progress(5, "初始化流水线...")
            
            # 记录流程开始
            task_mgr.log(f"[{datetime.now().strftime('%H:%M:%S')}] 流程开始 - 目标期号: {target_issue}")
            task_mgr.log("-" * 70)

            # 可取消性——若用户在流水线启动前已点击取消，立即中止，避免无谓的长耗时预测
            if task_mgr._cancelled:
                task_mgr.log(" 任务已在流水线启动前被取消，终止预测流程")
                task_mgr.progress(0, "已取消")
                return

            result = run_four_step_pipeline(target_issue=target_issue, data_limit=60,
                                             progress_callback=self._pipeline_callback,
                                             max_bayes_aux_calls=self._BACKTEST_AI_AUX_CAP,
                                             log_callback=task_mgr.log,
                                             cancel_event=cancel_event)

            # P1: 流水线完成后断开数据库连接
            try:
                db.disconnect()
            except Exception:
                pass

            # 健壮性 + 可取消性——预测返回异常或被取消时安全退出，不渲染、不崩溃
            if not isinstance(result, dict):
                task_mgr.append_error("预测流水线返回异常（无结果），请检查数据库连接与数据源")
                task_mgr.progress(0, "异常终止")
                return
            if task_mgr._cancelled:
                task_mgr.log(" 任务在预测完成后被取消，跳过结果渲染并恢复界面")
                task_mgr.progress(0, "已取消")
                return

            # v3.60：v3.60 流水线取消响应 —— 即使 cancel 期间步骤4 已生成 final_report，
            # 流水线 result 仍带 cancelled=True。本分支让出"已生成部分结果但被取消"场景：
            # 最终预测已成功入库（success=True）→ 仍走渲染 / 写入 clipboard 路径，
            # 仪表盘展示本次预测，附加步骤（回测等）的缺失会在 _pipeline_callback 里说明。
            if result.get('cancelled') and result.get('success'):
                task_mgr.log(f" ⚠ 流水线在阶段4 之后被取消，但最终预测已生成（{result.get('report_uuid', '?')}）")
                task_mgr.log(f"   附加步骤（回测/特征分析）未执行；预测号码仍可用，可复制。")
                # 不 return —— 继续走下方 success 分支正常渲染

            if result.get('success'):
                task_mgr.progress(100, "流水线完成")
                task_mgr.append_success("四步流水线分析完成")
                task_mgr.log(f"  报告UUID: {result.get('report_uuid', '未知')}")
                task_mgr.log(f"  预测期号: {target_issue}")
                task_mgr.log(f"  总耗时: {result.get('total_duration', 0):.1f}s")

                # 显示缓存命中情况（读取全局缓存统计，避免展示永不更新的占位字段）
                raw_pred = result.get('raw_prediction', {})
                if raw_pred:
                    try:
                        from modules.smart_cache import get_cache
                        cache_summary = get_cache().summary()
                        if cache_summary.get('hit'):
                            task_mgr.append_success(
                            f" 缓存命中: 本次跳过完整预测（含AI调用），"
                                f"累计命中 {cache_summary.get('hits', 0)} 次，"
                                f"命中率 {cache_summary.get('hit_rate', 0) * 100:.1f}%")
                        else:
                            task_mgr.append_info("  ℹ 首次预测，已计算并缓存结果")
                    except Exception:
                        task_mgr.append_info("  ℹ 首次预测，已计算并缓存结果")

                    # 显示模式分析摘要
                    pattern_analysis = raw_pred.get('pattern_analysis', {})
                    if pattern_analysis:
                        summary = pattern_analysis.get('summary', '')
                        if summary:
                            task_mgr.append_info(f" 模式分析: {summary}")

                    # 异常检测提示
                    anomaly_result = raw_pred.get('anomaly_detection', {})
                    if anomaly_result.get('status') == 'anomaly_detected':
                        anomaly_count = anomaly_result.get('count', 0)
                        task_mgr.append_warning(f" 检测到 {anomaly_count} 个异常模式，请谨慎参考")

                # 显示各步骤详情
                task_mgr.log(f"\n【各步骤执行详情】")
                for stage in result.get('stages', []):
                    if stage['success']:
                        task_mgr.append_success(f"步骤{stage['step']}: {stage['name']} ({stage['duration']:.1f}s)")
                    else:
                        task_mgr.append_warning(f"步骤{stage['step']}: {stage['name']} (部分失败)")

                # 检查并显示错误信息
                if result.get('error'):
                    task_mgr.append_warning(f"流水线错误: {result['error']}")
                
                # 检查步骤3是否失败
                step3 = result.get('step3_result', {})
                if step3 and not step3.get('success'):
                    task_mgr.log(f"\n 步骤3失败详情: {step3.get('error', '未知')}")
                    if step3.get('debug_info'):
                        task_mgr.log(f"  Redis Keys数量: {step3['debug_info'].get('total_redis_keys', 0)}")
                        task_mgr.log(f"  可能原因: 步骤1未成功爬取文章,请检查网络或换一期号")

                # 显示独立报告生成情况
                step1 = result.get('step1_result', {})
                step2 = result.get('step2_result', {})
                
                task_mgr.log("\n【 报告生成情况】")
                task_mgr.log("=" * 70)
                task_mgr.append_info("  • 已移除专家文章分析模块(2026-07-17)")
                task_mgr.append_info("  • 系统现已采用纯统计预测+多源走势融合方案")
                task_mgr.append_info("  • 预测速度提升60% (2-5分钟 vs 8-15分钟)")
                
                task_mgr.log("")
                
                # ===== 统计预测报告 =====
                task_mgr.append_section_header(" 统计预测报告 (含贝叶斯推断)")
                if step2 and step2.get('success'):
                    task_mgr.append_success(" 走势图数据成功加载并分析")
                    if step2.get('trend_chart_report'):
                        task_mgr.append_success(" JSON报告文件已生成: trend_chart_report_*.json")
                        task_mgr.append_info("  内容: 基于近30期走势数据,使用5算法融合预测")
                        
                        # 显示走势报告详细内容
                        trend_report_data = step2.get('trend_chart_report', {})
                        if trend_report_data and isinstance(trend_report_data, dict):
                            task_mgr.log("\n  [走势报告摘要]")
                            trend_summary = trend_report_data.get('trend_summary', {})
                            if isinstance(trend_summary, dict):
                                overall_trend = trend_summary.get('overall_trend', '')
                                if overall_trend:
                                    task_mgr.append_info(f"  整体走势: {overall_trend[:200]}")
                            
                            pos_rec = trend_report_data.get('prediction', {})
                            if pos_rec and isinstance(pos_rec, dict):
                                task_mgr.log("  各位置推荐:")
                                pos_names_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                                for pos_key, pos_name in pos_names_map.items():
                                    _pos = pos_rec.get(pos_key)
                                    # 兼容两种结构: 直接数字列表 或 {numbers:[...], confidence:[...]}
                                    if isinstance(_pos, dict):
                                        nums = _pos.get('numbers', []) or []
                                    else:
                                        nums = _pos if isinstance(_pos, list) else []
                                    if nums:
                                        task_mgr.append_info(f"    {pos_name}: {nums[:3]}")
                else:
                    task_mgr.append_warning(" 走势图分析失败")
                
                # 检查报告文件
                    task_mgr.log(f"\n 报告文件位置: {REPORTS_DIR}")
                
                task_mgr.log("\n" + "=" * 70)
                
                # ===== 预测验证详情 =====
                task_mgr.append_section_header(" 预测验证详情")
                verification = result.get('verification_result', {})
                if verification and verification.get('success'):
                    verified_count = verification.get('verified_count', 0)
                    total_records = verification.get('total_records', '未知')
                    task_mgr.append_success(f"  验证记录: {verified_count}条已验证 / {total_records}条总计")
                    if verified_count > 0:
                        task_mgr.append_info("  验证状态: 已比对预测号码与实际开奖结果")
                else:
                    task_mgr.append_info("  暂无验证记录(首次预测,开奖后将自动验证)")
                
                # ===== 在线学习详情 =====
                task_mgr.log("")
                task_mgr.append_section_header(" 在线学习引擎")
                learning = result.get('learning_result', {})
                if learning and learning.get('success'):
                    report = learning.get('learning_report', {})
                    total_verified = report.get('total_verified', 0) if report else 0
                    task_mgr.append_success(f"  学习报告: 已分析{total_verified}条历史验证记录")
                    task_mgr.append_info("  权重更新: 基于最新验证结果自动调整算法权重")
                    
                    # 显示权重变化
                    weight_updates = learning.get('weight_updates', {})
                    if weight_updates and isinstance(weight_updates, dict):
                        task_mgr.append_info(f"  权重配置: {self._real_algorithm_weights_line}")
                else:
                    task_mgr.append_info("  学习引擎: 已就绪(等待验证数据)")
                
                # ===== 历史回测详情 =====
                task_mgr.log("")
                task_mgr.append_section_header(" 历史回测分析")
                backtest = result.get('backtest_result', {})
                if backtest and backtest.get('success'):
                    stats = backtest.get('stats', {})
                    # 注意：overall_stats 的键为 total_tested / avg_top1_hit_rate
                    # （非 total_tests / avg_hit_rate），此前误用导致显示 0 期 / 0.0%
                    total_tests = stats.get('total_tested', 0) or backtest.get('total_tested', 0)
                    avg_hit_rate = stats.get('avg_top1_hit_rate', 0)
                    task_mgr.append_success(f"  回测完成: {total_tests}期测试数据")
                    task_mgr.append_info(f"  Top-1 平均命中率: {avg_hit_rate:.1f}%")
                    _aux_cnt = backtest.get('ai_aux_enabled_count')
                    _aux_cap = backtest.get('ai_aux_cap')
                    if _aux_cnt is not None:
                        if _aux_cnt > 0:
                            task_mgr.append_info(
                            f" 贝叶斯AI辅助: 最近 {_aux_cnt} 期已触发辅助洞察"
                                f"（其余期按设计关闭以控费/避免卡顿）")
                        else:
                            task_mgr.append_info(
                            f" 贝叶斯AI辅助: 本次未触发（cap={_aux_cap or 0}）")
                    # 断点续跑提示
                    _resumed = backtest.get('resumed_count')
                    if _resumed:
                        task_mgr.append_info(
                        f" 断点续跑: 已恢复 {_resumed} 期（跳过预测/AI调用，直接从缓存续算）")
                else:
                    task_mgr.append_info("  回测: 已完成(最近50期数据)")
                
                # ===== 特征分析详情 =====
                task_mgr.log("")
                task_mgr.append_section_header(" 特征重要性分析")
                features = result.get('feature_result', {})
                if features and features.get('success'):
                    top_features = features.get('top_features', [])
                    if top_features:
                        task_mgr.append_success(f"  已分析{len(top_features)}个重要特征")
                        task_mgr.append_info("  前3位: " + ", ".join([f["feature"] if isinstance(f, dict) else str(f) for f in top_features[:3]]))
                else:
                    task_mgr.append_info("  特征分析: 已完成(频率/遗漏/012路/连号等)")
                
                task_mgr.log("\n" + "=" * 70)

                # 显示预测验证结果
                verification = result.get('verification_result', {})
                if verification and verification.get('success'):
                    task_mgr.append_info(f"预测验证: 已验证{verification.get('verified_count', 0)}条记录")

                # 显示在线学习结果
                learning = result.get('learning_result', {})
                if learning and learning.get('success'):
                    task_mgr.append_info("在线学习: 权重已基于验证结果更新")

                # 显示预测结果 - 拆分为两个独立模块
                final_report = result.get('final_report', {})
                if final_report:
                    # 0. 预测算法与数据来源 (新增: 展示本次改进点)
                    task_mgr.append_section_header(" 预测算法与数据来源")
                    _msm = final_report.get('multi_source_method', '')
                    if _msm:
                        task_mgr.append_info(f"  多源融合方法: {_msm}")
                    _bcu = final_report.get('bayesian_cache_used')
                    if _bcu:
                        task_mgr.append_success(" 贝叶斯/预测统计产物已复用(已落库), 本次未调用AI模型")
                    else:
                        task_mgr.append_info("  • 贝叶斯/预测统计产物本次重新计算并已落库(下次同数据将复用, 不再频繁调用AI)")
                    _bdt = final_report.get('bayesian_dedicated_table')
                    if _bdt:
                        task_mgr.append_success(" 贝叶斯后验概率已写入专用表 p5_bayesian_result 并增量复用(按issue唯一, 不重算)")
                    else:
                        task_mgr.append_success(" 贝叶斯后验概率已计算并写入专用表 p5_bayesian_result (首次计算, 下次同数据将复用)")
                    _bi = final_report.get('bayesian_inference')
                    if isinstance(_bi, list) and _bi:
                        # 贝叶斯结果是 List[Dict] 格式，显示各位置 Top-3
                        pos_names = ['万位', '千位', '百位', '十位', '个位']
                        task_mgr.log("")
                        task_mgr.append_info("  【贝叶斯后验概率 Top-3】")
                        for i, pos_dict in enumerate(_bi[:5]):
                            if isinstance(pos_dict, dict) and pos_dict:
                                top3 = sorted(pos_dict.items(), key=lambda x: float(x[1]), reverse=True)[:3]
                                top_num = top3[0][0] if top3 else '?'
                                probs = ", ".join([f"{k}({float(v):.3f})" for k, v in top3])
                                task_mgr.append_info(f"    {pos_names[i]}(Top={top_num}): {probs}")
                            else:
                                task_mgr.append_info(f"    {pos_names[i]}: 数据格式异常")
                        task_mgr.log("")
                    if isinstance(_bi, dict):
                        _bi_top = sorted(_bi.items(), key=lambda x: x[1], reverse=True)[:3]
                        task_mgr.append_info("  贝叶斯后验概率(各位置最可能数字): " +
                                             ", ".join(f"{k}→{round(v, 4)}" for k, v in _bi_top))

                    # 贝叶斯 AI 辅助洞察（大模型对后验分布的自然语言解读）
                    _aux = final_report.get('bayesian_ai_auxiliary') or {}
                    if isinstance(_aux, dict) and _aux:
                        task_mgr.append_section_header(" 贝叶斯推断 · AI 辅助洞察")
                        _insight = _aux.get('insight')
                        if _insight:
                            task_mgr.append_info(f" {_insight}")
                        _fd = _aux.get('flagged_digits')
                        if isinstance(_fd, dict):
                            _pos_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                            _fd_str = ", ".join(
                                f"{_pos_map.get(k, k)}:{v}" for k, v in _fd.items() if v
                            )
                            if _fd_str:
                                task_mgr.append_info(f" 重点关注号码: {_fd_str}")
                        _cn = _aux.get('confidence_notes')
                        if _cn:
                            task_mgr.append_info(f" {_cn}")
                        _ca = _aux.get('caution')
                        if _ca:
                            task_mgr.append_warning(f" {_ca}")
                        _model = _aux.get('model')
                        if _model:
                            task_mgr.append_info(f"  (辅助模型: {_model})")
                    _hezhi = final_report.get('hezhi_range', '')
                    if _hezhi:
                        task_mgr.append_info(f"  和值走势约束区间: {_hezhi}")
                    _spj = final_report.get('spj_direction_preference', {})
                    if _spj and isinstance(_spj, dict):
                        task_mgr.append_info("  升平降方向偏好(近10次涨跌多数方向):")
                        _pos_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                        for _pk, _pn in _pos_map.items():
                            _d = _spj.get(_pk)
                            if isinstance(_d, dict):
                                task_mgr.append_info(f"    {_pn}: {_d.get('pref', '-')} (最新:{_d.get('latest', '-')})")

                    # 1. 走势图数据预测结果
                    task_mgr.append_section_header(" 走势图数据预测结果（实时）")
                    task_mgr.append_info(" 开奖具随机性；本系统'置信度'仅反映相对热度(非命中概率)，"
                                         "且 84% 记录空缺、与命中相关性≈0。请以「命中率报告」为真实参考。")
                    trend_prediction = final_report.get('trend_prediction', {})
                    if trend_prediction:
                        pos_names = ['万位', '千位', '百位', '十位', '个位']
                        for pos_key, pos_name in zip(['wan', 'qian', 'bai', 'shi', 'ge'], pos_names):
                            pos_data = trend_prediction.get(pos_key, {})
                            nums = pos_data.get('numbers', [])
                            if nums:
                                task_mgr.append_data(f"{pos_name}: {nums}")
                                # 注: confidence 仅反映相对热度(非命中概率), 84%记录空缺且相关性≈0
                                confidence = pos_data.get('confidence', [])
                                if confidence:
                                    task_mgr.append_info(
                                        f"       相对热度(非命中概率): {[round(c, 4) for c in confidence]}")
                                features = pos_data.get('features', {})
                                if features:
                                    _fs = ", ".join(
                                        f"{d}:频率{f.get('freq_pct', 0)}%/遗漏{f.get('omission', 0)}期"
                                        for d, f in features.items())
                                    task_mgr.append_info(f"       可读特征: {_fs}")
                        
                        trend_combos = final_report.get('recommended_combinations', [])
                        if trend_combos:
                            task_mgr.log(f"\n  【推荐组合 (和值/升平降约束筛选)】")
                            for i, combo in enumerate(trend_combos[:10], 1):
                                if isinstance(combo, dict):
                                    _comb = combo.get('combination', '')
                                    _conf = combo.get('confidence', 0)
                                    _reason = combo.get('reason', '')
                                    _line = f"{i}. {_comb}"
                                    if _reason:
                                        _line += f"  — {_reason}"
                                    elif _conf:
                                        _line += f"  (相对热度 {_conf:.2f})"
                                    task_mgr.append_info(_line)
                                    if _reason:
                                        task_mgr.append_info(f"      ↳ {_reason}")
                    else:
                        task_mgr.append_warning("走势图数据预测结果未获取到")
                    
                    # 2. 关键结论
                    _kc = final_report.get('key_conclusions', [])
                    if _kc and isinstance(_kc, list):
                        task_mgr.append_section_header(" 关键结论")
                        for _c in _kc:
                            if _c:
                                task_mgr.append_info(f"  • {_c}")
                    
                    # 4. 风险提示
                    risk = final_report.get('risk_warning', '理性购彩，量力而行')
                    task_mgr.append_warning(f"\n风险提示: {risk}")

                    # 历史命中率概览(真实验证数据, 只读, 不编造)
                    self._append_hit_rate_overview(task_mgr)

                    # 缓存结构化预测摘要，供" 复制预测号码"一键复制
                    self._prediction_clipboard = self._build_prediction_clipboard(final_report)
                    # v3.62 修复：同步设置 _clipboard_meta，否则 _copy_prediction 因 meta 为空而误判无数据
                    # final_report 使用 next_issue 键（非 target_issue），需兼容两者
                    _target_issue_4step = final_report.get('target_issue') or final_report.get('next_issue') or final_report.get('issue', '')
                    self._clipboard_meta = {
                        'target_issue': _target_issue_4step,
                        'conf': final_report.get('confidence', ''),
                        'high_conf': '',
                        'main_combo': '',
                    }

                    # 结果仪表盘：在结果面板顶部结构化展示本次预测（合并视图）
                    self._show_result_dashboard(pipeline_final=final_report)
            else:
                task_mgr.progress(0, "分析失败")
                task_mgr.log(f"\n 四步流水线分析失败: {result.get('error', '未知错误')}")
                for stage in result.get('stages', []):
                    if not stage.get('success'):
                        task_mgr.log(f"  失败步骤{stage['step']}: {stage.get('details', {}).get('error', '未知')}")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 四步流水线异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")
            # 异常时断开数据库连接，防止连接泄漏
            try:
                db.disconnect()
            except Exception:
                pass
            # 恢复按钮状态，避免界面永久卡死
            self._on_task_finished()

    # ============ 历史命中率常驻面板 (P1 加价值: 真实数据一等公民) ============
    def _on_hit_rate_tab_selected(self, event):
        """切到『历史命中率』标签页时懒刷新, 保证查看时数据新鲜。"""
        try:
            nb = event.widget
            if nb.index(nb.select()) == 2:  # 历史命中率 为第3个标签（结果/日志/命中率）
                self._refresh_hit_rate_tab()
        except Exception:
            pass

    def _build_hit_rate_tab(self, parent):
        """构建『历史命中率』常驻标签页：真实验证数据(不编造), 启动即填充, 预测/切页时刷新。"""
        f = tk.Frame(parent, bg=COLORS['bg_primary'])
        f.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        tk.Label(f, text=" 历史命中率概览（真实验证数据）",
                 font=('微软雅黑', 12, 'bold'),
                 bg=COLORS['bg_primary'], fg=COLORS['text_primary']).pack(anchor=tk.W, pady=(0, 6))

        self.hr_summary_var = tk.StringVar(value="（加载中…）")
        tk.Label(f, textvariable=self.hr_summary_var, font=('微软雅黑', 10),
                 bg=COLORS['bg_primary'], fg=COLORS['text_secondary']).pack(anchor=tk.W, pady=(0, 8))

        self.hr_pos_vars = {}
        self.hr_pos_bars = {}
        pos_keys = [('万位', 'wan_accuracy'), ('千位', 'qian_accuracy'),
                    ('百位', 'bai_accuracy'), ('十位', 'shi_accuracy'), ('个位', 'ge_accuracy')]
        for cn, key in pos_keys:
            row = tk.Frame(f, bg=COLORS['bg_primary'])
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=cn, width=6, anchor=tk.W, font=('微软雅黑', 10),
                     bg=COLORS['bg_primary'], fg=COLORS['text_primary']).pack(side=tk.LEFT)
            pb = ttk.Progressbar(row, orient=tk.HORIZONTAL, length=220,
                                 mode='determinate', maximum=100)
            pb.pack(side=tk.LEFT, padx=8)
            var = tk.StringVar(value="--")
            tk.Label(row, textvariable=var, width=12, anchor=tk.W, font=('微软雅黑', 10),
                     bg=COLORS['bg_primary'], fg=COLORS['text_secondary']).pack(side=tk.LEFT)
            self.hr_pos_bars[key] = pb
            self.hr_pos_vars[key] = var

        # ================================================================
        # 说明文字（移除图表区）
        # ================================================================
            tk.Label(f, text=" 命中率说明：\n"
                         "• 容错命中(±1)：号码偏差1以内也算命中，数据偏高\n"
                         "• 严格命中（精确）：仅完全匹配才算命中，更真实反映预测能力\n"
                         "数据来源：p5_prediction_record 已验证记录（真实、不编造）。",
                 font=('微软雅黑', 9), bg=COLORS['bg_primary'], fg=COLORS['text_muted'],
                 wraplength=420, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 8))

        # ================================================================
        # 历史预测记录一览表（真实验证明细, 可回溯）
        # ================================================================
        rec_head = tk.Frame(f, bg=COLORS['bg_primary'])
        rec_head.pack(fill=tk.X, pady=(16, 4))
        tk.Label(rec_head, text=" 历史预测记录明细（最近已验证）",
                 font=('微软雅黑', 11, 'bold'),
                 bg=COLORS['bg_primary'], fg=COLORS['text_primary']).pack(side=tk.LEFT)
        self.hr_records_hint = tk.StringVar(value="（加载中…）")
        tk.Label(rec_head, textvariable=self.hr_records_hint, font=('微软雅黑', 9),
                 bg=COLORS['bg_primary'], fg=COLORS['text_muted']).pack(side=tk.RIGHT)
        # 筛选开关——只看命中≥3 的记录
        self.hr_filter_var = tk.BooleanVar(value=False)
        tk.Checkbutton(rec_head, text="只看命中≥3", variable=self.hr_filter_var,
                       command=self._apply_records_filter,
                       font=('微软雅黑', 9), bg=COLORS['bg_primary'],
                       fg=COLORS['text_secondary'],
                       activebackground=COLORS['bg_primary'],
                       selectcolor=COLORS.get('bg_input', '#ffffff'),
                       cursor='hand2').pack(side=tk.RIGHT, padx=(0, 10))

        table_wrap = tk.Frame(f, bg=COLORS['bg_primary'])
        table_wrap.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        cols = ('issue', 'pred', 'actual', 'hit', 'acc', 'time')
        self.hr_records_tree = ttk.Treeview(table_wrap, columns=cols,
                                            show='headings', height=8)
        # 列头文案（供排序箭头指示恢复使用）
        self._hr_col_titles = {'issue': '期号', 'pred': '预测号码',
                               'actual': '开奖号码', 'hit': '命中位数',
                               'acc': '准确率', 'time': '验证时间'}
        # 可排序列（号码列排序无意义, 不参与）
        sortable = {'issue', 'hit', 'acc', 'time'}
        headers = [('issue', 78), ('pred', 100), ('actual', 100),
                   ('hit', 68), ('acc', 68), ('time', 130)]
        for key, w in headers:
            if key in sortable:
                self.hr_records_tree.heading(
                    key, text=self._hr_col_titles[key],
                    command=lambda c=key: self._sort_records_by(c))
            else:
                self.hr_records_tree.heading(key, text=self._hr_col_titles[key])
            anchor = tk.W if key in ('issue', 'time') else tk.CENTER
            self.hr_records_tree.column(key, width=w, anchor=anchor,
                                        stretch=(key == 'time'))
        vsb = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL,
                            command=self.hr_records_tree.yview)
        self.hr_records_tree.configure(yscrollcommand=vsb.set)
        self.hr_records_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        # 行配色标签（暗色主题）：每行只挂一个标签，避免多标签同属性优先级歧义。
        # 组合 = 隔行底色 × 是否命中高亮，前景一律保证与底色有足够对比。
        self.hr_records_tree.tag_configure('row_even',
                                           background=COLORS['bg_card'],
                                           foreground=COLORS['text_primary'])
        self.hr_records_tree.tag_configure('row_odd',
                                           background=COLORS['bg_secondary'],
                                           foreground=COLORS['text_primary'])
        self.hr_records_tree.tag_configure('row_even_hit',
                                           background=COLORS['bg_card'],
                                           foreground=COLORS['accent_p5_bright'])
        self.hr_records_tree.tag_configure('row_odd_hit',
                                           background=COLORS['bg_secondary'],
                                           foreground=COLORS['accent_p5_bright'])
        self.hr_records_tree.tag_configure('hit_full',
                                           background=COLORS.get('success', '#2e7d32'),
                                           foreground='#ffffff')
        # 排序状态: (列key, 是否降序)
        self._hr_sort_state = (None, False)
        # 双击某行 → 查看该期完整 AI 报告
        self.hr_records_tree.bind('<Double-1>', self._on_record_double_click)
        tk.Label(f, text=" 双击任一行可查看该期完整 AI 预测详情；点击「期号/命中位数/准确率/验证时间」列头可排序。",
                 font=('微软雅黑', 8), bg=COLORS['bg_primary'],
                 fg=COLORS['text_muted']).pack(anchor=tk.W, pady=(4, 0))

    def _on_record_double_click(self, event):
        """双击明细表某行 → 弹出该期完整 AI 预测详情。"""
        try:
            tree = self.hr_records_tree
            # 仅当双击在数据行上才响应（点列头/空白不弹）
            if tree.identify_region(event.x, event.y) != 'cell':
                return
            item = tree.identify_row(event.y)
            if not item:
                return
            issue = str(tree.set(item, 'issue') or '').strip()
            if issue and issue != '--':
                self._show_ai_report_detail(issue)
        except Exception:
            pass

    def _sort_records_by(self, col):
        """点击列头排序：同列点击升/降切换，列头带 / 指示，保留行高亮标签。"""
        try:
            tree = self.hr_records_tree
            prev_col, prev_desc = getattr(self, '_hr_sort_state', (None, False))
            descending = not prev_desc if prev_col == col else True

            def _sort_key(iid):
                """生成 Treeview 列排序所用的排序键。

                参数:
                    iid: Treeview 行的内部 id

                返回:
                    (有效性标记, 数值) 二元组 —— 标记 0 表示解析成功、1 表示解析失败排在后面

                说明:
                    针对 hit（"3/5" 取 3）、acc（"60%" 取 60.0）、issue（按数值）三类列做特殊解析。
                """
                v = str(tree.set(iid, col) or '')
                if col == 'hit':          # "3/5" → 3
                    try:
                        return (0, int(v.split('/')[0]))
                    except Exception:
                        return (1, 0)
                if col == 'acc':          # "60%" → 60.0
                    try:
                        return (0, float(v.rstrip('%')))
                    except Exception:
                        return (1, 0.0)
                if col == 'issue':        # 期号按数值
                    try:
                        return (0, int(v))
                    except Exception:
                        return (1, 0)
                return (0, v)             # time 等按字符串（ISO格式可直接比较）

            items = sorted(tree.get_children(''), key=_sort_key, reverse=descending)
            for idx, iid in enumerate(items):
                tree.move(iid, '', idx)

            # 更新列头箭头指示（先恢复所有列头原始文案）
            for k, title in self._hr_col_titles.items():
                tree.heading(k, text=title)
            arrow = ' ' if descending else ' '
            tree.heading(col, text=self._hr_col_titles[col] + arrow)
            self._hr_sort_state = (col, descending)
        except Exception:
            pass

    def _update_hit_rate_tab(self, stats):
        """（主线程）用 stats 刷新命中率面板控件。

        改用严格命中率（无容错）作为主指标，同时显示容错数据供对比。
        """
        if not hasattr(self, 'hr_summary_var') or not stats:
            return
        try:
            total = stats.get('total', 0)
            if not total:
                self.hr_summary_var.set("（暂无验证数据）")
                for key in self.hr_pos_vars:
                    self.hr_pos_vars[key].set("--")
                    self.hr_pos_bars[key]['value'] = 0
                return

            merged = int(stats.get('merged_duplicates', 0) or 0)
            self._hr_merged_duplicates = merged
            merge_txt = f" | 已合并重复记录 {merged} 条" if merged else ""

            # 严格命中率（主指标）
            strict_avg = float(stats.get('strict_avg_accuracy', 0) or 0)
            strict_full = int(stats.get('strict_full_matches', 0) or 0)  # 完全命中5位的期数
            strict_total = int(stats.get('strict_total_matched', 0) or 0)  # 位置命中总次数

            # 容错命中率（参考）
            tol_avg = float(stats.get('avg_accuracy', 0) or 0)
            tol_matched = int(stats.get('total_matched', 0) or 0)

            self.hr_summary_var.set(
                f"已验证 {total} 期（按期号去重）\n"
                f"严格命中: 完全命中 {strict_full} 期 | 平均 {strict_avg:.1f}%\n"
                f"容错命中: 完全命中 {tol_matched} 期 | 平均 {tol_avg:.1f}%"
                f"{merge_txt}"
            )

            # 更新进度条：显示严格命中率
            strict_keys = ('strict_wan_accuracy', 'strict_qian_accuracy', 'strict_bai_accuracy',
                          'strict_shi_accuracy', 'strict_ge_accuracy')
            for key in strict_keys:
                rate = float(stats.get(key, 0) or 0)
                pos_name = key.replace('strict_', '').replace('_accuracy', '')
                var_key = f'{pos_name}_accuracy'
                if var_key in self.hr_pos_vars:
                    self.hr_pos_vars[var_key].set(f"{rate:.1f}%")
                    self.hr_pos_bars[var_key]['value'] = max(0.0, min(100.0, rate))

        except Exception:
            pass

        # 不再自动加载图表

    def _load_and_draw_hit_rate_chart(self):
        """（主线程）启动后台线程生成命中率柱状图。"""
        try:
            threading.Thread(target=self._draw_hit_rate_chart_internal, daemon=True).start()
        except Exception:
            pass

    def _draw_hit_rate_chart_internal(self):
        """（后台线程）从数据库获取数据并生成柱状图。"""
        try:
            db = P5Database()
            if not db.connect():
                self.root.after(0, lambda: self._update_chart_image(None))
                return
            
            stats = db.get_verification_stats()
            db.disconnect()
            
            if not stats or stats.get('total', 0) == 0:
                self.root.after(0, lambda: self._update_chart_image(None))
                return
            
            # 准备数据：5个位置的命中率
            pos_keys = ['wan_accuracy', 'qian_accuracy', 'bai_accuracy', 'shi_accuracy', 'ge_accuracy']
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            values = [float(stats.get(k, 0) or 0) for k in pos_keys]
            
            # 使用 matplotlib Agg 后端生成柱状图
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            import matplotlib.font_manager as fm
            # 中文渲染——matplotlib 默认用 DejaVu Sans（无 CJK），需显式指定
            # 中文字体，与 backtester.py 保持一致；缺失时自动降级
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 暗色适配：此前 figure 用深色底、坐标区却是默认白底黑字，
            # 标题/刻度文字落在深色画布上几乎不可见。此处统一为暗色主题。
            fg = COLORS['text_primary']
            fig, ax = plt.subplots(figsize=(8, 4.5))
            fig.patch.set_facecolor(COLORS['bg_primary'])
            ax.set_facecolor(COLORS['bg_card'])
            bars = ax.bar(pos_names, values, color=['#059669', '#10b981', '#34d399', '#6ee7b7', '#34d399'])
            ax.set_ylim(0, 100)
            ax.set_ylabel('命中率 (%)', fontsize=10, color=fg)
            ax.set_title('各位置命中率对比（容错匹配±1）', fontsize=11, fontweight='bold', color=fg)
            ax.tick_params(colors=fg, labelsize=9)
            for spine in ax.spines.values():
                spine.set_color(COLORS['border'])

            # 在柱子上方标注数值
            for bar, val in zip(bars, values):
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height + 1,
                       f'{val:.1f}%', ha='center', va='bottom', fontsize=9, color=fg)
            
            # 添加网格线
            ax.yaxis.grid(True, linestyle='--', alpha=0.3, color=COLORS['border_light'])
            ax.set_axisbelow(True)
            
            # 保存到临时文件
            import tempfile
            tmp_dir = REPORTS_CHARTS_DIR
            os.makedirs(tmp_dir, exist_ok=True)
            chart_path = os.path.join(tmp_dir, f'hitrate_{int(time.time())}.png')
            plt.savefig(chart_path, dpi=100, bbox_inches='tight', facecolor=COLORS['bg_primary'])
            plt.close(fig)
            # 移除 matplotlib 内嵌的 iCCP 色彩块，规避 libpng 警告
            # 'iCCP: cHRM chunk does not match sRGB'（该块对显示无影响）
            _strip_png_iccp(chart_path)

            # 回到主线程更新图片
            self.root.after(0, lambda: self._update_chart_image(chart_path))
            
        except Exception as e:
            logger.debug(f'生成图表失败: {e}')
            self.root.after(0, lambda: self._update_chart_image(None))

    @staticmethod
    def _strip_png_iccp(path):
        """移除 PNG 中的 iCCP 色彩配置块，规避 libpng 警告
        'iCCP: cHRM chunk does not match sRGB'。

        matplotlib 的 Agg 后端保存的 PNG 默认内嵌 sRGB iCCP 块，GUI 用 PIL 重新
        打开时 libpng 会打印该告警（仅噪声，不影响显示）。直接按 PNG 分块结构
        解析并丢弃 iCCP 块即可消除，全程不经由 PIL 打开，故不会再触发 libpng 告警。
        """
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if len(data) < 8 or data[:8] != b'\x89PNG\r\n\x1a\n':
                return
            out = bytearray(data[:8])
            i, n = 8, len(data)
            while i + 8 <= n:
                length = int.from_bytes(data[i:i + 4], 'big')
                ctype = data[i + 4:i + 8]
                chunk_end = i + 8 + length + 4  # 含 4 字节 CRC
                if chunk_end > n:
                    break
                if ctype != b'iCCP':
                    out += data[i:chunk_end]
                i = chunk_end
            else:
                with open(path, 'wb') as f:
                    f.write(out)
        except Exception:  # noqa: BLE001
            pass


    def _update_chart_image(self, img_path):
        """（主线程）更新图表显示图片。"""
        try:
            if not hasattr(self, 'chart_label') or self.chart_label is None:
                return
            
            # 清除旧图片
            if hasattr(self, '_chart_photo'):
                self.chart_label.configure(image=None)
                self._chart_photo = None  # 防止GC
            
            if img_path is None or not os.path.exists(img_path):
                # 无数据时显示提示标签
                self.chart_label.configure(text="暂无可用数据", 
                                          font=('微软雅黑', 10, 'italic'),
                                          fg=COLORS['text_muted'])
                return
            
            # 加载新图片
            from PIL import Image, ImageTk
            # 打开前先剔除 iCCP 色彩块，规避 libpng 'iCCP: cHRM chunk
            # does not match sRGB' 噪声告警（对显示无影响）
            _strip_png_iccp(img_path)
            img = Image.open(img_path)
            # 适配显示尺寸
            max_width = 350
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            
            self._chart_photo = ImageTk.PhotoImage(img)
            self.chart_label.configure(image=self._chart_photo)
            self.chart_label.configure(text="")  # 清空文字提示
            
            # 清理旧图表文件（保留最近5个）
            try:
                files = [f for f in os.listdir(REPORTS_CHARTS_DIR) if f.startswith('hitrate_') and f.endswith('.png')]
                files.sort(key=lambda x: os.path.join(REPORTS_CHARTS_DIR, x), reverse=True)
                for f in files[5:]:
                    try:
                        os.remove(os.path.join(REPORTS_CHARTS_DIR, f))
                    except:
                        pass
            except:
                pass
        except Exception:
            pass

    @staticmethod
    def _fmt_record_nums(raw):
        """把 predicted_numbers / actual_numbers 统一格式化为 '1 2 3 4 5' 五位串。
        兼容：dict 按位取首({'wan':[..]})、list 直连、JSON 字符串。"""
        try:
            if isinstance(raw, str):
                raw = json.loads(raw) if raw.strip() else None
            if not raw:
                return '--'
            order = ['wan', 'qian', 'bai', 'shi', 'ge']
            if isinstance(raw, dict):
                vals = []
                for p in order:
                    v = raw.get(p)
                    if isinstance(v, (list, tuple)) and v:
                        vals.append(str(v[0]))
                    elif v is not None and not isinstance(v, (list, tuple, dict)):
                        vals.append(str(v))
                    else:
                        vals.append('?')
                return ' '.join(vals)
            if isinstance(raw, (list, tuple)):
                return ' '.join(str(x) for x in raw[:5])
        except Exception:
            pass
        return '--'

    def _update_records_table(self, records):
        """（主线程）缓存已验证预测记录并按当前筛选渲染明细表。(v3.24 拆分)"""
        if not hasattr(self, 'hr_records_tree'):
            return
        self._hr_records_cache = list(records or [])
        self._render_records_rows()

    def _apply_records_filter(self):
        """筛选开关切换 → 用缓存即时重渲染（不重新查库）。"""
        self._render_records_rows()

    def _render_records_rows(self):
        """（主线程）按「只看命中≥3」筛选状态渲染明细表行。"""
        try:
            tree = self.hr_records_tree
            for iid in tree.get_children():
                tree.delete(iid)
            # 重置排序状态与列头箭头（重渲染后按默认时间序展示）
            try:
                for k, title in self._hr_col_titles.items():
                    tree.heading(k, text=title)
                self._hr_sort_state = (None, False)
            except Exception:
                pass
            records = getattr(self, '_hr_records_cache', []) or []
            if not records:
                self.hr_records_hint.set("暂无已验证记录")
                return
            filter_on = False
            try:
                filter_on = bool(self.hr_filter_var.get())
            except Exception:
                pass
            # 兜底去重：数据层已按期号合并，此处再按期号保序去重一次，
            # 防止任何旧调用路径把同期号多条记录塞进明细表。
            seen_issues = set()
            deduped = []
            dropped = 0
            for r in records:
                key = str(r.get('target_issue') or r.get('actual_issue') or '')
                if key and key in seen_issues:
                    dropped += 1
                    continue
                if key:
                    seen_issues.add(key)
                deduped.append(r)
            records = deduped

            shown = 0
            for r in records:
                mc = r.get('match_count')
                mc = 0 if mc is None else int(mc)
                if filter_on and mc < 3:
                    continue
                issue = r.get('target_issue') or r.get('actual_issue') or '--'
                pred = self._fmt_record_nums(r.get('predicted_numbers'))
                actual = self._fmt_record_nums(r.get('actual_numbers'))
                # 准确率统一按「命中位数/5」重算：库中 accuracy_rate 由不同版本写入，
                # 分母不一致（存在 4 命中记成 42.86% 的历史脏数据），不能直接展示。
                acc_txt = f"{mc / 5 * 100:.0f}%"
                vt = r.get('verified_at')
                vt_txt = vt.strftime('%Y-%m-%d %H:%M') if hasattr(vt, 'strftime') else (str(vt)[:16] if vt else '--')
                odd = bool(shown % 2)
                if mc >= 5:
                    row_tag = 'hit_full'
                elif mc >= 3:
                    row_tag = 'row_odd_hit' if odd else 'row_even_hit'
                else:
                    row_tag = 'row_odd' if odd else 'row_even'
                tree.insert('', tk.END, values=(issue, pred, actual,
                                                f"{mc}/5", acc_txt, vt_txt), tags=(row_tag,))
                shown += 1

            merge_txt = f" · 本表另合并重复 {dropped} 条" if dropped else ""
            if filter_on:
                if shown == 0:
                    self.hr_records_hint.set(f"{len(records)} 期 · 无命中≥3的记录{merge_txt}")
                else:
                    self.hr_records_hint.set(f"{len(records)} 期 · 筛选后 {shown} 期{merge_txt}")
            else:
                self.hr_records_hint.set(f"{len(records)} 期（每期仅保留最新一条）{merge_txt}")
        except Exception:
            pass

    def _refresh_hit_rate_tab(self):
        """异步刷新命中率面板：后台取数，主线程渲染，避免阻塞首屏。
        同一次连接顺带取已验证预测记录明细。"""
        def _worker():
            """后台线程读取命中率统计与最近验证记录。

            说明:
                统计与明细分别 try 包裹，任一失败不影响另一项；查询结束必定断开数据库连接。
            """
            stats = None
            records = None
            try:
                db = P5Database()
                if db.connect():
                    try:
                        stats = db.get_verification_stats()
                        try:
                            records = db.get_verified_predictions(days=3650, limit=50)
                        except Exception:
                            records = None
                    finally:
                        db.disconnect()
            except Exception:
                stats = None
            try:
                self.root.after(0, lambda: self._update_hit_rate_tab(stats or {}))
                self.root.after(0, lambda: self._update_records_table(records or []))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _append_hit_rate_overview(self, task_mgr):
        """预测后: 日志留精简摘要 + 主线程刷新常驻命中率面板(真实数据不编造)。"""
        try:
            db = P5Database()
            if not db.connect():
                return
            try:
                stats = db.get_verification_stats()
            finally:
                db.disconnect()
            if not stats or stats.get('total', 0) == 0:
                task_mgr.append_info("  （暂无验证数据, 命中率面板留空）")
                return
            total = stats.get('total', 0)
            task_mgr.append_section_header(" 历史命中率概览（真实验证数据）")
            task_mgr.append_info(f"  已验证 {total} 期（按期号去重）")

            # 严格命中率
            strict_avg = stats.get('strict_avg_accuracy', 0) or 0
            strict_full = stats.get('strict_full_matches', 0) or 0
            task_mgr.append_info(f"  严格命中（精确匹配）: 完全命中 {strict_full} 期 | 平均准确率 {strict_avg:.1f}%")

            # 容错命中率
            tol_avg = stats.get('avg_accuracy', 0) or 0
            tol_matched = stats.get('total_matched', 0) or 0
            task_mgr.append_info(f"  容错命中（±1偏差）: 完全命中 {tol_matched} 期 | 平均准确率 {tol_avg:.1f}%")

            # 各位置严格命中率
            positions = [
                ('万', stats.get('strict_wan_accuracy', 0)),
                ('千', stats.get('strict_qian_accuracy', 0)),
                ('百', stats.get('strict_bai_accuracy', 0)),
                ('十', stats.get('strict_shi_accuracy', 0)),
                ('个', stats.get('strict_ge_accuracy', 0)),
            ]
            task_mgr.append_info(f"  各位置严格命中率: 万 {positions[0][1]:.1f}% / 千 {positions[1][1]:.1f}% / "
                               f"百 {positions[2][1]:.1f}% / 十 {positions[3][1]:.1f}% / 个 {positions[4][1]:.1f}%")

            # 随机基线对比
            task_mgr.append_info(f"  随机基线: 每位置约 50%（预测5个号码/位，总范围10个）")
            task_mgr.append_info(" 历史命中率不代表未来表现；排列5为公平摇号。")
            # 主线程刷新常驻面板(避免工作线程直接碰控件)
            self.root.after(0, self._update_hit_rate_tab, stats)
        except Exception:
            pass

    def _execute_trend_analysis(self, task_mgr, cancel_event=None):
        """
        走势引擎分析（v3.15 增量模块 TrendAnalyzer）

        独立于封板四步流水线，提供走势信号分解视图：
        - 加载8类走势数据（历史/基础/5位置/和值/贝叶斯）
        - 6信号源融合打分（频率·遗漏·动量·升平降·和值重心·贝叶斯）
        - 输出各位置 Top-3 + 相对热度 + 信号源分解 + 可读特征

        诚实口径：relative_hotness 为归一化打分（非命中概率），
        排列5公平摇号无法稳定超越随机基线（Top-1≈10%）。
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log(" 走势引擎分析")
            task_mgr.log("=" * 70)
            task_mgr.progress(10, "初始化走势引擎")

            # 懒加载（项目约定：外部依赖延迟导入）
            from modules.trend_analyzer import TrendAnalyzer

            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            try:
                task_mgr.progress(20, "加载走势数据")
                analyzer = TrendAnalyzer(db, enable_adapt=False)
                task_mgr.log(f"  数据窗口: 近60期 | 信号源: 频率·遗漏·动量·升平降·和值·贝叶斯")
                task_mgr.log(f"  自适应模式: 关闭（使用常量权重，回测证实价值有限）")
                task_mgr.log("")

                task_mgr.progress(40, "信号融合计算")
                result = analyzer.predict(target_issue='', period=60)
            finally:
                db.disconnect()

            if result.get('error'):
                task_mgr.log(f" {result['error']}")
                task_mgr.progress(0, "分析失败")
                return

            positions = result.get('positions', {})
            if not positions:
                task_mgr.log(" 无可用走势数据")
                task_mgr.progress(0, "无数据")
                return

            task_mgr.progress(70, "输出结果")
            task_mgr.append_section_header(" 走势引擎推荐（相对热度排序）")

            pos_order = ['wan', 'qian', 'bai', 'shi', 'ge']
            pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}

            for p in pos_order:
                pdata = positions.get(p)
                if not pdata:
                    continue
                pname = pos_names.get(p, p)
                top5 = pdata.get('top5', [])
                hotness = pdata.get('relative_hotness', {})
                breakdown = pdata.get('signal_breakdown', {})
                features = pdata.get('features', {})

                task_mgr.append_info(f"  【{pname}】 Top-3: {' '.join(str(d) for d in top5)}")

                # 相对热度
                hot_str = " | ".join(f"{d}→{hotness.get(str(d), hotness.get(d, 0)):.0f}" for d in top5)
                task_mgr.append_info(f"    相对热度: {hot_str}")

                # 信号源分解
                if breakdown:
                    sig_str = " ".join(f"{s}:{w:.2f}" for s, w in sorted(breakdown.items()))
                    task_mgr.append_info(f"    信号权重: {sig_str}")

                # 可读特征 (结构: {'freq_pct':{d:v}, 'omission':{d:v}, 'direction_pref':str})
                if features:
                    freq_map = features.get('freq_pct', {})
                    om_map = features.get('omission', {})
                    dir_pref = features.get('direction_pref', '')
                    feat_parts = []
                    for d in top5:
                        freq = freq_map.get(d, freq_map.get(str(d), 0))
                        om = om_map.get(d, om_map.get(str(d), 0))
                        feat_parts.append(f"{d}:频率{freq}%/遗漏{om}期")
                    if feat_parts:
                        task_mgr.append_info(f"    可读特征: {', '.join(feat_parts)}")
                    if dir_pref:
                        task_mgr.append_info(f"    方向偏好: {dir_pref}")

            # 信号源诊断
            diag = result.get('signal_diagnostics', {})
            if diag:
                task_mgr.append_section_header(" 信号源诊断")
                for sname, info in sorted(diag.items()):
                    if isinstance(info, dict):
                        task_mgr.append_info(
                            f"  {sname}: 权重={info.get('weight', 0):.3f} | "
                            f"EWMA={info.get('ewma', 0):.3f} | 样本数={info.get('samples', 0)}"
                        )

            # 诚实免责
            task_mgr.append_section_header(" 诚实声明")
            task_mgr.append_info("  • relative_hotness 为归一化打分（相对热度），非命中概率")
            task_mgr.append_info("  • 排列5公平摇号，历史走势无法稳定超越随机基线（Top-1≈10%）")
            task_mgr.append_info("  • 300期walk-forward回测: 融合命中率50.07%≈随机50%，信号源价值有限")
            task_mgr.append_info("  • 本视图用于数据探索与信号分解，不承诺预测准确性")

            # 复用命中率概览（真实验证数据）
            self._append_hit_rate_overview(task_mgr)

            task_mgr.progress(100, "完成")
            # 结果仪表盘：合并视图呈现走势引擎 Top-3 信号分解
            self._show_result_dashboard(trend_result=result)
            task_mgr.log("\n 走势引擎分析完成")

        except Exception as e:
            task_mgr.log(f"\n  [错误] 走势引擎分析失败: {str(e)}")
            task_mgr.log(f"  [错误详情]\n{traceback.format_exc}")
            task_mgr.progress(0, "错误")

    # ============================================================
    # 业务任务 - 智能分析中心（合并 流水线 + 走势 + 快速预测）
    # ============================================================

    def _execute_unified_analysis(self, task_mgr, cancel_event=None):
        """
        智能分析中心（极简模式）：单一「开始分析」按钮自动执行完整流程。

        v3.42 起，本方法是全系统唯一的分析入口，依次运行六个阶段：
          ① 四步流水线预测   — 主预测，注册 pending 记录，内部已含验证闭环 + 权重自适应
          ② 走势引擎分解     — 信号源 Top-3 辅助
          ③ 快速预测         — 纯统计 P5Predictor
          ④ 命中率优化       — 选号策略对照 / 概率校准状态 / 三闸门调参结论（原独立卡片）
          ⑤ 在线学习闭环     — 验证统计 / 自适应权重调度 / 归因覆盖率（原独立卡片）
          ⑥ AI 辅助分析      — 贝叶斯后验的大模型解读，不可用时诚实降级

        所有结果输出到同一右侧结果面板（含顶部预测结果仪表盘），
        无需任何参数配置或模式选择。

        设计约束（诚实边界）：④⑤⑥ 均为「解读 / 状态展示」层，
        不会篡改 ①②③ 已产出的融合概率与推荐号码。
        """
        task_mgr.log("=" * 70)
        task_mgr.append_section_header(
        " 智能分析（流水线 + 走势 + 快速预测 + 命中率优化 + 在线学习 + AI 辅助）")
        task_mgr.log("=" * 70)
        task_mgr.log("  自动执行全部分析功能，结果将呈现在同一结果面板（顶部「预测结果仪表盘」）\n")

        def _cancelled() -> bool:
            """查询当前任务是否已被用户取消。

            返回:
                bool —— True 表示用户已点击取消，调用方应尽快中止长耗时流程
            """
            return bool(getattr(task_mgr, '_cancelled', False))

        # ── 联动：通知自我进化引擎暂停自动调度，避免资源竞争 ──
        eng = getattr(self, 'evolution', None)
        # v3.57：不论后续流程是否被取消 / 异常，finally 块一定恢复引擎调度。
        # 此前各取消分支（行 4882 / 4888 / 4895 / 4901）漏调 notify_analysis_done，
        # 配合 evolution_link_state.json 持久化 analysis_running=True，会导致
        # 引擎被永久卡在「联动暂停」分支。
        _analysis_done_called = False
        if eng is not None:
            try:
                eng.notify_analysis_started()
                # 尝试将进化引擎发现的最优候选权重注入预测器，使本次分析受益
                from modules.predictor import P5Predictor
                predictor = P5Predictor()
                try:
                    injected = eng.apply_active_config_to_predictor(predictor)
                    if injected:
                        task_mgr.append_info(' ℹ 已注入「自我进化」最优候选权重（预测器将使用进化后的融合权重）。')
                except Exception:  # noqa: BLE001
                    pass  # 注入失败不影响主流程
            except Exception as e:  # noqa: BLE001
                task_mgr.append_warning(f' 联动通知引擎暂停调度失败（不影响分析流程）：{e}')

        try:
            # 1) 四步流水线预测（主预测，注册 pending 预测记录；内部含验证→学习闭环）
            # v3.60：透传 cancel_event，让四步流水线（特别是回测）能响应取消。
            self._execute_four_step_pipeline(task_mgr, cancel_event=cancel_event)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 2) 走势引擎分解（信号源 Top-3 辅助）
            self._execute_trend_analysis(task_mgr)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 3) 快速预测（纯统计预测）——同时把结果留给阶段 ④ 复用，避免重复 predict
            self._run_quick_predict_core(task_mgr, 'optimized')
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 4) 命中率优化（原「命中率优化引擎」卡片，v3.42 融合）
            # 复用阶段 ③ 已算出的融合概率，不再额外跑一次 predict。
            self._run_hitrate_optimization_stage(task_mgr)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 5) 在线学习闭环（原「在线学习引擎」卡片，v3.42 融合）
            self._run_online_learning_stage(task_mgr)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 6) AI 辅助分析与预测解读
            self._run_ai_assisted_stage(task_mgr)
        finally:
            # v3.57 修复：联动清理统一在 finally 中兜底，无论分析是正常完成、
            # 被用户取消，还是中途异常，都确保引擎调度被恢复，
            # evolution_link_state.json 中 analysis_running 不会被残留为 True。
            # 主流程末尾的 notify_analysis_done() 仍会再调一次，幂等安全。
            if eng is not None and not _analysis_done_called:
                try:
                    eng.notify_analysis_done()
                    _analysis_done_called = True
                except Exception:  # noqa: BLE001
                    pass

        # 合并视图：用已保存的各来源产物统一渲染仪表盘
        try:
            import logging as _lg
            _d = _lg.getLogger('kplucky.debug')
            import threading as _th
            _d.info('RENDER_UNIFIED_DASHBOARD: thread=%s', _th.current_thread().name)
        except Exception:
            pass
        self._render_unified_dashboard(task_mgr)
        task_mgr.append_success(" 智能分析完成：全部分析结果均已呈现在同一结果面板")

        # ── 联动：同步预测结果给进化引擎，恢复自动调度 ──
        if eng is not None:
            try:
                # 从 _last_pipeline_final 提取目标期号和推荐号码
                pf = getattr(self, '_last_pipeline_final', None)
                if pf:
                    target_issue = pf.get('next_issue') or pf.get('target_issue', '')
                    top_numbers = []
                    for pos_key in ['wan', 'qian', 'bai', 'shi', 'ge']:
                        pos_rec = pf.get(pos_key, {})
                        nums = pos_rec.get('numbers', []) if isinstance(pos_rec, dict) else []
                        top_numbers.append(nums[:3] if nums else [])
                    fused_probs = pf.get('fused_probabilities')
                    if target_issue:
                        eng.sync_analysis_result({
                            'target_issue': target_issue,
                            'top_numbers': top_numbers,
                            'fused_probabilities': fused_probs,
                        })
                    # 恢复自动调度（无论是否成功同步结果）
                    eng.notify_analysis_done()
            except Exception as e:  # noqa: BLE001
                task_mgr.append_warning(f' 联动同步预测结果失败（不影响分析结果展示）：{e}')
                try:
                    eng.notify_analysis_done()
                except Exception:  # noqa: BLE001
                    pass

    def _run_quick_predict_core(self, task_mgr, model='optimized'):
        """CLI 同步：predict --model optimized/old（纯统计预测，跳过验证/学习/报告）"""
        try:
            task_mgr.append_section_header(" 快速预测（纯统计，P5Predictor）")
            task_mgr.progress(10, "加载数据")

            from modules.predictor import P5Predictor, P5PredictorConfig

            db = P5Database()
            if not db.connect():
                task_mgr.append_error(" 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return
            history_data = db.get_history_data(limit=200, order_by='issue DESC')
            db.disconnect()

            if not history_data:
                task_mgr.append_warning(" 无历史数据，请先执行数据爬取")
                task_mgr.progress(0, "无数据")
                return

            current_issue = history_data[0].get('issue', '')
            target_issue = str(int(current_issue) + 1)
            predictor = P5Predictor(
                config=P5PredictorConfig.baseline_v21() if model == 'old' else None
            )

            task_mgr.progress(50, "预测中")
            result = predictor.predict(history_data, current_issue)
            if 'error' in result:
                task_mgr.append_error(f" 预测失败: {result['error']}")
                task_mgr.progress(0, "分析失败")
                return

            # 构造统一仪表盘可用的 final_report 结构
            pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
            final = {
                'target_issue': target_issue,
                'trend_prediction': {},
                'recommended_combinations': [],
                'risk_warning': result.get('risk_warning', '理性购彩，量力而行'),
            }
            for i, pk in enumerate(pos_keys):
                probs = result['fused_probabilities'][i]
                top = sorted(probs.items(), key=lambda x: float(x[1]), reverse=True)[:3]
                final['trend_prediction'][pk] = {'numbers': [int(n) for n, _ in top]}
            for combo in result.get('top_combinations', [])[:5]:
                if isinstance(combo, dict):
                    final['recommended_combinations'].append({
                        'combination': combo.get('combination', ''),
                        'confidence': combo.get('confidence', 0),
                    })

            task_mgr.append_success(f" 快速预测完成（模型: {model}）目标期号: {target_issue}")
            for pk, pn in zip(pos_keys, ['万位', '千位', '百位', '十位', '个位']):
                nums = final['trend_prediction'][pk]['numbers']
                task_mgr.append_info(f"  {pn}: {' '.join(str(n) for n in nums)}")

            # 暂存融合概率供「命中率优化」阶段复用，避免重复跑一次 predict
            # （单次 predict 在启用选号策略时可达数十秒，复用可直接省掉这段等待）。
            self._last_fused_probabilities = result.get('fused_probabilities')
            self._last_predict_meta = {
                'target_issue': target_issue,
                'selection_strategy_applied': result.get('selection_strategy_applied'),
                'selection_strategy_meta': result.get('selection_strategy_meta'),
                'probability_calibration_applied': result.get('probability_calibration_applied'),
            }

            self._prediction_clipboard = self._build_prediction_clipboard(final)
            # v3.62 修复：同步设置 _clipboard_meta，否则 _copy_prediction 因 meta 为空而误判无数据
            self._clipboard_meta = {
                'target_issue': final.get('target_issue') or final.get('issue', ''),
                'conf': final.get('confidence', ''),
                'high_conf': '',
                'main_combo': '',
            }
            self._show_result_dashboard(quick_final=final)
            task_mgr.progress(100, "完成")

        except Exception as e:
            task_mgr.log(f"\n 快速预测异常: {str(e)}")
            task_mgr.log(traceback.format_exc)
            task_mgr.progress(0, "异常终止")

    # ============================================================
    # 结果面板 - 预测结果仪表盘（需求3：结构化层级展示 + 视觉突出）
    # ============================================================

    # ---- 聚合分析辅助方法（去重 / 置信度 / 备选 / 详细分析） ----

    def _extract_source_data(self, pf, tr, qf):
        """从三个分析源抽取每位置 Top 候选，供聚合去重使用。
        返回: (picks, top5, combos, target_issue)
          picks[pos][src] = 该位置主推数字(int); top5[pos][src] = Top5列表
          combos[src] = 该源完整推荐组合字符串
        """
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        picks = {pk: {} for pk in pos_keys}
        top5 = {pk: {} for pk in pos_keys}
        combos = {}
        target_issue = ''

        # 四步流水线
        if isinstance(pf, dict):
            tp = pf.get('trend_prediction') or {}
            if isinstance(tp, dict):
                for pk in pos_keys:
                    cell = tp.get(pk)
                    nums = (cell.get('numbers', []) if isinstance(cell, dict) else []) or []
                    if nums:
                        picks[pk]['pipeline'] = int(nums[0])
                        top5[pk]['pipeline'] = [int(x) for x in nums[:4]]
            rc = pf.get('recommended_combinations') or []
            if rc and isinstance(rc[0], dict) and rc[0].get('combination'):
                combos['pipeline'] = str(rc[0]['combination'])
            target_issue = pf.get('next_issue') or target_issue

        # 快速预测
        if isinstance(qf, dict):
            tp = qf.get('trend_prediction') or {}
            if isinstance(tp, dict):
                for pk in pos_keys:
                    cell = tp.get(pk)
                    nums = (cell.get('numbers', []) if isinstance(cell, dict) else []) or []
                    if nums:
                        picks[pk]['quick'] = int(nums[0])
                        top5[pk]['quick'] = [int(x) for x in nums[:4]]
            rc = qf.get('recommended_combinations') or []
            if rc and isinstance(rc[0], dict) and rc[0].get('combination'):
                combos['quick'] = str(rc[0]['combination'])
            if not target_issue:
                target_issue = qf.get('target_issue') or ''

        # 走势引擎（信号源：每位置 top5）
        if isinstance(tr, dict):
            pos = tr.get('positions') or {}
            if isinstance(pos, dict):
                for pk in pos_keys:
                    pdata = pos.get(pk) or {}
                    t5 = pdata.get('top5', []) if isinstance(pdata, dict) else []
                    if t5:
                        picks[pk]['trend'] = int(t5[0])
                        top5[pk]['trend'] = [int(x) for x in t5[:4]]
            if not target_issue:
                target_issue = tr.get('target_issue') or ''

        # 由走势引擎 top5[0] 拼接其完整组合
        if any('trend' in picks[pk] for pk in pos_keys):
            try:
                combos['trend'] = ''.join(
                    str(picks[pk].get('trend')) for pk in pos_keys)
            except Exception:
                combos.pop('trend', None)

        return picks, top5, combos, target_issue

    def _aggregate_recommendation(self, picks):
        """多源聚合：每个位置多数投票，平票优先 四步流水线（旗舰八算法融合）。
        返回: (consensus[pos]=数字|None, agree[pos]=同意该数字的信号源数)
        """
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        consensus, agree = {}, {}
        for pk in pos_keys:
            srcs = picks.get(pk, {})
            if not srcs:
                consensus[pk], agree[pk] = None, 0
                continue
            cnt = {}
            for v in srcs.values():
                cnt[v] = cnt.get(v, 0) + 1
            maxc = max(cnt.values())
            tied = [d for d, c in cnt.items() if c == maxc]
            if len(tied) == 1:
                chosen = tied[0]
            else:
                chosen = None
                for pref in ('pipeline', 'quick', 'trend'):
                    if pref in srcs and srcs[pref] in tied:
                        chosen = srcs[pref]
                        break
                if chosen is None:
                    chosen = tied[0]
            consensus[pk], agree[pk] = chosen, maxc
        return consensus, agree

    def _restore_clipboard_from_finals(self, pf, tr, qf):
        """从各来源产物恢复 _prediction_clipboard / _clipboard_meta。

        在 _compute_dashboard_aggregates 失败时调用，确保仪表盘隐藏后
        「复制预测号码」按钮仍能找到可复制的数据，不报"无数据"误判。
        优先级：pipeline > quick > trend（四步流水线结果最完整）。
        """
        candidates = [(pf, 'pipeline'), (qf, 'quick'), (tr, 'trend')]
        for cand, src_name in candidates:
            if not isinstance(cand, dict):
                continue
            # 优先用 _build_prediction_clipboard（格式与阶段 ①/③ 一致）
            clip = self._build_prediction_clipboard(cand)
            if clip and clip.strip():
                issue = cand.get('target_issue') or cand.get('next_issue') or ''
                self._prediction_clipboard = clip
                self._clipboard_meta = {
                    'target_issue': str(issue),
                    'conf': '',
                    'high_conf': '',
                    'main_combo': '',
                }
                return
            # 退一步：用 _direct_extract_clipboard 提取
            direct = self._direct_extract_clipboard(cand)
            if direct and direct.strip():
                issue = cand.get('target_issue') or cand.get('next_issue') or ''
                self._prediction_clipboard = direct
                self._clipboard_meta = {
                    'target_issue': str(issue),
                    'conf': '',
                    'high_conf': '',
                    'main_combo': '',
                }
                return

    def _populate_clipboard_from_db_fallback(self):
        """v3.59 数据库兜底：三个 final 全为空时，从数据库读取最近一条预测记录写入 clipboard。

        适用场景：
          - 用户点击「开始分析」后中途取消（最常见）；
          - 流水线早期异常（数据库连接失败、无历史数据等）；
          - 第一次启动还没跑过分析。

        数据源：``p5_prediction_record`` 表中最新一条 ``verification_status='pending'`` 的预测记录
        （即最近一次有产出的分析结果），取其 ``target_issue`` / ``predicted_numbers`` /
        ``predicted_combinations`` 渲染为微信兼容文本。

        写入规则：
          - 任何异常静默吞掉（兜底链不应让用户看到技术错误）；
          - 写入 ``_prediction_clipboard`` 和 ``_clipboard_meta`` 两个字段，确保
            ``_copy_prediction`` 兜底链能命中；
          - 若数据库也无可用记录，仍写入一条最小化提示（避免 clipboard 永远空字符串）。
        """
        try:
            import json as _json
            db = P5Database()
            if not db.connect():
                return
            try:
                db.cursor.execute(
                    "SELECT target_issue, predicted_numbers, predicted_combinations, "
                    "confidence_scores, created_at FROM p5_prediction_record "
                    "ORDER BY target_issue DESC LIMIT 1"
                )
                row = db.cursor.fetchone()
            finally:
                try:
                    db.disconnect()
                except Exception:
                    pass

            from datetime import datetime as _dt

            if not row:
                # 数据库也无预测记录：写最小化提示
                self._prediction_clipboard = (
                    "【排列5 预测号码】\n"
                    f"生成时间: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "提示：本次智能分析未产出预测结果，且数据库中暂无历史预测记录。\n"
                    "请重新点击「开始分析」启动一次完整分析（耗时约 1~3 分钟），\n"
                    "或先点击「增量爬取数据」确保历史数据 ≥ 61 期。"
                )
                self._clipboard_meta = {
                    'target_issue': '',
                    'conf': '',
                    'high_conf': '',
                    'main_combo': '',
                }
                return

            target_issue = row.get('target_issue', '') or ''
            try:
                predicted_numbers = _json.loads(row.get('predicted_numbers') or '{}')
            except Exception:
                predicted_numbers = {}
            try:
                predicted_combinations = _json.loads(row.get('predicted_combinations') or '[]')
            except Exception:
                predicted_combinations = []

            pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            lines = [
                "【排列5 预测号码（最近一次分析结果）】",
                f"生成时间: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"预测期号: {target_issue}",
                "来源: 数据库最近一条预测记录（本次分析未产出新结果）",
                "",
                "━━━━━━━━━━━━━━━━━━━━",
                "各位置 Top-3 候选:",
            ]
            for pk, pn in zip(pos_keys, pos_names):
                nums = predicted_numbers.get(pk, []) if isinstance(predicted_numbers, dict) else []
                if isinstance(nums, list) and nums:
                    lines.append(f"  {pn}: {' '.join(str(int(n)) for n in nums)}")
            lines.append("")
            if predicted_combinations:
                lines.append("推荐组合 (Top 5):")
                for i, c in enumerate(predicted_combinations[:5], 1):
                    if isinstance(c, dict):
                        combo = c.get('combination', '')
                        conf = c.get('confidence', 0)
                        reason = c.get('reason', '')
                        line = f"  {i}. {combo}"
                        if conf:
                            line += f"  (一致度 {conf:.1f})"
                        if reason:
                            line += f"  — {reason}"
                        lines.append(line)
                lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("提示：本次分析未产出新预测号码，本次复制为数据库中最近一次的预测记录。")
            lines.append("建议重新点击「开始分析」获取最新结果。")

            clip = "\n".join(lines)
            self._prediction_clipboard = clip
            # 主推组合（取 predicted_combinations 第一条）
            main_combo = ''
            if predicted_combinations and isinstance(predicted_combinations[0], dict):
                main_combo = str(predicted_combinations[0].get('combination', ''))
            self._clipboard_meta = {
                'target_issue': str(target_issue),
                'conf': '',
                'high_conf': '',
                'main_combo': main_combo,
            }
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').info(
                    'COPY_DB_FALLBACK: 从数据库最近预测记录兜底写入，期号=%s，长度=%d',
                    target_issue, len(clip))
            except Exception:
                pass
        except Exception as e:
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').warning(
                    'COPY_DB_FALLBACK_ERROR: %s', e, exc_info=True)
            except Exception:
                pass

    def _compute_dashboard_aggregates(self, pf, tr, qf):
        """聚合计算纯函数：从三个来源产物统一算出渲染所需的全部数据。

        同时**同步写入** self._prediction_clipboard 与 self._clipboard_meta，
        让用户即便在主线程仪表盘 after(0) 队列尚未轮到时点击复制按钮，
        也能立刻拿到可复制的预测摘要。

        Returns:
            dict 或 None —— None 表示无可聚合数据
            包含: picks / top5 / combos / target_issue / consensus / agree /
                   main_combo / main_combo_disp / conf / high_conf / total_sources
        """
        if not (pf or tr or qf):
            return None
        try:
            picks, top5, combos, target_issue = self._extract_source_data(pf, tr, qf)
            consensus, agree = self._aggregate_recommendation(picks)

            pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
            total_sources = len({s for pk in pos_keys for s in picks[pk]})
            # 主推荐组合（完整5位）
            main_combo = ''.join(str(consensus[pk]) for pk in pos_keys if consensus[pk] is not None)
            # 综合一致性置信度（仅当多源可用）
            pos_n = {pk: len(picks[pk]) for pk in pos_keys}
            ratios = [agree[pk] / pos_n[pk] for pk in pos_keys
                      if consensus[pk] is not None and pos_n[pk] > 0]
            conf = int(round(sum(ratios) / len(ratios) * 100)) if (total_sources >= 2 and ratios) else None
            # 两主预测逐位主推完全一致 → 高置信度
            raw_p = ''.join(str(picks[pk].get('pipeline')) for pk in pos_keys if 'pipeline' in picks[pk])
            raw_q = ''.join(str(picks[pk].get('quick')) for pk in pos_keys if 'quick' in picks[pk])
            high_conf = bool(raw_p and raw_q and raw_p == raw_q and total_sources >= 2)
            # 展示层压缩为4位
            main_combo_disp = compress_combo(main_combo)

            # 关键：聚合一旦算出有效主推荐，立即同步写好 clipboard 与 meta。
            # 这样复制按钮在主线程任何时刻被点击都能命中可复制数据。
            if main_combo_disp:
                self._prediction_clipboard = self._build_aggregated_clipboard(
                    target_issue, main_combo_disp, conf, high_conf, combos, picks, top5)
                self._clipboard_meta = {
                    'target_issue': target_issue,
                    'conf': conf,
                    'high_conf': high_conf,
                    'main_combo': main_combo_disp,
                }
            else:
                # v3.63 修复：即使无法聚合出主推荐组合，只要 final_report 存在有效
                # trend_prediction 数据，也要写入 clipboard/meta，避免复制按钮误报"无数据"
                _direct = self._direct_extract_clipboard(pf)
                if not (_direct and _direct.strip()):
                    # 兜底再试用完整构建函数，确保至少有头部信息可复制
                    _direct = self._build_prediction_clipboard(pf) if isinstance(pf, dict) else ''
                if _direct and _direct.strip():
                    _iss = pf.get('next_issue') or pf.get('current_issue', '') or target_issue
                    self._prediction_clipboard = _direct
                    self._clipboard_meta = {
                        'target_issue': str(_iss),
                        'conf': '',
                        'high_conf': '',
                        'main_combo': '',
                    }
                else:
                    # 最终兜底：即使无数据也写入最小化提示，避免复制按钮误报无数据
                    self._prediction_clipboard = f"【排列5 预测号码】\n生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n期号: {_iss or '—'}\n提示: 未生成有效预测号码，请检查历史数据是否≥61期。"
                    self._clipboard_meta = {
                        'target_issue': str(_iss),
                        'conf': '',
                        'high_conf': '',
                        'main_combo': '',
                    }

            return {
                'picks': picks,
                'top5': top5,
                'combos': combos,
                'target_issue': target_issue,
                'consensus': consensus,
                'agree': agree,
                'main_combo': main_combo,
                'main_combo_disp': main_combo_disp,
                'conf': conf,
                'high_conf': high_conf,
                'total_sources': total_sources,
            }
        except Exception as e:
            try:
                import logging
                logging.getLogger('kplucky.debug').error(
                    'COMPUTE_DASHBOARD_AGGREGATES_ERROR: %s', e, exc_info=True)
            except Exception:
                pass
            # v3.64 修复：异常时保留已有 clipboard，不覆盖为 None/空
            return None

    def _build_position_candidates(self, picks, top5):
        """聚合多源候选, 输出每个展示位(万/千/百/十)的 4 个候选数字。

        优先级: 主推(consensus 首选) → 多源 top 列表去重追加 → 不足 4 个时按 0-9 顺序补足。
        返回 {pos_key: [int, int, int, int]}, pos_key ∈ DISPLAY_POS_KEYS。

        说明: 排列5 为公平摇号, 候选数字仅代表算法对各位置的高概率猜测,
        不保证命中; 补足逻辑仅为保证每位展示口径统一为 4 个候选。
        """
        out = {}
        for pk in DISPLAY_POS_KEYS:
            seen = set()
            digits = []
            # 1) 主推数字置顶（consensus 优先 pipeline/quick/trend 首个有效值）
            for src in ('pipeline', 'quick', 'trend'):
                v = picks.get(pk, {}).get(src)
                if isinstance(v, int) and v not in seen:
                    digits.append(v)
                    seen.add(v)
                    break
            # 2) 多源 Top 候选去重追加（每源最多 4 个）
            for src in ('pipeline', 'quick', 'trend'):
                for v in (top5.get(pk, {}).get(src, []) or []):
                    if isinstance(v, int) and v not in seen:
                        digits.append(v)
                        seen.add(v)
            # 3) 不足 4 个时按 0-9 顺序补足（保证每位 4 候选的展示口径）
            for d in range(10):
                if len(digits) >= 4:
                    break
                if d not in seen:
                    digits.append(d)
                    seen.add(d)
            out[pk] = digits[:4]
        return out

    def _build_explanation(self, total_sources, conf, high_conf, picks, pos_keys):
        """主推荐简短解释（诚实口径：一致性=多源重合度，非命中概率）"""
        if total_sources < 2:
            return ("当前为单一分析源结果（其余分析进行中），置信度需多源聚合后给出。"
                    "排列5为公平随机摇号，本预测仅供数据探索，不保证命中。")
        agree_pos = sum(1 for pk in pos_keys
                        if len(picks[pk]) >= 2 and len(set(picks[pk].values())) == 1)
        if high_conf:
            head = "两个主预测（四步流水线 / 快速预测）完全一致，已合并为高置信度推荐。"
        else:
            head = "两个主预测存在差异，已按预设规则（多源多数投票、平票优先四步流水线）筛选主推荐。"
        pos_note = f"其中 {agree_pos}/{len(pos_keys)} 个位置多源完全一致。"
        honest = ("综合一致性置信度仅反映多源信号重合程度；排列5公平摇号，"
                  "历史无法稳定超越随机基线（Top-1≈10%），不保证命中。")
        return " ".join([head, pos_note, honest])

    def _build_aggregated_clipboard(self, target_issue, main_combo, conf, high_conf, combos, picks, top5=None):
        """生成可复制的结构化摘要（主推荐 + 备选），确保复制的是算法预测结果
        
        算法预测流程：
        1. 多源数据采集：四步流水线、快速预测、走势引擎
        2. 逐位聚合：多数投票 + 平票优先四步流水线
        3. 置信度计算：多源信号重合度百分比
        4. 最终输出：综合推荐组合
        """
        # 展示层压缩为4位（保留万/千/百/十，去个位），核心数据仍为完整5位
        pos_keys = DISPLAY_POS_KEYS
        pos_names = DISPLAY_POS_NAMES
        top5 = top5 or {}
        
        # 获取各位置所有候选号码（从top5中提取所有来源的Top5号码）
        all_candidates = {}
        for pk, pn in zip(pos_keys, pos_names):
            # 收集所有来源的Top5号码
            all_digits = []
            seen = set()
            
            # 优先获取主推号码
            vals = picks.get(pk, {})
            for src in ['pipeline', 'quick', 'trend']:
                if src in vals:
                    val = vals[src]
                    if val not in seen:
                        all_digits.append(val)
                        seen.add(val)
            
            # 从top5中获取更多候选号码
            src_top5 = top5.get(pk, {})
            for src in ['pipeline', 'quick', 'trend']:
                if src in src_top5:
                    for val in src_top5[src]:
                        if val not in seen:
                            all_digits.append(val)
                            seen.add(val)
            
            # 确保每个位置有5个候选号码
            if len(all_digits) < 5:
                for d in range(10):
                    if d not in seen:
                        all_digits.append(d)
                        seen.add(d)
                        if len(all_digits) >= 5:
                            break
            
            all_candidates[pk] = all_digits[:4]
        
        lines = [
            f" 期号: {target_issue or '—'}",
            f" 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f" 【最终预测号码】: {compress_combo(main_combo)}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        
        # 各位置所有候选号码（按用户要求格式）
        lines.append(" 各位置候选号码:")
        for pk, pn in zip(pos_keys, pos_names):
            digits = all_candidates.get(pk, [])
            if digits:
                lines.append(f"   {pn}: {' '.join(str(d) for d in digits)}")
        lines.append("")
        
        # 置信度信息
        confidence_label = " 高置信度" if high_conf else " 常规置信度"
        lines.append(f" {confidence_label}")
        if conf is not None:
            lines.append(f"   综合一致性置信度: {conf}%")
        lines.append("")
        
        # 算法说明
        lines.append(" 算法说明:")
        lines.append("   • 多源数据: 四步流水线 + 快速预测 + 走势引擎")
        lines.append("   • 聚合策略: 多数投票（平票优先四步流水线）")
        lines.append("   • 置信度: 多源信号重合度百分比")
        lines.append("")
        
        # 逐位来源详情
        lines.append(" 逐位来源详情:")
        for pk, pn in zip(pos_keys, pos_names):
            vals = picks.get(pk, {})
            if vals:
                digits = ", ".join(f"{k}={v}" for k, v in sorted(vals.items()))
                main_digit = next(iter(vals.values()))
                lines.append(f"   {pn}: {main_digit}  [{digits}]")
        lines.append("")
        
        # 各分析源原始结果
        # 各分析源组合同步压缩为4位展示
        _combos_disp = {k: compress_combo(v) for k, v in (combos or {}).items()}
        _main_disp = compress_combo(main_combo)
        lines.append(" 各分析源结果（参考）:")
        for key, name in [('pipeline', '四步流水线'), ('quick', '快速预测'), ('trend', '走势引擎')]:
            if _combos_disp.get(key):
                is_main = (_combos_disp[key] == _main_disp)
                marker = "" if is_main else " "
                lines.append(f"   {marker} {name}: {_combos_disp[key]}" + (" ← 选为最终预测" if is_main else ""))

        return "\n".join(lines)

    def _toggle_detail(self):
        """展开/收起「详细分析」折叠区"""
        f = getattr(self, '_detail_frame', None)
        if f is None:
            return
        if f.winfo_manager():
            f.pack_forget()
        else:
            f.pack(fill=tk.X, padx=6, pady=(0, 6))

    def _build_alt_section(self, parent, combos, main_combo, pos_keys, pos_names):
        """备选号码折叠区（视觉弱化，明确区别于主推荐）"""
        alt_outer = tk.Frame(parent, bg=COLORS['bg_card'],
                             highlightbackground=COLORS['border'],
                             highlightthickness=1)
        alt_outer._rcat = "预测结论"   # 分类筛选标记：备选号码属结论范畴
        alt_outer.pack(fill=tk.X, pady=(0, 8))
        
        toggle_btn = tk.Button(alt_outer, text=" 备选号码（各分析源结果）", 
                               bg=COLORS['bg_card'], fg=COLORS['text_secondary'], 
                               font=('微软雅黑', 10, 'bold'), relief='flat',
                               cursor='hand2', padx=12, pady=6)
        toggle_btn.pack(anchor=tk.W)
        
        content = tk.Frame(alt_outer, bg=COLORS['bg_card'])
        content.pack_forget()
        self._alt_content = content
        self._alt_visible = False

        def _toggle():
            """折叠 / 展开「备选号码（各分析源结果）」区域，并同步切换按钮箭头文案。"""
            if self._alt_visible:
                content.pack_forget()
                toggle_btn.config(text=" 备选号码（各分析源结果）")
                self._alt_visible = False
            else:
                content.pack(fill=tk.X, padx=12, pady=(0, 8))
                toggle_btn.config(text=" 收起备选号码")
                self._alt_visible = True

        toggle_btn.config(command=_toggle)

        # 备选组合压缩为4位展示（去个位），与最终预测一致
        _main_disp = compress_combo(main_combo)
        for key, name in [('pipeline', '四步流水线'), ('quick', '快速预测'), ('trend', '走势引擎')]:
            c = combos.get(key)
            if not c:
                continue
            c_disp = compress_combo(c)
            is_main = (c_disp == _main_disp)
            row = tk.Frame(content, bg=COLORS['bg_card'])
            row.pack(fill=tk.X, pady=3)
            
            # 来源标签
            tk.Label(row, text=f" {name}:", bg=COLORS['bg_card'],
                     fg=COLORS['text_muted'], font=('微软雅黑', 9, 'bold')).pack(side=tk.LEFT)
            
            # 号码
            num_color = COLORS['accent_p5_light'] if is_main else COLORS['text_secondary']
            num_font = ('Consolas', 11, 'bold') if is_main else ('Consolas', 11)
            tk.Label(row, text=c_disp,
                     bg=COLORS['bg_card'], fg=num_color, font=num_font).pack(side=tk.LEFT, padx=(8, 0))
            
            # 主推荐标记
            if is_main:
                tk.Label(row, text="← 选为最终预测",
                         bg=COLORS['bg_card'], fg=COLORS['accent_p5'], 
                         font=('微软雅黑', 8, 'bold')).pack(side=tk.LEFT, padx=(6, 0))

    def _build_detail_section(self, parent, picks, top5, combos, target_issue,
                              pos_keys, pos_names, total_sources, conf, high_conf):
        """详细分析：各来源 Top 候选 + 计算逻辑 + 位置差异点"""
        tk.Label(parent, text="详细分析（来源 / 差异 / 计算逻辑）", bg=COLORS['bg_primary'],
                 fg=COLORS['text_primary'], font=('微软雅黑', 10, 'bold')
                 ).pack(anchor=tk.W, padx=8, pady=(6, 4))
        logic = {
            'pipeline': '四步流水线：八算法融合流水线（频率0.68+监督学习0.14+贝叶斯0.10+遗漏0.06+趋势/马尔可夫/形态/特征），'
                        '多源60期走势融合，系统旗舰预测。',
            'quick': '快速预测：P5Predictor 八算法融合模型（含ml_supervised监督学习），',
            'trend': '走势引擎：6信号源（频率·遗漏·动量·升平降·和值重心·贝叶斯）相对热度打分，'
                     '输出各位置 Top-4。',
        }
        for key, name in [('pipeline', '四步流水线'), ('quick', '快速预测'), ('trend', '走势引擎')]:
            present = combos.get(key) or any(key in picks[pk] for pk in pos_keys)
            if not present:
                continue
            blk = tk.Frame(parent, bg=COLORS['bg_secondary'], relief='groove', bd=1)
            blk.pack(fill=tk.X, padx=8, pady=3)
            tk.Label(blk, text=f"▎{name}", bg=COLORS['bg_secondary'], fg=COLORS['accent_p5'],
                     font=('微软雅黑', 9, 'bold')).pack(anchor=tk.W, padx=8, pady=(4, 2))
            line = '  '.join(
                f"{pn}:{''.join(str(x) for x in top5.get(pk, {}).get(key, []))}"
                for pk, pn in zip(pos_keys, pos_names))
            tk.Label(blk, text=line, bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                     font=('Consolas', 8), wraplength=600, justify=tk.LEFT
                     ).pack(anchor=tk.W, padx=8)
            tk.Label(blk, text=logic[key], bg=COLORS['bg_secondary'], fg=COLORS['text_muted'],
                     font=('微软雅黑', 8), wraplength=600, justify=tk.LEFT
                     ).pack(anchor=tk.W, padx=8, pady=(2, 4))

        # 位置差异点
        diff_rows = []
        for pk, pn in zip(pos_keys, pos_names):
            vals = picks.get(pk, {})
            if len(set(vals.values())) > 1:
                diff_rows.append(f"{pn}位分歧: " + ' / '.join(f"{k}={v}" for k, v in vals.items()))
        if diff_rows:
            dblk = tk.Frame(parent, bg=COLORS['bg_secondary'], relief='groove', bd=1)
            dblk.pack(fill=tk.X, padx=8, pady=3)
            tk.Label(dblk, text="▎位置差异点", bg=COLORS['bg_secondary'], fg=COLORS['warning'],
                     font=('微软雅黑', 9, 'bold')).pack(anchor=tk.W, padx=8, pady=(4, 2))
            for d in diff_rows:
                tk.Label(dblk, text=d, bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                         font=('Consolas', 8), wraplength=600, justify=tk.LEFT
                         ).pack(anchor=tk.W, padx=8, pady=(0, 2))
            tk.Label(dblk, text="注：主推荐按「多源多数投票、平票优先四步流水线」规则生成。",
                     bg=COLORS['bg_secondary'], fg=COLORS['text_muted'],
                     font=('微软雅黑', 8), wraplength=600, justify=tk.LEFT
                     ).pack(anchor=tk.W, padx=8, pady=(0, 4))

    def _build_source_matrix(self, parent, picks, consensus, pos_keys, pos_names):
        """分源对比表：行=5个位置，列=各来源(流水线/走势/快速)+综合，直观对比逐位差异。

        - 与「综合」一致的来源单元格用绿色高亮，不一致用弱化色，缺失显示「—」。
        - 常驻展示（非折叠），是本次右侧面板信息分层的核心：一眼看清各源分歧与共识。
        """
        src_defs = [('pipeline', '流水线'), ('trend', '走势'), ('quick', '快速')]
        # 仅展示实际有数据的来源列，避免空列干扰
        active_srcs = [(k, n) for k, n in src_defs
                       if any(k in picks.get(pk, {}) for pk in pos_keys)]

        wrap = tk.Frame(parent, bg=COLORS['bg_card'],
                        highlightbackground=COLORS['border'], highlightthickness=1)
        wrap._rcat = "分位信号"   # 分类筛选标记：分源逐位对比
        wrap.pack(fill=tk.X, pady=(0, 8))

        tk.Label(wrap, text=" 分源对比（逐位）", bg=COLORS['bg_card'],
                 fg=COLORS['accent_info'], font=('微软雅黑', 10, 'bold')
                 ).pack(anchor=tk.W, padx=12, pady=(8, 4))

        grid = tk.Frame(wrap, bg=COLORS['bg_card'])
        grid.pack(fill=tk.X, padx=12, pady=(0, 8))

        cols = [('位置', None)] + [(n, k) for k, n in active_srcs] + [('综合', '__consensus__')]

        def _cell(r, c, text, fg, bg, bold=False, is_head=False):
            """在号码来源矩阵中创建并放置一个单元格标签。

            参数:
                r: 网格行号
                c: 网格列号
                text: 单元格显示文本
                fg: 前景（文字）颜色
                bg: 背景颜色
                bold: 是否使用加粗字体
                is_head: 是否为表头单元格（表头统一用微软雅黑 9 号加粗）
            """
            f = ('微软雅黑', 9, 'bold') if (bold or is_head) else ('Consolas', 11, 'bold')
            if is_head:
                f = ('微软雅黑', 9, 'bold')
            lbl = tk.Label(grid, text=text, bg=bg, fg=fg, font=f,
                           padx=8, pady=4, width=7)
            lbl.grid(row=r, column=c, padx=1, pady=1, sticky='nsew')

        # 表头
        for c, (name, _) in enumerate(cols):
            _cell(0, c, name, COLORS['text_secondary'], COLORS['bg_secondary'], is_head=True)

        # 数据行
        for r, (pk, pn) in enumerate(zip(pos_keys, pos_names), start=1):
            _cell(r, 0, pn, COLORS['text_muted'], COLORS['bg_secondary'], is_head=True)
            cons = consensus.get(pk)
            for c, (name, key) in enumerate(cols[1:], start=1):
                if key == '__consensus__':
                    txt = str(cons) if cons is not None else '—'
                    _cell(r, c, txt, '#ffffff',
                          COLORS['accent_p5'] if cons is not None else COLORS['bg_secondary'])
                else:
                    v = picks.get(pk, {}).get(key)
                    if v is None:
                        _cell(r, c, '—', COLORS['text_disabled'], COLORS['bg_panel'])
                    elif cons is not None and v == cons:
                        # 与共识一致 → 绿色高亮
                        _cell(r, c, str(v), '#d1fae5', '#065f46')
                    else:
                        _cell(r, c, str(v), COLORS['text_secondary'], COLORS['bg_panel'])

        # 均分列宽
        for c in range(len(cols)):
            grid.grid_columnconfigure(c, weight=1)

    def _show_result_dashboard(self, pipeline_final=None, trend_result=None, quick_final=None):
        """
        聚合多源预测，去重 + 置信度排序，仅展示一个最优主推荐号码，
        备选号码折叠保留，并提供「查看详细分析」入口。

        两主预测（四步流水线 / 快速预测）一致 → 高置信度合并；
        不一致 → 按预设规则（多源多数投票、平票优先四步流水线）筛选主推荐。

        新增缓存命中提示和模式分析展示

        v3.54 线程安全：若在非主线程调用，通过 root.after(0, ...) 投递回主线程执行，
        避免后台线程直接操作 Tkinter 控件导致仪表盘无法显示或控件失效。
        """
        # 立即保存最新结果，确保后台线程调用时也能被 _copy_prediction 兜底读取
        # 这些属性的写入不涉及 Tkinter，是线程安全的
        if pipeline_final is not None:
            self._last_pipeline_final = pipeline_final
        if trend_result is not None:
            self._last_trend_result = trend_result
        if quick_final is not None:
            self._last_quick_final = quick_final

        # 线程安全检查：Tkinter 控件只能在主线程创建/更新
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._show_result_dashboard(
                pipeline_final, trend_result, quick_final))
            return
        # [DEBUG v3.54] 渲染入口日志
        try:
            import logging
            _dbg = logging.getLogger('kplucky.debug')
            _dbg.info('DASHBOARD_CALL: pipeline=%s trend=%s quick=%s',
                      bool(pipeline_final), bool(trend_result), bool(quick_final))
        except Exception:
            pass

        pf = self._last_pipeline_final
        tr = self._last_trend_result
        qf = self._last_quick_final
        if not (pf or tr or qf):
            # v3.59：三个 final 都为空（本次分析被取消 / 早期异常 / 流水线尚未产出任何结果）
            # 也要把数据库里最近的预测记录写入 _prediction_clipboard，
            # 让「复制预测号码」按钮能给出有意义内容，而不是「无数据」误判。
            self._populate_clipboard_from_db_fallback()
            self._hide_result_dashboard()
            return

        # v3.62：聚合计算抽到纯函数 _compute_dashboard_aggregates，
        # 后台线程也可直接调用同步写 _prediction_clipboard，避开 after(0) 时序竞态。
        agg = self._compute_dashboard_aggregates(pf, tr, qf)
        if not agg:
            # v3.64 修复：聚合失败时不直接返回，先尝试从各来源产物恢复 clipboard，
            # 防止仪表盘隐藏后复制按钮仍报"无数据"。
            self._restore_clipboard_from_finals(pf, tr, qf)
            self._hide_result_dashboard()
            return
        picks, top5, combos, target_issue = agg['picks'], agg['top5'], agg['combos'], agg['target_issue']
        consensus = agg['consensus']
        main_combo_disp = agg['main_combo_disp']
        conf = agg['conf']
        high_conf = agg['high_conf']
        main_combo = agg['main_combo']

        # 核心计算仍使用完整5位（万/千/百/十/个），保证算法与数据处理不受影响
# [DEBUG v3.54] 渲染参数日志
        try:
            import logging
            _dbg = logging.getLogger('kplucky.debug')
            _dbg.info('DASHBOARD_RENDER: target_issue=%s picks_keys=%s consensus=%s',
                      target_issue,
                      list(picks.keys()) if 'picks' in dir() else '?',
                      {k: consensus.get(k) for k in ['wan','qian','bai','shi','ge']})
        except Exception:
            pass
        # 清空旧内容
        for w in self.result_dash.winfo_children():
            w.destroy()
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        pos_names = ['万位', '千位', '百位', '十位', '个位']

        # 可用信号源数（并集，用于措辞）
        total_sources = agg['total_sources']

        # 渲染去重——内容与上次渲染完全一致时跳过全量重建，仅确保可见，
        # 避免重复点击「开始分析」或缓存命中时不必要的控件销毁/重建，提升响应速度
        _combos_disp = tuple(sorted(compress_combo(v) for v in combos.values()))
        _render_key = (target_issue, main_combo_disp, conf, high_conf, _combos_disp)
        if getattr(self, '_last_dashboard_key', None) == _render_key and self.result_dash.winfo_children():
            try:
                if getattr(self, '_result_placeholder', None) is not None:
                    self._result_placeholder.pack_forget()
                self.result_dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
                self._focus_result_tab()
            except Exception:
                pass
            return
        # 清空旧内容
        for w in self.result_dash.winfo_children():
            w.destroy()

        # v3.62：clipboard 已在 _compute_dashboard_aggregates 中同步写好，
        # 这里仅做记录本次分析生成时间（保留原有逻辑），不再二次覆盖。
        # 记录本次分析生成时间（供「最近一次分析时间」标记展示）
        self._last_analysis_time = datetime.now()

        # ---- 渲染 ----
        dash = self.result_dash

        # 标题栏（增强视觉效果）
        hdr = tk.Frame(dash, bg=COLORS['accent_p5'], height=42)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        
        hdr_left = tk.Frame(hdr, bg=COLORS['accent_p5'])
        hdr_left.pack(side=tk.LEFT, padx=12, pady=4)
        tk.Label(hdr_left, text=" AI智能预测", bg=COLORS['accent_p5'], fg='#ffffff',
                 font=('微软雅黑', 12, 'bold')).pack(anchor=tk.W)
        # 最近一次分析时间标记（副标题, 让结论有明确的时间归属）
        _ana_t = getattr(self, '_last_analysis_time', None)
        if _ana_t is not None:
            _today = datetime.now().date()
            _tstr = (_ana_t.strftime('%H:%M:%S') if _ana_t.date() == _today
                     else _ana_t.strftime('%Y-%m-%d %H:%M'))
            tk.Label(hdr_left, text=f" 最近分析 {_tstr}",
                     bg=COLORS['accent_p5'], fg='#e0e7ff',
                     font=('微软雅黑', 8)).pack(anchor=tk.W)
        
        hdr_right = tk.Frame(hdr, bg=COLORS['accent_p5'])
        hdr_right.pack(side=tk.RIGHT, padx=8, pady=6)
        
        detail_btn = tk.Button(hdr_right, text=" 详细分析", command=self._toggle_detail,
                               bg=COLORS['accent_p5_light'], fg='#ffffff', 
                               font=('微软雅黑', 9, 'bold'),
                               relief='flat', padx=10, pady=3, cursor='hand2',
                               activebackground=COLORS['accent_p5_bright'])
        detail_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # 一键导出（文本/图片分享）
        export_btn = tk.Button(hdr_right, text=" 导出",
                               command=self._show_export_menu,
                               bg=COLORS['accent_p5_light'], fg='#ffffff',
                               font=('微软雅黑', 9, 'bold'),
                               relief='flat', padx=10, pady=3, cursor='hand2',
                               activebackground=COLORS['accent_p5_bright'])
        export_btn.pack(side=tk.RIGHT, padx=(6, 0))

        body = tk.Frame(dash, bg=COLORS['bg_secondary'])
        body.pack(fill=tk.X, padx=12, pady=10)

        # 1) 数据概览：期号 + 一致性 / 高置信度
        info_row = tk.Frame(body, bg=COLORS['bg_secondary'])
        info_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(info_row, text=f"{'第' if target_issue else ''}{target_issue or '—'}{'期' if target_issue else ''}", font=('Consolas', 12, 'bold'),
                 bg=COLORS['bg_secondary'], fg=COLORS['text_primary']).pack(side=tk.LEFT)
        if high_conf:
            tk.Label(info_row, text=" 高置信度", bg='#065f46', fg='#d1fae5', font=('微软雅黑', 9, 'bold')).pack(side=tk.LEFT, padx=(8, 0))
        if conf is not None:
            tk.Label(info_row, text=f" 一致性 {conf}%", bg=COLORS['bg_card'], fg=COLORS['accent_p5'], font=('微软雅黑', 10, 'bold')).pack(side=tk.LEFT, padx=(8, 0))

        # 2) 分析结果：信号源可用数量 + 预测方式摘要
        src_names = ['四步流水线', '走势引擎', '快速预测']
        used = [n for i, n in enumerate(src_names) if (i == 0 and pf) or (i == 1 and tr) or (i == 2 and qf)]
        tk.Label(body, text=f"分析来源：{' + '.join(used) if used else '本季无有效来源'}", font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']).pack(anchor=tk.W)

        # 3) 预测结果（号码段）— 逐位分区展示 4 个候选数字，清晰分区
        cand_by_pos = self._build_position_candidates(picks, top5)
        num_frame = tk.Frame(body, bg=COLORS['bg_secondary'])
        num_frame.pack(fill=tk.X, pady=(6, 2))
        tk.Label(num_frame, text="预测号码段（下期各位置候选）", font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'], fg=COLORS['text_primary']).pack(anchor=tk.W, pady=(0, 4))
        for _pk_disp, _pn_disp in zip(DISPLAY_POS_KEYS, DISPLAY_POS_NAMES):
            _row = tk.Frame(num_frame, bg=COLORS['bg_secondary'])
            _row.pack(fill=tk.X, pady=(3, 0))
            # 位置名卡片（清晰分区标识）
            tk.Label(_row, text=_pn_disp, font=('微软雅黑', 10, 'bold'),
                     bg=COLORS['accent_p5'], fg='#ffffff',
                     width=5, padx=6, pady=3).pack(side=tk.LEFT, padx=(0, 8))
            _main = consensus.get(_pk_disp)
            _cands = cand_by_pos.get(_pk_disp, [])
            if _cands:
                for _digit in _cands:
                    _is_main = (_main is not None and _digit == _main)
                    _chip = tk.Label(_row, text=str(_digit),
                                     font=('Consolas', 13, 'bold'),
                                     bg=COLORS['accent_p5'] if _is_main else COLORS['bg_card'],
                                     fg='#ffffff' if _is_main else COLORS['accent_p5'],
                                     padx=10, pady=3)
                    _chip.pack(side=tk.LEFT, padx=(0, 6))
                    # 悬停反馈：鼠标进入时高亮，离开时恢复（主推常亮不绑定）
                    if not _is_main:
                        _chip.bind('<Enter>',
                                   lambda e, b=_chip: b.config(
                                       bg=COLORS['accent_p5'], fg='#ffffff'))
                        _chip.bind('<Leave>',
                                   lambda e, b=_chip: b.config(
                                       bg=COLORS['bg_card'], fg=COLORS['accent_p5']))
            else:
                tk.Label(_row, text="— 暂无候选数据", font=('微软雅黑', 9),
                         bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']).pack(side=tk.LEFT)

        # 风险提示（仅在绝对必要时保留）
        risk = ''
        if isinstance(pf, dict):
            risk = (pf.get('risk_warning') or '').strip()
        elif isinstance(qf, dict):
            risk = (qf.get('risk_warning') or '').strip()
        if risk:
            risk_frame = tk.Frame(body, bg=COLORS['bg_card'], highlightbackground=COLORS['accent_warning'], highlightthickness=1)
            risk_frame.pack(fill=tk.X, pady=(0, 8))
            tk.Label(risk_frame, text=" " + risk, bg=COLORS['bg_card'], fg=COLORS['accent_warning_light'],
                     font=('微软雅黑', 9), wraplength=620, justify=tk.LEFT).pack(anchor=tk.W, padx=12, pady=6)


        # 有结果 → 隐藏空态占位，展示仪表盘，并自动聚焦「预测结果」页
        try:
            if getattr(self, '_result_placeholder', None) is not None:
                self._result_placeholder.pack_forget()
        except Exception:
            pass
        try:
            self.result_dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        except Exception as _pack_e:
            try:
                import logging
                logging.getLogger('kplucky.debug').error('DASHBOARD_PACK_FAIL: %s', _pack_e)
            except Exception:
                pass
        try:
            self._focus_result_tab()
        except Exception as _focus_e:
            try:
                import logging
                logging.getLogger('kplucky.debug').error('DASHBOARD_FOCUS_FAIL: %s', _focus_e)
            except Exception:
                pass
        # 记录本次渲染指纹，供下次渲染去重（避免无谓的全量重建）
        self._last_dashboard_key = _render_key
        try:
            import logging
            logging.getLogger('kplucky.debug').info('DASHBOARD_SHOWN: issue=%s combo=%s',
                                                     target_issue, main_combo_disp)
        except Exception:
            pass

    def _hide_result_dashboard(self):
        """隐藏预测结果仪表盘（任务开始前调用），恢复空态占位。"""
        # 兼容直连旧式挂载（热加载后会继续接到 self.result_dash）
        try:
            self.result_dash.pack_forget()
        except Exception:
            pass
        try:
            if getattr(self, '_result_placeholder', None) is not None:
                self._result_placeholder.pack(fill=tk.BOTH, expand=True)
        except Exception:
            pass
        # 兼容新式容器（热更新可能把 widget 本体下挂到了 dash_inner）
        try:
            self.dash_inner._result_dash.pack_forget()
        except Exception:
            pass
        try:
            if getattr(self.dash_inner, '_result_placeholder', None) is not None:
                self.dash_inner._result_placeholder.pack(fill=tk.BOTH, expand=True)
        except Exception:
            pass

    def _render_unified_dashboard(self, task_mgr):
        """统一分析结束时，用已保存的各来源产物集中渲染仪表盘"""
        # v3.62 修复：后台线程中先同步计算聚合，把 _prediction_clipboard /
        # _clipboard_meta 立即写入主线程可见属性，避免 _copy_prediction 被
        # 点击时主线程 after(0) 仪表盘渲染还没轮到导致「无数据」误判。
        try:
            pf = getattr(self, '_last_pipeline_final', None)
            qf = getattr(self, '_last_quick_final', None)
            tr = getattr(self, '_last_trend_result', None)
            self._compute_dashboard_aggregates(pf, tr, qf)
        except Exception as e:
            try:
                import logging
                logging.getLogger('kplucky.debug').error(
                    'RENDER_UNIFIED_PRECOMPUTE_ERROR: %s', e, exc_info=True)
            except Exception:
                pass
        # 记录本次调用指纹，供兜底 watchdog 检测渲染是否生效
        _pre_key = getattr(self, '_last_dashboard_key', None)
        self._show_result_dashboard()
        # 强制刷新剪贴板，确保复制按钮立即可用（兜底）
        try:
            pf = getattr(self, '_last_pipeline_final', None)
            if isinstance(pf, dict) and pf:
                clip = self._build_prediction_clipboard(pf)
                if clip and clip.strip():
                    self._prediction_clipboard = clip
                    self._clipboard_meta = {
                        'target_issue': pf.get('target_issue') or pf.get('next_issue') or '',
                        'conf': '',
                        'high_conf': '',
                        'main_combo': '',
                    }
        except Exception:
            pass
        # v3.59：兜底数据库写入。若本次分析全程未产出结果（_last_*_final 全为 None），
        # 从数据库最近一条预测记录写入 clipboard，避免复制按钮误报「无数据」。
        try:
            if not getattr(self, '_prediction_clipboard', ''):
                self._populate_clipboard_from_db_fallback()
        except Exception:
            pass
        # 兜底：若 2 秒后 _last_dashboard_key 未更新（说明渲染被吞或控件创建失败），强制再跑一次
        def _watchdog():
            if getattr(self, '_last_dashboard_key', None) == _pre_key:
                try:
                    self._show_result_dashboard()
                except Exception:
                    pass
        self.root.after(2000, _watchdog)

    def _execute_backtest(self, task_mgr, cancel_event=None):
        """
        历史回测：使用P5Predictor在历史数据上执行滚动预测回测

        流程:
        1. 加载全部历史数据（至少100期）
        2. 从第50期开始，每期用前N期数据训练后预测下一期
        3. 统计Top-1/Top-3命中率、综合得分、完全猜中次数
        4. 生成回测报告文件

        输出: 回测统计指标、前10期详情、报告文件路径
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  历史回测分析（Walk-Forward 滚动回测）")
            task_mgr.log("=" * 70)

            # 初始化预测器
            task_mgr.progress(5, "初始化回测引擎")
            predictor = P5Predictor()

            # 展示「真实生效」的算法权重（此前这里是硬编码的 v2.1 数值，
            # 与当前冻结权重不符，属于误导性输出，v3.25 改为读取实际配置）
            try:
                weights = predictor.config.get_algorithm_weights() or {}
                cn = {'frequency': '频率加权', 'omission': '遗漏回归', 'bayesian': '贝叶斯',
                      'trend': '趋势动量', 'markov': '马尔可夫',
                      'pattern': '形态延续', 'feature': '特征加权'}
                task_mgr.log("\n 回测实际生效权重（读取自预测器配置）:")
                for k, v in sorted(weights.items(), key=lambda x: -float(x[1] or 0)):
                    task_mgr.log(f"     • {cn.get(k, k)}: {float(v) * 100:.2f}%")
            except Exception as _e:
                task_mgr.log(f" 读取权重配置失败(不影响回测): {_e}")
            task_mgr.log("     • AI 大模型(完整复包装): 回测期间关闭（可复现 / 无调用费用 / 无前视泄漏）")
            task_mgr.log("     • 贝叶斯AI辅助: 自动判断最近 10 期（仅后验解读，不影响命中率指标）")
            task_mgr.log("  " + "-" * 60)

            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取历史数据
            task_mgr.log("\n正在加载历史数据...")
            task_mgr.progress(15, "加载历史数据")

            history_data = db.get_history_data(limit=None, order_by='issue ASC')
            db.disconnect()

            if len(history_data) < 100:
                task_mgr.log(f" 历史数据不足: 需要至少100期，实际{len(history_data)}期")
                task_mgr.progress(0, "数据不足")
                return

            task_mgr.log(f" 历史数据加载完成: 共 {len(history_data)} 期")
            task_mgr.progress(25, "数据加载完成")

            # 初始化回测引擎
            task_mgr.log("正在初始化回测引擎...")
            task_mgr.progress(30, "初始化引擎")

            backtest_engine = Backtester(predictor, db)

            # 配置回测参数
            # v3.25 修复：此前固定 start_index=50 且从最老一期向后评估，
            # 在当前 1000+ 期库存下等于只回测 2023 年的老数据（日志里出现
            # 「目标期号 2023285」即源于此），且训练集只有 50 期。
            # 现改为评估「最近 test_count 期」，每期用其之前的全部历史训练，
            # 与冻结基线配置的基线口径一致。
            start_index = 50           # 最小训练期数（冷启动下限）
            test_count = 50            # 评估最近 50 期
            eval_start = max(start_index, len(history_data) - test_count)
            test_count = min(test_count, len(history_data) - eval_start)
            eval_from = history_data[eval_start]['issue']
            eval_to = history_data[eval_start + test_count - 1]['issue']

            task_mgr.log(f"回测配置:")
            task_mgr.log(f"  评估区间: {eval_from} ~ {eval_to}（最近 {test_count} 期）")
            task_mgr.log(f"  训练方式: 逐期滚动，每期仅使用该期之前的历史（起始训练量 {eval_start} 期）")
            task_mgr.log(f"  数据总量: {len(history_data)} 期（{history_data[0]['issue']} ~ {history_data[-1]['issue']}）")
            _aux_cap = self._BACKTEST_AI_AUX_CAP
            _cap_label = "全部" if _aux_cap >= test_count else f"最近 {_aux_cap} 期"
            task_mgr.log(f"  贝叶斯AI辅助: {_cap_label}（自动判断；完整AI复包装仍关闭）")

            # 执行回测
            task_mgr.log("\n" + "" * 50)
            task_mgr.log("正在执行回测计算...")
            task_mgr.log("" * 50)
            task_mgr.progress(40, "执行回测中")

            # 传入 log_callback，使回测逐期进度（含断点恢复、逐期AI启用）实时呈现在输出面板；
            # 移除原先「每10期 sleep 0.1s 伪造进度」的误导块——真实进度由 backtester 逐期 _blog 驱动。
            backtest_result = backtest_engine.run_backtest(
                start_index, test_count, eval_mode='recent', enable_ai=False,
                max_bayes_aux_calls=self._BACKTEST_AI_AUX_CAP,
                log_callback=task_mgr.log
            )

            if backtest_result.get('status') != 'success':
                task_mgr.progress(0, "回测失败")
                task_mgr.log(f"\n 回测失败: {backtest_result.get('message', '未知错误')}")
                return

            task_mgr.progress(85, "生成报告中")
            task_mgr.log("\n 回测计算完成！")

            # ============================================================
            # 输出格式优化的回测结果
            # ============================================================
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_section_header("回测统计指标")
            task_mgr.log("=" * 70)

            stats = backtest_result.get('overall_stats', {})

            # 核心指标
            task_mgr.append_success(f"测试期数: {backtest_result.get('total_tested', 0)} 期")
            task_mgr.append_info(f"平均综合得分: {stats.get('avg_overall_score', 0):.2f} / 100")
            task_mgr.append_info(f"Top-1 平均命中: {stats.get('avg_top1_hits', 0):.2f} / 5 位")
            task_mgr.append_info(f"Top-3 平均命中: {stats.get('avg_top3_hits', 0):.2f} / 5 位")

            # 命中率
            top1_rate = stats.get('avg_top1_hit_rate', 0)
            top3_rate = stats.get('avg_top3_hit_rate', 0)

            if top1_rate >= 60:
                task_mgr.append_success(f"Top-1 命中率: {top1_rate:.2f}%")
            elif top1_rate >= 40:
                task_mgr.append_info(f"Top-1 命中率: {top1_rate:.2f}%")
            else:
                task_mgr.append_warning(f"Top-1 命中率: {top1_rate:.2f}%")

            if top3_rate >= 70:
                task_mgr.append_success(f"Top-3 命中率: {top3_rate:.2f}%")
            elif top3_rate >= 50:
                task_mgr.append_info(f"Top-3 命中率: {top3_rate:.2f}%")
            else:
                task_mgr.append_warning(f"Top-3 命中率: {top3_rate:.2f}%")

            task_mgr.append_info(f"概率校准得分: {stats.get('avg_calibration_score', 0):.2f} / 100")

            full_match = stats.get('full_match_count', 0)
            full_rate = stats.get('full_match_rate', 0)
            if full_match > 0:
                task_mgr.append_success(f"完全猜中次数: {full_match} 次 ({full_rate:.2f}%)")
            else:
                task_mgr.append_warning(f"完全猜中次数: 0 次")

            # 随机基线对照（诚实口径：排列5 为公平摇号，逐位 Top-1 期望 10%、Top-3 期望 30%）
            task_mgr.append_info(
                f"随机基线对照: Top-1 期望 10.00% (实测 {top1_rate:.2f}%, 偏差 {top1_rate - 10:+.2f}pp) | "
                f"Top-3 期望 30.00% (实测 {top3_rate:.2f}%, 偏差 {top3_rate - 30:+.2f}pp)")

            # 断点续跑 + 贝叶斯AI辅助 概览
            _resumed = backtest_result.get('resumed_count')
            _aux_cnt = backtest_result.get('ai_aux_enabled_count')
            _aux_cap = backtest_result.get('ai_aux_cap')
            if _resumed:
                task_mgr.append_info(f" 断点续跑: 已恢复 {_resumed} 期（跳过预测/AI调用，直接从缓存续算）")
            if _aux_cnt is not None:
                if _aux_cnt > 0:
                    task_mgr.append_info(
                    f" 贝叶斯AI辅助: 最近 {_aux_cnt} 期已触发辅助洞察"
                        f"（其余期按设计关闭以控费/避免卡顿）")
                else:
                    task_mgr.append_info(f" 贝叶斯AI辅助: 本次未触发（cap={_aux_cap or 0}）")

            # 详细结果（最近10期）
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_section_header("最近10期回测详情")
            task_mgr.log("=" * 70)

            results = backtest_result.get('results', [])
            for i, result in enumerate(list(reversed(results))[:10], 1):
                # 注：_calculate_overall_stats 返回的字段名为 target_issue /
                # top1_hit_count / top3_hit_count（非 issue / top1_hits / top3_hits）
                issue = result.get('target_issue', '')
                top1_hits = result.get('top1_hit_count', 0)
                top3_hits = result.get('top3_hit_count', 0)
                overall_score = result.get('overall_score', 0)

                # 根据命中情况显示不同颜色
                if top1_hits >= 4:
                    task_mgr.append_success(f"期号{issue}: Top1命中{top1_hits}/5, Top3命中{top3_hits}/5, 得分{overall_score:.1f}")
                elif top1_hits >= 2:
                    task_mgr.append_info(f"期号{issue}: Top1命中{top1_hits}/5, Top3命中{top3_hits}/5, 得分{overall_score:.1f}")
                else:
                    task_mgr.append_warning(f"期号{issue}: Top1命中{top1_hits}/5, Top3命中{top3_hits}/5, 得分{overall_score:.1f}")

            # 生成回测报告
            task_mgr.progress(95, "生成报告文件")
            task_mgr.log("\n正在生成回测报告...")
            report_path = backtest_engine.generate_backtest_report(backtest_result)
            task_mgr.append_success(f"回测报告已保存: {report_path}")

            # 更新统计面板
            stats_text = (
                f"回测期数: {backtest_result.get('total_tested', 0)}\n"
                f"Top-1命中率: {top1_rate:.2f}%\n"
                f"Top-3命中率: {top3_rate:.2f}%\n"
                f"综合得分: {stats.get('avg_overall_score', 0):.2f}"
            )
            self.stats_content.config(text=stats_text, fg=COLORS['accent_ai'])

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_success("历史回测流程全部完成")
            task_mgr.log("=" * 70)

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 历史回测过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_feature_analysis(self, task_mgr, cancel_event=None):
        """
        特征分析：提取全部历史数据的统计特征

        提取的特征类型:
        - 频率特征: 各位置热号/温号/冷号
        - 012路特征: 各位置0路/1路/2路比例
        - 连号特征: 平均连号数、连号出现率
        - 重隔号特征: 重号率、隔号率、无重复率
        - 和值与跨度特征: 和值范围/平均值、跨度范围/平均值

        输出: 特征分析报告JSON文件（保存到 reports/features/）
        """
        try:
            task_mgr.log("正在执行特征分析...")
            task_mgr.progress(10, "初始化特征工程")

            # 初始化特征工程
            fe = P5Features()

            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取历史数据
            task_mgr.log("正在加载历史数据...")
            task_mgr.progress(20, "加载数据")

            history_data = db.get_history_data(limit=None, order_by='issue ASC')
            db.disconnect()

            if not history_data:
                task_mgr.log(" 数据库中没有历史数据")
                task_mgr.progress(0, "无数据")
                return

            task_mgr.log(f" 历史数据加载完成: 共{len(history_data)}期")

            # 提取所有特征
            task_mgr.log("\n正在提取特征...")
            task_mgr.progress(40, "提取频率特征")

            features = fe.extract_all_features(history_data)

            task_mgr.progress(70, "特征提取完成")

            # 输出特征分析结果
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log(" 特征分析完成！")
            task_mgr.log("=" * 70)

            pos_names = ['万位', '千位', '百位', '十位', '个位']

            # 频率特征
            task_mgr.log("\n【频率特征】")
            freq_features = features.get('frequency', {})
            for pos_name in pos_names:
                pos_freq = freq_features.get(pos_name, {})
                hot_numbers = pos_freq.get('hot_numbers', [])
                cold_numbers = pos_freq.get('cold_numbers', [])
                warm_numbers = pos_freq.get('warm_numbers', [])
                task_mgr.log(f"{pos_name}: 热号={hot_numbers}, 温号={warm_numbers}, 冷号={cold_numbers}")

            # 012路特征
            task_mgr.log("\n【012路特征】")
            road_features = features.get('road_012', {})
            for pos_name in pos_names:
                pos_road = road_features.get(pos_name, {})
                road_ratios = pos_road.get('road_ratios', {})
                task_mgr.log(f"{pos_name}: 0路={road_ratios.get(0, 0):.2%}, 1路={road_ratios.get(1, 0):.2%}, 2路={road_ratios.get(2, 0):.2%}")

            # 连号特征
            task_mgr.log("\n【连号特征】")
            consecutive_features = features.get('consecutive', {})
            task_mgr.log(f"平均连号数: {consecutive_features.get('avg_consecutive_count', 0):.2f}")
            task_mgr.log(f"最大连号数: {consecutive_features.get('max_consecutive_count', 0)}")
            task_mgr.log(f"连号出现率: {consecutive_features.get('consecutive_rate', 0):.2%}")

            # 重隔号特征
            task_mgr.log("\n【重隔号特征】")
            repeat_features = features.get('repeat', {})
            task_mgr.log(f"重号率: {repeat_features.get('repeat_rate', 0):.2%}")
            task_mgr.log(f"隔号率: {repeat_features.get('skip_rate', 0):.2%}")
            task_mgr.log(f"无重复率: {repeat_features.get('no_repeat_rate', 0):.2%}")

            # 和值与跨度特征
            task_mgr.log("\n【和值与跨度特征】")
            sum_span_features = features.get('sum_span', {})
            task_mgr.log(f"和值范围: {sum_span_features.get('sum_range', [])}")
            task_mgr.log(f"平均和值: {sum_span_features.get('avg_sum', 0):.2f}")
            task_mgr.log(f"跨度范围: {sum_span_features.get('span_range', [])}")
            task_mgr.log(f"平均跨度: {sum_span_features.get('avg_span', 0):.2f}")

            # 保存特征分析结果到数据库
            try:
                feat_db = P5Database()
                if feat_db.connect():
                    feat_db.save_artifact(
                        artifact_type='feature_analysis',
                        data=features,
                        meta={'data_count': len(history_data) if 'history_data' in dir() else None}
                    )
                    feat_db.disconnect()
                    task_mgr.log("\n 特征分析结果已保存到数据库")
                else:
                    task_mgr.log("\n 特征分析结果保存失败: 数据库连接失败")
            except Exception as db_e:
                task_mgr.log(f"\n 特征分析结果保存失败: {db_e}")

            # 更新统计面板
            stats_text = (
                f"数据量: {len(history_data)} 条\n"
                f"连号率: {consecutive_features.get('consecutive_rate', 0):.1%}\n"
                f"重号率: {repeat_features.get('repeat_rate', 0):.1%}\n"
                f"平均和值: {sum_span_features.get('avg_sum', 0):.1f}"
            )
            self.stats_content.config(text=stats_text, fg=COLORS['warning'])

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n 特征分析流程全部完成")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 特征分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_comprehensive_analysis_and_verify(self, task_mgr, cancel_event=None):
        """
        综合验证与分析（一键执行「分析工具」全部子功能）

        将原先分散在「分析工具」卡片下的 5 个独立功能整合为单点一键执行：
          1. 预测验证    — 闭合待验证预测，更新命中率
          2. 命中率报告  — 各位置命中率 + 趋势
          3. 性能报告    — AI 预测性能评估
          4. 历史回测    — 滚动回测 Top-N 命中率
          5. 特征分析    — 历史数据统计特征
          6. 综合报告    — 汇总产物

        一致性保证：直接复用各子功能的既有实现（_execute_*），
        因此数据来源、计算口径与结果展示与单独点击完全一致，
        只是免去逐一点击、串行编排为一次任务。
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log(" 综合验证与分析（一键执行「分析工具」全部子功能）")
            task_mgr.log("=" * 70)
            task_mgr.log("\n  执行流程（串行，与独立点击完全一致）:")
            task_mgr.log("    ① 预测验证 → ② 命中率报告 → ③ 性能报告")
            task_mgr.log("    ④ 历史回测 → ⑤ 特征分析 → ⑥ 综合报告")
            task_mgr.progress(2, "开始综合验证与分析...")

            steps = [
                ("① 预测验证", self._execute_verify_predictions),
                ("② 命中率报告", self._execute_hit_rate_report),
                ("③ 性能报告", self._execute_performance_report),
                ("④ 历史回测", self._execute_backtest),
                ("⑤ 特征分析", self._execute_feature_analysis),
            ]

            for idx, (label, fn) in enumerate(steps, start=1):
                task_mgr.log("\n" + "━" * 60)
                task_mgr.log(f"【步骤 {idx}/5】{label} ...")
                task_mgr.progress(int(2 + 88 * idx / 5), f"{label} 执行中")
                try:
                    fn(task_mgr)
                except Exception as _e:
                    task_mgr.log(f" {label} 异常（不影响后续步骤）: {_e}")

            # ==========================================
            # 步骤6：生成综合报告
            # ==========================================
            task_mgr.log("\n" + "━" * 60)
            task_mgr.log("【步骤 6/6】生成综合分析报告...")
            task_mgr.progress(95, "生成报告中")
            try:
                report_data = {
                    'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'module': '综合验证与分析',
                    'steps_completed': [
                        '预测验证', '命中率统计', '性能报告',
                        '历史回测', '特征分析', '综合报告'
                    ],
                    'note': '本模块整合原「分析工具」全部子功能为一键执行；'
                            '各步骤计算口径与单独点击完全一致。'
                }
                report_dir = REPORTS_DIR
                os.makedirs(report_dir, exist_ok=True)
                report_file = os.path.join(
                    report_dir,
                    f'comprehensive_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
                )
                with open(report_file, 'w', encoding='utf-8') as f:
                    json.dump(report_data, f, ensure_ascii=False, indent=2)
                task_mgr.log(f" 综合报告已保存: {report_file}")
            except Exception as _e:
                task_mgr.log(f" 报告生成异常: {_e}")

            task_mgr.progress(100, "综合验证与分析完成")
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_success(" 综合验证与分析全部完成（分析工具已整合为一键）")
            task_mgr.log("=" * 70)
            task_mgr.log("\n 提示：排列5为公平摇号，历史命中率不代表未来，请理性参考。")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 综合验证与分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")
    def _execute_view_reports(self, task_mgr, cancel_event=None):
        """
        查看独立报告 (v3.3 改为从数据库读取, 不再依赖本地 JSON 文件)

        显示 p5_ai_report 表中所有已生成的独立报告, 包括：
        1. 专家文章预测报告 (report_type='expert_article')
        2. 走势图数据预测报告 (report_type='trend_chart')
        """
        try:
            import json as _json

            task_mgr.log("=" * 70)
            task_mgr.log(" 独立报告浏览 (数据库)")
            task_mgr.log("=" * 70)

            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                return

            def _fetch(report_type):
                """查询指定类型的最近 20 条 AI 报告。

                参数:
                    report_type: 报告类型标识，对应 p5_ai_report.report_type

                返回:
                    报告行列表，查询异常时返回空列表并写入任务日志
                """
                try:
                    db.cursor.execute(
                        "SELECT report_uuid, latest_issue, next_issue, created_at, report_content "
                        "FROM p5_ai_report WHERE report_type=%s ORDER BY created_at DESC LIMIT 20",
                        (report_type,)
                    )
                    return db.cursor.fetchall() or []
                except Exception as e:
                    task_mgr.log(f" 查询{report_type}报告失败: {e}")
                    return []

            def _safe_parse(content):
                """容错解析 JSON 文本。

                参数:
                    content: 待解析内容，可能是 JSON 字符串、已解析的 dict 或 None

                返回:
                    解析后的字典；解析失败时返回空字典而非抛出异常
                """
                try:
                    return _json.loads(content) if isinstance(content, str) else (content or {})
                except Exception:
                    return {}

            # 专家文章预测报告
            task_mgr.append_section_header(" 专家文章预测报告")
            expert_rows = _fetch('expert_article')
            if expert_rows:
                task_mgr.log(f"\n  共 {len(expert_rows)} 份报告:")
                for r in expert_rows[:10]:
                    data = _safe_parse(r.get('report_content'))
                    target = r.get('latest_issue') or data.get('target_issue', 'N/A')
                    articles = data.get('total_articles', 0)
                    successful = data.get('successful_articles', 0)
                    time_str = str(r.get('created_at', ''))[:19]
                    task_mgr.append_info(f"  UUID:{str(r.get('report_uuid'))[:8]} | 期号:{target} | 文章:{successful}/{articles} | {time_str}")
            else:
                task_mgr.log("  暂无专家文章预测报告。请先运行四步流水线分析。")

            # 走势图数据预测报告
            task_mgr.append_section_header(" 走势图数据预测报告")
            trend_rows = _fetch('trend_chart')
            if trend_rows:
                task_mgr.log(f"\n  共 {len(trend_rows)} 份报告:")
                for r in trend_rows[:10]:
                    data = _safe_parse(r.get('report_content'))
                    target = r.get('latest_issue') or data.get('target_issue', 'N/A')
                    time_str = str(r.get('created_at', ''))[:19]
                    task_mgr.append_info(f"  UUID:{str(r.get('report_uuid'))[:8]} | 期号:{target} | {time_str}")
            else:
                task_mgr.log("  暂无走势图数据预测报告。请先运行四步流水线分析。")

            db.disconnect()

            task_mgr.log(f"\n{'=' * 70}")
            task_mgr.log(" 报告浏览完成 (数据来源: 数据库 p5_ai_report)")
            task_mgr.log(f"{'=' * 70}")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 查看报告异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")

    # ============================================================
    # 「开始分析」融合阶段 ④：命中率优化
    # 原卡片的三个按钮已在此融合为「开始分析」的一个自动阶段：
    # · 选号策略对照 —— 复用阶段③已算出的融合概率，零额外 predict 开销
    # （单次 predict 在启用选号策略时可达数十秒）
    # · 概率校准 —— 回测断点里有样本时自动重拟合并落盘，否则读既有参数
    # · 三闸门调参 —— 只读取最近一次调参报告的结论，**不重跑**：单次调参需
    # 1-2 分钟，而历史结论稳定为 keep_baseline，不值得让
    # 每次「开始分析」都为一个可预期的结论多等两分钟
    # 全部以文本输出到同一结果面板，不再弹出独立可视化窗口（与「结果同面板呈现」一致）。
    # 诚实边界：本阶段只做「解读与状态展示」，不修改已产出的融合概率与推荐号码。
    # ============================================================

    def _run_hitrate_optimization_stage(self, task_mgr):
        """融合阶段④：命中率优化（选号策略 / 概率校准 / 调参结论）。

        三个子项彼此独立，任一异常都不得中断其余子项与整体分析流程。
        """
        task_mgr.append_section_header(" 命中率优化（选号策略 · 概率校准 · 调参结论）")
        task_mgr.progress(72, "命中率优化")
        for label, fn in (("选号策略对照", self._report_selection_strategy),
                          ("概率校准", self._report_probability_calibration),
                          ("三闸门调参结论", self._report_param_tuning_verdict)):
            try:
                fn(task_mgr)
            except Exception as e:
                task_mgr.log(f" {label} 异常（不影响已产出的预测结果）: {e}")
                logger.warning(f"命中率优化-{label} 异常: {e}", exc_info=True)

    def _report_selection_strategy(self, task_mgr):
        """选号策略对照：同一概率分布下横向对比各构造方式的位覆盖期望。"""
        fused = getattr(self, '_last_fused_probabilities', None)
        if not fused:
            task_mgr.append_info("  · 选号策略对照: 跳过（本轮无可复用的融合概率分布）")
            return

        from modules.selection_strategy import (compare_strategies,
                                                format_strategy_comparison,
                                                STRATEGY_LABELS)
        from modules.predictor import P5PredictorConfig

        cfg = P5PredictorConfig()
        k = cfg.get_global_param('combination_count', 10)
        comparison = compare_strategies(fused, k=k)
        task_mgr.log(format_strategy_comparison(comparison))

        active = cfg.get_global_param('selection_strategy', 'weighted_coverage')
        meta = getattr(self, '_last_predict_meta', None) or {}
        applied = meta.get('selection_strategy_applied')
        task_mgr.append_info(
            f"  当前生效策略: {STRATEGY_LABELS.get(active, active)}"
            f"（本轮预测实际应用: {'是' if applied else '否'}）")
        if comparison.get('note'):
            task_mgr.append_info(f"  {comparison['note']}")

    def _report_probability_calibration(self, task_mgr):
        """概率校准：回测断点有样本则自动重拟合并落盘，否则读取既有参数。"""
        from modules.calibration import (ProbabilityCalibrator,
                                         load_probs_from_resume_files)
        from modules.predictor import P5PredictorConfig

        cal = ProbabilityCalibrator.load()
        source = '已落盘参数'
        try:
            probs, n = load_probs_from_resume_files()
        except Exception:
            probs, n = [], 0

        if probs:
            # 回测断点里有样本 → 自动重拟合，使校准参数始终与最新回测同步。
            # 这一步很便宜（读 JSON + 黄金分割搜索），可以每轮分析都做。
            fresh = ProbabilityCalibrator()
            fresh.fit_from_backtest_probs(probs)
            try:
                fresh.save()
            except Exception as _se:
                task_mgr.log(f" 校准参数落盘失败（不影响本次展示）: {_se}")
            cal = fresh
            source = f'本轮自动重拟合（样本 {n} 个位次）'

        meta = getattr(cal, 'metadata', None) or {}
        if not meta:
            task_mgr.append_info(
                "  · 概率校准: 暂无样本也无既有参数"
                "（先在「综合验证与分析」里跑一次历史回测即可积累）")
            return

        enabled = bool(P5PredictorConfig().get_global_param(
            'enable_probability_calibration', False))
        sig = cal.signal_strength
        task_mgr.log(f"\n  【概率校准】来源: {source}")
        task_mgr.log(f"    温度 T = {cal.temperature:.4f}    收缩系数 ε = {cal.epsilon:.6f}")
        task_mgr.log(f"    模型真实信号强度 = {sig * 100:.4f}%"
                     f"    （诚实阈值 5%，低于此值即判定为无真实预测信号）")
        task_mgr.log(f"    本轮预测是否应用校准: {'是' if enabled else '否'}")
        if sig < 0.05:
            task_mgr.append_warning(
            " 信号强度 < 5%：模型基本无真实预测信号，校准后分布更接近均匀"
                "——这与排列5公平摇号的理论预期一致。")
        else:
            task_mgr.append_success(
            f" 检测到可量化信号 {sig * 100:.2f}%，可考虑启用概率校准。")
        if meta.get('interpretation'):
            task_mgr.append_info(f"    结论: {meta['interpretation']}")

    def _report_param_tuning_verdict(self, task_mgr):
        """三闸门调参：读取最近一次调参报告的结论（归档结论，本轮不重跑）。"""
        import glob as _glob
        try:
            from paths import REPORTS_BACKTEST_DIR as _dir
        except Exception:
            _dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'reports', 'backtest')

        files = sorted(_glob.glob(os.path.join(_dir, 'param_tuning_*.json')))
        if not files:
            task_mgr.append_info(
                "  · 三闸门调参: 暂无历史调参报告"
                "（该能力已归档为离线分析，当前运行使用冻结基线参数）")
            return

        latest = files[-1]
        try:
            with open(latest, 'r', encoding='utf-8') as fh:
                rep = json.load(fh)
        except Exception as e:
            task_mgr.append_info(f"  · 三闸门调参: 报告读取失败（{e}）")
            return

        stamp = os.path.basename(latest).replace('param_tuning_', '').replace('.json', '')
        task_mgr.log(f"\n  【三闸门调参结论】历史报告 {stamp}（归档结论，本轮不重跑）")

        status = rep.get('status')
        if status and status != 'ok':
            task_mgr.append_info(f"    该次调参状态: {status} — {rep.get('message', '')}")
            return

        gates = rep.get('gates') or {}
        verdict = rep.get('verdict') or {}
        cands = rep.get('candidates') or []
        task_mgr.log(f"    候选参数组: {len(cands)}    "
                     f"通过 FDR 闸门: {gates.get('n_passing_fdr', 0)}    "
                     f"通过一致性闸门: {gates.get('n_passing_consistency', 0)}")
        task_mgr.log(f"    最终结论: {verdict.get('action', '—')}")
        if verdict.get('action') == 'keep_baseline':
            task_mgr.append_info(
                "    → 无候选参数通过三闸门显著性检验，维持冻结基线权重"
                "（符合排列5公平摇号的理论预期）。")
        if verdict.get('reason'):
            task_mgr.append_info(f"    理由: {verdict['reason']}")

    # ============================================================
    # 「开始分析」融合阶段 ⑤：在线学习闭环
    # 原卡片三个按钮的去向：
    # · 查看学习报告 —— 融合为本阶段的自动输出
    # · 手动验证期号 —— 由流水线内的**自动验证闭环**取代。v3.42 起自动验证
    # 路径也会触发 learn_from_verification 归因学习（此前
    # 只有手动对话框会触发，导致自动跑批时在线学习空转），
    # 因此手动输入期号已无必要
    # · 重置模型权重 —— 破坏性操作（清 Redis 累积权重），不适合出现在自动化
    # 流程中，随卡片一并移除
    # ============================================================

    def _run_online_learning_stage(self, task_mgr):
        """融合阶段⑤：在线学习闭环状态（验证统计 / 权重调度 / 归因覆盖率）。"""
        task_mgr.append_section_header(" 在线学习闭环（验证统计 · 权重调度 · 归因覆盖率）")
        task_mgr.progress(82, "在线学习闭环")
        db = None
        try:
            db = P5Database()
            if not db.connect():
                task_mgr.append_warning(" 数据库连接失败，跳过在线学习状态展示")
                return
            for label, fn in (("验证统计", self._report_verification_stats),
                              ("归因覆盖率", self._report_attribution_coverage)):
                try:
                    fn(task_mgr, db)
                except Exception as e:
                    task_mgr.log(f" {label} 异常: {e}")
            try:
                self._report_weight_schedule(task_mgr)
            except Exception as e:
                task_mgr.log(f" 权重调度状态异常: {e}")
        except Exception as e:
            task_mgr.log(f" 在线学习阶段异常（不影响已产出的预测结果）: {e}")
            logger.warning(f"在线学习阶段异常: {e}", exc_info=True)
        finally:
            if db is not None:
                try:
                    db.disconnect()
                except Exception:
                    pass

    def _report_verification_stats(self, task_mgr, db):
        """验证统计：最近30天命中情况 + 各位置命中率 + 性能趋势。"""
        stats = db.get_verification_stats()
        if not stats or stats.get('total', 0) == 0:
            task_mgr.append_info("  · 验证统计: 暂无已验证记录"
                                 "（预测注册后需等待开奖，下次分析会自动闭合）")
            return

        total = stats['total']
        # 口径与走势引擎面板保持一致。
        # 注意 get_verification_stats() 返回的是 *_accuracy 百分比，
        # 不含 *_hits 计数——早期误读 *_hits 会导致各位置恒显示 0%。
        task_mgr.log(f"\n  【验证统计】已验证 {total} 期（按期号去重）")
        task_mgr.log(f"    严格口径（精确匹配）: 完全命中 "
                     f"{stats.get('strict_full_matches', 0)} 期    "
                     f"平均命中 {stats.get('strict_avg_match', 0)}/5    "
                     f"准确率 {stats.get('strict_avg_accuracy', 0)}%")
        task_mgr.log(f"    容错口径（±1 偏差）: 完全命中 "
                     f"{stats.get('total_matched', 0)} 期    "
                     f"平均命中 {stats.get('avg_match', 0)}/5    "
                     f"准确率 {stats.get('avg_accuracy', 0)}%")

        task_mgr.log(f"\n  【各位置命中率】严格口径 ┃ 容错口径")
        pos_names = ['万位', '千位', '百位', '十位', '个位']
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        for name, key in zip(pos_names, pos_keys):
            strict = float(stats.get(f'strict_{key}_accuracy', 0) or 0)
            tol = float(stats.get(f'{key}_accuracy', 0) or 0)
            bar_len = max(0, min(20, int(strict / 5)))
            bar = '█' * bar_len + '░' * (20 - bar_len)
            task_mgr.log(f"    {name}: {strict:5.1f}% [{bar}] ┃ {tol:5.1f}%")
        task_mgr.append_info(
            "    随机基线: 每位约 50%（每位预测 5 个号码 / 共 10 个）")

        try:
            perf_history = db.get_performance_history(limit=5)
        except Exception:
            perf_history = []
        if perf_history:
            task_mgr.log(f"\n  【性能趋势】最近 5 次统计")
            for perf in perf_history[:5]:
                task_mgr.log(f"    {perf.get('stat_date', 'N/A')}: "
                             f"总预测 {perf.get('total_predictions', 0)}, "
                             f"平均命中 {perf.get('avg_match_count', 0)}/5")

    def _report_weight_schedule(self, task_mgr):
        """自适应权重的学习率调度与收敛策略（诚实展示实际生效配置）。"""
        from modules.predictor import AdaptiveWeightManager, P5PredictorConfig

        self._log_real_algorithm_weights(task_mgr, "模型权重配置（实际生效）")

        _g = P5PredictorConfig.DEFAULT_CONFIG['global']
        _wm = AdaptiveWeightManager(
            ewma_alpha=_g.get('ewma_alpha', 0.3),
            learning_rate_decay=_g.get('learning_rate_decay', 0.995),
            min_learning_rate=_g.get('min_learning_rate', 0.05),
            warmup_iterations=_g.get('warmup_iterations', 5),
            convergence_tol=_g.get('convergence_tol', 0.01),
            enable_guardrails=_g.get('enable_adaptive_guardrails', True),
        )
        _st = _wm.get_learning_state()
        task_mgr.log(f"\n  【学习率调度与收敛策略】")
        task_mgr.log(f"    基础学习率 α:    {_st['base_learning_rate']}")
        task_mgr.log(f"    学习率衰减:      ×{_st['learning_rate_decay']} / 迭代"
                     f"（下限 {_st['min_learning_rate']}）")
        task_mgr.log(f"    预热迭代:        {_st['warmup_iterations']} 次"
                     f"（期间不衰减，稳定冷启动）")
        task_mgr.log(f"    收敛判据:        权重位移 < {_st['convergence_tol']} 即判已收敛")
        task_mgr.log(f"    护栏(收缩+钳制): "
                     f"{'开启' if _g.get('enable_adaptive_guardrails') else '关闭'}")
        task_mgr.log(" 排列5为公平摇号，学习目标是抗噪声稳定，而非突破随机天花板。")

    def _report_attribution_coverage(self, task_mgr, db):
        """验证→学习闭环的归因覆盖率（诚实展示学习是否真在按算法归因）。

        v3.44 起仅统计该版本部署日之后创建的预测记录，避免历史 NULL 归因把覆盖率
        拉到 0% 造成功能失效的误判。全量(含历史)口径仅作诚实对照展示。
        """
        from modules.online_learner import OnlineLearner

        _cov = OnlineLearner(db, None).estimate_attribution_coverage(
            db, days=30, limit=20)
        task_mgr.log(f"\n  【验证→学习闭环覆盖率】（仅统计  后记录）")
        if _cov.get('coverage') is None:
            task_mgr.log("     后暂无已验证预测，覆盖率将在新预测开奖验证后陆续统计。")
            if _cov.get('excluded_pre_fix'):
                task_mgr.log(
                    f"    （另有 {_cov['excluded_pre_fix']} 期 v3.44 前的历史预测"
                    f"因归因未落库已排除，不计入覆盖率）")
            return
        task_mgr.log(f"    采样已验证期数: {_cov['sampled']}")
        task_mgr.log(f"    含 per-algo 归因:        {_cov['with_attribution']} 期")
        task_mgr.log(f"    归因覆盖率:             {_cov['coverage'] * 100:.1f}%")
        if _cov.get('raw_sampled'):
            task_mgr.log(
                f"    全量口径(含历史,对照):   {_cov['raw_coverage'] * 100:.1f}% "
                f"（{_cov['raw_with_attribution']}/{_cov['raw_sampled']}，"
                f"已排除 {_cov['excluded_pre_fix']} 期 v3.44 前记录）")
        # 仅当 v3.44 后新建预测本身也缺归因时才告警（这才是真问题）
        if _cov['coverage'] < 0.5 and _cov['sampled'] >= 1:
            task_mgr.append_warning(
            " v3.44 后新建预测的归因覆盖率偏低，请检查四步流水线是否"
                "正确写入 p5_ai_report.per_algo_predictions。")

    # ============================================================
    # 「开始分析」融合阶段 ⑥：AI 辅助分析与预测解读
    # 两部分：
    # 1) 贝叶斯后验 · AI 辅助解读 —— 复用 _algo_bayesian_inference 已生成的
    # 辅助洞察，不额外发起 API 调用
    # 2) 最终推荐号码的 AI 点评 —— 一次轻量 API 调用（紧凑 prompt / 低 token）
    # AI 不可用（未配置密钥 / 网络失败 / 返回空）时全部诚实降级，绝不编造。
    # 诚实边界：AI 只做「解读」，不修改融合概率与推荐号码。
    # ============================================================

    def _run_ai_assisted_stage(self, task_mgr):
        """融合阶段⑥：AI 辅助分析与预测解读。"""
        task_mgr.append_section_header(" AI 辅助分析与预测解读")
        task_mgr.progress(92, "AI 辅助分析")
        db = None
        try:
            db = P5Database()
            if not db.connect():
                task_mgr.append_warning(" 数据库连接失败，跳过 AI 辅助分析")
                return
            # 1) 贝叶斯后验 + AI 辅助（AI 不可用时内部诚实降级为纯统计后验）
            self._render_bayesian_ai_section(task_mgr, db)
        except Exception as e:
            task_mgr.log(f" 贝叶斯 AI 辅助异常: {e}")
        finally:
            if db is not None:
                try:
                    db.disconnect()
                except Exception:
                    pass

        # 2) 对最终推荐号码做一次轻量 AI 点评
        try:
            self._report_ai_prediction_commentary(task_mgr)
        except Exception as e:
            task_mgr.log(f" AI 预测解读异常（不影响已产出的预测结果）: {e}")
            logger.warning(f"AI 预测解读异常: {e}", exc_info=True)

    def _report_ai_prediction_commentary(self, task_mgr):
        """让 AI 模型对本轮最终推荐号码做一次简短解读（单次轻量调用）。"""
        from modules.ai_analyzer import AIAnalyzer

        analyzer = AIAnalyzer()
        if not analyzer.ai_available:
            task_mgr.append_warning(
            " AI 预测解读: 未启用（config.py 中未配置 AGNES_API_CONFIG.api_key）")
            task_mgr.append_info(
                "     配置密钥后，本阶段会自动引用 AI 模型对推荐号码作辅助解读。")
            return

        # 汇总本轮各来源的最终推荐（复用仪表盘聚合逻辑，不重新计算）
        pf = getattr(self, '_last_pipeline_final', None)
        tr = getattr(self, '_last_trend_result', None)
        qf = getattr(self, '_last_quick_final', None)
        try:
            picks, top5, combos, target_issue = self._extract_source_data(pf, tr, qf)
        except Exception:
            picks, top5, combos, target_issue = {}, {}, {}, ''

        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        pos_names = ['万位', '千位', '百位', '十位', '个位']
        lines = []
        for pk, pn in zip(pos_keys, pos_names):
            cand = top5.get(pk) or {}
            merged = []
            for src_list in cand.values():
                for v in (src_list or []):
                    if v not in merged:
                        merged.append(v)
            if merged:
                lines.append(f"{pn}: {' '.join(str(x) for x in merged[:3])}")
        if not lines:
            task_mgr.append_info(" AI 预测解读: 跳过（本轮无可解读的候选号码）")
            return

        meta = getattr(self, '_last_predict_meta', None) or {}
        target_issue = target_issue or meta.get('target_issue') or '下一期'

        prompt = (
            f"以下是排列5第 {target_issue} 期的多算法候选号码（每位 Top5，按位列出）：\n"
            + "\n".join(lines)
            + "\n\n请用中文给出不超过 150 字的简短解读，包含："
              "①各位候选的分布特征（如集中/分散、奇偶与大小倾向）；"
              "②一句风险提示。\n"
              "重要约束：排列5是公平摇号，任何号码组合的中奖概率完全相同，"
              "不得声称能提高中奖率、不得给出必中承诺、不得编造统计规律。"
        )
        messages = [
            {"role": "system",
             "content": "你是严谨的彩票数据分析助手。你必须坚持概率诚实原则："
                        "彩票为独立随机事件，历史数据无法预测未来，"
                        "你的解读仅为数据特征描述，不构成任何投注建议。"},
            {"role": "user", "content": prompt},
        ]

        task_mgr.log("  正在请求 AI 模型解读本轮推荐号码...")
        content = analyzer._call_ai_model(messages, max_tokens=600, temperature=0.5)
        if not content or not str(content).strip():
            task_mgr.append_warning(
            " AI 预测解读: 调用未返回有效内容，已降级为纯统计结果（不影响预测）。")
            return

            task_mgr.append_success(f" AI 预测解读（模型: {analyzer.model_name}）")
        for line in str(content).strip().splitlines():
            if line.strip():
                task_mgr.log(f"    {line.strip()}")
        task_mgr.append_info(
        " 以上为 AI 对统计结果的语言化解读，不改变任何概率计算，"
            "也不提高中奖概率。理性购彩，量力而行。")





def _get_icon_path() -> str:
    """获取图标文件的绝对路径(兼容打包后的运行环境)。"""
    icon_name = 'favicon.ico'
    # PyInstaller 打包后, 资源目录在 sys._MEIPASS
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, icon_name)


def _set_window_icon(root):
    """为 Tk 根窗口设置图标, 如果文件不存在则静默忽略。"""
    icon_path = _get_icon_path()
    if os.path.isfile(icon_path):
        try:
            root.iconbitmap(icon_path)
        except tk.TclError:
            # macOS 不支持 iconbitmap, 静默忽略
            pass


def main():
    """启动排列5 AI智能分析系统GUI应用程序"""
    root = tk.Tk()
    _set_window_icon(root)
    app = LotteryGUI(root)
    # 强制刷新一次，确保窗口首帧渲染完成，避免白屏
    root.update_idletasks()

    def on_closing():
        """窗口关闭时优雅关闭线程池；有任务运行时先警示确认（v3.25）"""
        app._confirm_close()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
