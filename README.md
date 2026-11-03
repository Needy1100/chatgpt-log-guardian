# ChatGPT Log Guardian

## 清理 ChatGPT 日志，帮助节省磁盘空间

适用于 Windows 桌面端，清理前自动备份。

## 怎么用

从 [下载页](https://github.com/Needy1100/chatgpt-log-guardian/releases/latest) 下载 `chatgpt_log_guardian.py`，放在固定的可写目录，在 PowerShell 运行：

```powershell
py -3 .\chatgpt_log_guardian.py
```

运行一次检查一次，完成后退出。想每 30 分钟自动清理，可以让 ChatGPT 帮你配置。

## 复制给 ChatGPT 的配置提示词

需要使用具备本机文件和终端权限的 ChatGPT/Codex；没有本机权限时，只能提供操作步骤。

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

## 使用前了解

- 不清理正式聊天历史或长期记忆；诊断日志可能包含内容副本。
- 自动备份会额外占用空间，维护时也可能短暂占用 CPU 和磁盘。
- 应用大版本更新后，应重新检查兼容性。

路径、清理规则、计划任务和故障处理统一见 [详细说明](docs/DETAILS.md)。

[MIT 许可证](LICENSE)
