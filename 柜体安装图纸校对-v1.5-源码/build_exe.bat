@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 打包柜图校对
set "ROOT=%CD%"

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo 正在安装 PyInstaller...
    python -m pip install -r requirements-build.txt
    if errorlevel 1 (
        echo [错误] 打包依赖安装失败。
        pause
        exit /b 1
    )
)

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "柜图校对" ^
  --icon "%ROOT%\柜图校对.ico" ^
  --version-file "%ROOT%\version_info.txt" ^
  --exclude-module numpy ^
  --exclude-module PIL ^
  --exclude-module psutil ^
  --collect-all webview ^
  --collect-all pythonnet ^
  --collect-all clr_loader ^
  --add-data "%ROOT%\web;web" ^
  --add-data "%ROOT%\示例;示例" ^
  --distpath "%ROOT%\发布" ^
  --workpath "%ROOT%\build\pyinstaller" ^
  --specpath "%ROOT%\build" ^
  "%ROOT%\app.py"

if errorlevel 1 (
    echo [错误] exe 打包失败。
    pause
    exit /b 1
)
echo.
echo 打包完成：%ROOT%\发布\柜图校对.exe
pause
