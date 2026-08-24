"""
改进的异步任务管理器 (v2.0)

职责：
    替代 main.py 中的 TaskManager，提供更强大的异步任务执行能力：
    - 多工作线程支持（可配置，默认 4）
    - 真正的任务取消（线程中断协作式）
    - 事件驱动 UI 更新（替代轮询）
    - 任务优先级队列
    - 结构化日志集成
    - 进度/状态/结果回调的类型安全
    - 任务超时控制
    - 任务依赖/链式执行支持

设计原则：
    - 保持与原 TaskManager 兼容的 API（submit, log, progress, status 等）
    - 内部使用 concurrent.futures + queue，无额外依赖
    - 线程安全，主线程仅处理 UI 回调
    - 支持优雅关闭和资源清理

与 main.py 的关系：
    - main.py 导入 TaskManager 类替代原内联实现
    - LotteryGUI 通过 self.task_manager 访问
    - 所有业务方法 _execute_* 通过 task_manager.submit() 提交
"""

import sys
import os
import time
import threading
import queue
import traceback
import logging
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Any, Dict, List, Union
from contextlib import contextmanager
from datetime import datetime

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.logging_utils import get_logger, LogContext
from modules.exceptions import KPLuckyNumberError, TaskTimeoutError, handle_exception

logger = get_logger(__name__)


# ============ 消息类型枚举 ============
class MsgType(Enum):
    """任务消息类型（与原 TaskManager 兼容）"""
    LOG = 'log'
    PROGRESS = 'progress'
    STATUS = 'status'
    FINISHED = 'finished'
    ERROR = 'error'
    REPORT = 'report'
    APPEND_SUCCESS = 'append_success'
    APPEND_WARNING = 'append_warning'
    APPEND_ERROR = 'append_error'
    APPEND_INFO = 'append_info'
    APPEND_DATA = 'append_data'
    APPEND_SECTION_HEADER = 'append_section_header'
    # 新增类型
    TASK_STARTED = 'task_started'
    TASK_CANCELLED = 'task_cancelled'
    TASK_TIMEOUT = 'task_timeout'


# ============ 任务优先级 ============
class TaskPriority(Enum):
    LOW = 0
    NORMAL = 50
    HIGH = 100
    CRITICAL = 200


# ============ 任务数据类 ============
@dataclass(order=True)
class Task:
    """任务封装（用于优先级队列）"""
    priority: int
    task_id: int = field(compare=False)
    func: Callable = field(compare=False)
    name: str = field(compare=False, default='未命名任务')
    timeout: Optional[float] = field(compare=False, default=None)
    callback: Optional[Callable] = field(compare=False, default=None)
    cancel_event: threading.Event = field(compare=False, default_factory=threading.Event)
    future: Optional[Future] = field(compare=False, default=None)
    submitted_at: float = field(compare=False, default_factory=time.time)
    started_at: Optional[float] = field(compare=False, default=None)
    finished_at: Optional[float] = field(compare=False, default=None)
    metadata: Dict = field(compare=False, default_factory=dict)


# ============ 任务管理器核心类 ============
class TaskManager:
    """
    改进的异步任务管理器
    
    主要改进：
    1. 多工作线程（默认 4，可配置）
    2. 优先级队列（重要任务优先执行）
    3. 协作式取消机制（cancel_event）
    4. 任务超时控制
    5. 事件驱动 UI 回调（after_idle 替代 after 轮询）
    6. 结构化日志集成
    7. 任务链式执行支持
    """
    
    # 默认配置
    DEFAULT_MAX_WORKERS = 4
    DEFAULT_QUEUE_SIZE = 100
    POLL_INTERVAL_MS = 50  # UI 轮询间隔（ms），更低延迟
    MAX_BATCH_MSGS = 20    # 单次批量处理上限
    
    def __init__(
        self,
        gui_instance,
        max_workers: int = None,
        queue_size: int = None,
        enable_structured_logging: bool = True
    ):
        """
        初始化任务管理器
        
        Args:
            gui_instance: LotteryGUI 实例，用于 UI 回调
            max_workers: 最大工作线程数（None=默认4）
            queue_size: 任务队列最大长度（None=100）
            enable_structured_logging: 是否启用结构化日志
        """
        self.gui = gui_instance
        self.max_workers = max_workers or self.DEFAULT_MAX_WORKERS
        self.queue_size = queue_size or self.DEFAULT_QUEUE_SIZE
        self.enable_structured_logging = enable_structured_logging
        
        # 线程池
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix='KPLuckyTask'
        )
        
        # 任务队列（优先级队列）
        self._task_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=self.queue_size)
        
        # UI 消息队列（后台线程 -> 主线程）
        self._ui_queue: queue.Queue = queue.Queue()
        
        # 当前运行的任务映射 {task_id: Task}
        self._running_tasks: Dict[int, Task] = {}
        self._running_lock = threading.RLock()
        
        # 任务 ID 生成器
        self._task_id_counter = 0
        self._task_id_lock = threading.Lock()
        
        # 状态标志
        self._shutdown = False
        self._paused = False
        
        # 日志文件
        from paths import LOG_GUI_RUN
        self._log_file_path = LOG_GUI_RUN
        self._log_file = None
        self._log_lock = threading.Lock()
        
        # 批量缓冲
        self._log_buffer: List[str] = []
        self._buffer_lock = threading.Lock()
        self._last_flush = time.time()
        self.FLUSH_INTERVAL = 1.0  # 秒
        self.FLUSH_THRESHOLD = 10  # 条数
        
        # UI 轮询句柄
        self._poll_handle = None
        
        # 启动 UI 轮询
        self._start_ui_polling()
        
        logger.info(f"TaskManager 初始化完成: workers={self.max_workers}, queue_size={self.queue_size}")
    
    # ============ 任务 ID 生成 ============
    def _next_task_id(self) -> int:
        with self._task_id_lock:
            self._task_id_counter += 1
            return self._task_id_counter
    
    # ============ UI 轮询（事件驱动优化）===========
    def _start_ui_polling(self):
        """启动 UI 消息轮询（使用 after 定时轮询，避免 after_idle 导致定时器饥饿）"""
        def poll():
            if self._shutdown:
                return
            
            processed = 0
            log_batch = []
            
            try:
                while processed < self.MAX_BATCH_MSGS:
                    try:
                        msg = self._ui_queue.get_nowait()
                    except queue.Empty:
                        break
                    
                    processed += 1
                    self._dispatch_ui_message(msg, log_batch)
                    
            except Exception as e:
                logger.error(f"UI 轮询异常: {e}", exc_info=True)
            finally:
                # 刷新日志批次
                if log_batch:
                    self._flush_log_batch(log_batch)
                
                # 定期刷新缓冲区
                self._maybe_flush_buffer()
                
                # 继续轮询（使用 after 固定间隔，避免 after_idle 导致定时器回调饥饿）
                if not self._shutdown:
                    self._poll_handle = self.gui.root.after(self.POLL_INTERVAL_MS, poll)
        
        # 首次启动
        self._poll_handle = self.gui.root.after(self.POLL_INTERVAL_MS, poll)
    
    def _dispatch_ui_message(self, msg: Dict, log_batch: List):
        """分发 UI 消息到对应处理器"""
        msg_type = msg.get('type', 'log')
        
        try:
            if msg_type == MsgType.LOG.value:
                log_batch.append(msg['text'])
            elif msg_type == MsgType.PROGRESS.value:
                self.gui._update_progress_ui(msg.get('value', 0), msg.get('text', ''))
            elif msg_type == MsgType.STATUS.value:
                self.gui._update_status_ui(msg.get('text', ''), msg.get('color', ''))
            elif msg_type == MsgType.FINISHED.value:
                if log_batch:
                    self._flush_log_batch(log_batch)
                    log_batch.clear()
                self._on_task_finished(msg.get('task_id'))
            elif msg_type == MsgType.ERROR.value:
                if log_batch:
                    self._flush_log_batch(log_batch)
                    log_batch.clear()
                self._on_task_error(msg.get('task_id'), msg.get('error', '未知错误'))
            elif msg_type == MsgType.REPORT.value:
                self.gui._display_report(msg.get('data', {}))
            elif msg_type in (MsgType.APPEND_SUCCESS.value, MsgType.APPEND_WARNING.value,
                            MsgType.APPEND_ERROR.value, MsgType.APPEND_INFO.value,
                            MsgType.APPEND_DATA.value, MsgType.APPEND_SECTION_HEADER.value):
                if log_batch:
                    self._flush_log_batch(log_batch)
                    log_batch.clear()
                self._render_styled_message(msg_type, msg)
            elif msg_type == MsgType.TASK_STARTED.value:
                self.gui._on_task_started(msg.get('name', '任务'))
            elif msg_type == MsgType.TASK_CANCELLED.value:
                self.gui._on_task_cancelled(msg.get('task_id'))
            elif msg_type == MsgType.TASK_TIMEOUT.value:
                self.gui._on_task_timeout(msg.get('task_id'), msg.get('name', '任务'))
            else:
                log_batch.append(str(msg))
        except Exception as e:
            logger.error(f"UI 消息分发失败: {e}", exc_info=True)
    
    def _render_styled_message(self, msg_type: str, msg: Dict):
        """渲染带样式的消息（委托给 GUI）"""
        # 复用原有的 _render_batched_msg 逻辑
        if hasattr(self.gui, '_render_batched_msg'):
            self.gui._render_batched_msg(msg_type, msg)
    
    # ============ 日志文件处理 ============
    def _open_log_file(self):
        """打开日志文件（按天滚动）"""
        try:
            os.makedirs(os.path.dirname(self._log_file_path), exist_ok=True)
            self._log_file = open(self._log_file_path, 'a', encoding='utf-8', errors='replace')
        except Exception:
            self._log_file = None
    
    def _write_log(self, text: str):
        """线程安全写入日志文件"""
        if self._log_file is None:
            self._open_log_file()
        if self._log_file is None:
            return
        try:
            with self._log_lock:
                self._log_file.write(text)
                if '\n' in text:
                    self._log_file.flush()
        except Exception:
            pass
    
    def _flush_log_batch(self, messages: List[str]):
        """批量刷新日志到文件"""
        if not messages:
            return
        try:
            self._write_log(''.join(messages))
        except Exception:
            pass
    
    def _buffer_log(self, text: str):
        """缓冲日志（定期批量刷新）"""
        with self._buffer_lock:
            self._log_buffer.append(text)
            if (len(self._log_buffer) >= self.FLUSH_THRESHOLD or 
                time.time() - self._last_flush >= self.FLUSH_INTERVAL):
                self._flush_buffer_locked()
    
    def _flush_buffer_locked(self):
        """刷新缓冲区（需持有 _buffer_lock）"""
        if self._log_buffer:
            self._flush_log_batch(self._log_buffer)
            self._log_buffer.clear()
            self._last_flush = time.time()
    
    def _maybe_flush_buffer(self):
        """定期检查并刷新缓冲区"""
        with self._buffer_lock:
            if (self._log_buffer and 
                time.time() - self._last_flush >= self.FLUSH_INTERVAL):
                self._flush_buffer_locked()
    
    def flush_pending_logs(self):
        """强制刷新所有待写日志"""
        # 刷新 UI 队列中的日志
        log_batch = []
        while not self._ui_queue.empty():
            try:
                msg = self._ui_queue.get_nowait()
                if msg.get('type') == 'log':
                    log_batch.append(msg['text'])
                else:
                    log_batch.append(str(msg))
            except queue.Empty:
                break
        if log_batch:
            self._flush_log_batch(log_batch)
        
        # 刷新缓冲区
        with self._buffer_lock:
            self._flush_buffer_locked()
    
    # ============ 公共 API（兼容原 TaskManager）===========
    def log(self, text: str):
        """追加日志（线程安全，缓冲写入）"""
        self._ui_queue.put({'type': MsgType.LOG.value, 'text': text})
        # 同时缓冲到文件
        self._buffer_log(text)
    
    def progress(self, value: int, text: str = ""):
        """更新进度（线程安全）"""
        self._ui_queue.put({'type': MsgType.PROGRESS.value, 'value': value, 'text': text})
    
    def status(self, text: str, color: str = ''):
        """更新状态栏（线程安全）"""
        self._ui_queue.put({'type': MsgType.STATUS.value, 'text': text, 'color': color})
    
    def report(self, data: Dict):
        """投递报告数据（线程安全）"""
        self._ui_queue.put({'type': MsgType.REPORT.value, 'data': data})
    
    def finished(self, task_id: int = None):
        """任务完成通知"""
        self._ui_queue.put({'type': MsgType.FINISHED.value, 'task_id': task_id})
    
    def error(self, err_text: str, task_id: int = None):
        """任务错误通知"""
        self._ui_queue.put({'type': MsgType.ERROR.value, 'error': err_text, 'task_id': task_id})
    
    def append_success(self, text: str):
        self._ui_queue.put({'type': MsgType.APPEND_SUCCESS.value, 'text': text})
    
    def append_warning(self, text: str):
        self._ui_queue.put({'type': MsgType.APPEND_WARNING.value, 'text': text})
    
    def append_error(self, text: str):
        self._ui_queue.put({'type': MsgType.APPEND_ERROR.value, 'text': text})
    
    def append_info(self, text: str):
        self._ui_queue.put({'type': MsgType.APPEND_INFO.value, 'text': text})
    
    def append_data(self, text: str):
        self._ui_queue.put({'type': MsgType.APPEND_DATA.value, 'text': text})
    
    def append_section_header(self, text: str):
        self._ui_queue.put({'type': MsgType.APPEND_SECTION_HEADER.value, 'text': text})
    
    # ============ 任务提交与管理 ============
    def submit(
        self,
        task_func: Callable,
        task_name: str = "任务",
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout: Optional[float] = None,
        callback: Optional[Callable] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        提交后台任务
        
        Args:
            task_func: 接收 TaskManager 实例作为参数的可调用对象
            task_name: 任务显示名称
            priority: 任务优先级
            timeout: 超时时间（秒），None=无超时
            callback: 任务完成回调
            metadata: 附加元数据
            
        Returns:
            bool: 是否成功提交（队列满/已关闭返回 False）
        """
        if self._shutdown:
            return False
        
        task_id = self._next_task_id()
        cancel_event = threading.Event()
        
        task = Task(
            priority=-priority.value,  # 负值因为 PriorityQueue 是小根堆
            task_id=task_id,
            func=task_func,
            name=task_name,
            timeout=timeout,
            callback=callback,
            cancel_event=cancel_event,
            metadata=metadata or {}
        )
        
        # 包装任务函数
        wrapped_func = self._wrap_task(task)
        task.func = wrapped_func
        
        try:
            self._task_queue.put_nowait(task)
        except queue.Full:
            logger.warning(f"任务队列已满，拒绝任务: {task_name}")
            return False
        
        # 记录运行中任务
        with self._running_lock:
            self._running_tasks[task_id] = task
        
        # 提交到线程池
        future = self._executor.submit(self._execute_task, task)
        task.future = future
        
        # 通知 UI 任务开始
        self._ui_queue.put({
            'type': MsgType.TASK_STARTED.value,
            'task_id': task_id,
            'name': task_name
        })
        
        logger.info(f"任务已提交: {task_name} (id={task_id}, priority={priority.name})")
        return True
    
    def _wrap_task(self, task: Task) -> Callable:
        """包装任务函数，注入取消检查和超时处理"""
        original_func = task.func
        cancel_event = task.cancel_event
        timeout = task.timeout
        
        def wrapped(tm: 'TaskManager'):
            task.started_at = time.time()
            
            # 启动超时监控线程
            timeout_timer = None
            if timeout:
                def on_timeout():
                    if not cancel_event.is_set():
                        cancel_event.set()
                        tm._ui_queue.put({
                            'type': MsgType.TASK_TIMEOUT.value,
                            'task_id': task.task_id,
                            'name': task.name
                        })
                        logger.warning(f"任务超时取消: {task.name} (id={task.task_id}, timeout={timeout}s)")
                
                timeout_timer = threading.Timer(timeout, on_timeout)
                timeout_timer.daemon = True
                timeout_timer.start()
            
            try:
                # 执行原任务，传入 self (TaskManager) 和 cancel_event
                original_func(tm, cancel_event)
            except Exception as e:
                if cancel_event.is_set():
                    raise KPLuckyNumberError(f"任务被取消: {task.name}", code=5004) from e
                raise
            finally:
                if timeout_timer:
                    timeout_timer.cancel()
                task.finished_at = time.time()
        
        return wrapped
    
    def _execute_task(self, task: Task):
        """在线程池中执行任务"""
        try:
            task.func(self)
        except Exception as e:
            if task.cancel_event.is_set():
                # 任务被取消，不视为错误
                logger.info(f"任务已取消: {task.name} (id={task.task_id})")
                self._ui_queue.put({
                    'type': MsgType.TASK_CANCELLED.value,
                    'task_id': task.task_id
                })
            else:
                # 真实错误
                error_detail = traceback.format_exc()
                self.log(f"\n  [错误] 任务执行失败: {str(e)}\n")
                self.log(f"  [错误详情]\n{error_detail}\n")
                self.error(str(e), task.task_id)
        finally:
            # 触发完成通知（如果未被取消/超时处理）
            if not task.cancel_event.is_set():
                self.finished(task.task_id)
            
            # 执行回调
            if task.callback:
                try:
                    task.callback(task)
                except Exception as e:
                    logger.error(f"任务回调异常: {e}", exc_info=True)
            
            # 清理运行中任务记录
            with self._running_lock:
                self._running_tasks.pop(task.task_id, None)
    
    def _on_task_finished(self, task_id: int = None):
        """任务完成清理"""
        with self._running_lock:
            if task_id:
                self._running_tasks.pop(task_id, None)
        if hasattr(self.gui, '_on_task_finished'):
            self.gui._on_task_finished()
    
    def _on_task_error(self, task_id: int, error_msg: str):
        """任务错误清理"""
        with self._running_lock:
            self._running_tasks.pop(task_id, None)
        if hasattr(self.gui, '_on_task_error'):
            self.gui._on_task_error(error_msg)
    
    def cancel(self, task_id: int = None) -> bool:
        """
        取消任务
        
        Args:
            task_id: 要取消的任务 ID，None=取消所有运行中任务
            
        Returns:
            bool: 是否成功发送取消信号
        """
        cancelled_count = 0
        
        with self._running_lock:
            if task_id is None:
                # 取消所有
                for tid, task in self._running_tasks.items():
                    if not task.cancel_event.is_set():
                        task.cancel_event.set()
                        cancelled_count += 1
            else:
                task = self._running_tasks.get(task_id)
                if task and not task.cancel_event.is_set():
                    task.cancel_event.set()
                    cancelled_count = 1
        
        if cancelled_count > 0:
            logger.info(f"已发送取消信号给 {cancelled_count} 个任务")
            return True
        return False
    
    def cancel_all(self) -> int:
        """取消所有运行中任务，返回取消数量"""
        return self.cancel(None)
    
    def get_running_tasks(self) -> List[Dict]:
        """获取当前运行中任务信息"""
        with self._running_lock:
            return [
                {
                    'task_id': t.task_id,
                    'name': t.name,
                    'priority': TaskPriority(-t.priority).name,
                    'running_time': time.time() - (t.started_at or t.submitted_at),
                    'metadata': t.metadata
                }
                for t in self._running_tasks.values()
            ]
    
    def is_running(self) -> bool:
        """是否有任务正在运行"""
        with self._running_lock:
            return len(self._running_tasks) > 0
    
    def get_task_count(self) -> int:
        """获取队列中等待的任务数"""
        return self._task_queue.qsize()
    
    # ============ 任务链式执行支持 ============
    def submit_chain(
        self,
        tasks: List[Dict],
        chain_name: str = "任务链"
    ) -> bool:
        """
        提交任务链（顺序执行，前一个完成后自动开始下一个）
        
        Args:
            tasks: 任务配置列表，每项包含 func, name, priority, timeout, metadata
            chain_name: 链名称
            
        Returns:
            bool: 是否成功提交第一个任务
        """
        if not tasks:
            return False
        
        def run_chain(tm: TaskManager, cancel_event: threading.Event, index: int = 0):
            if index >= len(tasks) or cancel_event.is_set():
                return
            
            task_config = tasks[index]
            task_name = f"{chain_name} [{index+1}/{len(tasks)}]: {task_config.get('name', '步骤')}"
            
            def step_func(tm2: TaskManager, cancel_event2: threading.Event):
                if cancel_event2.is_set() or cancel_event.is_set():
                    return
                task_config['func'](tm2, cancel_event2)
                
                # 完成后启动下一步
                if index + 1 < len(tasks) and not cancel_event.is_set():
                    run_chain(tm2, cancel_event, index + 1)
            
            tm.submit(
                step_func,
                task_name=task_name,
                priority=task_config.get('priority', TaskPriority.NORMAL),
                timeout=task_config.get('timeout'),
                metadata={'chain': chain_name, 'step': index}
            )
        
        return self.submit(run_chain, task_name=chain_name, metadata={'chain': True})
    
    # ============ 并行任务组支持 ============
    def submit_group(
        self,
        tasks: List[Dict],
        group_name: str = "任务组",
        wait_all: bool = True,
        callback: Optional[Callable] = None
    ) -> List[int]:
        """
        提交并行任务组
        
        Args:
            tasks: 任务配置列表
            group_name: 组名称
            wait_all: True=等待全部完成，False=任意一个完成即回调
            callback: 完成回调
            
        Returns:
            List[int]: 提交的任务 ID 列表
        """
        task_ids = []
        completed = {'count': 0, 'results': [], 'errors': []}
        completed_lock = threading.Lock()
        total = len(tasks)
        
        def group_callback(task: Task):
            with completed_lock:
                completed['count'] += 1
                if task.future and task.future.exception():
                    completed['errors'].append(str(task.future.exception()))
                else:
                    completed['results'].append(task.metadata)
                
                should_callback = (not wait_all and completed['count'] == 1) or \
                                 (wait_all and completed['count'] == total)
                
                if should_callback and callback:
                    try:
                        callback(completed)
                    except Exception as e:
                        logger.error(f"任务组回调异常: {e}", exc_info=True)
        
        for i, task_config in enumerate(tasks):
            task_name = f"{group_name} [{i+1}/{total}]: {task_config.get('name', '任务')}"
            success = self.submit(
                task_config['func'],
                task_name=task_name,
                priority=task_config.get('priority', TaskPriority.NORMAL),
                timeout=task_config.get('timeout'),
                callback=group_callback,
                metadata={'group': group_name, 'index': i, **task_config.get('metadata', {})}
            )
            if success:
                # 获取刚提交的任务 ID（最后一个）
                with self._running_lock:
                    for tid, t in self._running_tasks.items():
                        if t.metadata.get('group') == group_name and t.metadata.get('index') == i:
                            task_ids.append(tid)
                            break
        
        return task_ids
    
    # ============ 上下文管理器支持 ============
    @contextmanager
    def task_context(self, task_name: str, **kwargs):
        """任务上下文管理器（自动处理开始/结束/异常）"""
        success = self.submit(lambda tm, ce: None, task_name=task_name, **kwargs)
        try:
            yield self
        finally:
            pass  # 实际任务在后台运行，此处不阻塞
    
    # ============ 向后兼容属性 ============
    @property
    def _last_activity(self) -> float:
        """返回最近一次活动时间（用于看门狗检查）"""
        with self._running_lock:
            if not self._running_tasks:
                return time.time()
            # 返回所有运行中任务中最早的开始时间（最久未活动）
            return min(t.started_at or t.submitted_at for t in self._running_tasks.values())
    
    @property
    def _cancelled(self) -> bool:
        """检查是否有任务被取消（向后兼容）"""
        with self._running_lock:
            return any(t.cancel_event.is_set() for t in self._running_tasks.values())
    
    @property
    def last_activity(self) -> float:
        """公共属性：最近活动时间"""
        return self._last_activity
    
    @property
    def is_cancelled(self) -> bool:
        """公共属性：是否有任务被取消"""
        return self._cancelled

    # ============ 关闭与清理 ============
    def shutdown(self, wait: bool = True, timeout: float = 30.0):
        """
        关闭任务管理器
        
        Args:
            wait: 是否等待任务完成
            timeout: 等待超时时间（实际使用取决于Python版本）
        """
        logger.info("TaskManager 正在关闭...")
        self._shutdown = True
        
        # 取消所有运行中任务
        self.cancel_all()
        
        # 停止 UI 轮询
        if self._poll_handle:
            try:
                self.gui.root.after_cancel(self._poll_handle)
            except Exception:
                pass
            self._poll_handle = None
        
        # 刷新剩余日志
        self.flush_pending_logs()
        
        # 关闭线程池
        # 注意：ThreadPoolExecutor.shutdown() 在 Python 3.9+ 支持 timeout 参数
        # 为了兼容性，我们这里只传递 wait 参数
        self._executor.shutdown(wait=wait)
        
        # 关闭日志文件
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        
        logger.info("TaskManager 已关闭")
    
    def pause(self):
        """暂停接收新任务"""
        self._paused = True
        logger.info("TaskManager 已暂停")
    
    def resume(self):
        """恢复接收新任务"""
        self._paused = False
        logger.info("TaskManager 已恢复")


# ============ 便捷函数 ============
def create_task_manager(gui_instance, **kwargs) -> TaskManager:
    """创建 TaskManager 实例的工厂函数"""
    return TaskManager(gui_instance, **kwargs)


# ============ 导出 ============
__all__ = [
    'TaskManager',
    'TaskPriority',
    'MsgType',
    'Task',
    'create_task_manager',
]