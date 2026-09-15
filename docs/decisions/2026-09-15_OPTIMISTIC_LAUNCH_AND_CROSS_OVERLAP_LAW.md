# ADR — 乐观先行与时间交叉重叠律 (Optimistic Launch & Cross-Overlap Execution Law)

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: CLI 核心运行范式 + 异步并发投影引擎 + 人机交互响应时序
- **Canonical Owners**:
  - `OptimisticLaunchAxiom` (乐观先行公理 · 感知响应零等待)
  - `CrossOverlapExecution` (时间交叉重叠律 · 交互前台与同步后台并发交叠)
  - `UnifiedRunParadigm` (统一运行范式 · 统一收敛至 run 命令)

---

## 1. Context (背景与认知颠覆)

### 1.1 独立 `web` 命令的语义越界
在初代实现中，系统引入了 `git shadow web <host>` 作为顶级命令，但在底层却将目标硬编码绑定为 CloudCLI。这种设计存在严重的概念污染：
- **语义混淆**：`web` 是交互介质，而 `cloudcli` 是具体应用。如果远端未来运行其他 Web 工具（如 OpenCode Web、Jupyter、code-server），`web` 命令的命名便彻底名存实亡；
- **心智分裂**：终端 AI 用 `run` 启动，Web AI 却用 `web` 启动，给人类和外部 AI Agent 带来了额外的概念区分负担。
- **治理抉择**：**正式暂缓并废弃独立的 `web` 命令，将所有 AI 工具与工作台的唤起统一收敛至 `git shadow run <host> [agent]`。**

### 1.2 悲观串行阻塞的体验灾难
在过去的设计中，命令执行遵循传统的悲观瀑布流时序：
```text
[用户敲击命令] ──> [等待网络握手 3s] ──> [等待基线对齐 10s] ──> [等待影子传输 5s] ──> [唤起浏览器/终端]
```
这种串行阻塞导致用户在敲下回车后，必须死盯着黑框光标等待 15~30 秒，极易产生“系统是否卡死”的严重挫败感与注意力断裂。

---

## 2. Decision (架构决策与核心契约)

### 2.1 统一运行范式 (`UnifiedRunParadigm`)
- 取缔顶级 `web` 命令，统一以 **`git shadow run <host> [agent]`** 作为全局唯一运行入口：
  ```bash
  git shadow run <host>              # 智能列出远端已就绪工具菜单 (🌐 Web / 💻 终端)
  git shadow run <host> cloudcli     # 明确拉起 Web Remote 型 AI (CloudCLI)
  git shadow run <host> commandcode  # 明确拉起终端型 AI (Command Code)
  git shadow run <host> opencode     # 明确拉起终端型 AI (OpenCode)
  ```
- 系统内部根据 Agent 的元数据类型（`type: web` vs `type: terminal`）自动分流调度（弹本地浏览器会话 vs 接入远端 TTY Shell）。

---

### 2.2 乐观先行公理 (`OptimisticLaunchAxiom`)
> **核心法则**：人机交互通道的开启必须是**第一优先响应**，绝不允许被人眼不可见的后台文件传输所阻断。

- 当用户触发 Web 型应用（如 `run <host> cloudcli`）时：
  **0 秒内立即调用系统浏览器弹出 Web 工作台网址（`https://cli.daduiot.com`）！**
- 人类视觉聚焦、浏览器冷启动加载、页面渲染与认证鉴权需要 3~5 秒。这段时间正是最天然的“计算遮罩期”。

---

### 2.3 时间交叉重叠律 (`CrossOverlapExecution`)

```text
┌────────────────────────────────────────────────────────────────────────┐
│               git shadow run 时间交叉重叠时序 (Cross-Overlap)            │
├────────────────────────────────────────────────────────────────────────┤
│ [T = 0s]  用户回车触发命令                                               │
│                                                                        │
│   ├── 【前台主线程：乐观先行】(立即唤起，0 秒无阻断)                          │
│   │    - 浏览器秒开 https://cli.daduiot.com (或直接进入远端 Shell 终端)   │
│   │    - 人类视线进入界面、渲染 DOM、准备输入                             │
│   │                                                                    │
│   └── 【后台随行边缘任务】(时间与人类操作重叠)                            │
│        - 创建远端项目目录                                               │
│        - 立即创建 CloudCLI Project/Session                              │
│        - 返回 session.ready 并打开 /session/<id>                        │
│        - Session 已运行时继续 clone/init/checkout 与 Shadow CAS          │
│                                                                        │
│ [T = 2~3s] 用户可以开始输入；远端文件随后持续出现并被 Codex 感知。       │
└────────────────────────────────────────────────────────────────────────┘
```

- **并发实现模型**：
  - 前台先行负责 UI/会话窗口瞬时触达；
  - VPS 边缘任务先创建目录和 CloudCLI Session，再继续执行 Git/Shadow 准备；
  - 终端输出提供非阻塞的优雅进度吐息，彻底消灭用户心智中的“卡死感”。

---

## 3. Consequences (收益与演进)

1. **零等待感知**：敲完命令即刻见界面，人类体验从“枯燥干等 20 秒”质变为“秒开即用”；
2. **概念正交纯粹**：消除概念冗余，`run` 通吃终端型与 Web 型，`push/pull` 专心做数据流，架构坚如磐石；
3. **容错与鲁棒性飞跃**：即便后台影子同步遇到偶发重试，也不影响用户先进入 Web 查看既有任务与历史。
