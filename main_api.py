"""
FastAPI主应用入口
七星彩数据采集与分析API服务
"""

import sys
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 添加模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入路由模块
from api.routes import data_routes, analysis_routes, report_routes, system_routes, auth_routes
from api.exceptions import setup_exception_handlers

# 创建FastAPI应用
app = FastAPI(
    title="七星彩数据采集与分析API",
    description="提供七星彩历史数据采集、概率分析、报告生成等功能的RESTful API服务",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# 配置CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(data_routes, prefix="/api/data", tags=["数据采集与管理"])
app.include_router(analysis_routes, prefix="/api/analysis", tags=["概率分析"])
app.include_router(report_routes, prefix="/api/report", tags=["报告管理"])
app.include_router(system_routes, prefix="/api/system", tags=["系统管理"])
app.include_router(auth_routes, prefix="/api/auth", tags=["用户认证与付费"])

# 注册异常处理器
setup_exception_handlers(app)

# 静态文件服务
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


# 健康检查接口
@app.get("/", tags=["健康检查"])
async def root():
    return {"message": "七星彩数据采集与分析API服务运行正常", "version": "1.0.0"}


@app.get("/health", tags=["健康检查"])
async def health_check():
    return {"status": "healthy", "timestamp": __import__('datetime').datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main_api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
