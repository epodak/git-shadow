# git-shadow Roadmap

> 目标：把当前 MVP 收敛为可在真实 VPS 上长期运行、可恢复、可双向协同的跨端开发闭环。
>
> 工程节奏：每个功能先写测试，再实现，再独立提交；每个阶段完成后推送到 `origin/main`。

## 0. 已完成基线

- [x] GitHub 公有仓库、`main` 分支和基础 CLI
- [x] 干净 SSH + JSONL 边缘任务协议
- [x] VPS 单文件边缘执行器、事件日志、Lease 取消、状态和重放
- [x] `workspace.create → cloudcli.session → workspace.prepare → shadow.sync`
- [x] Git 代码层、Shadow CAS 层、Native Runtime 层分离
- [x] 本地 `.gitshadow` 双向 watcher（push + pull）
- [x] WIP 补丁改为显式 `--wip`
- [x] 项目级 VPS service-agent：Unix socket、状态、Lease 自毁与 SSH 客户端

## 当前执行目标：个人闭环（优先于最终产品）

这一阶段不做桌面安装包、Managed VPS、IDE 扩展或多用户商业化。唯一目标是让项目作者自己的
“本地影子开发 → 远程 VPS CloudCLI Web Remote → 本地持续收割”稳定可用：

```text
本地 Git 工作区
  ├─ 已提交代码 ── GitHub ── VPS Git 工作区 ── 远程 Agent commit/push
  └─ .gitshadow ── Shadow Manifest/CAS 双向同步 ── VPS 私有工作区
                                  ↓
                         CloudCLI Web Remote Session
                                  ↓
                         本地 watcher 持续接收结果
```

个人闭环的明确语义：

- 初次启动使用 `git shadow run <host> cloudcli --provider <provider> --service --watch`，浏览器打开本次准确 Session；
- `.gitshadow` 文件由本地 watcher 自动 push，远端新建或修改的 Shadow 文件自动 pull；CAS 冲突保留冲突副本，绝不覆盖；
- Git 追踪代码仍只走 Git。watcher 定期 `fetch + merge --ff-only` 拉回远端 Agent 已 push 的提交；本地有未提交改动时自动拉取暂停并提示；
- 本地 watcher 进程必须保持运行；退出后 VPS 服务依靠 Lease 自动回收，下一次启动可恢复；
- “完全跑通”的验收必须包含真实 VPS、真实 CloudCLI、真实 GitHub 和三种 Shadow 流程，而不只是假服务器测试。

个人闭环的平台边界也必须明确：Windows、Linux、macOS 都可以作为本地控制端；当前远端执行端只验收
Linux VPS。这里的“跨平台”指本地控制端跨平台，不代表 Windows/Linux/macOS 之间任意互为远端执行端。
远端 macOS/Windows 适配属于未来独立工作，不纳入本轮个人闭环。

当前只按这个个人闭环排任务；第 8 节的独立程序、Snapshot Lane、Agent Adapter、Managed VPS 均暂缓，不作为当前开发阻塞。

## 1. P0：真实运行闭环

### 1.1 CLI 与安装

- [x] 提供可靠的 `git-shadow`/`git shadow` wrapper 安装方式
- [x] 明确控制端和 VPS 端的 Python 依赖边界
- [x] `edge install` 失败时给出可恢复诊断
- [x] 不依赖 `pip` 的 VPS 边缘执行器验收（standalone agent 协议测试）

### 1.2 CloudCLI 编排

- [x] 对 CloudCLI Project/Session API 建立可替换 HTTP 测试服务器
- [x] 验证 `session.ready` 早于 Git clone 完成
- [x] clone/后续步骤失败时广播 partial 状态；Session 创建失败保持明确失败
- [x] provider、project path、公开深链的契约校验
- [x] 真实 `aws-us` VPS 部署 CloudCLI 1.37.3，并通过 SSH 转发完成注册、登录、受保护 API 与 `git shadow run` smoke

### 1.3 Git 工作区

- [x] 公有仓库 HTTPS/SSH URL 的明确策略
- [x] 空目录、CloudCLI 预先写入文件、已有 Git 工作区核心路径测试
- [x] Git 可选的普通文件夹可创建 CloudCLI Session、远端空工作区和 Shadow lane
- [x] branch 不存在、已有脏工作区的安全失败语义；非快进由 Git 原生 pull 明确失败

## 2. P0：VPS 常驻服务治理

- [x] 新增 VPS 常驻 `git-shadow service-agent`
- [x] `service load`：安装、注册项目、启动 Lease
- [x] `service status`：PID、状态、版本、Lease 剩余时间
- [x] `service unload`：优雅停止并清理 PID/Lease
- [x] Lease 超时自动自毁
- [x] 服务与一次性 SSH edge job 共享同一 TypedExecutionPlan
- [x] 服务重启后重放 terminal durable state；未完成任务标记 `interrupted`，不重复覆盖

> 服务端是可恢复的任务承载/执行端，不是本地工作区的磁盘 watcher。要保持双向 Shadow
> 静默同步，控制端仍需运行 `run ... --watch --service`；本地 watcher 负责发现变化，
> service-agent 负责在 VPS 上执行 CAS、重放事件和承载断线后的任务状态。

## 3. P0：Shadow 双向同步

- [x] 新增 `shadow.pull` 结构化动作，并按 `.gitshadow` 规则发现远端新文件
- [x] VPS 只返回变更文件的 hash 和受保护内容，不把密钥写入事件日志
- [x] 本地在未修改时安全接收远端变更
- [x] 本地和 VPS 同时修改时生成两端冲突副本，绝不静默覆盖
- [x] 删除、空文件、重命名和目录层级的核心路径测试
- [x] 本地 watcher 同时处理已登记 Shadow 的 push lane 与 pull lane
- [x] Git 追踪文件继续只通过 Git，不被 Shadow watcher 接管；watch 模式可安全执行 Git ff-only 自动拉取

## 4. P1：任务可靠性

- [x] 区分 replay、retry、resume 三种语义
- [x] 标记为可重试的准备步骤可有限幂等重试；CloudCLI/exec/CAS 冲突默认不重试
- [x] 常驻 service SSH 断线后自动重连并按 seq 去重/补齐事件
- [x] CloudCLI Session 已创建但后续失败时广播明确的 partial state
- [x] 常驻 service 的重复提交、断线重连和幂等 job_id 测试
- [x] 任务日志大小上限和清理策略

## 5. P1：本地体验与性能

- [x] Linux inotify 监听器
- [ ] macOS FSEvents、Windows ReadDirectoryChangesW 监听器
- [x] 轮询作为兼容 fallback
- [x] watcher 防抖、合并提交和失败退避
- [ ] 安静模式与可读进度模式
- [ ] 端到端首次启动耗时和重复启动耗时指标

## 6. P1：安全与运维

- [ ] Shadow 内容只存在于 SSH 内存管道和受控冲突目录
- [ ] 所有路径、归档、环境变量和 argv 输入测试
- [x] CloudCLI token/API key 不进入 request、event、错误信息
- [x] 边缘程序和状态目录权限收紧；远端工作区权限保留真实 VPS smoke 验收
- [ ] VPS 磁盘、内存、Lease、Job 数量的治理策略
- [ ] `probe` 输出与实际服务能力一致

## 7. P2：交付验收

- [ ] 新 VPS 仅有 SSH 登录时的一键验收脚本
- [ ] 公有 GitHub 仓库完整流程
- [ ] 私有 GitHub 仓库完整流程
- [ ] CloudCLI Codex Session 完整流程
- [ ] 本地改 Shadow、远端改 Shadow、并发冲突三套流程
- [ ] 本地 commit/push 与远端 AI commit/pull 流程
- [ ] 文档、Skill、ADR、CLI help、实现和测试完全一致

## 8. P1/P2：独立程序、本地首启与托管 VPS

产品方向已校正为：**独立的本地工作区启动器是主入口，GitHub 是可选加速器，IDE 扩展是可选集成；Managed VPS 是可选托管数据面。** 详细决策见 [本地工作区启动器、Agent 适配与托管 VPS 产品路线 ADR](decisions/2026-09-15_IDE_EXTENSION_AND_MANAGED_VPS_ROADMAP.md)。

### 8.1 独立程序与无 Python 分发

- [ ] 定义桌面启动器与 Core Client 的稳定本地进程协议
- [ ] 提供 Windows/macOS/Linux standalone client，内置运行时，不要求预装 Python
- [ ] 实现“选择文件夹 → 选择 Agent → 开始远程项目”的首启流程
- [ ] 安装包覆盖升级、卸载、停止、销毁、重试和恢复
- [ ] 保留 `pip install -e .` 与源码 wrapper 作为贡献者/高级用户模式

### 8.2 GitHub 非必需的 Workspace Snapshot Lane

- [ ] 支持普通本地文件夹，不要求 Git 初始化或 GitHub 账号
- [ ] 设计 manifest/hash/加密分块快照协议，禁止无边界全量 rsync
- [ ] 排除依赖、缓存、构建产物和未经允许的私密文件
- [ ] 远端修改以 patch/diff 返回，本地确认后应用
- [ ] 覆盖中断、重试、冲突、取消和销毁测试
- [ ] 支持随时初始化 Git 或导出到 GitHub

### 8.3 Agent 适配器注册表

- [ ] 抽象 `AgentAdapterRegistry`：detect/prepare/launch/health/auth boundary
- [ ] 将 CloudCLI、Codex、Claude Code、Command Code、OpenCode 纳入统一适配描述
- [ ] 验证 `agy`/`antigravity` 的启动形态、认证边界和原生安装准备
- [ ] 建立 Agent 版本兼容矩阵和失败诊断

### 8.4 Managed VPS MVP

- [ ] 单云厂商、单 Linux 镜像、有限规格的工作区创建/暂停/销毁
- [ ] 复用现有 Edge/Service Agent、TypedExecutionPlan、事件重放、Shadow CAS 和 Lease
- [ ] 增加租户隔离、资源配额、闲置回收、审计和成本上限
- [ ] 独立程序、CLI 与托管控制面共用 API；自有 VPS/SSH 模式保持能力平权

### 8.5 可选生态入口

- [ ] VS Code 等 IDE 扩展作为可选控制面，而非产品准入条件
- [ ] Web 控制台用于项目列表、用量、账单和远端工作区管理
- [ ] 多地域/多云、团队权限、共享工作区和 Agent 适配器扩展机制

## 当前优先顺序

```text
P0.1 真实 CloudCLI + GitHub + VPS 个人闭环 smoke test
P0.2 Shadow 双向 push/pull/conflict 个人验收
P0.3 Git 自动 ff-only pull 与本地脏工作区保护
P0.4 watcher/service Lease 断线、恢复、卸载验收
P1   安全、资源、文档和 CLI 一致性收口
暂缓  独立程序、Workspace Snapshot、Agent Adapter、Managed VPS、IDE/Web 生态
```

## 完成定义

当且仅当以下条件同时满足，Roadmap 才算完成：

1. 新 VPS 只配置 SSH 后，可以被控制端一键安装/启动；
2. CloudCLI Session 在目录创建后立即可打开，Git/Shadow 准备异步继续；
3. Git 代码和 Shadow 文件拥有不同且可验证的同步语义；
4. 本地与 VPS 任意一端修改 Shadow 都不会静默丢失；
5. VPS 断线、重启、超时和重复任务都可恢复或明确失败；
6. 每个对外宣称的命令都有实现、测试和文档。
