# ADR — 本地控制端极简静默律与零触碰本地 Git 隔离律 (The Local Silence and Zero-Git-Touch Isolation Law)

- **Status**: Accepted
- **Date**: 2026-09-25
- **Scope**: `git_shadow.dev_server` + `git_shadow.cli` + `git_shadow.binding` + 跨端 SSH RPC 拓扑 + 本地文件监听边界
- **Canonical Owners**:
  - `ZeroLocalGitTouchAxiom` (零触碰本地 Git 公理 · 本地绝不在后台读写 `.git`)
  - `StrictShadowHashAxiom` (纯哈希变动触发公理 · 杜绝假积极 HMR)
  - `SilentPollAxiom` (例行探查完全静默公理 · 零变动零报幕)
  - `MandatoryContractPromptAxiom` (契约门禁强制公理 · 终结无感知黑盒)
  - `StatelessEdgeRPCTopology` (无状态边缘代理拓扑 · 厘清 I/O 责任边界)
  - `ZeroOverheadHeartbeat` (本地守护进程 60 秒静默心跳)
- **取代/修订关系**：
  - 彻底废除并替代 `2026-09-15_CONTINUOUS_AUTO_SYNC_AND_HEARTBEAT_LAW.md` 中的 §2.5 `SafeGitAutoPull`（本地自动合流条款）；
  - 修订 `2026-09-15_GITSHADOW_CONTRACT_LAW.md` 中的 §2.3（废除未配置时的静默内存兜底，改为启动强制交互式建档）。

---

## 1. Context (背景与故障现实)

2026-09-25，用户在项目中使用 `shadow`（前台随行 Dev Server）时遭遇了严重的异常行为与体验恐慌：
1. **本地未动却陷入疯狂推送循环**：在开发者没有任何编辑操作的情况下，控制台每隔十几秒便打印 `⚡ 检测到 本地工作区改动，正在热同步打入远端...`，并持续发起 20 秒的网络空跑推送；
2. **例行拉取疯狂刷屏**：推送被制止后，控制台又以 8 秒一次的频率疯狂滚屏报幕（`workspace.create` → `正在拉取远端影子文件` → `边缘同步任务执行完毕`），仿佛系统一直在狂拉海量文件；
3. **严重信任危机与失控感**：由于当前项目根目录压根没有 `.gitshadow` 声明文件，用户无法得知工具究竟在以什么规则传输哪些文件，因而产生了强烈的安全恐慌：*“它是不是在同步我的整个项目，甚至把我 Windows 的 `~` 家目录也同步上去了？！”*；同时，用户对远端 Mac 究竟有没有常驻后台脚本在跑、到底在执行什么产生了严重疑惑。

---

## 2. 根因深度剖析：之前怎么就搞错了？基于什么搞出来的？哪里没有说清楚？（Forensic Root Causes）

为什么之前在设计和实现中会犯下这一连串荒谬的错误？追根溯源，是在以下五个关键认知与设计层面发生了严重的走形和误判：

### 2.1 认知断层一：偷懒的“全自动收割”愿望，彻底踩碎了 Git 状态的所有权边界
* **当初是基于什么搞出来的？**
  翻开历史 ADR `2026-09-15_CONTINUOUS_AUTO_SYNC_AND_HEARTBEAT_LAW.md` 第 2.5 节，赫然写着所谓的“个人闭环 Git 收割边界 (`SafeGitAutoPull`)”：
  *“本地 `--watch` 通过 Git 原生命令获取远端分支，并只接受 fast-forward……”*
  在 `feat: add managed workspace sync flow` (commit `fdfefe3`) 中，实现者抱着一种**“为了让用户更爽、替用户省去手动 `git pull`”的幼稚便利主义愿望**，在本地客户端循环中植入了 `auto_fast_forward_pull`，导致本地控制端**每隔 10 秒在本地执行一次 `git fetch --prune origin main`**。
* **哪里没有说清楚？**
  1. **没有说清楚“Git 仓库本身也是底层文件系统”这一基本物理事实**！
     本地执行 `git fetch` 会直接写入 `.git/FETCH_HEAD` 和更新 `.git/objects`。在 Windows 操作系统中，只要工作区根目录下有任何文件写入（包括隐藏的 `.git` 子目录），Win32 的 `FindFirstChangeNotificationW` / `ReadDirectoryChangesW` 就会被强行唤醒！
  2. **没有说清楚“本地 Git 是开发者的神圣领地，任何外部同步工具绝无权在后台私自操纵”**！
     远端从 GitHub 拉代码是远端 VPS 的事；本地工作区何时 `fetch`、何时 `pull`、何时 `merge`，必须 100% 遵从人类开发者的显式意志。外围同步工具在本地后台偷跑 `git fetch`，无异于在系统内部自己踩下油门触发监听，直接引爆了“后台 fetch → 触发监听 → 误报改动 → 触发推送 → 下一轮定时 fetch”的自激推拉死循环！

### 2.2 认知断层二：偷懒的“假统一日志”与“伪兜底文案”（Goodhart 自欺欺人漏洞）
* **当初是基于什么搞出来的？**
  在写 `dev_server.py` 时，为了兼顾“未提交修改能快速推上去”，草率地写下了：
  ```python
  if current != previous or (watcher.native and notified):
  ```
  而在处理显示描述时，发现当 `changed_files` 为空列表时代码会报错或无法描述，于是顺手写出了伪兜底：
  ```python
  count_desc = f"{len(changed_files)} 个影子文件" if changed_files else "本地工作区改动"
  ```
* **哪里没有说清楚？**
  1. **没有说清楚“监听器通知只是唤醒提示，绝不能作为数据变动的判定依据”**！
     文件系统监听器是出了名的噪点来源（编辑器交换文件、系统 index、Git 内部变动都会触发 notified）。将 `(watcher.native and notified)` 作为一个与内容哈希并列的 `or` 条件，等同于向一切系统噪点敞开大门。
  2. **没有说清楚“当改动为空时必须立即短路返回，绝不允许强行编造文案伪装正常”**！
     这一行 `count_desc = ... if ... else "本地工作区改动"` 是典型的 **Goodhart 欺骗性代码**：它把一个根本没有文件变动的异常空跑分支，强行涂脂抹粉成看似正常的业务文案，堂而皇之地向用户谎报“检测到本地工作区改动”，造成极其严重的狼来了效应与恐慌。

### 2.3 认知断层三：盲目崇拜“零配置”，把涉密同步做成了无感黑盒
* **当初是基于什么搞出来的？**
  在 `2026-09-15_GITSHADOW_CONTRACT_LAW.md` 第 2.3 节写着：
  *“若项目中未显式配置 .gitshadow，系统优雅兼容默认轻量安全模板（.env*, *.secret, config.local.json, _dev_log/）……”*
  设计者把前端构建工具（如 Vite/Parcel）的“Zero-Config 零配置开箱即用”盲目迁移到了文件同步与涉密传输领域。
* **哪里没有说清楚？**
  **没有说清楚“在数据同步与凭据管理领域，知情权与审查权高于一切便利性”**！
  一个没有 `.gitshadow` 的项目，工具在内存中默默启用默认规则静默运转，用户肉眼看不到任何物理契约文件，心理上就是一片黑盒。一旦终端开始频繁滚屏，用户由于没有任何物理锚点可以印证，立刻就会合理怀疑：“它在传什么？我的私钥、我的全盘文件、我的家目录是不是正在被全量传走？”
  必须明确：**首次启动，必须停下脚步，当面询问，落盘契约，绝不暗箱操作！**

### 2.4 认知断层四：缺乏日志分层思维，把例行后台巡检做成了“战功报幕”
* **当初是基于什么搞出来的？**
  早期单次执行 `git shadow push` 或 `pull` 时，为了方便调试，开发者对接了远端 SSH 执行器返回的全部 JSONL 事件（`workspace.create`、`step.started`、`step.succeeded`、`job.completed`）。
  后来加入后台 8 秒例行 pull 探查时，直接偷懒复用了 `_on_edge_event` 打印函数。
* **哪里没有说清楚？**
  **没有说清楚“交互式命令的调试输出”与“常驻后台例行巡航的静默契约”是两套完全不同的世界**！
  - 前台手动命令需要详尽的进度感；
  - 后台轮询的最高哲学是 **Silence is Golden（没有新闻就是最好的新闻）**。
  每 8 秒滚屏 6 行，即使远端 0 改动，终端也被海量“执行完毕”所淹没。这在感知上把原本为了保障一致性的“轻量巡检”，变成了狂躁的“资源吞噬者”。

### 2.5 认知断层五：远端边缘代理（Edge Agent）的存在形式语焉不详
* **当初是基于什么搞出来的？**
  早期文档充斥着“范式 A / 范式 B / 租约自毁 / 内核事件心跳”等高大上词汇，却没有画出一张质朴的 I/O 拓扑图，导致用户甚至维护团队的脑海中出现幻觉：*“远端 Mac 是不是有一个常驻的守护脚本或 cron 任务在一直跑、一直在拉？”*
* **哪里没有说清楚？**
  **没有用人话讲透远端边缘代理的纯无状态管道本质**！
  远端 Mac 上压根没有常驻的、失控的后台轮询进程；远端代码只有一份位于 `~/.local/share/git-shadow/bin/git-shadow-edge-agent.py` 的脚本。它是本地通过 SSH 管道喂入 JSON Plan 启动的一次性子进程，执行完就退出，所有状态沉淀在 `runs/<job-id>` 留痕。这一质朴拓扑如果没讲透，排查故障时就会产生巨大的信息黑洞。

---

## 3. Decision (今天落地的六大不可动摇公理)

为彻底绝除上述五大隐患，今天在代码与架构中全面落地以下六大刚性公理：

```text
┌────────────────────────────────────────────────────────────────────────┐
  git-shadow 2026-09-25 架构重整公理矩阵
├───────────────────────────────┬────────────────────────────────────────┤
  1. 零触碰本地 Git (Zero Git Touch)  │ 本地彻底禁用 git fetch/pull，严禁读写本地 .git │
  2. 纯哈希变动触发 (Strict Hash HMR) │ 仅白名单文件 SHA-256 变动才触发热同步，杜绝假报警 │
  3. 契约门禁强制公理 (Mandatory Prompt)│ 首次运行必须交互式询问并落地 .gitshadow 契约    │
  4. 例行巡检完全静默 (Silent 30s Pull)│ 30秒无感知轮询，屏蔽所有过程日志，变动才报幕    │
  5. 零干扰本地心跳 (60s Heartbeat)   │ 本地后台每 60 秒刷新静默时间戳，供 status 查询   │
  6. 无状态边缘代理 (Stateless Edge)   │ 明确 Mac 远端为 SSH RPC 子进程，无常驻僵尸进程   │
└───────────────────────────────┴────────────────────────────────────────┘
```

### 3.1 零触碰本地 Git 公理 (`ZeroLocalGitTouchAxiom`)
- **彻底移除 `auto_fast_forward_pull` 与相关定时器**；
- 本地控制端（`shadow` / `gsw` / `dev_server`）**严禁在本地工作区执行任何后台 `git fetch`、`git pull`、`git merge` 或调用任何读写本地 `.git` 的命令**；
- 本地 `.git` 目录的演进与更新完全归属开发者手动意志；
- 远端代码基线的对齐由远端 VPS 边缘执行器直接面向 GitHub 骨干网独立完成，本地决不干预、决不越俎代庖。

### 3.2 纯哈希变动触发公理 (`StrictShadowHashAxiom`)
- 废除任何以“底层监听器是否通知”作为推送依据的伪逻辑（彻底删除 `or (watcher.native and notified)`）；
- **唯一触发条件**：只有在 `.gitshadow` 显式白名单内的私有文件（及 `.gitshadow` 本身）的实际 **SHA-256 内容哈希发生变化（`current != previous`）** 时，才允许发起 HMR 热同步；
- 若影子文件内容未变，任凭本地有任何环境噪声、缓存写入或底层唤醒，控制端**死死休眠，坚决不发网络包，坚决不打任何日志，坚决不伪造“本地工作区改动”**。

### 3.3 契约门禁强制公理 (`MandatoryContractPromptAxiom`)
- 在项目首次运行 `shadow` 或绑定目标主机时，若检测到工作区根目录不存在 `.gitshadow` 文件，**必须主动停下来，交互式询问用户并生成标准模板**：
  ```text
  [shadow] ⚠️ 检测到当前项目尚未声明 .gitshadow 影子契约文件！
  [shadow] 为防止误同步无关文件或隐私泄露，是否立即创建标准 .gitshadow 模板？[Y/n]:
  ```
- 彻底废除“静默内存兜底”，让用户在启动的第一秒就对即将被同步的文件（`.env*`、私钥、`config/local_config.py`、`.runtime/`）拥有 100% 的知情权与审查权。

### 3.4 例行探查完全静默公理 (`SilentPollAxiom`)
- 后台例行探查远端影子状态默认间隔从 8 秒收敛至 **30.0 秒**（平衡时效与 SSH 唤醒开销，杜绝在 VPS 堆积空任务目录）；
- **例行探查必须 100% 完全静默（Silent Pull）**：
  - 严禁打印 `workspace.create`、`step.started`、`step.succeeded` 等过程垃圾信息；
  - **有改动才报幕**：仅当远端切实产生了文件同步（`shadow.remote`）或检测到冲突（`shadow.conflict`）时输出一行轻量提示；零改动时保持零输出。

### 3.5 零干扰本地心跳公理 (`ZeroOverheadHeartbeat`)
- 本地后台守护循环每 60 秒刷新一次本地守护进程心跳文件（`LocalDaemonManager.touch_heartbeat`）；
- 零控制台输出，零网络包消耗，仅为状态查询命令（`shadow status`）提供本地进程的存活判定。

### 3.6 无状态边缘代理拓扑定性 (`StatelessEdgeRPCTopology`)
- **远端 Mac 的真实运行形态定性**：
  ```text
  [本地 Windows 控制端] ──(SSH Stdin: TypedExecutionPlan)──> [远端 Mac Edge Agent]
                                                                     │
                                                       (直连 GitHub 获取代码基线)
                                                       (原子 CAS 读写影子文件)
                                                                     ↓
  [本地 Windows 控制端] <──(SSH Stdout: JSONL Events)────── [运行审计: runs/<job-id>]
  ```
  - 脚本位于 `~/.local/share/git-shadow/bin/git-shadow-edge-agent.py`；
  - **无常驻常开的后台失控守护进程**，完全由本地 SSH 管道触发、随任务结束而退出；
  - 任务执行详情脱敏记录于 `~/.local/share/git-shadow/runs/<job-id>/events.ndjson`，随时可追溯审计。

---

## 4. 架构改造前后对比矩阵 (Before vs. After)

| 维度 | 修改前 (2026-09-15 隐患态) | 修改后 (2026-09-25 健全态) | 根治的痛点 |
| :--- | :--- | :--- | :--- |
| **本地 Git 介入** | 每 10 秒定时偷跑 `git fetch` | **本地 100% 零触碰 `.git`** | 根除 `.git/FETCH_HEAD` 触发监听引发的自激推拉死循环 |
| **HMR 触发机制** | 依赖监听器唤醒 `or notified`，改动为空时伪造“本地工作区改动” | **严格比对 `.gitshadow` 白名单 SHA-256 哈希**，无变动立即短路 | 根除空跑推送与狼来了假报警 |
| **契约文件门禁** | 无配置时静默使用内存默认白名单，用户完全无感 | **启动强检测，无契约则交互式提示建档**，代码公开可审计 | 根除用户对“全量搬运/家目录泄露”的恐惧与失控感 |
| **后台 Pull 行为** | 8 秒轮询，每一行 RPC 状态全量打入控制台 | **30 秒轮询，完全静默 (Silent Mode)**，仅当真实变更时打印一行 | 根除终端瀑布刷屏与控制台视觉污染 |
| **心跳管理** | 概念宏大但本地缺乏静默心跳落地 | **60 秒本地原子 touch 心跳**，轻量供 `status` 查询 | 厘清本地存活判定与远端租约边界 |
| **远端执行定性** | 概念模糊，用户怀疑 Mac 是否有常驻吸血守护 | **明确为无状态 SSH RPC 边缘执行器**，按需调用，留痕审计 | 消除远端进程疑虑，确立清晰 I/O 拓扑 |

---

## 5. Consequences & Non-Negotiable Guards (后果与不可逆门禁)

1. **绝对禁令**：任何人或任何 AI 智能体，严禁在 `git_shadow` 本地代码中重新引入任何后台自动执行 `git fetch`、`git pull` 或触碰本地 `.git` 的逻辑；
2. **测试门禁**：所有针对 `dev_server` 的单测必须断言：当白名单哈希无改动时，`sync_shadow_files` 与网络推送调用次数严格为 **0**；
3. **静默红线**：后台 Routine 任务的日志等级必须受 `silent=True` 门禁约束，除真实数据变动与严重异常外，严禁在终端输出任何心跳巡检信息。
