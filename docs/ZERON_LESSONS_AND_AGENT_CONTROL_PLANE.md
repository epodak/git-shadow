# Zeron 可吸收设计：Agent Control Plane 与 Persistent Remote Computer

> 目的：记录 Zeron 对 git-shadow 的可迁移设计，以及它揭示出的长期产品方向。
>
> 核心结论：**不把 Zeron 作为 git-shadow 的替代品，也不复制其 workspace 同步模型；吸收其 Engine / Viewport 分离、headless remote control、durable command plane、Agent harness 与 Rust native daemon 的设计。**
>
> 更长期的方向是：
>
> ~~~text
> LLM Session
>    ↓
> Durable Agent
>    ↓
> Persistent / Elastic Remote Computer
>    ↓
> 24h Work
> ~~~
>
> 参考：
> - https://github.com/zeronsh/zeron
> - https://github.com/zeronsh/zeron/blob/main/README.md
> - https://github.com/zeronsh/zeron/blob/main/ARCHITECTURE.md
>
> 截至 2026-10-02，本文只记录当前公开仓库能够确认的事实和由此得到的架构假设。本文不意味着引入 Zeron 依赖、兼容其 API，也不意味着立即用 Rust 重写 git-shadow。

---

## 1. Zeron 当前在做什么

Zeron 对自己的定位是 coding agents 的 native control plane，目标 Agent 包括 Claude Code、Codex、Cursor、Devin 等。

它的核心拓扑可以粗化为：

~~~text
Native UI
   ↓
typed RPC
   ↓
Zeron Engine
   ├─ Agent Harness
   ├─ Session
   ├─ Terminal
   ├─ Repo / Worktree
   ├─ Diff
   ├─ Workspace Files
   └─ Auth / Device Identity
          ↓
 optional sync / relay
          ↓
Cloudflare Worker + Durable Objects
          ↓
another Zeron device
~~~

Zeron 的几个关键事实：

1. device-side 主体使用 Rust；
2. UI 使用 gpui；
3. 同一个 binary 可以 headed，也可以 headless；
4. headless engine 可以常驻 VPS；
5. 一台设备可以 follow / drive 另一台设备上的 Agent Session；
6. 本地模式默认不需要账号和网络；
7. 可选同步使用 Loro CRDT + Cloudflare Durable Objects；
8. 远程 peer 可以对远端 workspace 做文件读写；
9. 当前主要 viewport 是 native desktop / mobile，而不是 browser Web app。

所以它的 Remote 更准确地说是：

~~~text
Native Remote / Device Remote
~~~

而不是：

~~~text
Browser Web Remote
~~~

---

## 2. Zeron、CloudCLI、git-shadow 不在同一层

三个系统最容易混淆，但它们控制的对象不同。

| 系统 | 核心对象 | 主要职责 |
| --- | --- | --- |
| git-shadow | Workspace / Target | 把开发状态安全投影到远端计算环境，并维持 Git / Shadow / Runtime / WIP 的 ownership |
| CloudCLI | Project / Session | 用 Web UI 操作远端 Agent |
| Zeron | Device / Space / Session / Agent Run | 原生的跨设备 Agent Control Plane |

可以压缩成：

~~~text
git-shadow
= 把“工作区 / 计算机”准备给 Agent

Zeron
= 控制 Agent 如何在这些计算机上持续工作

CloudCLI
= 给人一个浏览器窗口去操纵 Agent
~~~

因此长期架构不应该是：

~~~text
git-shadow → CloudCLI
~~~

而应该是：

~~~text
                    git-shadow
                        │
                  Workspace Plane
                        │
             ┌──────────┴──────────┐
             ↓                     ↓
      Agent Control Plane      Direct Agent
             │                     │
       ┌─────┴─────┐        ┌──────┼──────┐
       ↓           ↓        ↓      ↓      ↓
   CloudCLI      Zeron    Codex  Claude OpenCode
~~~

这里最重要的是：**CloudCLI 与 Zeron 更接近同一个抽象层；Codex / Claude Code / OpenCode 属于下一层的 Agent Runtime。**

这意味着现有 AgentAdapterRegistry 应继续拆分，而不是简单扩大 if/else 名单。

---

## 3. Rust 的意义：不是“Rust 天生更快”，而是更适合长期驻留的 Engine

Zeron device-side 采用 Rust，这一点值得记录，但不能把结论简化为：

~~~text
Rust == automatically faster
~~~

真正值得吸收的是 Rust 对 git-shadow 未来长期常驻组件的工程属性：

~~~text
single native binary
+ no language runtime dependency
+ predictable memory ownership
+ no GC pause
+ strong concurrency model
+ low daemon/runtime overhead potential
+ easier static distribution
~~~

对于下面这种长期驻留组件，这些属性尤其有价值：

~~~text
Remote Core
Service Agent
Edge Executor
Workspace Watcher
Terminal Multiplexer
Agent Session Host
Transport / Relay Client
~~~

这与当前 Python git-shadow 的目标不同。

当前 Python CLI 的优势是：

- 开发速度快；
- 业务状态机容易修改；
- SSH / Git / 文件编排已有大量实现；
- 当前个人闭环并不存在已验证的 Python 性能瓶颈。

因此正确的演进方式不是“因为 Zeron 用 Rust，所以立即重写”。

应该先得到测量结果：

~~~text
startup latency
resident memory
CPU while idle
watcher cost
event throughput
SSH orchestration overhead
reconnect cost
long-running stability
~~~

然后只把长期驻留、性能敏感、跨平台分发困难的部分作为 Rust candidate。

建议长期形成：

~~~text
Control / Product Logic
        │
        ├─ Python is acceptable
        │
        ▼
Stable Protocol Boundary
        │
        ▼
Native Remote Core
        └─ Rust candidate
~~~

也就是说：

> **Rust 是可能的实现材料，不是新的产品边界。**

---

## 4. 更大的方向：大模型需要一台“自己的远端电脑”

Zeron 与 Railway 放在一起看，会出现一个比单个项目更重要的共同方向。

传统 AI coding 的隐含模型是：

~~~text
Human Laptop
    ↓
IDE / Terminal
    ↓
LLM
    ↓
run command locally
~~~

这个模型的问题是：

- 笔记本必须在线；
- 人睡觉后工作容易中断；
- 网络变化会打断 session；
- 本地环境同时承担私人电脑与 Agent 执行环境；
- 多 Agent 并发会争抢本地 CPU / RAM；
- Agent 生命周期被人类 UI 生命周期绑住。

新的模型更接近：

~~~text
Human Device
    │
    │ intent / supervision
    ▼
Agent Control Plane
    │
    ▼
Persistent Remote Computer
    │
    ├─ repo
    ├─ tools
    ├─ credentials
    ├─ runtime
    ├─ terminal
    ├─ browser
    ├─ cache
    └─ long-running agents
~~~

人类设备逐步退化成：

~~~text
Viewport + Approval + Intervention
~~~

而 VPS / VM / Container / remote host 逐步成为：

~~~text
Agent's Working Computer
~~~

这里的“拥有”不是法律意义或永久绑定，而是：

> 一个 Agent 拥有稳定可寻址、可恢复、可持续执行的计算环境，其生命周期不再依赖人类当前是否打开 IDE。

这可以称为：

~~~text
Model-operated Persistent Computer
~~~

或者从 git-shadow 产品语言看：

~~~text
Persistent Agent Workspace
~~~

为避免把长期方向绑定到某个厂商当前产品名，本文不把 ChatGPT、Grok 或其他厂商的具体功能名称写成架构前提；真正应该追踪的是这个更稳定的收敛趋势：

~~~text
LLM as chat
   ↓
Agent as worker
   ↓
Computer as persistent execution substrate
~~~

---

## 5. 从 Session-first 走向 Computer-first

目前大量 AI 工具还是：

~~~text
Prompt
  ↓
Session
  ↓
Tool Calls
  ↓
End
~~~

但当 Agent 可以持续数小时甚至跨天工作时，稳定对象应该从 Session 上移到 Computer / Workspace：

~~~text
Workspace
   ├─ identity
   ├─ filesystem state
   ├─ git state
   ├─ shadow state
   ├─ runtime
   ├─ credentials
   ├─ cache
   ├─ agent sessions[]
   └─ lifecycle
~~~

Session 变成 Workspace 内的暂态对象：

~~~text
Workspace > Session
~~~

这与 Railway reference 中已经沉淀的结论一致：

~~~text
Persistent Experience != Persistent Compute
~~~

一个 Agent 的“电脑”可以长期存在，但底层 CPU/RAM 可以：

~~~text
ACTIVE
  ↓ idle
SUSPENDED
  ↓ wake
ACTIVE
  ↓ migrate
ANOTHER TARGET
~~~

只要下面这些不变量保持：

~~~text
Workspace Identity
Git State
Shadow State
Runtime Contract
Agent Control State
Return Path
~~~

用户和 Agent 就不必关心底层到底是不是原来那台 VPS。

---

## 6. Zeron 最值得吸收之一：Engine != Viewport

Zeron 明确把 Engine 与 UI 分开。

这对 git-shadow 非常重要。

长期不要形成：

~~~text
CLI owns workflow
Web owns workflow
Desktop owns workflow
IDE owns workflow
~~~

应该是：

~~~text
                 Viewports
      ┌────────────┼─────────────┐
      ↓            ↓             ↓
     CLI          Web          Native
      │            │             │
      └────────────┼─────────────┘
                   ↓
              Core Protocol
                   ↓
               Engine
                   ↓
       Workspace / Agent / Target
~~~

建议 future ViewportKind：

~~~text
web
native
mobile
terminal
api
ide
~~~

于是：

~~~text
CloudCLI → web viewport/control plane
Zeron    → native/mobile viewport/control plane
Codex    → terminal/direct agent
~~~

这还解决一个长期命名问题：

> Web 是交互介质，不是 Agent 类型。

git-shadow 已经通过废弃独立 web 顶级语义避免了一次污染，后续应该继续保持这一原则。

---

## 7. Zeron 最值得吸收之二：Control Plane 与 Agent Runtime 分层

当前 Roadmap 的 AgentAdapterRegistry 将两类对象放在了同一个集合：

~~~text
CloudCLI
Codex
Claude Code
OpenCode
...
~~~

Zeron 让这个问题变得明显。

建议拆为：

~~~text
AgentControlPlaneAdapter
├─ CloudCLI
├─ Zeron
└─ future control planes

AgentRuntimeAdapter
├─ Codex
├─ Claude Code
├─ OpenCode
├─ Command Code
└─ future agents
~~~

Control Plane 的契约可能是：

~~~text
detect()
prepare()
launch_control_plane()
create_session()
attach_session()
send()
interrupt()
observe()
open_viewport()
health()
auth_boundary()
capabilities()
~~~

Agent Runtime 的契约更接近：

~~~text
detect()
prepare()
launch()
resume()
send()
interrupt()
health()
auth_boundary()
capabilities()
~~~

这样组合关系变成：

~~~text
Workspace
   ↓
ControlPlaneAdapter (optional)
   ↓
AgentRuntimeAdapter
~~~

CloudCLI / Zeron 不再被误认为和 Codex 是同一种东西。

---

## 8. Zeron 最值得吸收之三：Durable Intent / Command Plane

Zeron 将 send / steer / interrupt / respondInput 等操作放入 durable command state，而不是把一次临时 RPC 当作事实本身。

git-shadow 已经有：

- TypedExecutionPlan；
- job_id；
- event sequence；
- replay；
- retry；
- resume；
- Lease；
- interrupted；
- cancellation。

两者可以抽象到同一个更稳定模型：

~~~text
Intent
   ↓
Durable Operation / Command
   ↓
Transport
   ↓
Executor
   ↓
Observed State
   ↓
Reconcile
~~~

核心不变量：

~~~text
Intent != Transport
~~~

SSH 断开、WebSocket 重连、设备休眠不应该让“用户想让 Agent 做什么”丢失。

因此长期应该避免：

~~~text
RPC success == operation success
~~~

而应该使用：

~~~text
Desired
  ↓
Accepted
  ↓
Executing
  ↓
Observed
  ↓
Reconciled
~~~

这与现有 service-agent / edge-agent 的 durable operation 方向一致，不需要另造第二套协议。

---

## 9. Zeron 最值得吸收之四：Space，但 Workspace Authority 必须留在 git-shadow

Zeron 定义的空间接近：

~~~text
Space = Device + Folder
~~~

git-shadow 当前定义：

~~~text
WorkspaceIdentity = Repository + Branch
~~~

未来再叠加 Target：

~~~text
RemoteWorkspace = WorkspaceIdentity + Target
~~~

两者可以建立映射：

~~~text
git-shadow Target       → Zeron Device
git-shadow Remote Path  → Zeron Folder
git-shadow Workspace    → Zeron Space
~~~

例如：

~~~text
repo   = AI
branch = foo/bar
target = aws-us

git-shadow:
~/wkspace/AI/foo/bar

Zeron:
device = aws-us
space  = ~/wkspace/AI/foo/bar
~~~

但 ownership 必须明确：

~~~text
Workspace Authority = git-shadow
Agent Session Authority = selected control plane
~~~

Zeron 可以控制已经存在的远端 workspace，但不能反过来重新定义 git-shadow 的 Git / Shadow / Runtime / WIP ownership。

---

## 10. 明确不要吸收：Zeron 的 peer-trust workspace sync 边界

Zeron 当前的 synced account 设计把同账号设备视为 trusted peers。

远程 peer 可以读取和写入 workspace 文件；启用 ignored files 后，像 .env 这样的 gitignored 文件也可能被远程访问，只有 .git 被固定排除。

这与 git-shadow 的安全哲学不同。

git-shadow 已经建立：

~~~text
GitTracked != Shadow != Runtime != WIP
~~~

以及：

~~~text
ShadowCandidates
    ∩ .gitshadow allowlist
    - .shadowignore
    = Actual Shadow Projection
~~~

这条不变量必须保留。

因此：

- 不要用“远程文件树同步”替代 Shadow Lane；
- 不要因为某个 control plane 能看到 workspace 就扩大本地→远端的数据授权；
- Zeron/CloudCLI 修改远端 tracked 文件后，仍通过 Git 返回；
- 修改 Shadow 文件后，仍通过 Shadow CAS 返回；
- Runtime 仍然留在远端；
- WIP 仍然显式授权。

Control Plane 的权限不能吞掉 Workspace Projection 的权限模型。

---

## 11. Web Remote：Zeron 当前不是 CloudCLI 的直接替代

截至本文记录时间，Zeron 可以：

~~~text
remote device control       yes
headless VPS engine         yes
cross-device agent control  yes
native desktop viewport     yes
mobile viewport             yes
browser Web viewport        not current primary implementation
~~~

因此不能把：

~~~text
Zeron Remote
~~~

直接等价成：

~~~text
CloudCLI Web Remote
~~~

但 Zeron 的 Engine / RPC 分离意味着从架构上看，未来可以出现：

~~~text
Browser
   ↓
Web Viewport
   ↓
typed RPC
   ↓
Zeron-like Engine
~~~

对 git-shadow 的启发不是“自己马上做一个 Web UI”，而是：

> Viewport 应该是可替换对象，Web、Native、Mobile、Terminal 都不能反过来定义 Workspace/Agent 的核心状态机。

---

## 12. 对 git-shadow 的具体吸收方向

### 12.1 近期：只改模型，不重写实现

当前个人闭环优先级不变。

先完成：

1. 把 Control Plane 与 Agent Runtime 从概念上拆开；
2. Agent adapter capability 改成声明式；
3. 把 Zeron 记录为 future ControlPlaneAdapter candidate；
4. 明确 ViewportKind；
5. 保持 CloudCLI 当前 Web Remote 路径正常工作。

不要因为这份 reference 阻塞 P0 闭环。

### 12.2 中期：让 Remote Core 成为稳定边界

逐步形成：

~~~text
Local Controller
      ↓
Stable Protocol
      ↓
Remote Core
      ├─ workspace operations
      ├─ durable commands
      ├─ event stream
      ├─ terminal
      ├─ agent adapters
      └─ lifecycle
~~~

当前 edge-agent / service-agent 是这个方向的原型。

### 12.3 中期：评估 Rust Remote Core

只在有基准数据后评估：

~~~text
Python service-agent
      ↓ protocol-compatible replacement
Rust remote-core
~~~

要求：

- 同一 TypedExecutionPlan；
- 同一 event schema；
- 同一 workspace ownership；
- 同一 recovery semantics；
- Python/Rust 可以在迁移期能力平权。

不要 Big Bang rewrite。

### 12.4 长期：Persistent Remote Computer

长期对象可以收敛为：

~~~text
AgentComputer
├─ workspace_id
├─ target
├─ runtime
├─ control_plane
├─ agent_sessions
├─ durable_operations
├─ access_paths
├─ lease
├─ suspend/resume
├─ retained_state
└─ cost_policy
~~~

用户不应该管理：

~~~text
VPS #1
VPS #2
Docker Host
Cloud VM
~~~

而应该管理：

~~~text
My Workspace
My Agent Computer
~~~

底层 Target 是 replaceable implementation。

---

## 13. 与已有 references 的关系

三份 reference 现在可以形成一条连续的尺度链：

~~~text
OpenShip
  ↓
Target / Capability / Durable Operation
  ↓
Railway
  ↓
Identity / Ephemeral Workspace / Claim / Managed Compute
  ↓
Zeron
  ↓
Agent Control Plane / Headless Engine / Multi-device Remote
~~~

组合之后，git-shadow 的长期形态更清晰：

~~~text
Identity
   ↓
Workspace Resolver
   ↓
Workspace Authority
   ↓
Target
   ↓
Remote Core
   ↓
Agent Control Plane
   ↓
Agent Runtime
   ↓
Viewport
~~~

其中：

~~~text
Git / Shadow / Runtime / WIP ownership
~~~

仍然是 git-shadow 自己必须守住的核心差异。

---

## 14. Object / Morphism 映射

### Objects

~~~text
Workspace
Target
RemoteCore
ControlPlane
AgentRuntime
Session
Viewport
Operation
~~~

### Morphisms

~~~text
project      : LocalWorkspace → RemoteWorkspace
resolve      : WorkspaceIntent → Workspace
allocate     : Workspace → Target
host         : Target → RemoteCore
attach       : RemoteCore → ControlPlane
launch       : ControlPlane → AgentRuntime
observe      : AgentRuntime → SessionState
render       : SessionState → Viewport
suspend      : ActiveComputer → RetainedComputer
resume       : RetainedComputer → ActiveComputer
reconcile    : DesiredState → ObservedState
~~~

### Functor-like mappings

~~~text
Zeron Space
≈ (Device, Folder)
→ git-shadow (Target, RemoteWorkspace)

Zeron Engine
→ candidate RemoteCore pattern

Zeron UI
→ Native Viewport

CloudCLI
→ Web ControlPlane + Web Viewport
~~~

---

## 15. 不变量与 Fixed Point

### 不变量

1. **Workspace Authority stays in git-shadow.**
2. **GitTracked != Shadow != Runtime != WIP.**
3. **Control Plane is replaceable.**
4. **Agent Runtime is replaceable.**
5. **Viewport is replaceable.**
6. **Transport is not Intent.**
7. **Persistent Workspace does not require Persistent Compute.**
8. **Remote access capability must not silently widen private-file authorization.**
9. **Rust adoption must follow measured need, not language fashion.**

### Fixed Point 假设

如果方向正确，无论以后使用：

~~~text
AWS
Hetzner
Railway
local workstation
container
microVM
Zeron
CloudCLI
Codex
Claude Code
Web
Native app
mobile
~~~

用户面对的核心状态机仍然应该近似：

~~~text
Project
   ↓
Resolve Workspace
   ↓
Resume / Allocate Computer
   ↓
Start or Attach Agent
   ↓
Work Continuously
   ↓
Observe / Intervene
   ↓
Commit / Return Changes
   ↓
Suspend / Resume
~~~

底层实现不断变化，但这个状态机保持稳定。

这可能是 git-shadow 更长期的 Fixed Point：

> **不是“把代码同步到 VPS”，而是给 AI 一个可恢复、可替换、可远程监督、可以持续工作的 Computer，同时让人类本地工作区仍然保持事实源和安全边界。**
