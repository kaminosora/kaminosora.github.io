@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0site\launch_studio.py"
  exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0site\launch_studio.py"
  exit /b
)
echo 请先安装 Python 3.10 或更新版本，然后重新双击此文件。
echo 安装时勾选 Add Python to PATH：https://www.python.org/downloads/
pause
