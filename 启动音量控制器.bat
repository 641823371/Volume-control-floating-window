@echo off
cd /d "%~dp0"

REM 优先使用项目内的虚拟环境（如已执行过 python -m venv .venv 并安装依赖），
REM 否则回退到系统 PATH 中的 pythonw。无控制台窗口启动，程序以托盘形式常驻。
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" main.py
) else (
    start "" pythonw main.py
)

if errorlevel 1 (
    echo 启动失败：请确保已安装 Python 及依赖（pip install -r requirements.txt）
    pause
)
