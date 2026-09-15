# git-shadow

<p align="center">
  <b>Git clones the repo. Shadow overlays the secrets.</b><br>
  Instant cloud workspace projection for remote AI coding agents (OpenCode, Claude Code, Aider, Codex, etc.).
</p>

<p align="center">
  <a href="#key-features">Key Features</a> •
  <a href="#why-git-shadow">Why git-shadow</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#chinese-readme">中文说明</a>
</p>

---

## 💡 Why git-shadow?

Running AI coding agents (like **OpenCode**, **Claude Code**, or **Aider**) inside remote cloud servers (AWS EC2, Hetzner, DigitalOcean, etc.) is incredible:
- **Zero latency** to AI provider APIs (Anthropic, OpenAI).
- **Clean, native Linux environment** with fast package managers (`pnpm`, `uv`, `cargo`).
- **No Windows path / line-ending quirks**.

**However, bridging your local work with the cloud has been painful:**
- ❌ **Network Mounts (SSHFS / Samba)**: Running `git status` or `ripgrep` over WAN hangs or crashes the agent due to high RTT metadata round-trips.
- ❌ **Full Sync (Rsync / Mutagen)**: Uploading `node_modules` or `.venv` wastes gigabytes of bandwidth and breaks binary `.node` / `.so` compatibility across platforms.
- ❌ **Pure Git Clone**: If you only clone, your local `.env`, private keys, local configs, and uncommitted WIP changes are missing. The AI can't run tests!

### Enter `git-shadow`

`git-shadow` splits your workspace into **Three Layers**:
1. **Base Layer (Code Base)**: Let the remote server `git clone` / `git checkout` directly from GitHub via its ultra-fast backbone network (takes ~2 seconds).
2. **Shadow Layer (Secrets & Overlays)**: Automatically detects files ignored by `.gitignore` (e.g. `.env`, `.pem`, `config.local.json`) and streams them over an encrypted SSH pipe in milliseconds (< 50 KB).
3. **Native Layer (Heavy Dependencies)**: Dependencies (`node_modules`, `venv`) are installed natively on the remote Linux host — never transferred across platforms.
4. **WIP Layer (Opt-in Only)**: Uncommitted local diffs are never synchronized by default; pass `--wip` when a remote AI agent explicitly needs a one-time patch.

---

## ⚡ Architecture

```mermaid
flowchart TD
    subgraph Local [Local Machine (Windows / macOS / Linux)]
        A[Git Codebase] -- Push / Branch Ref --> GH[GitHub / Remote Git]
        B[Local .gitignore Files<br/>.env, *.secret, local configs] -- Compressed Stream (<50KB) --> SSH_PIPE[Encrypted SSH Pipe]
        C[.gitshadow Manifest + CAS] -- Atomic Shadow Sync --> SSH_PIPE
    end

    subgraph Remote [Cloud Server (AWS / VPS / Linux)]
        GH -- Fast Backbone Clone/Pull --> R_DIR[Remote Workspace]
        SSH_PIPE -- CAS Shadow Overlay --> R_DIR
        R_DIR --> D[Native Package Install<br/>pnpm / uv / cargo]
        D --> AI[AI Agent Running at Native SSD Speed<br/>OpenCode / Claude Code]
        AI -- Commit & Push --> GH
    end
```

---

## 🚀 Quick Start

### 1. Installation & Git CLI Registration

Zero external dependencies! Works with pure Python 3.8+:

#### Option A: Install via Pip (Standard)
```bash
git clone https://github.com/epodak/git-shadow.git
cd git-shadow
pip install -e .
```

#### Option B: Standalone Wrapper (Zero Environment Pollution / Live Reload)
You can also place a lightweight wrapper script named `git-shadow` (or `git-shadow.cmd` on Windows) into any directory in your system `$PATH` (e.g. `~/.local/bin` or `D:/Tool/DIY`):

```bash
#!/bin/bash
export PYTHONPATH="/path/to/git-shadow:$PYTHONPATH"
exec python -m git_shadow.cli "$@"
```

#### How `git shadow` Works (Git Subcommand Discovery)
Git has a built-in subcommand discovery mechanism: whenever you type `git <subcommand>`, Git searches your system `$PATH` for an executable named `git-<subcommand>`.
Therefore, having `git-shadow` in your `$PATH` enables both commands interchangeably:
- `git shadow <command>`
- `git-shadow <command>`

> [!TIP]
> **Help Flag Tip**: Running `git shadow --help` causes Git to intercept the call and look for its built-in HTML/manpage manuals (which throws a "documentation file not found" error). To view the complete CLI options and ASCII banner, use:
> ```bash
> git shadow -h
> # or
> git-shadow --help
> ```

### 2. Common Usage

#### 🔍 Preview your shadow files
Inspect which private files and uncommitted diffs will be projected to the cloud:
```bash
git shadow diff
```

#### 🚀 Project & launch remote AI Agent directly
One command will:
1. Clone / align the current git branch on your remote host.
2. Inject your local `.env` and secret configs.
3. Leave tracked uncommitted code local by default (`--wip` is explicit).
4. Launch the AI agent inside the remote workspace!

```bash
git shadow run aws-micro opencode
```

#### 🌐 Batch the remote projection through the VPS edge executor

For CloudCLI, `git-shadow` uploads a standalone VPS executor, submits one
typed job over a clean SSH JSONL channel, streams progress asynchronously, and
opens the exact session returned by CloudCLI:

The session is created immediately after the remote project directory exists;
Git clone/checkout and Shadow CAS projection continue afterward, so Codex can
start observing the workspace while preparation is still running.

```bash
git shadow edge install aws-micro
git shadow run aws-micro cloudcli --provider codex
git shadow edge status aws-micro <job-id>
git shadow edge resume aws-micro <job-id>
```

The VPS executor persists redacted job metadata, state, and replayable events
under `~/.local/share/git-shadow/runs/<job-id>/`. Set
`GIT_SHADOW_CLOUDCLI_BASE_URL` on the VPS when CloudCLI is not listening on
`http://127.0.0.1:3001`. CloudCLI credentials stay in VPS environment
variables and are never written to the event journal.

#### 💻 Project & open an interactive shell
```bash
git shadow up aws-micro
```

#### 📦 Push shadows silently
```bash
git shadow push aws-micro
```

#### 🔄 Pull AI's committed changes back to local
```bash
git shadow pull aws-micro
```

---

## ⚙️ Configuration (`.shadowignore`)

By default, `git-shadow` automatically filters out heavy dependency and cache directories (`node_modules/`, `.venv/`, `dist/`, `build/`, `.next/`, `__pycache__/`, etc.).

If you have additional large local files that should not be sent, create a `.shadowignore` in your repository root:

```gitignore
# .shadowignore
large_dataset/
*.mp4
test_dump.sql
```

---

## 🇨🇳 中文说明 (Chinese README)

`git-shadow` 专为**在远端云主机（如 AWS / 独立 VPS）调用 AI 编程智能体（OpenCode, Claude Code 等）辅助本地开发**而设计。

### 核心哲学：分层协同
- **公有事实走 Git**：远端主机利用高速海外骨干网秒级 `git clone`，零本地上行带宽消耗；
- **私有状态走影子**：自动捕获本地受 `.gitignore` 保护的真实配置（`.env*`、私钥、本地调试文件），通过带基线哈希的 CAS 原子投影到远端；
- **重型依赖走原生**：`node_modules` 与虚拟环境在远端 Linux 原生就地安装，彻底告别 Windows 与 Linux 跨平台二进制兼容性噩梦；
- **影子状态 CAS 同步**：`.gitshadow` 文件携带基线哈希并原子写入，远端同时修改时保留冲突副本而不静默覆盖；未提交代码默认留在本地，确需临时投影时显式追加 `--wip`。

### 注册与执行原理：Git 原生子命令发现 (Git Subcommand Discovery)
Git 天生支持原生子命令扩展：当在终端执行 `git <subcommand>` 时，Git 会自动从系统环境变量 `PATH` 中检索名为 `git-<subcommand>` 的可执行体（在 Windows 下优先匹配 `git-shadow.cmd`、`git-shadow.exe`、`git-shadow` 等）。
- 因此，无论是 `git shadow <command>` 还是 `git-shadow <command>` 均可完全等价无缝调用；
- **免安装热重载方案**：将简单包装脚本（设置 `PYTHONPATH` 指向工程目录并调用 `python -m git_shadow.cli`）放入任意系统 `PATH` 路径（如 `D:/Tool/DIY` 或 `~/.local/bin`），即可零污染系统 Python 环境，且修改源码即刻生效；
- **查看帮助避坑**：因 Git 官方机制会拦截 `--help` 转去寻找内置 HTML 手册并报错，查看帮助请统一使用 **`git shadow -h`** 或 **`git-shadow --help`**。

---

## 📄 License

[MIT License](LICENSE) © 2026 Epodak
