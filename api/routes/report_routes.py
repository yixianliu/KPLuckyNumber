"""
报告管理路由
严格遵循数据库表结构定义
"""

from fastapi import APIRouter, Query, Depends, Header
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import uuid

# 导入内部模块
from modules.database import Database
from modules.analyzer import ProbabilityAnalyzer
from modules.report_generator import ReportGenerator
from modules.head4_analyzer import Head4Analyzer
from api.schemas import (
    ApiResponse, ReportQueryRequest, DetailedReportItem, FinalReportItem,
    DetailedReportCreateRequest, FinalReportCreateRequest, ReportStatus
)
from api.exceptions import DatabaseException, ValidationException

router = APIRouter()


def check_user_payment(db: Database, user_id: int, payment_type: str = "report_view") -> bool:
    """检查用户是否已付费"""
    try:
        db.cursor.execute(
            """
            SELECT id FROM payment_records 
            WHERE user_id = %s AND payment_type = %s AND status = 'success'
            LIMIT 1
            """,
            (user_id, payment_type)
        )
        return db.cursor.fetchone() is not None
    except Exception:
        return False

@router.post("/generate", summary="生成分析报告", description="生成概率分析报告")
async def generate_report(
    report_types: List[str] = Query(["detailed", "optimal"], description="报告类型"),
    use_trend: bool = Query(True, description="是否使用走势图数据")
):
    """
    生成概率分析报告
    
    Args:
        report_types: 报告类型列表（detailed/optimal）
        use_trend: 是否使用走势图数据
    
    Returns:
        报告生成结果
    """
    try:
        database = Database()
        analyzer = ProbabilityAnalyzer()
        generator = ReportGenerator()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 查询历史数据
        data = database.query_all_history_data()
        
        # 查询走势图数据
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
                message="数据库中没有数据，请先执行爬取操作",
                data=None
            )
        
        # 执行综合分析
        result = analyzer.calculate_probability(data, trend_data if use_trend else None)
        
        generated_reports = []
        detailed_report_id = None
        
        for report_type in report_types:
            if report_type == 'detailed':
                report_result = generator.generate_detailed_report(result, analyzer)
                generated_reports.append({
                    "type": "detailed",
                    "status": "success",
                    "preview": report_result['report_content'][:500] + "..."
                })
                
                # 存储到数据库
                if database.connect():
                    database.create_tables()
                    database.insert_detailed_report(
                        report_result['report_content'],
                        report_result.get('total_samples', 0),
                        json.dumps(report_result.get('frequency_analysis', {})),
                        json.dumps(report_result.get('probability_analysis', {})),
                        json.dumps(report_result.get('interval_analysis', {})),
                        json.dumps(report_result.get('hezhi_analysis', {})),
                        json.dumps(report_result.get('odd_even_analysis', {})),
                        json.dumps(report_result.get('span_analysis', {})),
                        json.dumps(report_result.get('raw_data_snapshot', {})),
                        json.dumps(report_result.get('calculation_steps', [])),
                        json.dumps(report_result.get('analysis_params', {})),
                        report_result.get('confidence_level', 0.0),
                        report_result.get('frequency_chart'),
                        report_result.get('probability_chart')
                    )
                    
                    # 获取刚插入的详细报告ID
                    database.cursor.execute('SELECT id FROM qxc_detailed_report ORDER BY id DESC LIMIT 1')
                    detailed_result = database.cursor.fetchone()
                    detailed_report_id = detailed_result['id'] if detailed_result else None
                    
                    database.disconnect()
            
            elif report_type == 'optimal':
                report_result = generator.generate_optimal_report(result)
                generated_reports.append({
                    "type": "optimal",
                    "status": "success",
                    "recommended_numbers": report_result.get('recommended_numbers', ''),
                    "confidence_score": report_result.get('confidence_score', 0.0),
                    "preview": report_result['report_content'][:500] + "..."
                })
                
                # 存储到数据库
                if database.connect():
                    database.create_tables()
                    database.insert_final_report(
                        detailed_report_id=detailed_report_id,
                        recommended_numbers=report_result.get('recommended_numbers', ''),
                        confidence_score=report_result.get('confidence_score', 0.0),
                        analysis_summary=report_result.get('analysis_summary', ''),
                        key_conclusions=report_result.get('key_conclusions', ''),
                        core_metrics=json.dumps(report_result.get('core_metrics', {})),
                        decision_recommendations=report_result.get('decision_recommendations', ''),
                        report_content=report_result['report_content'],
                        frequency_chart=report_result.get('frequency_chart'),
                        probability_chart=report_result.get('probability_chart'),
                        status='validated'
                    )
                    database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="报告生成成功",
            data={
                "generated_reports": generated_reports,
                "total_samples": len(data),
                "generated_at": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"生成报告失败: {str(e)}", str(e))


@router.post("/generate-head4", summary="生成头4分析报告", description="生成头4（前四位）分析报告")
async def generate_head4_report(
    use_trend: bool = Query(True, description="是否使用走势图数据")
):
    """
    生成头4（前四位）分析报告
    分析数据的前四位数字：第一位是头、第二第三位是中间、第四位是尾

    Args:
        use_trend: 是否使用走势图数据

    Returns:
        头4报告生成结果
    """
    try:
        database = Database()
        analyzer = Head4Analyzer()

        if not database.connect():
            raise DatabaseException("数据库连接失败")

        # 查询历史数据
        data = database.query_all_history_data()

        database.disconnect()

        if not data:
            return ApiResponse(
                success=False,
                code=404,
                message="数据库中没有数据，请先执行爬取操作",
                data=None
            )

        # 执行头4分析
        result = analyzer.calculate_head4_analysis(data)

        # 生成报告内容
        report_content = analyzer.generate_head4_report(result)

        # 存储到数据库
        if database.connect():
            database.create_tables()
            database.insert_head4_report(
                report_content=report_content,
                total_samples=result.get('total_samples', 0),
                head_frequency_analysis=json.dumps(result.get('head_frequency', {})),
                middle_frequency_analysis=json.dumps(result.get('middle_frequency', {})),
                tail_frequency_analysis=json.dumps(result.get('tail_frequency', {})),
                head_omission_analysis=json.dumps(result.get('head_omission', {})),
                middle_omission_analysis=json.dumps(result.get('middle_omission', {})),
                tail_omission_analysis=json.dumps(result.get('tail_omission', {})),
                head_tail_combination=json.dumps(result.get('head_tail_combination', {})),
                middle_features=json.dumps(result.get('middle_features', {})),
                confidence_level=0.85
            )
            database.disconnect()

        return ApiResponse(
            success=True,
            code=200,
            message="头4分析报告生成成功",
            data={
                "type": "head4",
                "status": "success",
                "preview": report_content[:500] + "...",
                "total_samples": len(data),
                "generated_at": datetime.now().isoformat()
            }
        )

    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"生成头4报告失败: {str(e)}", str(e))


@router.get("/list", summary="获取报告列表", description="分页获取报告列表")
async def get_report_list(
    report_type: Optional[str] = Query(None, description="报告类型过滤（detailed/optimal）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=50, description="每页数量")
):
    """
    分页获取报告列表
    
    Args:
        report_type: 报告类型过滤（detailed/optimal）
        page: 页码
        page_size: 每页数量
    
    Returns:
        分页报告列表
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        reports = database.get_reports()
        database.disconnect()
        
        # 过滤
        if report_type:
            reports = [r for r in reports if r.get('report_type') == report_type]
        
        # 分页
        total = len(reports)
        start = (page - 1) * page_size
        end = start + page_size
        items = reports[start:end]
        
        # 格式化
        formatted_items = []
        for report in items:
            report_type = report.get('report_type', 'detailed')
            if report_type == 'optimal':
                item = {
                    "id": report.get('id'),
                    "report_date": report.get('report_date'),
                    "report_type": 'optimal',
                    "report_uuid": report.get('report_uuid'),
                    "detailed_report_id": report.get('detailed_report_id'),
                    "recommended_numbers": report.get('recommended_numbers'),
                    "confidence_score": report.get('confidence_score'),
                    "analysis_summary": report.get('analysis_summary'),
                    "status": report.get('status'),
                    "created_at": report['created_at'].isoformat() if report.get('created_at') else None
                }
            else:
                item = {
                    "id": report.get('id'),
                    "report_date": report.get('report_date'),
                    "report_type": 'detailed',
                    "report_uuid": report.get('report_uuid'),
                    "total_samples": report.get('total_samples'),
                    "confidence_level": report.get('confidence_level'),
                    "created_at": report['created_at'].isoformat() if report.get('created_at') else None
                }
            formatted_items.append(item)
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "items": formatted_items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": (total + page_size - 1) // page_size
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询报告失败: {str(e)}", str(e))

@router.get("/{report_id}", summary="获取报告详情", description="根据报告ID获取报告详情（需付费后才能查看完整内容）")
async def get_report_by_id(
    report_id: int,
    user_id: int,
    x_token: Optional[str] = Header(None, alias="X-Token")
):
    """
    根据报告ID获取报告详情
    用户必须登录且已付费才能查看完整报告内容
    
    Args:
        report_id: 报告ID
        user_id: 用户ID
        x_token: 用户访问令牌
    
    Returns:
        报告详情（未付费返回预览内容）
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 确保用户表存在
        database.create_user_tables()
        
        # 验证用户身份
        if x_token:
            database.cursor.execute(
                "SELECT id FROM users WHERE id = %s AND access_token = %s AND token_expire_at > NOW()",
                (user_id, x_token)
            )
        else:
            database.cursor.execute(
                "SELECT id FROM users WHERE id = %s",
                (user_id,)
            )
        
        user = database.cursor.fetchone()
        if not user:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=401,
                message="用户未登录或token已过期",
                data=None
            )
        
        # 检查用户是否已付费
        is_paid = check_user_payment(database, user_id, "report_view")
        
        # 先查询最终报告表
        database.cursor.execute(
            "SELECT * FROM qxc_final_report WHERE id = %s",
            (report_id,)
        )
        result = database.cursor.fetchone()
        
        if result:
            database.disconnect()
            
            if not is_paid:
                # 未付费，返回预览内容
                return ApiResponse(
                    success=True,
                    code=200,
                    message="查询成功（预览模式，请付费后查看完整内容）",
                    data={
                        "id": result['id'],
                        "report_date": result['report_date'],
                        "report_uuid": result['report_uuid'],
                        "recommended_numbers": "****",
                        "confidence_score": result['confidence_score'],
                        "analysis_summary": "付费后可查看完整分析摘要",
                        "key_conclusions": "付费后可查看关键结论",
                        "status": result['status'],
                        "is_preview": True,
                        "is_paid": False,
                        "created_at": result['created_at'].isoformat() if result.get('created_at') else None
                    }
                )
            
            return ApiResponse(
                success=True,
                code=200,
                message="查询成功",
                data={
                    **FinalReportItem(**result).dict(),
                    "is_preview": False,
                    "is_paid": True
                }
            )
        
        # 查询详细报告表
        database.cursor.execute(
            "SELECT * FROM qxc_detailed_report WHERE id = %s",
            (report_id,)
        )
        result = database.cursor.fetchone()
        
        database.disconnect()
        
        if not result:
            return ApiResponse(
                success=False,
                code=404,
                message="报告未找到",
                data=None
            )
        
        if not is_paid:
            # 未付费，返回预览内容
            return ApiResponse(
                success=True,
                code=200,
                message="查询成功（预览模式，请付费后查看完整内容）",
                data={
                    "id": result['id'],
                    "report_date": result['report_date'],
                    "report_uuid": result['report_uuid'],
                    "total_samples": result['total_samples'],
                    "confidence_level": result['confidence_level'],
                    "report_content": "付费后可查看完整报告内容...",
                    "is_preview": True,
                    "is_paid": False,
                    "created_at": result['created_at'].isoformat() if result.get('created_at') else None
                }
            )
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                **DetailedReportItem(**result).dict(),
                "is_preview": False,
                "is_paid": True
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询报告失败: {str(e)}", str(e))

@router.post("/detailed", summary="新增详细报告", description="手动新增详细分析报告")
async def create_detailed_report(request: DetailedReportCreateRequest):
    """
    手动新增详细分析报告
    
    Args:
        request: 详细报告请求体
    
    Returns:
        创建的详细报告详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 检查UUID是否已存在
        database.cursor.execute(
            "SELECT id FROM qxc_detailed_report WHERE report_uuid = %s",
            (request.report_uuid,)
        )
        existing = database.cursor.fetchone()
        if existing:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=409,
                message="报告UUID已存在",
                data=None
            )
        
        # 插入数据
        sql = '''
        INSERT INTO qxc_detailed_report 
        (report_date, report_uuid, raw_data_snapshot, calculation_steps, analysis_params,
         frequency_analysis, probability_analysis, interval_analysis,
         hezhi_analysis, odd_even_analysis, span_analysis,
         total_samples, confidence_level, report_content)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        '''
        
        database.cursor.execute(sql, (
            request.report_date,
            request.report_uuid,
            request.raw_data_snapshot,
            request.calculation_steps,
            request.analysis_params,
            request.frequency_analysis,
            request.probability_analysis,
            request.interval_analysis,
            request.hezhi_analysis,
            request.odd_even_analysis,
            request.span_analysis,
            request.total_samples,
            request.confidence_level,
            request.report_content
        ))
        
        database.connection.commit()
        
        # 获取插入的数据
        database.cursor.execute(
            "SELECT * FROM qxc_detailed_report WHERE report_uuid = %s",
            (request.report_uuid,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=201,
            message="创建成功",
            data=DetailedReportItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"创建详细报告失败: {str(e)}", str(e))

@router.post("/final", summary="新增最终报告", description="手动新增最终最优报告")
async def create_final_report(request: FinalReportCreateRequest):
    """
    手动新增最终最优报告
    
    Args:
        request: 最终报告请求体
    
    Returns:
        创建的最终报告详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 检查UUID是否已存在
        database.cursor.execute(
            "SELECT id FROM qxc_final_report WHERE report_uuid = %s",
            (request.report_uuid,)
        )
        existing = database.cursor.fetchone()
        if existing:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=409,
                message="报告UUID已存在",
                data=None
            )
        
        # 检查关联的详细报告是否存在
        if request.detailed_report_id:
            database.cursor.execute(
                "SELECT id FROM qxc_detailed_report WHERE id = %s",
                (request.detailed_report_id,)
            )
            detailed_exists = database.cursor.fetchone()
            if not detailed_exists:
                database.disconnect()
                return ApiResponse(
                    success=False,
                    code=400,
                    message="关联的详细报告不存在",
                    data=None
                )
        
        # 插入数据
        sql = '''
        INSERT INTO qxc_final_report 
        (detailed_report_id, report_date, report_uuid, recommended_numbers,
         confidence_score, analysis_summary, key_conclusions, core_metrics,
         decision_recommendations, report_content, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        '''
        
        database.cursor.execute(sql, (
            request.detailed_report_id,
            request.report_date,
            request.report_uuid,
            request.recommended_numbers,
            request.confidence_score,
            request.analysis_summary,
            request.key_conclusions,
            request.core_metrics,
            request.decision_recommendations,
            request.report_content,
            request.status.value
        ))
        
        database.connection.commit()
        
        # 获取插入的数据
        database.cursor.execute(
            "SELECT * FROM qxc_final_report WHERE report_uuid = %s",
            (request.report_uuid,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=201,
            message="创建成功",
            data=FinalReportItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"创建最终报告失败: {str(e)}", str(e))

@router.put("/{report_id}", summary="更新报告", description="根据报告ID更新报告")
async def update_report(report_id: int, request: FinalReportCreateRequest = None):
    """
    根据报告ID更新报告
    
    Args:
        report_id: 报告ID
        request: 更新数据请求体（仅支持最终报告更新）
    
    Returns:
        更新后的报告详情
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 检查最终报告是否存在
        database.cursor.execute(
            "SELECT id FROM qxc_final_report WHERE id = %s",
            (report_id,)
        )
        existing = database.cursor.fetchone()
        
        if not existing:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=404,
                message="报告未找到或不支持更新",
                data=None
            )
        
        # 构建更新语句
        update_fields = []
        update_values = []
        
        if request.detailed_report_id is not None:
            update_fields.append("detailed_report_id = %s")
            update_values.append(request.detailed_report_id)
        if request.report_date is not None:
            update_fields.append("report_date = %s")
            update_values.append(request.report_date)
        if request.recommended_numbers is not None:
            update_fields.append("recommended_numbers = %s")
            update_values.append(request.recommended_numbers)
        if request.confidence_score is not None:
            update_fields.append("confidence_score = %s")
            update_values.append(request.confidence_score)
        if request.analysis_summary is not None:
            update_fields.append("analysis_summary = %s")
            update_values.append(request.analysis_summary)
        if request.key_conclusions is not None:
            update_fields.append("key_conclusions = %s")
            update_values.append(request.key_conclusions)
        if request.core_metrics is not None:
            update_fields.append("core_metrics = %s")
            update_values.append(request.core_metrics)
        if request.decision_recommendations is not None:
            update_fields.append("decision_recommendations = %s")
            update_values.append(request.decision_recommendations)
        if request.report_content is not None:
            update_fields.append("report_content = %s")
            update_values.append(request.report_content)
        if request.status is not None:
            update_fields.append("status = %s")
            update_values.append(request.status.value)
        
        if not update_fields:
            database.disconnect()
            return ApiResponse(
                success=False,
                code=400,
                message="未提供更新字段",
                data=None
            )
        
        update_values.append(report_id)
        sql = f"UPDATE qxc_final_report SET {', '.join(update_fields)} WHERE id = %s"
        database.cursor.execute(sql, tuple(update_values))
        database.connection.commit()
        
        # 获取更新后的数据
        database.cursor.execute(
            "SELECT * FROM qxc_final_report WHERE id = %s",
            (report_id,)
        )
        result = database.cursor.fetchone()
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="更新成功",
            data=FinalReportItem(**result).dict()
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"更新报告失败: {str(e)}", str(e))

@router.delete("/{report_id}", summary="删除报告", description="根据报告ID删除报告")
async def delete_report(report_id: int):
    """
    根据报告ID删除报告
    
    Args:
        report_id: 报告ID
    
    Returns:
        删除结果
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        # 先尝试删除最终报告
        database.cursor.execute(
            "DELETE FROM qxc_final_report WHERE id = %s",
            (report_id,)
        )
        affected_rows = database.cursor.rowcount
        
        if affected_rows == 0:
            # 尝试删除详细报告
            database.cursor.execute(
                "DELETE FROM qxc_detailed_report WHERE id = %s",
                (report_id,)
            )
            affected_rows = database.cursor.rowcount
        
        database.connection.commit()
        database.disconnect()
        
        if affected_rows == 0:
            return ApiResponse(
                success=False,
                code=404,
                message="报告未找到",
                data=None
            )
        
        return ApiResponse(
            success=True,
            code=200,
            message="删除成功",
            data={"deleted_id": report_id, "affected_rows": affected_rows}
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"删除报告失败: {str(e)}", str(e))

@router.get("/summary", summary="报告统计", description="获取报告统计信息")
async def get_report_summary():
    """
    获取报告统计信息
    
    Returns:
        报告统计信息
    """
    try:
        database = Database()
        
        if not database.connect():
            raise DatabaseException("数据库连接失败")
        
        try:
            database.cursor.execute('SELECT COUNT(*) as count FROM qxc_detailed_report')
            detailed_count = database.cursor.fetchone()['count']
        except:
            detailed_count = 0
        
        try:
            database.cursor.execute('SELECT COUNT(*) as count FROM qxc_final_report')
            final_count = database.cursor.fetchone()['count']
        except:
            final_count = 0
        
        # 获取最新报告信息
        latest_detailed = None
        latest_final = None
        
        try:
            database.cursor.execute('SELECT * FROM qxc_detailed_report ORDER BY created_at DESC LIMIT 1')
            latest_detailed = database.cursor.fetchone()
        except:
            pass
        
        try:
            database.cursor.execute('SELECT * FROM qxc_final_report ORDER BY created_at DESC LIMIT 1')
            latest_final = database.cursor.fetchone()
        except:
            pass
        
        database.disconnect()
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "detailed_report_count": detailed_count,
                "final_report_count": final_count,
                "total_report_count": detailed_count + final_count,
                "latest_detailed_report": {
                    "id": latest_detailed['id'],
                    "report_date": latest_detailed['report_date'],
                    "total_samples": latest_detailed['total_samples'],
                    "confidence_level": latest_detailed['confidence_level'],
                    "created_at": latest_detailed['created_at'].isoformat()
                } if latest_detailed else None,
                "latest_final_report": {
                    "id": latest_final['id'],
                    "report_date": latest_final['report_date'],
                    "recommended_numbers": latest_final['recommended_numbers'],
                    "confidence_score": latest_final['confidence_score'],
                    "status": latest_final['status'],
                    "created_at": latest_final['created_at'].isoformat()
                } if latest_final else None
            }
        )
    
    except DatabaseException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询报告统计失败: {str(e)}", str(e))