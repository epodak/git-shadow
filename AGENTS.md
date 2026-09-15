# AGENTS.md — git-shadow 核心法则与协同宪法

> 本文件不是普通的开发文档。
> 它定义 `git-shadow` 项目在面对人类开发者、AI 编程智能体（Coding Agents）和跨端自动化时必须成立的**最高公理与不变法则**。
> 任何代码重构、功能扩展与文档变更，均不得违背本文件确立的不变量。

---

## 1. 核心哲学：分层协同与三态隔离 (Three-State Axiom)

`git-shadow` 严格将分布式云端开发拆解为三种不可混淆的状态层级：

```text
Public Code State (公有代码层)
  ↓ 映射
Shadow Secrets State (私有影子层)
  ↓ 隔离
Native Runtime State (原生依赖层)
```

1. **公有代码走 Git**：
   * 基础代码通过远端骨干网直接从 Git Remote 克隆或通过 Git 协议传输；
   * 绝不允许通过全量 rsync 或慢速网络文件系统（SSHFS/Samba）盲目对拷代码仓。
2. **私有配置走影子**：
   * 受 `.gitignore` 保护的敏感配置（`.env*`、私钥、本地调试文件）由影子引擎在内存中流式压缩、传输、覆盖解压；
   * 影子传输必须保持极速（通常 < 50 KB）、毫秒级完成，零上行带宽浪费。
3. **重型依赖走原生**：
   * `node_modules`、Python `venv`、二进制动态库（`.so`/`.dll`）**绝对禁止**跨平台同步；
   * 所有依赖必须在远端 Linux 宿主原生就地安装，彻底杜绝跨系统二进制兼容灾难。

---

## 2. 交互范式：Web 优先与双模操控律 (Web-First Paradigm)

针对现代云端开发和 AI Coding Agent，交互形态遵循明确的层级分工：

```text
Web 远程控制台 (CloudCLI / Zero Trust) ── 第一公民 (默认优先)
  ↓ 互补
TTY 终端交互 (OpenCode / Claude Code / Bash) ── 调试与底座
```

1. **Web 优先与统一运行范式 (`run`)**：
   * 彻底取缔硬编码专属 `web` 命令，所有 AI 应用与交互环境统一由 `git shadow run <host> [agent]` 统领；
   * 当运行 Web 型应用（如 `cloudcli`）时，自动调用系统默认浏览器弹出对应 Web 页面；运行终端 AI（如 `commandcode`/`opencode`）时进入远端交互 Shell。
2. **乐观先行与时间交叉重叠律 (Cross-Overlap Execution)**：
   * **交互通道第一响应**：敲击命令瞬间（0 毫秒）立即拉起浏览器或终端交互通道，绝不让人类死等后台传输；
   * **后台并发随行投影**：代码基线、私密影子文件（.env）与未提交补丁在后台并发管道中流式打入，在人类页面加载与视线聚焦的 2~3 秒内悄然对齐，实现零感知等待。
3. **终端黑框底座原则**：
   * 终端命令（如 `probe`、`auth sync`、`up`）专注于基础设施诊断、环境自愈与深度调试；
   * 运行终端 AI Agent 时，提供自动感知与交互式数字菜单挑选，禁止让用户陷入手敲命令盲猜的窘境。


---

## 3. 文档治理法：真源文档所有权 (Canonical Document Ownership)

本项目严格执行 **Canonical Document Composition Law（真源文档组合律）**，彻底拒绝面条式补丁与多头冲突：

```text
one concept = one owner            # 一个核心概念有且仅有一个真源所有者
owner exports concept               # 所有者定义并导出规范
consumer imports OWNER.Concept      # 消费者仅引用，禁止自说自话重新解释
history explains why (ADR)          # 决策与权衡演变归入 docs/decisions/
implementation proves what runs     # 代码与自动化测试证明当前实际运行
```

### 文档分层职责：
1. **本文件 (`AGENTS.md`)**：项目宪法，定义系统固定点（Fixed Points）、全局公理与不可违背的硬约束。
2. **架构决策真源 (`docs/decisions/`)**：
   * 记录所有系统级架构抉择、权衡、为什么废弃旧方案（ADR 格式）；
   * 每篇决策明确标注其拥有的 Canonical Concepts。
3. **日常工程轨迹 (`_dev_log/YYYY-MM-DD_主题.md`)**：
   * 沉淀调试细节、测试日志与阶段验收结果，作为历史备查；
   * 禁止在根目录随意新建散落的临时 `.md` 文件。
4. **禁止面条补丁铁律**：
   * 任何现有概念发生修正时，直接就地改写其 Canonical Owner 正文；
   * **严禁在文档末尾追加“补充说明”、“整合澄清”、“其实上面不是那个意思”等打架修补补丁**。
5. **智能体技能真源 (`.agents/skills/git-shadow/SKILL.md`)**：
   * AI 时代的第一公民交互文档与可执行 Cheatsheet；
   * 既是产品创作迭代的即时验收标准，也是对外交付时外部 AI 智能体自动学会操控 `git-shadow` 的自举入口（一举两得与跨端镜像自举）。


---

## 4. Git 身份治理与跨端安全契约 (Git Identity Governance)

身份与凭证跨端同步（`git shadow auth sync`）必须遵守以下四项不可推翻的安全准则：

1. **Diff-First 比对先行，严禁暴力覆写**：
   * 密钥写入前必须比对本地与远端 SHA-256 指纹；一致则跳过，严禁无脑重传；
   * `~/.ssh/config` 必须按标记块（Block-Level）增量合并，严禁覆盖或破坏用户在远端已有的其他 Host 与网络配置。
2. **Scoped Git Auth 范围铁律**：
   * 凭证治理严格限定在 Git 托管服务鉴权（GitHub / GitLab / Gitee 等），绝不越界干涉服务器系统账号、sudo 权限或通用运维网络。
3. **多 Git 账号上下文精准推断**：
   * 系统必须根据当前本地仓库的 Remote URL 和本地 SSH Config 自动推导匹配的密钥；
   * 当存在多密钥冲突时，必须提供交互式菜单或 `--key` 参数供显式确认，严禁盲目套用默认 key 导致 403 权限灾难。
4. **提交人身份伴随对齐 (Author Alignment)**：
   * 同步密钥时，必须伴随将本地仓库的 `user.name` 和 `user.email` 对齐至远端，确保 AI Agent 提交的代码归属正确，杜绝 commit 匿名或报错中断。

---

## 5. 跨平台执行底座与环境感知 (Platform Adaptability)

针对 Windows 本地客户端与 Linux 远端 VPS 混编环境，系统必须具备以下防御性适配能力：

1. **路径防污染机制**：
   * 严格清洗 Windows Git Bash (MSYS2) 引入的绝对路径自动转换（如强行将 `/` 开头转为 `D:/Tool/...` 本地盘符）；
   * 远端路径计算一律通过远端原生 `$HOME` 展开，杜绝 Windows 盘符渗透到远端 Linux 命令中。
2. **SSH 管道防冲突保护**：
   * 在所有非交互式命令与流式数据传输中，必须显式前置：
     `-o RemoteCommand=none -o RequestTTY=no`
   * 彻底消除宿主在 `~/.ssh/config` 中配置的交互式工具（如 Zellij / tmux）对后台自动化通道的拦截与冲突。
3. **远端工作区自发现 (Probe-First)**：
   * 任何写操作前必须探针先行；
   * 自动探测远端已有的标准工作区目录（优先 `~/wkspace`，其次 `~/workspace`、`~/projects`），智能对齐默认承载路径。

---

## 6. 当前命令与能力真源矩阵

任何针对 CLI 的调整，必须确保以下核心能力矩阵的语义连续性：

| 命令 | 行为契约 | 交互形式 | 核心目标 |
| :--- | :--- | :--- | :--- |
| **`run <host> [agent]`**| 乐观先行拉起 AI，后台并发流式投影代码与影子 | 浏览器弹窗 (Web AI) / 终端 (TTY AI) | 零感知等待，时间交叉重叠，统一入口 |
| **`probe <host>`** | 探针全景扫描系统、工作区、AI 工具 | 格式化诊断看板 | 呈现应用版本（带更新提醒）与运行环境 |
| **`auth sync <host>`**| 净化并同步 Git SSH 凭证与作者信息 | 3 步闭环测试与反馈 | 遵循 Diff-First 契约打通 Git 权限 |
| **`diff`** | 纯本地扫描 `.gitignore` 保护文件 | 高保真树状图 (Diff Tree) | 投影前自检隐私泄露风险与改动行数 |
| **`push <host>`** | 静默推送代码基线、影子文件与补丁 | 无头静默执行 | 后台快速同步工作区 |
| **`pull <host>`** | 从远端拉取最新代码到本地 | Git 标准合流 | AI 交付成果本地一键收割 |
| **`service <host> [load\|unload\|status]`** | 范式 B：加载/卸载 Linux 后台同步守护 | Linux 宿主系统级治理 | 远端常驻 + 租约超时自毁释放内存 |
| **`run <host> [agent] --watch`** | 范式 A：前台随行实时监听与防抖推送 | 弹窗/终端 + 动态心跳 | 终端关闭即随行销毁，绝对零残留 |
