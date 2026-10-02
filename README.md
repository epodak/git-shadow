# git-shadow

> Git 负责代码事实，Shadow 负责私有状态，远端原生环境负责运行。
>
> **Git clones the repo. Shadow overlays the secrets.**

git-shadow 用于把本地开发工作区快速投影到远端 Linux/macOS 主机，让 OpenCode、Claude Code、Codex、Aider、CloudCLI 等 AI Agent 在远端原生环境中工作，同时保持本地代码、私有配置和远端运行环境之间的职责边界。

[中文](#中文) · [English](#english) · [架构](#架构) · [快速开始](#快速开始) · [分支工作区](#分支工作区) · [远端能力自举](#远端能力自举)

---

# 中文

## 为什么需要 git-shadow

直接把本地开发环境搬到 VPS，通常会遇到几个矛盾：

- **SSHFS / Samba**：跨公网文件元数据访问延迟高，git status、ripgrep、依赖扫描容易变慢。
- **Rsync / 全量同步**：node_modules、.venv、构建缓存体积大，而且跨 Windows/macOS/Linux 复制二进制依赖经常不可用。
- **只用 Git clone**：.env、私钥、本地配置、未追踪调试文件不会进入 Git，远端 Agent 拿不到完整运行上下文。
- **直接让 AI 操作本地机器**：长时间任务会占住本地电脑，也扩大了误操作影响范围。

git-shadow 不试图用一种同步机制解决所有问题，而是把工作区拆成不同所有权层。

## 核心模型

| 层 | 内容 | 同步方式 | 原则 |
| --- | --- | --- | --- |
| Git Lane | 已提交代码、分支、提交历史 | Git clone/fetch/pull/push | Git 是代码事实源 |
| Shadow Lane | .env、私钥、本地配置、调试资产 | .gitshadow + CAS + SSH | 只同步显式白名单 |
| Native Runtime Lane | node_modules、venv、编译缓存 | 在远端原生安装 | 不跨平台搬运重型依赖 |
| WIP Lane | 未提交 tracked 修改 | --wip 一次性 patch | 默认不自动传输 |

最重要的边界是：

~~~text
GitTracked != Shadow != Runtime != WIP
~~~

Shadow 不是第二套 Git，也不是 rsync 替代品。

## 架构

为避免不同 GitHub/Markdown 环境下图表渲染差异，架构改为纯文本表示：

~~~text
本地工作区
│
├─ Git tracked files
│      │
│      └──────────────→ Git Remote
│                           │
│                           └─ clone/fetch → 远端分支工作区
│
├─ .gitshadow 白名单
│      │
│      └─ Manifest + CAS + SSH ───────────→ Shadow Overlay
│
├─ 未提交 tracked 修改
│      │
│      └─ 默认不传；显式 --wip 才发送
│
└─ node_modules / venv / build cache
       │
       └─ 不同步，在远端原生生成

远端工作区
│
├─ Git baseline
├─ Shadow private state
├─ Native dependencies
└─ AI Agent / CloudCLI
~~~

## 分支工作区

Git 项目的远端工作区身份不是单纯的仓库名，而是：

~~~text
WorkspaceIdentity = Repository + Branch
~~~

例如：

~~~text
repo:   AI
branch: foo/bar

→ ~/wkspace/AI/foo/bar
~~~

另一个分支：

~~~text
repo:   AI
branch: main

→ ~/wkspace/AI/main
~~~

这样同一个本地仓库切换分支时，不会把远端另一个分支的工作区直接 checkout 掉，也不会让不同分支错误复用同一个 Shadow CAS 基线。

首次创建 Git 工作区时，git-shadow 使用单分支 clone/fetch 语义：

~~~bash
git clone --branch foo/bar --single-branch ...
~~~

因此目录路由与 Git 内容模型保持一致。

普通非 Git 文件夹没有真实分支身份，因此仍然使用：

~~~text
~/wkspace/<project>
~~~

而不会人为追加 /main。

## Shadow 契约

Shadow 的唯一正向授权入口是项目根目录的 .gitshadow。

示例：

~~~gitignore
# .gitshadow
.env*
*.secret
*.key
*.pem
config.local.*
_dev_log/
*.local.md
~~~

只有匹配这些规则的文件才有资格进入 Shadow Lane。

如果还需要进一步排除某些文件，可以增加 .shadowignore：

~~~gitignore
# .shadowignore
large_dataset/
*.mp4
test_dump.sql
~~~

可以理解为：

~~~text
ShadowCandidates
    ∩ .gitshadow allowlist
    - .shadowignore denylist
    = Actual Shadow Projection
~~~

## git-shadow 自身状态目录

git-shadow 现在只使用一个默认根目录：

~~~text
~/.git-shadow/
├── bindings.json
├── shadows/
├── conflicts/
├── daemon/
├── tmp/
│   └── tests/
├── bin/
├── apps/
├── runtime/
├── logs/
├── runs/
└── services/
~~~

本地控制端通常只会使用 binding、Shadow 状态、daemon 和测试临时目录；远端 VPS 还会使用 bin、apps、runtime、runs、services 等运行时目录。

以前的 `~/.local/state/git-shadow` 和 `~/.local/share/git-shadow` 只作为升级兼容的迁移来源，新状态不再默认写入那里。

特别是测试：所有 `git_shadow_*_xxxxxxxx` 临时工作区现在都创建在：

~~~text
~/.git-shadow/tmp/tests/
~~~

即使 Windows 因文件句柄尚未释放导致某次测试清理失败，也不会再把大量临时文件夹直接留在用户 Home 根目录。

如果确实需要改根目录，可以设置：

~~~bash
GIT_SHADOW_HOME=/path/to/state
~~~

详见：[Unified git-shadow Home Law](docs/decisions/2026-09-27_UNIFIED_GIT_SHADOW_HOME_LAW.md)。

## 安装

要求：

- Python 3.8+
- 本地可使用 Git
- 至少存在一个可 SSH 的远端主机

开发安装：

~~~bash
git clone https://github.com/epodak/git-shadow.git
cd git-shadow
pip install -e .
~~~

也可以不安装 Python 包，只生成轻量 wrapper：

~~~bash
python -m git_shadow.cli install ~/.local/bin
~~~

之后两种调用方式等价：

~~~bash
git shadow <command>
git-shadow <command>
~~~

查看帮助推荐：

~~~bash
git shadow -h
# 或
git-shadow --help
~~~

注意：git shadow --help 可能被 Git 自身的 manpage 机制截获。

## 快速开始

### 1. 查看当前工作区会投影什么

~~~bash
git shadow diff
~~~

### 2. 投影并启动远端 Agent

~~~bash
git shadow run aws-micro opencode
~~~

CloudCLI：

~~~bash
git shadow run aws-micro cloudcli --provider codex
~~~

执行过程会把当前 Git 分支路由到自己的远端目录，例如：

~~~text
AI@foo/bar
→ ~/wkspace/AI/foo/bar
~~~

### 3. 只同步，不进入 Agent

~~~bash
git shadow push aws-micro
~~~

### 4. 投影并进入远端终端

~~~bash
git shadow up aws-micro
~~~

### 5. 拉取 AI 已提交的 Git 修改

~~~bash
git shadow pull aws-micro
~~~

连同 Shadow 变化一起检查：

~~~bash
git shadow pull aws-micro --with-shadows
~~~

### 6. 显式投影未提交 tracked 修改

默认不会同步本地未提交代码。需要时：

~~~bash
git shadow run aws-micro opencode --wip
~~~

## 远端能力自举

如果远端已经能 SSH，但没有 CloudCLI，不需要再手工 SSH 登录安装。

查看状态：

~~~bash
git shadow remote status aws-micro cloudcli
~~~

查看准备计划：

~~~bash
git shadow remote plan aws-micro cloudcli
~~~

直接通过现有 SSH 安装、配置并启动：

~~~bash
git shadow remote ensure aws-micro cloudcli
~~~

显式运行 CloudCLI 时也会自动处理可修复状态：

~~~bash
git shadow run aws-micro cloudcli
~~~

如果希望只探测、不要自动安装：

~~~bash
git shadow run aws-micro cloudcli --no-bootstrap
~~~

当前 bootstrap 原则：

- 支持远端 Linux / macOS；
- 不要求 sudo；
- 不修改项目目录；
- 不修改 .bashrc / .zshrc；
- 运行时资产放在 ~/.git-shadow；
- Linux 优先 systemd --user；
- macOS 优先 launchd；
- 不可用时回退到 nohup；
- CloudCLI 失败不会阻断 Git / Shadow 同步。

详见：[Remote Capability Bootstrap](docs/REMOTE_CAPABILITY_BOOTSTRAP.md)。

## Web 接入路径：Tailscale + Cloudflare 优势互补

CloudCLI 本体始终保持本地监听：

~~~text
127.0.0.1:3001
~~~

git-shadow 不再假设某个固定公网域名，而是增加独立的 Access Path 层：

~~~text
本地浏览器
   │
   ├─ Tailscale Serve ──→ VPS:127.0.0.1:3001
   │      私网、Tailnet ACL、无需公开服务端口
   │
   └─ 配置的公网 URL ──→ Cloudflare Tunnel 等 ──→ VPS:127.0.0.1:3001
          自定义域名、跨 Tailnet 访问、Cloudflare 安全/边缘能力
~~~

默认策略是 `--access auto`：

~~~text
同一 Tailnet + direct       → 优先 Tailscale
同一 Tailnet + peer-relay   → 优先 Tailscale
Tailscale 只能 DERP
  + 已配置公网 URL           → 优先公网 URL
Tailscale 只能 DERP
  + 未配置公网 URL           → 使用 Tailscale
Tailscale 不可用
  + 已配置公网 URL           → 使用公网 URL
两者都不可用                 → Web 降级；Git/Shadow 继续同步
~~~

这不是“永远认为某一路更快”，而是根据实际连接状态选择。

查看本地与 VPS 是否已处于可互访 Tailnet：

~~~bash
git shadow network status aws-micro
~~~

显式把 Linux VPS 纳入 Tailnet：

~~~bash
export GIT_SHADOW_TAILSCALE_AUTH_KEY="tskey-auth-..."
git shadow network ensure aws-micro tailscale
~~~

Tailscale 入网属于带权限的主机身份变更，因此普通 `run/push/pull` **不会偷偷安装或登录 Tailscale**。自动安装目前只支持 Linux VPS，并要求 passwordless sudo。Auth Key 属于敏感凭据，不应提交到仓库；生产环境建议使用短期、受限或带 tag 的 Auth Key，并从秘密管理器注入。

如果已经有 Cloudflare Tunnel，只需要把公网入口配置给 git-shadow：

~~~bash
export GIT_SHADOW_CLOUDFLARE_URL="https://cli.example.com"
git shadow run aws-micro cloudcli --access auto
~~~

也可以单次显式指定：

~~~bash
git shadow run aws-micro cloudcli \
  --cloudcli-public-url https://cli.example.com \
  --access auto
~~~

强制走某一路：

~~~bash
git shadow run aws-micro cloudcli --access tailscale
git shadow run aws-micro cloudcli --access public \
  --cloudcli-public-url https://cli.example.com
~~~

因此推荐的实际部署不是“Cloudflare 和 Tailscale 二选一”，而是：

~~~text
Tailscale = 默认私网控制面 / 内部 Web 入口
Cloudflare Tunnel = 公网域名入口 / 非 Tailnet 设备 / 备用路径
~~~

详见：[Adaptive Web Access Path Law](docs/decisions/2026-09-27_ADAPTIVE_WEB_ACCESS_PATH_LAW.md)。

## Edge Executor 与常驻 Service

安装/更新远端 standalone edge executor：

~~~bash
git shadow edge install aws-micro
~~~

查看任务：

~~~bash
git shadow edge status aws-micro <job-id>
git shadow edge resume aws-micro <job-id>
~~~

使用项目级常驻 service：

~~~bash
git shadow service aws-micro load
git shadow run aws-micro cloudcli --provider codex --service --watch
git shadow service aws-micro status
git shadow service aws-micro unload
~~~

VPS 不需要安装完整 git-shadow Python 包。控制端会上传 standalone executor。

## 持续开发模式

推荐的个人开发闭环：

~~~bash
git shadow run aws-micro cloudcli --provider codex --service --watch
~~~

--watch 的职责：

- 本地 Shadow 改动 → CAS push；
- 远端 Shadow 改动 → CAS pull；
- 本地 Git 工作树干净时 → 定期 fetch + fast-forward；
- 本地有未提交修改时 → 自动暂停 Git 拉取，绝不覆盖本地工作；
- 本地切换 Git 分支时 → 当前 watcher 立即停止，防止把新分支 Shadow 推到旧分支工作区。

远端 Agent 应通过 Git commit + push 交付 tracked 代码，本地再正常收取提交。切换本地分支后，应在新分支重新启动 watch/run --watch，让新的 Repository + Branch identity 接管同步。

## Git 远端与鉴权

如果仓库存在 origin，git-shadow 直接沿用它：

~~~text
https://github.com/org/repo.git
git@github.com:org/repo.git
~~~

私有仓库可以先同步 Git/SSH 鉴权：

~~~bash
git shadow auth sync aws-micro
~~~

没有 origin 的 Git 项目会进入显式 P2P archive lane，不会猜测某个 GitHub 仓库。

## 安全与一致性原则

git-shadow 当前遵循这些不变量：

1. .gitshadow 是 Shadow 正向授权契约。
2. Git 分支之间使用独立远端 workspace。
3. Shadow acknowledgement 同时按本地项目、远端 host、远端 workspace 隔离。
4. 未提交 tracked 文件默认不离开本机。
5. Shadow CAS 冲突不能静默覆盖另一端修改。
6. Runtime/依赖不通过 Shadow 搬运。
7. CloudCLI 不可用不等于整个远端 host 不可用。
8. SSH 不可用时，不尝试把“连接失败”误判为“应用缺失”并自动安装。
9. CloudCLI 默认只监听 localhost，Web 暴露由独立 Access Path 层负责。
10. 个人公网域名不能成为运行时代码的硬编码默认值。
11. Tailscale 入网是显式的主机身份变更，普通同步命令不得隐式执行 sudo/登录。

## 常用命令

~~~text
git shadow diff
git shadow probe <host>
git shadow push <host>
git shadow pull <host>
git shadow up <host>
git shadow run <host> [agent]

git shadow auth sync <host>

git shadow remote status <host> cloudcli
git shadow remote plan <host> cloudcli
git shadow remote ensure <host> cloudcli

git shadow network status <host>
git shadow network ensure <host> tailscale

git shadow edge install <host>
git shadow edge status <host> <job-id>
git shadow edge resume <host> <job-id>

git shadow service <host> load
git shadow service <host> status
git shadow service <host> unload
~~~

## 更多设计文档

- [Unified git-shadow Home Law](docs/decisions/2026-09-27_UNIFIED_GIT_SHADOW_HOME_LAW.md)
- [Remote Capability Bootstrap](docs/REMOTE_CAPABILITY_BOOTSTRAP.md)
- [Adaptive Web Access Path Law](docs/decisions/2026-09-27_ADAPTIVE_WEB_ACCESS_PATH_LAW.md)
- [Branch-Scoped Workspace Routing Law](docs/decisions/2026-09-27_BRANCH_SCOPED_WORKSPACE_ROUTING_LAW.md)
- [Roadmap](docs/ROADMAP.md)
- [Zeron Lessons: Agent Control Plane & Persistent Remote Computer](docs/ZERON_LESSONS_AND_AGENT_CONTROL_PLANE.md)
- [Layered Sync Ownership Law](docs/decisions/2026-09-15_LAYERED_SYNC_OWNERSHIP_LAW.md)
- [Remote Edge Executor JSONL Law](docs/decisions/2026-09-15_REMOTE_EDGE_EXECUTOR_JSONL_LAW.md)
- [Local Silence and Zero Git Touch Law](docs/decisions/2026-09-25_LOCAL_SILENCE_AND_ZERO_GIT_TOUCH_LAW.md)

---

# English

## Overview

git-shadow projects a local development workspace onto a remote Linux/macOS host for AI coding agents such as OpenCode, Claude Code, Codex, Aider, and CloudCLI.

It does not treat the workspace as one monolithic sync tree. Ownership is split into independent lanes:

| Lane | Data | Transport |
| --- | --- | --- |
| Git | committed code and history | clone / fetch / pull / push |
| Shadow | explicitly allowed private local state | .gitshadow + CAS + SSH |
| Native Runtime | dependencies and build caches | installed on the remote host |
| WIP | uncommitted tracked changes | opt-in --wip patch |

## Branch-scoped workspaces

A Git workspace is identified by repository **and branch**.

~~~text
repository: AI
branch:     foo/bar

remote:
~/wkspace/AI/foo/bar
~~~

A different branch receives a different remote workspace:

~~~text
AI@main    → ~/wkspace/AI/main
AI@foo/bar → ~/wkspace/AI/foo/bar
~~~

New remote Git workspaces use single-branch clone/fetch semantics, so switching a local checkout does not mutate another branch's remote workspace.

Plain non-Git folders keep the simpler route:

~~~text
~/wkspace/<project>
~~~

## Architecture

~~~text
Local
├─ Git tracked files ─────────────→ Git Remote ─────→ remote Git workspace
├─ .gitshadow private files ─────→ CAS over SSH ───→ Shadow overlay
├─ uncommitted tracked changes ──→ local by default; --wip is explicit
└─ dependencies/build cache ─────→ not copied

Remote
├─ branch-scoped Git baseline
├─ Shadow private state
├─ native dependencies
└─ AI Agent / CloudCLI
~~~

## Application state directory

git-shadow uses one default application root:

~~~text
~/.git-shadow/
├── bindings.json
├── shadows/
├── conflicts/
├── daemon/
├── tmp/tests/
├── bin/
├── apps/
├── runtime/
├── logs/
├── runs/
└── services/
~~~

Legacy `~/.local/state/git-shadow` and `~/.local/share/git-shadow` locations are migration/read-compatibility sources only. New state is written under `~/.git-shadow`.

All test workspaces also live below `~/.git-shadow/tmp/tests/`, so failed Windows cleanup cannot litter the visible home directory with `git_shadow_*_xxxxxxxx` folders.

Override the root with `GIT_SHADOW_HOME` when needed.

## Installation

~~~bash
git clone https://github.com/epodak/git-shadow.git
cd git-shadow
pip install -e .
~~~

Or install only the wrapper:

~~~bash
python -m git_shadow.cli install ~/.local/bin
~~~

Then use either:

~~~bash
git shadow -h
git-shadow --help
~~~

## Quick start

~~~bash
git shadow diff
git shadow run aws-micro opencode
git shadow run aws-micro cloudcli --provider codex
git shadow push aws-micro
git shadow up aws-micro
git shadow pull aws-micro
~~~

For explicit WIP projection:

~~~bash
git shadow run aws-micro opencode --wip
~~~

## Shadow contract

.gitshadow is the positive allowlist. Files are not sent merely because they appear in .gitignore.

~~~gitignore
.env*
*.secret
*.key
*.pem
config.local.*
~~~

.shadowignore can apply additional exclusions.

## Remote CloudCLI bootstrap

No second manual SSH session is required when the host is already reachable:

~~~bash
git shadow remote status aws-micro cloudcli
git shadow remote plan aws-micro cloudcli
git shadow remote ensure aws-micro cloudcli
~~~

An explicit CloudCLI run automatically attempts repairable bootstrap unless disabled with --no-bootstrap.

## Adaptive Web access

CloudCLI remains bound to localhost. Browser access is resolved separately:

~~~text
CloudCLI 127.0.0.1:3001
├─ Tailscale Serve   → private tailnet HTTPS
└─ configured public URL → e.g. Cloudflare Tunnel
~~~

The default `--access auto` policy prefers a direct or peer-relay Tailscale path. When Tailscale is DERP-only and a public URL is configured, the public path is preferred. If neither Web path is available, Git/Shadow synchronization continues in sync-only mode.

~~~bash
git shadow network status aws-micro
git shadow network ensure aws-micro tailscale

GIT_SHADOW_CLOUDFLARE_URL=https://cli.example.com \
  git shadow run aws-micro cloudcli --access auto
~~~

Tailscale enrollment is explicit and privileged; ordinary run/push/pull commands never silently install or authenticate Tailscale.

## Continuous workflow

~~~bash
git shadow run aws-micro cloudcli --provider codex --service --watch
~~~

The watcher synchronizes Shadow state bidirectionally through CAS and only fast-forwards Git when the local worktree is clean. If the local checkout changes branch, the watcher stops instead of continuing to write Shadow state into the previous branch workspace.

## Design invariants

- Git branches map to independent remote workspaces.
- .gitshadow is the Shadow allowlist.
- tracked WIP remains local unless explicitly requested.
- Shadow conflicts never silently overwrite the opposite side.
- heavy runtime dependencies stay native to the remote host.
- CloudCLI failure degrades to sync-only instead of disabling the host.
- bootstrap never treats an SSH connection failure as an application installation problem.
- CloudCLI stays localhost-bound; browser exposure belongs to the Access Path layer.
- no personal public hostname is a runtime default.
- privileged Tailscale enrollment is explicit.

See [docs](docs/) for protocol, security, bootstrap, and roadmap details.

---

## License

[MIT License](LICENSE) © 2026 Epodak
