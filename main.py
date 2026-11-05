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
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 控制台仅输出 WARNING 及以上级别（INFO/DEBUG 仅写入文件），减少终端噪音
logging.getLogger().setLevel(logging.WARNING)
for _h in logging.getLogger().handlers:
    if isinstance(_h, logging.StreamHandler) and not isinstance(_h, logging.FileHandler):
        _h.setLevel(logging.WARNING)

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


def _safe_grab_set(win):
    """安全版 grab_set：窗口已持有 / 另一弹窗先占锁时不得让整个对话框崩掉。

    Tk 的 `grab_set()` 在「已有其他窗口持有 grab」或某些远程桌面 / 无显示
    环境下会抛 TclError。对话框通常在 `win.grab_set()` 之后才填充内容，
    一旦抛错就会留下一个只有标题栏的空壳窗口——用户看到的是「点了按钮
    弹出一个空白弹窗」，比直接失败更糟。此处降级为「不抓焦点」，
    对话框仍可正常展示与关闭。
    """
    try:
        # 修复（v5.3 GUI-UX）：此处原为 `_safe_grab_set(win)` 自递归调用，
        # 递归到解释器栈上限后抛 RecursionError 被本函数捕获，导致
        # 所有模态弹窗**从未真正 grab_set**（焦点可逃逸到主窗口），
        # 且每次调用平白消耗约 1000 层栈帧。改为调用 Tk 原生 grab_set。
        win.grab_set()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning('[GUI] grab_set 失败，对话框降级为普通弹窗（内容不受影响）: %s', e)
        return False


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
from config import DATA_WINDOW_PERIOD, FIRST_RUN_ENV_CREATED, ENV_FILE_PATH
from paths import PROJECT_ROOT, REPORTS_DIR, REPORTS_CHARTS_DIR, LOG_GUI_RUN
# 新版任务管理器（多线程、优先级队列、协作式取消、超时控制）
from modules.task_manager import TaskManager
# 四步流水线（模块级别导入，避免方法内部导入导致 P5Database 局部变量遮蔽）
from modules.pipeline import run_four_step_pipeline
# 可视化模块
from modules.visualizer import visualizer, set_theme as _viz_set_theme
# 中央样式工厂（v5.0 §2.1：统一色彩 token / 按钮三态 / ttk 样式注册）
from modules import ui_styles
# Toast 浮层契约（v5.2 GUI-10）：停留时长钳制 / 图标解析纯函数单一来源
from modules import ui_toast
# GUI-11：构建期面板 Mixin 装配（方案 §337，main.py 拆分为薄入口）
from gui.panels.output_panel import OutputPanelMixin  # noqa: E402

# 主题自适应系统（v5.2 GUI-08）：深浅主题色板与系统主题检测统一迁入
# modules/ui_theme_spec；经再导出保持 main.ThemeManager / main.COLORS
# 的外部引用（审计脚本/守护脚本/单测，含 is 身份比较）完全兼容
from modules.ui_theme_spec import (
    ThemeManager,
    button_style_map,
    WHITE,
    SUCCESS_STATIC,
    HIT_FG,
    TOP3_FG,
    MISS_FG,
    HIT_ROW_BG,
    HIT_ROW_FG,
    CONSENSUS_HIT_FG,
    CONSENSUS_HIT_BG,
    ACCENT_P5_SOFT_FG,
    CHART_SYSTEM,
    CHART_SYSTEM_ERR,
    CHART_RANDOM,
    CHART_RANDOM_ERR,
    CHART_NOTE_TEXT,
    CHART_NOTE_FACE,
    CHART_NOTE_EDGE,
    CHART_BARS,
    UI_FONT,
)

# 全局颜色主题配置（当前系统主题的实时色板）
COLORS = ThemeManager.get_theme()
# GUI-11：渲染期/轮询期 Mixin 装配（方案 §337）。插入点必须位于
# COLORS 绑定之后：gui/scheduler.py 文件级 COLORS 别名在 import
# 期求值，依赖 main.COLORS 已完成绑定（时序说明见 scheduler.py）。
from gui.dashboard import DashboardMixin  # noqa: E402
from gui.scheduler import SchedulerMixin  # noqa: E402

# ---------------------------------------------------------------------------
# 右侧仪表盘（Canvas + 内部 Frame）事件循环治理常量（v5.2 §2.2 GUI-01/02）
# ---------------------------------------------------------------------------
# GUI-01：dash_container Configure 回写内部视窗宽度的最小变化阈值（像素）。
# 1px 级拖拽抖动不足以产生可见差异，合并后可避免大量无效 itemconfig 重排。
_DASH_WIDTH_SYNC_THRESHOLD = 2
# GUI-02：scrollregion 刷新防抖窗口（毫秒）。50ms 内的多次刷新请求合并为一次，
# 使刷新执行离开 Configure 事件回调栈（验收 A4），同时拖拽期间刷新次数大幅下降。
_DASH_REFRESH_DEBOUNCE_MS = 50

# ---------------------------------------------------------------------------
# 响应式布局三档断点常量（v5.2 GUI-07，方案 PUP-20261019-013100 §308-317）
# ---------------------------------------------------------------------------
# 断点口径以「输出面板 Notebook 实际宽度」为准（非窗口宽度）：左侧导航栏
# 常驻约 270px，窗口 1024×720 时面板实测仅 ~712px；只有按面板宽度分类，
# 验收三尺寸（1024×720 / 1366×768 / 1920×1080）才分别落入紧凑 / 标准 /
# 宽绰档（离屏实测 712 / 1054 / 1608）。
# 紧凑档：面板宽度 < 1024px。工具栏按钮切换短文案，防止操作区溢出，
# 保障窄屏可交互性（验收 A17）。
_BREAKPOINT_COMPACT_MAX_W = 1024
# 标准档上限：面板宽度 <= 1440px；超过进入宽绰档（A2 四档扩展）。
# 宽绰档视觉与标准档一致（工具栏全文案 + 磁贴 4 列）。
_BREAKPOINT_STANDARD_MAX_W = 1440
# 超宽档阈值：面板宽度 > 1600px 进入 ultrawide 档（A2 四档断点体系）。
# 当前 ultrawide 档复用宽绰档视觉（全文案 + 4 列磁贴），但作为独立档位
# 供超宽屏差异化扩展（如后续加 5 列磁贴 / 工具栏全宽分布）锚点，
# 且保证从 1920px 高分屏缩窗时不会误触紧凑档切换。
_BREAKPOINT_WIDE_MAX_W = 1600
# 概览磁贴 2×2 网格启用阈值：磁贴容器宽度 < 1100px 时由 4 列并排 pack
# 切换为 grid 网格（方案 §311）；>= 1100px 保持 4 列并排。
_TILE_GRID_MAX_W = 1100

# ============================================================
# 预测号码「展示层」配置（v3.64：扩展为5位全展示，含个位）
# 预测号码段、复制摘要、仪表盘均使用完整5位（万/千/百/十/个）。
# 核心算法、数据存储、命中率分析等仍使用完整 5 位，不受影响。
# ============================================================
DISPLAY_POS_KEYS = ['wan', 'qian', 'bai', 'shi', 'ge']
DISPLAY_POS_NAMES = ['万位', '千位', '百位', '十位', '个位']


def compress_combo(combo):
    """将 5 位预测组合返回为显示串（v3.64：保留完整5位）。

    核心数据不动，仅用于展示/复制层。幂等：对已是5位的串无副作用。
    """
    if not combo:
        return ''
    s = str(combo)
    return s[:5] if len(s) >= 5 else s


# ---------------------------------------------------------------------------
# GUI-04：轮询统一调度特性开关（v5.2 §2.3 / R4 回滚路径）。
# 默认开启：三条轮询（_poll_cli_log / _poll_evolution / _update_time）由
# _ui_scheduler 单一 100ms tick 循环统一触发；置环境变量
# ENABLE_UI_SCHEDULER=0/false/no/off（大小写不敏感）时回退为各自独立的
# after 自调度链，行为与 v5.1 完全一致（R4 回滚预案）。
# ---------------------------------------------------------------------------
def _ui_scheduler_enabled() -> bool:
    """返回轮询统一调度器是否启用（默认 True，环境变量可关闭）。"""
    val = (os.environ.get('ENABLE_UI_SCHEDULER') or '1').strip().lower()
    return val not in ('0', 'false', 'no', 'off')


class _UIScheduler:
    """GUI-04（v5.2 §2.3）：轮询定时器统一批处理调度器。

    背景：原 _poll_cli_log（200ms）/ _poll_evolution（200ms）/ _update_time
    （1000ms）三条各自独立的 after 自调度链与全量重建交错，既是主线程回调
    风暴来源（30s 约 310+ 次），也使「时钟刷新」与「日志渲染」在不同帧
    交错产生可见撕裂。

    设计要点：
      1. 注册制：register(name, job, interval_ms) 后由本调度器统一触发，
         任务自身不再调用 root.after 自调度；任务签名保持原样（无参回调）。
      2. 单一 100ms after 循环：到期任务在同一个主线程帧内串行执行，
         天然避免「同一 flush 周期跨帧撕裂」（A13 单帧口径）。
      3. 空闲退避：60s 无业务事件（notify_event）→ 跟随全局间隔任务的
         轮询间隔 200ms→500ms；固定间隔任务（如时钟 1000ms）不受影响。
      4. 漏调度防护：单个任务异常仅记警告并跳过，绝不拖垮循环；循环续期
         置于 finally，异常逃逸也不会导致轮询停摆。

    线程约定：所有调用均发生在 Tk 主线程（after 回调栈），不做加锁。
    """

    # 单一调度循环的基础步长（毫秒）
    BASE_TICK_MS = 100
    # 默认业务轮询间隔（毫秒）：与旧 _poll_cli_log / _poll_evolution 一致
    ACTIVE_INTERVAL_MS = 200
    # 空闲退避间隔（毫秒）：60s 无业务事件后启用
    IDLE_INTERVAL_MS = 500
    # 无业务事件判定阈值（毫秒）
    IDLE_THRESHOLD_MS = 60000

    def __init__(self, after_fn, after_cancel_fn=None, monotonic_fn=None):
        """初始化调度器（不启动循环，start() 后才开始 tick）。

        Args:
            after_fn: root.after 的可调用替身（注入便于单测计数）。
            after_cancel_fn: root.after_cancel 替身（可选）。
            monotonic_fn: 单调时钟替身（可选，默认 time.monotonic）。
        """
        self._after = after_fn
        self._after_cancel = after_cancel_fn
        self._monotonic = monotonic_fn or time.monotonic
        # 任务表：name -> {'job': callable, 'interval': int(0=跟随全局), 'last': float}
        self._jobs = {}
        self._running = False
        self._stopped = False
        self._tick_handle = None
        self._last_event_ts = self._monotonic()
        self._idle = False

    # ---- 生命周期 ----
    def start(self):
        """启动单一调度循环（幂等；stop() 后不可重启）。"""
        if self._running or self._stopped:
            return
        self._running = True
        self._last_event_ts = self._monotonic()
        self._schedule_tick()

    def stop(self):
        """停止调度循环并取消挂起的 tick（幂等）。"""
        self._stopped = True
        self._running = False
        handle, self._tick_handle = self._tick_handle, None
        if handle is not None and self._after_cancel is not None:
            try:
                self._after_cancel(handle)
            except Exception:
                pass

    # ---- 任务管理 ----
    def register(self, name, job, interval_ms=0):
        """注册轮询任务（同名重复注册覆盖并重置计时）。

        Args:
            name: 任务唯一键。
            job: 到期时在主线程执行的无参回调。
            interval_ms: 固定触发间隔（毫秒）；0 表示跟随全局轮询间隔
                        （默认 200ms，空闲退避后 500ms）。
        """
        self._jobs[name] = {
            'job': job,
            'interval': int(interval_ms or 0),
            'last': self._monotonic(),
        }

    def unregister(self, name):
        """注销任务（若存在），注销后调度循环不再触发它。"""
        self._jobs.pop(name, None)

    def job_names(self):
        """返回已注册任务名列表（供自省/单测）。"""
        return list(self._jobs)

    # ---- 业务事件通知（驱动空闲退避切换）----
    def notify_event(self):
        """业务事件到达：重置空闲计时，恢复 200ms 活跃轮询间隔。"""
        self._last_event_ts = self._monotonic()
        self._idle = False

    # ---- 内部机制 ----
    def _poll_interval(self):
        """当前全局轮询间隔（毫秒）：活跃 200ms / 空闲 500ms。"""
        return self.IDLE_INTERVAL_MS if self._idle else self.ACTIVE_INTERVAL_MS

    def _schedule_tick(self):
        """安排下一次 tick（after 失败时停循环并告警，绝不外冒异常）。"""
        try:
            self._tick_handle = self._after(self.BASE_TICK_MS, self._tick)
        except Exception as _e:
            self._running = False
            logger.warning('[GUI] _ui_scheduler tick 调度失败(循环停止): %s', _e)

    def _tick(self):
        """单一调度循环体：到期任务同帧串行执行（防撕裂 + 便于探针统计）。"""
        if self._stopped:
            return
        now = self._monotonic()
        # 空闲退避判定（毫秒阈值换算为单调秒）
        self._idle = (now - self._last_event_ts) * 1000.0 >= self.IDLE_THRESHOLD_MS
        try:
            for name in list(self._jobs):
                spec = self._jobs.get(name)
                if spec is None:
                    continue
                interval = spec['interval'] or self._poll_interval()
                if now - spec['last'] >= interval / 1000.0:
                    spec['last'] = now
                    try:
                        spec['job']()
                    except Exception as _je:
                        # 漏调度防护：单任务异常不拖垮循环，也不影响其他任务
                        logger.warning('[GUI] _ui_scheduler 任务 %s 执行异常(已跳过): %s',
                                       name, _je)
        finally:
            # 续期置于 finally：即使上方异常逃逸也不会导致轮询停摆
            if self._running:
                self._schedule_tick()


class LotteryGUI(OutputPanelMixin, DashboardMixin, SchedulerMixin):
    """
    排列5 AI智能分析系统主界面

    负责构建和管理整个GUI，包括：
    - 控制面板（数据爬取、智能分析中心、自我进化、智能分析与验证四个功能卡片）
    - 输出面板（预测结果 / 命令行输出 / 自我进化 三个标签页；命令行输出实时展示 stdout/stderr 与 logging 日志）
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
        # 兼容极宽/极窄屏：下限 780px，上限 1024px，避免极化宽屏下窗口过宽
        # 或极窄屏下窗口无法完全显示。
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        # 宽度：最宽 1280px，留出 40px 边距；最窄不低于屏幕宽的 80%
        win_width = min(1280, max(int(screen_width * 0.8), screen_width - 40))
        # 高度：屏幕 85% 作为目标，允许 780~1024px 范围内波动，确保内容可见
        win_height = max(780, min(1024, int(screen_height * 0.85)))
        # 确保窗口坐标不为负数
        x = max(0, (screen_width - win_width) // 2)
        y = max(0, (screen_height - win_height) // 2)
        self.root.geometry(f"{win_width}x{win_height}+{x}+{y}")
        self.root.minsize(1024, 720)
        self.root.configure(bg=COLORS['bg_primary'])

        # §3.4 模型监控「建议型回灌」接线（惰性 + 幂等 + 失败静默）：
        # 把 ModelMonitor 的 feedback_callback 接到 monitor_feedback 的建议型 sink，
        # 使监控达到反馈阈值时以「诚实洞察」追加到 data/ 供「自动评估」页复核。
        # 该接线只记录洞察、**不**触碰冻结权重；配置开关 MODEL_FEEDBACK_ENABLE
        # 关闭时 wire 函数内部自行跳过。异常绝不阻断 GUI 启动。
        try:
            import config as _cfg
            if getattr(_cfg, 'MODEL_FEEDBACK_ENABLE', True):
                from modules.monitor_feedback import wire_model_monitor_feedback
                wire_model_monitor_feedback()
        except Exception as _mf_e:
            import logging as _lg
            _lg.getLogger(__name__).warning('模型监控建议型回灌接线跳过(非致命): %s', _mf_e)

        # §4.3 系统性能监控启动（惰性 + 幂等 + 失败静默）：1Hz 守护线程采集
        # CPU/内存/响应/任务耗时 + 资源压力越限自动优化 + 趋势留存。纯内存 +
        # 可选本地 JSONL，无 DB 依赖；psutil 不可用时 CPU/内存指标优雅降级为
        # None（任务/耗时类指标纯标准库、恒可用）。配置开关 PERF_MONITOR_ENABLE
        # 关闭时跳过。daemon 线程，关闭时在 _confirm_close 里 stop_perf_monitoring()。
        try:
            import config as _cfg2
            if getattr(_cfg2, 'PERF_MONITOR_ENABLE', True):
                from modules.perf_monitor import start_perf_monitoring
                start_perf_monitoring()
        except Exception as _pf_e:
            import logging as _lg2
            _lg2.getLogger(__name__).warning('性能监控启动跳过(非致命): %s', _pf_e)

        self.task_mgr = TaskManager(self, max_workers=4, queue_size=100, enable_structured_logging=True)  # 异步任务管理器（多线程、优先级队列、协作式取消、超时控制）
        # §需求#3 命令行实时日志收集器：捕获 stdout/stderr + 全部 logging 记录，
        # 推入线程安全队列，由 _poll_cli_log 在「命令行输出」标签页实时渲染。
        # 安装为幂等，失败静默（绝不阻断 GUI 启动）。
        try:
            from modules.cli_log_sink import install_cli_sink
            install_cli_sink()
        except Exception as _cli_e:
            import logging as _lg3
            _lg3.getLogger(__name__).warning('CLI 日志收集器安装跳过(非致命): %s', _cli_e)
        self._buttons = []  # 所有按钮列表（用于批量启用/禁用）
        self._current_task_name = ""  # 当前正在执行的任务名称
        self._prediction_clipboard = ""  # 最近一次生成的预测号码（供一键复制）
        self._clipboard_meta = {}  # 复制缓冲元数据：{target_issue, conf, high_conf, main_combo}
        # 结果仪表盘（合并视图）：保存最近一次各来源预测产物
        self._last_pipeline_final = None
        self._last_trend_result = None
        self._last_quick_final = None
        # v4.1：各来源产物生成时间戳 + 聚合剪贴板生成时间戳（用于判定复制数据是否过期）
        self._last_pipeline_ts = 0
        self._last_trend_ts = 0
        self._last_quick_ts = 0
        self._clipboard_generated_ts = 0
        # 供「命中率优化」融合阶段复用的融合概率与预测元数据，
        # 避免为做选号策略对照而重复跑一次 predict（单次可达数十秒）。
        self._last_fused_probabilities = None
        self._last_predict_meta = {}
        # 仪表盘折叠区状态（推荐/备选/详细分析）
        self._detail_frame = None   # 详细分析折叠区
        self._alt_content = None    # 备选号码折叠区
        self._alt_visible = False
        # GUI-03 增量渲染（v5.2 §2.2）：结果卡片索引与上次渲染状态。
        # key 形如 'section::card_id'；_dash_card_state 保存对应规格（含内容签名），
        # 渲染时经 _dash_diff 计算 add/update/remove，仅对差异部分做 create/destroy。
        self._dash_card_index = {}   # type: Dict[str, tk.Widget]
        self._dash_card_state = {}   # type: Dict[str, dict]
        self._dash_grid_ncols = {}   # type: Dict[str, int] 各 grid 容器已配置列数
        self.evolution = None       # 自我进化引擎句柄（启动后自动初始化）
        self._poll_evolution_handle = None  # _poll_evolution after 定时器句柄（用于关闭时精确 cancel）
        self._liveness_watchdog_handle = None  # _liveness_watchdog after 定时器句柄（用于关闭时精确 cancel）
        self._update_time_handle = None  # _update_time after 定时器句柄（用于关闭时精确 cancel）
        self._poll_cli_log_handle = None  # _poll_cli_log after 定时器句柄（用于关闭时精确 cancel）
        # GUI-04：轮询统一调度器句柄（v5.2 §2.3）。None 表示特性开关关闭
        # 或初始化失败，此时三条轮询回退为各自独立的 after 自调度链（R4）。
        self._ui_scheduler = None
        self.cli_log_text = None  # 命令行输出文本框（实时日志标签页）
        self._cli_autoscroll = True  # 命令行日志自动滚动到底部
        # 进度节流状态（v5.0 §2.1.3：值 + 文案双维度节流）
        self._last_progress_value = -1
        self._last_progress_text = ""
        # 预计剩余（ETA）几何外推估计器，仅运行中样本参与外推
        self._progress_est = ui_styles.ProgressEstimator()
        # 任务收尾原因（'' = 正常完成 / 'timeout' = 超时被杀），
        # 用于区分「用户主动取消」与「超时取消」的文案呈现
        self._task_finish_reason = ""
        # 行号更新节流
        self._line_number_update_pending = False
        # 概览刷新标志
        # overview_flush_pending removed = False

        # GUI-04：初始化轮询统一调度器并注册任务（须先于 _build_ui：
        # _build_ui 内状态栏会直接调用 _update_time 做首刷；注册后由 tick 接管）。
        self._init_ui_scheduler()
        self._setup_window_style()
        self._build_ui()
        # 启动即自动触发「自我进化」引擎（后台守护线程，非阻塞）
        # 延迟初始化：避免在 UI 渲染阶段阻塞主线程导致白屏卡顿
        self.root.after(100, self._init_evolution_engine)
        # 命令行实时日志轮询（每 200ms 消费队列，主线程安全渲染，永不阻塞）
        # GUI-04：调度器开启时已在 _init_ui_scheduler 注册，此处仅覆盖回退路径
        if self._ui_scheduler is None:
            self.root.after(200, self._poll_cli_log)
        self._setup_keyboard_shortcuts()
        # 加载主题偏好
        self._load_theme_preference()
        
        # 初始化可视化菜单
        self._viz_menu = None

    def _setup_window_style(self):
        """配置ttk样式主题（暗色主题，clam引擎）"""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=(UI_FONT, 10),
                        background=COLORS['bg_primary'],
                        foreground=COLORS['text_primary'],
                        fieldbackground=COLORS['bg_secondary'])
        # v5.3 GUI-UX：统一 Notebook / Combobox / Entry / Button 的主题化外观。
        # 此前这些 ttk 控件完全沿用 clam 默认（浅灰页签 + 白底输入框），
        # 在深色主题下与卡片形成明显割裂；集中注册到 ui_styles 单一实现，
        # 避免 main.py 与 theme refresh 两条路径出现样式漂移。
        ui_styles.build_ttk_styles(self.root, COLORS)
        style.configure('TFrame', background=COLORS['bg_primary'])
        style.configure('Horizontal.TProgressbar',
                        background=COLORS['accent_p5'],
                        troughcolor=COLORS['bg_card'],
                        borderwidth=0)

        # ------------------------------------------------------------------
        # Treeview 暗色适配
        # 问题：clam 主题下 Treeview 的行背景不会继承 '.' 样式，实测
        # style.lookup('Treeview','background') 实测返回白色（非主题随动值），
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
                  foreground=[('selected', WHITE)])
        style.configure('Treeview.Heading',
                        background=COLORS['bg_secondary'],
                        foreground=COLORS['text_primary'],
                        relief='flat',
                        borderwidth=0,
                        font=(UI_FONT, 9, 'bold'))
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

        # v5.0 §2.1 键盘可达性：开始分析 / 导出
        # 复用 _on_button_click（内含 is_running() 守卫、任务提交与看门狗），
        # 保证快捷键与点击按钮走完全相同的状态机，不会并发重复启动。
        def _on_ctrl_return(_e=None):
            self._on_button_click("智能分析", self._execute_unified_analysis)

        def _on_ctrl_e(_e=None):
            try:
                self._show_export_menu()
            except Exception as _e:
                logger.debug(f'快捷键导出失败(非致命): {_e}')

        self.root.bind('<Control-Return>', _on_ctrl_return)
        # 兼容小键盘回车
        self.root.bind('<Control-KP_Enter>', _on_ctrl_return)
        self.root.bind('<Control-e>', _on_ctrl_e)
        self.root.bind('<Control-E>', _on_ctrl_e)

        # Escape 取消当前操作
        self.root.bind('<Escape>', lambda e: self._cancel_current_task())

    def _build_ui(self):
        """构建完整的UI布局：顶部标题栏 + 中部（左侧控制面板 + 右侧输出区） + 底部状态栏。

        布局采用 grid 响应式：左侧控制面板权重 0（固定最小宽度，窗口缩小时可折叠为
        图标模式），右侧输出区权重 1（随窗口横向撑开）。顶部标题栏与底部状态栏横
        向占满（weight=1），高度随内容自适应。
        """
        main_container = tk.Frame(self.root, bg=COLORS['bg_primary'])
        main_container.grid(row=0, column=0, sticky='nsew')
        # 根窗口配置：单行单列，随窗口撑开。
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        # 三行：标题 / 内容 / 状态栏；内容行随窗口纵向撑开。
        main_container.grid_rowconfigure(0, weight=0)
        main_container.grid_rowconfigure(1, weight=1)
        main_container.grid_rowconfigure(2, weight=0)
        main_container.grid_columnconfigure(0, weight=1)

        self._build_header(main_container)
        self._build_content(main_container)
        self._build_status_bar(main_container)

        # 响应式断点：绑定窗口大小变化，<1000px 时左栏折叠为图标-only。
        self._bind_responsive_breakpoints()

    def _bind_responsive_breakpoints(self):
        """绑定窗口尺寸变化响应式断点。

        窗口宽度 < 1000px：左侧控制面板折叠为图标-only（minsize 收至 60px），
        右侧输出区撑开全部可用空间。
        窗口宽度 >= 1000px：恢复完整卡片模式（minsize 260px）。
        使用 after_idle 合并连续拖拽事件，避免重复重排。
        """
        self.root.bind('<Configure>', self._on_layout_resize)

    def _on_layout_resize(self, event=None):
        """窗口尺寸变化时的响应式重排。

        仅在宽度跨越 1000px 阈值时调整左栏 minsize，避免每次 <Configure>
        都触发完整重排。
        """
        if event is not None and event.widget is not self.root:
            return
        self.root.after_idle(self._apply_responsive_breakpoint)

    def _apply_responsive_breakpoint(self):
        """根据窗口宽度切换左栏标准/紧凑两档宽度（v5.3 GUI-UX）。

        原实现仅在 ``width < 1000`` 时把左栏 minsize 改成 60，但窗口最小宽度
        被锁定为 1024（永远进不去该分支），且左栏 ``grid_propagate(False)``
        的请求宽度仍是 260，即便改 minsize 也不会真正收缩——整套响应式逻辑
        实为空转。现改为按窗口宽度直接设置容器/画布宽度，并同步卡片说明文字
        的 wraplength，保证 1024px 窄窗下文字不被裁切。
        """
        try:
            width = self.root.winfo_width()
        except Exception:
            return
        left_container = getattr(self, '_left_container', None)
        left_canvas = getattr(self, '_left_canvas', None)
        if left_container is None or left_canvas is None:
            return
        # 800 下限用于排除窗口尚未完成映射时的 width=1 噪声值
        compact = 800 <= width < 1120
        target = 232 if compact else 260
        try:
            left_container.configure(width=target)
            left_canvas.configure(width=target)
            # 画布宽度变化会触发 <Configure>，内嵌 frame 自动跟随；
            # 说明文字的换行宽度按可用内宽（栏宽 - 内边距）同步。
            for lbl in list(getattr(self, '_left_wrap_labels', [])):
                try:
                    lbl.configure(wraplength=target - 44)
                except Exception:
                    pass
        except Exception:
            pass

    # 主题管理
    _current_theme = 'dark'  # 记录当前主题状态

    def _toggle_theme(self):
        """切换主题：深色↔浅色"""
        if self._current_theme == 'dark':
            self._current_theme = 'light'
            self._apply_theme(ThemeManager.LIGHT_THEME)
        else:
            self._current_theme = 'dark'
            self._apply_theme(ThemeManager.DARK_THEME)
        # v5.3 GUI-UX 第二批：Canvas 图标由 _apply_theme 末尾统一重绘，
        # 不再设置 emoji 文本（原 .config(text=...) 在 Canvas 上不适用）。
        # 保存主题设置
        self._save_theme_preference()

    def _apply_theme(self, theme: dict):
        """应用主题配色到所有控件。

        修复（v5.0 §2.3）：旧实现在重绑定 ``COLORS`` **之后**才遍历控件，并用
        ``hasattr(widget, 'bg')`` 判定可改属性。tkinter 控件并不把 ``bg``/``background``
        暴露为 Python 属性，判定恒为 False，导致 tk.Button / tk.Label / tk.Frame 被整批
        跳过——主题切换后大面积残留旧主题色（实测 12 个 bg_card 按钮卡死）。
        现改为：切换前快照旧主题做「色值→语义槽」反查，遍历时直接 cget/config。
        """
        global COLORS
        old_theme = COLORS  # 控件当前色取自旧主题，必须按旧主题反查语义槽
        old_color_map = self._build_old_color_map(old_theme)
        COLORS = theme
        # 根窗口直接取新主题值（不参与反向映射：它已被改写为新值，再按旧主题反查
        # 会误配为第三色——实测浅色 text_primary 与深色 bg_primary 同为 #0f172a）
        self.root.configure(bg=theme['bg_primary'])
        # 重新应用样式
        self._setup_window_style()
        # 递归更新所有子控件颜色（从根的子控件开始，避开根窗口自身）
        try:
            root_children = self.root.winfo_children()
        except Exception:
            root_children = []
        for child in root_children:
            self._update_widget_colors(child, theme, old_color_map)
        # 按钮经显式 token 精确还原（token 路径无歧义，不受反向映射碰撞影响）
        self._refresh_registered_button_styles(theme)
        # Text/Treeview 标签色与 Canvas 绘图项 fill 均不经
        # cget('background') 反查（tag_configure/itemconfig 无此入口），
        # 须按显式 token 重放，否则主题切换后残留旧色（v5.2 GUI-06）
        self._refresh_theme_tags(theme)
        self._refresh_theme_canvas_items(theme)
        # 更新可视化器主题（v4.0 §2.4 修复：set_theme 是 modules.visualizer 的模块级函数，
        # 旧代码误用「实例方法」调用 visualizer.set_theme → AttributeError 使主题切换崩溃。
        # 现导入模块级 _viz_set_theme，并加守卫：可视化器异常绝不阻断主题切换主流程。）
        try:
            _viz_set_theme(theme)
        except Exception as e:
            logger.warning('[GUI] 更新可视化器主题失败(不影响主题切换): %s', e)
        # v5.3 GUI-UX 第二批：重绘 Canvas 自绘主题图标（太阳/月亮），
        # 保证切主题后图标前景与画布底色同步，不残留旧主题色。
        self._sync_theme_icon()

    def _sync_theme_icon(self):
        """安全触发主题图标重绘（构建早期 _theme_btn 尚不存在时静默跳过）。"""
        draw = getattr(self, '_draw_theme_icon', None)
        if callable(draw):
            try:
                draw()
            except Exception as e:
                logger.debug('[GUI] 主题图标重绘失败: %s', e)

    def _build_old_color_map(self, old_theme: dict) -> dict:
        """构建「旧主题色值 → 语义槽键」反查表。

        委托 ui_styles.build_color_slot_map——与 token_for_color 共用同一张语义槽
        优先级表，避免「按钮 token 反查」与「控件色值反查」两套规则各自漂移。

        已知残余局限：对主题内重色的低频键（bg_panel、gradient_end 等）无法还原其
        真实语义槽，会落到优先级更高的同色键上（视觉上同为深/浅底，无可见差异）。
        """
        return ui_styles.build_color_slot_map(old_theme)

    def _refresh_registered_button_styles(self, theme: dict) -> None:
        """按 token 精确还原所有已注册按钮的配色（长任务期间禁用态除外）。

        禁用态由 _set_buttons_state 独占管理，主题切换不覆盖，避免任务运行中
        按钮「突然变亮」掩盖运行中不可操作的视觉提示。
        """
        targets = list(getattr(self, '_buttons', []) or [])
        copy_btn = getattr(self, '_result_copy_btn', None)
        if copy_btn is not None and copy_btn not in targets:
            targets.append(copy_btn)
        for btn in targets:
            try:
                if str(btn.cget('state')) == 'disabled':
                    continue
                self._apply_button_normal_style(btn)
            except Exception:
                continue

    def _register_theme_tags(
        self, widget, tag_props: Dict[str, Dict[str, str]]
    ) -> None:
        """登记 Text/Treeview 的「tag → 主题 token」映射，供切换时重放。

        tag_configure 设置的标签前景/背景色不经 cget('background')
        反查（无此入口），控件树刷新无法覆盖，主题切换后残留旧色；
        故创建时显式登记 token，切换时按新主题逐 tag 重放。

        参数:
            widget: Text/Treeview 等支持 tag_configure 的控件
            tag_props: {tag 名: {tag 选项: 主题 token}}，如
                {'error': {'foreground': 'accent_danger_light'}}
        """
        registrations = getattr(self, '_theme_tag_registrations', None)
        if registrations is None:
            registrations = []
            self._theme_tag_registrations = registrations
        registrations.append((widget, tag_props))

    def _refresh_theme_tags(self, theme: dict) -> None:
        """主题切换后重放所有已登记 tag 的 tag_configure（v5.2 GUI-06）。

        参数:
            theme: 新主题配色字典（COLORS 新值）
        """
        # list() 快照：重放过程中登记表不被遍历器持有，允许回调内再登记
        for widget, tag_props in list(
                getattr(self, '_theme_tag_registrations', []) or []):
            for tag, props in tag_props.items():
                for opt, token in props.items():
                    value = theme.get(token)
                    if not isinstance(value, str):
                        continue
                    try:
                        widget.tag_configure(tag, **{opt: value})
                    except Exception:
                        # 控件已销毁或 tag 已删：跳过，不阻断其余刷新
                        continue

    def _register_theme_canvas_item(
        self, canvas, item_id: int, token: str
    ) -> None:
        """登记 Canvas 绘图项（圆点/渐变条）的 fill 主题 token。

        Canvas 绘图项不经控件树遍历刷新（itemconfig 需 item id，
        无 cget 反查入口），主题切换后残留创建时旧色；故登记
        (canvas, item_id) → token，切换时重放 itemconfig。
        同一 (canvas, item_id) 重复登记时**更新** token，支持阶段
        圆点等「按状态动态变色后仍跟随主题」的场景。

        参数:
            canvas: Canvas 控件
            item_id: create_oval/create_rectangle 返回的绘图项 id
            token: 主题语义槽键（COLORS 键名）
        """
        items = getattr(self, '_theme_canvas_items', None)
        if items is None:
            items = []
            self._theme_canvas_items = items
        for entry in items:
            if entry[0] is canvas and entry[1] == item_id:
                entry[2] = token
                return
        items.append([canvas, item_id, token])

    def _refresh_theme_canvas_items(self, theme: dict) -> None:
        """主题切换后重放所有已登记 Canvas 绘图项的 fill 色（GUI-06）。

        参数:
            theme: 新主题配色字典（COLORS 新值）
        """
        for canvas, item_id, token in list(
                getattr(self, '_theme_canvas_items', []) or []):
            value = theme.get(token)
            if not isinstance(value, str):
                continue
            try:
                canvas.itemconfig(item_id, fill=value)
            except Exception:
                # 画布已销毁或绘图项已删：跳过，不阻断其余刷新
                continue

    def _update_widget_colors(self, widget, theme: dict, old_color_map: dict):
        """递归刷新控件配色：旧主题色值 → 语义槽 → 新主题值。

        参数:
            widget: 待刷新控件（递归起点）
            theme: 新主题配色字典
            old_color_map: _build_old_color_map(旧主题) 的结果，控件当前色按此反查语义槽
        """
        # 刷新当前控件
        try:
            # v5.2 GUI-06：可刷新属性清单 2 项扩至 10 项——
            # activebackground/activeforeground（按钮/菜单按压色）、
            # insertbackground（Text 插入光标）、highlightbackground/
            # highlightcolor（高亮边框）、selectbackground/
            # selectforeground（选中文本）、troughcolor（滚动槽）。
            # 此前不在清单内，主题切换后这些属性残留旧主题色。
            # 不支持该属性的控件（ttk 子集等）由下方 except 逐项跳过。
            for attr in ('background', 'foreground',
                         'activebackground', 'activeforeground',
                         'insertbackground', 'highlightbackground',
                         'highlightcolor', 'selectbackground',
                         'selectforeground', 'troughcolor'):
                try:
                    current = widget.cget(attr)
                except Exception:
                    continue
                if not isinstance(current, str):
                    continue
                key = old_color_map.get(current)
                if key is None:
                    continue
                new_value = theme.get(key)
                if isinstance(new_value, str) and new_value != current:
                    try:
                        widget.config(**{attr: new_value})
                    except Exception:
                        pass
        except Exception:
            pass
        # 递归处理子控件（单独 try：单个控件失败不中断整棵子树）
        try:
            children = widget.winfo_children()
        except Exception:
            return
        for child in children:
            self._update_widget_colors(child, theme, old_color_map)

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
                        # v5.3 GUI-UX 第二批：图标由 _apply_theme → _sync_theme_icon
                        # 统一重绘（浅色显示月亮、深色显示太阳），此处不再设置 emoji 文本。
        except Exception:
            pass

    # 回测 AI 辅助期数（v3.54 自动判断：不再由用户选择，始终使用固定值）
    _BACKTEST_AI_AUX_CAP = 10  # 最近 10 期：在 API 耗时与可见性之间平衡





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


def _enable_windows_dpi_awareness():
    """Windows 进程级 DPI 感知（A1 高 DPI 适配）。

    在创建 tk.Tk() 之前调用，使 Tk 以物理像素感知系统 DPI 缩放，
    避免 125%/150% 高分屏下界面模糊、控件错位。
    使用 shcore 优先（Win8.1+），失败降级 user32.SetProcessDPIAware。
    非 Windows / 任何失败均静默（不影响启动）。
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        # 先试 shcore（Win8.1+ 逐显示器感知），失败再兜底 user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass


def _apply_dpi_scaling(root):
    """按系统 DPI 校正 Tk 缩放因子（A1）。

    Tk 默认 1.0；Windows 高 DPI 下需按 `tk scaling` 比例放大，
    使字体/控件随物理像素变清晰。读取失败静默回退默认。
    为防止因多显示器或虚拟环境导致的异常 DPI 导致界面缩放错误，
    若检测到 dpi 极值（< 80 或 > 200）则跳过缩放调整。
    """
    try:
        dpi = root.winfo_fpixels('1i') if hasattr(root, 'winfo_fpixels') else 96.0
        # 畸值保护：虚拟/远程环境中常出现的异常 DPI 会导致界面缩放错乱
        if dpi and 80 <= dpi <= 200:
            factor = dpi / 96.0
            current = float(root.tk.call('tk', 'scaling')) or 1.0
            root.tk.call('tk', 'scaling', current * factor)
    except Exception:
        pass


def main():
    """启动排列5 AI智能分析系统GUI应用程序"""
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    _apply_dpi_scaling(root)
    _set_window_icon(root)
    app = LotteryGUI(root)
    # 强制刷新一次，确保窗口首帧渲染完成，避免白屏
    root.update_idletasks()

    # 冻结（PyInstaller）首次运行：config/.env 缺失时已生成空白模板，
    # 此处给出明确引导，避免「数据库未连接」在无控制台环境下静默无解释。
    # 源码运行 FIRST_RUN_ENV_CREATED 恒为 False，不会打扰开发者。
    if FIRST_RUN_ENV_CREATED:
        # 标题栏标记 + 日志双通道提示：即使模态弹窗在当前会话不可见，
        # 也不会出现「配置缺失却无任何用户可见提示」的静默失败
        try:
            root.title(f"{root.title()}  【请先配置 config/.env】")
        except Exception:
            logging.getLogger("__main__").exception("[首次运行] 标题栏提示失败")
        logging.getLogger("__main__").warning(
            "[首次运行] 已生成 config/.env 空白模板: %s，请填写 DB_PASSWORD 与 "
            "AGNES_API_KEY 后重启程序", ENV_FILE_PATH)
        try:
            messagebox.showwarning(
                "首次运行：请填写配置",
                "未找到配置文件，已生成空白模板：\n\n"
                f"{ENV_FILE_PATH}\n\n"
                "请填写数据库密码（DB_PASSWORD）与 AI 接口密钥（AGNES_API_KEY）"
                "后重启程序。\n未填写时界面可启动，但数据库与 AI 相关功能不可用。"
            )
        except Exception:
            # 窗口模式/受限桌面下弹窗可能不可用，记录异常而不是静默吞掉
            logging.getLogger("__main__").exception("[首次运行] 配置引导弹窗展示失败")

    def on_closing():
        """窗口关闭时优雅关闭线程池；有任务运行时先警示确认（v3.25）"""
        app._confirm_close()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    # v3.67: Windows 下 spawn 启动方式的标准做法——
    # 任何使用 multiprocessing 的模块都必须在 if __name__ 保护下调用 freeze_support()，
    # 否则 Windows 会为每个子进程重复导入并执行主模块（反复打印命令、重复弹出 GUI）。
    import multiprocessing
    multiprocessing.freeze_support()
    main()
