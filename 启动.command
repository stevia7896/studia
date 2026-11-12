#!/bin/zsh
# Studia 启动器（macOS）：双击打开网页版
# 第一次运行会自动安装依赖，需要几分钟；之后几秒就能起来。
# 这个终端窗口要一直开着，关掉它网页版就停了。按 Ctrl+C 退出。

cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "没有找到 uv。先装一下："
  echo '  curl -LsSf https://astral.sh/uv/install.sh | sh'
  echo "装完重新双击这个文件。"
  read -r "?按回车键关闭窗口..."
  exit 1
fi

uv sync --locked --python 3.12 || {
  echo "依赖安装失败，把上面的报错发到 Issues 里。"
  read -r "?按回车键关闭窗口..."
  exit 1
}

uv run StartWork.py --web

echo
read -r "?程序已退出，按回车键关闭窗口..."
