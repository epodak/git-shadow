---
name: git-shadow
description: 跨端 Git 代码基线与私有影子配置（.env/私钥）极速流式投影工具。支持 CloudCLI Web 会话一键弹窗、远端环境全景探针、Diff-First Git 身份同步、Linux 后台守护治理（带 10 分钟租约超时自毁）与前台随行实时监听。当需要将本地项目推送到海外 VPS、在浏览器中打开云端开发会话、打通远端 Git 鉴权或治理后台同步时触发使用。
---

# git-shadow 智能体交互指南与操作速查

`git-shadow` 是面向现代云端开发与 AI Coding Agent 的极速流式投影工具。它严格恪守**三态隔离公理**：
1. **公有代码走 Git**（远端骨干网直接 clone/fetch 或本地 P2P 流式推送）；
2. **私有配置走影子**（受 `.gitignore` 与 `.gitshadow` 保护的敏感文件通过按远端目标隔离的 Manifest/CAS 原子投影，禁止静默覆盖另一端修改）；
3. **重型依赖走原生**（远端 Linux 宿主原生就地安装，绝不跨平台对拷）。

分层同步硬边界：Git 追踪文件只交给 Git；`.gitshadow` 文件只交给 Shadow Manifest/CAS；未提交追踪文件默认不同步（只有显式 `--wip` 才作为一次性补丁投影）。

---

## 🚀 核心命令矩阵速查 (Quick Reference)

| 场景 | 推荐命令 | 核心行为 |
| :--- | :--- | :--- |
| **一键静默监控 (极简推荐)** | **`shadow`** / **`gsw`** | **智能零摩擦**：已绑定项目**直接拉起后台监控并输出状态看板**；未绑定项目自动交互引导选择 VPS 与远端目录并记忆 |
| **查看后台状态与日志** | **`shadow status`** / **`gsw st`** | 查看当前项目守护进程健康度 (PID/心跳/运行时长) 与最近同步日志 |
| **停止后台静默同步** | **`shadow stop`** | 优雅停止本地后台守护进程，释放资源 |
| **换绑目标 VPS / 路径** | **`shadow switch`** | 重新呼出交互引导，切换当前项目同步的目标主机与远端目录 |
| **一键启动 AI (统一入口)** | `git shadow run <host> [agent]` | **乐观先行**：0 秒弹出浏览器 (Web AI) 或连通终端 (TTY AI)，**后台并发打入代码与影子**，零感知等待 |
| **数字菜单智能挑选** | `git shadow run <host>` | 自动探查远端已就绪的 AI Agent，呈现数字菜单供一键挑选启动 |
| **前台随行实时监听** | `git shadow run <host> [agent] --watch` | 范式 A：本地 `.gitshadow` 双向 CAS + 干净 Git 分支 ff-only 自动拉取，**终端关闭即自动随行销毁，零残留** |
| **Linux 后台服务治理** | `git shadow service <host> load` | 范式 B：将项目级任务执行服务挂载到远端 Linux 后台，**带 10 分钟心跳租约超时自毁，彻底释放 VPS 内存** |
| **注销 Linux 服务** | `git shadow service <host> unload` | 优雅停止远端任务服务，清除 PID/Lease/socket，归还服务进程资源 |
| **检查服务状态** | `git shadow service <host> status` | 查看远端服务 PID、版本、状态与租约剩余秒数 |
| **环境与工具全景诊断** | `git shadow probe <host>` | 格式化输出远端 OS、架构、主目录、工作区路径（`~/wkspace`）及已安装 AI Agent 版本 |
| **Git 鉴权与身份治理** | `git shadow auth sync <host>` | 遵循 Diff-First 契约，净化同步本地 SSH 密钥到远端，打通 GitHub 权限并对齐提交人信息 |
| **投影前本地自检** | `git shadow diff` | 高保真树状图 (Diff Tree) 扫描待投影的私有影子文件与 WIP 代码修改 |
| **收割远端 AI 产出** | `git shadow pull <host>` | 从远端 Git 仓库拉取最新 commit 到本地 |
| **收割包含影子配置改动** | `git shadow pull <host> --with-shadows` | 同步拉回远端修改的 `.gitshadow` 私密文件；若发生冲突隔离生成 `.remote` 文件 |
| **纯静默后台推送** | `git shadow push <host>` | 仅推送代码基线与影子，不进入交互式终端，不弹窗 |
| **手动终端远程调试** | `git shadow up <host>` | 投影后直接进入交互式 Remote Shell |
| **安装 VPS 边缘执行器** | `git shadow edge install <host>` | 上传独立 JSONL 执行器，不要求远端安装 Python 包 |
| **查询/恢复远端任务** | `git shadow edge status <host> <job>` / `edge resume <host> <job>` | 查询 Job 状态或按 seq 重放事件 |

---

## 📚 专项治理与深度 SOP (References)

对于特定复杂场景与异常恢复，查阅对应的专项深度文档：

- **CAS 并发冲突处理与三方合并**：👉 [CAS_CONFLICT_RESOLUTION.md](references/CAS_CONFLICT_RESOLUTION.md)
  *覆盖：Base Hash 失配告警、`.remote` 隔离生成、手动/双栏比对合并与基线更新 SOP。*
- **CloudCLI 鉴权自愈与深链挂载**：👉 [CLOUDCLI_DEEPLINK_AUTH.md](references/CLOUDCLI_DEEPLINK_AUTH.md)
  *覆盖：401 令牌过期读取 `auth.db` 离线签发 7 天 HS256 JWT、409 项目存在幂等复用、乐观深链。*
- **持续自动同步与租约自毁**：👉 [CONTINUOUS_SILENT_SYNC.md](references/CONTINUOUS_SILENT_SYNC.md)
  *覆盖：10 分钟 Lease 超时内存回收、Windows/Linux 监听机制差异、双端 Edge 对称演进路线。*

---

## 🛡️ 核心法则与防坑红线 (Critical Rules)

任何 AI Agent 在调用、调试或扩展 `git-shadow` 时，必须严格遵守以下法则：

### 0. CLI 注册原理与 Git 原生子命令发现 (Git Subcommand Discovery)
- **子命令发现机制**：`git shadow` 能够全局直接运行，依赖 Git 的原生扩展发现机制——当敲击 `git shadow` 时，Git 会自动从系统 `$PATH` 中定位可执行的 `git-shadow`（或 `git-shadow.cmd`）并透传全部参数；
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

### 场景 B：长时间开发（范式 B 服务治理 + 本地 watcher）
```bash
# 加载后台服务到 Linux 治理
git shadow service aws load

# 本地持续发现并双向提交 .gitshadow 变化，同时安全拉回远端 Git 提交
git shadow run aws cloudcli --provider codex --service --watch

# 查看运行状态与租约倒计时
git shadow service aws status

# 编码完成或下班前显式注销；若本地 watcher 失联，远端 Lease 也会自动自毁
git shadow service aws unload
```

### 场景 C：收割远端改动与冲突处理
```bash
# 从远端拉回 Git 代码与影子文件
git shadow pull aws --with-shadows

# 若出现 CAS 冲突，按提示合并 .remote 文件后重新推送
git shadow push aws
```
