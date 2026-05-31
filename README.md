# Agent 灯塔 / Agent Beacon

Agent 灯塔是一个常驻菜单栏/系统托盘的小工具，用红黄绿状态灯提示本机 AI Agent 的运行状态。

它会在启动时扫描本机正在运行的 Agent，例如 Codex Desktop、Codex CLI、Cloud Code CLI，并把检测到的程序和 Session 显示在右键菜单里。用户选择一个 Session 后，Agent 灯塔只监听这个 Session；切换目标时会自动断开旧监听。

支持多个 Agent、多个 Session 同时存在，但同一时间只接入一个目标。Codex 与 CloudCode/Claude Code 都通过本地 Hook 同步状态；写入 Hook 前会弹窗确认，右键菜单里也可以一键取消所有监听 Hook。

## 状态灯

- 绿灯闪烁：Agent 正在执行任务
- 绿灯常亮：Agent 已完成或当前空闲
- 黄灯：Agent 需要用户确认、授权或输入
- 红灯：Agent 报错或异常停止
- 灰灯：未连接或目标已断开

右键菜单中也会显示这组说明。

## 怎么工作

Agent 灯塔主要通过两种方式判断状态：

1. 扫描系统进程，识别正在运行的 Agent 和 Session。
2. 用户选择 Session 后，经确认写入本地 Hook，让 Agent 在执行、等待授权、完成或报错时写入状态文件。

CloudCode/Claude Code 会使用一个本地 wrapper 脚本转发 Hook 事件，避免不同版本 CLI 对 Hook 参数格式的兼容问题。没有 Hook 状态时，程序会用进程存在性做保守判断：进程存在但没有明确执行信号时，默认显示为空闲。

运行期间的扫描、Hook 写入、状态变化会记录到应用日志，方便排查监听异常。

## 运行

安装包可在 GitHub Releases 下载。

macOS / Linux:

```bash
python3 -m pip install -r requirements.txt
python3 -m agent_light
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m agent_light
```

扫描当前 Agent：

```bash
python3 -m agent_light --scan --json
```

Windows 中把 `python3` 换成 `.\.venv\Scripts\python.exe`。

无界面监听某个 Session：

```bash
python3 -m agent_light --headless --agent codex_cli --session codex_cli:12345
```

启动托盘时也可以直接接入某个 Session：

```bash
python3 -m agent_light --agent cloud_code_cli --session cloud_code_cli:12345
```

## 状态 JSON

Agent 或外部脚本可以写入状态 JSON，让灯塔更准确地同步状态：

```json
{
  "agent_id": "codex_cli",
  "status": "needs_interaction",
  "message": "Codex CLI 正在等待权限审批",
  "milestone": true
}
```

支持的 `status`：

- `busy`
- `idle`
- `needs_interaction`
- `error`
- `disconnected`

常用状态文件目录：

- macOS：`~/.agent-traffic-light/` 或 `~/Library/Application Support/Agent Beacon/`
- Windows：`%APPDATA%\Agent Beacon\` 或 `%LOCALAPPDATA%\Agent Beacon\`
- Linux：`~/.agent-traffic-light/`、`$XDG_STATE_HOME/agent-beacon/` 或 `~/.local/state/agent-beacon/`

## 资源

图标资源在 `assets/`：

- `agent-beacon-icon-1024.png`
- `agent-beacon.ico`
- `agent-beacon.iconset/`

## 测试

```bash
python3 -m unittest discover -s tests
```

## 许可证

MIT License
