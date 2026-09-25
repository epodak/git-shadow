# ADR — 本地极简静默律、双向安全 Git 智能联动与脏工作区避让律 (The Local Silence, Safe Two-Way Git Alignment & Dirty Workspace Suspension Law)

- **Status**: Accepted
- **Date**: 2026-09-25
- **Scope**: `git_shadow.dev_server` + `git_shadow.git_sync` + `git_shadow.engine` + 本地文件监听边界 + 双向 Git 协同模型
- **Canonical Owners**:
  - `SafeGitAlignmentAxiom` (安全 Git 智能联动公理 · 干净快进合流，脏工作区避让延缓)
  - `StrictShadowHashAxiom` (纯哈希变动触发公理 · 彻底隔离 Git 写操作与 HMR 触发)
  - `SilentPollAxiom` (例行探查完全静默公理 · 零变动零报幕)
  - `MandatoryContractPromptAxiom` (契约门禁强制公理 · 终结无感知黑盒)
  - `StatelessEdgeRPCTopology` (无状态边缘代理拓扑 · 厘清 I/O 责任边界)
  - `ZeroOverheadHeartbeat` (本地守护进程 60 秒静默心跳)
- **取代/演进关系**：
  - 彻底废除并演进 `2026-09-15_CONTINUOUS_AUTO_SYNC_AND_HEARTBEAT_LAW.md` 中的 §2.5 `SafeGitAutoPull`；
  - 修订 `2026-09-15_GITSHADOW_CONTRACT_LAW.md` 中的 §2.3（废除未配置时的静默内存兜底，改为启动强制交互式建档）；
  - 修正早先版本中的“零触碰本地 Git（ZeroLocalGitTouchAxiom）”过矫正条目，以本篇的“安全 Git 智能联动与脏工作区避让”作为最终单一真源。

---

## 1. Context (背景与故障现实)

2026-09-25，用户在项目中使用 `shadow`（前台随行 Dev Server）时遭遇了严重的异常行为与体验恐慌：
1. **本地未动却陷入疯狂推送循环**：在开发者没有任何编辑操作的情况下，控制台每隔十几秒便打印 `⚡ 检测到 本地工作区改动，正在热同步打入远端...`，并持续发起 20 秒的网络空跑推送；
2. **例行拉取疯狂刷屏**：推送被制止后，控制台又以 8 秒一次的频率疯狂滚屏报幕（`workspace.create` → `正在拉取远端影子文件` → `边缘同步任务执行完毕`），仿佛系统一直在狂拉海量文件；
3. **严重信任危机与失控感**：由于当前项目根目录压根没有 `.gitshadow` 声明文件，用户无法得知工具究竟在以什么规则传输哪些文件，因而产生了强烈的安全恐慌：*“它是不是在同步我的整个项目，甚至把我 Windows 的 `~` 家目录也同步上去了？！”*；同时，用户对远端 Mac 究竟有没有常驻后台脚本在跑、到底在执行什么产生了严重疑惑；
4. **两端 Git 提交脱节（过矫正后遗症）**：在初期排查死循环后，开发者简单粗暴地将本地后台所有 `git fetch/pull` 逻辑切除，宣称“本地永远不碰 Git”，导致随后用户发现：**远端 AI 提交了代码本地不拉取也不提示，本地提交了远端也不拉取**，破坏了实际的日常多端协同流。

---

## 2. 根因深度剖析：之前怎么就搞错了？基于什么搞出来的？哪里没有说清楚？（Forensic Root Causes）

为什么之前在设计和实现中会犯下这一连串荒谬的错误？追根溯源，是在以下六个关键认知与设计层面发生了严重的走形和误判：

### 2.1 认知断层一：偷懒的“全自动收割”愿望，踩中了操作系统的文件监听物理死穴
* **当初是基于什么搞出来的？**
  翻开历史 ADR `2026-09-15_CONTINUOUS_AUTO_SYNC_AND_HEARTBEAT_LAW.md` 第 2.5 节，写着所谓的“个人闭环 Git 收割边界 (`SafeGitAutoPull`)”：
  *“本地 `--watch` 通过 Git 原生命令获取远端分支，并只接受 fast-forward……”*
  在 `feat: add managed workspace sync flow` (commit `fdfefe3`) 中，实现者抱着一种**“为了让用户更爽、替用户省去手动 `git pull`”的幼稚便利主义愿望**，在本地客户端循环中植入了 `auto_fast_forward_pull`，导致本地控制端**每隔 10 秒在本地执行一次 `git fetch --prune origin main`**。
* **哪里没有说清楚？**
  1. **没有说清楚“Git 仓库本身也是底层文件系统”这一基本物理事实**！
     本地执行 `git fetch` 会直接写入 `.git/FETCH_HEAD` 和更新 `.git/objects`。在 Windows 操作系统中，只要工作区根目录下有任何文件写入（包括隐藏的 `.git` 子目录），Win32 的 `FindFirstChangeNotificationW` / `ReadDirectoryChangesW` 就会被强行唤醒！
  2. 当时的代码没有把“Git 内部文件写入”与“用户真实代码修改”进行物理阻断。

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
  1. **没有说清楚“监听器 notified 只是唤醒信号，绝不能作为数据变动的判定依据”**！
     文件系统监听器是出了名的噪点来源（编辑器交换文件、系统 index、Git 内部变动都会触发 notified）。将 `(watcher.native and notified)` 作为一个与内容哈希并列的 `or` 条件，等同于向一切系统噪点敞开大门。
  2. **没有说清楚“当改动为空时必须立即短路返回，绝不允许强行编造文案伪装正常”**：
     这行伪兜底是典型的 **Goodhart 欺骗性代码**：它把一个根本没有文件变动的异常空跑分支，强行涂脂抹粉成看似正常的业务文案，堂而皇之地向用户谎报“检测到本地工作区改动”，造成恶性死循环与恐慌。

### 2.3 认知断层三：盲目崇拜“零配置”，把涉密同步做成了无感黑盒
* **当初是基于什么搞出来的？**
  在 `2026-09-15_GITSHADOW_CONTRACT_LAW.md` 第 2.3 节写着：
  *“若项目中未显式配置 .gitshadow，系统优雅兼容默认轻量安全模板……”*
  设计者把前端构建工具（如 Vite/Parcel）的“Zero-Config 零配置开箱即用”盲目套用在文件同步与涉密传输领域。
* **哪里没有说清楚？**
  **没有说清楚“在数据同步与凭据管理领域，知情权与审查权绝对高于便利性”**！
  一个没有 `.gitshadow` 的项目，工具在内存中默默启用默认规则静默运转，用户肉眼看不到任何物理契约文件，心理上就是一片黑盒。一旦终端开始频繁滚屏，用户因为没有实体锚点印证，必然陷入“是否全量对拷、是否泄露家目录”的恐慌。

### 2.4 认知断层四：缺乏日志分层思维，把例行后台巡检做成了“战功报幕”
* **当初是基于什么搞出来的？**
  早期单次执行 `git shadow push` 或 `pull` 时，为了方便调试，控制台输出了全部 JSONL 事件；后来加入后台 8 秒例行轮询时，直接偷懒复用了同一个打印函数。
* **哪里没有说清楚？**
  **没有说清楚“交互式命令的报幕”与“后台例行巡检的静默契约”必须彻底分流”**！后台例行巡检的核心哲学是 **Silence is Golden（没有改动就是最好的输出）**。8 秒刷屏 6 行，直接把一个原本健康的探查做成了狂躁的资源消耗者。

### 2.5 认知断层五：远端边缘代理（Edge Agent）的存在形式语焉不详
* **当初是基于什么搞出来的？**
  早期文档充斥着“范式 A / 范式 B / 租约自毁 / 内核事件”等抽象概念，却没有画出质朴的物理链路图，导致用户怀疑 Mac 上是不是一直挂着个失控的常驻脚本。
* **哪里没有说清楚？**
  **没有用人话讲透“远端压根没有常驻守护进程”**！远端只有一份按需启动的无状态脚本（`~/.local/share/git-shadow/bin/git-shadow-edge-agent.py`），本地通过 SSH 管道喂入 JSON Plan，远端执行完就退出并把事件留痕在 `runs/<job-id>`。

### 2.6 认知断层六：“因噎废食式”的过度防御与一刀切过矫正
* **后来又怎么走偏了？**
  在排查出 `auto_fast_forward_pull` 导致自激死循环后，开发者一度走向了另一个极端：立下所谓的“零触碰本地 Git 公理”，将本地所有后台 Git 操作全盘切除。
* **为什么这也是不对劲的？**
  用户敏锐地指出：“一般情况下，远端 AI 提交了，本地开启 shadow 时，如果本地干净就自动拉取；如果有暂存或未提交修改，就提示警告并延缓；本地提交了，远端自动拉取。现在怎么两端互不拉取了？”
* **哪里没有说清楚？**
  **死循环的根源从来不是 Git 操作本身，而是 HMR 触发器中把“监听器被唤醒”误当成了“代码被修改”！**
  只要坚守 `StrictShadowHashAxiom`（HMR 严格只看 `.gitshadow` 白名单文件的 SHA256 哈希），那么无论本地执行 `git fetch` 还是 `git pull`，底层 `.git` 的写入都**绝对无法**穿透到 HMR 触发层！
  因此，不需要“因噎废食永远不碰 Git”，而是应当建立起**优雅、安全、避让脏工作区的双向 Git 联动模型**！

---

## 3. Decision (今天落地的六大不可动摇公理)

为彻底绝除上述六大隐患，确立以下六大刚性公理：

```text
┌────────────────────────────────────────────────────────────────────────┐
  git-shadow 架构重整公理矩阵 (2026-09-25 终态真源)
├───────────────────────────────┬────────────────────────────────────────┤
  1. 安全 Git 智能联动 (Safe Alignment)│ 干净自动快进，脏工作区避让提示，本地提交通知远端 │
  2. 纯哈希变动触发 (Strict Hash HMR) │ 仅白名单文件 SHA-256 变动才触发热同步，与 Git 隔离│
  3. 契约门禁强制公理 (Mandatory Prompt)│ 首次运行必须交互式询问并落地 .gitshadow 契约    │
  4. 例行巡检完全静默 (Silent 30s Poll)│ 30秒无感知轮询，屏蔽所有过程日志，变动才报幕    │
  5. 零干扰本地心跳 (60s Heartbeat)   │ 本地后台每 60 秒刷新静默时间戳，供 status 查询   │
  6. 无状态边缘代理 (Stateless Edge)   │ 明确 Mac 远端为 SSH RPC 子进程，无常驻僵尸进程   │
└───────────────────────────────┴────────────────────────────────────────┘
```

### 3.1 安全双向 Git 智能联动与脏工作区避让公理 (`SafeGitAlignmentAxiom`)
严格执行用户日常开发的三条核心协同准则：

#### 规则 1：远程端（Mac / Linux）AI 交付
- AI 在远端完成开发，产生暂存并提交，推送到 Git 远程仓库（GitHub）。

#### 规则 2：本地手动提交，远端自动拉取
- 用户在本地手动查看、commit 并 push 到 GitHub；
- 本地 `shadow` 检测到本地提交领先远端分支（`status == local_ahead`），通过轻量 SSH RPC 触发远端 `workspace.prepare(pull=True)`；
- 远端自动从 GitHub 执行 `git pull origin <branch>` 完成拉取对齐，终端输出：
  `[shadow] 🚀 检测到本地新提交 (xxxx)，正在通知远端自动拉取对齐...`
  `[shadow] ✔ 远端已成功拉取 GitHub 最新提交`

#### 规则 3：远程提交时的本地双分支判定与避让
本地 `shadow` 在 30 秒后台巡检或按下 `p` 手动拉取时，检查远端分支提交状态：
- **分支 (a) 本地无暂存、无修改（干净工作区）**：
  自动执行 `git merge --ff-only origin/<branch>` 快进拉取，并在控制台轻量提示：
  `[shadow] 📥 检测到远端新提交 (commit abcd123)，本地工作区干净，已自动拉取同步 (Fast-forward)`。
- **分支 (b) 本地存在暂存（Staged）或未提交（Unstaged / Dirty）修改**：
  **绝对不执行拉取！绝对不修改或 stash 本地任何文件！**
  安全延缓拉取动作，并在控制台输出醒目的黄色 Warning / INFO 提示：
  `[shadow] 💡 远端 AI 有新提交 (commit abcd123)，但本地存在未提交/暂存的修改，已安全暂缓自动拉取。请本地提交或暂存后再 pull。`
  为了防止在用户 coding 时反复刷屏骚扰，系统缓存 `_last_warned_remote_commit`，同一远端 commit 仅提醒一次。

### 3.2 纯哈希变动触发公理 (`StrictShadowHashAxiom`)
- 废除任何以“底层监听器是否通知”作为推送依据的伪逻辑（彻底删除 `or (watcher.native and notified)`）；
- **唯一触发条件**：只有在 `.gitshadow` 显式白名单内的私有文件（及 `.gitshadow` 本身）的实际 **SHA-256 内容哈希发生变化（`current != previous`）** 时，才允许发起 HMR 影子推送；
- `.git` 目录下的任何变动（包括 `git fetch` 写入的 `FETCH_HEAD`）由于不在 `.gitshadow` 白名单内，**绝对无法触发 HMR 推送**，死循环的物理通路被永久切断。

### 3.3 契约门禁强制公理 (`MandatoryContractPromptAxiom`)
- 在项目首次运行 `shadow` 或绑定目标主机时，若检测到工作区根目录不存在 `.gitshadow` 文件，**必须主动停下来，交互式询问用户并生成标准模板**；
- 彻底废除“静默内存兜底”，让用户在启动的第一秒就对即将被同步的文件（`.env*`、私钥、`config/local_config.py`、`.runtime/`）拥有 100% 的知情权与审查权。

### 3.4 例行探查完全静默公理 (`SilentPollAxiom`)
- 后台例行探查远端影子状态默认间隔从 8 秒收敛至 **30.0 秒**；
- **例行探查必须 100% 完全静默（Silent Pull）**：
  - 严禁打印 `workspace.create`、`step.started`、`step.succeeded` 等过程垃圾信息；
  - **有改动才报幕**：仅当远端切实产生了文件同步（`shadow.remote`）、冲突（`shadow.conflict`）、或 Git 提交更新（`git pull` / `blocked_dirty`）时输出提示；零改动时保持零输出。

### 3.5 零干扰本地心跳公理 (`ZeroOverheadHeartbeat`)
- 本地后台守护循环每 60 秒刷新一次本地守护进程心跳文件（`LocalDaemonManager.touch_heartbeat`）；
- 零控制台输出，零网络包消耗，仅为状态查询命令（`shadow status`）提供本地进程的存活判定。

### 3.6 无状态边缘代理拓扑定性 (`StatelessEdgeRPCTopology`)
- 远端 Mac 上的真实运行形态定性为纯无状态 SSH RPC 代理（`~/.local/share/git-shadow/bin/git-shadow-edge-agent.py`）；
- **无常驻常开的后台失控守护进程**，完全由本地 SSH 管道触发、随任务结束而退出；
- 任务执行详情脱敏记录于 `~/.local/share/git-shadow/runs/<job-id>/events.ndjson`，随时可追溯审计。

---

## 4. 历史决策冲突审查（Conflict Review & Resolution）

针对用户指出的“看看与以前是否有冲突”，对现有决策体系进行逐一排查与裁决：

| 历史决策文件 | 历史条款 | 本次新决策判定 | 裁决结果 |
| :--- | :--- | :--- | :--- |
| **`2026-09-15_LAYERED_SYNC_OWNERSHIP_LAW.md`** | §2.1 GitTrackedLane：“公有代码只走 Git，非快进、脏工作区和合并冲突必须交给 Git 显式处理，禁止工具静默强推或覆盖。” | **完全契合，无冲突**。本次升级严格遵循此条：公有代码绝不以普通文件覆盖，必须走 Git；脏工作区坚决不碰、只给提示；非快进坚决不强制合流。 | 保持一致并继承 |
| **`2026-09-15_CONTINUOUS_AUTO_SYNC_AND_HEARTBEAT_LAW.md`** | 原 §2.5 粗暴的 10 秒定时 fetch 逻辑 | **历史缺陷，已废除并升级**。原逻辑未做 HMR 哈希隔离，引发推拉死循环。现已就地改写，正式演进为本双向安全联动律。 | 就地演进改写 |
| **`2026-09-25 早先草案中的 ZeroLocalGitTouchAxiom`** | “本地控制端严禁在本地工作区执行任何后台 git fetch、git pull、git merge 或调用任何读写本地 .git 的命令” | **存在冲突（过矫正）！** 该条文系当时为切断死循环做出的防御性一刀切，导致两端 Git 提交无法自动感知拉取。现以 `SafeGitAlignmentAxiom` 为准，允许安全的 fetch 与干净工作区的 fast-forward。 | **正式废除过矫正条款，以本决策为准** |

---

## 5. Consequences & Non-Negotiable Guards (后果与不可逆门禁)

1. **绝对禁令**：严禁在本地工作区脏（Dirty，含 staged / unstaged）状态下自动执行任何形式的 `git merge`、`git pull`、`git stash` 或 `git reset`。脏工作区只能且必须仅输出提示；
2. **测试门禁**：`tests/test_git_sync.py` 必须全面覆盖干净工作区自动拉取、脏工作区安全拦截、本地超前感知检测，测试必须 100% 通过；
3. **静默红线**：后台 Routine 任务除真实数据变动、新提交拉取与脏工作区避让提示外，严禁在控制台输出任何过程日志。
