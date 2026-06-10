"""
自定义异常类和异常处理器
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from datetime import datetime
import traceback

# ==================== 自定义异常类 ====================

class ApiException(Exception):
    """API自定义异常基类"""
    def __init__(self, code: int, message: str, detail: str = None):
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)

class DatabaseException(ApiException):
    """数据库操作异常"""
    def __init__(self, message: str, detail: str = None):
        super().__init__(500, message, detail)

class CrawlException(ApiException):
    """数据爬取异常"""
    def __init__(self, message: str, detail: str = None):
        super().__init__(503, message, detail)

class ValidationException(ApiException):
    """数据验证异常"""
    def __init__(self, message: str, detail: str = None):
        super().__init__(400, message, detail)

class ResourceNotFoundException(ApiException):
    """资源未找到异常"""
    def __init__(self, message: str, detail: str = None):
        super().__init__(404, message, detail)

# ==================== 异常处理器 ====================

def api_exception_handler(request: Request, exc: ApiException):
    """API自定义异常处理器"""
    return JSONResponse(
        status_code=exc.code,
        content={
            "success": False,
            "code": exc.code,
            "message": exc.message,
            "detail": exc.detail,
            "timestamp": datetime.now().isoformat()
        }
    )

def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP异常处理器"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "code": exc.status_code,
            "message": exc.detail,
            "detail": None,
            "timestamp": datetime.now().isoformat()
        }
    )

def general_exception_handler(request: Request, exc: Exception):
    """通用异常处理器"""
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "code": 500,
            "message": "服务器内部错误",
            "detail": traceback.format_exc()[:500],
            "timestamp": datetime.now().isoformat()
        }
    )

def setup_exception_handlers(app):
    """注册异常处理器"""
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)