"""
数据采集与管理路由
严格遵循数据库表结构定义
"""

from fastapi import APIRouter, Query, Depends
from typing import List, Optional
from datetime import datetime
import json

# 导入内部模块
from modules.spider import QXCSpider
from modules.data_cleaner import DataCleaner
from modules.database import Database
from api.schemas import (
    ApiResponse, LotteryDataItem, PaginatedResponse, CrawlRequest,
    LotteryDataCreateRequest, LotteryDataUpdateRequest, TrendDataItem, TrendDataCreateRequest
)
from api.exceptions import DatabaseException, CrawlException

router = APIRouter()

@router.post("/crawl", summary="爬取开奖数据", description="从官方网站爬取七星彩历史开奖数据")
async def crawl_data(request: CrawlRequest):
    """
    爬取七星彩历史开奖数据
    
    Args:
        qishu: 获取期数（1-500）
        trend: 是否获取走势图数据
    
    Returns:
        爬取结果统计信息
    """
    try:
        spider = QXCSpider()
        cleaner = DataCleaner()
        database = Database()
        
        # 爬取数据
        raw_data = spider.crawl_history_data()
        if not raw_data:
            raise CrawlException("爬取数据失败", "未能从服务器获取数据")
        
        # 获取走势图数据
        trend_data = []
        if request.trend:
            trend_data = spider.crawl_trend_data(record=120)
        
        # 数据清洗
        clean_data = cleaner.clean(raw_data)
        
        # 存储到数据库
        if database.connect():
            database.create_tables()
            count = database.insert_or_update_qxc_data(clean_data)
            total = database.get_qxc_data_count()
            
            if trend_data:
                trend_count = database.insert_or_update_trend_data(trend_data)
            database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="数据爬取成功",
            data={
                "crawled_count": len(raw_data),
                "cleaned_count": len(clean_data),
                "stored_count": count,
                "total_in_db": total,
                "trend_data_count": len(trend_data),
                "crawl_time": datetime.now().isoformat()
            }
        )
    
    except CrawlException as e:
        raise e
    except Exception as e:
        raise CrawlException(f"爬取过程发生错误: {str(e)}", str(e))

@router.get("/list", summary="获取开奖数据列表", description="分页获取七星彩历史开奖数据")
async def get_data_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    sort_by: str = Query("issue", description="排序字段"),
    sort_order: str = Query("desc", description="排序顺序")
):
    """
    分页获取七星彩历史开奖数据
    
    Args:
        page: 页码
        page_size: 每页数量
        sort_by: 排序字段（issue/draw_date）
        sort_order: 排序顺序（asc/desc）
    
    Returns:
        分页数据列表
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        # 排序
        if sort_by in ["issue", "draw_date"]:
            data.sort(key=lambda x: x[sort_by], reverse=(sort_order == "desc"))
        
        # 分页
        total = len(data)
        start = (page - 1) * page_size
        end = start + page_size
        items = data[start:end]
        
        # 转换为响应格式
        formatted_items = []
        for item in items:
            formatted_items.append(LotteryDataItem(**item).dict())
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data=PaginatedResponse(
                items=formatted_items,
                total=total,
                page=page,
                page_size=page_size,
                pages=(total + page_size - 1) // page_size
            )
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询数据失败: {str(e)}", str(e))

@router.get("/{issue}", summary="获取单期数据", description="根据期号获取单期开奖数据")
async def get_data_by_issue(issue: str):
    """
    根据期号获取单期开奖数据
    
    Args:
        issue: 期号
    
    Returns:
        单期开奖数据详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 查询数据
        database.cursor.execute(
            "SELECT * FROM qxc_history_data WHERE issue = %s",
            (issue,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        if not result:
            return ApiResponse(
                success=False,
                code=404,
                message="数据未找到",
                data=None
            )
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data=LotteryDataItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询数据失败: {str(e)}", str(e))

@router.post("/", summary="新增开奖数据", description="新增一条七星彩开奖数据")
async def create_data(request: LotteryDataCreateRequest):
    """
    新增一条七星彩开奖数据
    
    Args:
        request: 开奖数据请求体
    
    Returns:
        创建的开奖数据详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 检查期号是否已存在
        database.cursor.execute(
            "SELECT id FROM qxc_history_data WHERE issue = %s",
            (request.issue,)
        )
        existing = database.cursor.fetchone()
        if existing:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=409,
                message="期号已存在",
                data=None
            )
        
        # 插入数据
        sql = '''
        INSERT INTO qxc_history_data 
        (issue, draw_date, num1, num2, num3, num4, num5, num6, special_num, 
         hezhi, hezhi_type, odd_even_ratio, odd_even_pattern, span)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        '''
        
        database.cursor.execute(sql, (
            request.issue,
            request.draw_date,
            request.num1,
            request.num2,
            request.num3,
            request.num4,
            request.num5,
            request.num6,
            request.special_num,
            request.hezhi,
            request.hezhi_type,
            request.odd_even_ratio,
            request.odd_even_pattern,
            request.span
        ))
        
        database.connection.commit()
        
        # 获取插入的数据
        database.cursor.execute(
            "SELECT * FROM qxc_history_data WHERE issue = %s",
            (request.issue,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=201,
            message="创建成功",
            data=LotteryDataItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"创建数据失败: {str(e)}", str(e))

@router.put("/{issue}", summary="更新开奖数据", description="根据期号更新开奖数据")
async def update_data(issue: str, request: LotteryDataUpdateRequest):
    """
    根据期号更新开奖数据
    
    Args:
        issue: 期号
        request: 更新数据请求体
    
    Returns:
        更新后的开奖数据详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 检查数据是否存在
        database.cursor.execute(
            "SELECT id FROM qxc_history_data WHERE issue = %s",
            (issue,)
        )
        existing = database.cursor.fetchone()
        if not existing:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=404,
                message="数据未找到",
                data=None
            )
        
        # 构建更新语句
        update_fields = []
        update_values = []
        
        if request.draw_date is not None:
            update_fields.append("draw_date = %s")
            update_values.append(request.draw_date)
        if request.num1 is not None:
            update_fields.append("num1 = %s")
            update_values.append(request.num1)
        if request.num2 is not None:
            update_fields.append("num2 = %s")
            update_values.append(request.num2)
        if request.num3 is not None:
            update_fields.append("num3 = %s")
            update_values.append(request.num3)
        if request.num4 is not None:
            update_fields.append("num4 = %s")
            update_values.append(request.num4)
        if request.num5 is not None:
            update_fields.append("num5 = %s")
            update_values.append(request.num5)
        if request.num6 is not None:
            update_fields.append("num6 = %s")
            update_values.append(request.num6)
        if request.special_num is not None:
            update_fields.append("special_num = %s")
            update_values.append(request.special_num)
        if request.hezhi is not None:
            update_fields.append("hezhi = %s")
            update_values.append(request.hezhi)
        if request.hezhi_type is not None:
            update_fields.append("hezhi_type = %s")
            update_values.append(request.hezhi_type)
        if request.odd_even_ratio is not None:
            update_fields.append("odd_even_ratio = %s")
            update_values.append(request.odd_even_ratio)
        if request.odd_even_pattern is not None:
            update_fields.append("odd_even_pattern = %s")
            update_values.append(request.odd_even_pattern)
        if request.span is not None:
            update_fields.append("span = %s")
            update_values.append(request.span)
        
        if not update_fields:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=400,
                message="未提供更新字段",
                data=None
            )
        
        update_values.append(issue)
        sql = f"UPDATE qxc_history_data SET {', '.join(update_fields)} WHERE issue = %s"
        database.cursor.execute(sql, tuple(update_values))
        database.connection.commit()
        
        # 获取更新后的数据
        database.cursor.execute(
            "SELECT * FROM qxc_history_data WHERE issue = %s",
            (issue,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="更新成功",
            data=LotteryDataItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"更新数据失败: {str(e)}", str(e))

@router.delete("/{issue}", summary="删除单期数据", description="根据期号删除单期开奖数据")
async def delete_data_by_issue(issue: str):
    """
    根据期号删除单期开奖数据
    
    Args:
        issue: 期号
    
    Returns:
        删除结果
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 删除数据
        database.cursor.execute(
            "DELETE FROM qxc_history_data WHERE issue = %s",
            (issue,)
        )
        affected_rows = database.cursor.rowcount
        database.connection.commit()
        database.disconnect()
        
        if affected_rows == 0:
            return ApiResponse(
                success=False,
                code=404,
                message="数据未找到",
                data=None
            )
        
        return ApiResponse(
            success=True,
            code=200,
            message="删除成功",
            data={"deleted_issue": issue, "affected_rows": affected_rows}
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"删除数据失败: {str(e)}", str(e))

@router.get("/summary", summary="获取数据概览", description="获取数据库中数据统计概览")
async def get_data_summary():
    """
    获取数据库中数据统计概览
    
    Returns:
        数据统计信息
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 查询数据数量
        history_count = database.get_history_data_count()
        
        # 查询走势图数据数量
        try:
            database.cursor.execute('SELECT COUNT(*) as count FROM qxc_trend_data')
            trend_result = database.cursor.fetchone()
            trend_count = trend_result['count'] if trend_result else 0
        except:
            trend_count = 0
        
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "history_data_count": history_count,
                "trend_data_count": trend_count
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询数据失败: {str(e)}", str(e))

# ==================== 走势图数据接口 ====================

@router.get("/trend/list", summary="获取走势图数据列表", description="分页获取走势图数据")
async def get_trend_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量")
):
    """
    分页获取走势图数据
    
    Args:
        page: 页码
        page_size: 每页数量
    
    Returns:
        分页走势图数据列表
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        database.cursor.execute('SELECT * FROM qxc_trend_data ORDER BY issue DESC')
        data = database.cursor.fetchall()
        database.disconnect()
        
        # 分页
        total = len(data)
        start = (page - 1) * page_size
        end = start + page_size
        items = data[start:end]
        
        # 转换为响应格式
        formatted_items = []
        for item in items:
            formatted_items.append(TrendDataItem(**item).dict())
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data=PaginatedResponse(
                items=formatted_items,
                total=total,
                page=page,
                page_size=page_size,
                pages=(total + page_size - 1) // page_size
            )
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询走势图数据失败: {str(e)}", str(e))

@router.get("/trend/{issue}", summary="获取单期走势图数据", description="根据期号获取走势图数据")
async def get_trend_by_issue(issue: str):
    """
    根据期号获取走势图数据
    
    Args:
        issue: 期号
    
    Returns:
        走势图数据详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        database.cursor.execute(
            "SELECT * FROM qxc_trend_data WHERE issue = %s",
            (issue,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        if not result:
            return ApiResponse(
                success=False,
                code=404,
                message="走势图数据未找到",
                data=None
            )
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data=TrendDataItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询走势图数据失败: {str(e)}", str(e))

@router.post("/trend", summary="新增走势图数据", description="新增一条走势图数据")
async def create_trend(request: TrendDataCreateRequest):
    """
    新增一条走势图数据
    
    Args:
        request: 走势图数据请求体
    
    Returns:
        创建的走势图数据详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 检查期号是否已存在
        database.cursor.execute(
            "SELECT id FROM qxc_trend_data WHERE issue = %s",
            (request.issue,)
        )
        existing = database.cursor.fetchone()
        if existing:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=409,
                message="期号已存在",
                data=None
            )
        
        # 插入数据
        sql = '''
        INSERT INTO qxc_trend_data (issue, trend_values)
        VALUES (%s, %s)
        '''
        database.cursor.execute(sql, (request.issue, request.trend_values))
        database.connection.commit()
        
        # 获取插入的数据
        database.cursor.execute(
            "SELECT * FROM qxc_trend_data WHERE issue = %s",
            (request.issue,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=201,
            message="创建成功",
            data=TrendDataItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"创建走势图数据失败: {str(e)}", str(e))

@router.delete("/trend/{issue}", summary="删除走势图数据", description="根据期号删除走势图数据")
async def delete_trend_by_issue(issue: str):
    """
    根据期号删除走势图数据
    
    Args:
        issue: 期号
    
    Returns:
        删除结果
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        database.cursor.execute(
            "DELETE FROM qxc_trend_data WHERE issue = %s",
            (issue,)
        )
        affected_rows = database.cursor.rowcount
        database.connection.commit()
        database.disconnect()
        
        if affected_rows == 0:
            return ApiResponse(
                success=False,
                code=404,
                message="走势图数据未找到",
                data=None
            )
        
        return ApiResponse(
            success=True,
            code=200,
            message="删除成功",
            data={"deleted_issue": issue, "affected_rows": affected_rows}
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"删除走势图数据失败: {str(e)}", str(e))