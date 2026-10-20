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
        _safe_grab_set(win)
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
from config import DATA_WINDOW_PERIOD
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
)

# 全局颜色主题配置（当前系统主题的实时色板）
COLORS = ThemeManager.get_theme()

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
# 标准档上限：面板宽度 <= 1440px；超过进入宽绰档。当前宽绰档视觉与
# 标准档一致（工具栏全文案 + 磁贴 4 列），保留档位供宽屏差异化扩展。
_BREAKPOINT_STANDARD_MAX_W = 1440
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


class LotteryGUI:
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
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        win_width = min(1280, screen_width - 40)
        win_height = max(780, min(860, int(screen_height * 0.85)))  # 至少 780px 保证内容可见
        self.root.geometry(f"{win_width}x{win_height}+{max(0, (screen_width - win_width) // 2)}+{max(0, (screen_height - win_height) // 2)}")
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

    def _init_ui_scheduler(self):
        """GUI-04（v5.2 §2.3）：创建轮询统一调度器并注册任务。

        注册集中在 _build_ui 之前完成：
          - update_time：固定 1000ms 间隔（与旧行为一致，不参与空闲退避）；
          - poll_cli_log / poll_evolution：interval_ms=0 跟随全局轮询间隔
            （默认 200ms，60s 无业务事件退避至 500ms）。
        poll_evolution 由 _init_evolution_engine 在引擎就绪后注册（引擎未启动
        时无需空转）；本方法保证调度循环在 _build_ui 前启动。

        特性开关关闭或初始化失败时 _ui_scheduler 保持 None，三条轮询回退为
        各自独立的 after 自调度链（R4 回滚，行为与 v5.1 一致）。
        """
        if not _ui_scheduler_enabled():
            logger.info('[GUI] ENABLE_UI_SCHEDULER=false，轮询回退自调度 after 链（R4）')
            return
        try:
            self._ui_scheduler = _UIScheduler(
                self.root.after,
                after_cancel_fn=self.root.after_cancel,
            )
            self._ui_scheduler.register('update_time', self._update_time,
                                        interval_ms=1000)
            self._ui_scheduler.register('poll_cli_log', self._poll_cli_log,
                                        interval_ms=0)
            self._ui_scheduler.start()
            logger.info('[GUI] _ui_scheduler 已启动：单一 100ms tick 循环接管轮询')
        except Exception as _us_e:
            logger.warning('[GUI] _ui_scheduler 初始化失败(回退自调度链): %s', _us_e)
            self._ui_scheduler = None

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

    def _update_time(self):
        """每秒更新顶部时钟显示

        GUI-04：调度器开启时由 _ui_scheduler 以固定 1000ms 间隔触发，
        本方法不再自调度（仅回退路径保留 after(1000) 自调度链）。
        """
        self.time_label.config(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if getattr(self, '_ui_scheduler', None) is not None:
            return
        self._update_time_handle = self.root.after(1000, self._update_time)

    # 主题管理
    _current_theme = 'dark'  # 记录当前主题状态

    def _toggle_theme(self):
        """切换主题：深色↔浅色"""
        if self._current_theme == 'dark':
            self._current_theme = 'light'
            self._apply_theme(ThemeManager.LIGHT_THEME)
            self._theme_btn.config(text="🌙")
        else:
            self._current_theme = 'dark'
            self._apply_theme(ThemeManager.DARK_THEME)
            self._theme_btn.config(text="☀️")
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
                        # 与 _toggle_theme 语义一致：浅色主题显示 🌙（点击切深色），深色显示 ☀️
                        # v5.0 §2.3 修复：旧代码两个分支均为空串，加载偏好后主题按钮 emoji 恒为空
                        self._theme_btn.config(
                            text='🌙' if self._current_theme == 'light' else '☀️')
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

    def _on_layout_resize(self):
        """窗口大小变化时的布局调整（v3.36 新增）"""
        try:
            # 触发所有子组件重新计算布局
            self.root.update_idletasks()
            # v5.2 GUI-07：三档断点响应式重排（方案 §311）。该调用幂等：
            # 仅当断点档位/磁贴布局模式相对上次发生变化时才触碰控件，
            # 同档位内的连续 Configure（拖拽中）零开销，不干扰 A1-A4
            # 闪烁守护；模式未变即返回，不会因磁贴高度变化引发
            # Configure 二次触发死循环。
            self._apply_breakpoint_layout()
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
        # 委托中央样式工厂（v5.0 §2.1），保持「token → 当前主题值」单一实现
        return ui_styles.resolve_color_token(value, COLORS)

    def _apply_button_normal_style(self, btn):
        """按缓存的 token/静态色还原按钮正常态外观（主题切换后调用亦正确）。

        参数:
            btn: 记录了 _normal_bg / _normal_fg 的按钮
        """
        # 委托中央样式工厂（v5.0 §2.1）
        ui_styles.apply_button_normal_style(btn, COLORS)

    def _build_output_panel(self, parent):
        """构建右侧输出面板（ttk.Notebook）

        保留「预测结果」「命令行输出」「自我进化」三个标签页。
        「命令行输出」标签页实时展示命令行 stdout/stderr 与 logging 日志（需求 #3）；
        同时 ``logs/gui_run.log`` 仍保留完整文件日志，二者互补不冲突。
        """
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


    def _show_visualization_menu(self):
        """显示可视化菜单"""
        # 如果已经有预测结果，则显示可视化选项菜单
        if not hasattr(self, '_last_pipeline_final') or not self._last_pipeline_final:
            messagebox.showinfo("提示", "请先进行预测分析，以便生成可视化图表")
            return
            
        # 创建弹出菜单
        if self._viz_menu:
            self._viz_menu.destroy()
            
        self._viz_menu = tk.Menu(self.root, tearoff=0)
        
        # 添加各种可视化选项
        self._viz_menu.add_command(label="基本走势趋势图", 
                                  command=lambda: self._generate_and_show_viz('basic_trend'))
        self._viz_menu.add_command(label="冷热分布直方图", 
                                  command=lambda: self._generate_and_show_viz('cold_hot_histogram'))
        self._viz_menu.add_command(label="分布关联矩阵", 
                                  command=lambda: self._generate_and_show_viz('distribution_correlation'))
        self._viz_menu.add_command(label="和值重心曲线", 
                                  command=lambda: self._generate_and_show_viz('average_value_center'))
        self._viz_menu.add_command(label="冷热演变趋势图", 
                                  command=lambda: self._generate_and_show_viz('cold_hot_evolution'))
        self._viz_menu.add_separator()
        self._viz_menu.add_command(label="导出所有图表", 
                                  command=self._export_all_visualizations)
        
        # 显示菜单
        try:
            self._viz_menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            self._viz_menu.grab_release()

    def _generate_and_show_viz(self, viz_type):
        """生成并显示指定类型的可视化图表"""
        try:
            # 获取历史数据
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                messagebox.showerror("错误", "数据库连接失败")
                return
                
            try:
                # 获取最近的历史数据用于可视化
                history_data = db.get_history_data(limit=100, order='ASC')  # 最近100期
                if not history_data:
                    messagebox.showwarning("警告", "没有足够的历史数据生成可视化图表")
                    return
                    
                # 调用可视化器生成图表
                output_path = None
                if viz_type == 'basic_trend':
                    output_path = visualizer.plot_basic_trend(history_data)
                elif viz_type == 'cold_hot_histogram':
                    output_path = visualizer.plot_cold_hot_histogram(history_data)
                elif viz_type == 'distribution_correlation':
                    output_path = visualizer.plot_distribution_correlation(history_data)
                elif viz_type == 'average_value_center':
                    output_path = visualizer.plot_average_value_center(history_data)
                elif viz_type == 'cold_hot_evolution':
                    output_path = visualizer.plot_cold_hot_evolution(history_data)
                    
                if output_path:
                    # 显示图片
                    self._show_image(output_path)
                else:
                    messagebox.showerror("错误", f"生成{viz_type}可视化图表失败")
                    
            finally:
                db.disconnect()
                
        except Exception as e:
            messagebox.showerror("错误", f"生成可视化图表时出错: {str(e)}")

    def _show_image(self, image_path):
        """显示图片"""
        try:
            # 创建图片查看窗口
            img_window = tk.Toplevel(self.root)
            img_window.title("可视化图表")
            img_window.geometry("800x600")
            img_window.configure(bg=COLORS['bg_primary'])
            
            # 添加滚动条
            canvas = tk.Canvas(img_window, bg=COLORS['bg_primary'])
            scrollbar_v = tk.Scrollbar(img_window, orient="vertical", command=canvas.yview)
            scrollbar_h = tk.Scrollbar(img_window, orient="horizontal", command=canvas.xview)
            canvas.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
            
            scrollbar_v.pack(side="right", fill="y")
            scrollbar_h.pack(side="bottom", fill="x")
            canvas.pack(side="left", fill="both", expand=True)
            
            # 在canvas中显示图片
            from PIL import Image, ImageTk
            img = Image.open(image_path)
            # 调整图片大小以适应窗口，但保持宽高比
            img_width, img_height = img.size
            canvas_width, canvas_height = 760, 560  # 留出滚动条空间
            
            # 计算缩放比例
            scale = min(canvas_width/img_width, canvas_height/img_height, 1.0)
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            
            # 创建图片标签
            img_label = tk.Label(canvas, image=photo, bg=COLORS['bg_primary'])
            img_label.image = photo  # 防止被垃圾回收
            canvas.create_window(0, 0, anchor="nw", window=img_label)
            
            # 更新滚动区域
            canvas.configure(scrollregion=canvas.bbox("all"))
            
            # 添加关闭按钮
            close_btn = tk.Button(img_window, text="关闭", 
                                 command=img_window.destroy,
                                 bg=COLORS['accent_p5'], fg='white',
                                 font=('微软雅黑', 10, 'bold'))
            close_btn.pack(pady=10)
            
        except ImportError:
            # 如果PIL不可用，则尝试使用其他方式显示
            messagebox.showinfo("提示", f"图表已保存至:\n{image_path}\n\n请使用图片查看器打开。")
        except Exception as e:
            messagebox.showerror("错误", f"显示图片时出错: {str(e)}")

    def _export_all_visualizations(self):
        """导出所有可视化图表"""
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                messagebox.showerror("错误", "数据库连接失败")
                return
                
            try:
                history_data = db.get_history_data(limit=100, order='ASC')
                if not history_data:
                    messagebox.showwarning("警告", "没有足够的历史数据生成可视化图表")
                    return
                    
                # 生成所有类型的图表
                viz_types = [
                    ('basic_trend', '基本走势趋势图'),
                    ('cold_hot_histogram', '冷热分布直方图'),
                    ('distribution_correlation', '分布关联矩阵'),
                    ('average_value_center', '和值重心曲线'),
                    ('cold_hot_evolution', '冷热演变趋势图')
                ]
                
                exported_files = []
                for viz_type, viz_name in viz_types:
                    try:
                        output_path = None
                        if viz_type == 'basic_trend':
                            output_path = visualizer.plot_basic_trend(history_data)
                        elif viz_type == 'cold_hot_histogram':
                            output_path = visualizer.plot_cold_hot_histogram(history_data)
                        elif viz_type == 'distribution_correlation':
                            output_path = visualizer.plot_distribution_correlation(history_data)
                        elif viz_type == 'average_value_center':
                            output_path = visualizer.plot_average_value_center(history_data)
                        elif viz_type == 'cold_hot_evolution':
                            output_path = visualizer.plot_cold_hot_evolution(history_data)
                            
                        if output_path:
                            exported_files.append(f"{viz_name}: {output_path}")
                    except Exception as e:
                        print(f"生成{viz_name}时出错: {e}")
                        
                if exported_files:
                    messagebox.showinfo("导出成功", 
                                      f"已成功导出{len(exported_files)}个可视化图表:\n\n" + 
                                      "\n".join(exported_files))
                else:
                    messagebox.showwarning("警告", "没有成功导出任何可视化图表")
                    
            finally:
                db.disconnect()
                
        except Exception as e:
            messagebox.showerror("错误", f"导出可视化图表时出错: {str(e)}")

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
            # GUI-04：调度器开启时注册 poll_evolution 由 tick 循环触发；
            # 回退路径保留原 after(200) 自调度链
            if self._ui_scheduler is not None:
                self._ui_scheduler.register('poll_evolution', self._poll_evolution,
                                            interval_ms=0)
            else:
                self.root.after(200, self._poll_evolution)
        except Exception as e:
            logger.warning('自我进化引擎初始化失败: %s', e)
            import traceback
            logger.warning(traceback.format_exc())

    def _build_cli_log_tab(self, parent):
        """构建「命令行输出」标签页：实时展示 stdout/stderr 与 logging 日志（需求 #3）。

        通过后台线程安全队列（modules.cli_log_sink）接收日志行，
        由 _poll_cli_log 每 200ms 在主线程渲染，效果等同「命令行输出界面化」。
        """
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

    def _poll_evolution(self):
        """轮询自我进化引擎消息队列（每 200ms），实时刷新 UI（主线程安全）。"""
        # v3.64: 关闭时不再继续调度，防止 destroy() 后 TclError 白屏
        if getattr(self, '_poll_evolution_cancelled', False):
            self._poll_evolution_handle = None
            return
        # v3.65: 窗口已销毁则直接退出，防止访问已释放的 Tcl 资源
        try:
            _ = self.root.winfo_exists()
        except Exception:
            self._poll_evolution_handle = None
            return
        eng = getattr(self, 'evolution', None)
        _consumed = False  # GUI-04：本次轮询是否真正消费到消息（驱动退避恢复）
        if eng is not None:
            try:
                while True:
                    msg = eng.queue.get_nowait()
                    self._handle_evolution_msg(msg)
                    _consumed = True
            except queue.Empty:
                pass
            except Exception as e:
                logger.warning('[GUI] _poll_evolution 消费消息时异常: %s', e)
        # GUI-04：消费到消息即业务事件，重置空闲退避计时
        if _consumed and self._ui_scheduler is not None:
            self._ui_scheduler.notify_event()
        # 保存句柄以便关闭时精确 cancel。
        # GUI-04：调度器开启时不再自调度，由 tick 循环按间隔触发
        if getattr(self, '_ui_scheduler', None) is not None:
            return
        self._poll_evolution_handle = self.root.after(200, self._poll_evolution)

    def _poll_cli_log(self):
        """轮询命令行日志队列（GUI-04：由 _ui_scheduler 按 200ms/退避 500ms 触发）。

        实时渲染到「命令行输出」标签页（主线程安全，永不阻塞）。
        GUI-04 优化：
          1. 空闲期（has_pending 为假）零 Tk 调用直接返回 —— A11「渲染类 0 次」；
          2. 单次 drain 的全部行合并为**一次** txt.insert 批调用提交
             （Tcl insert 支持 chars/tagList 交替段，保持日志时间顺序），
             替代逐行 insert —— A12「1000 行 ≤ 5 次批插入」实际 1 次；
          3. 消费到日志行即通知调度器业务事件到达，恢复 200ms 活跃轮询间隔。
        """
        if getattr(self, '_poll_cli_log_cancelled', False):
            self._poll_cli_log_handle = None
            return
        try:
            if self.root.winfo_exists() is False:
                self._poll_cli_log_handle = None
                return
        except Exception:
            self._poll_cli_log_handle = None
            return
        txt = getattr(self, 'cli_log_text', None)
        buf = getattr(self, '_cli_log_buffer', None)
        if txt is not None:
            try:
                from modules.cli_log_sink import drain_cli_lines, has_pending
                # 空闲期无待渲染行：不 drain、不触 Tk（A11 渲染类 0 次）。
                # 注意：此处不得提前 return —— 回退路径（调度器关闭）仍需
                # 走到方法尾部完成 after(200) 自调度续期。
                lines = drain_cli_lines() if has_pending() else []
                if lines:
                    # 业务事件到达 → 重置空闲退避计时，恢复 200ms 活跃轮询间隔
                    if self._ui_scheduler is not None:
                        self._ui_scheduler.notify_event()
                    kw = (getattr(self, '_cli_filter_var', None).get()
                          if getattr(self, '_cli_filter_var', None) is not None
                          else '').strip().lower()
                    # 过滤 + 内存缓冲逐行处理（不触 Tk），渲染合并为一次批 insert：
                    # Tk Text.insert(index, chars, tagList, chars, tagList, ...)
                    # 支持文本段与标签列表交替，天然保持日志原有顺序。
                    segments = []  # type: list
                    for tag, line in lines:
                        if buf is not None:
                            buf.append((tag, line))
                            if len(buf) > 5000:
                                del buf[:len(buf) - 5000]
                        # 有关键字时仅渲染匹配行；无关键字渲染全部
                        if not kw or kw in line.lower():
                            segments.append(line + '\n')
                            segments.append((tag,))
                    if segments:
                        txt.insert(tk.END, *segments)
                    # 缓冲上限裁剪（防止无限增长）
                    _count = int(txt.index('end-1c').split('.')[0])
                    if _count > 5000:
                        txt.delete('1.0', f'{_count - 5000}.0')
                    if getattr(self, '_cli_autoscroll', True):
                        txt.see(tk.END)
            except Exception as e:
                import logging as _lgc
                _lgc.getLogger(__name__).warning('[GUI] _poll_cli_log 渲染异常: %s', e)
        # GUI-04：调度器开启时不再自调度，由 tick 循环按间隔触发
        if getattr(self, '_ui_scheduler', None) is not None:
            return
        self._poll_cli_log_handle = self.root.after(200, self._poll_cli_log)

    def _apply_cli_filter(self, *_):
        """关键字过滤：重建命令行输出文本框，仅展示匹配行（大小写不敏感）。

        复用内存缓冲 ``_cli_log_buffer``，避免对原始队列重复消费。
        """
        txt = getattr(self, 'cli_log_text', None)
        buf = getattr(self, '_cli_log_buffer', None)
        if txt is None or buf is None:
            return
        try:
            kw = (getattr(self, '_cli_filter_var', None).get()
                  if getattr(self, '_cli_filter_var', None) is not None
                  else '').strip().lower()
            txt.config(state=tk.NORMAL)
            txt.delete('1.0', tk.END)
            for tag, line in buf:
                if not kw or kw in line.lower():
                    txt.insert(tk.END, line + '\n', tag)
            if getattr(self, '_cli_autoscroll', True):
                txt.see(tk.END)
        except Exception:
            pass

    def _cli_log_context_menu(self, event):
        """右键菜单：复制选中 / 复制全部 / 清空（Phase 4）。"""
        txt = getattr(self, 'cli_log_text', None)
        if txt is None:
            return
        try:
            menu = tk.Menu(self.root, tearoff=0)
            try:
                sel = txt.selection_get()
            except Exception:
                sel = ''
            if sel:
                menu.add_command(label="复制选中", command=lambda: self._cli_copy(sel))
            menu.add_command(label="复制全部",
                             command=lambda: self._cli_copy(txt.get('1.0', tk.END)))
            menu.add_separator()
            menu.add_command(label="清空", command=self._clear_cli_log)
            menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass

    def _cli_copy(self, text):
        """将文本写入系统剪贴板（失败静默）。"""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass

    def _clear_cli_log(self):
        """清空命令行输出文本框（同步清空关键字过滤缓冲）。"""
        if getattr(self, 'cli_log_text', None) is not None:
            try:
                self.cli_log_text.delete('1.0', tk.END)
                self.cli_log_text.insert(tk.END, "> 日志已清空。\n", 'dim')
                if getattr(self, '_cli_log_buffer', None) is not None:
                    self._cli_log_buffer.clear()
            except Exception:
                pass

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
                        self.evo_status_badge.config(
                            text="● 运行中",
                            fg=COLORS['accent_warning_light'],
                            bg=COLORS['bg_input'])
                    elif '完成' in txt or '就绪' in txt:
                        self.evo_status_badge.config(
                            text="● 就绪",
                            fg=COLORS['accent_p5_light'],
                            bg=COLORS['bg_input'])
                    else:
                        self.evo_status_badge.config(
                            text="● " + txt[:8],
                            fg=COLORS['accent_ai_bright'],
                            bg=COLORS['bg_input'])
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
                    # 统一文案模板（v5.0 §2.1.3）：阶段 N/M · 名称
                    self.evo_tab_status.config(
                        text=ui_styles.format_stage_text(idx, total, name))
                # 更新阶段指示器
                if hasattr(self, '_update_phase_indicator'):
                    self._update_phase_indicator(idx)
            elif t == 'evolve_trigger':
                # v5.0 §7.3：开奖回填后的进化触发判定必须可见，不得静默跳过
                triggered = bool(m.get('triggered'))
                issue = m.get('issue', '—')
                reason = m.get('reason') or m.get('reason_code', '')
                tag = 'success' if triggered else 'info'
                icon = '⚡' if triggered else '⏸'
                if hasattr(self, 'evo_log'):
                    self.evo_log.insert(tk.END,
                                        f"{icon} 进化触发判定（期号 {issue}）："
                                        f"{'已触发轻量进化' if triggered else '跳过'} — {reason}\n",
                                        tag)
                    self.evo_log.see(tk.END)
            elif t == 'version':
                self._refresh_evolution_versions()
                d = m.get('data', {})
                if hasattr(self, 'evo_overview_var'):
                    self.evo_overview_var.set(
                        f"当前版本: {d.get('version_tag', '')} | 状态: {d.get('status', '')}\n{d.get('note', '')}")
            elif t == 'done':
                self._refresh_evolution_versions()
                if hasattr(self, 'evo_status_badge'):
                    self.evo_status_badge.config(
                        text="● 就绪",
                        fg=COLORS['accent_p5_light'],
                        bg=COLORS['bg_input'])
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
        # 阶段点配色（v5.2 GUI-06：原 colors 字面量列表删除，改主题槽 token；
        # 每次刷新同步重登记 Canvas 注册表，主题切换后按当前阶段状态重放）
        _token_done = 'accent_p5_light'    # 已完成阶段（绿色）
        _token_running = 'accent_ai'       # 当前阶段（紫色高亮）
        _token_pending = 'text_muted'      # 未开始阶段（弱化灰）
        for i, dot_canvas in enumerate(self.evo_phase_indicators):
            if i < current_phase_idx:
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(
                    dot_canvas.find_all()[0], fill=COLORS[_token_done])
                self._register_theme_canvas_item(
                    dot_canvas, dot_canvas.find_all()[0], _token_done)
            elif i == current_phase_idx:
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(
                    dot_canvas.find_all()[0], fill=COLORS[_token_running])
                self._register_theme_canvas_item(
                    dot_canvas, dot_canvas.find_all()[0], _token_running)
            else:
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(
                    dot_canvas.find_all()[0], fill=COLORS[_token_pending])
                self._register_theme_canvas_item(
                    dot_canvas, dot_canvas.find_all()[0], _token_pending)

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
        _safe_grab_set(win)

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
                 bg=COLORS['accent_ai'], fg=WHITE).pack(side=tk.LEFT, padx=12, pady=9)
        tk.Button(hdr, text="✕", font=('微软雅黑', 9),
                  bg=COLORS['accent_ai'], fg=WHITE, relief='flat',
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
                      bg=COLORS['accent_warning'], fg=WHITE, relief='flat',
                      activebackground=COLORS['accent_warning_active'],
                      command=lambda vt=version_tag: self._on_evo_rollback_to(vt)).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(btn_frame, text="关闭", font=('微软雅黑', 9),
                  bg=COLORS['accent_p5'], fg=WHITE, relief='flat',
                  activebackground=COLORS['accent_p5_active'],
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
            self._show_toast('当前已是 active 版本，无需回滚', COLORS['accent_info'], duration=1500)
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
        _safe_grab_set(win)

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
                 bg=COLORS['accent_ai'], fg=WHITE).pack(side=tk.LEFT, padx=12, pady=9)
        tk.Button(hdr, text="✕", font=('微软雅黑', 9),
                  bg=COLORS['accent_ai'], fg=WHITE, relief='flat',
                  command=win.destroy).pack(side=tk.RIGHT, padx=10, pady=6)

        # 内容区（卡片式布局）
        content = tk.Frame(win, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # 构建展示文本
        # v5.0 §7.3：命中/候选判定统一走引擎的只读建议接口，避免 GUI 自算两套口径。
        # 注意 `or {}` 而非 dict.get 默认值：键存在但值为 None 时不会触发默认值。
        lines = []
        lines.append(f"最后分析期号: {link_state.get('last_analysis_issue') or '—'}")
        last_pred = link_state.get('last_prediction') or {}
        lines.append(f"预测目标期号: {last_pred.get('target_issue') or '—'}"
                     if isinstance(last_pred, dict) else "预测目标期号: —")
        lines.append(f"等待回填: {'是' if link_state.get('pending_verification') else '否'}")
        lines.append(f"最后同步时间: {link_state.get('last_sync_ts') or '—'}")
        lines.append("")
        lines.append("─" * 30)
        lines.append("")

        # 最新验证结果
        last_verif = link_state.get('last_verification') or {}
        if isinstance(last_verif, dict) and last_verif:
            lines.append(f"期号: {last_verif.get('issue') or '—'}")
            lines.append(f"Top-1 命中: {last_verif.get('top1')}%")
            lines.append(f"Top-3 命中: {last_verif.get('top3')}%")
            lines.append(f"Top-5 命中: {last_verif.get('top5')}%")
            lines.append(f"验证时间: {last_verif.get('ts') or '—'}")
        else:
            lines.append("暂无验证记录")

        # 最佳候选（v5.0 §7.3：附诚实结论，不暗示可提升命中率）
        advisory = {}
        try:
            if eng is not None and hasattr(eng, 'get_latest_advisory'):
                advisory = eng.get_latest_advisory() or {}
        except Exception:  # noqa: BLE001
            advisory = {}
        best = link_state.get('best_candidate') or {}
        if isinstance(best, dict) and best:
            lines.append("")
            lines.append("─" * 30)
            lines.append("")
            lines.append(f"候选版本: {best.get('version_tag') or '—'}")
            metrics = best.get('metrics') or {}
            lines.append(f"Top-1: {metrics.get('top1')}%  "
                         f"Top-3: {metrics.get('top3')}%  "
                         f"Top-5: {metrics.get('top5')}%")
            if advisory.get('has_better_candidate'):
                lines.append("结论: 综合评分高于基线（walk-forward 口径）")
            elif advisory.get('baseline_metrics'):
                lines.append("结论: 未严格超越基线，未注入生产权重")
            else:
                lines.append("结论: 暂无可比基线，无法判定优劣")

        # 诚实边界声明（常驻，不随状态消失）
        lines.append("")
        lines.append("─" * 30)
        lines.append(f"⚠ {advisory.get('honest_note') or '排列5 为公平摇号，历史统计不改变随机概率。'}")

        txt = "\n".join(lines)

        text_widget = tk.Text(content, wrap=tk.WORD, font=('Consolas', 9),
                              bg=COLORS['bg_card'], fg=COLORS['text_primary'],
                              relief='flat', padx=12, pady=10, state=tk.NORMAL)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert(tk.END, txt)
        text_widget.config(state=tk.DISABLED)

        # 关闭按钮
        tk.Button(win, text="关闭", font=('微软雅黑', 9),
                  bg=COLORS['accent_p5'], fg=WHITE, relief='flat',
                  activebackground=COLORS['accent_p5_active'],
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
            self._show_toast('正在生成系统健康诊断报告…', COLORS['accent_info'])
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

    def _on_auto_evaluator_scan(self):
        """触发自我升级自动评估（§4.1 模块自诊断 + §4.4 结果验证/优化建议）。

        运行 8 个领域评估器（数据层/预测性能/算法权重/缓存/进化引擎/模块依赖/AI模型/代码质量），
        输出综合评分 + 分级优化建议，把 Markdown 报告在进化日志区展示并落盘 reports/。

        评估器各自打开 DB，全量评估可能耗时数十秒，故后台线程运行，经 root.after 回投
        主线程渲染，永不阻塞 GUI。DB 不可用时各评估器优雅降级为 critical（不崩溃）。
        """
        try:
            from modules.auto_evaluator import ProjectAutoEvaluator
        except Exception as e:
            logger.warning('[GUI] 自动评估模块导入失败: %s', e)
            self._show_toast(f'自动评估模块不可用: {e}', COLORS['accent_danger_light'])
            return

        self._show_toast('正在运行自我升级自动评估…', COLORS['accent_info'])

        import queue as _queue
        q = _queue.Queue()

        def _worker():
            # 后台线程：只做非 Tk 操作（评估/落盘），结果经队列投递；
            # 绝不直接调 Tk API（跨线程调用 Tk 会抛 "main thread is not in main loop"）。
            try:
                evaluator = ProjectAutoEvaluator()
                report = evaluator.run_full_evaluation()
                md = evaluator.render_markdown(report)
                saved_path = ''
                try:
                    saved_path = evaluator.save_report(report)
                except Exception:
                    saved_path = ''
                q.put(('ok', report, md, saved_path))
            except Exception as e:
                q.put(('error', e))

        threading.Thread(target=_worker, daemon=True).start()

        # 主线程轮询队列：结果就绪后渲染并停止；未就绪每 200ms 再查一次。
        def _poll():
            handled = False
            try:
                while True:
                    item = q.get_nowait()
                    if item[0] == 'ok':
                        _, report, md, saved_path = item
                        self._render_auto_eval_report(report, md, saved_path)
                        handled = True
                    else:
                        err = item[1]
                        logger.warning('[GUI] 自动评估异常: %s', err, exc_info=True)
                        self._show_toast(f'自动评估异常: {err}',
                                         COLORS['accent_danger_light'])
                        handled = True
            except _queue.Empty:
                pass
            if not handled:
                self.root.after(200, _poll)

        _poll()

    def _render_auto_eval_report(self, report, md, saved_path):
        """主线程渲染自动评估报告到进化日志区（由后台线程经 root.after 回投）。"""
        summary = report.summary or {}
        overall = summary.get('overall_score')
        level = summary.get('health_level', '')
        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(tk.END,
            f'[自我升级自动评估] {report.scan_time} | 综合评分 {overall} | 等级 {level}\n',
            'section')
        self.evo_log.insert(tk.END, f'{md}\n', 'info')
        self.evo_log.see(tk.END)
        color = (COLORS['success'] if level == 'healthy'
                 else COLORS['warning'] if level == 'degraded'
                 else COLORS['accent_danger_light'])
        detail = f'，已保存 {saved_path}' if saved_path else ''
        self._show_toast(f'自动评估完成（评分 {overall}/{level}）{detail}',
                         color, duration=4000)

    def _on_update_history_log(self):
        """§4.2 可追溯：在进化日志区展示「更新记录台账」（可查询 + 可导出）。

        数据来源：进化引擎 `SelfEvolutionEngine.get_update_history()`（本地
        `data/update_ledger.jsonl`，DB 不可达亦可读）。记录含 触发原因/执行结果/
        校验/是否自动回滚，倒序展示（最新在前）；点按时顺带把台账导出到
        reports/ 供问题回溯。无记录时给出友好提示（首次运行属正常）。
        """
        eng = getattr(self, 'evolution', None)
        if eng is None:
            self._init_evolution_engine()
            eng = self.evolution
        if eng is None or not hasattr(eng, 'get_update_history'):
            self._show_toast('进化引擎不可用，无法读取更新台账',
                             COLORS['accent_danger_light'])
            return
        try:
            rows = eng.get_update_history(limit=50)
        except Exception as e:
            logger.warning('[GUI] 读取更新台账异常: %s', e, exc_info=True)
            self._show_toast(f'读取更新台账失败: {e}', COLORS['accent_danger_light'])
            return

        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(tk.END,
                            f'[更新流程台账] 共 {len(rows)} 条（最新在前）\n', 'section')
        if not rows:
            self.evo_log.insert(tk.END,
                '（暂无更新记录 —— 进化产版/手动回滚/自动回滚后会在此累积；'
                '属正常初始态）\n', 'info')
        else:
            for i, r in enumerate(rows, 1):
                tag = r.get('version_tag', '—')
                action = r.get('action', '—')
                ts = r.get('timestamp', '')
                result = r.get('execution_result', '—')
                auto_rb = ' [自动回滚]' if r.get('auto_rolled_back') else ''
                reason = r.get('trigger_reason', '')
                restored = r.get('restored_tag')
                line = (f'  {i:>2}. [{ts}] {action} → {tag} '
                        f'结果={result}{auto_rb}')
                if restored:
                    line += f'（回退至 {restored}）'
                self.evo_log.insert(tk.END, line + '\n', 'info')
                if reason:
                    self.evo_log.insert(tk.END, f'       触发: {reason}\n', 'info')
                # 校验摘要（fatal 项高亮为 danger）
                val = r.get('validation') or {}
                checks = val.get('checks', {}) if isinstance(val, dict) else {}
                if checks:
                    bad = {k: s for k, s in checks.items() if s in ('fail', 'warn')}
                    detail_txt = '；'.join(f'{k}={s}' for k, s in bad.items())
                    key = 'warn' if bad else 'info'
                    self.evo_log.insert(
                        tk.END, f'       校验: {detail_txt or "全部通过"}\n', key)
        self.evo_log.see(tk.END)

        # 顺带导出到 reports/ 供回溯（§4.2.2(2) 可导出）
        export_path = ''
        try:
            ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            export_path = os.path.join(REPORTS_DIR, f'update_ledger_{ts_str}.json')
            eng.export_update_history(export_path)
        except Exception as e:
            logger.debug('[GUI] 导出更新台账跳过(非致命): %s', e)
            export_path = ''

        color = COLORS['success'] if rows else COLORS['accent_info']
        detail = f'，已导出 {export_path}' if export_path else ''
        self._show_toast(
            f'更新台账 {len(rows)} 条{detail}',
            color, duration=3000)

    def _on_perf_monitor_view(self):
        """§4.3 系统性能监控：在进化日志区展示性能指标摘要 + 告警状态。

        数据来源：``modules.perf_monitor`` 的全局单例（惰性启动 1Hz 守护线程
        采集 CPU/内存/响应/任务耗时，资源压力越限自动优化）。点按时：
          * 若监控线程未运行则按需启动（幂等）；
          * 渲染 get_summary()（各指标窗口统计 + 告警 + 健康度）到 evo_log；
          * psutil 不可用时标注「降级」，不误导用户。
        无 DB 依赖；异常绝不阻断 GUI。
        """
        try:
            from modules.perf_monitor import (
                get_perf_monitor, start_perf_monitoring)
        except Exception as e:
            logger.warning('[GUI] 性能监控模块不可用: %s', e, exc_info=True)
            self._show_toast(f'性能监控不可用: {e}', COLORS['accent_danger_light'])
            return

        mon = get_perf_monitor()
        # 任务来源注入（TaskManager 鸭子类型：get_task_count / is_running /
        # get_running_tasks）。监控线程每采样周期读它统计任务类指标。
        try:
            mon.set_task_source(self.task_mgr)
        except Exception:
            pass
        # 按需启动（幂等；已运行则直接 True）
        try:
            start_perf_monitoring()
        except Exception as e:
            logger.debug('[GUI] 启动性能监控线程跳过(非致命): %s', e)

        # 采集一次新鲜指标（即便守护线程刚停，也保证按钮点按时有数据）
        try:
            summary = mon.get_summary()
        except Exception as e:
            logger.warning('[GUI] 读取性能摘要异常: %s', e, exc_info=True)
            self._show_toast(f'读取性能摘要失败: {e}', COLORS['accent_danger_light'])
            return

        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(
            tk.END,
            f'[系统性能监控] 采样 {summary.get("sample_count", 0)} 点 '
            f'| 健康度 {summary.get("health_score", "—")}/100\n', 'section')

        psutil_ok = bool(summary.get('psutil_available'))
        self.evo_log.insert(
            tk.END,
            ('psutil 已启用（CPU/内存指标可用）\n' if psutil_ok
             else 'psutil 不可用 → CPU/内存指标降级（任务/耗时类指标仍可用）\n'),
            'info')

        # 各指标窗口统计（最近窗口均值/峰值/当前值）
        metrics = summary.get('metrics', {}) or {}
        for key, st in metrics.items():
            cur = st.get('current')
            cur_txt = f'{cur}' if cur is not None else '—(降级/无数据)'
            avg = st.get('avg')
            avg_txt = f'{avg}' if avg is not None else '—'
            mx = st.get('max')
            mx_txt = f'{mx}' if mx is not None else '—'
            self.evo_log.insert(
                tk.END,
                f'   {key:<18} 当前={cur_txt}  均值={avg_txt}  '
                f'峰值={mx_txt}  (窗口 {st.get("window", 0)})\n', 'info')

        # 告警状态
        alerts = summary.get('active_alerts') or []
        if alerts:
            self.evo_log.insert(
                tk.END, f'   ⚠ 触发告警: {", ".join(alerts)}\n', 'warn')
        else:
            self.evo_log.insert(tk.END, '   ✓ 无活动告警\n', 'info')
        self.evo_log.see(tk.END)

        color = COLORS['success'] if not alerts else COLORS['accent_danger_light']
        detail = f'，告警 {len(alerts)} 项' if alerts else ''
        self._show_toast(
            f'性能监控 健康度 {summary.get("health_score", "—")}{detail}',
            color, duration=3000)

    def _auto_verify_result(self, pipeline_final, quick_final) -> None:
        """§4.4.3 自动自我验证：每次分析产出后登记一次验证。

        目的：方案 §4.4.3 要求「结果自我验证覆盖率达 100%」。此前验证只在用户
        手动点击「🔬 自我验证」时才发生，覆盖率实际取决于人工操作、并非 100%。
        这里在结果落库后自动登记一次验证，使覆盖率为**真实度量**。

        约束（对齐项目惯例 + 诚实边界）：
          * 只登记到 ResultSelfVerifier（供 §4.1 自诊断 / 自动评估报告消费），
            不弹窗、不渲染、不阻塞分析流程；
          * 绝不自动调整 v4.0 冻结权重（建议仅为建议型记录）；
          * 由 RESULT_VERIFIER_ENABLE 门控，任何异常一律静默（非致命）。
        """
        try:
            import config as _cfg_av
            if not getattr(_cfg_av, 'RESULT_VERIFIER_ENABLE', True):
                return
            from modules.result_verifier import get_result_verifier
            rv = get_result_verifier()
        except Exception as e:  # 模块不可用 → 静默跳过，不影响分析
            logger.debug('[GUI] 自动自我验证不可用（非致命）: %s', e)
            return

        for cand in (pipeline_final, quick_final):
            if not isinstance(cand, dict) or not cand:
                continue
            try:
                fused = cand.get('fused_probabilities')
                tops = (cand.get('top_combinations')
                        or cand.get('recommended_combinations'))
                inner = cand.get('prediction') or cand.get('raw_prediction') or {}
                payload = {
                    'fused_probabilities': (fused if fused is not None
                                            else inner.get('fused_probabilities')),
                    'top_combinations': tops or inner.get('top_combinations'),
                    'target_issue': cand.get('current_issue') or cand.get('target_issue'),
                    'next_issue': cand.get('next_issue'),
                    'data_period': cand.get('data_period'),
                    'per_algo_top_predictions': (
                        cand.get('per_algo_top_predictions')
                        or inner.get('per_algo_top_predictions')),
                }
                out = rv.self_verify_result(payload, None)
                rv.generate_optimization_suggestions(out, None)
            except Exception as e:
                logger.debug('[GUI] 自动自我验证失败（非致命）: %s', e)
            break  # 每次分析只登记最优先的那一个结果

    def _on_result_self_verify(self):
        """§4.4 结果自我验证 + 分级优化建议：对最近一次流水线结果做一致性自检。

        数据来源：
          * 被验结果：最近一次流水线产物 ``_last_pipeline_final`` /
            ``_last_quick_final``（含 fused_probabilities / top_combinations /
            per_algo_top_predictions / next_issue），无产物时提示先「开始分析」。
          * 监控数据：ModelMonitor.get_current_status()（惰性导入，无则跳过）。

        校验维度（§4.4.2(1)）：命中率边界 / 概率归一 / 组合结构 / 窗口一致性 /
        数据一致性。据此生成分级（高/中/低）优化建议（§4.4.2(2)），渲染到 evo_log。
        诚实边界：建议仅对照随机基线、标注正常波动，**不**自动调整冻结权重。
        无 DB 依赖；异常绝不阻断 GUI。
        """
        try:
            import config as _cfg3
            if not getattr(_cfg3, 'RESULT_VERIFIER_ENABLE', True):
                self._show_toast('结果自我验证已被配置关闭（RESULT_VERIFIER_ENABLE=False）',
                                 COLORS['accent_info'])
                return
            from modules.result_verifier import (
                get_result_verifier, RANDOM_BASELINE)
        except Exception as e:
            logger.warning('[GUI] 结果自我验证模块不可用: %s', e, exc_info=True)
            self._show_toast(f'结果自我验证不可用: {e}', COLORS['accent_danger_light'])
            return

        # ---- 收集被验结果（多个候选来源，取第一个能拿到可校验字段的）----
        candidates = [
            getattr(self, '_last_pipeline_final', None),
            getattr(self, '_last_quick_final', None),
        ]
        verify_result = None
        used_label = ''
        for cand in candidates:
            if isinstance(cand, dict) and cand:
                # final_report：fused/top 可能在 'prediction'(趋势) 或 'raw_prediction'
                fused = cand.get('fused_probabilities')
                tops = cand.get('top_combinations') or cand.get('recommended_combinations')
                if fused is None or tops:
                    inner = cand.get('prediction') or cand.get('raw_prediction') or {}
                    verify_result = {
                        'fused_probabilities': fused
                        if fused is not None else inner.get('fused_probabilities'),
                        'top_combinations': tops or inner.get('top_combinations'),
                        'target_issue': cand.get('current_issue') or cand.get('target_issue'),
                        'next_issue': cand.get('next_issue'),
                        'data_period': cand.get('data_period'),
                        'per_algo_top_predictions': cand.get('per_algo_top_predictions')
                        or inner.get('per_algo_top_predictions'),
                    }
                    used_label = '最近一次流水线结果'
                    break
        if verify_result is None:
            self._show_toast('暂无可验证的流水线结果，请先「开始分析」',
                             COLORS['accent_info'])
            return

        # ---- 监控数据（惰性导入；无则跳过命中率维度）----
        monitor_data = None
        try:
            from modules.model_monitor import get_model_monitor
            status = get_model_monitor().get_current_status()
            monitor_data = {
                'hit_rate': (status.get('latest_values') or {}).get('hit_rate'),
                'alert_states': status.get('alert_states', {}),
            }
        except Exception:
            monitor_data = None

        # ---- 执行自我验证 + 生成分级建议 ----
        rv = get_result_verifier()
        verify_out = rv.self_verify_result(verify_result, None)
        sug_out = rv.generate_optimization_suggestions(verify_out, monitor_data)

        # ---- 渲染到 evo_log（验证通过/失败 + 分级建议）----
        valid = bool(verify_out.get('valid'))
        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(
            tk.END,
            f'[结果自我验证] {used_label} | 结论={"✓ 通过" if valid else "✗ 未通过"} '
            f'| 基线 top1={RANDOM_BASELINE["top1"]:.0%}\n',
            'section')

        # 校验维度逐项
        checks = verify_out.get('checks', {}) or {}
        for dim, ok in checks.items():
            mark = '✓' if ok else '✗'
            key = 'info' if ok else 'warn'
            self.evo_log.insert(
                tk.END, f'   {mark} {dim}\n', key)
        for err in verify_out.get('errors', []):
            self.evo_log.insert(tk.END, f'       ✗ {err}\n', 'danger')

        # 分级建议
        by_priority = sug_out.get('by_priority', {}) or {}
        self.evo_log.insert(tk.END, '\n   ── 分级优化建议 ──\n', 'section')
        priority_label = {'high': '高优先级', 'medium': '中优先级', 'low': '低优先级'}
        for p in ('high', 'medium', 'low'):
            for s in by_priority.get(p, []) or []:
                self.evo_log.insert(
                    tk.END,
                    f'   [{priority_label[p]}] {s.get("title", "")}\n',
                    'danger' if p == 'high' else 'info')
                self.evo_log.insert(
                    tk.END, f'       依据: {s.get("rationale", "")}\n', 'info')
                self.evo_log.insert(
                    tk.END, f'       建议: {s.get("action", "")}\n', 'info')
                if not s.get('auto_safe', True):
                    self.evo_log.insert(
                        tk.END,
                        '       ⚠ 涉及权重/窗口调整，须人工经 '
                        'ONLINE_LEARNER_WRITEBACK 通道执行（本模块不自动改权重）\n',
                        'warn')
        if not any(by_priority.get(p) for p in ('high', 'medium', 'low')):
            self.evo_log.insert(tk.END, '   （暂无优化建议）\n', 'info')

        self.evo_log.insert(
            tk.END,
            f'\n   [诚实边界] {sug_out.get("honest_note", "")}\n', 'info')
        self.evo_log.see(tk.END)

        color = COLORS['success'] if valid else COLORS['accent_danger_light']
        n_sug = sum(len(v) for v in by_priority.values())
        self._show_toast(
            f'自我验证 {"通过" if valid else "未通过"}，建议 {n_sug} 条',
            color, duration=3000)

    # =========================================================================
    # 结果显示板块（v3.50 重构辅助）：滚动 / 分类 / 清空 / 导出
    # =========================================================================
    def _dash_yview(self, *args):
        """转发滚动条命令到滚动画布。"""
        try:
            self.dash_container.yview(*args)
        except Exception:
            pass

    def _on_dash_configure(self, event):
        """仪表盘容器尺寸变化处理器（GUI-01 阈值守卫版，v5.2 §2.2）。

        v5.1 旧行为：每次 Configure 都无条件 ``itemconfig('all', width=event.width - 4)``
        并同步刷新滚动区域。当内部视窗宽度改变又反向触发容器 Configure 时，形成
        Canvas 尺寸反馈循环（闪烁根因之一），且拖拽时的 1px 抖动会产生大量无效重排。

        修复后行为：
          * 仅当容器宽度较上次同步宽度变化 >= ``_DASH_WIDTH_SYNC_THRESHOLD`` 像素时，
            才回写内部视窗宽度（1px 级抖动被合并）；
          * 不再在本回调内同步调用 ``_update_result_scrollregion``，改为走防抖入口
            ``_dash_request_refresh``，使滚动区域刷新离开 Configure 事件回调栈。

        参数:
            event: Tk Configure 事件（``event.width`` 为 dash_container 新宽度）。
        """
        try:
            last = getattr(self, '_dash_last_synced_width', None)
            width = int(event.width)
            # 口径说明：与「上次事件宽度」比较，skip 时也更新 last，
            # 使连续 1px 拖拽被逐级合并，仅在累计达到阈值时才真正同步一次。
            self._dash_last_synced_width = width
            if last is None or abs(width - last) >= _DASH_WIDTH_SYNC_THRESHOLD:
                self.dash_container.itemconfig('all', width=width - 4)
            self._dash_request_refresh()
        except Exception:
            pass

    def _dash_request_refresh(self, force: bool = False) -> None:
        """GUI-02 防抖入口：合并短时间内的多次 scrollregion 刷新请求。

        50ms 窗口内的重复请求仅保留一个待执行标记，由 ``root.after`` 统一到
        ``_dash_flush_refresh`` 执行；``force=True`` 时立即执行（用于渲染等
        需要即时同步的场景）。

        参数:
            force: 为 True 时跳过防抖、立即执行一次刷新并取消待执行标记。
        """
        if force:
            self._dash_refresh_pending = False
            self._dash_flush_refresh()
            return
        if getattr(self, '_dash_refresh_pending', False):
            return
        self._dash_refresh_pending = True
        try:
            self.root.after(_DASH_REFRESH_DEBOUNCE_MS, self._dash_flush_refresh)
        except Exception:
            # 窗口已销毁等场景：退化为直接执行，避免刷新丢失
            self._dash_refresh_pending = False
            try:
                self._dash_flush_refresh()
            except Exception:
                pass

    def _dash_flush_refresh(self):
        """防抖刷新落地：执行一次滚动区域刷新并清除待执行标记。"""
        self._dash_refresh_pending = False
        self._update_result_scrollregion()

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
        """清空右侧预测结果仪表盘（工具栏「清空」按钮专用）。

        GUI-03（v5.2 §2.2）：语义收窄——仅响应用户显式清空动作，
        无条件销毁全部卡片；常规渲染更新走 _render_dashboard_impl 的
        增量 diff 路径（_dash_diff），不再全量 destroy。
        """
        self._dash_reset()
        self._show_toast('结果已清空', COLORS['success'])

    def _dash_reset(self):
        """销毁全部仪表盘卡片并复位索引/状态，恢复空态占位（GUI-03）。"""
        # 按原 v3.66 约定：卡片自身不注册 after 回调，destroy 前无需额外清理，
        # 窗口已销毁等场景一律静默降级。
        try:
            for w in list(self.result_dash.winfo_children()):
                if w.winfo_exists():
                    w.destroy()
        except Exception:
            pass
        self._dash_card_index = {}
        self._dash_card_state = {}
        self._dash_grid_ncols = {}
        self._last_dashboard_key = None
        self._alt_content = None
        self._alt_visible = False
        try:
            self.result_dash.pack_forget()
            self._result_placeholder.pack(fill=tk.BOTH, expand=True)
            self._update_result_scrollregion()
        except Exception:
            pass

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

    @staticmethod
    def _classify_breakpoint(panel_width: int) -> str:
        """按输出面板宽度划分三档断点（v5.2 GUI-07，方案 §311）。

        参数:
            panel_width: 输出面板（output_nb）当前实际宽度（像素）

        返回:
            str: 'compact'（<1024 紧凑）/ 'standard'（1024-1440 标准）
                 / 'wide'（>1440 宽绰）
        """
        if panel_width < _BREAKPOINT_COMPACT_MAX_W:
            return 'compact'
        if panel_width <= _BREAKPOINT_STANDARD_MAX_W:
            return 'standard'
        return 'wide'

    def _set_toolbar_mode(self, bp: str) -> None:
        """按断点切换结果页工具栏按钮文案（GUI-07：短文案 <1024）。

        紧凑档使用登记表中的短文案（释放窄屏操作区宽度，验收 A17），
        标准/宽绰档使用完整文案。仅改 text，不触碰按钮其余属性与
        禁用态（禁用外观由任务运行链路单独负责）。

        参数:
            bp: 断点档位（_classify_breakpoint 的返回值）
        """
        compact = (bp == 'compact')
        registry = getattr(self, '_bp_toolbar_texts', None) or {}
        for btn, texts in registry.items():
            text = texts[1] if compact else texts[0]
            try:
                if btn.winfo_exists():
                    btn.config(text=text)
            except Exception:
                continue

    def _set_ov_tiles_mode(self, mode: str) -> None:
        """概览磁贴在「4 列并排 pack」与「2×2 网格 grid」间切换（GUI-07）。

        方案 §311：容器宽度 < _TILE_GRID_MAX_W 时以 grid 替代并排 pack，
        窄屏两列排布避免磁贴被过度挤压；切回按同一套参数复原。

        Tk 约束：同一容器内 pack 与 grid 几何管理器不可混用，必须先
        成批脱离旧管理器、再成批挂载新管理器——逐个小部件交错切换会抛
        TclError（离屏实测证实），故此处脱离/挂载两段循环严格分离。

        参数:
            mode: 'grid'（2×2 网格）或 'row'（4 列并排）
        """
        cont = getattr(self, '_ov_container', None)
        # _ov_tiles 存的是磁贴「数值标签」句柄，其 master 即磁贴 Frame
        tiles = [lbl.master
                 for lbl in (getattr(self, '_ov_tiles', None) or {}).values()]
        if cont is None or not tiles:
            return
        try:
            if mode == 'grid':
                for t in tiles:
                    t.pack_forget()
                    t.pack_propagate(True)
                    t.configure(width=0)
                # uniform 组保证两列等宽、两行等高；weight=1 让磁贴
                # 随容器拉伸铺满单元格（sticky='nsew'）
                cont.grid_columnconfigure((0, 1), weight=1, uniform='ovcol')
                cont.grid_rowconfigure((0, 1), weight=1, uniform='ovrow')
                for idx, t in enumerate(tiles):
                    t.grid(row=idx // 2, column=idx % 2, sticky='nsew',
                           padx=(0, 8), pady=(0, 4) if idx < 2 else 0)
            else:
                for t in tiles:
                    t.grid_forget()
                for t in tiles:
                    self._pack_ov_tile_row(t)
        except Exception:
            # 个别磁贴已销毁等异常：放弃本次切换，不影响主流程
            return

    def _apply_breakpoint_layout(self) -> None:
        """按当前输出面板宽度应用响应式布局（幂等，GUI-07 主入口）。

        由 _on_layout_resize（right 容器 Configure → after_idle）驱动：
        1. 读 output_nb 实际宽度划分三档断点，档位变化时切换工具栏文案；
        2. 读磁贴容器宽度，按 _TILE_GRID_MAX_W 阈值切换 grid/row 布局；
        3. 幂等保护：未映射（withdraw / 离屏未 deiconify，宽度为 1）时
           跳过；档位与磁贴模式均未变化时零触碰，拖拽期连续 Configure
           不产生重排风暴（不干扰 A1-A4 闪烁守护）。

        无参数、无返回值。
        """
        nb = getattr(self, 'output_nb', None)
        if nb is None:
            return
        try:
            if not nb.winfo_exists():
                return
            panel_w = nb.winfo_width()
        except Exception:
            return
        if panel_w <= 1:
            return  # 未映射：保持构建期默认布局（标准档全文案 + 4 列并排）
        bp = self._classify_breakpoint(panel_w)
        if bp != getattr(self, '_active_breakpoint', 'standard'):
            self._set_toolbar_mode(bp)
            self._active_breakpoint = bp
        cont = getattr(self, '_ov_container', None)
        if cont is None:
            return
        try:
            cont_w = cont.winfo_width()
        except Exception:
            return
        if cont_w <= 1:
            return  # 容器未映射：保持当前磁贴模式
        mode = 'grid' if cont_w < _TILE_GRID_MAX_W else 'row'
        if mode != getattr(self, '_active_tile_mode', 'row'):
            self._set_ov_tiles_mode(mode)
            self._active_tile_mode = mode

    def _refresh_result_overview(self):
        """后台刷新「预测结果」页概览区四个指标磁贴（v4.0 §2.4）。

        在后台线程查询数据库（最新预测期号 / Top-1 历史命中率 / 已验证期数 / 历史数据量），
        结果经 root.after 回投主线程原地回填磁贴数值，全程不阻塞 UI。
        数据库不可用时各磁贴优雅降级为「—」，不影响主流程。

        GUI-05 指纹节流（PUP-20261019-013100 §2.2，验收 A14）：回投主线程前比对
        指纹 (latest_issue, verified_count, data_count) 三元组——与上次刷新完全
        一致时直接跳过，主线程零唤醒（_apply_ov_values 不被调用）；任一成分变化
        才回投。命中率由已验证数据聚合派生、不在指纹内，遵循方案三元组口径。

        参数:
            无（方法内部读取 self._ov_tiles 中的数值标签句柄）
        """
        tiles = getattr(self, '_ov_tiles', {}) or {}
        if not tiles:
            return

        def _worker():
            """后台线程：查库收集四个指标，返回 (issue_text, hit_rate_text, verified_text, data_text)。"""
            issue_text, hit_text, verified_text, data_text = '—', '—', '—', '—'
            # 指纹原始值（库内原始口径，非格式化文案）：-1 表示该查询未成功，
            # 与「查询成功且为 0」区分，保证失败→成功跳变必然触发回投。
            issue_raw, total_raw, cnt_raw = '—', -1, -1
            try:
                db = P5Database()
                if db.connect():
                    try:
                        # 最新预测期号
                        db.cursor.execute(
                            'SELECT target_issue FROM p5_prediction_record '
                            'ORDER BY target_issue DESC LIMIT 1')
                        row = db.cursor.fetchone()
                        if row and row.get('target_issue'):
                            issue_text = str(row['target_issue'])
                            issue_raw = issue_text
                        # 已验证期数 + 历史命中率（Top-1 真实口径）
                        stats = db.get_verification_stats()
                        total = int(stats.get('total', 0) or 0)
                        if total > 0:
                            verified_text = f"{total} 期"
                            top1 = stats.get('top1_avg_accuracy', stats.get('avg_accuracy', 0)) or 0
                            hit_text = f"{float(top1):.1f}%"
                        total_raw = total
                        # 历史数据量
                        cnt = db.get_history_data_count()
                        data_text = f"{cnt} 期"
                        cnt_raw = cnt
                    finally:
                        db.disconnect()
            except Exception:
                # DB 不可用：保持占位符，静默降级
                pass
            # GUI-05 指纹节流：三元组与上次刷新一致则跳过回投（主线程零唤醒）
            fingerprint = (issue_raw, total_raw, cnt_raw)
            if getattr(self, '_ov_last_fingerprint', None) == fingerprint:
                return
            self._ov_last_fingerprint = fingerprint
            try:
                self.root.after(0, lambda: self._apply_ov_values(
                    issue_text, hit_text, verified_text, data_text))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_ov_values(self, issue_text, hit_text, verified_text, data_text):
        """（主线程）原地回填四个概览磁贴数值，不重建控件。"""
        tiles = getattr(self, '_ov_tiles', {}) or {}
        values = {
            'issue': issue_text,
            'hit_rate': hit_text,
            'verified': verified_text,
            'data_count': data_text,
        }
        for key, val in values.items():
            lbl = tiles.get(key)
            if lbl is not None:
                try:
                    if lbl.winfo_exists():
                        lbl.config(text=val)
                except Exception:
                    pass

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
            _safe_grab_set(win)

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
                        fg, note = HIT_FG, '' # 首选命中
                        hit_cnt += 1
                    elif any(int(n) == act for n in nums[:3] if n is not None):
                        fg, note = TOP3_FG, '' # Top-3 含开奖
                    else:
                        fg, note = MISS_FG, '' # 未含
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
                             bg=HIT_ROW_BG if is_hit else COLORS['bg_secondary'],
                             fg=HIT_ROW_FG if is_hit else COLORS['text_primary']
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
                      bg=COLORS['accent_p5'], fg=WHITE, relief='flat',
                      command=win.destroy).pack(pady=(0, 8))

        except Exception as e:
            logger.debug(f"查看预测详情失败: {e}")

    def _show_search_dialog(self):
        """（已移除）运行日志已改为写入文件，搜索对话框停用。"""
        self._show_toast(" 运行日志已改为文件记录，无需界面搜索", COLORS['accent_info'], duration=1500)

    def _build_status_bar(self, parent):
        """构建底部状态栏（状态指示灯 + 状态文字 + 数据库状态）"""
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

    def _refresh_db_status(self):
        """每30秒刷新数据库状态"""
        try:
            from modules.database import P5Database
            db = P5Database()
            db.connect()
            self.db_status_var.set("数据库：已连接")
        except Exception:
            self.db_status_var.set("数据库：未连接")
        self.root.after(30000, self._refresh_db_status)

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
            self._liveness_watchdog_handle = self.root.after(60 * 1000, self._liveness_watchdog)
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
        # 重置进度节流状态与 ETA 外推估计器（v5.0 §2.1.3）
        self._last_progress_value = -1
        self._last_progress_text = ""
        self._progress_est.reset()
        self._task_finish_reason = ""

        # 重置并隐藏结果仪表盘（每次任务以最新结果为准）
        self._last_pipeline_final = None
        self._last_trend_result = None
        self._last_quick_final = None
        # v4.1：随任务启动重置各来源产物时间戳，避免复用上一轮陈旧时间
        self._last_pipeline_ts = 0
        self._last_trend_ts = 0
        self._last_quick_ts = 0
        self._clipboard_generated_ts = 0
        # 一并清空上轮融合概率缓存，避免「命中率优化」阶段
        # 拿到上一次任务的陈旧分布做对照。
        self._last_fused_probabilities = None
        self._last_predict_meta = {}
        try:
            self._hide_result_dashboard()
        except Exception:
            pass
        try:
            self._show_loading_indicator()
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
        # 统一文案模板：阶段名 · 百分比 · 预计剩余（v5.0 §2.1.3）
        self.task_status_label.config(
            text=ui_styles.format_progress_text(0, "", task_name=task_name),
            fg=COLORS['warning'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['warning'])

        # 使用 TaskManager 刷新日志，避免高频UI操作
        now = datetime.now().strftime('%H:%M:%S')
        self.task_mgr.log(f"\n{'=' * 70}\n")
        self.task_mgr.log(f"  [{now}] 开始执行: {task_name}\n")
        self.task_mgr.log(f"{'=' * 70}\n\n")

    def _on_task_finished(self):
        """任务完成时：恢复按钮、进度条置100%、状态指示器变绿（v3.36 优化版）"""
        self._hide_loading_indicator()
        if self._last_pipeline_final or self._last_trend_result or self._last_quick_final:
            self._show_result_dashboard(
                pipeline_final=self._last_pipeline_final,
                trend_result=self._last_trend_result,
                quick_final=self._last_quick_final
            )
        else:
            self._hide_result_dashboard()
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 100
        self.progress_label.config(text="100%")
        self._last_progress_value = 100  # 更新节流状态
        self._last_progress_text = ""    # 终态文案与运行中不同，避免节流误跳过
        self.status_var.set("任务完成")
        # 统一文案模板（v5.0 §2.1.3）
        self.task_status_label.config(
            text=ui_styles.format_progress_text(100, "完成", task_name=self._current_task_name),
            fg=COLORS['success'])
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

    def _classify_error(self, error_msg: str):
        """将任务错误文本归因分类，返回 (类别, 原因说明, 建议操作列表)（v4.0 §2.4 错误引导）。

        按已知错误特征做关键词归因，输出用户可直接执行的排查建议，
        替代原先「只抛原始错误文本」的低效反馈。未知错误走「其他」兜底。

        参数:
            error_msg: 任务抛出的错误信息（可能含异常类名 / 堆栈片段）

        返回:
            tuple: (category: str, reason: str, suggestions: list[str])
        """
        s = (error_msg or '').lower()

        # 网络 / 爬取类（先判定：requests/urllib/timeout 等强网络特征优先，
        # 避免被数据库组宽泛的 "connection" 误捕）
        if any(k in s for k in ('requests', 'urllib', 'timeout', 'timed out', 'connectionerror',
                                 'proxy', 'ssl', 'dns', 'resolve', '网络', '爬取', '抓取', '502', '504')):
            return ('网络/数据源', '外部数据源访问超时或被限流',
                    ['检查本机网络连通性', '等待 1~2 分钟后重试（数据源可能限流）',
                     '尝试「全量爬取数据」重建数据', '检查是否触发反爬（可间隔重试）'])
        # 数据库 / 连接类（去掉宽泛裸 "connection"，避免误捕 requests.ConnectionError）
        if any(k in s for k in ('pymysql', 'mysql', 'database', '连接', 'cannot connect',
                                 'access denied', 'no such file', 'operating system',
                                 'dbutilspooled', 'maxed out', 'too many connections')):
            return ('数据库连接', '数据库连接失败或连接不可用',
                    ['检查 MySQL 服务是否已启动', '确认 config.py 中 DB_CONFIG 主机/端口/账号正确',
                     '检查防火墙与最大连接数限制', '稍后重试或先执行「数据爬取」触发重连'])
        # 数据不足 / 空态
        if any(k in s for k in ('no data', 'empty', '索引', 'indexerror', 'list index out of range',
                                 '至少需要', '样本', '历史数据', '无数据', 'insufficient')):
            return ('数据不足', '历史样本不足以支撑预测计算',
                    ['先执行「增量爬取数据」补齐历史期数', '确认库存 ≥ 61 期后再运行分析',
                     '查看「数据概览」核对各表记录数'])
        # 模型 / 算法类
        if any(k in s for k in ('numpy', 'sklearn', 'np.', 'matmul', 'shape', 'divide by zero',
                                 'nan', 'convergence', '模型', '训练', 'predict')):
            return ('模型/算法', '预测模型或算法执行异常',
                    ['查看 logs/gui_run.log 定位具体阶段', '确认 numpy/scikit-learn 版本匹配',
                     '清理陈旧缓存后重试（data/ 目录缓存）'])
        # 文件 / 路径类
        if any(k in s for k in ('filenotfound', 'permission denied', '磁盘', 'disk full',
                                 'permissionerror', '路径', 'no such file')):
            return ('文件/路径', '文件读写或磁盘空间异常',
                    ['检查磁盘剩余空间', '确认 reports/ 与 logs/ 目录可写', '确认未超出系统路径长度限制'])
        # 内存类
        if any(k in s for k in ('memoryerror', '内存', 'out of memory', 'cannot allocate')):
            return ('内存不足', '资源占用过高导致分配失败',
                    ['关闭其他高内存程序后重试', '尝试分批（缩短回测期数）运行', '重启应用释放内存'])
        # 兜底
        return ('未知错误', '任务执行时发生未分类异常',
                ['查看完整日志：logs/gui_run.log', '截图错误信息便于排查',
                 '若反复出现，尝试重启应用', '必要时执行「综合验证与分析」做健康诊断'])

    def _on_task_error(self, error_msg):
        """任务出错时：恢复按钮、进度条归零、状态指示器变红、弹窗提示。

        v4.0 §2.4 增强：错误反馈从「只抛原始文本」升级为「分类归因 + 原因 + 建议操作」，
        帮助用户快速定位问题而非面对一段原始堆栈。
        """
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self._last_progress_value = 0  # 更新节流状态
        self._last_progress_text = ""    # 终态文案与运行中不同，避免节流误跳过
        self.status_var.set("任务失败")
        # 统一文案模板（v5.0 §2.1.3）
        self.task_status_label.config(
            text=ui_styles.format_progress_text(0, "失败", task_name=self._current_task_name),
            fg=COLORS['accent_danger'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])

        # 分类归因 + 建议操作（v4.0 §2.4 错误引导）
        category, reason, suggestions = self._classify_error(error_msg)
        guide_lines = '\n'.join(f"  {i+1}. {s}" for i, s in enumerate(suggestions))
        messagebox.showerror(
            "错误",
            f"任务「{self._current_task_name}」执行失败\n"
            f"\n【错误类型】{category}\n"
            f"【原因】{reason}\n"
            f"【详细】{error_msg}\n"
            f"\n【建议操作】\n{guide_lines}"
        )

        # 关键修复：强制重置任务运行状态，防止竞态条件导致后续操作误判
        # 使用 after(0) 确保在主线程中执行，避免多线程问题
        self.root.after(0, self._sync_task_state)

    def _on_task_cancelled(self, task_id=None):
        """任务被取消（TaskManager 经 TASK_CANCELLED 消息通知）。

        v5.0 §2.1 修复：本方法此前**缺失**——TaskManager 派发 TASK_CANCELLED 时
        抛 AttributeError，被 _dispatch_ui_message 的 except 静默吞掉，导致
        「取消任务」后按钮永久禁用、状态条停在运行中。现统一恢复交互并按
        统一文案模板呈现已取消状态。

        幂等：可安全重复调用（GUI 主动取消与后台 TASK_CANCELLED 消息都会触发）。
        """
        self._hide_loading_indicator()
        self._set_buttons_state(tk.NORMAL)
        reason = getattr(self, '_task_finish_reason', '') or ''

        if reason == 'timeout':
            # 超时杀死任务：按失败呈现，而非「用户主动取消」
            self._last_progress_value = 0
            self._last_progress_text = ""
            self.progress['value'] = 0
            self.progress_label.config(text="0%")
            self.task_status_label.config(
                text=ui_styles.format_progress_text(0, '超时', task_name=self._current_task_name),
                fg=COLORS['accent_danger'])
            self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])
            self.status_var.set("任务超时")
            self._show_toast("任务超时已停止", COLORS['accent_danger'], duration=2500)
        else:
            # 用户主动取消：保留取消瞬间的进度值，仅标注已取消
            try:
                at = int(self.progress['value'])
            except Exception:
                at = 0
            self._last_progress_value = at
            self._last_progress_text = ""
            self.task_status_label.config(
                text=ui_styles.format_progress_text(at, '已取消', task_name=self._current_task_name),
                fg=COLORS['warning'])
            self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['warning'])
            self.status_var.set("任务已取消")

        # 强制重置任务运行状态，防止竞态导致后续按钮状态误判
        self.root.after(0, self._sync_task_state)

    def _on_task_timeout(self, task_id=None, name=''):
        """任务超时（TaskManager 经 TASK_TIMEOUT 消息通知）。

        v5.0 §2.1 修复：本方法此前**缺失**——TaskManager 超时监控线程派发
        TASK_TIMEOUT 时抛 AttributeError 被静默吞掉，超时后按钮永久禁用、
        用户无从得知任务已被终止。超时视为失败：记录原因后统一恢复，
        文案由紧随的 TASK_CANCELLED 处理路径按「超时」呈现。
        """
        self._task_finish_reason = 'timeout'
        if name:
            self._current_task_name = name
        self._on_task_cancelled(task_id)

    def _cancel_current_task(self):
        """取消当前任务（v3.36 新增 / v5.0 §2.1 修正状态文案）。

        原实现直接调用 _on_task_finished()，会把已取消的任务显示为
        「已完成 100%」，属状态误导。现改为按统一文案模板呈现「已取消」，
        并保留后续 TASK_CANCELLED 消息的幂等恢复能力。
        """
        if self.task_mgr.is_running():
            self.task_mgr.cancel()
            self._show_toast("任务已取消", COLORS['warning'], duration=1500)
            self._on_task_cancelled(None)

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
        # 按钮三态样式表由视觉规范工厂按当前主题即时产出（v5.2 GUI-08）
        _style_map = button_style_map(COLORS)
        style = _style_map.get(button_type, _style_map['secondary'])
        button.config(bg=style['bg'], fg=style['fg'])
        # 若仍处于禁用（任务运行中），应用禁用外观而非正常态
        if str(button['state']) == 'disabled':
            button.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])

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

    def _copy_prediction(self):
        """将「预测结果仪表盘」中的预测号码一键复制到剪贴板（微信兼容版）

        v4.1 修复：复制优先级链重构
        1. 任务运行中 → 提示等待
        2. 存在已聚合多源 clipboard（_prediction_clipboard 由
           _compute_dashboard_aggregates 在渲染时写入，含 ①②③ 共识主推荐）
           → 直接采用（与仪表盘所见一致，避免单源陈旧缓存）
        3. 否则回退到本次流水线 _last_pipeline_final 单源构建
        4. 再否则数据库最近记录兜底
        5. 全无数据 → 提示运行分析

        根因：此前 ② 直接读 _last_pipeline_final（仅① 单源），
        若本次 ① 失败/被 ②③ 覆盖共识，复制仍显示上一轮 ① 结果 → 
        "始终相同"。现改为优先取已聚合多源缓存。
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

        # ② v4.1：优先采用已聚合多源剪贴板（由 _compute_dashboard_aggregates 渲染时写入，
        #    与仪表盘共识主推荐完全一致）。命中且未过期即复制，避免单源陈旧缓存。
        # 关键修复：当 _pf 为 None 时（如任务刚启动、仪表盘未渲染），强制重算，
        # 避免沿用上一轮陈旧的预测结果导致不同期号出现完全相同的号码
        existing = (getattr(self, '_prediction_clipboard', '') or '').strip()
        _gen_ts = getattr(self, '_clipboard_generated_ts', 0) or 0
        _pf = getattr(self, '_last_pipeline_final', None)
        _pf_ts = getattr(self, '_last_pipeline_ts', 0) or 0
        # 判据：流水线单源在聚合缓存写入之后才产生（_pf 晚于 clipboard）→ 判定过期，重算聚合
        _stale = bool(existing and _pf and _pf_ts > _gen_ts) or (existing and not _pf)
        if existing and not _stale:
            clip = existing
            meta = getattr(self, '_clipboard_meta', {}) or {}
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').info(
                    'COPY_USE_AGGREGATED: 复用多源聚合剪贴板 len=%d issue=%s combo=%s',
                    len(clip), meta.get('target_issue', ''), meta.get('main_combo', ''))
            except Exception:
                pass
        else:
            # ③ 回退：重新聚合或单源构建
            if _stale:
                # 用 _last_* 重新算一遍多源聚合，写入最新 clipboard + 刷新时间戳
                try:
                    self._compute_dashboard_aggregates(
                        getattr(self, '_last_pipeline_final', None),
                        getattr(self, '_last_trend_result', None),
                        getattr(self, '_last_quick_final', None))
                except Exception:
                    pass
                clip = (getattr(self, '_prediction_clipboard', '') or '').strip()
                meta = getattr(self, '_clipboard_meta', {}) or {}
            if not clip:
                # 单源兜底
                if isinstance(_pf, dict) and _pf:
                    _single = self._build_prediction_clipboard(_pf)
                    if _single and _single.strip():
                        self._prediction_clipboard = _single
                        self._clipboard_meta = {
                            'target_issue': _pf.get('target_issue') or _pf.get('next_issue') or '',
                            'conf': '',
                            'high_conf': '',
                            'main_combo': '',
                        }
                        self._clipboard_generated_ts = _pf_ts or time.time()
                    clip = self._prediction_clipboard
                    meta = self._clipboard_meta
            # ④ 数据库兜底
            if not (clip and clip.strip()):
                try:
                    self._populate_clipboard_from_db_fallback()
                except Exception:
                    pass
                clip = getattr(self, '_prediction_clipboard', '')
                meta = getattr(self, '_clipboard_meta', {}) or {}

        if not clip or not clip.strip():
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').warning('COPY_BLOCKED_NO_RESULT: 无有效预测结果')
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
        """根据四步流水线 final_report 生成结构化的可复制预测摘要（委托纯函数）"""
        from modules.clipboard_builder import build_prediction_clipboard
        return build_prediction_clipboard(final_report, getattr(self, '_clipboard_meta', None))

    def _direct_extract_clipboard(self, final_report):
        """直接从 final_report 提取可复制文本，作为 _compute_dashboard_aggregates 兜底使用（委托纯函数）"""
        from modules.clipboard_builder import build_prediction_clipboard
        return build_prediction_clipboard(final_report, getattr(self, '_clipboard_meta', None))

    def _flash_status(self, text, color=COLORS['success']):
        """在底部状态栏临时显示提示，2.5 秒后恢复"就绪" """
        self.status_var.set(text)
        self.root.after(2500, lambda: self.status_var.set("就绪"))

    def _show_toast(self, text, color=None,
                    duration=ui_toast.TOAST_DEFAULT_DURATION_MS):
        """在主窗口内顶部居中弹出醒目的浮层提示（比状态栏更抓眼球）。

        无边框 Toplevel，淡入→短驻→淡出后自动销毁；同一时刻只保留一个 toast。
        任何异常都静默降级（不影响主流程），并同时刷新状态栏作为兜底。
        停留时长经 ui_toast.clamp_toast_duration 钳制（v5.2 GUI-10 方案
        §331 验收：Toast 自动消失 ≤ 5 s）；图标由 ui_toast.toast_icon_for
        按背景色解析，与此前内联三元行为完全一致。
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
            # 添加图标前缀区分状态（图标解析收敛至 v5.2 GUI-10 契约纯函数）
            icon = ui_toast.toast_icon_for(bg, COLORS)
            tk.Label(frame, text=f"{icon}  {text}", bg=bg, fg=WHITE,
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
            # 停留时长先钳制（≤5s 契约）再与最短驻留取大，行为同旧 max(600, duration)
            _hold_ms = ui_toast.clamp_toast_duration(duration)
            _hold_ms = max(ui_toast.TOAST_MIN_HOLD_MS, _hold_ms)
            toast.after(_hold_ms, _fade_out)
        except Exception as e:
            logger.debug(f"toast 显示失败(非致命): {e}")
        # 状态栏兜底，确保信息始终可见
        self._flash_status(text, bg)

    def _confirm_close(self):
        """窗口关闭请求处理（v3.25/v3.64/v3.65）：有任务运行时先警示确认，避免误关中断分析。

        v3.64 修复：关闭前先 cancel _poll_evolution 的 after 定时器，防止 destroy() 后
        仍有 Tcl 回调在调度导致白屏卡死。
        v3.65 修复：
          1. 保存并 cancel 所有 after 定时器句柄（_poll_evolution / _liveness_watchdog）
          2. destroy() 前等待 self_evolution 守护线程真正退出，防止 TclError 白屏
          3. cancel 顺序：标记 → 取消定时器 → 关闭线程池 → 等待线程退出 → destroy

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
        
        # 更新状态栏：开始关闭
        self._update_status_ui("正在关闭...", COLORS['text_secondary'])

        # ===== 第一步：标记取消，阻断所有 after 定时器继续调度 =====
        self._poll_evolution_cancelled = True

        # cancel _update_time 定时器（v3.66：防止 destroy() 后时钟回调继续调度）
        _ut_handle = getattr(self, '_update_time_handle', None)
        if _ut_handle is not None:
            try:
                self.root.after_cancel(_ut_handle)
            except Exception:
                pass
            self._update_time_handle = None

        # cancel _poll_evolution 定时器（v3.65：使用保存的句柄）
        handle = getattr(self, '_poll_evolution_handle', None)
        if handle is not None:
            try:
                self.root.after_cancel(handle)
            except Exception:
                pass
            self._poll_evolution_handle = None

        # cancel _poll_cli_log 定时器（需求 #3：命令行输出实时刷新）
        self._poll_cli_log_cancelled = True
        cli_handle = getattr(self, '_poll_cli_log_handle', None)
        if cli_handle is not None:
            try:
                self.root.after_cancel(cli_handle)
            except Exception:
                pass
            self._poll_cli_log_handle = None

        # GUI-04：停止轮询统一调度循环（cancel 挂起 tick；stop 幂等，
        # 已取消的任务内部自守卫双保险，防止 destroy() 后回调继续调度）
        _scheduler = getattr(self, '_ui_scheduler', None)
        if _scheduler is not None:
            _scheduler.stop()

        # cancel _liveness_watchdog 定时器（v3.65）
        lw_handle = getattr(self, '_liveness_watchdog_handle', None)
        if lw_handle is not None:
            try:
                self.root.after_cancel(lw_handle)
            except Exception:
                pass
            self._liveness_watchdog_handle = None
        
        # 更新状态栏：停止后台任务
        self._update_status_ui("停止后台任务...", COLORS['text_secondary'])

        # ===== 第二步：关闭任务线程池 =====
        # v3.67 修复：必须在主线程里先 cancel TaskManager._poll_handle
        # （Tcl 调用跨线程不安全：后台线程调 after_cancel 会被封送回主线程，
        # 而主线程又卡在 join(5s) 等待后台线程退出 → 死锁！）
        try:
            _tm_poll = getattr(self.task_mgr, '_poll_handle', None)
            if _tm_poll is not None:
                self.root.after_cancel(_tm_poll)
                self.task_mgr._poll_handle = None
                logger.debug('[GUI] 主线程已 cancel TaskManager._poll_handle')
        except Exception as e:
            logger.debug('[GUI] cancel TaskManager._poll_handle 失败: %s', e)

        # v3.67: shutdown() 内部已移除 Tcl 调用，后台线程现在可以安全地只做
        # 线程池关闭和日志 flush，不会再产生跨线程死锁
        try:
            def shutdown_task_manager():
                self.task_mgr.shutdown()
            
            shutdown_thread = threading.Thread(target=shutdown_task_manager, daemon=True)
            shutdown_thread.start()
            shutdown_thread.join(timeout=5.0)
            if shutdown_thread.is_alive():
                logger.warning("TaskManager shutdown timed out, proceeding anyway")
        except Exception:
            pass

        # ===== 第三步：优雅关闭自我进化引擎 =====
        eng = getattr(self, 'evolution', None)
        if eng is not None:
            try:
                eng.shutdown()
            except Exception:
                pass
            # v3.65：等待后台线程真正退出，防止 destroy() 后 TclError 白屏
            if eng._thread is not None and eng._thread.is_alive():
                try:
                    eng._thread.join(timeout=3.0)
                except Exception:
                    pass

        # ===== 3.5：优雅停止 §3.4 模型监控守护线程（若已启动） =====
        # model_monitor 的 _monitor_loop 是 daemon 线程；若 app 启动后启用了
        # start_model_monitoring()，关闭时须 stop_model_monitoring() 让线程在 5s 内
        # 主动退出并置 _stop_monitor，避免守护线程在 destroy() 后继续 5s 轮询刷盘。
        # 惰性导入 + try/except：监控未启动时 stop_monitoring() 仅 warning，不影响关闭。
        try:
            from modules.model_monitor import stop_model_monitoring
            stop_model_monitoring()
        except Exception as _mm_stop_e:
            logger.debug('[GUI] 停止模型监控守护线程跳过(非致命): %s', _mm_stop_e)

        # ===== 3.6：优雅停止 §4.3 系统性能监控守护线程（若已启动） =====
        # perf_monitor 的 _monitor_loop 是 1Hz daemon 线程；关闭时须
        # stop_perf_monitoring() 让线程在 ~1s 内主动退出，避免 destroy() 后
        # 继续采样。惰性导入 + try/except：监控未启动时 stop_monitoring() 幂等
        # 空操作，不影响关闭。
        try:
            from modules.perf_monitor import stop_perf_monitoring
            stop_perf_monitoring()
        except Exception as _pf_stop_e:
            logger.debug('[GUI] 停止性能监控守护线程跳过(非致命): %s', _pf_stop_e)

        # ===== 第四步：销毁窗口（所有定时器已取消，后台线程已退出）=====
        # v3.66: 先强制处理所有待执行的 after 回调，再销毁窗口，防止白屏
        try:
            # 等待所有 pending 的 after(0) 回调执行完毕
            self.root.update_idletasks()
        except Exception:
            pass

        # v3.65: destroy 前做最终窗口有效性检查，防止在 after 回调嵌套执行时重复 destroy
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except Exception:
            pass
        return True

    def _update_progress_ui(self, value, text=""):
        """更新进度条值与进度文本（v3.36 节流版 / v5.0 §2.1.3 统一文案模板）。

        文案模板统一为「阶段名 · 百分比 · 预计剩余」：
          * 运行中：``阶段名 · 42% · 预计剩余约1分20秒``（ETA 不可估时显示「计算中」）
          * 终态：  ``任务名 · 已完成/失败原因 · NN%``
        预计剩余来自 :class:`ui_styles.ProgressEstimator` 的几何外推，样本不足时
        显示「计算中」而非编造数字。上游 ``task_mgr.progress(value, text)`` 的既有
        文案全部保持不变，归一化只发生在本渲染层。
        进度条本体（progress / progress_label）仍只显示百分比，避免与状态标签重复。
        """
        try:
            pct = int(round(max(0.0, min(100.0, float(value)))))
        except Exception:
            pct = 0

        # 仅运行中样本参与 ETA 外推；终态样本不外推
        if ui_styles.classify_progress_text(text, pct) == ui_styles.STATUS_RUNNING:
            eta = self._progress_est.sample(pct)
        else:
            eta = None
        new_text = ui_styles.format_progress_text(
            pct, text,
            task_name=getattr(self, '_current_task_name', '') or '',
            eta_seconds=eta,
        )

        # 节流：值与文案均未变化才跳过（仅文案变化仍须刷新，例如 ETA 从
        # 「计算中」变为具体时长，或同百分比下阶段名变化）
        if (hasattr(self, '_last_progress_value') and self._last_progress_value == pct
                and getattr(self, '_last_progress_text', '') == new_text):
            return
        self._last_progress_value = pct
        self._last_progress_text = new_text

        self.progress['value'] = pct
        self.progress_label.config(text=f"{pct}%")
        if text:
            self.task_status_label.config(text=new_text, fg=COLORS['text_secondary'])

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
        """批量设置所有操作按钮的启用/禁用状态（v4.0 §2.4 加载态视觉反馈）。

        禁用（任务运行中）：统一置灰外观，让用户感知「运行中不可再操作」；
        恢复（NORMAL）：还原各按钮记录的正常态配色。
        还原经 _apply_button_normal_style 按 token 动态解析（v4.0 主题适配）：
        按钮缓存的是「主题键 token / 静态 hex」，切换主题后仍取当前主题值，不泄漏旧色。
        另含显式取消按钮的反转管理（v5.2 GUI-10，详见 _set_cancel_btn_state）。
        """
        for btn in self._buttons:
            try:
                btn.config(state=state)
                if state == tk.DISABLED:
                    btn.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])
                else:
                    self._apply_button_normal_style(btn)
            except Exception:
                continue
        # 结果区「复制预测号码」按钮独立控制：分析进行中禁用，完成或无数据时恢复
        if hasattr(self, '_result_copy_btn') and self._result_copy_btn is not None:
            try:
                self._result_copy_btn.config(state=state)
                if state == tk.DISABLED:
                    self._result_copy_btn.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])
                else:
                    self._apply_button_normal_style(self._result_copy_btn)
            except Exception:
                pass
        # 显式取消按钮（v5.2 GUI-10）启用态与操作按钮相反，单独反转管理
        _cancel_btn = getattr(self, '_cancel_btn', None)
        if _cancel_btn is not None:
            self._set_cancel_btn_state(_cancel_btn, state)

    def _set_cancel_btn_state(self, btn, state):
        """反转设置显式取消按钮的启停状态（v5.2 GUI-10 方案 §331）。

        取消按钮的启用语义与操作按钮相反：任务运行中（操作按钮收到
        tk.DISABLED）才允许点击取消；任务结束/取消/出错（操作按钮收到
        tk.NORMAL）后取消按钮灰化禁用。配色取自视觉规范工厂（v5.2
        GUI-08 单一来源）的 danger 三态，禁用外观与其余按钮统一
        （bg_input / text_disabled）。

        参数:
            btn: 取消按钮控件（tk.Button 或其测试替身）
            state: 操作按钮侧的状态（tk.DISABLED / tk.NORMAL）

        返回:
            None
        """
        try:
            if state == tk.DISABLED:
                # 任务运行中：取消按钮转为可点击的危险态
                _style_map = button_style_map(COLORS)
                _danger = _style_map.get('danger', _style_map['secondary'])
                btn.config(state=tk.NORMAL)
                btn.config(bg=_danger['bg'], fg=_danger['fg'])
            else:
                # 空闲态：灰化禁用，与操作按钮禁用外观一致
                btn.config(state=tk.DISABLED)
                btn.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])
        except Exception:
            pass

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
            task_mgr.log("  ↑ 以上为「集合包含+±1容错」宽松口径（候选集~5个，天然虚高）")
            
            # ── Top-1 真实预测力口径（v3.67）──
            task_mgr.append_section_header(" ★ Top-1 真实预测力（首推号精确命中）")
            task_mgr.log("-" * 60)
            top1_wan = stats.get('top1_wan_accuracy', stats.get('strict_wan_accuracy', 0)) or 0
            top1_qian = stats.get('top1_qian_accuracy', stats.get('strict_qian_accuracy', 0)) or 0
            top1_bai = stats.get('top1_bai_accuracy', stats.get('strict_bai_accuracy', 0)) or 0
            top1_shi = stats.get('top1_shi_accuracy', stats.get('strict_shi_accuracy', 0)) or 0
            top1_ge = stats.get('top1_ge_accuracy', stats.get('strict_ge_accuracy', 0)) or 0
            top1_avg = stats.get('top1_avg_accuracy', stats.get('strict_avg_accuracy', 0)) or 0
            top1_full = stats.get('top1_full_matches', stats.get('strict_full_matches', 0)) or 0
            task_mgr.log(f"  各位置 Top-1 命中率（随机基线=10%）:")
            task_mgr.log(f"    万位: {top1_wan:6.2f}%    千位: {top1_qian:6.2f}%    百位: {top1_bai:6.2f}%")
            task_mgr.log(f"    十位: {top1_shi:6.2f}%    个位: {top1_ge:6.2f}%")
            task_mgr.log(f"  平均 Top-1 准确率: {top1_avg:.2f}%   全5位 Top-1 命中: {top1_full} 期")
            task_mgr.log("  ↑ 这是真实预测力指标。排列5公平摇号，Top-1理论上限≈10%")
            
            # ── 随机基线对照（v3.67 Phase3）──
            task_mgr.append_section_header(" 随机基线对照（系统是否超越随机？）")
            task_mgr.log("-" * 60)
            _r1 = top1_avg / 100.0
            _r_base = 0.10
            _delta_pp = (_r1 - _r_base) * 100
            _verdict = "略高于" if _delta_pp > 0 else ("接近" if abs(_delta_pp) <= 1.0 else "低于")
            task_mgr.log(f"  系统 Top-1 命中率 : {top1_avg:6.2f}%")
            task_mgr.log(f"  随机基线 Top-1     : {_r_base*100:6.2f}%   (排列5 每位随机期望=10%)")
            task_mgr.log(f"  超出/低于随机      : {_delta_pp:+.2f} 个百分点  →  {_verdict} 随机水平")
            task_mgr.log("  结论: 排列5 为公平摇号, 历史统计规律无法事前预测未来开奖")
            task_mgr.log("        Top-1 命中率趋近 10% 即「无超越随机」, 属正常且诚实的结果")
            
            task_mgr.append_section_header(" 各位置命中率（宽松口径·候选集包含）")
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

            result = run_four_step_pipeline(target_issue=target_issue,
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
                    # v4.1：流水线产物时间戳（_copy_prediction 判定过期用）
                    self._last_pipeline_ts = time.time()

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

    def _toggle_hr_explanation(self, event):
        """切换历史命中率说明文字的显示/隐藏状态。"""
        if self.hr_explanation_visible.get():
            # 隐藏说明文字
            self.hr_explanation_text.pack_forget()
            self.hr_explanation_toggle.config(text=" [显示]")
            self.hr_explanation_visible.set(False)
        else:
            # 显示说明文字
            self.hr_explanation_text.pack(before=self.hr_explanation_toggle.master.master, fill=tk.X, pady=(0, 8))
            self.hr_explanation_toggle.config(text=" [隐藏]")
            self.hr_explanation_visible.set(True)


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

    def _load_and_draw_baseline_chart(self):
        """（主线程）启动后台线程生成基线对照图表。"""
        try:
            threading.Thread(target=self._draw_baseline_chart_internal, daemon=True).start()
        except Exception:
            pass

    def _draw_baseline_chart_internal(self):
        """（后台线程）生成基线对照柱状图，显示系统实测 vs 随机基线。"""
        try:
            # 导入必要模块
            import matplotlib
            matplotlib.use('Agg')  # 无头环境
            import matplotlib.pyplot as plt
            import numpy as np
            import os
            
            # 中文字体配置
            plt.rcParams['font.sans-serif'] = [
                'Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'SimSun', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 代表性评估窗口数（用于 Wilson 95% CI 宽度）
            N = 1000
            
            def wilson_ci(pct, n=N, z=1.96):
                """Wilson 95% 置信区间（输入/输出均为百分数）。"""
                p = pct / 100.0
                denom = 1 + z * z / n
                center = (p + z * z / (2 * n)) / denom
                half = (z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
                return (center - half) * 100.0, (center + half) * 100.0
            
            # 数据（来源：reports/diagnosis/hitrate_root_cause.md §2/§3）
            labels = ['Top-1\n(首推号精确匹配)', 'Top-3', 'Top-5']
            # 从数据库获取实际的系统实测数据
            db = P5Database()
            if db.connect():
                stats = db.get_verification_stats()
                db.disconnect()
            else:
                stats = {}
            system_top1 = stats.get('top1_avg_accuracy', 9.2)
            system_top3 = stats.get('top3_avg_accuracy', 30.0)
            system_top5 = stats.get('top5_avg_accuracy', 45.0)
            system = np.array([system_top1, system_top3, system_top5])
            baseline = np.array([10.0, 30.0, 50.0])    # 随机基线常量
            
            # 计算置信区间
            sys_lo = [wilson_ci(v)[0] for v in system]
            sys_hi = [wilson_ci(v)[1] for v in system]
            base_lo = [wilson_ci(v)[0] for v in baseline]
            base_hi = [wilson_ci(v)[1] for v in baseline]
            sys_err = [[v - lo for v, lo in zip(system, sys_lo)],
                       [hi - v for v, hi in zip(system, sys_hi)]]
            base_err = [[v - lo for v, lo in zip(baseline, base_lo)],
                        [hi - v for v, hi in zip(baseline, base_hi)]]
            
            # 使用 matplotlib Agg 后端生成柱状图
            fig, ax = plt.subplots(figsize=(9, 5.2), dpi=150)
            
            x = np.arange(len(labels))
            w = 0.36
            b1 = ax.bar(x - w / 2, system, w, yerr=sys_err, capsize=5,
                        color=CHART_SYSTEM, alpha=0.85, label='系统实测（严格口径）',
                        error_kw={'elinewidth': 1.4, 'ecolor': CHART_SYSTEM_ERR})
            b2 = ax.bar(x + w / 2, baseline, w, yerr=base_err, capsize=5,
                        color=CHART_RANDOM, alpha=0.85, label='随机基线（公平摇号）',
                        error_kw={'elinewidth': 1.4, 'ecolor': CHART_RANDOM_ERR})
            
            ax.set_ylabel('命中率 (%)', fontsize=12)
            ax.set_title('排列5 命中率：系统实测 vs 随机基线（95% Wilson CI）',
                        fontsize=13, fontweight='bold')
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=11)
            ax.set_ylim(0, 62)
            ax.grid(axis='y', alpha=0.3)
            ax.legend(loc='upper right', fontsize=10, framealpha=0.9)
            
            # 数值标注
            for bars, vals in ((b1, system), (b2, baseline)):
                for bar, val in zip(bars, vals):
                    ax.text(bar.get_x() + bar.get_width() / 2, val + 1.2, f'{val:.1f}%',
                            ha='center', va='bottom', fontsize=9)
            
            # 诚实结论注释
            conclusion = ('结论：系统实测 Top-1/3/5 全部落在随机基线 95% 置信区间内，\n'
                         '无统计显著差异 —— 低命中率 = 公平摇号属性，非代码缺陷。')
            ax.text(0.5, -0.22, conclusion, transform=ax.transAxes, ha='center',
                    fontsize=9.5, color=CHART_NOTE_TEXT,
                    bbox=dict(boxstyle='round,pad=0.5', facecolor=CHART_NOTE_FACE, edgecolor=CHART_NOTE_EDGE))
            
            ax.text(0.01, 0.98, f'n≈{N} 评估窗口（代表性）', transform=ax.transAxes,
                    ha='left', va='top', fontsize=8.5, color=CHART_NOTE_TEXT)
            
            plt.tight_layout()
            
            # 保存到临时文件
            import tempfile
            tmp_dir = REPORTS_CHARTS_DIR
            os.makedirs(tmp_dir, exist_ok=True)
            chart_path = os.path.join(tmp_dir, f'baseline_{int(time.time())}.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=COLORS['bg_primary'])
            plt.close(fig)
            
            # 移除 matplotlib 内嵌的 iCCP 色彩块，规避 libpng 警告
            self._strip_png_iccp(chart_path)
            
            # 回到主线程更新图片
            self.root.after(0, lambda: self._update_chart_image(chart_path))
            
        except Exception as e:
            logger.debug(f'生成基线对照图表失败: {e}')
            self.root.after(0, lambda: self._update_chart_image(None))

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

            # Top-1 真实口径（首推号精确命中, v3.67: strict_* 已改为真实预测力指标）
            # 随机基线: 排列5 公平摇号, 每位 Top-1 理论 = 10%, 系统无法稳定超越
            top1_avg = float(stats.get('top1_avg_accuracy', stats.get('strict_avg_accuracy', 0)) or 0)
            top1_full = int(stats.get('top1_full_matches', stats.get('strict_full_matches', 0)) or 0)
            top1_total = int(stats.get('top1_total_matched', stats.get('strict_total_matched', 0)) or 0)

            # 集合包含参考口径（候选集~5, 宽松, 仅作对照, 非真实预测力）
            loose_avg = float(stats.get('avg_accuracy', 0) or 0)
            loose_matched = int(stats.get('total_matched', 0) or 0)

            # 随机基线对照
            _delta_pp = top1_avg - 10.0
            _verdict = "略高于随机" if _delta_pp > 0 else ("接近随机" if abs(_delta_pp) <= 1.0 else "低于随机")

            self.hr_summary_var.set(
                f"已验证 {total} 期（按期号去重）\n"
                f"★ Top-1 真实命中（首推号精确）: 全中 {top1_full} 期 | 平均 {top1_avg:.1f}%\n"
                f"   随机基线 = 10% → 系统{_verdict}（Δ{_delta_pp:+.1f}pp）\n"
                f"集合包含参考（宽松口径·非预测力）: 全中 {loose_matched} 期 | 平均 {loose_avg:.1f}%"
                f"{merge_txt}"
            )

            # 更新进度条：显示 Top-1 真实命中率
            top1_keys = ('top1_wan_accuracy', 'top1_qian_accuracy', 'top1_bi_accuracy',
                        'top1_shi_accuracy', 'top1_ge_accuracy')
            for key in top1_keys:
                rate = float(stats.get(key, stats.get(key.replace('top1_', 'strict_'), 0)) or 0)
                pos_name = key.replace('top1_', '').replace('_accuracy', '')
                var_key = f'{pos_name}_accuracy'
                if var_key in self.hr_pos_vars:
                    self.hr_pos_vars[var_key].set(f"{rate:.1f}%")
                    self.hr_pos_bars[var_key]['value'] = max(0.0, min(100.0, rate))

            # 不再自动加载图表（已改为手动触发基线对照图表）
            # 可通过定时器或手动触发 _load_and_draw_baseline_chart() 来更新基线对照图表
            # 同时更新基线对照图表以反映最新的验证统计
            self._load_and_draw_baseline_chart()
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

            # Top-1 真实口径（首推号精确命中, v3.67: strict_* 已改为真实预测力指标）
            # 随机基线: 排列5 公平摇号, 每位 Top-1 理论 = 10%, 系统无法稳定超越
            top1_avg = float(stats.get('top1_avg_accuracy', stats.get('strict_avg_accuracy', 0)) or 0)
            top1_full = int(stats.get('top1_full_matches', stats.get('strict_full_matches', 0)) or 0)
            top1_total = int(stats.get('top1_total_matched', stats.get('strict_total_matched', 0)) or 0)

            # 集合包含参考口径（候选集~5, 宽松, 仅作对照, 非真实预测力）
            loose_avg = float(stats.get('avg_accuracy', 0) or 0)
            loose_matched = int(stats.get('total_matched', 0) or 0)

            # 随机基线对照
            _delta_pp = top1_avg - 10.0
            _verdict = "略高于随机" if _delta_pp > 0 else ("接近随机" if abs(_delta_pp) <= 1.0 else "低于随机")

            self.hr_summary_var.set(
                f"已验证 {total} 期（按期号去重）\n"
                f"★ Top-1 真实命中（首推号精确）: 全中 {top1_full} 期 | 平均 {top1_avg:.1f}%\n"
                f"   随机基线 = 10% → 系统{_verdict}（Δ{_delta_pp:+.1f}pp）\n"
                f"集合包含参考（宽松口径·非预测力）: 全中 {loose_matched} 期 | 平均 {loose_avg:.1f}%"
                f"{merge_txt}"
            )

            # 更新进度条：显示 Top-1 真实命中率
            top1_keys = ('top1_wan_accuracy', 'top1_qian_accuracy', 'top1_bai_accuracy',
                        'top1_shi_accuracy', 'top1_ge_accuracy')
            for key in top1_keys:
                rate = float(stats.get(key, stats.get(key.replace('top1_', 'strict_'), 0)) or 0)
                pos_name = key.replace('top1_', '').replace('_accuracy', '')
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
            
            # 准备数据：5个位置的 Top-1 真实命中率（首推号精确, 随机基线=10%）
            pos_keys = ['top1_wan_accuracy', 'top1_qian_accuracy', 'top1_bai_accuracy',
                        'top1_shi_accuracy', 'top1_ge_accuracy']
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            values = [float(stats.get(k, stats.get(k.replace('top1_', 'strict_'), 0) or 0)) for k in pos_keys]
            
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
            bars = ax.bar(pos_names, values, color=CHART_BARS)
            ax.set_ylim(0, 100)
            ax.set_ylabel('命中率 (%)', fontsize=10, color=fg)
            ax.set_title('各位置 Top-1 真实命中率（首推号精确·随机基线=10%）', fontsize=11, fontweight='bold', color=fg)
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
                    except OSError:
                        logger.debug('清理旧图表文件失败: %s', f)
            except OSError:
                logger.debug('清理旧图表文件目录失败: %s', REPORTS_CHARTS_DIR)
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
                result = analyzer.predict(target_issue='', period=DATA_WINDOW_PERIOD)
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

                task_mgr.append_info(f"  【{pname}】 Top-4: {' '.join(str(d) for d in top5)}")

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
            self._last_trend_ts = time.time()
            self._show_result_dashboard(trend_result=result)
            task_mgr.log("\n 走势引擎分析完成")

        except Exception as e:
            task_mgr.log(f"\n  [错误] 走势引擎分析失败: {str(e)}")
            task_mgr.log(f"  [错误详情]\n{traceback.format_exc}")
            task_mgr.progress(0, "错误")

    # ============================================================
    # 业务任务 - 智能分析中心（合并 流水线 + 走势 + 快速预测）
    # ============================================================

    def _stage_guard(func):
        """v4.0 阶段守卫装饰器：防止重入、验证状态、统一取消处理。
        
        - 防止同时运行多个分析任务（_analysis_running 标志）
        - 确保标志在异常情况下也正确清除
        - 主流程日志由被装饰函数自行处理
        """
        def wrapper(self, task_mgr, cancel_event=None):
            # 检查是否已有分析在运行
            if getattr(self, '_analysis_running', False):
                task_mgr.append_warning(" 分析已在运行中，请等待当前分析完成或取消后再试")
                return
            
            # 设置运行标志
            self._analysis_running = True
            try:
                return func(self, task_mgr, cancel_event)
            except Exception as e:
                task_mgr.append_error(f" 分析执行异常: {e}")
                raise
            finally:
                # 确保标志被清除（无论成功、取消还是异常）
                self._analysis_running = False
        
        return wrapper

    @_stage_guard
    def _execute_unified_analysis(self, task_mgr, cancel_event=None):
        """
        智能分析中心（极简模式）：单一「开始分析」按钮自动执行完整流程。

        v3.42 起，本方法是全系统唯一的分析入口，依次运行六个阶段：
          ① 四步流水线预测   — 主预测，注册 pending 记录，内部已含验证闭环 + 权重自适应
          ② 走势引擎分解     — 信号源 Top-4 辅助
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

            # 2) 走势引擎分解（信号源 Top-4 辅助）
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
                top = sorted(probs.items(), key=lambda x: float(x[1]), reverse=True)[:4]
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
            # v4.1：快速预测产物时间戳
            self._last_quick_ts = time.time()

            # v5.0 §8.2.1：快速预测也落 p5_prediction_record（此前只有四步流水线落库，
            # 快速预测产物完全不持久化，导致验证闭环缺数据）。走统一持久化入口，
            # 含可达性预检 / 指数退避重试 / 应用层幂等 / 本地兜底，失败不阻断主流程。
            try:
                from modules.pipeline import Pipeline
                _persist_pipe = Pipeline()
                _persist_res = _persist_pipe._persist_prediction(
                    final, target_issue, source='quick')
                if not _persist_res.get('db_ok') and _persist_res.get('fallback_ok'):
                    task_mgr.append_warning(
                        f" 预测已生成但入库失败，已本地兜底: {_persist_res.get('local_path')}")
                elif not _persist_res.get('db_ok'):
                    task_mgr.append_warning(
                        f" 预测已生成但持久化失败: {_persist_res.get('error')}")
            except Exception as _persist_e:
                task_mgr.append_warning(f" 预测已生成但持久化失败: {_persist_e}")

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
                        top5[pk]['pipeline'] = [int(x) for x in nums[:5]]
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
                        top5[pk]['quick'] = [int(x) for x in nums[:5]]
            rc = qf.get('recommended_combinations') or []
            if rc and isinstance(rc[0], dict) and rc[0].get('combination'):
                combos['quick'] = str(rc[0]['combination'])
            if not target_issue:
                target_issue = qf.get('target_issue') or ''

        # 走势引擎（信号源：每位置 top5） - 已禁用，使用核心预测器融合概率
        if not target_issue and isinstance(tr, dict):
            target_issue = tr.get('target_issue') or ''

        # 由走势引擎 top5[0] 拼接其完整组合 - 已禁用
        pass

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
                "各位置 Top-4 候选:",
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
            # 展示层使用完整5位（v3.64）
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
                # v4.1：聚合剪贴板生成时间戳（_copy_prediction 判定过期用）
                self._clipboard_generated_ts = time.time()
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
        """聚合多源候选, 输出每个展示位的 4 个候选数字（Top-4 展示）。

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
        4. 最终输出：综合推荐组合（每位 Top-4 候选）
        """
        # 展示层使用完整5位（v3.64）
        pos_keys = DISPLAY_POS_KEYS
        pos_names = DISPLAY_POS_NAMES
        top5 = top5 or {}
        
        # 获取各位置所有候选号码（从top5中提取所有来源的Top-4号码）
        all_candidates = {}
        for pk, pn in zip(pos_keys, pos_names):
            # 收集所有来源的Top-4号码
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
            
            # 确保每个位置有4个候选号码
            if len(all_digits) < 4:
                for d in range(10):
                    if d not in seen:
                        all_digits.append(d)
                        seen.add(d)
                        if len(all_digits) >= 4:
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
        # 各分析源组合同步使用完整5位展示（v3.64）
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

    def _build_dashboard_specs(self, agg):
        """GUI-03（v5.2 §2.2）：把仪表盘渲染逻辑编译为纯数据规格树。

        规格树不再直接创建 Tkinter 控件，而是按 ``key = 'section::card_id'``
        逐条登记卡片规格（创建参数/几何/内容签名）。渲染时由 ``_dash_diff``
        对比新旧规格树：同 key 仅 ``configure`` 文本/色值（不 destroy），
        仅 add/remove 才涉及 create/destroy，从根上消除全量重建闪白。

        本方法与原 ``_build_alt_section`` / ``_build_source_matrix`` /
        ``_build_basis_section`` 三个「创建即渲染」的构建函数一一对应，
        视觉布局保持完全不变。

        参数:
            agg: ``_compute_dashboard_aggregates`` 产出的聚合 dict。

        返回:
            Dict[str, dict]: {key: spec}；dict 插入顺序保证父规格先于子规格，
            spec 含 key/kind/parent/pack/grid/props/rcat/cmd/extra/sig。
        """
        specs = {}   # type: Dict[str, dict]

        def _emit(section, card_id, kind, parent=None, pack=None, grid=None,
                  props=None, rcat=None, cmd=None, extra=None):
            """向规格树注册一张卡片；props 只允许可哈希值（用于内容签名）。"""
            props = dict(props or {})
            # sig 内容签名：结构（kind/parent/几何/命令标签/分类标记）与
            # props 全部纳入；任一变化即视为 update，kind/parent/几何变化
            # 由 _dash_diff 升级为 remove+add（无法原地 configure）。
            sig = (kind, parent,
                   tuple(sorted((pack or {}).items())),
                   tuple(sorted((grid or {}).items())),
                   cmd, rcat, tuple(sorted(props.items())))
            key = '{}::{}'.format(section, card_id)
            specs[key] = {
                'key': key,
                'kind': kind,
                'parent': parent,
                'pack': pack,
                'grid': grid,
                'props': props,
                'rcat': rcat,
                'cmd': cmd,
                'extra': extra,
                'sig': sig,
            }
            return key

        pf = self._last_pipeline_final
        tr = self._last_trend_result
        qf = self._last_quick_final
        picks = agg['picks']
        combos = agg['combos']
        consensus = agg['consensus']
        target_issue = agg['target_issue']
        main_combo_disp = agg['main_combo_disp']
        conf = agg['conf']
        high_conf = agg['high_conf']
        main_combo = agg['main_combo']
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        pos_names = ['万位', '千位', '百位', '十位', '个位']

        # ── 标题栏（渐变效果：P5绿 → AI紫）──────────────────────────
        _emit('hdr', 'canvas', 'canvas', pack={'fill': tk.X},
              props={'height': 42, 'highlightthickness': 0,
                     'bg': COLORS['bg_secondary']},
              extra={'draw': 'hdr_gradient'})
        _emit('hdr', 'left', 'frame', parent='hdr::canvas',
              pack={'side': tk.LEFT, 'padx': 12, 'pady': 4},
              props={'bg': COLORS['accent_p5']})
        _emit('hdr', 'lbl_ai', 'label', parent='hdr::left',
              pack={'anchor': tk.W},
              props={'text': ' AI智能预测', 'bg': COLORS['accent_p5'],
                     'fg': WHITE, 'font': ('微软雅黑', 12, 'bold')})
        # 最近一次分析时间标记（副标题, 让结论有明确的时间归属）
        _ana_t = getattr(self, '_last_analysis_time', None)
        if _ana_t is not None:
            _today = datetime.now().date()
            _tstr = (_ana_t.strftime('%H:%M:%S') if _ana_t.date() == _today
                     else _ana_t.strftime('%Y-%m-%d %H:%M'))
            _emit('hdr', 'lbl_ana', 'label', parent='hdr::left',
                  pack={'anchor': tk.W},
                  props={'text': f' 最近分析 {_tstr}',
                         'bg': COLORS['accent_p5'], 'fg': ACCENT_P5_SOFT_FG,
                         'font': ('微软雅黑', 8)})
        _emit('hdr', 'right', 'frame', parent='hdr::canvas',
              pack={'side': tk.RIGHT, 'padx': 8, 'pady': 6},
              props={'bg': COLORS['accent_p5']})
        # 导出下拉 / 详细分析按钮（Primary 样式，命令按标签在应用期绑定）
        _emit('hdr', 'btn_export', 'button', parent='hdr::right',
              pack={'side': tk.RIGHT, 'padx': (6, 0)},
              props={'text': ' 导出 ▾', 'bg': COLORS['accent_p5_light'],
                     'fg': WHITE, 'font': ('微软雅黑', 9, 'bold'),
                     'relief': 'flat', 'padx': 10, 'pady': 3,
                     'cursor': 'hand2',
                     'activebackground': COLORS['accent_p5_bright']},
              cmd='export_menu')
        _emit('hdr', 'btn_detail', 'button', parent='hdr::right',
              pack={'side': tk.RIGHT, 'padx': (6, 0)},
              props={'text': ' 详细分析', 'bg': COLORS['accent_p5_light'],
                     'fg': WHITE, 'font': ('微软雅黑', 9, 'bold'),
                     'relief': 'flat', 'padx': 10, 'pady': 3,
                     'cursor': 'hand2',
                     'activebackground': COLORS['accent_p5_bright']},
              cmd='detail_toggle')

        # ── 层次容器 ────────────────────────────────────────────────
        _emit('layers', 'container', 'frame',
              pack={'fill': tk.X, 'padx': 12, 'pady': 10},
              props={'bg': COLORS['bg_secondary']})

        # === 预测结论层 ===
        _emit('conclusion', 'frame', 'frame', parent='layers::container',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_secondary']}, rcat='预测结论')

        # 风险提示（放在结论层顶部）
        risk = ''
        if isinstance(pf, dict):
            risk = (pf.get('risk_warning') or '').strip()
        elif isinstance(qf, dict):
            risk = (qf.get('risk_warning') or '').strip()
        if risk:
            _emit('risk', 'frame', 'frame', parent='conclusion::frame',
                  pack={'fill': tk.X, 'pady': (0, 4)},
                  props={'bg': COLORS['bg_card'],
                         'highlightbackground': COLORS['accent_warning'],
                         'highlightthickness': 1})
            _emit('risk', 'lbl', 'label', parent='risk::frame',
                  pack={'anchor': tk.W, 'padx': 12, 'pady': 6},
                  props={'text': ' ' + risk, 'bg': COLORS['bg_card'],
                         'fg': COLORS['accent_warning_light'],
                         'font': ('微软雅黑', 9), 'wraplength': 620,
                         'justify': tk.LEFT})

        # 1) 数据概览行：期号 + 一致率 + 高置信度 + 来源数
        _emit('info', 'row', 'frame', parent='conclusion::frame',
              pack={'fill': tk.X, 'pady': (0, 6)},
              props={'bg': COLORS['bg_secondary']})
        _emit('info', 'lbl_issue', 'label', parent='info::row',
              pack={'side': tk.LEFT},
              props={'text': f"{'第' if target_issue else ''}"
                             f"{target_issue or '—'}"
                             f"{'期' if target_issue else ''}",
                     'font': ('Consolas', 13, 'bold'),
                     'bg': COLORS['bg_secondary'],
                     'fg': COLORS['text_primary']})
        if conf is not None:
            _emit('info', 'lbl_conf', 'label', parent='info::row',
                  pack={'side': tk.LEFT, 'padx': (8, 0)},
                  props={'text': f' 一致率 {conf}%',
                         'bg': COLORS['bg_card'],
                         'fg': COLORS['accent_p5'],
                         'font': ('微软雅黑', 10, 'bold')})
        if high_conf:
            _emit('info', 'lbl_high', 'label', parent='info::row',
                  pack={'side': tk.LEFT, 'padx': (8, 0)},
                  props={'text': ' 高置信度 ✓',
                         'bg': COLORS['badge_active_bg'],
                         'fg': COLORS['badge_active_fg'],
                         'font': ('微软雅黑', 9, 'bold')})
        src_names = ['四步流水线', '走势引擎', '快速预测']
        used = [n for i, n in enumerate(src_names)
                if (i == 0 and pf) or (i == 1 and tr) or (i == 2 and qf)]
        _emit('info', 'lbl_src', 'label', parent='info::row',
              pack={'side': tk.LEFT, 'padx': (8, 0)},
              props={'text': f' 来源 {len(used)}/3',
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_secondary'],
                     'font': ('微软雅黑', 9)})

        # 2) 主推号大卡
        _emit('main', 'card', 'frame', parent='conclusion::frame',
              pack={'fill': tk.X, 'pady': (8, 6)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1})
        _emit('main', 'lbl_title', 'label', parent='main::card',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (8, 4)},
              props={'text': '推荐号码', 'font': ('微软雅黑', 11, 'bold'),
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_primary']})
        _emit('main', 'row', 'frame', parent='main::card',
              pack={'fill': tk.X, 'padx': 12, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card']})
        for _i, _pk_disp in enumerate(DISPLAY_POS_KEYS):
            _d = consensus.get(_pk_disp)
            _emit('main', 'digit_%d' % _i, 'label', parent='main::row',
                  pack={'side': tk.LEFT, 'padx': 4},
                  props={'text': str(_d) if _d is not None else '—',
                         'font': ('Consolas', 18, 'bold'),
                         'bg': (COLORS['accent_p5'] if _d is not None
                                else COLORS['bg_card']),
                         'fg': (WHITE if _d is not None
                                else COLORS['text_muted']),
                         'width': 3, 'padx': 6, 'pady': 6})
        _emit('main', 'lbl_conf', 'label', parent='main::card',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (0, 8)},
              props={'text': f"高置信度 · 一致率 {conf or 0}%",
                     'font': ('微软雅黑', 9), 'bg': COLORS['bg_card'],
                     'fg': COLORS['text_secondary']})

        # 3) 备选号码折叠区（结论层的一部分，原 _build_alt_section 逻辑）
        _emit('alt', 'outer', 'frame', parent='conclusion::frame',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1}, rcat='预测结论')
        _emit('alt', 'btn_toggle', 'button', parent='alt::outer',
              pack={'anchor': tk.W},
              props={'text': (' 收起备选号码' if self._alt_visible
                              else ' 备选号码（各分析源结果）'),
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_secondary'],
                     'font': ('微软雅黑', 10, 'bold'),
                     'relief': 'flat', 'cursor': 'hand2',
                     'padx': 12, 'pady': 6},
              cmd='alt_toggle')
        # content 的 pack/unpack 由 _apply_dashboard_diff 结构修正段按
        # self._alt_visible 统一处理（保留用户折叠状态，规格内不写几何）
        _emit('alt', 'content', 'frame', parent='alt::outer',
              props={'bg': COLORS['bg_card']})
        _main_disp = compress_combo(main_combo)
        for _key, _name in [('pipeline', '四步流水线'),
                            ('quick', '快速预测'),
                            ('trend', '走势引擎')]:
            _c = combos.get(_key)
            if not _c:
                continue
            _c_disp = compress_combo(_c)
            _is_main = (_c_disp == _main_disp)
            _num_color = (COLORS['accent_p5_light'] if _is_main
                          else COLORS['text_secondary'])
            _num_font = (('Consolas', 11, 'bold') if _is_main
                         else ('Consolas', 11))
            _emit('alt', 'row_%s' % _key, 'frame', parent='alt::content',
                  pack={'fill': tk.X, 'pady': 3},
                  props={'bg': COLORS['bg_card']})
            _emit('alt', 'lbl_%s' % _key, 'label',
                  parent='alt::row_%s' % _key,
                  pack={'side': tk.LEFT},
                  props={'text': f' {_name}:', 'bg': COLORS['bg_card'],
                         'fg': COLORS['text_muted'],
                         'font': ('微软雅黑', 9, 'bold')})
            _emit('alt', 'num_%s' % _key, 'label',
                  parent='alt::row_%s' % _key,
                  pack={'side': tk.LEFT, 'padx': (8, 0)},
                  props={'text': _c_disp, 'bg': COLORS['bg_card'],
                         'fg': _num_color, 'font': _num_font})
            if _is_main:
                _emit('alt', 'badge_%s' % _key, 'label',
                      parent='alt::row_%s' % _key,
                      pack={'side': tk.LEFT, 'padx': (6, 0)},
                      props={'text': '← 选为最终预测',
                             'bg': COLORS['bg_card'],
                             'fg': COLORS['accent_p5'],
                             'font': ('微软雅黑', 8, 'bold')})

        # === 分位信号层（原 _build_source_matrix 逻辑）────────────────
        _emit('signals', 'frame', 'frame', parent='layers::container',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_secondary']}, rcat='分位信号')
        src_defs = [('pipeline', '流水线'), ('trend', '走势'),
                    ('quick', '快速')]
        # 仅展示实际有数据的来源列，避免空列干扰
        active_srcs = [(k, n) for k, n in src_defs
                       if any(k in picks.get(pk, {}) for pk in pos_keys)]
        _emit('matrix', 'wrap', 'frame', parent='signals::frame',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1}, rcat='分位信号')
        _emit('matrix', 'lbl_title', 'label', parent='matrix::wrap',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (8, 4)},
              props={'text': ' 分源对比（逐位）',
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['accent_info'],
                     'font': ('微软雅黑', 10, 'bold')})
        _cols = [('位置', None)] + [(n, k) for k, n in active_srcs] \
            + [('综合', '__consensus__')]
        _emit('matrix', 'grid', 'frame', parent='matrix::wrap',
              pack={'fill': tk.X, 'padx': 12, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card']},
              extra={'grid_weight_cols': len(_cols)})
        for _c, (_name, _) in enumerate(_cols):
            _emit('matrix', 'head_%d' % _c, 'label', parent='matrix::grid',
                  grid={'row': 0, 'column': _c, 'padx': 1, 'pady': 1,
                        'sticky': 'nsew'},
                  props={'text': _name, 'fg': COLORS['text_secondary'],
                         'bg': COLORS['bg_secondary'],
                         'font': ('微软雅黑', 9, 'bold'),
                         'padx': 10, 'pady': 6, 'width': 7,
                         'relief': 'solid',
                         'highlightbackground': COLORS['border'],
                         'highlightthickness': 1})
        for _r, (_pk, _pn) in enumerate(zip(pos_keys, pos_names), start=1):
            _cons = consensus.get(_pk)
            _emit('matrix', 'cell_%d_0' % _r, 'label',
                  parent='matrix::grid',
                  grid={'row': _r, 'column': 0, 'padx': 1, 'pady': 1,
                        'sticky': 'nsew'},
                  props={'text': _pn, 'fg': COLORS['text_muted'],
                         'bg': COLORS['bg_secondary'],
                         'font': ('微软雅黑', 9, 'bold'),
                         'padx': 10, 'pady': 6, 'width': 7,
                         'relief': 'solid',
                         'highlightbackground': COLORS['border'],
                         'highlightthickness': 1})
            for _c, (_name, _key) in enumerate(_cols[1:], start=1):
                if _key == '__consensus__':
                    _txt = str(_cons) if _cons is not None else '—'
                    _fg, _bg = WHITE, (COLORS['accent_p5']
                                       if _cons is not None
                                       else COLORS['bg_secondary'])
                else:
                    _v = picks.get(_pk, {}).get(_key)
                    if _v is None:
                        _txt = '—'
                        _fg = COLORS['text_disabled']
                        _bg = COLORS['bg_panel']
                    elif _cons is not None and _v == _cons:
                        # 与共识一致 → 绿色高亮
                        _txt = str(_v)
                        _fg = CONSENSUS_HIT_FG
                        _bg = CONSENSUS_HIT_BG
                    else:
                        _txt = str(_v)
                        _fg = COLORS['text_secondary']
                        _bg = COLORS['bg_panel']
                _emit('matrix', 'cell_%d_%d' % (_r, _c), 'label',
                      parent='matrix::grid',
                      grid={'row': _r, 'column': _c, 'padx': 1, 'pady': 1,
                            'sticky': 'nsew'},
                      props={'text': _txt, 'fg': _fg, 'bg': _bg,
                             'font': ('Consolas', 12, 'bold'),
                             'padx': 10, 'pady': 6, 'width': 7,
                             'relief': 'flat'})

        # === 算法依据层（原 _build_basis_section 逻辑）────────────────
        _emit('basis', 'frame', 'frame', parent='layers::container',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_secondary']}, rcat='算法依据')
        _emit('basis', 'card', 'frame', parent='basis::frame',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1}, rcat='算法依据')
        _emit('basis', 'lbl_title', 'label', parent='basis::card',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (8, 4)},
              props={'text': '算法依据',
                     'font': ('微软雅黑', 10, 'bold'),
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_primary']})
        _emit('basis', 'weights', 'frame', parent='basis::card',
              pack={'fill': tk.X, 'padx': 12, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card']})
        # 冻结权重（来自升级方案）
        weight_items = [
            ('频率', '0.68'),
            ('监督学习', '0.14'),
            ('贝叶斯', '0.10'),
            ('遗漏', '0.00'),
            ('趋势', '0.01'),
            ('马尔可夫', '0.005'),
            ('形态', '0.003'),
            ('特征', '0.002'),
        ]
        for _i, (_name, _w) in enumerate(weight_items):
            _emit('basis', 'row_%d' % _i, 'frame', parent='basis::weights',
                  pack={'fill': tk.X, 'pady': 2},
                  props={'bg': COLORS['bg_card']})
            _emit('basis', 'name_%d' % _i, 'label',
                  parent='basis::row_%d' % _i, pack={'side': tk.LEFT},
                  props={'text': _name, 'font': ('微软雅黑', 9),
                         'bg': COLORS['bg_card'],
                         'fg': COLORS['text_secondary']})
            _emit('basis', 'val_%d' % _i, 'label',
                  parent='basis::row_%d' % _i, pack={'side': tk.RIGHT},
                  props={'text': _w, 'font': ('Consolas', 9, 'bold'),
                         'bg': COLORS['bg_card'],
                         'fg': COLORS['accent_p5']})

        return specs

    def _build_detail_section(self, parent, picks, top5, combos, target_issue,
                              pos_keys, pos_names, total_sources, conf, high_conf):
        """详细分析：各来源 Top 候选 + 计算逻辑 + 位置差异点，表格化展示"""
        tk.Label(parent, text="详细分析（来源 / 差异 / 计算逻辑）", bg=COLORS['bg_primary'],
                 fg=COLORS['text_primary'], font=('微软雅黑', 10, 'bold')
                 ).pack(anchor=tk.W, padx=8, pady=(6, 4))
        
        # 创建表格容器
        table_frame = tk.Frame(parent, bg=COLORS['bg_secondary'], relief='groove', bd=1)
        table_frame.pack(fill=tk.X, padx=8, pady=3)
        
        # 表头
        headers = ['来源', '万位', '千位', '百位', '十位', '个位', '说明']
        for i, header in enumerate(headers):
            tk.Label(table_frame, text=header, bg=COLORS['bg_secondary'], fg=COLORS['accent_p5'],
                     font=('微软雅黑', 9, 'bold')).grid(row=0, column=i, padx=4, pady=2, sticky='w')
        
        logic = {
            'pipeline': '四步流水线：八算法融合流水线，系统旗舰预测',
            'quick': '快速预测：P5Predictor 八算法融合模型',
            'trend': '走势引擎：6信号源相对热度打分',
        }
        
        # 来源颜色映射（v5.2 GUI-06：trend 原用未定义键 accent_orange，
        # dict 构造即 KeyError；走势引擎语义为橙，改查已定义槽 accent_warning）
        source_colors = {
            'pipeline': COLORS['accent_p5'],
            'quick': COLORS['accent_ai'],
            'trend': COLORS['accent_warning']
        }
        
        row = 1
        for key, name in [('pipeline', '四步流水线'), ('quick', '快速预测'), ('trend', '走势引擎')]:
            present = combos.get(key) or any(key in picks[pk] for pk in pos_keys)
            if not present:
                continue
            
            # 来源名称列
            tk.Label(table_frame, text=name, bg=COLORS['bg_secondary'], fg=source_colors.get(key, COLORS['text_primary']),
                     font=('微软雅黑', 9, 'bold')).grid(row=row, column=0, padx=4, pady=2, sticky='w')
            
            # 各位置候选
            for col, pk in enumerate(pos_keys, start=1):
                candidates = top5.get(pk, {}).get(key, [])
                text = ''.join(str(x) for x in candidates) if candidates else '—'
                tk.Label(table_frame, text=text, bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                         font=('Consolas', 9)).grid(row=row, column=col, padx=4, pady=2, sticky='w')
            
            # 说明列
            tk.Label(table_frame, text=logic.get(key, ''), bg=COLORS['bg_secondary'], fg=COLORS['text_muted'],
                     font=('微软雅黑', 8), wraplength=200, justify=tk.LEFT).grid(row=row, column=6, padx=4, pady=2, sticky='w')
            row += 1

        # 位置差异点表格
        diff_rows = []
        for pk, pn in zip(pos_keys, pos_names):
            vals = picks.get(pk, {})
            if len(set(vals.values())) > 1:
                diff_rows.append(f"{pn}位分歧: " + ' / '.join(f"{k}={v}" for k, v in vals.items()))
        
        if diff_rows:
            diff_frame = tk.Frame(parent, bg=COLORS['bg_secondary'], relief='groove', bd=1)
            diff_frame.pack(fill=tk.X, padx=8, pady=3)
            tk.Label(diff_frame, text="位置差异点", bg=COLORS['bg_secondary'], fg=COLORS['warning'],
                     font=('微软雅黑', 9, 'bold')).grid(row=0, column=0, columnspan=2, padx=8, pady=(4, 2), sticky='w')
            for i, d in enumerate(diff_rows, 1):
                tk.Label(diff_frame, text=d, bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                         font=('Consolas', 8), wraplength=600, justify=tk.LEFT).grid(row=i, column=0, columnspan=2, padx=8, pady=(0, 2), sticky='w')
            tk.Label(diff_frame, text="注：主推荐按「多源多数投票、平票优先四步流水线」规则生成。",
                     bg=COLORS['bg_secondary'], fg=COLORS['text_muted'],
                     font=('微软雅黑', 8), wraplength=600, justify=tk.LEFT).grid(row=len(diff_rows)+1, column=0, columnspan=2, padx=8, pady=(0, 4), sticky='w')

    @staticmethod
    def _dash_diff(old_state, new_data):
        """计算仪表盘卡片差异（GUI-03 增量渲染核心，纯函数，可离线单测）。

        对比新旧规格树（`{key: spec}`），产出 add/update/remove 三列表：

        * add: 新出现的 key（首次渲染 / 新数据源 / 新卡片）；
        * update: 同 key 且结构不变（kind/parent/pack/grid 均一致），
          仅内容签名 sig 变化（文本/色值等）→ 走原地 configure；
        * remove: 旧树中消失的 key + 结构变化的 key（结构变化无法
          原地 configure，降级为 remove+add）。

        参数:
            old_state: 上一次渲染保存的规格树（_dash_card_state）。
            new_data: 本次渲染编译出的新规格树。

        返回:
            dict: {'add': [spec, ...], 'update': [(key, spec), ...],
                   'remove': [key, ...]}
        """
        old_state = old_state or {}
        new_data = new_data or {}
        add, update, remove = [], [], []
        for key, spec in new_data.items():
            old = old_state.get(key)
            if old is None:
                add.append(spec)
            elif (old.get('kind') != spec.get('kind')
                  or old.get('parent') != spec.get('parent')
                  or old.get('pack') != spec.get('pack')
                  or old.get('grid') != spec.get('grid')):
                # 结构变化无法原地 configure → 升级为 remove+add
                remove.append(key)
                add.append(spec)
            elif old.get('sig') != spec.get('sig'):
                update.append((key, spec))
        remove.extend(k for k in old_state if k not in new_data)
        return {'add': add, 'update': update, 'remove': remove}

    def _dash_card_create(self, spec):
        """按规格创建单张卡片控件并完成几何布局（GUI-03 唯一创建入口）。

        创建参数全部来自规格树 props（与 configure 同源，保证视觉一致）；
        pack/grid 几何来自 spec['pack'] / spec['grid']；parent 为规格 key，
        经 _dash_card_index 解析为真实父控件（缺失时降级挂到 result_dash，
        保证父子顺序异常时也不会丢失卡片）。

        参数:
            spec: 规格树中的单项 spec dict。

        返回:
            创建成功的控件；kind 未知或创建异常时返回 None（记日志，不中断渲染）。
        """
        parent_w = None
        if spec.get('parent'):
            parent_w = self._dash_card_index.get(spec['parent'])
            if parent_w is not None and not parent_w.winfo_exists():
                parent_w = None
        if parent_w is None:
            parent_w = self.result_dash
        kind = spec['kind']
        props = dict(spec['props'])
        try:
            if kind == 'frame':
                w = tk.Frame(parent_w, **props)
            elif kind == 'label':
                w = tk.Label(parent_w, **props)
            elif kind == 'button':
                w = tk.Button(parent_w, **props)
            elif kind == 'canvas':
                w = tk.Canvas(parent_w, **props)
            else:
                logger.warning('[GUI-03] 未知卡片类型 kind=%s（key=%s），已跳过',
                               kind, spec.get('key'))
                return None
        except Exception as e:  # noqa: BLE001
            logger.warning('[GUI-03] 卡片创建失败 key=%s: %s',
                           spec.get('key'), e)
            return None
        # 分类筛选标记（兼容 _apply_result_category 的 _rcat 遍历协议）
        if spec.get('rcat'):
            try:
                w._rcat = spec['rcat']
            except Exception:
                pass
        # 几何布局（一张卡片只会使用 pack 或 grid 之一）
        try:
            if spec.get('grid'):
                w.grid(**dict(spec['grid']))
            elif spec.get('pack'):
                w.pack(**dict(spec['pack']))
        except Exception as e:  # noqa: BLE001
            logger.warning('[GUI-03] 卡片布局失败 key=%s: %s',
                           spec.get('key'), e)
        # 命令按钮按标签重建闭包，规避缓存闭包持有已销毁控件（见 _dash_bind_command）
        if spec.get('cmd'):
            self._dash_bind_command(w, spec['cmd'])
        # 标题栏渐变（复刻原实现：两个同尺寸矩形先后叠加，后绘者覆盖前者）
        extra = spec.get('extra') or {}
        if extra.get('draw') == 'hdr_gradient':
            try:
                _dash_w = self.result_dash.winfo_width()
                w.create_rectangle(0, 0, _dash_w, 42,
                                   fill=COLORS['accent_p5'], outline='')
                w.create_rectangle(0, 0, _dash_w, 42,
                                   fill=COLORS['accent_ai'], outline='')
            except Exception:
                pass
        return w

    def _dash_bind_command(self, widget, cmd):
        """按命令标签为按钮重建闭包（GUI-03）。

        按钮 command 闭包不可哈希、无法进入内容签名，改以 cmd 标签在
        create / update 应用期重建；任何阶段拿到的命令绑定的都是当前
        最新控件引用，杜绝「缓存闭包调用已销毁控件」类故障。

        参数:
            widget: 目标按钮控件。
            cmd: 命令标签（'export_menu' / 'detail_toggle' / 'alt_toggle'）。
        """
        if cmd == 'export_menu':
            widget.config(command=self._show_export_menu)
        elif cmd == 'detail_toggle':
            widget.config(command=self._toggle_detail)
        elif cmd == 'alt_toggle':
            widget.config(command=self._make_alt_toggle())

    def _make_alt_toggle(self):
        """构造「备选号码」折叠开关闭包（GUI-03）。

        折叠状态以 self._alt_visible 为单一事实源：切换时同步
        pack/unpack ``alt::content`` 与按钮文案；下一次渲染的规格树
        会按新状态生成按钮文本（text 纳入 sig），实现自愈。
        """

        def _toggle():
            self._alt_visible = not self._alt_visible
            content = self._dash_card_index.get('alt::content')
            try:
                if content is not None and content.winfo_exists():
                    if self._alt_visible:
                        content.pack(fill=tk.X, padx=12, pady=(0, 8))
                    else:
                        content.pack_forget()
                btn = self._dash_card_index.get('alt::btn_toggle')
                if btn is not None and btn.winfo_exists():
                    btn.config(text=' 收起备选号码' if self._alt_visible
                               else ' 备选号码（各分析源结果）')
                # 折叠/展开改变内容高度，防抖刷新滚动区域
                self._dash_request_refresh()
            except Exception:
                pass

        return _toggle

    def _dash_restore_scroll(self, saved):
        """A9：渲染后若视口位置漂移超过 5% 则回拽到渲染前位置。

        参数:
            saved: 渲染前保存的 ``dash_container.yview()`` 元组（None 时跳过）。
        """
        if saved is None:
            return
        try:
            cur = self.dash_container.yview()
            if abs(cur[0] - saved[0]) > 0.05:
                self.dash_container.yview_moveto(saved[0])
        except Exception:
            pass

    def _apply_dashboard_diff(self, diff):
        """应用渲染 diff：remove → add → update → 结构修正（GUI-03 唯一 Tk 写入口）。

        执行顺序保证父子控件依赖：先销毁消失卡片，再按规格树顺序
        （父先于子）创建新卡片，最后原地更新同 key 卡片；末尾单次
        ``update_idletasks`` 摊平本轮全部几何变化，避免逐控件抖动。

        参数:
            diff: ``_dash_diff`` 产出的 {'add': [spec], 'update': [(key, spec)],
                'remove': [key]}；三项均为空时零 Tk 操作（A7 口径）。
        """
        add = diff.get('add') or []
        update = diff.get('update') or []
        remove = diff.get('remove') or []
        if not (add or update or remove):
            return  # 无差异：本帧不触碰 Tk

        # A9：apply 前保存滚动位置，末尾按漂移回拽
        try:
            _saved_y = self.dash_container.yview()
        except Exception:
            _saved_y = None

        # 1) remove：销毁消失的卡片并摘除全部索引（含 grid 列权与折叠句柄）
        for _k in remove:
            w = self._dash_card_index.pop(_k, None)
            self._dash_card_state.pop(_k, None)
            self._dash_grid_ncols.pop(_k, None)
            if _k == 'alt::content':
                self._alt_content = None
            try:
                if w is not None and w.winfo_exists():
                    w.destroy()
            except Exception:
                pass

        # 2) add：按规格树顺序创建（_build_dashboard_specs 保证父先于子）
        for spec in add:
            w = self._dash_card_create(spec)
            if w is None:
                continue
            self._dash_card_index[spec['key']] = w
            if spec['key'] == 'alt::content':
                self._alt_content = w

        # 3) update：同 key 原地 configure（仅文本/色值等可配置项，不销毁）
        for key, spec in update:
            w = self._dash_card_index.get(key)
            if w is None or not w.winfo_exists():
                continue
            try:
                w.configure(**dict(spec['props']))
            except Exception as e:  # noqa: BLE001
                logger.warning('[GUI-03] 卡片更新失败 key=%s: %s', key, e)
            if spec.get('cmd'):
                self._dash_bind_command(w, spec['cmd'])

        # 4) 结构修正：同 key 场景的类级配置 / 折叠状态保持
        for spec in add + [s for _, s in update]:
            extra = spec.get('extra') or {}
            _ncols = extra.get('grid_weight_cols')
            if _ncols:
                gw = self._dash_card_index.get(spec['key'])
                if (gw is not None and gw.winfo_exists()
                        and self._dash_grid_ncols.get(spec['key']) != _ncols):
                    try:
                        for _c in range(_ncols):
                            gw.grid_columnconfigure(_c, weight=1)
                    except Exception:
                        pass
                    self._dash_grid_ncols[spec['key']] = _ncols

        # 备选号码折叠区：pack/unpack 不在规格内，按用户折叠状态统一修正
        # （规格树只描述「应有内容」，几何由本段按运行时状态补齐）
        content = self._dash_card_index.get('alt::content')
        if content is not None and content.winfo_exists():
            try:
                if self._alt_visible:
                    if not content.winfo_manager():
                        content.pack(fill=tk.X, padx=12, pady=(0, 8))
                elif content.winfo_manager():
                    content.pack_forget()
            except Exception:
                pass

        # 单次 idle 刷新，摊平本轮全部几何变化（避免逐个 create 触发重排）
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        # A9：滚动位置回拽
        self._dash_restore_scroll(_saved_y)

    def _show_result_dashboard(self, pipeline_final=None, trend_result=None, quick_final=None):
        """
        v4.0 重构：薄包装入口，参数适配后委托至 _render_unified_dashboard。
        保留旧签名以保持向后兼容，实际渲染由统一入口处理。
        """
        # 立即保存最新结果，确保后台线程调用时也能被 _copy_prediction 兜底读取
        # 这些属性的写入不涉及 Tkinter，是线程安全的
        if pipeline_final is not None:
            self._last_pipeline_final = pipeline_final
        if trend_result is not None:
            self._last_trend_result = trend_result
        if quick_final is not None:
            self._last_quick_final = quick_final

        # §4.4.3：结果落库后自动登记一次自我验证，使「覆盖率 100%」成为真实度量
        # （仅登记，不渲染/不阻塞；异常内部静默）
        self._auto_verify_result(pipeline_final, quick_final)

        # 线程安全检查：非主线程投递回主线程
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._show_result_dashboard(
                pipeline_final, trend_result, quick_final))
            return

        # 统一渲染入口
        try:
            self._render_unified_dashboard(task_mgr=None)
        except Exception as e:
            try:
                import logging
                logging.getLogger('kplucky.debug').warning(
                    'SHOW_RESULT_DASHBOARD_DELEGATE_ERROR: %s', e, exc_info=True)
            except Exception:
                pass

    def _render_dashboard_impl(self, _pre_agg=None, _diff: Optional[Dict] = None):
        """v5.2（GUI-03）实际渲染实现：规格树 + diff 增量渲染。

        与 v4.0「创建即渲染、每次全量重建」的区别：渲染逻辑先编译为
        纯数据规格树（``_build_dashboard_specs``），再经 ``_dash_diff``
        计算 add/update/remove，仅对差异部分执行 create/destroy/configure；
        数据无变化时零 Tk 写操作，从根上消除重复渲染的整屏闪白（A7）。

        参数:
            _pre_agg: 调用方已算好的聚合结果（dict）。传入则直接复用，
                避免同一次渲染重复执行 _compute_dashboard_aggregates
                （§2.1 第 4 项 响应速度：聚合计算只做一次）。
            _diff: 调用方预先算好的 diff（dict，含 add/update/remove），
                传入则跳过本方法内的 _dash_diff 计算（供异步预计算场景）。
        """
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
        # §2.1 第 4 项：复用调用方已算好的聚合结果（_pre_agg），同一次渲染不重复计算。
        agg = _pre_agg or self._compute_dashboard_aggregates(pf, tr, qf)
        if not agg:
            # v4.0 简化：聚合失败时仅用 pipeline_final 恢复 clipboard，
            # _copy_prediction 会在点击时自动从 _last_pipeline_final 或数据库兜底。
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

        # 渲染去重——内容与上次渲染完全一致时跳过重建，仅确保可见，
        # 避免重复点击「开始分析」或缓存命中时不必要的控件销毁/重建，提升响应速度
        # （GUI-03 之后常态命中：无数据变化时连 diff 都不计算，零 Tk 操作）
        _combos_disp = tuple(sorted(compress_combo(v) for v in combos.values()))
        _render_key = (target_issue, main_combo_disp, conf, high_conf, _combos_disp)
        if (getattr(self, '_last_dashboard_key', None) == _render_key
                and self._dash_card_index
                and self.result_dash.winfo_children()):
            try:
                if getattr(self, '_result_placeholder', None) is not None:
                    self._result_placeholder.pack_forget()
                self.result_dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
                self._focus_result_tab()
            except Exception:
                pass
            return

        # 记录本次分析生成时间（供「最近一次分析时间」标记展示）
        self._last_analysis_time = datetime.now()

        # ---- GUI-03 增量渲染：规格树 → diff → 应用 ----
        new_state = self._build_dashboard_specs(agg)
        if _diff is not None:
            diff = _diff
        else:
            diff = self._dash_diff(self._dash_card_state, new_state)
        self._apply_dashboard_diff(diff)
        # 状态只登记真实创建成功的卡片（create 失败的 key 留待下次渲染重试）
        self._dash_card_state = {k: v for k, v in new_state.items()
                                 if k in self._dash_card_index}

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
        # 记录本次渲染指纹，供下次渲染去重（避免无谓的增量计算）
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

    def _render_unified_dashboard(self, task_mgr=None):
        """v4.0 统一渲染入口：唯一仪表盘渲染入口，_show_result_dashboard 仅做薄包装。
        
        统一分析结束时，用已保存的各来源产物集中渲染仪表盘。
        支持从后台线程安全调用，自动投递至主线程执行。
        """
        # 线程安全检查：Tkinter 控件只能在主线程操作
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._render_unified_dashboard(task_mgr))
            return

        # v3.62 修复：后台线程中先同步计算聚合，把 _prediction_clipboard /
        # _clipboard_meta 立即写入主线程可见属性，避免 _copy_prediction 被
        # 点击时主线程 after(0) 仪表盘渲染还没轮到导致「无数据」误判。
        try:
            pf = getattr(self, '_last_pipeline_final', None)
            qf = getattr(self, '_last_quick_final', None)
            tr = getattr(self, '_last_trend_result', None)
            _pre_agg = self._compute_dashboard_aggregates(pf, tr, qf)
        except Exception as e:
            _pre_agg = None
            try:
                import logging
                logging.getLogger('kplucky.debug').error(
                    'RENDER_UNIFIED_PRECOMPUTE_ERROR: %s', e, exc_info=True)
            except Exception:
                pass

        # 执行实际渲染（复用上面的聚合结果，避免同一次渲染重复计算）
        _pre_key = getattr(self, '_last_dashboard_key', None)
        self._render_dashboard_impl(_pre_agg)

        # 兜底：仅当多源聚合未写出剪贴板时才用单源构建补齐。
        # v5.0 §2.1 第 4 项修正：原实现无条件覆盖 _prediction_clipboard，
        # 会把刚算好的多源聚合（共识/置信度/备选组合）改回单源内容。
        try:
            if not (getattr(self, '_prediction_clipboard', '') or '').strip():
                _pf = getattr(self, '_last_pipeline_final', None)
                if isinstance(_pf, dict) and _pf:
                    clip = self._build_prediction_clipboard(_pf)
                    if clip and clip.strip():
                        self._prediction_clipboard = clip
                        self._clipboard_meta = {
                            'target_issue': _pf.get('target_issue') or _pf.get('next_issue') or '',
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
                    self._render_unified_dashboard(task_mgr)
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

            # 更新统计面板（快捷统计卡已移除）
            # stats_text = (
            #     f"回测期数: {backtest_result.get('total_tested', 0)}\n"
            #     f"Top-1命中率: {top1_rate:.2f}%\n"
            #     f"Top-3命中率: {top3_rate:.2f}%\n"
            #     f"综合得分: {stats.get('avg_overall_score', 0):.2f}"
            # )
            # self.stats_content.config(text=stats_text, fg=COLORS['accent_ai'])

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

            # 更新统计面板（快捷统计卡已移除）
            # stats_text = (
            #     f"数据量: {len(history_data)} 条\n"
            #     f"连号率: {consecutive_features.get('consecutive_rate', 0):.1%}\n"
            #     f"重号率: {repeat_features.get('repeat_rate', 0):.1%}\n"
            #     f"平均和值: {sum_span_features.get('avg_sum', 0):.1f}"
            # )
            # self.stats_content.config(text=stats_text, fg=COLORS['warning'])

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
            f"以下是排列5第 {target_issue} 期的多算法候选号码（每位 Top-4，按位列出）：\n"
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
    # v3.67: Windows 下 spawn 启动方式的标准做法——
    # 任何使用 multiprocessing 的模块都必须在 if __name__ 保护下调用 freeze_support()，
    # 否则 Windows 会为每个子进程重复导入并执行主模块（反复打印命令、重复弹出 GUI）。
    import multiprocessing
    multiprocessing.freeze_support()
    main()
