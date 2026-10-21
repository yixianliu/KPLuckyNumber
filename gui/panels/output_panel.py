# -*- coding: utf-8 -*-
"""gui/panels/output_panel.py —— 输出面板构建期 Mixin（GUI-11 机械搬迁）。

v5.2 升级方案 PUP-20261019-013100 §337：8125 行单文件按
『构建期 / 渲染期 / 轮询 / 业务协调』拆分，本模块承载构建期
（BUILD 分区，30 法）方法，由 main.LotteryGUI 以 Mixin 方式继承。

机械搬迁约定（不改逻辑）：
  * 方法源码自 main.py 逐字搬运，仅含裸 COLORS 引用的方法在
    docstring 后追加方法头别名 `COLORS = _main_mod.COLORS`，
    将 LOAD_GLOBAL 转 LOAD_FAST 以在方法单次执行内锁定当期
    主题色板（PEP 562 模块 __getattr__ 不接 LOAD_GLOBAL，已由
    字节码实测证实，此为唯一可行解）；
  * 双模块陷阱破解：宿主以 python main.py 启动时模块名为
    __main__，直接 import main 会重复加载；此处先将 __main__
    注册回 sys.modules['main'] 别名再 import，保证同一模块
    实例（S1 最小复现实测通过）；
  * main.py 侧保留全部主题 re-export 与 rebind 语义
    （_apply_theme 的 global COLORS 切换后，_main_mod.COLORS
    每次方法调用取当期值，与原 LOAD_GLOBAL 行为一致）。

【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来
开奖结果，本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成
任何购彩建议。请理性购彩，量力而行。
"""

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:  # pragma: no cover - 无显示环境兜底
    tk = None
    ttk = None
    messagebox = None
    filedialog = None

# 构建期依赖的顶层符号（来源均为 main.py 原 import 域，同一 sys.modules 实例）
from modules import ui_styles
from modules.ui_theme_spec import (
    SUCCESS_STATIC,
    WHITE,
    button_style_map,
)
from version import get_current_version

import sys as _sys

# 双模块陷阱破解：python main.py 时宿主模块名为 __main__，
# 先注册 sys.modules['main'] 别名再 import，避免磁盘重复加载
if "main" not in _sys.modules:
    _host = _sys.modules.get("__main__")
    _host_file = str(getattr(_host, "__file__", "") or "").replace(
        "\\", "/")
    if _host_file.endswith("/main.py"):
        _sys.modules["main"] = _host
# S1 实测：经 setdefault 注册后同一模块实例（含 rebind 后新 COLORS）
import main as _main_mod  # noqa: E402  双模块陷阱破解后的正式引用


class OutputPanelMixin:
    """输出面板构建期方法（GUI-11 BUILD 分区 30 法，main.py 机械搬迁）。

    仅经 LotteryGUI(OutputPanelMixin, ...) 组合使用；
    自身不构造 tk 视图、不持有状态（状态仍在 main.LotteryGUI）。
    """
    def _build_header(self, parent):
        """构建顶部标题栏（Logo + 系统名称 + 副标题 + 版本号 + 实时时钟）

        修复说明(v3.8):
        - 原 header 高度固定 50px 且 pack_propagate(False)，在 Windows 高 DPI
          缩放下标题(13pt)+副标题(8pt)实际像素增高，导致副标题底部被裁切。
        - 现将高度提升到 60px 并收紧内边距，保证副标题完整可见。
        - 右侧新增版本号显示（get_current_version()），与时钟纵向排列，风格统一。
        """
        COLORS = _main_mod.COLORS
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left = tk.Frame(header, bg=COLORS['bg_secondary'])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=12)

        # Logo: Canvas 圆角渐变色块 + 文字，优化视觉
        icon = tk.Canvas(left, width=40, height=40, bg=COLORS['bg_secondary'],
                         highlightthickness=0)
        icon.pack(side=tk.LEFT, pady=10)
        # 圆角渐变：使用 oval 模拟圆角矩形
        icon.create_oval(4, 4, 36, 36, fill=COLORS['accent_p5'], outline='', width=0)
        # 内层 AI 紫叠加
        icon.create_oval(8, 8, 32, 32, fill=COLORS['accent_ai'], outline='', width=0)
        icon.create_text(20, 20, text='5', font=('Consolas', 16, 'bold'), fill=WHITE)

        title_box = tk.Frame(left, bg=COLORS['bg_secondary'])
        title_box.pack(side=tk.LEFT, padx=(10, 0), pady=0)

        tk.Label(title_box, text="排列5 AI智能分析系统",
                 font=('微软雅黑', 14, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(anchor=tk.W)

        tk.Label(title_box, text="七算法融合 · 概率建模 · 诚实边界",
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(anchor=tk.W)

        # 右侧：版本号 + 实时时钟（纵向排列）
        right = tk.Frame(header, bg=COLORS['bg_secondary'])
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=12)

        # 风险提示徽章：圆角胶囊样式
        risk_badge = tk.Canvas(right, width=60, height=22, bg=COLORS['bg_secondary'], highlightthickness=0)
        risk_badge.create_rectangle(1, 1, 59, 21, fill=COLORS['bg_secondary'], outline=COLORS['accent_warning'], width=1)
        risk_badge.create_text(30, 11, text='⚠ 随机事件', font=('微软雅黑', 8, 'bold'), fill=COLORS['accent_warning'])
        risk_badge.pack(anchor=tk.E, pady=(6, 2))
        risk_badge.bind('<Button-1>', lambda e: messagebox.showinfo('风险提示', '排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。请理性购彩，量力而行。'))

        self.version_label = tk.Label(right, text=f"v{get_current_version()}",
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['accent_p5'])
        self.version_label.pack(anchor=tk.E, pady=(2, 0))

        # 主题切换按钮：显示 emoji
        self._theme_btn = tk.Button(right, text="☀️",
                                    font=('微软雅黑', 12),
                                    bg=COLORS['bg_secondary'],
                                    fg=COLORS['text_secondary'],
                                    relief='flat',
                                    cursor='hand2',
                                    padx=4, pady=0,
                                    command=self._toggle_theme)
        self._theme_btn.pack(anchor=tk.E, pady=(2, 0))

        self.time_label = tk.Label(right, text="",
                                   font=('Consolas', 9),
                                   bg=COLORS['bg_secondary'],
                                   fg=COLORS['text_secondary'])
        self.time_label.pack(anchor=tk.E, pady=(2, 0))
        self._update_time()

    def _build_content(self, parent):
        """构建中部内容区：左侧控制面板（可滚动，280px）+ 右侧输出面板（自适应）"""
        COLORS = _main_mod.COLORS
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
                                      command=left_canvas.yview, width=10,
                                      bg=COLORS['bg_card'],
                                      troughcolor=COLORS['bg_secondary'],
                                      activebackground=COLORS['accent_p5'])
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
        COLORS = _main_mod.COLORS
        # 数据爬取卡片
        crawl_card = self._create_card(
            parent, "数据爬取", COLORS['accent_warning_light'],
            accent_token='accent_warning_light')
        crawl_card.pack(fill=tk.X, pady=(0, 8))

        self._add_big_button(
            crawl_card, "增量爬取数据", COLORS['accent_warning_light'],
            lambda: self._on_button_click("增量爬取", self._execute_crawl_incremental),
            color_token='accent_warning_light')
        self._add_action_button(
            crawl_card, "全量爬取数据", COLORS['accent_warning'],
            lambda: self._on_button_click("全量爬取", self._execute_crawl_full),
            color_token='accent_warning')

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
        p5_card = self._create_card(
            parent, "智能分析中心", COLORS['accent_p5'],
            accent_token='accent_p5')
        p5_card.pack(fill=tk.X, pady=(0, 8))

        # P2 优化: 快速模式与标准模式双按钮
        self._add_big_button(
            p5_card, " 开始分析", COLORS['accent_p5'],
            lambda: self._on_button_click("智能分析", self._execute_unified_analysis),
            color_token='accent_p5')

        tk.Label(p5_card,
                 text="开始分析: 完整流程（含验证/学习/AI解读）",
                 font=('微软雅黑', 8),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted'],
                 wraplength=240, justify=tk.CENTER
                 ).pack(pady=(0, 8))

        # 进度展示合并进智能分析中心卡片底部
        progress_row = tk.Frame(p5_card, bg=COLORS['bg_secondary'])
        progress_row.pack(fill=tk.X, padx=10, pady=(8, 0))
        self.progress = ttk.Progressbar(progress_row, mode='determinate', maximum=100)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.progress_label = tk.Label(progress_row, text="0%",
                                       font=('Consolas', 11, 'bold'),
                                       bg=COLORS['bg_secondary'],
                                       fg=COLORS['accent_p5'], width=4)
        self.progress_label.pack(side=tk.LEFT)
        # 显式取消按钮（v5.2 GUI-10 方案 §331：长任务可取消入口，与 Escape
        # 快捷键共用 _cancel_current_task 链路）。不注册进 self._buttons：
        # 其启用态与操作按钮相反（任务运行中才可点），由 _set_buttons_state
        # 反转管理；初始为灰化禁用态，空闲时不可点击。
        self._cancel_btn = tk.Button(progress_row, text="取消",
                                     command=self._cancel_current_task,
                                     font=('微软雅黑', 9),
                                     bg=COLORS['bg_input'],
                                     fg=COLORS['text_disabled'],
                                     activebackground=COLORS['accent_danger'],
                                     activeforeground=WHITE,
                                     relief='flat', bd=0,
                                     padx=8, pady=2,
                                     cursor='hand2',
                                     highlightthickness=0,
                                     takefocus=True,
                                     state=tk.DISABLED)
        self._cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))
        # 悬停效果复用工具栏按钮工厂的通用处理器（danger 三态）
        self._cancel_btn.bind(
            '<Enter>',
            lambda e: self._on_button_enter(e, self._cancel_btn, 'danger'))
        self._cancel_btn.bind(
            '<Leave>',
            lambda e: self._on_button_leave(e, self._cancel_btn, 'danger'))

        # 卡片状态徽章动态更新示例
        self._update_card_badge(p5_card, "就绪", "badge_trial")

        self.task_status_label = tk.Label(p5_card, text="就绪",
                                          font=('微软雅黑', 8),
                                          bg=COLORS['bg_secondary'],
                                          fg=COLORS['text_muted'])
        self.task_status_label.pack(anchor=tk.W, padx=10, pady=(4, 8))

        # 智能分析与验证卡片 — 单个综合按钮，v3.11
        analysis_card = self._create_card(
            parent, "智能分析与验证", COLORS['accent_p5'],
            accent_token='accent_p5')
        analysis_card.pack(fill=tk.X, pady=(0, 8))

        # 综合验证与分析按钮（一键执行「分析工具」全部子功能：验证→命中率→性能→回测→特征→报告）
        tk.Button(analysis_card, text=" 综合验证与分析",
                  font=('微软雅黑', 10, 'bold'),
                  bg=COLORS['accent_p5_light'], fg=WHITE,
                  activebackground=COLORS['accent_p5'], activeforeground=WHITE,
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
                 ).pack(pady=(0, 6))

        # 快捷入口：历史命中率
        quick_row = tk.Frame(analysis_card, bg=COLORS['bg_secondary'])
        quick_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        for txt, cmd in [
            ("查看命中率", lambda: self._on_button_click("查看命中率", self._show_data_overview)),
        ]:
            btn = tk.Button(quick_row, text=txt, font=('微软雅黑', 8),
                            bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                            relief='flat', cursor='hand2', command=cmd)
            btn.pack(side=tk.LEFT, padx=(0, 6))

        # 注: 原「在线学习引擎」卡片(学习报告/重置权重/手动验证)与
        # 「命中率优化引擎」卡片(选号策略对比/概率校准/三闸门调参) 已于 v3.42
        # 整体融合进「智能分析中心 → 开始分析」，作为其内建阶段自动执行，
        # 故此处不再单独陈列。其中「重置模型权重」为破坏性操作，不适合出现在
        # 自动化流程中，已随卡片一并移除。
        # 注: 原「分析工具」卡片(预测验证/命中率报告/性能报告/历史回测/特征分析)
        # 已于 v3.17 整合进上方「智能分析与验证」卡片的「综合验证与分析」一键按钮,
        # 复用各子功能既有实现, 数据口径与单独点击完全一致, 故此处不再单独陈列。

    def _create_card(self, parent, title, accent_color, accent_token=None):
        """
        创建一个带标题和顶部彩色装饰条的功能卡片（v3.36 优化版）

        Args:
            parent: 父容器
            title: 卡片标题
            accent_color: 装饰条和圆点颜色（创建时的主题值快照）
            accent_token: 可选，accent_color 对应的主题语义槽键；提供后
                主题切换时 Canvas 圆点按该 token 重放（v5.2 GUI-06）

        Returns:
            tk.Frame: 卡片框架
        """
        COLORS = _main_mod.COLORS
        card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                        highlightbackground=COLORS['border'],
                        highlightthickness=1,
                        relief='solid')

        # 顶部彩色装饰条（3px高度，增强视觉层次）
        top_bar = tk.Frame(card, bg=accent_color, height=3)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        # 标题行（彩色圆点 + 标题文字 + 状态徽章占位）
        title_frame = tk.Frame(card, bg=COLORS['bg_secondary'])
        title_frame.pack(fill=tk.X, padx=10, pady=(8, 6))

        dot = tk.Canvas(title_frame, width=8, height=8,
                        bg=COLORS['bg_secondary'], highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 6))
        _dot_item = dot.create_oval(1, 1, 7, 7, fill=accent_color, outline='')
        # Canvas 绘图项不经控件树反查刷新：登记 token，切换主题后重放（GUI-06）
        if accent_token:
            self._register_theme_canvas_item(dot, _dot_item, accent_token)

        tk.Label(title_frame, text=title,
                 font=('微软雅黑', 11, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(side=tk.LEFT)

        # 状态徽章占位（后期动态插入）
        badge_placeholder = tk.Frame(title_frame, bg=COLORS['bg_secondary'])
        badge_placeholder.pack(side=tk.RIGHT)
        
        # 保存徽章占位引用，便于后续动态更新
        card._badge_placeholder = badge_placeholder
        card._card_title = title

        # 卡片悬停微动效：仅改变边框高亮色，避免子控件背景覆盖问题
        def _on_card_enter(e):
            card.config(highlightbackground=COLORS['accent_p5'])
        def _on_card_leave(e):
            card.config(highlightbackground=COLORS['border'])
        card.bind('<Enter>', _on_card_enter)
        card.bind('<Leave>', _on_card_leave)

        return card

    def _update_card_badge(self, card, text, badge_type='badge_trial'):
        """动态更新卡片标题状态徽章"""
        COLORS = _main_mod.COLORS
        if not hasattr(card, '_badge_placeholder'):
            return
        for child in card._badge_placeholder.winfo_children():
            child.destroy()
        
        # 六个 badge_* 槽键在深浅两主题中均已定义，直接查表（v5.2 GUI-06：
        # 原 .get 默认值字面量计缺口，且 COLORS['accent_orange'] 未定义，
        # 默认参数会被提前求值，rolledback 分支一旦调用即 KeyError）
        if badge_type == 'badge_active':
            bg = COLORS['badge_active_bg']
            fg = COLORS['badge_active_fg']
        elif badge_type == 'badge_rolledback':
            bg = COLORS['badge_rolledback_bg']
            fg = COLORS['badge_rolledback_fg']
        else:  # badge_trial
            bg = COLORS['badge_trial_bg']
            fg = COLORS['badge_trial_fg']
        
        badge = tk.Label(card._badge_placeholder, text=text,
                        font=('微软雅黑', 8),
                        bg=bg, fg=fg,
                        padx=6, pady=2)
        badge.pack(side=tk.RIGHT)

    def _add_big_button(self, parent, text, color, command, color_token=None):
        """
        添加主要操作按钮（大号、彩色背景、占满整行）（v3.36 优化版）

        Args:
            parent: 父容器
            text: 按钮文字
            color: 按钮背景色（创建时的主题值快照）
            command: 点击回调函数
            color_token: 可选，color 对应的主题语义槽键；显式提供时优先
                用于正常态注册（v5.2 GUI-06）

        Returns:
            tk.Button: 按钮实例
        """
        COLORS = _main_mod.COLORS
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
                        bd=0,
                        # v5.0 §2.1 可访问性：tk.Button 默认不可 Tab 聚焦，显式开启
                        takefocus=True)
        btn.pack(fill=tk.X)

        # 记录正常态配色（v4.0 §2.4：以主题 token 存储，主题切换后还原仍正确）
        # color 可能是静态色常量或主题值快照；fg 用 token 'text_primary'
        # 动态解析，避免缓存创建时的浅/深 hex 快照导致切换主题后泄漏旧色。
        _hover_c = self._lighten_color(color, 1.15)
        # 反查 token：color 多为 COLORS 当前值快照，存 token 才能在主题切换后跟随
        # （否则还原的仍是创建时的旧主题色 → 明暗不一致）；color_token 显式提供时
        # 优先（v5.2 GUI-06：「同值多键」反查依赖兜底插入序，显式 token 根除脆弱性）
        _bg_token = color_token or ui_styles.token_for_color(color, COLORS)
        ui_styles.remember_button_normal(btn, _bg_token, 'text_primary')
        # hover（Enter/Leave）委托中央样式工厂，禁用守卫由工厂统一处理；
        # hover 色不传创建时快照 hex，由工厂按当前主题自适应派生（暗变亮/亮变暗），
        # 主题切换后 hover 亦无残留（v5.2 GUI-06）
        ui_styles.bind_hover(btn, lambda: COLORS)
        # 按下效果
        btn.bind('<Button-1>', lambda e, b=btn, c=self._lighten_color(color, 0.9): b.config(bg=c) if str(b['state']) != 'disabled' else None)
        btn.bind('<ButtonRelease-1>', lambda e, b=btn, c=_hover_c: b.config(bg=c) if str(b['state']) != 'disabled' else None)

        self._buttons.append(btn)
        return btn

    def _add_action_button(self, parent, text, color, command,
                           color_token=None):
        """
        添加次要操作按钮（小号、深色背景、hover时变色）

        Args:
            parent: 父容器
            text: 按钮文字
            color: hover时的背景色（创建时的主题值快照）
            command: 点击回调函数
            color_token: 可选，color 对应的主题语义槽键；显式提供时
                优先用于 hover 派生（v5.2 GUI-06）

        Returns:
            tk.Button: 按钮实例
        """
        COLORS = _main_mod.COLORS
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
                        padx=12, pady=5,
                        takefocus=True)  # v5.0 §2.1 可访问性：允许 Tab 聚焦
        btn.pack(fill=tk.X)

        # 正常态 token + hover（Enter/Leave）统一委托中央样式工厂（v5.0 §2.1）；
        # hover 色优先取显式 color_token，禁用守卫由工厂统一处理（v5.2 GUI-06）
        ui_styles.remember_button_normal(btn, 'bg_card', 'text_primary')
        # hover_bg 支持传主题 token（v5.2 GUI-06：显式 color_token 优先，回落
        # 传入 color 快照），bind_hover 内部在事件内惰性解析为当前主题值
        _hover_token = color_token or color
        ui_styles.bind_hover(btn, lambda: COLORS, hover_bg=_hover_token)
        # 按下效果
        btn.bind('<Button-1>', lambda e, b=btn, c=self._lighten_color(color, 0.85): b.config(bg=c) if str(b['state']) != 'disabled' else None)
        btn.bind('<ButtonRelease-1>', lambda e, b=btn, c=color: b.config(bg=c) if str(b['state']) != 'disabled' else None)

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
        COLORS = _main_mod.COLORS
        btn = tk.Button(parent, text=text,
                        font=('微软雅黑', 8),
                        bg=COLORS['bg_card'],
                        fg=COLORS['text_primary'],
                        activebackground=color,
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=10, pady=4,
                        takefocus=True)  # v5.0 §2.1 可访问性：允许 Tab 聚焦
        btn.pack(side=tk.LEFT, padx=(0, 5))

        # 正常态 token + hover 统一委托中央样式工厂（v5.0 §2.1），hover 色沿用 color
        ui_styles.remember_button_normal(btn, 'bg_card', 'text_primary')
        ui_styles.bind_hover(btn, lambda: COLORS, hover_bg=color)

        self._buttons.append(btn)
        return btn

    @staticmethod
    def _lighten_color(hex_color, factor):
        """
        将十六进制颜色按比例变亮

        Args:
            hex_color: 十六进制颜色字符串（6 位 #RRGGBB 形式）
            factor: 亮度因子（>1变亮，<1变暗）

        Returns:
            变亮后的十六进制颜色字符串
        """
        # 委托中央样式工厂（v5.0 §2.1），与 UI 其余部分共用同一实现
        return ui_styles.lighten_color(hex_color, factor)

    @staticmethod
    def _resolve_color_token(value):
        """将颜色值解析为当前主题色（v4.0 §2.4 主题适配）。

        设计：按钮正常态配色以「主题键 token / 静态 hex」存储，避免缓存
        创建时的 hex 快照导致主题切换后泄漏旧主题色。

        参数:
            value: 主题键名（如 'bg_card' / 'text_primary'）或静态色常量（如 WHITE）

        返回:
            str: value 是当前 COLORS 的键名 → 取该键的当前主题值；否则（静态 hex）原样返回。
        """
        COLORS = _main_mod.COLORS
        # 委托中央样式工厂（v5.0 §2.1），保持「token → 当前主题值」单一实现
        return ui_styles.resolve_color_token(value, COLORS)

    def _apply_button_normal_style(self, btn):
        """按缓存的 token/静态色还原按钮正常态外观（主题切换后调用亦正确）。

        参数:
            btn: 记录了 _normal_bg / _normal_fg 的按钮
        """
        COLORS = _main_mod.COLORS
        # 委托中央样式工厂（v5.0 §2.1）
        ui_styles.apply_button_normal_style(btn, COLORS)

    def _build_output_panel(self, parent):
        """构建右侧输出面板（ttk.Notebook）

        保留「预测结果」「命令行输出」「自我进化」三个标签页。
        「命令行输出」标签页实时展示命令行 stdout/stderr 与 logging 日志（需求 #3）；
        同时 ``logs/gui_run.log`` 仍保留完整文件日志，二者互补不冲突。
        """
        COLORS = _main_mod.COLORS
        panel = tk.Frame(parent, bg=COLORS['bg_primary'])
        panel.pack(fill=tk.BOTH, expand=True)

        nb = ttk.Notebook(panel)
        nb.pack(fill=tk.BOTH, expand=True)
        self.output_nb = nb

        result_tab = tk.Frame(nb, bg=COLORS['bg_primary'])
        nb.add(result_tab, text=" 预测结果")
        self._build_result_tab(result_tab)

        # §需求#3 新增「命令行输出」标签页：实时展示命令行 stdout/stderr 与 logging 日志
        cli_tab = tk.Frame(nb, bg=COLORS['bg_primary'])
        nb.add(cli_tab, text=" 命令行输出")
        self._build_cli_log_tab(cli_tab)

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
        COLORS = _main_mod.COLORS
        # ---- 1) 工具栏：分类筛选 / 清空 / 导出 / 复制 / 日志 / 可视化 ----
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

        # 复制按钮 Primary 强调，带星号
        self._result_copy_btn = self._create_toolbar_button(
            btn_frame, " ★ 复制预测号码", self._copy_prediction, 'primary')
        self._result_copy_btn.pack(side=tk.RIGHT, padx=(0, 6))

        # 清空结果 Danger 样式
        clear_result_btn = self._create_toolbar_button(
            btn_frame, " 清空结果", self._clear_result_board, 'danger')
        clear_result_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # 导出改为带下拉菜单的 Primary 样式（模拟下拉）
        export_menu_btn = self._create_toolbar_button(btn_frame, " 导出 ▾",
                                                      lambda: self._show_export_menu(), 'primary')
        export_menu_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # 可视化按钮
        self._result_visualize_btn = self._create_toolbar_button(
            btn_frame, " 📊 可视化", self._show_visualization_menu, 'primary')
        self._result_visualize_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # v5.2 GUI-07：登记「按钮 → (标准文案, 紧凑文案)」映射。紧凑档
        # （面板宽度 < _BREAKPOINT_COMPACT_MAX_W）下由 _set_toolbar_mode
        # 统一切换短文案，窄屏释放操作区宽度（方案 §311 / 验收 A17）；
        # 标准/宽绰档沿用完整文案。导出按钮原文案已够短，双档一致。
        self._bp_toolbar_texts = {
            self._result_copy_btn: (' ★ 复制预测号码', ' ★ 复制'),
            clear_result_btn: (' 清空结果', ' 清空'),
            export_menu_btn: (' 导出 ▾', ' 导出 ▾'),
            self._result_visualize_btn: (' 📊 可视化', ' 📊'),
        }

        # 分类筛选放到左侧
        self.result_category_var = tk.StringVar(value="全部")
        cat_combo = ttk.Combobox(btn_frame, textvariable=self.result_category_var,
                                 values=["全部", "预测结论", "分位信号", "算法依据"],
                                 state='readonly', width=10, font=('微软雅黑', 9))
        cat_combo.pack(side=tk.LEFT, padx=(0, 6))
        cat_combo.bind('<<ComboboxSelected>>',
                       lambda e: self._apply_result_category(self.result_category_var.get()))
        # 工具栏按钮独立于全局 _buttons，确保分析进行中可单独禁用复制按钮
        # （主按钮在 task_mgr 运行中被禁用，但结果区按钮需独立管理）

        # ---- 2) 概览卡片区（信息架构第一层）----
        overview_container = tk.Frame(parent, bg=COLORS['bg_primary'])
        overview_container.pack(fill=tk.X, padx=8, pady=(8, 4))
        self._ov_container = overview_container
        # 四个指标磁贴：最新预测期号+号码 / 历史命中率 / 已验证期数 / 历史数据量
        # 保存各磁贴「数值标签」句柄，供 _refresh_result_overview 增量回填（不重建控件）。
        self._ov_tiles = {}
        self._ov_tiles['issue'] = self._create_ov_tile(
            overview_container, '最新预测', '—', COLORS['accent_p5'], sub='期号 / 主推荐')
        self._ov_tiles['hit_rate'] = self._create_ov_tile(
            overview_container, '历史命中率', '—', COLORS['accent_ai_light'], sub='Top-1 真实口径')
        self._ov_tiles['verified'] = self._create_ov_tile(
            overview_container, '已验证期数', '—', COLORS['success_light'], sub='验证记录')
        self._ov_tiles['data_count'] = self._create_ov_tile(
            overview_container, '历史数据量', '—', COLORS['text_secondary'], sub='开奖样本')
        # 启动后延迟刷新概览指标（后台线程查库，避免阻塞首屏渲染）
        self.root.after(1500, self._refresh_result_overview)

        # ---- 3) 滚动容器（保证大数据量不卡顿）----
        # 以 Canvas + 滚动条承载结果仪表盘（镜像运行日志页布局），
        # 超长内容滚动查看而非撑爆布局，刷新时仅更新滚动区域，避免整体重绘卡顿。
        self.dash_scroll_sb = tk.Scrollbar(parent, orient='vertical',
                                          command=self._dash_yview, width=10,
                                          bg=COLORS['bg_card'], troughcolor=COLORS['bg_secondary'],
                                          activebackground=COLORS['accent_p5'])
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

        # GUI-01/02（v5.2 §2.2）：dash_container 的 Configure 不再直接同步宽度 +
        # 刷新滚动区域，改为「阈值守卫 + 防抖合并」，切断 Canvas 尺寸反馈循环：
        #   * _on_dash_configure：仅当容器宽度较上次变化 >= 2px 才回写内部视窗宽度
        #     （1px 级拖拽抖动被合并），且不再同步调用 scrollregion 刷新；
        #   * _dash_request_refresh：50ms 防抖入口，短时间内的多次刷新请求合并为
        #     一次统一执行（离开 Configure 事件回调栈，消除嵌套触发）。
        self._dash_last_synced_width = None
        self._dash_refresh_pending = False
        self.dash_container.bind('<Configure>', self._on_dash_configure)
        self.dash_inner.bind('<Configure>',
                             lambda e: self._dash_request_refresh())
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
        # 图标：圆角卡片样式，统一空态视觉
        icon_canvas = tk.Canvas(ph_inner, width=64, height=64, bg=COLORS['bg_card'], highlightthickness=0)
        icon_canvas.create_oval(8, 8, 56, 56, fill=COLORS['accent_p5'], outline='', width=0)
        icon_canvas.create_text(32, 32, text='5', font=('Consolas', 24, 'bold'), fill=WHITE)
        icon_canvas.pack(pady=(0, 12))
        tk.Label(ph_inner, text="暂无预测结果",
                 font=('微软雅黑', 15, 'bold'),
                 bg=COLORS['bg_card'], fg=COLORS['text_primary']).pack()
        tk.Label(ph_inner,
                 text="点击左侧「开始分析」运行完整流程\n预测结论将在此处以仪表盘形式呈现",
                 font=('微软雅黑', 10), justify=tk.CENTER,
                 bg=COLORS['bg_card'], fg=COLORS['text_secondary']).pack(pady=(6, 0))
        self._result_placeholder.pack(fill=tk.BOTH, expand=True)
        # Loading indicator
        self._loading_indicator = tk.Frame(self.dash_inner, bg=COLORS["bg_primary"])
        loading_label = tk.Label(self._loading_indicator, text="正在分析，请稍候...",
                                 font=("微软雅黑", 10),
                                 bg=COLORS["bg_primary"],
                                 fg=COLORS["text_primary"])
        loading_label.pack(expand=True)
        self._loading_indicator.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._loading_indicator.place_forget()

    def _show_loading_indicator(self):
        """显示加载指示器"""
        self._loading_indicator.lift()  # 确保在最上层
        self._loading_indicator.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _hide_loading_indicator(self):
        """隐藏加载指示器"""
        self._loading_indicator.place_forget()

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
        COLORS = _main_mod.COLORS
        f = tk.Frame(parent, bg=COLORS['bg_primary'])
        f.pack(fill=tk.BOTH, expand=True)

        # ── 顶部概览区（带渐变边框效果）────────────────────────────────
        ov = tk.Frame(f, bg=COLORS['bg_card'],
                      highlightbackground=COLORS['accent_ai_bright'],
                      highlightthickness=2)
        ov.pack(fill=tk.X, padx=12, pady=10)

        # 标题栏（带图标装饰）
        hdr = tk.Frame(ov, bg=COLORS['bg_card'])
        hdr.pack(fill=tk.X, padx=14, pady=(10, 6))

        # 装饰渐变条
        grad_bar = tk.Canvas(hdr, width=4, height=28, bg=COLORS['bg_card'],
                             highlightthickness=0)
        # Canvas 绘图项登记 token，主题切换后重放（v5.2 GUI-06）
        _grad_item = grad_bar.create_rectangle(
            0, 0, 4, 28, fill=COLORS['accent_ai'], outline='')
        self._register_theme_canvas_item(grad_bar, _grad_item, 'accent_ai')
        grad_bar.pack(side=tk.LEFT, padx=(0, 10))

        # 标题文字
        tk.Label(hdr, text="自我进化引擎", font=('微软雅黑', 13, 'bold'),
                 bg=COLORS['bg_card'],
                 fg=COLORS['accent_ai_bright']).pack(side=tk.LEFT)

        # 状态徽章（动态更新）
        self.evo_status_badge = tk.Label(hdr, text="● 就绪", font=('微软雅黑', 9),
                                         bg=COLORS['bg_input'],
                                         fg=COLORS['accent_p5_light'],
                                         padx=10, pady=3, relief='flat')
        self.evo_status_badge.pack(side=tk.RIGHT)

        # 阶段指示器（6 阶段点状展示）
        self.evo_phase_indicators = []
        phase_frame = tk.Frame(ov, bg=COLORS['bg_card'])
        phase_frame.pack(fill=tk.X, padx=14, pady=(0, 6))
        for i, phase_name in enumerate(['采集', '基线', '调优', '评估', '持久化', '完成']):
            dot = tk.Canvas(phase_frame, width=12, height=12, bg=COLORS['bg_card'],
                           highlightthickness=0)
            # 初始「未开始」灰色点：登记 text_muted token，主题切换后重放；
            # 运行中由 _update_phase_indicator 按阶段改色并重登记（v5.2 GUI-06）
            _dot_item = dot.create_oval(
                1, 1, 11, 11, fill=COLORS['text_muted'], outline='')
            self._register_theme_canvas_item(dot, _dot_item, 'text_muted')
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
                           highlightbackground=COLORS['border'], highlightthickness=1,
                           relief='solid')
            tile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=2)
            tk.Label(tile, text=label, font=('微软雅黑', 9),
                     bg=COLORS['bg_card'], fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=10, pady=(6, 2))
            val_label = tk.Label(tile, text=initial, font=('Consolas', 14, 'bold'),
                                bg=COLORS['bg_card'], fg=color)
            val_label.pack(anchor=tk.W, padx=10, pady=(0, 6))
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
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        self._create_toolbar_button(tb, "🔗 联动状态", self._show_evolution_link_state,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        self._create_toolbar_button(tb, "💡 改进建议", self._on_evo_proposals,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        self._create_toolbar_button(tb, "❤️ 健康诊断", self._on_system_health_diagnostic,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        self._create_toolbar_button(tb, "🛡️ 自动评估", self._on_auto_evaluator_scan,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        self._create_toolbar_button(tb, "📜 更新日志", self._on_update_history_log,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        self._create_toolbar_button(tb, "📈 性能监控", self._on_perf_monitor_view,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        self._create_toolbar_button(tb, "🔬 自我验证", self._on_result_self_verify,
                                    'secondary').pack(side=tk.LEFT, padx=(0, 6))
        btn_spacer = tk.Frame(tb, bg=COLORS['bg_primary'], width=20)
        btn_spacer.pack(side=tk.LEFT)
        self._create_toolbar_button(tb, "🗑️ 清空日志", self._clear_evolution_log,
                                    'danger').pack(side=tk.RIGHT)

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
                               bg=COLORS['bg_panel'], fg=COLORS['text_secondary'],
                               relief='flat', padx=12, pady=10, state=tk.NORMAL,
                               undo=True,
                               insertbackground=COLORS['accent_ai_bright'])
        self.evo_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 滚动条（匹配主题）
        esb = tk.Scrollbar(log_frame, command=self.evo_log.yview, width=12,
                           bg=COLORS['bg_card'], troughcolor=COLORS['bg_secondary'],
                           activebackground=COLORS['accent_ai'])
        esb.pack(side=tk.RIGHT, fill=tk.Y)
        self.evo_log.config(yscrollcommand=esb.set)

        # 日志标签颜色（终端风格）
        self.evo_log.tag_config(
            'section', foreground=COLORS['accent_ai_light'],
            font=('微软雅黑', 10, 'bold'))
        self.evo_log.tag_config(
            'success', foreground=COLORS['accent_p5_light'],
            font=('微软雅黑', 9))
        self.evo_log.tag_config(
            'warning', foreground=COLORS['accent_warning_light'],
            font=('微软雅黑', 9))
        self.evo_log.tag_config(
            'error', foreground=COLORS['accent_danger_light'],
            font=('微软雅黑', 9, 'bold'))
        self.evo_log.tag_config('info', foreground=COLORS['badge_trial_fg'],
                               font=('Consolas', 9))
        self.evo_log.tag_config('dim', foreground=COLORS['text_disabled'],
                               font=('Consolas', 9))
        # Text tag 色不经控件树反查刷新：登记 token，切换主题后重放（GUI-06）
        self._register_theme_tags(self.evo_log, {
            'section': {'foreground': 'accent_ai_light'},
            'success': {'foreground': 'accent_p5_light'},
            'warning': {'foreground': 'accent_warning_light'},
            'error': {'foreground': 'accent_danger_light'},
            'info': {'foreground': 'badge_trial_fg'},
            'dim': {'foreground': 'text_disabled'},
        })
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
        # 状态徽章颜色（v5.2 GUI-06：字面量改主题槽查表，斑马纹与徽章色
        # 一并登记，主题切换后 tag_configure 重放）
        self.evo_version_tree.tag_configure(
            'active', foreground=COLORS['accent_p5'],
            font=('微软雅黑', 9, 'bold'))
        self.evo_version_tree.tag_configure('trial', foreground=COLORS['text_muted'])
        self.evo_version_tree.tag_configure(
            'rolledback', foreground=COLORS['accent_warning'])
        # ttk Treeview tag 色不经控件树反查刷新：登记 token 重放（GUI-06）
        self._register_theme_tags(self.evo_version_tree, {
            'odd': {'background': 'bg_card'},
            'even': {'background': 'bg_secondary'},
            'active': {'foreground': 'accent_p5'},
            'trial': {'foreground': 'text_muted'},
            'rolledback': {'foreground': 'accent_warning'},
        })
        self.evo_version_tree.pack(fill=tk.BOTH, expand=True)
        # 绑定双击事件查看版本详情
        self.evo_version_tree.bind('<Double-1>', self._on_version_row_double_click)
        # 绑定右键菜单（回滚功能）
        self.evo_version_tree.bind('<Button-3>', self._on_version_right_click)
        # 启动后延迟填充，避免与布局初始化竞争
        self.root.after(1500, self._refresh_evolution_versions)

    def _build_cli_log_tab(self, parent):
        """构建「命令行输出」标签页：实时展示 stdout/stderr 与 logging 日志（需求 #3）。

        通过后台线程安全队列（modules.cli_log_sink）接收日志行，
        由 _poll_cli_log 每 200ms 在主线程渲染，效果等同「命令行输出界面化」。
        """
        COLORS = _main_mod.COLORS
        f = tk.Frame(parent, bg=COLORS['bg_primary'])
        f.pack(fill=tk.BOTH, expand=True)

        # ── 工具栏（清空 / 自动滚动）────────────────────────────────
        tb = tk.Frame(f, bg=COLORS['bg_primary'])
        tb.pack(fill=tk.X, padx=12, pady=(8, 4))
        tk.Label(tb, text="命令行实时输出", font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_primary'], fg=COLORS['text_primary']).pack(side=tk.LEFT)
        # 关键字过滤框（Phase 4：复用已渲染文本 + 内存缓冲，大小写不敏感）
        self._cli_filter_var = tk.StringVar()
        self._cli_filter_var.trace_add('write', self._apply_cli_filter)
        tk.Label(tb, text="过滤", font=('微软雅黑', 9),
                 bg=COLORS['bg_primary'], fg=COLORS['text_muted']).pack(
            side=tk.LEFT, padx=(12, 4))
        tk.Entry(tb, textvariable=self._cli_filter_var, width=18,
                 font=('Consolas', 9), bg=COLORS['bg_card'],
                 fg=COLORS['text_primary'], relief='flat',
                 insertbackground=COLORS['accent_ai']).pack(
            side=tk.LEFT, padx=(0, 10))
        self._cli_autoscroll_var = tk.BooleanVar(value=True)
        _asb = tk.Checkbutton(tb, text="自动滚动", variable=self._cli_autoscroll_var,
                              bg=COLORS['bg_primary'], fg=COLORS['text_muted'],
                              selectcolor=COLORS['bg_card'], activebackground=COLORS['bg_primary'],
                              command=lambda: setattr(self, '_cli_autoscroll', self._cli_autoscroll_var.get()))
        _asb.pack(side=tk.RIGHT, padx=(0, 8))
        tk.Button(tb, text="🗑️ 清空", command=self._clear_cli_log,
                  bg=COLORS['bg_card'], fg=COLORS['text_primary'],
                  activebackground=COLORS['bg_secondary'], relief='flat',
                  font=('微软雅黑', 9), padx=10, pady=3).pack(side=tk.RIGHT)

        # ── 日志文本框（等宽字体，类终端风格，与进化日志一致）──────────
        log_frame = tk.Frame(f, bg=COLORS['bg_panel'],
                             highlightbackground=COLORS['border'], highlightthickness=1)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        self.cli_log_text = tk.Text(log_frame, wrap=tk.WORD, font=('Consolas', 9),
                                    bg=COLORS['bg_panel'],
                                    fg=COLORS['text_secondary'],
                                    relief='flat', padx=12, pady=10,
                                    state=tk.NORMAL, undo=True,
                                    insertbackground=COLORS['cursor_cli'])
        self.cli_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        csb = tk.Scrollbar(log_frame, command=self.cli_log_text.yview, width=12,
                           bg=COLORS['bg_card'], troughcolor=COLORS['bg_secondary'],
                           activebackground=COLORS['accent_ai'])
        csb.pack(side=tk.RIGHT, fill=tk.Y)
        self.cli_log_text.config(yscrollcommand=csb.set)

        # 日志级别颜色（终端风格）
        self.cli_log_text.tag_config(
            'info', foreground=COLORS['badge_trial_fg'], font=('Consolas', 9))
        self.cli_log_text.tag_config(
            'success', foreground=COLORS['accent_p5_light'],
            font=('Consolas', 9))
        self.cli_log_text.tag_config(
            'warning', foreground=COLORS['accent_warning_light'],
            font=('Consolas', 9))
        self.cli_log_text.tag_config(
            'error', foreground=COLORS['accent_danger_light'],
            font=('Consolas', 9, 'bold'))
        self.cli_log_text.tag_config(
            'dim', foreground=COLORS['text_disabled'], font=('Consolas', 9))
        # Text tag 色不经控件树反查刷新：登记 token，切换主题后重放（GUI-06）
        self._register_theme_tags(self.cli_log_text, {
            'info': {'foreground': 'badge_trial_fg'},
            'success': {'foreground': 'accent_p5_light'},
            'warning': {'foreground': 'accent_warning_light'},
            'error': {'foreground': 'accent_danger_light'},
            'dim': {'foreground': 'text_disabled'},
        })
        self.cli_log_text.insert(tk.END,
                                 "> 等待命令行输出…（开始分析 / 数据爬取 / 自进化等动作的输出将实时刷新到此）\n", 'dim')
        # 内存缓冲：保留全部日志行（tag, line），供关键字过滤重建视图
        self._cli_log_buffer = []
        # Phase 4：右键复制选中 / 复制全部 / 清空
        self.cli_log_text.bind('<Button-3>', self._cli_log_context_menu)

    def _build_log_tab(self, parent):
        """（已移除）运行日志标签页改为写入 logs/gui_run.log，不再在界面渲染。"""

    # =========================================================================
    # 快捷概览栏 & 搜索
    # =========================================================================


    def _ov_tile(self, parent, label, value, value_color, sub=None):
        """概览指标磁贴：小标签(上) + 大数值(下) + 可选副信息，统一信息层次。"""
        COLORS = _main_mod.COLORS
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

    def _create_ov_tile(self, parent, label, value, value_color, sub=None):
        """创建概览指标磁贴（v4.0 §2.4）：小标签 + 大数值 + 可选副信息。

        与既有 _ov_tile 的区别：本方法返回「数值标签」句柄（tk.Label），
        使 _refresh_result_overview 能原地回填数值而不重建整个磁贴控件。

        参数:
            parent: 父容器（概览卡片区）
            label: 磁贴标题（顶部小字）
            value: 初始数值文本
            value_color: 数值文字颜色（主题键名，如 'accent_p5'）
            sub: 可选副信息（底部小字）

        返回:
            tk.Label: 承载数值的主标签，调用方持有以便后续 config(text=...) 回填
        """
        COLORS = _main_mod.COLORS
        tile = tk.Frame(parent, bg=COLORS['bg_secondary'],
                        highlightbackground=COLORS['border'], highlightthickness=1)
        # 4 列并排布局统一入口（v5.2 GUI-07：窄屏 grid 切回复用同参）
        self._pack_ov_tile_row(tile)

        tk.Label(tile, text=label, font=('微软雅黑', 8),
                 bg=COLORS['bg_secondary'], fg=COLORS['text_muted']
                 ).pack(anchor=tk.W, padx=10, pady=(8, 0))
        val_color = COLORS.get(value_color, COLORS['text_primary'])
        val_label = tk.Label(tile, text=value, font=('Consolas', 15, 'bold'),
                             bg=COLORS['bg_secondary'], fg=val_color)
        val_label.pack(anchor=tk.W, padx=10, pady=(0, 4 if sub else 8))
        if sub:
            tk.Label(tile, text=sub, font=('微软雅黑', 8),
                     bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']
                     ).pack(anchor=tk.W, padx=10, pady=(0, 8))
        return val_label

    @staticmethod
    def _pack_ov_tile_row(tile) -> None:
        """概览磁贴按「4 列并排 pack」布局（标准/宽绰档，v5.2 GUI-07）。

        构建期与 grid→row 切回均走本入口，保证 pack 参数单一来源，
        避免两处书写漂移导致两种并排形态。

        GUI-07 缺陷修复（v5.2 登记）：原实现 pack_propagate(False) 且
        只设 width 不设 height，磁贴请求高度塌缩为 1px（离屏实测显示
        高度 9px=1px 内容 + ipady 8px），内部标签溢出到底板之外、
        背景割裂。本入口改为
        自然尺寸（propagate True）：并排时 expand+fill=X 平分容器宽度
        （row 档容器宽度 >= _TILE_GRID_MAX_W=1100，单磁贴实际 >=265px，
        原 220px 最小宽约束已无适用场景），高度自适应内容 ~95px。
        grid 档下发自然宽度由列权重与 sticky 拉伸铺满，与此一致。

        参数:
            tile: 单个磁贴 Frame（_create_ov_tile 的返回值宿主）
        """
        # 自然尺寸：宽度由 pack expand 平分，高度由内容决定
        tile.pack_propagate(True)
        tile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=4)

    def _build_status_bar(self, parent):
        """构建底部状态栏（状态指示灯 + 状态文字 + 数据库状态）"""
        COLORS = _main_mod.COLORS
        status_bar = tk.Frame(parent, bg=COLORS['bg_secondary'], height=28)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        status_bar.pack_propagate(False)

        self.status_dot = tk.Canvas(status_bar, width=14, height=14,
                                    bg=COLORS['bg_secondary'],
                                    highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(12, 5), pady=7)
        # 内外双层圆模拟发光效果
        self.status_dot.create_oval(0, 0, 14, 14, fill=COLORS['bg_secondary'], outline='')
        self._status_dot_id = self.status_dot.create_oval(2, 2, 12, 12,
                                                          fill=COLORS['success'], outline='')

        self.status_var = tk.StringVar(value="✓ 就绪")
        tk.Label(status_bar, textvariable=self.status_var,
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(side=tk.LEFT, padx=5, pady=4)

        tk.Frame(status_bar, bg=COLORS['border'], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=5)

        # 数据库状态标签
        self.db_status_var = tk.StringVar(value="数据库：检测中")
        tk.Label(status_bar, textvariable=self.db_status_var,
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(side=tk.RIGHT, padx=5, pady=4)

        # 版本信息
        tk.Label(status_bar, text=f"Python 3.9 | v{get_current_version()}",
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(side=tk.RIGHT, padx=5, pady=4)

        # 30s 轮询数据库状态
        self.root.after(30000, self._refresh_db_status)

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
        COLORS = _main_mod.COLORS
        # 按钮三态样式表由视觉规范工厂按当前主题即时产出（v5.2 GUI-08）：
        # 主题切换后天然跟随，且消除与 _on_button_leave 的重复字面量
        _style_map = button_style_map(COLORS)
        style = _style_map.get(button_type, _style_map['secondary'])
        
        btn = tk.Button(parent, text=text, command=command,
                        font=style['font'],
                        bg=style['bg'],
                        fg=style['fg'],
                        activebackground=style['active_bg'],
                        activeforeground=WHITE if button_type == 'danger' else style['fg'],
                        relief='flat', 
                        bd=0, 
                        padx=10, 
                        pady=4, 
                        cursor='hand2',
                        highlightthickness=0,
                        takefocus=True)  # v5.0 §2.1 可访问性：允许 Tab 聚焦
        
        # 记录正常态配色（v4.0 §2.4：token 化，主题切换后还原仍正确，不泄漏旧色）
        # primary 的 fg 固定为白色（两主题下文字均白），故静态存 WHITE 常量；
        # 其余 bg/fg 存主题键 token，_resolve_color_token 在还原时动态取当前主题值。
        _token_map = {
            'primary': ('accent_p5', WHITE),
            'secondary': ('bg_card', 'text_secondary'),
            'danger': ('bg_card', 'accent_danger_light'),
        }
        _bg_tok, _fg_tok = _token_map.get(button_type, _token_map['secondary'])
        btn._normal_bg = _bg_tok
        btn._normal_fg = _fg_tok
        
        # 添加悬停效果
        btn.bind('<Enter>', lambda e, b=btn: self._on_button_enter(e, b, button_type))
        btn.bind('<Leave>', lambda e, b=btn: self._on_button_leave(e, b, button_type))
        
        # 注册到按钮列表，便于批量启用/禁用（任务运行时自动禁用）
        self._buttons.append(btn)
        
        return btn

    def _on_button_enter(self, event, button, button_type):
        """按钮悬停进入效果（禁用时不响应，避免任务运行中误触变色误导视觉）"""
        COLORS = _main_mod.COLORS
        if str(button['state']) == 'disabled':
            return
        if button_type == 'primary':
            button.config(bg=COLORS['accent_p5_light'])
        elif button_type == 'secondary':
            button.config(bg=COLORS['bg_card_hover'])
            button.config(fg=COLORS['text_primary'])
        elif button_type == 'danger':
            button.config(bg=COLORS['accent_danger'])
            button.config(fg=WHITE)

    def _on_button_leave(self, event, button, button_type):
        """按钮悬停离开效果（禁用时维持禁用外观，不还原为正常态色）"""
        COLORS = _main_mod.COLORS
        # 按钮三态样式表由视觉规范工厂按当前主题即时产出（v5.2 GUI-08）
        _style_map = button_style_map(COLORS)
        style = _style_map.get(button_type, _style_map['secondary'])
        button.config(bg=style['bg'], fg=style['fg'])
        # 若仍处于禁用（任务运行中），应用禁用外观而非正常态
        if str(button['state']) == 'disabled':
            button.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])

    def _setup_loading_animation(self):
        """设置加载动画效果"""
        COLORS = _main_mod.COLORS
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

    def _build_hit_rate_tab(self, parent):
        """构建『历史命中率』常驻标签页：真实验证数据(不编造), 启动即填充, 预测/切页时刷新。"""
        COLORS = _main_mod.COLORS
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

        # 图表显示区域（用于基线对照等可视化）
        self.chart_label = tk.Label(f, bg=COLORS['bg_primary'])
        self.chart_label.pack(fill=tk.X, pady=(0, 8))
        
        # ================================================================
        # 说明文字（可折叠）
        # ================================================================
        # 创建可折叠的说明框架
        self.hr_explanation_frame = tk.Frame(f, bg=COLORS['bg_primary'])
        self.hr_explanation_frame.pack(fill=tk.X, pady=(0, 8))
        self.hr_explanation_visible = tk.BooleanVar(value=False)
        
        # 说明文字标题（可点击切换）
        explanation_header = tk.Frame(self.hr_explanation_frame, bg=COLORS['bg_primary'])
        explanation_header.pack(fill=tk.X)
        tk.Label(explanation_header, text=" 命中率说明：", 
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_primary'], fg=COLORS['text_primary']).pack(side=tk.LEFT)
        self.hr_explanation_toggle = tk.Label(explanation_header, text=" [显示]", 
                                            font=('微软雅黑', 8),
                                            bg=COLORS['bg_primary'], fg=COLORS['text_secondary'],
                                            cursor="hand2")
        self.hr_explanation_toggle.pack(side=tk.RIGHT)
        self.hr_explanation_toggle.bind("<Button-1>", self._toggle_hr_explanation)
        
        # 说明文字内容（初始隐藏）
        self.hr_explanation_text = tk.Label(self.hr_explanation_frame, 
                                          text="• 容错命中(±1)：号码偏差1以内也算命中，数据偏高\n"
                                               "• 严格命中（精确）：仅完全匹配才算命中，更真实反映预测能力\n"
                                               "数据来源：p5_prediction_record 已验证记录（真实、不编造）。",
                                          font=('微软雅黑', 9), bg=COLORS['bg_primary'], fg=COLORS['text_muted'],
                                          wraplength=420, justify=tk.LEFT)
        # 初始隐藏说明文字
        self.hr_explanation_text.pack_forget()

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
                       selectcolor=COLORS.get('bg_input', WHITE),
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
                                           background=COLORS.get('success', SUCCESS_STATIC),
                                           foreground=WHITE)
        # 排序状态: (列key, 是否降序)
        self._hr_sort_state = (None, False)
        # 双击某行 → 查看该期完整 AI 报告
        self.hr_records_tree.bind('<Double-1>', self._on_record_double_click)
        tk.Label(f, text=" 双击任一行可查看该期完整 AI 预测详情；点击「期号/命中位数/准确率/验证时间」列头可排序。",
                 font=('微软雅黑', 8), bg=COLORS['bg_primary'],
                 fg=COLORS['text_muted']).pack(anchor=tk.W, pady=(4, 0))

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
                       selectcolor=COLORS.get('bg_input', WHITE),
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
                                           background=COLORS.get('success', SUCCESS_STATIC),
                                           foreground=WHITE)
        # 排序状态: (列key, 是否降序)
        self._hr_sort_state = (None, False)
        # 双击某行 → 查看该期完整 AI 报告
        self.hr_records_tree.bind('<Double-1>', self._on_record_double_click)
        tk.Label(f, text=" 双击任一行可查看该期完整 AI 预测详情；点击「期号/命中位数/准确率/验证时间」列头可排序。",
                 font=('微软雅黑', 8), bg=COLORS['bg_primary'],
                 fg=COLORS['text_muted']).pack(anchor=tk.W, pady=(4, 0))
