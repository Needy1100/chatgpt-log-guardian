# ChatGPT 日志清理工具（ChatGPT Log Guardian）

清理 Windows 上 ChatGPT 桌面端积累的旧诊断日志，帮助节省磁盘空间。覆盖 ChatGPT 和 Codex 模式共用的桌面日志，以及 Codex 诊断数据库。

[下载脚本](https://github.com/Needy1100/chatgpt-log-guardian/releases/latest) · [快速开始](#快速开始) · [自动清理](#自动清理) · [详细说明](docs/DETAILS.md)

## 快速开始

需要 **Windows 和 Python 3.10 或更新版本**，无需安装第三方库。

1. 在 PowerShell 中运行 `py -3 --version`，确认 Python 版本。
2. 从 [发布页](https://github.com/Needy1100/chatgpt-log-guardian/releases/latest) 下载 `chatgpt_log_guardian.py`，放在固定、可写的文件夹。
3. 在这个文件夹打开 PowerShell，运行下方命令。

```powershell
py -3 .\chatgpt_log_guardian.py
```

如果 `py` 不可用，但 `python --version` 显示符合要求，可以用 `python .\chatgpt_log_guardian.py`。

脚本每次运行完成后自动退出。首次运行也可能清理符合条件的旧日志。

## 查看结果和备份

正常运行会分别输出数据库和桌面日志的状态、大小及删除数量摘要。`deleted_rows` 或 `deleted_files` 为 `0` 表示本轮对应部分没有删除内容。遇到 `missing_database` 或 `busy`，可查阅 [状态说明](docs/DETAILS.md#输出与退出码)。

脚本会先备份并校验，再清理旧日志。备份和脚本在同一文件夹，每类保留一份。

- `desktop-log-backup-latest.zip` 是被选中清理的桌面日志备份。
- `codex-log-backup-latest.sqlite` 是维护前的诊断数据库快照。

> [!NOTE]
> 清理范围限于本机诊断日志，正式聊天历史、长期记忆和项目文件不在范围内。日常检查通常很快，资源占用较低。需要备份或压缩大量日志时，耗时和临时磁盘占用可能增加。

## 自动清理

手动运行正常后，可用 Windows 任务计划程序每 30 分钟运行一次。要暂停，在任务计划程序中禁用对应任务。

可以按 [手动配置步骤](docs/DETAILS.md#配置自动运行) 操作，或将下方提示词复制给具有本机文件和终端权限的 ChatGPT/Codex。

```text
请帮我部署 https://github.com/Needy1100/chatgpt-log-guardian 发布页中的脚本。
先看 README 和详细说明，确认 Python 环境、新版桌面日志实际使用的路径和数据库结构。
把脚本放到固定的可写目录。使用当前用户的普通权限创建每 30 分钟运行一次的计划任务，设置 5 分钟运行上限，上一轮没结束就跳过新一轮。已有任务时先检查，避免重复配置。
验证脚本和任务配置，说明如何暂停。只处理本机诊断日志，保护系统文件、聊天历史、记忆和项目文件。只报告状态、大小和校验结果，不输出或上传日志正文、数据库或备份。
如果环境不兼容，请说明原因并停止。如果没有本机权限，请给出手动步骤。
```

ChatGPT 桌面端大版本更新后，请重新核对脚本处理的日志路径和数据库结构。清理规则、兼容性和故障处理见 [详细说明](docs/DETAILS.md)。
