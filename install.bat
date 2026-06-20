@echo off
chcp 65001 >nul
echo ========================================
echo   A股实时监控工具 - 安装依赖
echo ========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到Python，请先安装Python 3.7+
    echo.
    echo 下载地址: https://www.python.org/downloads/
    echo.
    echo 安装时请勾选 "Add Python to PATH" 选项！
    pause
    exit /b 1
)

echo [信息] Python已安装
python --version
echo.

echo [提示] 正在安装依赖包...
echo.

pip install PyQt5 requests -i https://pypi.tuna.tsinghua.edu.cn/simple

if %errorlevel% neq 0 (
    echo.
    echo [错误] 依赖安装失败！
    echo 请尝试手动运行: pip install PyQt5 requests
    pause
    exit /b 1
)

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 现在可以双击运行 start.bat 启动程序
echo.
pause
