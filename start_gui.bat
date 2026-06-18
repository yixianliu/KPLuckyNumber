@echo off
chcp 65001 >nul
echo ====================================
echo  彩票数字概率统计分析系统 - 启动中
echo ====================================
echo.

REM 尝试多个Python路径启动GUI
set "PYTHON_CMD="

REM 1. 优先使用Anaconda Python（带有tkinter）
if exist "D:\anaconda3\python.exe" (
    set "PYTHON_CMD=D:\anaconda3\python.exe"
    echo [使用] Anaconda Python
    goto :RUN
)

REM 2. 尝试系统Python
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python -c "import tkinter" >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PYTHON_CMD=python"
        echo [使用] 系统Python
        goto :RUN
    )
)

echo [错误] 未找到支持tkinter的Python环境！
echo.
echo 请确保已安装Python并包含tkinter模块。
echo 推荐安装Anaconda: https://www.anaconda.com/
pause
exit /b 1

:RUN
cd /d "%~dp0"
echo 正在启动GUI...
"%PYTHON_CMD%" gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [错误] 程序启动失败，请检查控制台输出。
    pause
)