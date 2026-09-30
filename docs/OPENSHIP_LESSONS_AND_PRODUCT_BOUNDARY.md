# OpenShip 可吸收设计与 git-shadow 产品边界

> 目的：记录 git-shadow 可以从 OpenShip 吸收的架构经验，以及必须保持的产品边界。
>
> 结论先行：**学习 OpenShip 的 Control Plane / Target / Adapter / Durable Operation，不把 git-shadow 做成部署 PaaS。**
>
> 参考：
> - https://github.com/oblien/openship
> - https://openship.io/
>
> 本文不意味着引入 OpenShip 依赖，也不意味着兼容其 API；它是一份架构观察和后续实现约束。

---

## 1. 两个系统解决的问题不同

OpenShip 的主要对象是“运行中的应用”：

~~~text
Source
  ↓
Build
  ↓
Release
  ↓
Deploy Target
  ↓
Runtime
  ↓
Route / TLS / Monitor / Rollback
~~~

git-shadow 的主要对象是“远端可工作的开发工作区”：

~~~text
Local Workspace
  ├─ Git Lane
  ├─ Shadow Lane
  ├─ optional WIP
  └─ Runtime intent
         ↓
Remote Workspace
         ↓
Agent / CloudCLI
         ↓
Git commit/push + Shadow return path
~~~

因此两者在 SSH、远端主机、桌面控制面、能力探测上存在同构，但最终状态不同：

~~~text
OpenShip   : Source → Running Application
git-shadow : Local Development State → Remote AI Development State
~~~

### 产品边界不动点

~~~text
git-shadow 到“远端开发工作区 + Agent 生命周期”结束。
应用 Build / Release / Production Deployment 不属于 git-shadow 的核心职责。
~~~

这条边界用于防止未来因为参考 OpenShip、Coolify、Railway、Render 等项目而把 git-shadow 逐步扩张成另一个 PaaS。

---

## 2. 最值得吸收：Target 成为一等对象

当前很多调用仍以一个 SSH host alias 表达目标：

~~~bash
git shadow run aws-micro cloudcli
~~~

但从系统语义看，aws-micro 已经不只是 SSH host，而是一个执行目标。

建议逐步显式形成：

~~~text
Target
├─ identity
├─ transport
├─ platform
├─ capabilities
├─ runtime
├─ resources
├─ lifecycle
└─ access_paths
~~~

例如：

~~~text
TargetDescriptor
├─ id: aws-micro
├─ kind: self_hosted_ssh
├─ os: linux
├─ arch: x86_64
├─ transport: ssh
├─ workspace_root: ~/.git-shadow / ~/wkspace
├─ capabilities:
│   ├─ git
│   ├─ cloudcli
│   ├─ tailscale
│   └─ systemd-user
└─ access_paths:
    ├─ tailscale
    └─ public-url
~~~

未来 Managed VPS 也消费同一个 Target 契约：

~~~text
SelfHostedSshTarget ─┐
ManagedVpsTarget ────┼─→ TargetDescriptor → ExecutionPlan
LocalTarget ─────────┘
~~~

不变量：

- Target 描述“在哪里执行”，不拥有文件同步语义；
- Git / Shadow / WIP / Runtime 的 ownership law 不因为 Target 类型变化而变化；
- Self-hosted 与 Managed 模式必须消费同一上层 ExecutionPlan。

这与现有 [IDE / Managed VPS Roadmap](decisions/2026-09-15_IDE_EXTENSION_AND_MANAGED_VPS_ROADMAP.md) 一致，应当作为后续 Target 抽象的入口，而不是另造一套 Managed-only 模型。

---

## 3. 吸收 Capability / Dependency Planner 思路

OpenShip 当前把远端所需组件声明成 requirements，并维护直接依赖关系；安装顺序由 dependency planner 决定，而不是在业务流程里手写一串 if/else。

git-shadow 已经具备：

~~~text
remote status
remote plan
remote ensure
Capability Probe
Graceful Degradation
~~~

所以不需要重写，只需要继续向声明式收敛。

建议目标模型：

~~~text
CapabilitySpec
├─ id
├─ detect()
├─ dependencies[]
├─ plan()
├─ ensure()
├─ verify()
├─ privilege_requirement
└─ failure_policy
~~~

应吸收：

1. requirements 是数据，不是散落的流程分支；
2. dependency graph 决定安装顺序；
3. plan 与 ensure 分开；
4. ensure 必须幂等；
5. verify 检查实际状态，不以“安装命令返回 0”作为最终真相；
6. 缺少非核心能力允许 graceful degradation。

不应吸收：

不要把 OpenShip 的 Docker、Edge、TLS、数据库等 deployment requirements 原样移植进来。git-shadow 只声明远端开发工作区和 Agent 真正需要的能力。

---

## 4. 吸收 Durable Operation，而不是增加更多临时 CLI 流程

OpenShip 的一些新操作已经使用明确的 durable operation 模型：

~~~text
operation
├─ sequence
├─ status
├─ steps
├─ logs
├─ heartbeat
├─ request identity
├─ lease
└─ retry/recovery
~~~

git-shadow 现有 Edge Executor / Service Agent 已经具有：

- job_id；
- JSONL event sequence；
- terminal durable state；
- replay；
- retry；
- resume；
- Lease；
- interrupted；
- cancellation。

方向本身已经正确。

值得吸收的是：**把这套能力提升为统一 Operation 模型，而不是每增加一个功能就自己定义状态。**

建议统一状态至少为：

~~~text
pending
planning
running
ready
partial
failed
interrupted
cancelled
~~~

并区分：

~~~text
Operation.status       = 整体状态
Operation.steps[]      = 分步状态
Operation.events[]     = 可重放事件
ObservedState          = 当前真实世界
DesiredState           = 此次操作想达到的状态
~~~

未来这些动作都应复用同一 Operation substrate：

~~~text
workspace.prepare
shadow.sync
capability.ensure
agent.launch
network.ensure
managed-vps.create
managed-vps.destroy
~~~

不要为 Managed VPS 再创造第二套“云任务系统”。

---

## 5. 吸收 Desired State / Observed State / Reconcile

OpenShip 一个重要经验不是“部署”，而是持续区分：

~~~text
用户希望的状态
vs
系统保存的状态
vs
目标机器上的真实状态
~~~

git-shadow 同样需要这一层。

例如：

~~~text
Desired:
cloudcli = running
workspace = branch foo/bar
tailscale = available

Observed:
cloudcli = stopped
workspace = foo/bar
tailscale = unavailable
~~~

此时：

~~~text
Reconcile(Desired, Observed)
  → capability.ensure(cloudcli)
  → tailscale 不满足时按 policy 降级 public URL
~~~

建议未来所有远端动作尽量遵循：

~~~text
Probe → Diff → Plan → Apply → Verify
~~~

而不是：

~~~text
直接执行一堆命令 → 希望成功
~~~

这与现有 [Capability Probing and Graceful Degradation Law](decisions/2026-09-26_CAPABILITY_PROBING_AND_GRACEFUL_DEGRADATION_LAW.md) 应合并思考。

---

## 6. 吸收 SSH Transport Manager / Connection Pool

OpenShip Desktop 在长期管理远端主机时已经遇到一个与 git-shadow 高度相似的问题：

> 如果所有功能都不断新建 SSH，会产生连接开销、生命周期混乱和 tunnel ownership 问题。

OpenShip 已经有 pooled SSH connection、retain/release ownership、port forwarding / reverse tunnel manager 等实现。

git-shadow 当前已经同时存在：

~~~text
SSH edge job
service-agent
CloudCLI access
network probe
Shadow CAS
未来 managed target
~~~

随着这些能力增加，应该逐渐把 SSH 从“每个功能自己打开连接”提升为：

~~~text
TransportManager
  └─ TargetConnection
       ├─ exec()
       ├─ stream()
       ├─ protected transfer
       ├─ forward()
       ├─ reverse_forward()
       ├─ retain()
       ├─ release()
       └─ health()
~~~

关键约束：

- pool 属于 Transport 层，不属于 AgentAdapter；
- Operation 持有连接 lease，操作结束后释放；
- 一个 operation cancel 不得杀掉其他 operation 正在共享的连接；
- Tunnel 的“配置状态”与“当前 live socket”分离；
- 连接断开必须允许上层 operation 判断 resume / retry / fail，而不是偷偷重跑有副作用的动作。

这一项对未来 Desktop Launcher 的体验价值很高。

---

## 7. 吸收 Control Plane / Data Plane 分离

OpenShip Desktop 很值得参考的一点是：

~~~text
Desktop Control Plane
        │
       SSH
        ↓
Remote Server / Runtime
~~~

这与 git-shadow Roadmap 中的独立客户端方向高度一致。

git-shadow 可以进一步稳定为：

~~~text
Desktop / CLI / optional IDE / future Web
                 │
                 ↓
          git-shadow Core Client
                 │
        Typed Operation / Plan
                 │
           Target Adapter
                 │
      SSH / future managed API
                 ↓
        Edge / Service Executor
                 │
                 ↓
          Remote Workspace
                 │
                 ↓
             Agent
~~~

UI 不拥有核心逻辑。

~~~text
CLI
Desktop
IDE extension
Web console
      │
      ↓
Control Surface → Core Client → Operation → Target
~~~

禁止 Desktop 自己再实现一套 SSH、Shadow 或 Agent orchestration。

---

## 8. 吸收“配置状态”和“Live State”分离

OpenShip 的 tunnel 管理中，一个值得直接吸收的思想是：

~~~text
saved configuration != live resource
~~~

例如保存了 tunnel 配置，并不代表 socket 现在真的存在。

git-shadow 很多对象也应遵循同样原则：

~~~text
Binding != Workspace exists
Target config != Target reachable
Agent config != Agent running
AccessPath config != AccessPath usable
Capability declared != Capability healthy
Service registered != Service alive
~~~

因此 API / CLI 输出中建议逐渐显式区分：

~~~text
configured
observed
effective
~~~

例如：

~~~text
AccessPath:
  configured: tailscale
  observed: DERP-only
  effective: public-url
~~~

这与现有 [Adaptive Web Access Path Law](decisions/2026-09-27_ADAPTIVE_WEB_ACCESS_PATH_LAW.md) 一致。

---

## 9. 吸收 Health Snapshot，但不要做完整 Monitoring 产品

OpenShip 对多服务器需要处理 SSH reachable、CPU / memory / disk、runtime 状态、resource freshness，以及避免一次 UI 刷新同时 hammer 大量服务器。

git-shadow 在 Managed VPS 和长期 Agent 场景也迟早需要最小 Target Health：

~~~text
TargetHealth
├─ reachable
├─ checked_at
├─ os / arch
├─ disk_free
├─ memory_available
├─ workspace_count
├─ active_jobs
├─ service_agent
└─ capabilities summary
~~~

这里只用于：

~~~text
Can this target safely accept/run this workspace?
~~~

而不是建设 Grafana 替代品。

建议：

- 有 freshness window；
- in-flight dedupe；
- 有并发上限；
- health probe 失败不伪装成 0；
- stale 和 unavailable 是不同状态；
- CLI/Desktop 显示“上次观测时间”。

---

## 10. 可以借鉴但不应现在实现：Provider / Adapter Registry

OpenShip 的 provider / adapter 结构适合它管理不同基础设施。

git-shadow 未来也会存在：

~~~text
TargetAdapter
├─ self-hosted-ssh
├─ managed-vps
└─ local / future sandbox

AgentAdapter
├─ cloudcli
├─ codex
├─ claude-code
├─ opencode
└─ ...

AccessAdapter
├─ tailscale
├─ public-url
└─ future tunnel provider
~~~

这里应借鉴“统一接口 + provider-specific implementation”，但不要过早建立巨大的插件框架。

遵守 YAGNI：

~~~text
至少出现 2~3 个真实实现
        ↓
提取共同接口
        ↓
再建立 Registry
~~~

---

## 11. 明确不要从 OpenShip 吸收的东西

| OpenShip 能力 | git-shadow 决策 |
| --- | --- |
| Application build pipeline | 不做 |
| Production release model | 不做 |
| Docker/Compose 应用部署 | 不做 |
| Nginx/OpenResty/Edge routing | 不做 |
| Domain management | 不做；仅维护 Agent/Web Remote 的 Access Path |
| ACME / TLS 生命周期 | 不做；交给 Cloudflare/Tailscale/入口层 |
| Production rollback | 不做；代码历史交给 Git |
| Database provisioning | 不做 |
| Database backup/restore | 不做 |
| Redis/Postgres/MySQL 生命周期 | 不做 |
| App marketplace / one-click apps | 不做 |
| K3s / Kubernetes orchestration | 不做 |
| Production autoscaling | 不做 |
| 通用 APM / monitoring | 不做 |
| 通用 CI/CD | 不做 |
| 面向生产应用的 secrets platform | 不做 |

如果未来用户希望把 AI 写好的代码发布到生产：

~~~text
git-shadow
    ↓
Git commit / push
    ↓
Cloudflare / OpenShip / Coolify / GitHub Actions / 其他部署系统
~~~

这是组合关系，不是 git-shadow 内部继续扩张。

---

## 12. Cloudflare First 对 git-shadow 的进一步约束

对于项目作者自己的基础设施，应采用：

~~~text
Can Cloudflare capability express this workload?
         │
     ┌───┴───┐
    Yes      No
     │        │
Cloudflare   Linux Target
             VPS / Bare Metal / Local
~~~

所以 git-shadow 不应以“管理更多 VPS”为目标。

VPS / Managed VPS 的存在理由是：

~~~text
Persistent Linux Workspace
+
Long-lived Agent Process
+
Native Toolchain / Cache
+
Stable filesystem identity
~~~

即：

> VPS 是 Cloudflare 无法自然表达的 persistent development machine escape hatch，而不是所有应用的默认宿主。

这条原则可以防止 Managed VPS Roadmap 演化成通用云平台。

---

## 13. Object / Morphism 映射

| Object | 含义 | 主要 Morphism |
| --- | --- | --- |
| Workspace | 一个 repo/branch 或 snapshot 工作区 | prepare, sync, suspend, destroy |
| Target | 可承载 Workspace 的执行位置 | probe, ensure, attach |
| Capability | Target 上的一个可验证能力 | detect, plan, ensure, verify |
| Operation | 一个可恢复的有状态动作 | start, replay, resume, cancel |
| AgentAdapter | Agent 生命周期契约 | detect, prepare, launch, health |
| AccessPath | 浏览器/控制端到远端服务的路径 | probe, select, open |
| Transport | 控制面与 Target 的通信通道 | exec, stream, forward |
| ShadowState | 私有状态投影 | diff, push, pull, conflict |
| GitState | 代码事实 | clone, fetch, commit, push, ff-only pull |

Functor 可以概括为：

~~~text
User Intent
   ↓ F
Desired Workspace State
   ↓ Reconcile
Typed Operation Plan
   ↓ Target Adapter
Observed Remote State
~~~

---

## 14. 必须保持的系统不变量

### Invariant A — Ownership

~~~text
GitTracked != Shadow != Runtime != WIP
~~~

任何新的 Target / Desktop / Managed 功能都不得破坏这一点。

### Invariant B — Target Parity

~~~text
Self-hosted SSH 与 Managed VPS
在 Workspace / Agent / Shadow 语义上能力平权。
~~~

### Invariant C — Observation before mutation

能 probe 的事情先 probe；普通 run/push/pull 不偷偷执行高权限主机变更。

### Invariant D — Durable truth

长操作的真相来自 durable operation state，不来自某个 UI 页面是否还连接着 SSE/WebSocket。

### Invariant E — Git is code truth

git-shadow 不创造第二套代码版本控制。

### Invariant F — No PaaS creep

~~~text
Remote Development Control Plane != Application Deployment Platform
~~~

---

## 15. Fixed-point hypothesis

如果未来继续增加 Managed VPS、更多 Agent、更多网络入口、Desktop、IDE、Local/remote/cloud sandbox，系统仍应收敛到同一个不动点：

~~~text
WorkspaceIntent
     ↓
Probe Target
     ↓
Reconcile Capabilities
     ↓
Prepare Workspace
     ↓
Launch Agent
     ↓
Maintain Git + Shadow return paths
~~~

新增 Provider 不应该改变这个主循环，只改变下面的 Adapter。

如果新增一个功能迫使主循环变成：

~~~text
Build Application
→ Create Release
→ Configure Production Domain
→ Manage Database
→ Rollback Deployment
~~~

则它已经越过 git-shadow 的产品边界。

---

## 16. 实施优先级

### 当前 P0：不因为 OpenShip 调研改变优先级

继续完成现有个人闭环：

- 真实 CloudCLI + GitHub + VPS；
- Shadow 双向同步与冲突；
- Git ff-only 收割；
- watcher / service Lease 恢复；
- 安全和真实 VPS 验收。

**不要现在为了“架构漂亮”重构 Target/Adapter 全栈。**

### P1：在真实需求出现时逐步吸收

- [ ] 定义最小 TargetDescriptor，先包住现有 SSH host/binding；
- [ ] 将 capability requirements/dependencies 数据化；
- [ ] 将远端长任务状态统一到 Operation vocabulary；
- [ ] 审计 SSH 创建点，设计共享 TransportManager；
- [ ] 为 Target 增加轻量 HealthSnapshot 与 freshness；
- [ ] 在 AccessPath 输出中统一 configured / observed / effective；
- [ ] Desktop Launcher 只调用 Core Client，不复制实现。

### P2：Managed VPS / 多 Target 时再抽象

- [ ] TargetAdapterRegistry；
- [ ] SelfHostedSshTarget / ManagedVpsTarget 能力平权；
- [ ] Desired/Observed/Reconcile 成为统一 orchestration API；
- [ ] Transport pool + tunnel lifecycle；
- [ ] Desktop/Web 展示 durable operation 和 target health；
- [ ] 基于真实重复代码提取 Provider Registry。

---

## 17. 最终结论

从 OpenShip 应吸收的是：

~~~text
Target as Object
Capability Dependency Planning
Durable Operation
Desired / Observed / Reconcile
SSH Transport Pool
Control Plane / Data Plane Separation
Configured / Live State Separation
Minimal Target Health
Adapter Boundary
~~~

不应吸收的是：

~~~text
Build
Release
Production Deploy
Routing
TLS
Database
Backup
Autoscaling
Kubernetes
General Monitoring
CI/CD
~~~

因此两者最稳定的组合关系是：

~~~text
Local Developer
      ↓
git-shadow
      ↓
Remote AI Development Workspace
      ↓
Git Commit / Push
      ↓
Cloudflare / OpenShip / other deployment system
      ↓
Production
~~~

**OpenShip 可以作为 git-shadow 的“控制面架构参考”，但不能成为 git-shadow 的“产品功能清单”。**
