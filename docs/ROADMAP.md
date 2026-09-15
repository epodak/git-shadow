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
- [x] 本地 `.gitshadow` 单向 watcher
- [x] WIP 补丁改为显式 `--wip`
- [x] 项目级 VPS service-agent：Unix socket、状态、Lease 自毁与 SSH 客户端

## 1. P0：真实运行闭环

### 1.1 CLI 与安装

- [ ] 提供可靠的 `git-shadow`/`git shadow` wrapper 安装方式
- [ ] 明确控制端和 VPS 端的 Python 依赖边界
- [ ] `edge install` 失败时给出可恢复诊断
- [ ] 不依赖 `pip` 的 VPS 边缘执行器验收

### 1.2 CloudCLI 编排

- [x] 对 CloudCLI Project/Session API 建立可替换 HTTP 测试服务器
- [x] 验证 `session.ready` 早于 Git clone 完成
- [x] clone/后续步骤失败时广播 partial 状态；Session 创建失败保持明确失败
- [x] provider、project path、公开深链的契约校验

### 1.3 Git 工作区

- [ ] 公有仓库 HTTPS/SSH URL 的明确策略
- [ ] 空目录、CloudCLI 预先写入文件、已有 Git 工作区三种路径测试
- [ ] branch 不存在、非快进、脏工作区的安全失败语义

## 2. P0：VPS 常驻服务治理

- [x] 新增 VPS 常驻 `git-shadow service-agent`
- [x] `service load`：安装、注册项目、启动 Lease
- [x] `service status`：PID、状态、版本、Lease 剩余时间
- [x] `service unload`：优雅停止并清理 PID/Lease
- [x] Lease 超时自动自毁
- [x] 服务与一次性 SSH edge job 共享同一 TypedExecutionPlan
- [ ] 服务重启后能从 durable state 恢复，而不是重复覆盖

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
- [x] Git 追踪文件继续只通过 Git，不被 Shadow watcher 接管

## 4. P1：任务可靠性

- [ ] 区分 replay、retry、resume 三种语义
- [ ] 失败步骤可幂等重试
- [x] 常驻 service SSH 断线后自动重连并按 seq 去重/补齐事件
- [ ] CloudCLI Session 已创建但后续失败时广播明确的 partial state
- [x] 常驻 service 的重复提交、断线重连和幂等 job_id 测试
- [ ] 任务日志大小上限和清理策略

## 5. P1：本地体验与性能

- [ ] Linux inotify、macOS FSEvents、Windows ReadDirectoryChangesW 监听器
- [ ] 轮询作为兼容 fallback
- [ ] watcher 防抖、合并提交和失败退避
- [ ] 安静模式与可读进度模式
- [ ] 端到端首次启动耗时和重复启动耗时指标

## 6. P1：安全与运维

- [ ] Shadow 内容只存在于 SSH 内存管道和受控冲突目录
- [ ] 所有路径、归档、环境变量和 argv 输入测试
- [ ] CloudCLI token/API key 不进入 request、event、错误信息
- [ ] 远端工作区权限、边缘程序权限和状态目录权限收紧
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

## 当前优先顺序

```text
P0.1 真实 CloudCLI 测试边界
P0.2 VPS service load/unload/status（已完成基础闭环）
P0.3 Shadow 双向 pull/conflict
P1.1 retry/resume/断线重连
P1.2 原生文件 watcher
P1.3 安全与真实 VPS smoke test
P2   发布验收与文档冻结
```

## 完成定义

当且仅当以下条件同时满足，Roadmap 才算完成：

1. 新 VPS 只配置 SSH 后，可以被控制端一键安装/启动；
2. CloudCLI Session 在目录创建后立即可打开，Git/Shadow 准备异步继续；
3. Git 代码和 Shadow 文件拥有不同且可验证的同步语义；
4. 本地与 VPS 任意一端修改 Shadow 都不会静默丢失；
5. VPS 断线、重启、超时和重复任务都可恢复或明确失败；
6. 每个对外宣称的命令都有实现、测试和文档。
