# ADR — 自动化心跳同步律与 Linux 治理契约 (Continuous Auto-Sync & Linux Daemon Governance Law)

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: `git_shadow.watch` + Linux 远端守护进程治理 + 自动卸载自毁生命周期
- **Canonical Owners**:
  - `AutonomousRemoteDaemon` (范式 B · 远端 Linux 服务治理常驻执行端)
  - `EphemeralSessionWatch` (范式 A · 前台随行会话监听)
  - `LeaseBasedAutoTeardown` (心跳租约超期自毁与内存释放法则)
  - `ContinuousAutoSync` (无感常态化自动同步)
  - `ZeroOverheadHeartbeat` (零内存开销内核事件心跳)
  - `TransparentExclusionContract` (透明排除契约 · 杜绝暗箱吞文件)
- **依赖决策**：同步对象的边界由 [分层同步所有权与影子 CAS 律](2026-09-15_LAYERED_SYNC_OWNERSHIP_LAW.md) 定义。本 ADR 只规定自动触发、租约和生命周期。

---

## 1. Context (背景与治理诉求)

在跨端云端开发与 AI 协同过程中，代码与影子配置的自动触发面临两个层面的现实冲突：
1. **离散命令的认知摩擦**：每次修改 `.gitshadow` 文件都需要在终端手动执行一次 `git shadow push`，极易发生“本地已保存但云端跑旧配置”的脱节事故。Git 追踪代码仍必须遵循 commit/push/fetch/pull，不由 watcher 伪装成文件同步。
2. **VPS 资源与常驻治理的焦虑**：
   - 用户顾虑：VPS（尤其 1C1G/1C2G 轻量云主机）内存极其宝贵，实时同步若常驻会不会吃光内存和 CPU？
   - 治理诉求：如果将实时同步推送到 Linux 系统的治理逻辑中（后台守护或服务），该由哪个命令触发？**能加载（Load）就必须能卸载（Unload）**。
   - 容灾与资源清理：若用户拔掉网线、关机或下班离开，远端会不会残留无尽的僵尸进程？是否必须设计**自动卸载（Auto-Teardown）以释放内存**？

经过架构评估，确立**范式 B（Linux 系统级服务治理，主导）**与**范式 A（客户端前台随行会话，互补）**的双轨治理体系。

---

## 2. Decision (架构决策与核心契约)

### 2.1 双轨交互范式定义 (Paradigm Definition)

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        git-shadow 实时同步治理双轨                       │
├──────────────────────────────────┬─────────────────────────────────────┤
│  范式 B：远端独立服务治理 (Primary)   │    范式 A：前台随行会话监听 (Secondary)│
│  Autonomous Remote Daemon        │    Ephemeral Session Watcher        │
├──────────────────────────────────┼─────────────────────────────────────┤
│ 触发: git shadow service load/unload│ 触发: git shadow run <host> --watch │
│ 承载: Linux 远端后台任务服务 / User Service │ 承载: 本地客户端进程伴随               │
│ 治理: 显式加载、卸载与租约自毁       │ 治理: 随终端前台会话存在，Ctrl+C 即毁  │
│ 场景: 长期开发、断线可恢复任务执行      │ 场景: 短平快桌面编码、即开即用、零残留  │
└──────────────────────────────────┴─────────────────────────────────────┘
```

---

### 2.2 范式 B 深度规范：Linux 治理、加载/卸载与自动自毁 (`AutonomousRemoteDaemon`)

#### A. 核心命令映射与配置真源
在 Linux 端治理逻辑中，远端任务承载由 `git shadow service` 命令族统领；本地文件变化仍由
控制端 watcher 发现，再通过这个服务执行同一套 TypedExecutionPlan：
- **`git shadow service <host> load`**：将当前项目的服务执行端安装、注册并加载至 Linux 治理逻辑。
- **`git shadow service <host> unload`**：显式从 Linux 治理逻辑中注销并卸载守护进程。
- **`git shadow service <host> status`**：探查远端守护进程运行状态、PID、内存占用与心跳租约剩余时间。

配置真源与运行时结构统一置于远端用户主目录下：
```text
~/.local/share/git-shadow/
├── bin/
│   ├── git-shadow-edge-agent.py   # 一次性与常驻服务共享的 TypedExecutionPlan 执行器
│   └── git-shadow-service-agent.py # Unix socket 服务入口
├── services/<project-hash>/
│   ├── service.sock               # SSH 转发进程接入的本地 Unix socket
│   ├── pid / lease                # 进程锁与心跳租约
│   └── state.json / service.log   # 脱敏服务状态与诊断日志
└── runs/<job-id>/                  # 任务的脱敏 durable state 与事件日志
```

#### B. 加载逻辑 (Load / Attach Workflow)
1. **安装与注册**：通过 SSH Diff-First 上传两个 standalone Python 执行器，并为项目创建服务目录；
2. **启动**：用 `nohup` 启动 Unix socket 服务入口，不要求 VPS 安装本地 Python 包；
3. **注册租约**：生成 `<project-hash>.pid`，写入当前的 UNIX 时间戳到 `<project-hash>.lease`（默认租约 TTL = 600 秒 / 10 分钟）；
4. **初始化对齐**：本地通过 `run/push/pull --service` 提交一次 TypedExecutionPlan，先执行 Git 基线，再按需执行 Manifest/CAS；两者不得合并成无条件的全量文件覆盖。

#### C. 卸载逻辑 (Unload / Detach Workflow)
1. **显式信号通知**：本地发送 `git shadow service <host> unload`，通过 SSH 发送 `SIGTERM` 信号给 PID 文件中的目标进程；
2. **优雅退出**：远端服务捕获 `SIGTERM`，关闭 Unix socket 与执行线程；
3. **物理清理与内存归还**：清理服务 PID/Lease 和 socket，彻底退出，**向 Linux 内核归还服务进程资源**。

#### D. 自动卸载自毁机制 (`LeaseBasedAutoTeardown`)
> ⚠️ **内存与僵尸进程防御铁律**：必须实行基于心跳租约的自动卸载！

- **为什么必须自动卸载？**
  如果用户合上笔记本电脑、网络断开或遗忘卸载，远端若无休止常驻，多项目累积将耗尽 VPS 的进程和系统内存。
- **心跳租约自毁机制 (Heartbeat Lease TTL)**：
  1. 服务进程在启动时携带租约超时参数（默认 `--lease-ttl 600`，即 10 分钟）；
  2. 当本地客户端活着时，本地在后台定期（如每 60 秒）向远端发送一次轻量租约刷新（`touch ~/.local/share/git-shadow/leases/<project-hash>.lease`）；
  3. 远端服务每隔约 1 秒检查 lease 文件时间戳：
     ```bash
     now=$(date +%s)
     last_lease=$(stat -c %Y "$LEASE_FILE")
     if [ $((now - last_lease)) -gt "$TTL" ]; then
         echo "[$(date)] 心跳租约过期 ($TTL s 无握手)，执行自动卸载并自毁以释放内存..." >> "$LOG_FILE"
         rm -f "$PID_FILE" "$LEASE_FILE"
         exit 0
     fi
     ```
  4. 一旦超过 10 分钟无本地心跳，**守护进程自动退出，释放所有内存与句柄**，系统完全自愈！

---

### 2.3 范式 A 规范：前台随行会话监听 (`EphemeralSessionWatch`)

- **触发形态**：
  ```bash
  git shadow run <host> --watch
  # 兼容别名由 CLI 决定，但语义仍归属于 run
  ```
- **契约行为**：
  1. 命令执行时，前台保持挂起状态并显示动态心跳状态条；
  2. 自动唤起系统默认浏览器弹出 CloudCLI Web 界面；
  3. Linux 当前使用 inotify、其他平台使用轮询 fallback 发现 `.gitshadow` 文件并提交 Shadow CAS 任务；Git 追踪文件仍通过 Git 提交和拉取；
  4. **随行退出**：当用户在终端按下 `Ctrl + C`，本地 watcher 会话终止；远端服务继续按 Lease 规则存活或自毁，不会留下无租约的僵尸进程。

---

### 2.4 零开销内核级心跳通道 (`ZeroOverheadHeartbeat`)

- **目标约束**：原生 watcher 方案应采用 OS 内核事件机制，在无文件改动时降低 CPU 唤醒；当前已落地 Linux inotify，其他平台仍使用轮询 fallback。
- **内存红线约束**：服务端不得加载本地项目依赖，常驻执行端的资源占用必须纳入后续真实 VPS smoke test 验收。

---

## 3. Consequences (治理收益与权衡)

1. **确定性掌控**：用户拥有完整的命令控制权——`service load` 加载云端任务服务，`service unload` 随手注销，随时 `service status` 检查；
2. **彻底解决内存焦虑**：通过 10 分钟心跳租约（`LeaseBasedAutoTeardown`），即使断网断电，VPS 也能在 10 分钟内自动卸载守护进程并释放内存，杜绝一切僵尸进程；
3. **双模自由切换**：
   - 喜欢随开随走的用户用 `run <host> --watch`（范式 A）；
   - 需要断线可恢复任务执行的用户用 `service load`（范式 B）；双向文件同步仍需本地 watcher 在线。
