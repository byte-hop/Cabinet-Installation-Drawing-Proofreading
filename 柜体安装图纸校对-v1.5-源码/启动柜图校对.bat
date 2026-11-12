@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 柜图校对 - 柜体安装说明书核对

set "PY=python"
%PY% -c "import sys" >nul 2>nul
if not errorlevel 1 goto check_deps
set "PY=py -3"
%PY% -c "import sys" >nul 2>nul
if not errorlevel 1 goto check_deps
echo.
echo [错误] 没有找到可用的 Python。
echo 请先安装 Python 3.11 或更高版本，安装时勾选 "Add Python to PATH"。
echo 下载地址：https://www.python.org/downloads/
echo.
pause
exit /b 1

:check_deps
%PY% -c "import pymupdf, openpyxl, webview" >nul 2>nul
if not errorlevel 1 goto run
echo.
echo 首次运行，正在安装依赖（需要联网）...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。请检查网络后重试，或手动执行：
    echo   %PY% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:run
%PY% app.py %*
if errorlevel 1 (
    echo.
    echo 程序异常退出，请查看上方错误信息。
    pause
)
endlocal
