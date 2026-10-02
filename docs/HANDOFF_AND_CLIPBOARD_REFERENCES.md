# Clipboard / Artifact Handoff 可吸收设计：wx-ime-sdk 与 UniClipboard

> 目的：记录 wx-ime-sdk 与 UniClipboard 对 git-shadow 的可迁移设计，以及未来“人 ↔ 设备 ↔ Agent Computer”低摩擦交接的可能方向。
>
> 核心结论：不要把剪贴板/文件传输塞进现有 Shadow Sync；应把它定义成独立的 Handoff / Artifact Transfer 能力。
>
> 核心不变量：
>
> ~~~text
> Synchronization != Handoff
> ~~~
>
> 参考：
> - https://github.com/mkdir700/wx-ime-sdk
> - https://github.com/UniClipboard/UniClipboard
> - https://github.com/UniClipboard/Engine
>
> 本文是 future reference，不意味着当前 P0 个人闭环需要实现这些能力，也不意味着 git-shadow 核心依赖微信输入法、UniClipboard 或其网络。

---

## 1. 这不是 Shadow Sync

git-shadow 当前的 Git / Shadow / Native Runtime / WIP 四条 lane 解决的是工作区状态来源、投影和一致性。

剪贴板、截图、PDF、临时日志、手机照片等解决的是另一类问题：

~~~text
“把这个东西交给那台设备 / 那个 Agent”
~~~

这类对象通常是一次性交付，有明确发送方和接收方，有 receipt / timeout / TTL，但不需要目录 merge，也不应该自动成为工作区事实源。

因此未来更合适的对象是：

~~~text
Handoff Lane / Artifact Transfer
~~~

而不是继续扩大 Shadow Lane。

Shadow Sync 的状态机更接近：

~~~text
local state → compare → remote state → conflict/reconcile
~~~

Handoff 的状态机更接近：

~~~text
artifact → offer → transfer → receive → receipt
~~~

所以必须保持：

~~~text
Synchronization = maintain state relationship
Handoff         = transfer an artifact/intention
~~~

---

## 2. wx-ime-sdk 值得吸收什么

wx-ime-sdk 是微信输入法跨设备剪贴板协议的独立 Rust 客户端，可以作为新的设备身份与官方输入法配对，收发文本、图片和文件。

最值得吸收的不是 WeType 本身，而是它对控制面和数据面的拆分：

~~~text
Device Identity
      ↓
Pairing / Group
      ↓
Control Service / Rendezvous
      ↓
Peer address + certificate exchange
      ↓
Authenticated direct transfer
      ↓
ACK / Receipt
~~~

这里可以提炼出：

~~~text
Identity
!= Discovery
!= Rendezvous
!= Data Plane
~~~

这与 git-shadow 已经形成的 Workspace / Control Plane / Access Path 分层是一致的。

它还把一次文件传输建模为拥有明确生命周期的 operation：AwaitingPeer → Connecting → Sending → Receipt，并具有 cancel、timeout、peer disconnected 等终态。

未来可借鉴：

~~~text
HandoffOperation
├─ operation_id
├─ source
├─ destination
├─ artifact
├─ progress
├─ cancel
├─ timeout
├─ receipt
└─ terminal_state
~~~

但 wx-ime-sdk 是非官方协议兼容实现，官方协议、服务策略和风控变化都可能导致失效；其普通文件传输当前也主要依赖局域网直连。因此正确位置应是 optional WeTypeHandoffAdapter，而不是 git-shadow transport core。

---

## 3. UniClipboard 更接近长期参考

UniClipboard 已经形成了更完整的独立设备网络：设备身份、加密 Space、短期邀请码、可信设备、文本/图片/文件、P2P direct、NAT traversal、encrypted relay fallback、离线恢复、headless CLI/daemon、移动端等。

其中最值得吸收的是：payload encryption 与 transport 解耦。

~~~text
Artifact
   ↓ encrypt
Ciphertext
   ↓
Transport Adapter
   ├─ LAN direct
   ├─ Internet P2P
   ├─ Relay
   ├─ Tailscale
   └─ SSH
~~~

因此：

~~~text
Transport changes != Security semantics change
~~~

这对 git-shadow 的未来 Handoff Plane 很重要。

---

## 4. 优先研究 UniClipboard/Engine，而不是 GUI

UniClipboard 桌面项目包含 GUI、daemon 和 CLI，但更值得参考的是独立的 UniClipboard Engine：Host 只提供目录、安全存储、剪贴板、文件句柄和生命周期；Engine 拥有 identity、space、pairing、encryption、P2P、persistence、transfer。

这与 Zeron reference 中已经提出的 Host / Viewport → Stable Boundary → Engine / Remote Core 非常接近。

许可证边界也值得记录：

~~~text
UniClipboard desktop app : AGPL-3.0
UniClipboard Engine      : Apache-2.0
wx-ime-sdk               : Apache-2.0
~~~

未来若真的考虑代码级复用，应优先研究 Apache-2.0 的 Engine 边界，而不是整体搬入 GUI 项目。

---

## 5. 建议增加第五条逻辑 Lane：Handoff Lane

未来完整模型可以扩展为：

~~~text
Git Lane      = durable committed code/history
Shadow Lane   = explicitly authorized private workspace state
Runtime Lane  = remote-native dependencies/cache/runtime
WIP Lane      = explicit uncommitted tracked patch
Handoff Lane  = ephemeral text/image/file/artifact delivery
~~~

新的总不变量：

~~~text
GitTracked != Shadow != Runtime != WIP != Handoff
~~~

Handoff 不参与 repo ownership，也不默认参与 Shadow conflict resolution。

---

## 6. HandoffArtifact 建议模型

~~~text
HandoffArtifact
├─ id
├─ kind: text | image | file
├─ content_hash
├─ size
├─ source_device
├─ destination: device | workspace | agent
├─ created_at
├─ ttl
├─ sensitivity
├─ transport_policy
└─ receipt
~~~

状态机可考虑：

~~~text
CREATED → ROUTING → OFFERED → TRANSFERRING → RECEIVED → ACKNOWLEDGED

EXPIRED / CANCELLED / FAILED / REJECTED / UNKNOWN
~~~

必须明确区分：

~~~text
accepted by relay
!= received by target
!= consumed by Agent
~~~

---

## 7. HandoffAdapter，而不是立即重造 P2P 网络

近期更合理的是先定义适配器：

~~~text
HandoffAdapter
├─ UniClipboardAdapter
├─ WeTypeAdapter
├─ SshTransferAdapter
└─ future NativeP2PAdapter
~~~

统一能力可考虑：

~~~text
detect()
prepare()
pair()
peers()
send_text()
send_file()
receive()
cancel()
progress()
receipt()
capabilities()
~~~

UniClipboardAdapter 更适合跨公网、PC/VPS/手机、大文件、长期可信设备网络和 relay fallback。

WeTypeAdapter 更适合用户已经安装微信输入法时的文本、图片和轻量文件入口，优势是 onboarding 摩擦极低，但只能作为 optional integration。

SSH Adapter 可作为最小 fallback：当没有 P2P 设备网络时，利用现有 SSH 把显式 artifact 交到目标 workspace inbox。

---

## 8. 与 Agent Computer 的关系

Zeron reference 已经把长期对象写成 Persistent Remote Computer，但“拥有一台远端电脑”还缺少一个现实问题：人手中的东西怎么交给这台电脑？

传统路径可能是：

~~~text
Phone → messaging/cloud drive → PC → download → find path → SSH → VPS → tell Agent
~~~

理想路径应该是：

~~~text
Phone / PC
   ↓ Share / Copy
Handoff Plane
   ↓
Agent Computer Inbox
   ↓
Agent
~~~

反向也一样：Agent 生成 artifact 后，可以通过 Handoff Plane 回到手机或 PC。

因此 Handoff Plane 是 Agent Computer 成为“真正个人设备”的一个必要组成。

---

## 9. Workspace Inbox 是交接落点，不是新事实源

未来可以有：

~~~text
Workspace
└─ Inbox
   ├─ screenshot.png
   ├─ report.pdf
   └─ note.txt
~~~

但语义必须保持：

~~~text
Inbox = delivery endpoint
~~~

而不是：

~~~text
Inbox = automatically synchronized source of truth
~~~

Handoff 到达后，Agent 或用户可以显式选择 consume only、copy into workspace、commit to Git、promote to Shadow、discard、expire。

这一步 explicit promote 很重要，它防止临时手机截图因为进入 Inbox 就自动改变 workspace ownership。

---

## 10. 安全边界

未来必须保持：

1. Handoff Artifact 不因为进入远端就自动成为 Shadow 文件；
2. clipboard history 不进入 git-shadow 日志；
3. 文件名、路径、文本内容按敏感负载处理；
4. paired device identity 不等于 workspace write authorization；
5. 自动写入 repo 必须显式发生；
6. third-party adapter failure 不能破坏 Git / Shadow 核心路径；
7. 如果使用 relay，优先保证 payload E2EE，而不是把 TLS endpoint trust 当作等价物。

长期关系应保持：

~~~text
Device Trust
!= Workspace Authorization
!= Shadow Authorization
!= Agent Permission
~~~

---

## 11. 分阶段吸收

Phase A：Reference only。当前只保留本文和 Roadmap 方向，不修改现有 P0，不引入第三方依赖。

Phase B：Adapter prototype。个人闭环稳定后，优先验证 UniClipboard CLI/daemon 与 wxc 作为外部 Handoff adapter，测试 latency、target selection、large file、offline、receipt、cancel、timeout 和 secret leakage。

Phase C：Native Handoff Plane。只有 adapter 模式证明该能力是 git-shadow 的高频核心路径，才考虑 Native P2P/E2EE Engine；此时 UniClipboard Engine 可作为主要 Rust 架构参考。

---

## 12. 与已有 references 的组合

~~~text
OpenShip
  → Target / Capability / Durable Operation

Railway
  → Identity / Workspace / Managed Compute

Zeron
  → Agent Control Plane / Persistent Remote Computer

wx-ime-sdk + UniClipboard
  → Device Trust / Handoff / P2P Artifact Transfer
~~~

组合后的长期系统：

~~~text
Human / Device
      │
      ├─ Viewport
      └─ Handoff Plane
             │
             ▼
       Agent Computer
             │
      ┌──────┼────────┐
      ▼      ▼        ▼
   Control  Agent   Workspace
    Plane   Runtime  Authority
                      │
              Git / Shadow / WIP / Runtime
~~~

Handoff Plane 与 Workspace Plane 相交，但不互相吞并。

---

## 13. Fixed Point

不管未来底层使用 UniClipboard、WeType、SSH、Tailscale、QUIC、relay、mobile share sheet 还是 desktop clipboard，对用户稳定的语义应该仍然是：

~~~text
I have an artifact
      ↓
choose / infer destination
      ↓
deliver securely
      ↓
target receives it
      ↓
optional Agent consumption
      ↓
receipt
~~~

> git-shadow 不只需要让 Agent 拥有一个长期可工作的远端 Computer，还应该最终让“人手里的东西”和“Agent 产出的东西”可以在设备之间低摩擦交接；但交接永远不能和 Workspace 同步混成同一个协议。
