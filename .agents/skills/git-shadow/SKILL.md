---
name: git-shadow
description: 跨端 Git 代码基线与私有影子配置（.env/私钥）极速流式投影工具。支持 CloudCLI Web 会话一键弹窗、远端环境全景探针、Diff-First Git 身份同步、Linux 后台守护治理（带 10 分钟租约超时自毁）与前台随行实时监听。当需要将本地项目推送到海外 VPS、在浏览器中打开云端开发会话、打通远端 Git 鉴权或治理后台同步时触发使用。
---

# git-shadow 智能体交互指南与操作速查

`git-shadow` 是一款面向现代云端开发与 AI 编程智能体的跨端极速流式投影工具。它遵循**三态隔离公理**：
1. **公有代码走 Git**（远端骨干网直接 clone/fetch 或本地 P2P 流式推送）；
2. **私有配置走影子**（受 `.gitignore` 与 `.gitshadow` 保护的敏感文件通过 Manifest/CAS 原子投影，禁止静默覆盖另一端修改）；
3. **重型依赖走原生**（远端 Linux 宿主原生安装，绝不跨平台对拷）。

分层同步的硬边界：Git 追踪文件只交给 Git；`.gitshadow` 文件只交给 Shadow Manifest/CAS；未提交追踪文件默认不同步，只有显式 `--wip` 才作为一次性补丁投影。

---

## 🚀 核心命令矩阵速查 (Quick Reference)

| 场景 | 推荐命令 | 核心行为 |
| :--- | :--- | :--- |
| **一键启动 AI (统一入口)** | `git shadow run <host> [agent]` | **乐观先行**：0 秒弹出浏览器 (Web AI) 或连通终端 (TTY AI)，**后台并发打入代码与影子**，零感知等待 |
| **数字菜单智能挑选** | `git shadow run <host>` | 自动探查远端已就绪的 AI Agent，呈现数字菜单供一键挑选启动 |
| **前台随行实时监听** | `git shadow run <host> [agent] --watch` | 范式 A：本地文件保存即 300ms 防抖增量推送，**终端关闭即自动随行销毁，零残留** |
| **Linux 后台守护治理** | `git shadow service <host> load` | 范式 B：将增量同步守护挂载到远端 Linux 后台，**带 10 分钟心跳租约超时自毁，彻底释放 VPS 内存** |
| **注销 Linux 守护** | `git shadow service <host> unload` | 优雅停止远端 watcher 进程，清除 PID 锁与句柄，100% 归还物理内存 |
| **检查守护状态** | `git shadow service <host> status` | 查看远端守护进程 PID、运行时间、内存开销与租约剩余秒数 |
| **环境与工具全景诊断** | `git shadow probe <host>` | 格式化输出远端 OS、架构、主目录、工作区路径（`~/wkspace`）及已安装 AI Agent 版本 |
| **Git 鉴权与身份治理** | `git shadow auth sync <host>` | 遵循 Diff-First 契约，净化同步本地 SSH 密钥到远端，打通 GitHub 权限并对齐提交人信息 |
| **投影前本地自检** | `git shadow diff` | 高保真树状图 (Diff Tree) 扫描待投影的私有影子文件与 WIP 代码修改 |
| **收割远端 AI 产出** | `git shadow pull <host>` | 从远端 Git 仓库拉取最新 commit 到本地 |
| **纯静默后台推送** | `git shadow push <host>` | 仅推送代码基线与影子，不进入交互式终端，不弹窗 |
| **手动终端远程调试** | `git shadow up <host>` | 投影后直接进入交互式 Remote Shell |
| **安装 VPS 边缘执行器** | `git shadow edge install <host>` | 上传独立 JSONL 执行器，不要求远端安装 Python 包 |
| **查询/恢复远端任务** | `git shadow edge status <host> <job>` / `edge resume <host> <job>` | 查询 Job 状态或按 seq 重放事件 |


---

## 🛡️ 核心法则与防坑红线 (Critical Rules)

任何 AI Agent 在调用、调试或扩展 `git-shadow` 时，必须严格遵守以下法则：

### 0. CLI 注册原理与 Git 原生子命令发现 (Git Subcommand Discovery)
- **子命令发现机制**：`git shadow` 能够全局直接运行，依赖 Git 的原生扩展发现机制——当敲击 `git shadow` 时，Git 会自动从系统 `$PATH` 中定位可执行的 `git-shadow`（或 `git-shadow.cmd`）并透传全部参数；
- **全终端免安装包装模式**：在全终端管理体系中（如 `D:/Tool/DIY` 或 `~/.local/bin`），建议采用注入 `PYTHONPATH` 的双子包装器（`git-shadow` + `git-shadow.cmd`），既不污染 Python 全局环境，又实现本地代码修改即刻热生效；
- **关键避坑：`--help` 陷阱**：严禁调用 `git shadow --help`（Git 会尝试检索内置 HTML 手册导致报 `documentation file not found` 错误）；查看全部命令时必须使用：
  ```bash
  git shadow -h
  # 或
  git-shadow --help
  ```

### 1. 跨平台路径防污染 (Platform Isolation)
- **Windows MSYS2 绝对路径陷阱**：在 Windows Git Bash 下，严禁将 `/` 开头的绝对路径（如 `-d /home/...`）直接裸传给外部工具，MSYS2 会强行将其转译为 `D:/Tool/...` 等本地盘符；
- **防御要求**：远端路径必须优先依赖远端环境探针感知的 `$HOME` 展开（如自动解析为 `/home/ubuntu/wkspace/<repo_name>`）。

### 2. SSH 非交互管道防冲突 (SSH Clean Channel)
- 远端或本地 `~/.ssh/config` 可能配置了 `RemoteCommand ... zellij` 或 `RequestTTY yes`；
- **防御要求**：底层所有执行命令、流式传输、探针扫描等非交互 SSH 调用，必须显式前置：
  ```bash
  ssh -o RemoteCommand=none -o RequestTTY=no <host> "<cmd>"
  ```
  彻底杜绝终端多路复用器拦截后台自动化管道。

### 2.1 VPS 边缘执行器与异步广播 (Remote Edge Executor)
- 长任务不得由本地逐条等待 SSH 返回；本地将结构化 `TypedExecutionPlan` 一次提交给 VPS 边缘执行器。
- 传输使用同一条干净 SSH 通道上的双向 JSONL：提交同步返回 `accepted + job_id`，执行过程异步广播 `step.started`、`output`、`session.ready`、`job.completed` 或 `job.failed`。
- VPS 为每个 Job 持久化脱敏的 `request.json`、`state.json` 和带单调 `seq` 的 `events.ndjson`；本地断线后必须使用 `resume + after_seq` 重放，不得重新猜测最新会话。
- 默认只允许结构化动作（`workspace.create`、`cloudcli.session`、`workspace.prepare`、`shadow.sync` 和 argv 数组形式的 `exec`），禁止无校验的 Shell 命令串拼接；`patch.apply` 仅在用户显式传入 `--wip` 时出现。
- `workspace.create` 后立即创建 CloudCLI Session；随后 `workspace.prepare` 才执行 Git clone/init/checkout，`shadow.sync` 携带 `base_hash/local_hash` 并由 VPS 执行 CAS。若 CAS 冲突，任务必须广播失败，禁止伪造“工作区已准备完成”或重复创建 Session。
- CloudCLI 会话必须由同一个远端 Job 明确创建，收到 `session.ready` 后才打开 `/session/<id>`；禁止直接扫描 SQLite 选择“最新会话”。
- CloudCLI provider 必须在 Session 创建前确定；命令行使用 `--provider`，交互模式使用本地菜单。当前数据库 provider 非空，不创建 provider-neutral 临时 Session。
- 短任务完成即退出；长期服务继续遵循 600 秒 Lease 心跳和超时自毁契约。

### 3. `.gitshadow` 显式白名单契约 (No Blackbox)
- 严禁在底层 Python 代码中硬编码排除黑名单（如私自排除 `_dev_log/`）；
- 受保护但需要投影的文件，由根目录 [`.gitshadow`](file:///.gitshadow) 统一治理声明；
- 默认合规白名单项包括：`.env*`、`*.secret`、`*.key`、`_dev_log/`、`*.local.md`。

### 4. 远端内存防御与租约自毁契约 (`LeaseBasedAutoTeardown`)
- VPS 物理内存极其宝贵，守护进程内存开销严格限制在 `< 5 MB`；
- 远端守护进程采用内核级 `inotify` 驱动，空闲期 CPU 占用恒定 `0.00%`；
- **租约超时自毁**：当本地客户端离线超过 10 分钟（无心跳握手刷新），远端服务自动卸载自毁，清理所有进程与句柄，绝不留僵尸进程。

---

## 💡 典型工作流 SOP

### 场景 A：新项目首次投影到远端并用 Web 结对
```bash
# 1. 探针先行：感知远端配置与工作区目录
git shadow probe aws

# 2. 身份打通：一次性同步 GitHub SSH 凭证与提交人配置
git shadow auth sync aws

# 3. 投影并自动弹开浏览器
git shadow run aws cloudcli --provider codex
# -> 浏览器将自动打开 https://cli.daduiot.com，进入对应工作区
```

### 场景 B：长时间脱机开发（范式 B 服务治理）
```bash
# 加载后台服务到 Linux 治理
git shadow service aws load

# 查看运行状态与租约倒计时
git shadow service aws status

# 编码完成或下班前显式注销
git shadow service aws unload
```

### 场景 C：VPS 批量执行并打开 CloudCLI 深链
```bash
# 首次使用：上传独立的 VPS 边缘执行器
git shadow edge install aws

# 一次提交 Git 基线、Shadow CAS 和 CloudCLI 会话任务
git shadow run aws cloudcli --provider codex

# 断线后查看或重放任务事件
git shadow edge status aws <job-id>
git shadow edge resume aws <job-id>

# 如确实需要把未提交追踪代码临时交给远端 Agent，必须显式开启
git shadow run aws cloudcli --provider codex --wip
```

VPS 上的 CloudCLI 内部地址默认是 `http://127.0.0.1:3001`，可通过 `--cloudcli-url` 或 `GIT_SHADOW_CLOUDCLI_BASE_URL` 覆盖。需要鉴权时只在 VPS 环境设置 `GIT_SHADOW_CLOUDCLI_TOKEN` / `GIT_SHADOW_CLOUDCLI_API_KEY`，绝不把凭证放进任务事件或日志。
