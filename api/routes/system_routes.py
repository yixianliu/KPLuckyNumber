"""
系统管理路由
"""

from fastapi import APIRouter, Query
from typing import Dict, Any
from datetime import datetime
import os
import psutil

# 导入内部模块
from modules.database import Database
from api.schemas import ApiResponse
from api.exceptions import DatabaseException

router = APIRouter()

# 记录服务启动时间
START_TIME = datetime.now()

@router.get("/status", summary="系统状态", description="获取系统状态信息")
async def get_system_status():
    """
    获取系统状态信息
    
    Returns:
        系统状态信息
    """
    try:
        # 计算运行时间
        uptime = datetime.now() - START_TIME
        uptime_str = str(uptime).split('.')[0]
        
        # 获取系统资源使用情况
        cpu_usage = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(os.getcwd())
        
        # 数据库连接测试
        db_connected = False
        history_count = 0
        trend_count = 0
        detailed_report_count = 0
        final_report_count = 0
        
        try:
            database = Database()
            db_connected = database.connect()
            
            if db_connected:
                history_count = database.get_qxc_data_count()
                
                try:
                    database.cursor.execute('SELECT COUNT(*) as count FROM qxc_trend_data')
                    trend_result = database.cursor.fetchone()
                    trend_count = trend_result['count'] if trend_result else 0
                except:
                    trend_count = 0
                
                try:
                    database.cursor.execute('SELECT COUNT(*) as count FROM qxc_detailed_report')
                    detailed_report_count = database.cursor.fetchone()['count']
                except:
                    detailed_report_count = 0
                
                try:
                    database.cursor.execute('SELECT COUNT(*) as count FROM qxc_final_report')
                    final_report_count = database.cursor.fetchone()['count']
                except:
                    final_report_count = 0
                
                database.disconnect()
        except Exception as db_err:
            db_connected = False
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "status": "healthy",
                "version": "1.0.0",
                "database_status": "connected" if db_connected else "disconnected",
                "uptime": uptime_str,
                "data_count": {
                    "history_data": history_count,
                    "trend_data": trend_count,
                    "detailed_reports": detailed_report_count,
                    "final_reports": final_report_count
                },
                "system_resources": {
                    "cpu_usage_percent": cpu_usage,
                    "memory_total_gb": round(memory.total / (1024**3), 2),
                    "memory_used_gb": round(memory.used / (1024**3), 2),
                    "memory_usage_percent": memory.percent,
                    "disk_total_gb": round(disk.total / (1024**3), 2),
                    "disk_used_gb": round(disk.used / (1024**3), 2),
                    "disk_usage_percent": disk.percent
                },
                "timestamp": datetime.now().isoformat()
            }
        )
    
    except Exception as e:
        return ApiResponse(
            success=False,
            code=500,
            message="获取系统状态失败",
            data={"error": str(e)}
        )

@router.get("/config", summary="获取配置", description="获取系统配置信息")
async def get_system_config():
    """
    获取系统配置信息
    
    Returns:
        系统配置信息
    """
    try:
        from config import DB_CONFIG, SPIDER_CONFIG, ANALYSIS_CONFIG, REPORT_CONFIG
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "database": {
                    "host": DB_CONFIG['host'],
                    "port": DB_CONFIG['port'],
                    "database": DB_CONFIG['database'],
                    "charset": DB_CONFIG['charset']
                },
                "spider": {
                    "pages": SPIDER_CONFIG['pages'],
                    "timeout": SPIDER_CONFIG['timeout'],
                    "retry_count": SPIDER_CONFIG['retry_count'],
                    "delay_min": SPIDER_CONFIG['delay_min'],
                    "delay_max": SPIDER_CONFIG['delay_max']
                },
                "analysis": {
                    "confidence_threshold": ANALYSIS_CONFIG['confidence_threshold'],
                    "min_data_count": ANALYSIS_CONFIG['min_data_count']
                },
                "report": {
                    "output_dir": REPORT_CONFIG['output_dir'],
                    "chart_format": REPORT_CONFIG['chart_format'],
                    "chart_dpi": REPORT_CONFIG['chart_dpi']
                }
            }
        )
    
    except Exception as e:
        raise DatabaseException(f"获取配置失败: {str(e)}", str(e))

@router.post("/init", summary="初始化数据库", description="初始化数据库表结构")
async def init_database():
    """
    初始化数据库表结构
    
    Returns:
        初始化结果
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        database.create_tables()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="数据库初始化成功",
            data={"tables_created": ["qxc_history_data", "qxc_trend_data", "qxc_detailed_report", "qxc_final_report"]}
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"初始化数据库失败: {str(e)}", str(e))

@router.post("/clean", summary="清理数据", description="清理所有数据（谨慎使用）")
async def clean_all_data(confirm: bool = Query(False, description="确认删除")):
    """
    清理所有数据（谨慎使用）
    
    Args:
        confirm: 确认删除标志
    
    Returns:
        清理结果
    """
    if not confirm:
        return ApiResponse(
            success=False,
            code=400,
            message="请确认删除操作，设置 confirm=true",
            data=None
        )
    
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 删除所有数据
        database.cursor.execute("DELETE FROM qxc_history_data")
        database.cursor.execute("DELETE FROM qxc_trend_data")
        database.cursor.execute("DELETE FROM qxc_detailed_report")
        database.cursor.execute("DELETE FROM qxc_final_report")
        database.connection.commit()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="数据清理成功",
            data={"message": "所有数据已删除"}
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"清理数据失败: {str(e)}", str(e))

@router.get("/logs", summary="获取日志", description="获取最近的日志内容")
async def get_logs(
    log_type: str = Query("database", description="日志类型（database/analyzer）"),
    lines: int = Query(50, ge=1, le=200, description="获取行数")
):
    """
    获取最近的日志内容
    
    Args:
        log_type: 日志类型
        lines: 获取行数
    
    Returns:
        日志内容
    """
    log_files = {
        "database": "logs/database.log",
        "analyzer": "logs/analyzer.log"
    }
    
    if log_type not in log_files:
        return ApiResponse(
            success=False,
            code=400,
            message="不支持的日志类型",
            data={"supported_types": list(log_files.keys())}
        )
    
    try:
        log_path = log_files[log_type]
        
        if not os.path.exists(log_path):
            return ApiResponse(
                success=False,
                code=404,
                message="日志文件不存在",
                data=None
            )
        
        with open(log_path, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "log_type": log_type,
                "total_lines": len(all_lines),
                "returned_lines": len(recent_lines),
                "content": "".join(recent_lines)
            }
        )
    
    except Exception as e:
        return ApiResponse(
            success=False,
            code=500,
            message="读取日志失败",
            data={"error": str(e)}
        )