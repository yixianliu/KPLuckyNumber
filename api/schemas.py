"""
Pydantic数据模型定义
用于请求验证和响应格式化
严格遵循数据库表结构定义
"""

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

# ==================== 枚举类型定义 ====================

class ReportStatus(str, Enum):
    """报告状态枚举"""
    draft = "draft"
    validated = "validated"
    published = "published"

# ==================== 请求模型 ====================

class CrawlRequest(BaseModel):
    """数据爬取请求模型"""
    qishu: int = Field(100, ge=1, le=500, description="获取期数")
    trend: bool = Field(False, description="是否获取走势图数据")

class AnalysisRequest(BaseModel):
    """概率分析请求模型"""
    use_trend: bool = Field(True, description="是否使用走势图数据")
    report_types: List[str] = Field(["detailed", "optimal"], description="报告类型")

class QueryRequest(BaseModel):
    """数据查询请求模型"""
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")
    sort_by: str = Field("issue", description="排序字段")
    sort_order: str = Field("desc", description="排序顺序")

class ReportQueryRequest(BaseModel):
    """报告查询请求模型"""
    report_type: Optional[str] = Field(None, description="报告类型过滤（detailed/optimal）")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(10, ge=1, le=50, description="每页数量")

class LotteryDataCreateRequest(BaseModel):
    """新增开奖数据请求模型"""
    issue: str = Field(..., description="期号（唯一标识）", max_length=20)
    draw_date: str = Field(..., description="开奖日期", max_length=20)
    num1: int = Field(..., ge=0, le=9, description="第一位号码")
    num2: int = Field(..., ge=0, le=9, description="第二位号码")
    num3: int = Field(..., ge=0, le=9, description="第三位号码")
    num4: int = Field(..., ge=0, le=9, description="第四位号码")
    num5: int = Field(..., ge=0, le=9, description="第五位号码")
    num6: int = Field(..., ge=0, le=9, description="第六位号码")
    special_num: int = Field(..., ge=0, le=9, description="特别号码")
    hezhi: Optional[str] = Field(None, description="和值", max_length=10)
    hezhi_type: Optional[str] = Field(None, description="和值类型（奇偶）", max_length=10)
    odd_even_ratio: Optional[str] = Field(None, description="奇偶比例", max_length=10)
    odd_even_pattern: Optional[str] = Field(None, description="奇偶模式")
    span: Optional[str] = Field(None, description="跨度", max_length=10)

class LotteryDataUpdateRequest(BaseModel):
    """更新开奖数据请求模型"""
    draw_date: Optional[str] = Field(None, description="开奖日期", max_length=20)
    num1: Optional[int] = Field(None, ge=0, le=9, description="第一位号码")
    num2: Optional[int] = Field(None, ge=0, le=9, description="第二位号码")
    num3: Optional[int] = Field(None, ge=0, le=9, description="第三位号码")
    num4: Optional[int] = Field(None, ge=0, le=9, description="第四位号码")
    num5: Optional[int] = Field(None, ge=0, le=9, description="第五位号码")
    num6: Optional[int] = Field(None, ge=0, le=9, description="第六位号码")
    special_num: Optional[int] = Field(None, ge=0, le=9, description="特别号码")
    hezhi: Optional[str] = Field(None, description="和值", max_length=10)
    hezhi_type: Optional[str] = Field(None, description="和值类型（奇偶）", max_length=10)
    odd_even_ratio: Optional[str] = Field(None, description="奇偶比例", max_length=10)
    odd_even_pattern: Optional[str] = Field(None, description="奇偶模式")
    span: Optional[str] = Field(None, description="跨度", max_length=10)

class TrendDataCreateRequest(BaseModel):
    """新增走势图数据请求模型"""
    issue: str = Field(..., description="期号（关联开奖数据）", max_length=20)
    trend_values: str = Field(..., description="走势图数据JSON")

class DetailedReportCreateRequest(BaseModel):
    """新增详细报告请求模型"""
    report_date: str = Field(..., description="报告日期", max_length=20)
    report_uuid: str = Field(..., description="报告唯一标识", max_length=36)
    raw_data_snapshot: Optional[str] = Field(None, description="原始数据快照")
    calculation_steps: Optional[str] = Field(None, description="计算步骤记录")
    analysis_params: Optional[str] = Field(None, description="分析参数配置")
    frequency_analysis: Optional[str] = Field(None, description="频率分析结果")
    probability_analysis: Optional[str] = Field(None, description="概率分析结果")
    interval_analysis: Optional[str] = Field(None, description="间隔分析结果")
    hezhi_analysis: Optional[str] = Field(None, description="和值分析结果")
    odd_even_analysis: Optional[str] = Field(None, description="奇偶分析结果")
    span_analysis: Optional[str] = Field(None, description="跨度分析结果")
    total_samples: Optional[int] = Field(None, description="分析样本数")
    confidence_level: Optional[float] = Field(None, ge=0, le=1, description="置信水平")
    report_content: Optional[str] = Field(None, description="报告内容")

class FinalReportCreateRequest(BaseModel):
    """新增最终报告请求模型"""
    detailed_report_id: Optional[int] = Field(None, description="关联详细报告ID")
    report_date: str = Field(..., description="报告日期", max_length=20)
    report_uuid: str = Field(..., description="报告唯一标识", max_length=36)
    recommended_numbers: Optional[str] = Field(None, description="推荐号码组合", max_length=50)
    confidence_score: Optional[float] = Field(None, ge=0, le=1, description="置信分数")
    analysis_summary: Optional[str] = Field(None, description="分析摘要")
    key_conclusions: Optional[str] = Field(None, description="关键结论")
    core_metrics: Optional[str] = Field(None, description="核心指标")
    decision_recommendations: Optional[str] = Field(None, description="决策建议")
    report_content: Optional[str] = Field(None, description="报告内容")
    status: ReportStatus = Field(ReportStatus.validated, description="报告状态")

# ==================== 响应模型 ====================

class ApiResponse(BaseModel):
    """通用API响应模型"""
    success: bool = Field(..., description="请求是否成功")
    code: int = Field(..., description="响应状态码")
    message: str = Field(..., description="响应消息")
    data: Optional[Any] = Field(None, description="响应数据")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间")

class LotteryDataItem(BaseModel):
    """单条开奖数据模型 - 与qxc_history_data表对应"""
    id: int = Field(..., description="主键ID")
    issue: str = Field(..., description="期号（唯一标识）")
    draw_date: str = Field(..., description="开奖日期")
    num1: int = Field(..., description="第一位号码")
    num2: int = Field(..., description="第二位号码")
    num3: int = Field(..., description="第三位号码")
    num4: int = Field(..., description="第四位号码")
    num5: int = Field(..., description="第五位号码")
    num6: int = Field(..., description="第六位号码")
    special_num: int = Field(..., description="特别号码")
    hezhi: Optional[str] = Field(None, description="和值")
    hezhi_type: Optional[str] = Field(None, description="和值类型（奇偶）")
    odd_even_ratio: Optional[str] = Field(None, description="奇偶比例")
    odd_even_pattern: Optional[str] = Field(None, description="奇偶模式")
    span: Optional[str] = Field(None, description="跨度")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")

class TrendDataItem(BaseModel):
    """走势图数据模型 - 与qxc_trend_data表对应"""
    id: int = Field(..., description="主键ID")
    issue: str = Field(..., description="期号（关联开奖数据）")
    trend_values: str = Field(..., description="走势图数据JSON")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")

class DetailedReportItem(BaseModel):
    """详细报告模型 - 与qxc_detailed_report表对应"""
    id: int = Field(..., description="主键ID")
    report_date: str = Field(..., description="报告日期")
    report_uuid: str = Field(..., description="报告唯一标识")
    raw_data_snapshot: Optional[str] = Field(None, description="原始数据快照")
    calculation_steps: Optional[str] = Field(None, description="计算步骤记录")
    analysis_params: Optional[str] = Field(None, description="分析参数配置")
    frequency_analysis: Optional[str] = Field(None, description="频率分析结果")
    probability_analysis: Optional[str] = Field(None, description="概率分析结果")
    interval_analysis: Optional[str] = Field(None, description="间隔分析结果")
    hezhi_analysis: Optional[str] = Field(None, description="和值分析结果")
    odd_even_analysis: Optional[str] = Field(None, description="奇偶分析结果")
    span_analysis: Optional[str] = Field(None, description="跨度分析结果")
    total_samples: Optional[int] = Field(None, description="分析样本数")
    confidence_level: Optional[float] = Field(None, description="置信水平")
    report_content: Optional[str] = Field(None, description="报告内容")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")

class FinalReportItem(BaseModel):
    """最终报告模型 - 与qxc_final_report表对应"""
    id: int = Field(..., description="主键ID")
    detailed_report_id: Optional[int] = Field(None, description="关联详细报告ID")
    report_date: str = Field(..., description="报告日期")
    report_uuid: str = Field(..., description="报告唯一标识")
    recommended_numbers: Optional[str] = Field(None, description="推荐号码组合")
    confidence_score: Optional[float] = Field(None, description="置信分数")
    analysis_summary: Optional[str] = Field(None, description="分析摘要")
    key_conclusions: Optional[str] = Field(None, description="关键结论")
    core_metrics: Optional[str] = Field(None, description="核心指标")
    decision_recommendations: Optional[str] = Field(None, description="决策建议")
    report_content: Optional[str] = Field(None, description="报告内容")
    status: Optional[ReportStatus] = Field(None, description="报告状态")
    created_at: Optional[datetime] = Field(None, description="创建时间")

class PaginatedResponse(BaseModel):
    """分页响应模型"""
    items: List[Any] = Field(..., description="当前页数据")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    pages: int = Field(..., description="总页数")

class FrequencyAnalysisResult(BaseModel):
    """频率分析结果模型"""
    number: int = Field(..., description="号码")
    frequency: List[int] = Field(..., description="各位置出现次数")
    probability: List[float] = Field(..., description="各位置概率")

class IntervalAnalysisResult(BaseModel):
    """间隔分析结果模型"""
    number: int = Field(..., description="号码")
    position: int = Field(..., description="位置")
    count: int = Field(..., description="出现次数")
    avg: float = Field(..., description="平均间隔")
    max: int = Field(..., description="最大间隔")
    min: int = Field(..., description="最小间隔")

class PredictionResult(BaseModel):
    """预测结果模型"""
    number: int = Field(..., description="号码")
    probability: float = Field(..., description="概率")
    confidence: float = Field(..., description="置信度")
    trend_factor: float = Field(..., description="趋势因子")
    expected_positions: List[int] = Field(..., description="推荐位置")

class TrendAnalysisResult(BaseModel):
    """趋势分析结果模型"""
    hot_numbers: List[Dict[str, Any]] = Field(..., description="热门号码")
    cold_numbers: List[Dict[str, Any]] = Field(..., description="冷门号码")
    trending_up: List[Dict[str, Any]] = Field(..., description="上升趋势号码")
    trending_down: List[Dict[str, Any]] = Field(..., description="下降趋势号码")

class ReportSummary(BaseModel):
    """报告摘要模型"""
    id: int = Field(..., description="报告ID")
    report_date: str = Field(..., description="报告日期")
    report_type: str = Field(..., description="报告类型")
    report_uuid: str = Field(..., description="报告唯一标识")
    confidence_score: Optional[float] = Field(None, description="置信分数")
    total_samples: Optional[int] = Field(None, description="分析样本数")
    created_at: datetime = Field(..., description="创建时间")

class SystemStatus(BaseModel):
    """系统状态模型"""
    status: str = Field(..., description="系统状态")
    version: str = Field(..., description="API版本")
    database_status: str = Field(..., description="数据库连接状态")
    data_count: int = Field(..., description="数据条数")
    report_count: int = Field(..., description="报告数量")
    uptime: str = Field(..., description="服务运行时间")

# ==================== 错误响应模型 ====================

class ErrorResponse(BaseModel):
    """错误响应模型"""
    success: bool = Field(False, description="请求是否成功")
    code: int = Field(..., description="错误状态码")
    message: str = Field(..., description="错误消息")
    detail: Optional[str] = Field(None, description="错误详情")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间")