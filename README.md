# Agent Traffic Light

跨平台 AI Agent 状态悬浮系统原型：启动后扫描本机运行中的 Agent，从菜单栏/系统托盘选择接入目标，并用红、黄、绿三色表达状态。

## 技术选型

当前实现采用 **Python + pystray + psutil + 原生通知命令**：

- `pystray` 提供 Windows 系统托盘、macOS 菜单栏、Linux 托盘后端；它要求 `Icon.run()` 在主线程运行，macOS 也是这个模型。
- `psutil` 负责低开销进程扫描，支持 Windows/macOS/Linux，并优先使用 `process_iter()` 避免 PID 枚举竞态。
- 原生通知直接走系统能力：macOS 使用 `osascript display notification`，Windows 使用 WinRT Toast，Linux 使用 `notify-send`。
- 核心层不依赖托盘 UI：扫描、状态监听、控制器、通知全部可测试，后续迁移到 Rust/Tauri 或新增第 4 个 Agent 不需要重写识别逻辑。

Rust/Tauri 也是可行路线：Tauri v2 托盘和通知集成更完整，适合正式产品化安装包；纯 Rust `tray-icon + sysinfo` 更轻，但菜单事件循环、通知、打包要写更多平台胶水。这个仓库先用 Python 落地低成本可运行版本，同时保留清晰的架构边界。

## 状态语义

- 绿色闪烁：`BUSY`，Agent 正在执行。
- 绿色常亮：`IDLE`，Agent 已执行完成或当前空闲。
- 黄色：`NEEDS_INTERACTION`，暂停、等待授权或用户输入。
- 红色：`ERROR`，报错或异常停止。
- 灰色：`UNCONNECTED/DISCONNECTED`，未接入或目标进程断开。

右键菜单会直接展示这组灯语说明，方便用户不记颜色语义也能确认当前状态。

## 架构

```text
agent_light/
  definitions.py       # Codex Desktop / Codex CLI / Cloud Code CLI 特征定义
  process_source.py    # psutil 进程快照，带无依赖 subprocess fallback
  scanner.py           # AgentMatcher + AgentScanner，只负责发现候选程序
  status.py            # 状态提供者：JSON sidecar 优先，进程启发式兜底
  controller.py        # 单一接入、切换释放、通知触发、订阅发布
  tray_app.py          # pystray 菜单栏/系统托盘适配层
  notify.py            # Windows Toast / macOS Notification Center / Linux notify-send
  cli.py               # --scan、--headless、托盘启动入口
```

`definitions.py` 是新增 Agent 的主要入口。第 4 个 Agent 只需要补一个 `AgentDefinition`，如果它有真实 IPC/SSE/WebSocket/本地文件状态源，再新增一个 `StatusProvider` 插到 `CompositeStatusProvider` 前面。

## 运行

```bash
python3 -m pip install -r requirements.txt
python3 -m agent_light --scan
python3 -m agent_light
```

无 GUI 调试：

```bash
python3 -m agent_light --scan --json
python3 -m agent_light --headless --agent codex_cli
```

## 精确状态接入

真实 Agent 若能写 sidecar JSON，状态延迟由轮询间隔控制，默认 `0.25s`，满足 500ms 内同步：

```json
{
  "agent_id": "codex_cli",
  "status": "needs_interaction",
  "message": "Codex CLI 正在等待权限审批",
  "milestone": true,
  "timestamp": 1790771523.12
}
```

默认路径：

- `~/.agent-traffic-light/codex-desktop*.json`
- `~/.agent-traffic-light/codex-cli*.json`
- `~/.agent-traffic-light/cloud-code*.json`
- `~/.agent-traffic-light/claude-code*.json`

没有 sidecar 时，系统使用进程启发式兜底：进程存在且 CPU 高于阈值视为执行中，停止态视为需要交互，否则视为空闲。这能保证轻量与可用，但精确的“等待用户授权”最好由 Agent 或 wrapper 明确发状态。

## 测试

```bash
python3 -m unittest discover -s tests
```
