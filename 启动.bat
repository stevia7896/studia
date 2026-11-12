@echo off
chcp 65001 >nul
title Studia
cd /d "%~dp0"

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

uv run StartWork.py --web
pause
