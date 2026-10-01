# Railway ssh railway.new 可吸收设计：零摩擦 Onboarding 与 Ephemeral Workspace

> 目的：记录 Railway 2026 年 ssh railway.new / Free VM / Sandboxes 模式中，对 git-shadow 有价值的产品与架构思想。
>
> 核心结论：值得吸收的不是“免费 VM”，而是把身份、计算、预览、回收和账号化压缩成一个极短的状态机：
>
> ~~~text
> SSH Key
>    ↓
> Ephemeral Workspace
>    ↓
> Immediately Usable
>    ↓
> Preview / Agent
>    ↓
> Claim or Expire
>    ↓
> Owned Persistent Workspace
> ~~~
>
> 参考：
> - https://railway.com/free-vm
> - https://railway.com/changelog/2026-09-25-free-vms-without-an-account
> - https://railway.com/changelog/2026-06-26-railway-over-ssh
> - https://railway.com/sandboxes
>
> 本文记录可迁移的设计原则，不意味着 git-shadow 要复制 Railway 的基础设施实现。

---

## 1. Railway 当前公开确认的行为

截至 2026-09，Railway 官方公开说明的 ssh railway.new 行为包括：

1. 无需预先创建 Railway 账号；
2. 用户直接运行 ssh railway.new；
3. SSH key 作为匿名体验的身份凭据；
4. 官方明确说明：使用同一 SSH key 再次连接，会返回同一个 box；
5. 免费 VM 当前提供 2 vCPU / 2 GB RAM；
6. VM 预装 Railway Agent、Claude Code、Codex、OpenCode 等开发 Agent 及常见工具；
7. 首次连接会返回 preview URL、剩余时间、claim link 等信息；
8. 匿名 VM 有 60 分钟 build window；
9. build window 结束后还有 24 小时 claim window；
10. 未 Claim 的 VM 及文件最终删除；
11. Claim 后 VM、文件以及原有 URL 被保留并移动到用户账号；
12. 未 Claim 前 preview URL 只允许创建该 VM 时的来源 IP 访问；
13. Agent 场景可以获得结构化 manifest，而不必解析纯人类终端文案。

这是一条极短的 onboarding path：

~~~text
Terminal
  ↓
SSH
  ↓
Identity
  ↓
Compute
  ↓
Agent
  ↓
Preview
~~~

注册、Dashboard、Project、Payment 等传统对象全部被延迟到了用户已经获得价值之后。

---

## 2. 明确不要写死的推断

下面这些现象可以合理推测 Railway 内部存在对应实现，但目前不应作为 git-shadow 的事实依据：

- Railway 是否使用 Firecracker / MicroVM；
- 是否直接以 SSH public-key fingerprint 作为数据库主键；
- 网关内部具体是 fingerprint → VM，还是 key → identity → lease → workspace；
- VM 创建是否保证某个固定秒数；
- preview IP ACL 具体落在哪一层 Edge 实现；
- claim 是否内部真正“移动 VM”，还是通过其他存储/控制面绑定实现。

因此本文使用的稳定抽象是：

~~~text
DeviceCredential
      ↓
TemporaryIdentity
      ↓
WorkspaceLease
      ↓
EphemeralWorkspace
~~~

而不是：

~~~text
SSH fingerprint == VM primary key
~~~

我们学习可验证的契约，不绑定竞争对手未公开的底层实现。

---

## 3. 最重要的产品思想：先交付价值，再要求账号

传统云开发流程通常是：

~~~text
Landing Page
    ↓
Sign Up
    ↓
Verify Email
    ↓
Create Workspace
    ↓
Create Project
    ↓
Add Payment / Trial
    ↓
Choose Region
    ↓
Provision
    ↓
Install Agent
    ↓
Finally Work
~~~

Railway 把它重新排序成：

~~~text
ssh railway.new
    ↓
Work
    ↓
See Result
    ↓
Decide Whether It Is Worth Keeping
    ↓
Claim
~~~

本质变化：

~~~text
Traditional:
Identity → Ownership → Resource → Value

Railway:
Temporary Identity → Resource → Value → Ownership
~~~

这对 git-shadow 的 Managed 模式非常值得吸收。

---

## 4. Device Identity 与 Account Identity 分离

第一次使用不一定需要 Account Identity，只需要足够安全、足够稳定的 Device Identity。

git-shadow 可以区分三个对象：

~~~text
DeviceIdentity
├─ SSH public key
├─ installation key
└─ future passkey/device credential

AccountIdentity
├─ user account
├─ billing
├─ organization
└─ long-term ownership

WorkspaceIdentity
├─ repo / branch
├─ snapshot workspace
└─ managed workspace id
~~~

建议关系：

~~~text
DeviceIdentity
      │
      ├─ before claim ─→ TemporaryPrincipal
      │
      └─ after claim  ─→ AccountPrincipal
                              │
                              ↓
                        WorkspaceOwnership
~~~

不变量：

- SSH key 是凭据，不等于永久账号；
- 一个设备可以有多个 workspace；
- 一个账号可以登记多个设备 key；
- Workspace 的 durable identity 不应直接依赖某个 key 永久存在；
- 更换 SSH key 不应意味着丢失 workspace。

---

## 5. Ephemeral Workspace 作为一等生命周期

未来 Managed 模式可以增加明确的短生命周期对象：

~~~text
EphemeralWorkspace
├─ identity
├─ device_principal
├─ created_at
├─ build_deadline
├─ claim_deadline
├─ lease
├─ resource_quota
├─ preview_access
├─ agent_session
└─ promotion_state
~~~

建议状态机：

~~~text
CREATING
   ↓
READY
   ↓
ACTIVE
   ├──────────────→ CLAIMED
   │                   ↓
   │                OWNED
   │
   └→ BUILD_EXPIRED
          ↓
      CLAIMABLE
          ↓
       EXPIRED
          ↓
       DESTROYED
~~~

这应复用 git-shadow 已经存在的 Lease / interrupted / durable operation 机制，而不是重新建立第二套生命周期系统。

---

## 6. Claim 是 Promotion，不是重新部署

Railway 做得很好的产品语义是：

> 用户体验满意以后认领原来的东西，而不是注册以后重新创建一个新的东西。

对 git-shadow 来说，未来应尽量保持：

~~~text
Temporary Workspace
       │
       │ claim
       ▼
Owned Workspace
~~~

而不是：

~~~text
Temporary Workspace
       ↓ export
Account Creation
       ↓ import
New Workspace
~~~

Claim / Promote 操作应尽可能保持以下状态连续：

~~~text
WorkspaceIdentity
Filesystem State
Git State
Shadow State
Preview / Access identity（若安全允许）
~~~

变化的是：

~~~text
owner
billing scope
retention policy
resource limits
access policy
~~~

即：

~~~text
Claim ≈ Ownership Morphism
~~~

而不是：

~~~text
Claim ≈ Data Migration
~~~

---

## 7. 同一设备恢复到同一临时 Workspace

Railway 官方明确把体验定义成：

~~~text
same SSH key
      ↓
same box
~~~

git-shadow 可以吸收这一 UX 契约，但内部不要把 key 与 workspace 硬编码成 1:1。

更好的模型：

~~~text
TemporaryPrincipal
      ↓
WorkspaceLeaseResolver
      ↓
Most Recent Active Ephemeral Workspace
~~~

可能的策略：

~~~text
(device_key, product_entrypoint)
        ↓
0 active workspace → create
1 active workspace → resume
N active workspaces → list/select
~~~

因此用户无需记住 workspace id，也非常适合 Agent 自动恢复自己的计算环境。

---

## 8. Human / Agent 双协议输出

Railway 很值得学习的一点是：

~~~text
同一个入口
├─ 人类 → 可读 Terminal UX
└─ Agent → structured manifest
~~~

git-shadow 未来所有关键入口都应尽量遵循：

~~~text
One Semantic Operation
        │
   ┌────┴────┐
   ↓         ↓
Human       Machine
TTY         JSON/JSONL
~~~

例如 Ephemeral Workspace 创建结果可以包含：

~~~json
{
  "workspace_id": "...",
  "status": "ready",
  "ssh": "...",
  "preview_url": "...",
  "claim_url": "...",
  "expires_at": "...",
  "agent": {
    "default": "codex"
  }
}
~~~

机器协议不能靠解析人类文案。

这一原则与 git-shadow 现有 JSONL Edge Executor 一致，应继续扩大到 onboarding 层。

---

## 9. Prebaked Agent Runtime

Railway Sandboxes 直接预装多个 Coding Agent 和常见 runtime。

对短生命周期环境，这是合理的，因为动态安装的时间可能接近整个临时 workspace 的有效工作时间。

因此 Managed git-shadow 可以区分：

~~~text
Long-lived Self-hosted VPS
    → capability.ensure / native install

Short-lived Managed Workspace
    → prebaked base image / template
~~~

未来 Managed Image 可考虑预装：

~~~text
git
ssh
python/node/basic toolchain
git-shadow edge/service runtime
Codex
Claude Code
OpenCode
CloudCLI（如果仍需要）
diagnostic tools
~~~

但项目自己的：

~~~text
node_modules
.venv
build cache
project dependencies
~~~

仍然由 Workspace 原生生成，不打进通用镜像。

---

## 10. Preview First，但不要照抄 IP-only

Railway 对匿名环境采用：

~~~text
Unclaimed Preview
      ↓
creator IP only
      ↓
Claim
      ↓
shareable
~~~

值得吸收的是原则：

> 未拥有、未认证、免费计算资源的公网暴露面应该最小。

git-shadow 不需要机械复制 IP-only。用户可能经过 CGNAT、公司 NAT、VPN、手机热点或 IPv4/IPv6 漂移，而且我们已经有 Cloudflare/Tailscale 能力。

更适合我们的候选模型：

~~~text
Ephemeral Preview
├─ Tailscale identity
├─ Cloudflare Access
├─ short-lived signed access token
└─ temporary single-device binding
~~~

因此应吸收：

~~~text
Preview is private by default
~~~

而不是写死：

~~~text
Preview auth == source IP
~~~

---

## 11. Timeout 是产品能力，不只是垃圾回收

Railway 把 60 min build window、24 h claim window 和自动删除直接展示给用户。

git-shadow Managed Workspace 也应明确展示：

~~~text
runtime_deadline
idle_deadline
claim_deadline
retention_deadline
cost_limit
~~~

用户和 Agent 都能读。

这与现有 Lease 思想可以统一成：

~~~text
WorkspaceLifetimePolicy
├─ active_lease
├─ idle_timeout
├─ hard_timeout
├─ claim_grace
├─ retention
└─ cost_ceiling
~~~

---

## 12. Cost Guard 必须进入生命周期模型

一旦做到“一条命令就创建机器”，资源滥用和成本风险会立刻成为核心问题。

建议：

~~~text
EphemeralWorkspacePolicy
├─ max_cpu
├─ max_memory
├─ max_disk
├─ max_runtime
├─ max_idle
├─ max_network
├─ max_agent_budget
├─ concurrent_workspace_limit
└─ abuse_policy
~~~

git-shadow Roadmap 已有资源配额、闲置回收、成本上限、Lease。Railway 的启发是：这些不是 P2 的附属运维功能，而是零摩擦创建能够成立的前提条件。

---

## 13. Checkpoint / Fork / Template 作为未来观察项

Railway 正式 Sandboxes 已支持 checkpoint、fork、template、destroy。

这对 Agent 工作流非常有价值：

~~~text
Base Template
    ↓
Workspace
    ↓ checkpoint
Stable Prepared State
    ├─ fork → Agent A
    ├─ fork → Agent B
    └─ fork → Agent C
~~~

例如：

~~~text
同一个 bug
   ↓
Fork × 3
   ├─ Codex
   ├─ Claude Code
   └─ OpenCode
   ↓
比较结果
   ↓
保留 winner
~~~

但目前不进入 P0/P1 实现范围。它依赖底层 provider 的快照能力，也不能为了 Managed provider 破坏 Self-hosted SSH parity。

应归入 P3 / provider-specific optimization。

---

## 14. 对当前 Managed VPS Roadmap 的修正

现有 Managed VPS MVP 偏传统 SaaS：

~~~text
下载程序
→ 注册/登录
→ 选择 Agent 与规格
→ 选择本地文件夹
→ 创建隔离工作区
→ 投影代码
→ 打开 Web Remote
~~~

Railway 的启发是：“注册/登录”不一定必须排在前面。

未来可以考虑：

~~~text
选择本地文件夹
      ↓
Start
      ↓
Device Credential
      ↓
Ephemeral Managed Workspace
      ↓
Agent immediately available
      ↓
用户获得真实价值
      ↓
Claim / Sign in
      ↓
绑定账号 + 延长生命周期
~~~

在 CLI / Agent 场景甚至可以是：

~~~text
one command
    ↓
temporary workspace
    ↓
structured manifest
    ↓
work
~~~

---

## 15. 与 Self-hosted 模式的关系

这套设计只增强 Managed 模式，不应破坏 Self-hosted：

~~~text
Self-hosted
User already owns Target
    ↓
Device/Account Claim 不需要
    ↓
git-shadow 直接建立 Workspace

Managed
User does not own Target yet
    ↓
Temporary Principal
    ↓
Ephemeral Workspace
    ↓
Claim / Promote
~~~

Workspace semantics、Git/Shadow ownership、Agent Adapter、Operation model 保持相同。

差异仅存在于：

~~~text
Target provisioning
Identity bootstrap
Billing
Retention
Resource policy
~~~

---

## 16. Object / Morphism 表

| Object | 含义 | Morphism |
| --- | --- | --- |
| DeviceIdentity | 本机/Agent 可证明持有的设备凭据 | register, rotate, revoke |
| TemporaryPrincipal | 未注册账号前的临时主体 | authenticate, resume, claim |
| EphemeralWorkspace | 有期限的远端开发环境 | create, resume, expire, destroy |
| WorkspaceLease | 临时主体与工作区的时间绑定 | renew, expire |
| PreviewAccess | 未 Claim 前的最小暴露入口 | grant, verify, revoke |
| Claim | 临时所有权向正式所有权的提升 | promote |
| AccountPrincipal | 正式账号身份 | own, bill, share |
| ManagedTarget | 平台提供的计算目标 | allocate, release |
| AgentRuntime | 预装或可启动的 AI Agent | launch, attach |
| Manifest | 给 Agent 的结构化结果 | emit, consume |

最关键 Morphism：

~~~text
Claim:
(TemporaryPrincipal, EphemeralWorkspace)
        →
(AccountPrincipal, OwnedWorkspace)
~~~

理想情况下：

~~~text
workspace_before ≈ workspace_after
~~~

即用户感知上“还是刚才那台机器”。

---

## 17. Onboarding Functor

可以把 Railway 的启发抽象成：

~~~text
Raw Device
   │
   │ F
   ▼
Temporary Development Identity
   │
   ▼
Usable Compute
   │
   ▼
Visible Value
   │
   ▼
Durable Ownership
~~~

git-shadow Managed 产品未来应优先优化：

~~~text
TimeToFirstUsefulAgentAction
~~~

而不是：

~~~text
TimeToAccountCreation
~~~

---

## 18. 新的不变量

### Invariant A — Value before bureaucracy

Managed onboarding 允许时，应尽量先让用户获得一个可工作的 Agent Workspace，再要求长期账号化。

### Invariant B — Temporary does not mean unsafe

匿名/临时资源必须拥有更严格的 quota、lease、network policy、preview policy、cost ceiling 和 cleanup。

### Invariant C — Claim preserves continuity

Claim 不应该迫使用户重新 clone、重新 build、重新启动 Agent 才能继续。

### Invariant D — Credential != Ownership

SSH key / device key 只是 credential；长期 Workspace ownership 属于账户/组织模型。

### Invariant E — Human and Agent share semantics

入口和显示可以不同，但底层 Operation / Workspace contract 必须相同。

### Invariant F — Zero-install is a product feature

减少 CLI install、Python install、GitHub setup、MCP setup、Dashboard setup，本身就是核心产品价值。

---

## 19. 建议进入 Roadmap 的条目

### P1/P2 产品设计

- [ ] 定义 DeviceIdentity / TemporaryPrincipal / AccountPrincipal 三层身份；
- [ ] 定义 EphemeralWorkspace 生命周期和 Claim/Promote 语义；
- [ ] Managed onboarding 允许“先 workspace、后 account”；
- [ ] onboarding 同时提供 Human UX 与 structured manifest；
- [ ] 将 idle timeout / hard timeout / claim grace / cost ceiling 收敛为 WorkspaceLifetimePolicy；
- [ ] 设计 private-by-default 的 ephemeral PreviewAccess，优先复用 Tailscale / Cloudflare 身份能力；
- [ ] 为 Managed Workspace 设计 prebaked Agent image/template；
- [ ] same-device reconnect 默认恢复活动中的临时 workspace；
- [ ] Claim 后尽量保持 workspace/files/Git state/access identity 连续。

### P3 观察项

- [ ] checkpoint；
- [ ] fork；
- [ ] reusable workspace templates；
- [ ] 多 Agent 从同一 checkpoint 并行探索；
- [ ] provider-native fast clone。

这些都不应阻塞当前个人闭环。

---

## 20. 与 OpenShip 调研合并后的总体图景

OpenShip 给我们的主要启发：

~~~text
Control Plane
Target
Capability
Adapter
Durable Operation
Observed / Desired State
~~~

Railway 给我们的主要启发：

~~~text
Zero-friction Entry
Device Credential
Ephemeral Workspace
Claim / Promotion
Prebaked Agent Runtime
Visible Lease
Private Preview
Human/Agent Dual UX
~~~

合并以后，git-shadow 未来 Managed 形态可以变成：

~~~text
                 Local / Agent
                      │
               Device Identity
                      │
                      ▼
             git-shadow Control Plane
                      │
          ┌───────────┴───────────┐
          │                       │
   Existing Target          No Existing Target
          │                       │
 Self-hosted Workspace      Ephemeral Managed Target
          │                       │
          └───────────┬───────────┘
                      ▼
               Prepare Workspace
                      │
               Launch Agent
                      │
                Git + Shadow
                      │
               Visible Result
                      │
             optional Claim
                      │
              Durable Ownership
~~~

---

## 21. Fixed-point hypothesis

即使以后入口从 Desktop、CLI、IDE、SSH、Agent API、Web 不断增加，核心状态机仍应尽量保持：

~~~text
Identify enough
    ↓
Allocate/resolve Target
    ↓
Prepare Workspace
    ↓
Launch Agent
    ↓
Return Git/Shadow state
    ↓
Renew / Claim / Destroy
~~~

Railway 最值得学习的地方是：

> 不要让账号系统成为用户获得计算能力之前必须穿过的墙。

对于 git-shadow，更具体地说：

> 首次体验的目标不是“创建一个 git-shadow 用户”，而是“尽快让一个 Agent 在一个可恢复的远端 Workspace 里开始工作”。
