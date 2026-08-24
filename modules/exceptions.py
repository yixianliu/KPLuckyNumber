"""
统一异常体系与错误码定义

职责：
    定义项目统一的异常层级、错误码、结构化错误信息。
    所有模块抛出异常应继承自 KPLuckyNumberError，便于上层统一捕获、日志记录、监控告警。

设计原则：
    - 异常携带错误码、用户友好消息、技术细节、上下文信息
    - 支持链式异常（__cause__/__context__）
    - 兼容 structlog 结构化日志记录
    - 错误码分类：1xxx=配置/环境, 2xxx=数据库, 3xxx=AI/外部API, 4xxx=业务逻辑, 5xxx=系统/资源
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import traceback


# ============ 错误码常量 ============
class ErrorCodes:
    """统一错误码定义（参考 HTTP 状态码风格）"""
    
    # 1xxx - 配置与环境错误
    CONFIG_MISSING = 1001          # 关键配置缺失
    CONFIG_INVALID = 1002          # 配置值无效
    ENV_NOT_SET = 1003             # 环境变量未设置
    DEPENDENCY_MISSING = 1004      # 依赖包缺失
    
    # 2xxx - 数据库错误
    DB_CONNECTION_FAILED = 2001    # 数据库连接失败
    DB_QUERY_FAILED = 2002         # 查询执行失败
    DB_TRANSACTION_FAILED = 2003   # 事务失败
    DB_POOL_EXHAUSTED = 2004       # 连接池耗尽
    DB_DATA_INTEGRITY = 2005       # 数据完整性错误
    DB_MIGRATION_FAILED = 2006     # 表结构迁移失败
    
    # 3xxx - AI/外部 API 错误
    AI_API_TIMEOUT = 3001          # AI API 超时
    AI_API_RATE_LIMIT = 3002       # AI API 限流
    AI_API_AUTH_FAILED = 3003      # AI API 认证失败
    AI_API_INVALID_RESPONSE = 3004 # AI API 返回格式错误
    AI_JSON_PARSE_FAILED = 3005    # AI 返回 JSON 解析失败
    EXTERNAL_API_FAILED = 3006     # 其他外部 API 调用失败
    
    # 4xxx - 业务逻辑错误
    PREDICTION_FAILED = 4001       # 预测生成失败
    BACKTEST_FAILED = 4002         # 回测执行失败
    CRAWLER_FAILED = 4003          # 爬虫采集失败
    VALIDATION_FAILED = 4004       # 数据验证失败
    INSUFFICIENT_DATA = 4005       # 历史数据不足
    MODEL_NOT_TRAINED = 4006       # 模型未训练
    
    # 5xxx - 系统/资源错误
    RESOURCE_EXHAUSTED = 5001      # 资源耗尽(内存/磁盘/CPU)
    FILE_IO_ERROR = 5002           # 文件读写失败
    CACHE_ERROR = 5003             # 缓存操作失败
    TASK_TIMEOUT = 5004            # 任务超时
    CONCURRENCY_ERROR = 5005       # 并发冲突
    
    # 9xxx - 未分类/兜底
    UNKNOWN_ERROR = 9999           # 未知错误


# ============ 基础异常类 ============
class KPLuckyNumberError(Exception):
    """项目统一基础异常类
    
    所有业务异常应继承此类，携带结构化错误信息。
    
    Attributes:
        code: 错误码 (ErrorCodes 中定义)
        message: 用户友好的错误消息
        details: 技术细节字典（用于调试/日志）
        context: 上下文信息（如请求参数、操作阶段等）
        cause: 原始异常对象（支持异常链）
    """
    
    def __init__(
        self,
        message: str,
        code: int = ErrorCodes.UNKNOWN_ERROR,
        details: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.context = context or {}
        self.cause = cause
        
        # 自动捕获调用栈
        self._traceback = traceback.format_exc() if cause else None
    
    def to_dict(self) -> Dict[str, Any]:
        """转为结构化字典（用于日志/监控/返回前端）"""
        return {
            'error_code': self.code,
            'error_name': self.__class__.__name__,
            'message': self.message,
            'details': self.details,
            'context': self.context,
            'cause': str(self.cause) if self.cause else None,
            'traceback': self._traceback,
        }
    
    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code}, message='{self.message}')"


# ============ 具体异常类 ============
class ConfigError(KPLuckyNumberError):
    """配置相关错误"""
    def __init__(self, message: str, code: int = ErrorCodes.CONFIG_INVALID, **kwargs):
        super().__init__(message, code, **kwargs)


class ConfigMissingError(ConfigError):
    """关键配置缺失"""
    def __init__(self, config_key: str, **kwargs):
        super().__init__(
            f"关键配置缺失: {config_key}",
            code=ErrorCodes.CONFIG_MISSING,
            details={'config_key': config_key},
            **kwargs
        )


class DatabaseError(KPLuckyNumberError):
    """数据库操作错误"""
    def __init__(self, message: str, code: int = ErrorCodes.DB_QUERY_FAILED, **kwargs):
        super().__init__(message, code, **kwargs)


class DBConnectionError(DatabaseError):
    """数据库连接失败"""
    def __init__(self, message: str = "数据库连接失败", **kwargs):
        super().__init__(message, code=ErrorCodes.DB_CONNECTION_FAILED, **kwargs)


class DBPoolExhaustedError(DatabaseError):
    """连接池耗尽"""
    def __init__(self, message: str = "数据库连接池耗尽", **kwargs):
        super().__init__(message, code=ErrorCodes.DB_POOL_EXHAUSTED, **kwargs)


class AIError(KPLuckyNumberError):
    """AI/外部 API 调用错误"""
    def __init__(self, message: str, code: int = ErrorCodes.EXTERNAL_API_FAILED, **kwargs):
        super().__init__(message, code, **kwargs)


class AITimeoutError(AIError):
    """AI API 超时"""
    def __init__(self, message: str = "AI API 调用超时", **kwargs):
        super().__init__(message, code=ErrorCodes.AI_API_TIMEOUT, **kwargs)


class AIRateLimitError(AIError):
    """AI API 限流"""
    def __init__(self, message: str = "AI API 调用频率限制", **kwargs):
        super().__init__(message, code=ErrorCodes.AI_API_RATE_LIMIT, **kwargs)


class AIAuthError(AIError):
    """AI API 认证失败"""
    def __init__(self, message: str = "AI API 认证失败，请检查 API Key", **kwargs):
        super().__init__(message, code=ErrorCodes.AI_API_AUTH_FAILED, **kwargs)


class AIResponseParseError(AIError):
    """AI 返回解析失败"""
    def __init__(self, message: str = "AI 返回格式错误，无法解析", raw_response: str = '', **kwargs):
        super().__init__(
            message, 
            code=ErrorCodes.AI_JSON_PARSE_FAILED,
            details={'raw_response_preview': raw_response[:500] if raw_response else ''},
            **kwargs
        )


class PredictionError(KPLuckyNumberError):
    """预测生成错误"""
    def __init__(self, message: str, code: int = ErrorCodes.PREDICTION_FAILED, **kwargs):
        super().__init__(message, code, **kwargs)


class BacktestError(KPLuckyNumberError):
    """回测执行错误"""
    def __init__(self, message: str, code: int = ErrorCodes.BACKTEST_FAILED, **kwargs):
        super().__init__(message, code, **kwargs)


class CrawlerError(KPLuckyNumberError):
    """爬虫采集错误"""
    def __init__(self, message: str, code: int = ErrorCodes.CRAWLER_FAILED, **kwargs):
        super().__init__(message, code, **kwargs)


class ValidationError(KPLuckyNumberError):
    """数据验证错误"""
    def __init__(self, message: str, code: int = ErrorCodes.VALIDATION_FAILED, **kwargs):
        super().__init__(message, code, **kwargs)


class InsufficientDataError(KPLuckyNumberError):
    """历史数据不足"""
    def __init__(self, message: str = "历史数据不足，无法执行操作", required: int = 0, actual: int = 0, **kwargs):
        super().__init__(
            message,
            code=ErrorCodes.INSUFFICIENT_DATA,
            details={'required': required, 'actual': actual},
            **kwargs
        )


class ResourceError(KPLuckyNumberError):
    """系统资源错误"""
    def __init__(self, message: str, code: int = ErrorCodes.RESOURCE_EXHAUSTED, **kwargs):
        super().__init__(message, code, **kwargs)


class TaskTimeoutError(KPLuckyNumberError):
    """任务超时"""
    def __init__(self, message: str = "任务执行超时", timeout_seconds: float = 0, **kwargs):
        super().__init__(
            message,
            code=ErrorCodes.TASK_TIMEOUT,
            details={'timeout_seconds': timeout_seconds},
            **kwargs
        )


class CacheError(KPLuckyNumberError):
    """缓存操作错误"""
    def __init__(self, message: str, code: int = ErrorCodes.CACHE_ERROR, **kwargs):
        super().__init__(message, code, **kwargs)


# ============ 异常处理工具 ============
def handle_exception(
    e: Exception,
    default_code: int = ErrorCodes.UNKNOWN_ERROR,
    context: Optional[Dict[str, Any]] = None,
    reraise: bool = True
) -> KPLuckyNumberError:
    """统一异常处理：将任意异常转为 KPLuckyNumberError
    
    Args:
        e: 原始异常
        default_code: 默认错误码
        context: 附加上下文
        reraise: 是否重新抛出
        
    Returns:
        转换后的 KPLuckyNumberError
    """
    if isinstance(e, KPLuckyNumberError):
        # 已是项目异常，合并上下文
        if context:
            e.context.update(context)
        if reraise:
            raise
        return e
    
    # 根据异常类型推断错误码
    code = default_code
    if isinstance(e, (ConnectionError, TimeoutError)):
        code = ErrorCodes.DB_CONNECTION_FAILED
    elif isinstance(e, PermissionError):
        code = ErrorCodes.CONFIG_INVALID
    elif isinstance(e, FileNotFoundError):
        code = ErrorCodes.FILE_IO_ERROR
    elif isinstance(e, MemoryError):
        code = ErrorCodes.RESOURCE_EXHAUSTED
    
    wrapped = KPLuckyNumberError(
        message=str(e),
        code=code,
        details={'original_type': type(e).__name__},
        context=context or {},
        cause=e
    )
    
    if reraise:
        raise wrapped from e
    return wrapped


def safe_execute(
    func,
    *args,
    default_return=None,
    error_code: int = ErrorCodes.UNKNOWN_ERROR,
    context: Optional[Dict[str, Any]] = None,
    log_errors: bool = True,
    **kwargs
):
    """安全执行函数，捕获异常并返回默认值
    
    Args:
        func: 要执行的函数
        *args, **kwargs: 传给函数的参数
        default_return: 异常时返回的默认值
        error_code: 异常时的错误码
        context: 上下文信息
        log_errors: 是否记录错误日志
        
    Returns:
        函数返回值或 default_return
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        wrapped = handle_exception(e, error_code, context, reraise=False)
        if log_errors:
            from modules.logging_utils import get_logger
            logger = get_logger(func.__module__)
            logger.error(f"安全执行失败: {wrapped}", extra={'error': wrapped.to_dict()})
        return default_return


# ============ 结果包装类 ============
@dataclass
class Result:
    """统一结果包装（参考 Rust Result / Go 多返回值模式）
    
    用于替代抛异常的流程控制，明确成功/失败路径。
    """
    success: bool
    data: Any = None
    error: Optional[KPLuckyNumberError] = None
    
    @classmethod
    def ok(cls, data: Any = None) -> 'Result':
        return cls(success=True, data=data)
    
    @classmethod
    def err(cls, error: KPLuckyNumberError) -> 'Result':
        return cls(success=False, error=error)
    
    def unwrap(self) -> Any:
        """获取数据，失败时抛出异常"""
        if not self.success:
            raise self.error
        return self.data
    
    def unwrap_or(self, default: Any) -> Any:
        """获取数据，失败时返回默认值"""
        return self.data if self.success else default
    
    def map(self, func) -> 'Result':
        """成功时应用函数转换数据"""
        if self.success:
            try:
                return Result.ok(func(self.data))
            except Exception as e:
                return Result.err(handle_exception(e, context={'stage': 'map'}))
        return self
    
    def map_err(self, func) -> 'Result':
        """失败时应用函数转换错误"""
        if not self.success:
            try:
                return Result.err(func(self.error))
            except Exception as e:
                return Result.err(handle_exception(e, context={'stage': 'map_err'}))
        return self


# ============ 重试装饰器 ============
import functools
import time
import random

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retry_on: tuple = (Exception,),
    should_retry=None
):
    """重试装饰器（指数退避 + 抖动）
    
    Args:
        max_attempts: 最大尝试次数
        base_delay: 基础延迟(秒)
        max_delay: 最大延迟(秒)
        exponential_base: 指数基数
        jitter: 是否添加随机抖动
        retry_on: 触发重试的异常类型元组
        should_retry: 可选的自定义判断函数 exception -> bool
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    
                    # 自定义重试判断
                    if should_retry and not should_retry(e):
                        raise
                    
                    if attempt == max_attempts:
                        break
                    
                    # 计算延迟
                    delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
                    if jitter:
                        delay *= (0.5 + random.random())  # 0.5-1.5x 抖动
                    
                    from modules.logging_utils import get_logger
                    logger = get_logger(func.__module__)
                    logger.warning(
                        f"重试 {attempt}/{max_attempts} 后执行 {func.__name__}: {e}, "
                        f"等待 {delay:.2f}s",
                        extra={'attempt': attempt, 'max_attempts': max_attempts, 'delay': delay}
                    )
                    time.sleep(delay)
            
            # 所有重试失败
            raise KPLuckyNumberError(
                f"重试 {max_attempts} 次后仍失败: {last_exception}",
                code=ErrorCodes.UNKNOWN_ERROR,
                cause=last_exception
            ) from last_exception
        
        return wrapper
    return decorator


# ============ 导出 ============
__all__ = [
    'ErrorCodes',
    'KPLuckyNumberError',
    'ConfigError', 'ConfigMissingError',
    'DatabaseError', 'DBConnectionError', 'DBPoolExhaustedError',
    'AIError', 'AITimeoutError', 'AIRateLimitError', 'AIAuthError', 'AIResponseParseError',
    'PredictionError', 'BacktestError', 'CrawlerError',
    'ValidationError', 'InsufficientDataError',
    'ResourceError', 'TaskTimeoutError', 'CacheError',
    'handle_exception', 'safe_execute',
    'Result', 'retry',
]