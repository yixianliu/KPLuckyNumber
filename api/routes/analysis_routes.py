"""
概率分析路由
严格遵循数据库表结构定义
"""

from fastapi import APIRouter, Query, Depends
from typing import List, Optional, Dict, Any
from datetime import datetime
import json

# 导入内部模块
from modules.database import Database
from modules.analyzer import ProbabilityAnalyzer
from api.schemas import ApiResponse
from api.exceptions import DatabaseException

router = APIRouter()

@router.get("/frequency", summary="号码频率分析", description="分析各号码在每个位置的出现频率")
async def analyze_frequency():
    """
    分析各号码在每个位置的出现频率
    
    Returns:
        频率分析结果，包含各号码在每个位置的出现次数和概率
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行频率分析
        freq_result = analyzer.analyze_frequency(data)
        
        # 格式化结果
        frequency_list = []
        for num, stats in freq_result.items():
            frequency_list.append({
                "number": num,
                "frequency": stats["frequency"],
                "probability": stats["probability"]
            })
        
        # 按概率排序
        frequency_list.sort(key=lambda x: sum(x["probability"]), reverse=True)
        
        return ApiResponse(
            success=True,
            code=200,
            message="分析成功",
            data={
                "total_samples": len(data),
                "frequency_analysis": frequency_list,
                "analysis_time": datetime.now().isoformat()
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))

@router.get("/interval", summary="间隔周期分析", description="分析号码的间隔周期分布")
async def analyze_interval():
    """
    分析号码的间隔周期分布
    
    Returns:
        间隔分析结果，包含号码在各位置的出现次数、平均间隔、最大间隔、最小间隔
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行间隔分析
        interval_result = analyzer.analyze_interval(data)
        
        # 格式化结果
        interval_list = []
        for (num, pos), stats in interval_result.items():
            interval_list.append({
                "number": num,
                "position": pos + 1,
                "count": stats["count"],
                "avg_interval": stats["avg"],
                "max_interval": stats["max"],
                "min_interval": stats["min"]
            })
        
        # 按平均间隔排序（降序）
        interval_list.sort(key=lambda x: x["avg_interval"], reverse=True)
        
        return ApiResponse(
            success=True,
            code=200,
            message="分析成功",
            data={
                "total_samples": len(data),
                "interval_analysis": interval_list[:20],
                "analysis_time": datetime.now().isoformat()
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))

@router.get("/hezhi", summary="和值分析", description="分析和值分布情况")
async def analyze_hezhi():
    """
    分析和值分布情况
    
    Returns:
        和值分析结果，包含和值分布、和值类型分布
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行和值分析
        hezhi_result = analyzer.analyze_hezhi(data)
        
        return ApiResponse(
            success=True,
            code=200,
            message="分析成功",
            data={
                "total_samples": len(data),
                "hezhi_distribution": hezhi_result["hezhi_distribution"],
                "hezhi_type_distribution": hezhi_result["hezhi_type_distribution"],
                "analysis_time": datetime.now().isoformat()
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))

@router.get("/odd_even", summary="奇偶分析", description="分析奇偶比例分布")
async def analyze_odd_even():
    """
    分析奇偶比例分布
    
    Returns:
        奇偶分析结果，包含奇偶比例分布、奇偶模式分布
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行奇偶分析
        odd_even_result = analyzer.analyze_odd_even(data)
        
        return ApiResponse(
            success=True,
            code=200,
            message="分析成功",
            data={
                "total_samples": len(data),
                "ratio_distribution": odd_even_result["ratio_distribution"],
                "pattern_distribution": odd_even_result["pattern_distribution"],
                "analysis_time": datetime.now().isoformat()
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))

@router.get("/span", summary="跨度分析", description="分析跨度分布情况")
async def analyze_span():
    """
    分析跨度分布情况
    
    Returns:
        跨度分析结果，包含跨度分布
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行跨度分析
        span_result = analyzer.analyze_span(data)
        
        return ApiResponse(
            success=True,
            code=200,
            message="分析成功",
            data={
                "total_samples": len(data),
                "span_distribution": span_result,
                "analysis_time": datetime.now().isoformat()
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))

@router.get("/repeats", summary="重号分析", description="分析重号规律")
async def analyze_repeats():
    """
    分析重号规律
    
    Returns:
        重号分析结果，包含重号分布、平均重号数、最大/最小重号数
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行重号分析
        repeat_result = analyzer.analyze_repeats(data)
        
        return ApiResponse(
            success=True,
            code=200,
            message="分析成功",
            data={
                "total_samples": len(data),
                "repeat_distribution": repeat_result["repeat_distribution"],
                "avg_repeats": repeat_result["avg_repeats"],
                "max_repeats": repeat_result["max_repeats"],
                "min_repeats": repeat_result["min_repeats"],
                "analysis_time": datetime.now().isoformat()
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))

@router.get("/consecutive", summary="连号分析", description="分析连号规律")
async def analyze_consecutive():
    """
    分析连号规律
    
    Returns:
        连号分析结果，包含连号分布、平均连号数、最大/最小连号数
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行连号分析
        consecutive_result = analyzer.analyze_consecutive(data)
        
        return ApiResponse(
            success=True,
            code=200,
            message="分析成功",
            data={
                "total_samples": len(data),
                "consecutive_distribution": consecutive_result["consecutive_distribution"],
                "avg_consecutive": consecutive_result["avg_consecutive"],
                "max_consecutive": consecutive_result["max_consecutive"],
                "min_consecutive": consecutive_result["min_consecutive"],
                "analysis_time": datetime.now().isoformat()
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))

@router.get("/predict", summary="号码概率预测", description="基于历史数据进行号码概率预测")
async def predict_numbers(use_trend: bool = Query(True, description="是否使用走势图数据")):
    """
    基于历史数据进行号码概率预测
    
    Args:
        use_trend: 是否使用走势图数据
    
    Returns:
        号码概率预测结果，包含预测号码列表、热门号码、冷门号码、推荐号码
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        
        # 获取走势图数据
        trend_data = []
        if use_trend:
            try:
                database.cursor.execute('SELECT * FROM qxc_trend_data')
                trend_raw = database.cursor.fetchall()
                trend_data = [{'issue': item['issue'], 'trend': json.loads(item['trend_values'])} for item in trend_raw]
            except Exception as e:
                trend_data = []
        
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行概率计算
        result = analyzer.calculate_probability(data, trend_data if use_trend else None)
        
        # 格式化预测结果
        predictions = []
        for num, pred in result["predictions"].items():
            predictions.append({
                "number": num,
                "probability": pred["probability"],
                "confidence": pred["confidence"],
                "trend_factor": pred["trend_factor"],
                "expected_positions": pred["expected_positions"]
            })
        
        # 获取热门和冷门号码
        hot_numbers = []
        cold_numbers = []
        
        if "trend" in result and result["trend"]:
            if "hot_numbers" in result["trend"]:
                hot_numbers = result["trend"]["hot_numbers"]
            if "cold_numbers" in result["trend"]:
                cold_numbers = result["trend"]["cold_numbers"]
        
        # 按概率排序获取推荐号码
        sorted_predictions = sorted(predictions, key=lambda x: x["probability"], reverse=True)
        top_recommendations = sorted_predictions[:7]  # 推荐7个号码（6位+特别号）
        
        return ApiResponse(
            success=True,
            code=200,
            message="预测成功",
            data={
                "total_samples": result["total_samples"],
                "analysis_time": result["analysis_time"],
                "use_trend_data": use_trend,
                "predictions": predictions,
                "hot_numbers": hot_numbers,
                "cold_numbers": cold_numbers,
                "top_recommendations": top_recommendations
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"预测失败: {str(e)}", str(e))

@router.get("/comprehensive", summary="综合分析", description="执行所有维度的综合分析")
async def comprehensive_analysis(use_trend: bool = Query(True, description="是否使用走势图数据")):
    """
    执行所有维度的综合分析
    
    Args:
        use_trend: 是否使用走势图数据
    
    Returns:
        综合分析结果，包含频率、间隔、和值、奇偶、跨度、重号、连号、趋势等分析结果
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        data = database.query_all_history_data()
        
        # 获取走势图数据
        trend_data = []
        if use_trend:
            try:
                database.cursor.execute('SELECT * FROM qxc_trend_data')
                trend_raw = database.cursor.fetchall()
                trend_data = [{'issue': item['issue'], 'trend': json.loads(item['trend_values'])} for item in trend_raw]
            except Exception as e:
                trend_data = []
        
        database.disconnect()
        
        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据",
                data=None
            )
        
        # 执行综合概率计算
        result = analyzer.calculate_probability(data, trend_data if use_trend else None)
        
        # 构建综合分析响应数据
        comprehensive_data = {
            "total_samples": result["total_samples"],
            "analysis_time": result["analysis_time"],
            "frequency": result.get("frequency", {}),
            "interval": result.get("interval", {}),
            "hezhi": result.get("hezhi", {}),
            "odd_even": result.get("odd_even", {}),
            "span": result.get("span", {}),
            "repeats": result.get("repeats", {}),
            "consecutive": result.get("consecutive", {}),
            "trend": result.get("trend", {}),
            "comparison": result.get("comparison", {}),
            "predictions": result.get("predictions", {})
        }
        
        return ApiResponse(
            success=True,
            code=200,
            message="综合分析成功",
            data=comprehensive_data
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"综合分析失败: {str(e)}", str(e))