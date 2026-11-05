# ChatGPT Log Guardian

## 清理 ChatGPT 桌面端日志，帮助节省磁盘空间

这个脚本用来清理 Windows 上 ChatGPT 桌面端积累的旧诊断日志。它处理 ChatGPT 和 Codex 模式共用的桌面日志，也维护 Codex 的诊断数据库。清理前会生成并验证备份。

脚本的清理范围限于本机诊断日志，正式聊天历史、长期记忆和项目文件不在范围内。

## 第一次使用

1. 准备 **Windows 和 Python 3.10 或更新版本**。无需安装第三方 Python 库。在 PowerShell 中运行 `py -3 --version` 检查版本。
2. 从 [下载页](https://github.com/Needy1100/chatgpt-log-guardian/releases/latest) 下载 `chatgpt_log_guardian.py`，放到一个固定、可写的文件夹。脚本会把备份也放在这里。
3. 在该文件夹内打开 PowerShell，运行下面的命令。

```powershell
py -3 .\chatgpt_log_guardian.py
```

如果电脑没有 `py` 命令，但 `python --version` 显示 Python 3.10 或更新版本，可以改用 `python .\chatgpt_log_guardian.py`。

**运行一次，维护一次，完成后退出。** 首次运行也可能清理符合条件的旧日志。

## 怎么看运行结果

通常会看到两行 JSON 摘要，分别对应诊断数据库和桌面日志。摘要显示状态、大小或删除数量。

| 看到什么 | 表示什么 |
| --- | --- |
| `deleted_rows` 或 `deleted_files` 为 `0` | 本轮相应部分没有删除记录或文件。 |
| `backup` 为 `null` | 本轮相应部分没有生成新备份。 |
| `missing_database` | 没有在预期位置找到诊断数据库。桌面日志仍会单独检查。 |
| `busy` 或 `checkpoint_busy` | 有文件被占用或发生变化，或数据库的 WAL 空间暂时未能回收。 |

需要清理时，脚本会在同一文件夹按类型各保留一份最新的已验证备份。

- 桌面日志备份为 `desktop-log-backup-latest.zip`。
- 诊断数据库备份为 `codex-log-backup-latest.sqlite`。

备份只覆盖最近一次对应类型的清理批次或维护前快照。需要恢复时，请先查看 [详细说明](docs/DETAILS.md#安全与隐私边界)。

## 设置自动清理

手动运行正常后，可以用 Windows 任务计划程序每 30 分钟运行一次。建议用当前用户权限，单次最多运行 5 分钟，上一轮还在运行时跳过新一轮。要暂停，在任务计划程序里禁用对应任务。

可以按 [手动配置步骤](docs/DETAILS.md#配置自动运行) 操作，也可以把下面这段提示词复制给 ChatGPT。


### 复制给 ChatGPT 的配置提示词

需要使用具备本机文件和终端权限的 ChatGPT/Codex。没有本机权限时，请让它提供手动操作步骤。

```text
请帮我部署 https://github.com/Needy1100/chatgpt-log-guardian 发布页中的脚本。
先阅读 README 和详细说明，确认实际诊断日志库的位置、结构及 Python 环境。
把脚本放在固定的可写目录，配置每 30 分钟运行一次的当前用户计划任务；
避免重复任务，不使用最高权限，整轮运行时限设为 5 分钟。
确认清理前备份可校验，验证脚本运行结果及任务配置，再告诉我如何暂停。
只处理本项目诊断日志，不修改系统文件、聊天历史或记忆存储；
不输出或上传日志正文、数据库和备份。若不兼容，停止并说明原因。
如果你没有本机执行权限，请给我手动操作步骤，不要声称已完成部署。
```

## 更新后还能用吗？

ChatGPT 桌面端大版本更新后，先确认脚本指向的是新版实际使用的诊断日志，并核对日志路径和数据库结构。兼容性检查、完整清理规则和故障处理见 [详细说明](docs/DETAILS.md)。

[MIT 许可证](LICENSE)
