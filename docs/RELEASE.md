下载脚本即可在 Windows 上清理 ChatGPT 桌面端积累的旧诊断日志。需要 **Python 3.10 或更新版本**。

- `chatgpt_log_guardian.py` 是可直接运行的脚本。
- `SHA256SUMS.txt` 记录脚本的 SHA-256 校验值。可在 PowerShell 中运行 `Get-FileHash .\chatgpt_log_guardian.py -Algorithm SHA256` 并比对结果。

首次运行和定时清理的配置方法见 [使用说明](https://github.com/Needy1100/chatgpt-log-guardian#readme)。
