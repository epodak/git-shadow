# ADR — 分层同步所有权与影子 CAS 律

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: Git 追踪文件、`.gitshadow` 私有文件、原生依赖的跨端同步边界
- **Canonical Owners**:
  - `GitTrackedLane`：Git commit / fetch / push / pull
  - `ShadowLane`：`.gitshadow` 文件清单与 CAS 原子投影
  - `NativeRuntimeLane`：远端原生依赖与构建产物
  - `ShadowManifestCAS`：本地与 VPS 的基线哈希、冲突检测和确认状态
  - `TrackedWipOptIn`：显式 `--wip` 的一次性未提交补丁

## 1. Context

早期实现把三类状态混在一个“投影”动作里：已追踪代码、被 `.gitignore` 保护的私有文件，以及未提交代码补丁都可能通过 SSH 直接覆盖远端。这会制造两个不同的问题：

1. Git 已经拥有代码历史、分支和合并语义，不应再由文件级补丁替代；
2. `.gitshadow` 没有 Git 历史，但通常是本机与 VPS 各自拥有的运行时状态，简单覆盖会把另一端刚刚修改的内容静默抹掉。

因此，“同步”不是一个统一的文件复制动作，而是三个有明确所有权的状态层。

## 2. Decision

### 2.1 GitTrackedLane：公有代码只走 Git

- Git 追踪的文件由远端仓库和 Git 分支负责：`clone`、`fetch`、`checkout`、`pull`、`push`。
- `git-shadow push` 只负责让 VPS 工作区到达 Git 基线，并不把已追踪文件当作普通文件覆盖。
- 非快进、脏工作区和合并冲突必须交给 Git 显式处理，禁止工具静默强推或覆盖。
- 未提交追踪文件默认留在本地；只有用户显式传入 `--wip`，才把当前 `git diff HEAD` 作为一次性 `patch.apply` 步骤发送。

### 2.2 ShadowLane：私有文件使用独立 Manifest + CAS

每个影子条目携带：

```text
path         相对于远端项目根目录的安全路径
base_hash    上一次远端确认成功时的内容哈希，可为空
local_hash   本次本地内容哈希
content_b64  当前任务的内容；只在传输中存在，不写入任务元数据
deleted      是否请求删除
```

VPS 在写入前读取远端当前哈希，并执行 compare-and-swap：

```text
remote_hash == local_hash       -> 幂等成功
remote_hash == base_hash        -> fsync + 原子替换/删除
其他                            -> 冲突，保留远端原文件和本地冲突副本
```

冲突不得覆盖远端文件。由于 CloudCLI 可以在项目目录创建后立即启动，CAS 冲突可能发生在 Session 已经打开之后；此时必须广播失败、停止后续任务并禁止重复创建 Session。成功的 `shadow.applied` 事件才会推进本地 Manifest 的 `synced_hash`；失败或断线不能伪造同步确认。

Manifest 只保存路径和哈希，不保存密钥内容。目标文件使用同目录临时文件、`fsync` 和 `os.replace`，避免半写入状态被另一端读取。
本地 acknowledgement Manifest 的作用域是 `本地项目路径 + 远端主机 + 远端工作区`；同一项目切换 VPS 或目标目录时必须使用独立基线，不能把一个远端的哈希当成另一个远端的 CAS 基线。旧版未带远端作用域的状态仍可读取，但新 CLI 投影会建立作用域隔离的状态文件。

### 2.3 NativeRuntimeLane：依赖不进入同步协议

`node_modules`、Python venv、编译产物和平台二进制不进入 Git lane 或 Shadow lane。它们必须在 VPS 原生安装或构建。

### 2.4 两端自动同步的边界

本地 watcher、VPS 边缘执行器和 CloudCLI 任务可以自动运行，但必须复用以上边界：

- 代码变化触发 Git 工作流，不能通过隐式文件补丁冒充 Git 合流；
- `.gitshadow` 变化触发 Manifest CAS 任务；
- 一端发现 CAS 冲突时停止该条目并广播可恢复事件，由用户选择合并或重新基于最新内容发送。

## 3. Consequences

- Git diff 只表示真实的代码历史差异，不会被 P2P 文件覆盖造成伪冲突；
- Shadow 文件的并发修改不会静默丢失，代价是冲突需要人工决策；
- 默认任务不再携带未提交代码，远端状态更可预测；
- `--wip` 仍保留给需要把半成品交给远端 Agent 的场景，但它是临时投影，不是同步真源。
