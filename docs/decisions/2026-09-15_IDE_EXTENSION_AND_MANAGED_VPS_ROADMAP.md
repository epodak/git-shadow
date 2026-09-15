# ADR — 本地工作区启动器、Agent 适配与托管 VPS 产品路线

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: 本地产品入口、无 GitHub 用户 onboarding、远端 Agent 适配和托管 VPS 商业化路线
- **Canonical Owners**:
  - `StandaloneClientDistribution`：不依赖用户本地 Python 环境的独立客户端分发
  - `LocalWorkspaceLauncher`：选择本地文件夹并一键启动远端开发工作区的主用户入口
  - `OptionalIdeIntegration`：IDE 内的可选控制面集成，不是产品准入条件
  - `WorkspaceSnapshotLane`：没有 GitHub/Git Remote 时的本地代码安全投影与 diff 回写
  - `AgentAdapterRegistry`：CloudCLI、AGY/Antigravity、Codex、Claude Code、Command Code 等 Agent 的统一适配契约
  - `ManagedVpsControlPlane`：托管 VPS 的租户、工作区、生命周期、用量和计费控制面
  - `SelfHostedSshParity`：自有 VPS/SSH 模式与托管 VPS 的能力平权边界

## 1. Context

如果把产品主入口定义为 IDE 扩展，受众会被限制为愿意使用特定 IDE 的开发者；如果把产品做成另一个 Codex、Antigravity、WorkBuddy 或 ZCode，又会和“用户只需调用一次”的目标冲突。`git-shadow` 不应该和 Agent 的编辑器体验竞争，而应该成为 Agent 与本地工作区、远端 Linux 环境之间的稳定连接层。

另一个限制是 GitHub onboarding。当前 Git lane 对已有仓库非常高效，但“有编程需求”并不等于“已经知道 GitHub”。没有 GitHub 的用户仍然应该能够从本地文件夹开始，并在需要时再导出到 GitHub；GitHub 不应成为产品准入条件。

产品真正要解决的是三件事：

1. 在本地网络不适合直接使用时，稳定地使用 Codex、Claude Code 等远端 Agent；
2. 在浏览器中直接打开一个已经准备好的 Web Remote 项目；
3. 本地代码始终可见、可控，远端 AI 的修改经过 diff/确认后才回到本地。

## 2. Decision

### 2.1 产品定位：本地工作区启动器，不是 IDE 或 Agent

主产品形态调整为独立的跨平台本地程序（桌面启动器，可附带 CLI）：

```text
用户下载程序
    ↓
选择本地文件夹 / 新建项目
    ↓
选择 Codex、Claude Code 或其他 Agent
    ↓
程序准备托管 VPS 或用户自有 VPS
    ↓
打开浏览器 Web Remote 项目
    ↓
远端执行，修改以 diff 返回本地
```

- 用户不需要知道 Git、GitHub、Python、SSH 或 VPS 的细节即可完成第一次启动；
- IDE 扩展降级为可选集成，面向希望在编辑器内操作的用户；
- Codex、Claude Code、Antigravity 等是可插拔的执行目标，`git-shadow` 提供的是工作区、网络、凭证和生命周期编排；
- CLI 仍然是开发者、自动化和自有 VPS 场景的强力入口，但不再承担唯一的产品 onboarding。

这保留了“一次调用”的体验：用户只需点击一次“开始远程开发”，内部的探针、代码投影、Shadow CAS、依赖安装、Agent 启动和浏览器深链全部由 Core Client/托管控制面编排。

### 2.2 分发策略：独立程序为主，Python 为可选开发入口

采用以下分发层级：

```text
Desktop Launcher / Standalone CLI   普通用户，不要求预装 Python
              ↓ local process protocol
git-shadow Core Client               复用计划、Shadow CAS、watch、恢复和安全边界
              ↓ clean SSH / HTTPS
Self-hosted VPS 或 Managed VPS       原生运行依赖与 Agent
```

- Windows、macOS、Linux 提供带运行时的 standalone client/桌面安装包；
- Python 包和源码 wrapper 保留给贡献者、调试者和已有 Python 环境的用户；
- IDE 扩展只调用 Core Client，不复制 SSH、JSONL、CAS 或 watcher 实现；
- 后续即使将核心迁移到 Rust/Go 等原生实现，也保持本地进程协议不变。

因此，Python 是实现与开发入口，不是普通客户的产品入口。

### 2.3 GitHub 是加速器，不是准入条件

保留现有 Git lane 作为最佳路径：有 Git Remote 的项目通过 clone/fetch/push 获得高效历史和合并语义。对于没有 GitHub 或尚未初始化 Git 的本地项目，增加 `WorkspaceSnapshotLane`：

- 用户显式点击“启动远程项目”后，程序扫描本地文件清单；
- 仅投影源代码和用户允许的项目文件，依赖目录、构建产物、缓存和 Shadow 私密文件继续按既有规则隔离；
- 使用带 manifest/hash 的加密分块快照传输，不采用无边界的全量 rsync，也不把快照内容写入事件日志；
- 本地目录仍是用户可见的主副本，远端是可销毁的执行副本；
- 远端 AI 产生的代码修改先生成 patch/diff，用户确认后才应用到本地；并发修改必须显式冲突，禁止静默覆盖；
- 用户之后可以选择“初始化 Git / 导出 GitHub”，再切换到 Git lane。

`WorkspaceSnapshotLane` 只服务于未发布或尚未接入 Git Remote 的本地工作区，不改变“公有代码走 Git、私密状态走 Shadow、重型依赖走远端原生”的既有三态边界。

### 2.4 Agent 适配：注册表 + 能力契约

引入 `AgentAdapterRegistry` 的产品概念。每个适配器至少声明：

```text
id                  cloudcli | codex | claude-code | agy | commandcode | ...
kind                web | terminal
detect              远端安装/运行状态探测
prepare             可选的原生安装或环境准备动作
launch              会话创建、深链或终端 argv
health              版本和可用性检查
auth_boundary       凭证由本地、VPS 环境或 Agent 自身管理
```

首批 roadmap 适配目标为：

- `cloudcli`：沿用现有 Project/Session 与 `session.ready` 契约；
- Codex、Claude Code：作为远端稳定使用的核心 Agent 适配目标；
- `agy`/`antigravity`：作为 Antigravity 方向的适配器别名，先验证其启动方式、认证边界和 Web/终端形态；
- Command Code、OpenCode：沿用现有终端 Agent 入口，并补齐探测、安装提示和失败诊断。

适配器只负责 Agent 生命周期和启动语义，不得拥有另一套文件同步协议。用户自有 VPS 与托管 VPS 必须消费同一适配器描述。

### 2.5 商业闭环：Managed VPS 是可选托管数据面

提供一个类似 CloudCLI 的托管服务，但产品边界分为两种模式：

```text
Self-hosted mode                  Managed mode
用户提供 VPS + SSH                 平台提供/托管 VPS
git-shadow 控制端                  IDE 扩展 / CLI / Web 控制台
Edge/Service Agent                 同一 Edge/Service/Adapter 栈
用户承担主机费用与运维               平台承担编排、隔离、计费与运维
```

Managed VPS 的最小商业闭环为：下载程序 → 注册/登录 → 选择 Agent 与规格 → 选择本地文件夹 → 创建隔离工作区 → 投影代码 → 打开 Web Remote → 查看/确认 diff → 停止或销毁并计费。必须优先实现以下护栏：租户隔离、SSH/Agent 凭证边界、磁盘/内存/运行时长配额、自动闲置回收、审计日志、备份/销毁确认和成本上限。

商业化不应先复制一套全新的云端执行器。第一阶段采用“自有 VPS 能力平权”的托管适配层：托管控制面负责主机池、工作区和账单，数据面复用现有 typed plan、事件重放、Shadow CAS 与 Lease 机制。

## 3. Roadmap 影响

### P1：独立程序与本地首启

- 定义桌面启动器与 Core Client 的稳定本地进程协议；
- 提供 Windows/macOS/Linux standalone client，内置运行时，不要求预装 Python；
- 实现“选择文件夹 → 选择 Agent → 开始远程项目”的首启流程；
- 本地明确展示投影范围、排除项、远端状态和预计资源；
- 提供停止、销毁、重试和恢复入口。

### P1：GitHub 非必需的 Workspace Snapshot Lane

- 设计 manifest/hash/加密分块快照协议；
- 支持普通本地文件夹，不要求 Git 初始化或 GitHub 账号；
- 排除依赖、缓存、构建产物和未经允许的私密文件；
- 远端修改以 patch/diff 返回，本地确认后应用；
- 覆盖中断、重试、冲突、取消和销毁测试；
- 支持随时初始化 Git 或导出到 GitHub。

### P1：Agent 适配器

- 把现有 CloudCLI、OpenCode、Command Code 纳入统一适配描述；
- 增加 Codex、Claude Code 的远端启动与健康检查；
- 验证 `agy`/`antigravity` 的运行形态、认证和安装准备；
- 提供适配器版本兼容矩阵、健康检查和可恢复诊断；
- 允许托管 VPS 与用户自有 VPS 使用同一适配器。

### P2：Managed VPS MVP

- 先支持单一云厂商/单一 Linux 镜像和有限规格；
- 创建、启动、暂停、销毁工作区，绑定 Git-shadow project；
- 复用 Edge/Service Agent 的 Lease、Job、事件重放和 Shadow CAS；
- 用量、闲置回收、成本上限和基础审计；
- 独立程序、CLI 与托管控制面共用 API，同时保留 SSH self-hosted 模式。

### P2/P3：可选生态入口

- VS Code 等 IDE 扩展作为可选控制面，而非产品准入条件；
- Web 控制台用于项目列表、用量、账单和远端工作区管理；
- 多地域/多云、团队权限、共享工作区和 Agent 适配器扩展机制。

## 4. Consequences

- 不懂 GitHub、没有 Python、也不使用 IDE 的用户可以从本地文件夹开始；
- Python 仍是低成本、可调试、对贡献者友好的实现载体；
- 产品不和 Codex、Claude Code、Antigravity 等 Agent 竞争编辑体验，而是提供稳定的远端运行基础设施和安全工作区桥接；
- 本地代码可见且由用户确认回写，降低“代码被上传后失控”的心理门槛；
- GitHub 用户仍然获得最快的 Git 历史同步路径；
- 独立程序、CLI、自有 VPS 和托管 VPS 共享同一同步与恢复语义，减少分叉；
- 托管 VPS 能形成订阅/按量计费闭环，但会引入租户隔离、成本控制和运维责任，不能在没有这些护栏时直接承诺通用云平台；
- `agy`/`antigravity` 的具体启动协议在验证前不能硬编码进核心同步协议。
