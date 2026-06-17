"""
概率分析路由（专业版）
提供七星彩多维度统计分析API
"""

from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime
import json

# 导入内部模块
from modules.database import Database
from modules.analyzer import ProbabilityAnalyzer
from api.schemas import ApiResponse
from api.exceptions import DatabaseException

router = APIRouter()


def _get_data_from_db():
    """从数据库获取数据的通用函数"""
    database = Database()
    if not database.connect():
        raise DatabaseException("数据库连接失败")

    data = database.query_all_history_data()
    database.disconnect()

    if not data:
        return None

    return data


@router.get("/frequency", summary="号码频率分析", description="分析各号码在每个位置的出现频率及与理论值的偏离")
async def analyze_frequency():
    """
    分析各号码在每个位置的出现频率

    Returns:
        频率分析结果，包含观测频率、理论概率、偏离率
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        freq_result = analyzer.analyze_frequency(data)

        # 简化输出格式
        simplified = {}
        for pos, pos_data in freq_result.items():
            simplified[pos] = {
                "position_name": pos_data["position_name"],
                "number_stats": pos_data["number_stats"]
            }

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "frequency_analysis": simplified,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/omission", summary="遗漏值分析", description="分析各号码的当前遗漏、最大遗漏、平均遗漏")
async def analyze_omission():
    """
    分析号码遗漏值

    Returns:
        遗漏分析结果，包含当前遗漏、最大遗漏、平均遗漏、遗漏比率
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        omission_result = analyzer.analyze_omission(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "omission_analysis": omission_result,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/hot_cold", summary="冷热号分析", description="基于遗漏值和近期频率进行冷热号分级")
async def analyze_hot_cold(recent_n: int = Query(30, description="近期统计期数")):
    """
    分析冷热号分级

    Args:
        recent_n: 近期统计期数，默认30期

    Returns:
        冷热号分级结果
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        hot_cold_result = analyzer.analyze_hot_cold(data, recent_n=recent_n)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "recent_periods": recent_n,
                "hot_cold_analysis": hot_cold_result,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/path_012", summary="012路分析", description="分析012路（除3余数）分布")
async def analyze_path_012():
    """
    分析012路分布

    Returns:
        012路分析结果
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        path_result = analyzer.analyze_012_path(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "path_012_analysis": path_result,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/big_small", summary="大小比分析", description="分析大小号分布")
async def analyze_big_small():
    """
    分析大小号分布

    Returns:
        大小号分布分析结果
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        bs_result = analyzer.analyze_big_small(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "big_small_analysis": bs_result,
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
        奇偶分析结果，包含各位置奇偶分布及整体模式
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        odd_even_result = analyzer.analyze_odd_even(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "odd_even_analysis": odd_even_result,
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
        和值分析结果，包含和值分布、区间分布、理论对比
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        hezhi_result = analyzer.analyze_hezhi(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "hezhi_analysis": hezhi_result,
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
        跨度分析结果
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        span_result = analyzer.analyze_span(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "span_analysis": span_result,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/repeats", summary="重号分析", description="分析相邻期重号规律")
async def analyze_repeats():
    """
    分析重号规律

    Returns:
        重号分析结果
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        repeat_result = analyzer.analyze_repeats(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "repeat_analysis": repeat_result,
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
        连号分析结果
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        consecutive_result = analyzer.analyze_consecutive(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "consecutive_analysis": consecutive_result,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/correlation", summary="位置相关性分析", description="分析各位置号码的相关性")
async def analyze_correlation():
    """
    分析位置间相关性

    Returns:
        位置间皮尔逊相关系数矩阵
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        corr_result = analyzer.analyze_position_correlation(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": len(data),
                "correlation_analysis": corr_result,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/randomness", summary="随机性检验", description="对历史数据进行随机性检验")
async def analyze_randomness():
    """
    执行随机性检验

    Returns:
        随机性检验结果，包含卡方检验、连号分析等
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        rand_result = analyzer.analyze_randomness(data)

        return ApiResponse(
            success=True, code=200, message="检验完成",
            data={
                "total_samples": len(data),
                "randomness_test": rand_result,
                "analysis_time": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"检验失败: {str(e)}", str(e))


@router.get("/position_analysis", summary="位置级综合分析", description="按位置进行综合分析")
async def position_analysis():
    """
    按位置进行综合分析

    Returns:
        各位置的综合统计特征
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        result = analyzer.calculate_probability(data)

        return ApiResponse(
            success=True, code=200, message="分析成功",
            data={
                "total_samples": result["total_samples"],
                "position_analysis": result.get("position_analysis", {}),
                "analysis_time": result["analysis_time"]
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"分析失败: {str(e)}", str(e))


@router.get("/comprehensive", summary="综合分析", description="执行所有维度的综合分析")
async def comprehensive_analysis():
    """
    执行所有维度的综合分析

    Returns:
        综合分析结果，包含频率、遗漏、冷热、012路、大小、奇偶、和值、跨度、重号、连号、相关性、随机性等
    """
    try:
        data = _get_data_from_db()
        if data is None:
            return ApiResponse(success=False, code=404, message="数据库中没有数据", data=None)

        analyzer = ProbabilityAnalyzer()
        result = analyzer.calculate_probability(data)

        # 构建简化版综合分析响应（避免数据过大）
        comprehensive_data = {
            "total_samples": result["total_samples"],
            "analysis_time": result["analysis_time"],
            "methodology_note": result.get("methodology_note", ""),
            "position_analysis_summary": {
                pos: {
                    "position_name": data["position_name"],
                    "hot_numbers": data.get("hot_numbers", [])[:3],
                    "cold_numbers": data.get("cold_numbers", [])[:3],
                    "theory_prob": data["theory_prob"]
                }
                for pos, data in result.get("position_analysis", {}).items()
            },
            "frequency_summary": {
                pos: {
                    "position_name": data["position_name"],
                    "most_frequent": data.get("most_frequent", []),
                    "least_frequent": data.get("least_frequent", [])
                }
                for pos, data in result.get("frequency", {}).items()
            },
            "omission_summary": {
                pos: {
                    "position_name": data["position_name"],
                    "top_omissions": sorted(
                        data.get("number_stats", {}).items(),
                        key=lambda x: x[1].get("current_omission", 0),
                        reverse=True
                    )[:3]
                }
                for pos, data in result.get("omission", {}).items()
            },
            "hezhi": result.get("hezhi", {}),
            "span": result.get("span", {}),
            "repeats": result.get("repeats", {}),
            "consecutive": result.get("consecutive", {}),
            "randomness": {
                "overall_assessment": result.get("randomness", {}).get("overall_assessment", ""),
                "chi_square_summary": {
                    pos: {
                        "position_name": data["position_name"],
                        "chi_square": data["chi_square"],
                        "interpretation": data["interpretation"]
                    }
                    for pos, data in result.get("randomness", {}).get("chi_square_test", {}).items()
                }
            },
            "correlation": {
                "note": result.get("correlation", {}).get("note", ""),
                "position_names": result.get("correlation", {}).get("position_names", [])
            }
        }

        return ApiResponse(
            success=True, code=200, message="综合分析成功",
            data=comprehensive_data
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"综合分析失败: {str(e)}", str(e))
