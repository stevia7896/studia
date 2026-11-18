@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Studia
cd /d "%~dp0"

rem 每次启动先跟进 GitHub 上的最新版本；只有真的拉到新提交才重新启动。
rem git 会改写这个文件，所以拉取和重新启动放在同一个括号块里，
rem cmd 整块读完才执行，不会读到改了一半的脚本。
if not "%~1"=="--updated" if not defined STUDIA_NO_UPDATE if exist .git (
  where git >nul 2>nul && (
    echo 检查更新...
    set "BEFORE_HEAD="
    for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "BEFORE_HEAD=%%H"
    set "GIT_TERMINAL_PROMPT=0"
    git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 pull --ff-only --quiet
    if errorlevel 1 (
      echo 更新失败（没联网，或本地改过代码），先用当前版本启动。
    ) else (
      set "AFTER_HEAD="
      for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "AFTER_HEAD=%%H"
      if defined AFTER_HEAD if not "!BEFORE_HEAD!"=="!AFTER_HEAD!" (
        "%~f0" --updated
        exit /b
      )
    )
  )
)

set "LOCK_STAMP="
if exist uv.lock for %%F in (uv.lock) do set "LOCK_STAMP=%%~zF_%%~tF"
set "SAVED_STAMP="
if exist .venv\.studia-lock set /p SAVED_STAMP=<.venv\.studia-lock

if exist .venv\Scripts\python.exe if defined LOCK_STAMP if "!SAVED_STAMP!"=="!LOCK_STAMP!" (
  .venv\Scripts\python.exe StartWork.py --web
  goto :done
)

where uv >nul 2>nul
if errorlevel 1 (
  echo 没有找到 uv，先安装：
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo 装完关掉这个窗口，重新双击启动。
  pause
  exit /b 1
)

uv sync --locked --python 3.12
if errorlevel 1 (
  echo 依赖安装失败，把上面的报错发到 Issues 里。
  pause
  exit /b 1
)
if defined LOCK_STAMP if exist .venv echo !LOCK_STAMP!>.venv\.studia-lock

uv run --no-sync StartWork.py --web

:done
pause
