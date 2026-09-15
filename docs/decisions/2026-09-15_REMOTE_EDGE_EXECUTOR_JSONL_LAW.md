# ADR — VPS 边缘执行器与 JSONL 任务广播律

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: 本地 `git-shadow` 与 VPS 边缘执行器之间的批量任务、异步事件、断线恢复和 CloudCLI 会话编排
- **Canonical Owners**:
  - `RemoteEdgeExecutor`：VPS 端单文件、低依赖任务执行器
  - `JsonlOverSshTransport`：基于干净 SSH 通道的双向 JSONL 协议
  - `DurableJobJournal`：VPS 端任务状态与事件序列日志
  - `ReplayableBroadcast`：按 `seq` 断线重放事件
  - `TypedExecutionPlan`：结构化动作计划，禁止默认拼接任意 Shell 字符串
- **依赖决策**：跨端状态的所有权由 [分层同步所有权与影子 CAS 律](2026-09-15_LAYERED_SYNC_OWNERSHIP_LAW.md) 定义；本 ADR 只定义如何执行和广播这些动作。

## 1. Context

此前本地客户端分别通过 SSH 执行探针、Git 对齐、影子注入、补丁应用和 CloudCLI 会话查找。每一步都需要一次往返，长任务期间本地必须负责全部编排，网络断开时也无法可靠恢复。更重要的是，已追踪代码和 `.gitshadow` 私有文件曾被错误地混入同一套文件覆盖流程。

VPS 端需要能够接收一组命令，在远端完成执行，并把进度异步广播回本地。CloudCLI 的 Project 和 Session 创建也应属于同一个远端任务，而不是依赖本地查询“最新会话”来猜测目标。

## 2. Decision

### 2.1 传输层：SSH 双向 JSONL

第一版不新增 WebSocket/SSE 公网端口，复用现有 SSH 鉴权和网络路径：

```text
ssh -o RemoteCommand=none -o RequestTTY=no <host> \
  '$HOME/.local/share/git-shadow/bin/git-shadow-edge-agent.py --rpc'
```

stdin 发送 JSONL 请求，stdout 返回 JSONL 事件。所有非交互 SSH 必须显式关闭 `RemoteCommand` 和 TTY，以避免远端 `~/.ssh/config` 中的 zellij/tmux 配置污染自动化通道。

### 2.2 任务语义：同步接收、异步执行

提交任务后立即返回：

```json
{"type":"accepted","job_id":"job-..."}
```

执行过程广播：

```text
ready
accepted
job.started
step.started
output
step.succeeded / step.failed
session.ready
job.completed / job.failed
```

因此调用方不会被长时间 Shell 阻塞，同时仍然能确定任务是否已被 VPS 接收。

### 2.3 断线恢复：事件必须带单调递增 seq

VPS 为每个任务保存：

```text
~/.local/share/git-shadow/runs/<job_id>/
├── request.json       # 已脱敏请求元数据
├── state.json         # 当前状态
└── events.ndjson      # 可追加、可重放事件日志
```

客户端重新连接时发送：

```json
{"type":"resume","job_id":"job-...","after_seq":42}
```

执行器只重放 `seq > 42` 的事件，避免网络抖动造成状态丢失或重复猜测。

### 2.4 执行层：TypedExecutionPlan

任务步骤必须是结构化动作：

```text
workspace.prepare
shadow.sync
patch.apply                 # 仅在用户显式选择 --wip 时出现
cloudcli.session
exec(argv=[...])
```

`workspace.prepare` 只建立或对齐 Git 代码基线；它不是全量文件同步。`shadow.sync` 执行独立的 Manifest/CAS 原子投影，遇到远端基线变化必须发出冲突事件并让任务失败。`patch.apply` 是一次性 WIP 例外，不属于默认同步路径。

默认禁止把多个命令拼成一条未校验的 Shell 字符串。`exec` 也只接收 argv 数组，并限制工作目录在远端用户主目录内；动作内部负责超时、进程组回收和失败事件。

### 2.5 CloudCLI 会话边界

远端执行器按明确路径创建 CloudCLI Project 和 Session：

```text
workspace.prepare
  → shadow.sync (CAS；冲突则停止)
  → POST /api/projects/create-project
  → POST /api/providers/sessions
  → event: session.ready
```

`session.ready` 事件携带 provider、projectPath、sessionId 和公共 URL。本地收到后打开 `/session/{id}`，不再扫描 SQLite 并猜测“最新会话”。

供应商在 Session 创建前确定：命令行可使用 `--provider`，交互式运行则在本地菜单选择。当前 CloudCLI 的 `sessions.provider` 是必填字段，因此暂不创建 provider-neutral 的临时 Session。

### 2.6 资源治理

短任务的 SSH RPC 进程在任务完成后退出；长期任务沿用 `LeaseBasedAutoTeardown`，由心跳刷新 600 秒租约。断线超过租约后，VPS 端清理 PID、租约和运行句柄，不允许遗留常驻僵尸进程。

## 3. Consequences

正面结果：

- 本地只提交一个任务计划，减少 SSH 往返和状态编排；
- 远端任务可以继续执行，本地断线后可重放；
- CloudCLI Session URL 来自本次任务，不会误打开另一个项目的旧会话；
- 不需要新增公网 RPC 端口，复用已有 SSH 安全边界。

代价：

- VPS 必须安装单文件 `git-shadow-edge-agent.py`；
- CloudCLI 内部 API 的鉴权需要通过远端环境变量提供，token 不进入事件日志；
- 未来如需浏览器直接订阅事件，可以在此协议之上增加 SSE/WebSocket 适配层，但不能替换 SSH 控制面。
