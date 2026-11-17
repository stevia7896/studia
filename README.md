# Studia · 配置教程

三步：装环境 → 下载启动 → 填 API Key。

## 1. 装环境（一次性）

macOS 打开终端：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows 打开 PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

没装 Git 的话先装：[git-scm.com/downloads](https://git-scm.com/downloads)。装完关掉终端重开。

## 2. 下载并启动

```bash
git clone https://github.com/stevia7896/studia.git
```

进入 `studia` 文件夹，双击 `启动.command`（macOS）或 `启动.bat`（Windows）。第一次装依赖约 2–5 分钟，之后浏览器自动打开界面。

> macOS 若提示无法打开，右键 `启动.command` → 打开。窗口别关，关掉网页版就停。

## 3. 填 API Key

界面左下角头像 → **设置**（⌘,）→ **AI 模型** → **＋ 添加模型**

<img src="docs/images/ui-add-model.png" width="760" alt="添加模型">

| 字段 | 填什么 |
| --- | --- |
| 名称 | 随便，例如 `DeepSeek` |
| 服务地址 | `https://api.deepseek.com/v1` |
| 模型 | `deepseek-chat` |
| API Key | 你的 `sk-` 开头那串 |

点 **保存并测试**，显示「可用」即可。列表里标着「使用中」的模型才会拿来答题。

> 报 401 / 404，多半是 Key 填错或服务地址少了 `/v1`。别家 OpenAI 兼容接口同样能填，换地址和模型名即可；图片题需要支持视觉的模型。

## 4. 答题

侧栏选平台扫码登录 → 点课程 → **答题** 标签 → 勾选试卷 → **仅生成答案** 或 **AI 作答并交卷**。

<img src="docs/images/ui-answer.png" width="760" alt="答题">

## 5. 学习通考试

登录超星学习通 → 点课程 → **考试** 标签 → 选择一场考试 → **保存答案（不交卷）** 或 **AI 考试并交卷**，再次点击确认开始。

考试使用「AI 模型」中当前标记为「使用中」的 API，和答题共用配置。进入考试可能启动平台倒计时；只有整卷作答并确认保存后才自动交卷。考试码、人脸验证或专用客户端要求需在学习通完成。列表可单独刷新，作业仍在「答题」标签。

本机首次使用需安装浏览器：`uv run playwright install --only-shell chromium`；Docker 镜像已包含。此功能已通过离线模拟页面测试，尚未用真实账号验证交卷，首次使用建议选「保存答案（不交卷）」并在学习通核对。

---

更多说明见 [docs/USAGE.md](docs/USAGE.md)。代码基于 [yijunjie07/studia](https://github.com/yijunjie07/studia)。
