"""
结构化日志工具模块

职责：
    提供统一的结构化日志记录接口，支持：
    - JSON 格式输出（便于 ELK/Splunk 等日志平台接入）
    - 结构化字段（trace_id, span_id, user_id, module, operation 等）
    - 日志级别控制、文件轮转、控制台彩色输出
    - 与标准 logging 兼容，无缝替换现有 logger 用法

使用示例：
    from modules.logging_utils import get_logger, LogContext
    
    logger = get_logger(__name__)
    
    # 普通日志
    logger.info("预测开始", extra={'issue': '2026221', 'algo_count': 8})
    
    # 结构化日志（自动提取 extra 字段）
    with LogContext(trace_id='abc-123', operation='predict'):
        logger.info("预测完成", extra={'duration_ms': 1234, 'top1': '37903'})
    
    # 异常日志
    try:
        risky_operation()
    except Exception as e:
        logger.exception("预测失败", extra={'error_code': 4001, 'issue': '2026221'})
"""

import logging
import logging.handlers
import json
import sys
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Union
from pathlib import Path
from contextlib import contextmanager

from paths import LOGS_DIR

# 版本号从 version.py 统一获取
try:
    from version import APP_VERSION as _APP_VERSION
except Exception:
    _APP_VERSION = 'v3.59'


# ============ 线程上下文存储 ============
# 用于在日志中自动注入 trace_id, span_id 等追踪字段
class LogContext:
    """日志上下文管理器（线程安全）
    
    使用 threading.local 存储上下文，支持嵌套使用。
    退出时自动恢复父级上下文。
    """
    _local = threading.local()
    
    def __init__(self, **kwargs):
        """初始化上下文变量
        
        Args:
            **kwargs: 要注入日志的字段，如 trace_id, span_id, user_id, operation, module 等
        """
        self.new_context = kwargs
        self.old_context = None
    
    def __enter__(self):
        # 获取当前上下文栈
        if not hasattr(self._local, 'context_stack'):
            self._local.context_stack = []
        
        # 保存当前上下文
        self.old_context = getattr(self._local, 'current_context', {})
        
        # 合并新上下文（新值覆盖旧值）
        merged = {**self.old_context, **self.new_context}
        self._local.current_context = merged
        self._local.context_stack.append(self.old_context)
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 恢复父级上下文
        if hasattr(self._local, 'context_stack') and self._local.context_stack:
            self._local.context_stack.pop()
        self._local.current_context = self.old_context
        return False
    
    @classmethod
    def get_current(cls) -> Dict[str, Any]:
        """获取当前线程的日志上下文"""
        return getattr(cls._local, 'current_context', {}).copy()
    
    @classmethod
    def set(cls, **kwargs):
        """直接设置当前上下文（不推荐，优先使用 with 语句）"""
        if not hasattr(cls._local, 'context_stack'):
            cls._local.context_stack = []
        cls._local.current_context = kwargs
    
    @classmethod
    def clear(cls):
        """清空当前上下文"""
        if hasattr(cls._local, 'current_context'):
            cls._local.current_context = {}


# ============ JSON 格式化器 ============
class JSONFormatter(logging.Formatter):
    """结构化 JSON 日志格式化器
    
    输出格式示例：
    {
        "timestamp": "2026-08-19T10:30:45.123Z",
        "level": "INFO",
        "logger": "modules.predictor",
        "message": "预测完成",
        "trace_id": "abc-123",
        "span_id": "span-456",
        "operation": "predict",
        "duration_ms": 1234,
        "top1": "37903"
    }
    """
    
    # 标准字段映射
    STANDARD_FIELDS = {
        'timestamp', 'level', 'logger', 'message',
        'module', 'funcName', 'lineno', 'thread', 'threadName', 'process'
    }
    
    def __init__(self, include_traceback: bool = True, **extra_fields):
        """
        Args:
            include_traceback: 是否在 ERROR 级别包含完整堆栈
            **extra_fields: 静态额外字段（如 service_name, version, environment）
        """
        super().__init__()
        self.include_traceback = include_traceback
        self.static_fields = extra_fields
    
    def format(self, record: logging.LogRecord) -> str:
        # 基础字段
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'funcName': record.funcName,
            'lineno': record.lineno,
            'thread': record.thread,
            'threadName': record.threadName,
            'process': record.process,
        }
        
        # 注入线程上下文
        context = LogContext.get_current()
        if context:
            log_data.update(context)
        
        # 合并 extra 字段（record.__dict__ 中非标准字段）
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_FIELDS and not key.startswith('_'):
                # 处理特殊类型
                if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
                    log_data[key] = value
                else:
                    log_data[key] = str(value)
        
        # 合并静态字段
        log_data.update(self.static_fields)
        
        # 异常信息
        if record.exc_info and self.include_traceback:
            log_data['exception'] = self.formatException(record.exc_info)
        elif record.exc_info:
            log_data['exception_type'] = record.exc_info[0].__name__ if record.exc_info[0] else None
            log_data['exception_message'] = str(record.exc_info[1]) if record.exc_info[1] else None
        
        return json.dumps(log_data, ensure_ascii=False, default=str)


# ============ 控制台彩色格式化器 ============
class ColorConsoleFormatter(logging.Formatter):
    """控制台彩色日志格式化器（开发环境友好）"""
    
    COLORS = {
        'DEBUG': '\033[36m',     # 青色
        'INFO': '\033[32m',      # 绿色
        'WARNING': '\033[33m',   # 黄色
        'ERROR': '\033[31m',     # 红色
        'CRITICAL': '\033[35m',  # 紫色
    }
    RESET = '\033[0m'
    
    def __init__(self, use_colors: bool = True):
        super().__init__(
            fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%H:%M:%S'
        )
        self.use_colors = use_colors and sys.stderr.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        # 注入上下文到 message
        context = LogContext.get_current()
        if context:
            ctx_str = ' '.join(f'{k}={v}' for k, v in context.items())
            record.msg = f"{record.msg} [{ctx_str}]"
        
        formatted = super().format(record)
        
        if self.use_colors:
            color = self.COLORS.get(record.levelname, '')
            return f"{color}{formatted}{self.RESET}"
        return formatted


# ============ 日志配置与获取 ============
_default_config = {
    'level': 'INFO',
    'json_output': False,           # 生产环境建议开启
    'console_output': True,
    'file_output': True,
    'max_bytes': 10 * 1024 * 1024,  # 10MB
    'backup_count': 30,
    'log_dir': LOGS_DIR,
}


def configure_logging(
    level: Union[str, int] = 'INFO',
    json_output: bool = False,
    console_output: bool = True,
    file_output: bool = True,
    log_dir: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 30,
    service_name: str = 'kpluckynumber',
    version: str = 'unknown',
    environment: str = 'development'
) -> None:
    """全局日志配置（应在程序启动时调用一次）
    
    Args:
        level: 日志级别
        json_output: 是否启用 JSON 格式（文件）
        console_output: 是否输出到控制台
        file_output: 是否输出到文件
        log_dir: 日志目录
        max_bytes: 单文件最大字节数
        backup_count: 保留文件数
        service_name: 服务名（注入到所有日志）
        version: 版本号
        environment: 环境标识
    """
    # 解析级别
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    
    # 确保日志目录存在
    log_dir = Path(log_dir or LOGS_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 静态字段注入所有日志
    static_fields = {
        'service': service_name,
        'version': version,
        'environment': environment,
    }
    
    # 根 logger 配置
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # 清除现有 handlers（避免重复）
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
    
    # 控制台 handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(ColorConsoleFormatter(use_colors=True))
        root_logger.addHandler(console_handler)
    
    # 文件 handler (JSON)
    if file_output and json_output:
        json_handler = logging.handlers.RotatingFileHandler(
            log_dir / 'app.json.log',
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        json_handler.setLevel(level)
        json_handler.setFormatter(JSONFormatter(
            include_traceback=True,
            **static_fields
        ))
        root_logger.addHandler(json_handler)
    
    # 文件 handler (人类可读)
    if file_output and not json_output:
        text_handler = logging.handlers.RotatingFileHandler(
            log_dir / 'app.log',
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        text_handler.setLevel(level)
        text_handler.setFormatter(logging.Formatter(
            fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        root_logger.addHandler(text_handler)
    
    # 设置第三方库日志级别（避免噪音）
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('pymysql').setLevel(logging.WARNING)
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    
    logging.info(f"日志系统初始化完成: level={logging.getLevelName(level)}, json={json_output}")


def get_logger(name: str) -> logging.Logger:
    """获取模块 logger（推荐方式）
    
    Args:
        name: 通常传 __name__
        
    Returns:
        配置好的 logger 实例
    """
    return logging.getLogger(name)


# ============ 便捷函数 ============
@contextmanager
def log_operation(
    logger: logging.Logger,
    operation: str,
    level: int = logging.INFO,
    **context_fields
):
    """记录操作开始/结束的上下文管理器
    
    用法：
        with log_operation(logger, 'predict', issue='2026221') as ctx:
            result = do_predict()
            ctx.extra['duration_ms'] = 1234
    """
    trace_id = str(uuid.uuid4())[:8]
    start_time = time.perf_counter()
    
    ctx = LogContext(trace_id=trace_id, operation=operation, **context_fields)
    ctx.__enter__()
    
    class OpContext:
        def __init__(self):
            self.extra = {}
        
        def add_field(self, key, value):
            self.extra[key] = value
    
    op_ctx = OpContext()
    
    logger.log(level, f"{operation} 开始", extra=context_fields)
    
    try:
        yield op_ctx
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        extra = {**context_fields, **op_ctx.extra, 'duration_ms': duration_ms, 'status': 'success'}
        logger.log(level, f"{operation} 完成", extra=extra)
    except Exception as e:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        extra = {**context_fields, **op_ctx.extra, 'duration_ms': duration_ms, 'status': 'failed', 'error': str(e)}
        logger.exception(f"{operation} 失败", extra=extra)
        raise
    finally:
        ctx.__exit__(None, None, None)


def log_function_call(logger: logging.Logger):
    """函数调用日志装饰器（记录入参、耗时、结果/异常）"""
    import functools
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with log_operation(logger, func.__name__, module=func.__module__) as ctx:
                # 记录入参（脱敏）
                safe_args = _sanitize_args(args)
                safe_kwargs = _sanitize_kwargs(kwargs)
                if safe_args or safe_kwargs:
                    ctx.add_field('args', safe_args)
                    ctx.add_field('kwargs', safe_kwargs)
                
                result = func(*args, **kwargs)
                
                # 记录返回值概要
                if result is not None:
                    ctx.add_field('result_type', type(result).__name__)
                    if hasattr(result, '__len__'):
                        ctx.add_field('result_len', len(result))
                return result
        return wrapper
    return decorator


def _sanitize_args(args) -> list:
    """脱敏参数（移除敏感字段）"""
    sensitive_keys = {'password', 'api_key', 'secret', 'token', 'key'}
    result = []
    for arg in args:
        if isinstance(arg, dict):
            result.append({k: '***' if k.lower() in sensitive_keys else v for k, v in arg.items()})
        elif hasattr(arg, '__dict__'):
            result.append({k: '***' if k.lower() in sensitive_keys else v for k, v in arg.__dict__.items()})
        else:
            result.append(arg)
    return result


def _sanitize_kwargs(kwargs) -> dict:
    """脱敏关键字参数"""
    sensitive_keys = {'password', 'api_key', 'secret', 'token', 'key'}
    return {k: '***' if k.lower() in sensitive_keys else v for k, v in kwargs.items()}


# ============ 性能指标记录 ============
class MetricsLogger:
    """性能指标记录器（用于关键路径埋点）"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
    
    def timing(self, operation: str, **tags):
        """记录耗时的上下文管理器"""
        return log_operation(self.logger, f"timing.{operation}", level=logging.DEBUG, **tags)
    
    def gauge(self, name: str, value: float, **tags):
        """记录 Gauge 指标"""
        self.logger.info(f"metric.gauge", extra={
            'metric_type': 'gauge',
            'metric_name': name,
            'metric_value': value,
            **tags
        })
    
    def counter(self, name: str, value: int = 1, **tags):
        """记录 Counter 指标"""
        self.logger.info(f"metric.counter", extra={
            'metric_type': 'counter',
            'metric_name': name,
            'metric_value': value,
            **tags
        })
    
    def histogram(self, name: str, value: float, **tags):
        """记录 Histogram 指标"""
        self.logger.info(f"metric.histogram", extra={
            'metric_type': 'histogram',
            'metric_name': name,
            'metric_value': value,
            **tags
        })


def get_metrics_logger(name: str) -> MetricsLogger:
    """获取指标记录器"""
    return MetricsLogger(get_logger(name))


# ============ 初始化（模块导入时自动配置基础日志）==========
# 延迟配置：首次 get_logger 时自动配置
_configured = False

def _ensure_configured():
    global _configured
    if not _configured:
        # 从环境变量读取配置
        json_output = os.getenv('LOG_JSON_OUTPUT', 'false').lower() == 'true'
        level = os.getenv('LOG_LEVEL', 'INFO')
        configure_logging(
            level=level,
            json_output=json_output,
            service_name='kpluckynumber',
            version=os.getenv('APP_VERSION') or _APP_VERSION,
            environment=os.getenv('ENVIRONMENT', 'development')
        )
        _configured = True

# 修补 get_logger 自动配置
_original_get_logger = get_logger
def get_logger(name: str) -> logging.Logger:
    _ensure_configured()
    return _original_get_logger(name)


# ============ 导出 ============
__all__ = [
    'LogContext',
    'JSONFormatter',
    'ColorConsoleFormatter',
    'configure_logging',
    'get_logger',
    'log_operation',
    'log_function_call',
    'MetricsLogger',
    'get_metrics_logger',
]