"""
API路由模块
"""

from .data_routes import router as data_routes
from .analysis_routes import router as analysis_routes
from .report_routes import router as report_routes
from .system_routes import router as system_routes

__all__ = ["data_routes", "analysis_routes", "report_routes", "system_routes"]