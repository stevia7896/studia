#!/bin/zsh
# Studia 启动器（macOS）：双击打开网页版
# 第一次运行会自动安装依赖，需要几分钟；之后几秒就能起来。
# 这个终端窗口要一直开着，关掉它网页版就停了。按 Ctrl+C 退出。

SELF="${0:A}"
cd "$(dirname "$SELF")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# 每次启动先跟进 GitHub 上的最新版本；只有真的拉到新提交才用新脚本重新开始。
if [[ -z "$STUDIA_UPDATED" && -z "$STUDIA_NO_UPDATE" && -d .git ]] && command -v git >/dev/null 2>&1; then
  echo "检查更新..."
  before_head="$(git rev-parse HEAD 2>/dev/null)"
  if GIT_TERMINAL_PROMPT=0 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 pull --ff-only --quiet; then
    after_head="$(git rev-parse HEAD 2>/dev/null)"
    if [[ -n "$after_head" && "$before_head" != "$after_head" ]]; then
      export STUDIA_UPDATED=1
      exec /bin/zsh "$SELF"
    fi
  else
    echo "更新失败（没联网，或本地改过代码），先用当前版本启动。"
  fi
fi

lock_stamp=""
if [[ -f uv.lock ]]; then
  lock_stamp="$(cksum uv.lock)"
fi

if [[ -x .venv/bin/python && -f .venv/.studia-lock && "$(<.venv/.studia-lock)" == "$lock_stamp" ]]; then
  .venv/bin/python StartWork.py --web
else
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
  [[ -n "$lock_stamp" && -d .venv ]] && printf '%s' "$lock_stamp" > .venv/.studia-lock

  uv run --no-sync StartWork.py --web
fi

echo
read -r "?程序已退出，按回车键关闭窗口..."
