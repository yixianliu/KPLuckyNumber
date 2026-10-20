# -*- coding: utf-8 -*-
"""
cli_log_sink.py — 命令行/stdout/stderr 实时日志收集器

功能：
    把 Python 进程的 stdout / stderr 输出，以及标准库 logging 的所有记录，
    统一捕获并推入一个线程安全的 ``queue.Queue``，供 GUI 在独立标签页中实时渲染，
    实现「命令行日志与输出的可视化集成」（需求 #3：命令行输出界面实时展示）。

设计原则：
    1. 非侵入：原始 stdout/stderr 仍照常输出（Tee 模式），不影响文件日志、不吞输出。
    2. 线程安全：``queue.Queue`` 原生线程安全；写入回显用独立锁保护缓冲。
    3. 幂等：``install_cli_sink()`` 可重复调用，仅在首次真正安装，避免重复接线。
    4. 零依赖：仅用标准库（sys / logging / queue / threading），可在无头环境安全 import。
    5. 失败静默：任何异常都不应阻断宿主程序（GUI）启动。

与诚实边界的关系：
    本模块只做「展示」，不改变任何预测逻辑、不触碰冻结权重、不写入任何 DB/Redis，
    纯属 UI/UX 增强（需求 #3），与排列5 公平摇号的诚实边界完全独立。
"""

import sys
import logging
import queue
import re
import threading

# 单例：全局唯一的日志队列与安装状态
_CLI_QUEUE = queue.Queue()
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()

# 成功级别判定用的「OK」词边界正则：避免 'TOKEN' / 'BROOKLYN' 等词被误判为 success
_OK_WORD = re.compile(r'\bOK\b')

# GUI 端单次轮询最多消费的行数（防止极端突发刷屏卡顿主线程）
MAX_DRAIN_PER_POLL = 200
# GUI 端日志缓冲上限（行数），超出从头部裁剪，避免内存无限增长
MAX_BUFFER_LINES = 5000


def get_cli_queue():
    """返回命令行日志队列（供 GUI 轮询消费）。"""
    return _CLI_QUEUE


def _infer_level(text, default_level):
    """从文本推断日志级别标签（info/warning/error/success）。

    Args:
        text: 单行日志文本。
        default_level: 无法推断时的回退级别（stdout→info / stderr→warning）。
    """
    if not text:
        return default_level
    up = text.upper()
    if any(k in up for k in ('TRACEBACK', 'CRITICAL', '❌', 'ERROR', '异常', 'FATAL')):
        return 'error'
    if any(k in up for k in ('WARNING', 'WARN', '⚠', '警告')):
        return 'warning'
    if ('SUCCESS' in up) or ('✅' in text) or ('完成' in text) \
            or ('就绪' in text) or bool(_OK_WORD.search(up)):
        return 'success'
    return default_level


class _TeeStream:
    """包装原始流：回显到原流 + 按行推入 CLI 队列。"""

    def __init__(self, original, stream_name):
        self._original = original
        self._stream_name = stream_name
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, data):
        # 1) 原样回显（保持文件日志 / 终端行为不变）
        try:
            self._original.write(data)
        except Exception:
            pass
        # 2) 按行切分推入队列，残留不足一行的部分留在缓冲
        if data:
            with self._lock:
                self._buf += data
                while '\n' in self._buf:
                    line, self._buf = self._buf.split('\n', 1)
                    default = 'warning' if self._stream_name == 'stderr' else 'info'
                    _CLI_QUEUE.put((_infer_level(line, default), line.rstrip('\r')))
        return len(data) if data else 0

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self):
        return getattr(self._original, 'isatty', lambda: False)()

    def fileno(self):
        # 某些库（如 colorama）会探测 fileno；透传原流，失败则抛（调用方自行兜底）
        return self._original.fileno()


class _QueueLogHandler(logging.Handler):
    """把 logging 记录推入 CLI 队列（不写文件、不回显到终端，避免与 Tee 重复）。"""

    _LEVEL_TAG = {
        logging.DEBUG: 'info',
        logging.INFO: 'info',
        logging.WARNING: 'warning',
        logging.ERROR: 'error',
        logging.CRITICAL: 'error',
    }

    def emit(self, record):
        try:
            tag = self._LEVEL_TAG.get(record.levelno, 'info')
            msg = self.format(record)
            _CLI_QUEUE.put((tag, msg))
        except Exception:
            # 绝不让日志处理器异常冒泡影响业务
            pass


def install_cli_sink():
    """安装 CLI 日志收集器（幂等，线程安全）。

    效果：
        - sys.stdout / sys.stderr 被 Tee 包装，输出同时进入 CLI 队列。
        - 向 root logger 追加一个 ``_QueueLogHandler``，所有 logging 记录进入 CLI 队列。

    Returns:
        bool: True 表示本次真正完成安装；False 表示此前已安装（幂等跳过）。
    """
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return False
        # 1) 包装 stdout / stderr
        if not isinstance(sys.stdout, _TeeStream):
            sys.stdout = _TeeStream(sys.stdout, 'stdout')
        if not isinstance(sys.stderr, _TeeStream):
            sys.stderr = _TeeStream(sys.stderr, 'stderr')
        # 2) 追加 logging 队列处理器（仅捕获记录，不重复回显终端）
        _handler = _QueueLogHandler()
        try:
            _handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                                                    '%H:%M:%S'))
        except Exception:
            pass
        logging.getLogger().addHandler(_handler)
        _INSTALLED = True
        # 安装完成提示（自身也会被捕获并在 GUI 显示）
        _CLI_QUEUE.put(('success', '> CLI 实时日志收集器已启动，命令行输出将在此实时展示。'))
        return True


def has_pending() -> bool:
    """队列中是否还有未消费的日志行（GUI-04：供渲染前快速判定）。

    GUI 在空闲期调用它为假时直接返回，不 drain、不触碰 Tk，
    对应验收 A11「无数据变化渲染类回调 0 次」。
    """
    return not _CLI_QUEUE.empty()


def drain_cli_lines(max_lines=MAX_DRAIN_PER_POLL):
    """从队列中批量取出日志行，返回 [(level, text), ...]。

    Args:
        max_lines: 单次最多取出行数（防止突发刷屏阻塞主线程）。
    """
    lines = []
    for _ in range(max_lines):
        try:
            lines.append(_CLI_QUEUE.get_nowait())
        except queue.Empty:
            break
    return lines
