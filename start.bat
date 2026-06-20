@echo off
chcp 65001 >nul
echo ========================================
echo   A股实时监控工具 - 启动程序
echo ========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到Python，请先安装Python 3.7+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [提示] 正在检查依赖...
pip list | findstr PyQt5 >nul
if %errorlevel% neq 0 (
    echo [提示] 正在安装依赖包...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [错误] 依赖安装失败！
        pause
        exit /b 1
    )
) else (
    echo [成功] 依赖检查通过
)

echo.
echo [提示] 正在启动程序...
echo [提示] 程序将在后台运行，关闭此窗口不影响程序
echo [提示] 如需停止程序，请在系统托盘中右键退出
echo.

REM 使用 start /B 让程序在后台运行，关闭窗口不会终止程序
start /B "" pythonw stock_monitor.py

echo [成功] 程序已启动！
echo [提示] 窗口最小化到系统托盘，双击托盘图标可恢复
timeout /t 3 >nul
