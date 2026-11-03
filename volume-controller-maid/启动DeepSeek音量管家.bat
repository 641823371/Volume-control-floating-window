@echo off
cd /d "%~dp0"

REM 依次尝试候选解释器，找到已安装 PySide6 的那个，再用 pythonw 无控制台启动。
REM 候选顺序：项目内 .venv → 本机开发用托管 venv（%USERPROFILE% 展开，避免硬编码用户名）→ 系统 PATH。
set "WORK="

REM 1) 项目内虚拟环境（用户执行过 pip install -r requirements.txt 的标准位置）
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import PySide6" >nul 2>&1
    if not errorlevel 1 set "WORK=%~dp0.venv\Scripts\pythonw.exe"
)

REM 2) 本机 WorkBuddy 托管 venv（仅本机便捷回退，其他机器不存在会被自动跳过）
if not defined WORK (
    if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
        "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -c "import PySide6" >nul 2>&1
        if not errorlevel 1 set "WORK=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
    )
)

REM 3) 系统 PATH 中的 python / pythonw
if not defined WORK (
    python -c "import PySide6" >nul 2>&1
    if not errorlevel 1 set "WORK=pythonw"
)

if defined WORK (
    start "" "%WORK%" main.py
    exit /b 0
)

echo 启动失败：未找到已安装 PySide6 的 Python 解释器。
echo 请先安装依赖后再启动：
echo   pip install -r requirements.txt
echo 或创建虚拟环境：
echo   python -m venv .venv
echo   .venv\Scripts\pip install -r requirements.txt
pause
