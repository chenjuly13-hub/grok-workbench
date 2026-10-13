@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 "%~dp0app.py"
if errorlevel 1 (
  echo.
  echo 启动失败。请确认已安装 Python 3，并且可以运行 py -3。
  pause
)
