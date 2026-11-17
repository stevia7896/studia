# Studia

网课自动学习和 AI 答题助手，支持职教云 MOOC / AI 优课、智慧树、超星学习通。网页界面，在自己电脑上运行。

上手只要三步：装环境 → 下载启动 → 填 API Key。

## 1. 装环境（只需一次）

macOS 打开「终端」：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows 打开 PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

没装 Git 的话先装：[git-scm.com/downloads](https://git-scm.com/downloads)。装完关掉终端重新打开。

## 2. 下载并启动

```bash
git clone https://github.com/stevia7896/studia.git
```

进入 `studia` 文件夹，双击 `启动.command`（macOS）或 `启动.bat`（Windows）。第一次要装依赖，约 2–5 分钟，装好后浏览器会自动打开界面。

- 启动窗口要一直开着，关掉它程序就停了。
- macOS 提示无法打开时，右键 `启动.command` → **打开**。
- 想放到桌面：macOS 右键 `启动.command` → **制作替身**，把替身拖到桌面；Windows 右键 `启动.bat` → **发送到** → **桌面快捷方式**。不要直接复制文件到桌面，复制出去的不会跟着更新。

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

> 报 401 / 404，多半是 Key 填错或服务地址少了 `/v1`。别家 OpenAI 兼容接口同样能填，换地址和模型名即可。图片题需要能看图的模型。

## 4. 学习和答题

侧栏选平台扫码登录 → 点课程：

- **学习** 标签：自动完成视频、文档等学习任务。下面的格子每格是一个学习单元，绿色是已完成。
- **答题** 标签：勾选试卷或作业 → **仅生成答案**（超星是 **保存答案（不交卷）**）或 **AI 作答并交卷**，交卷要再点一次确认。

<img src="docs/images/ui-answer.png" width="760" alt="答题">

每份试卷后面会显示状态：

| 状态 | 意思 |
| --- | --- |
| 已提交 | 已经交过，默认不勾选 |
| 未作答 / 未提交 | 还没交，打开答题页时默认勾选 |
| 状态未知 | 平台没有返回完成情况，请到平台确认后再决定勾不勾 |

超星章节作业的状态这样判断：通过 Studia 交卷成功的作业显示「已提交」；整章都已完成的，章里的作业也算已提交；章节还没学完的显示「状态未知」。

## 5. 学习通考试

登录超星学习通 → 点课程 → **考试** 标签 → 选择一场考试 → **保存答案（不交卷）** 或 **AI 考试并交卷**，再次点击确认开始。

考试使用「AI 模型」中当前标记为「使用中」的 API，和答题共用配置。进入考试可能启动平台倒计时；只有整卷作答并确认保存后才自动交卷。考试码、人脸验证或专用客户端要求需在学习通完成。列表可单独刷新，作业仍在「答题」标签。

本机首次使用需安装浏览器：`uv run playwright install --only-shell chromium`；Docker 镜像已包含。此功能已通过离线模拟页面测试，尚未用真实账号验证交卷，首次使用建议选「保存答案（不交卷）」并在学习通核对。

## 6. 更新

双击启动文件时会先自动拉取 GitHub 上的最新版本，再装依赖、启动，不用手动操作。登录状态和模型配置都保留。

窗口里提示「更新失败」时（没联网，或本地改过代码），会先用当前版本启动。要手动更新的话，关掉启动窗口，在 `studia` 文件夹里打开终端运行 `git pull`；报冲突的话先 `git stash` 再拉。

---

更多说明见 [docs/USAGE.md](docs/USAGE.md)。代码基于 [yijunjie07/studia](https://github.com/yijunjie07/studia)。
