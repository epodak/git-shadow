# ADR — 智能体技能驱动分发与自举律 (Skill-Driven Distribution & Agent Self-Bootstrap Law)

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: `.agents/skills/` 规范 + 跨端 AI 认知分发 + 产品能力自举机制
- **Canonical Owners**:
  - `SkillDrivenDistribution` (技能驱动的产品分发规范 · AI 时代的第一公民文档)
  - `AgentSelfBootstrapping` (跨端智能体自举与自生镜像契约)
  - `DualPurposeCheatsheet` (创作过程真源沉淀与开箱即用一举两得律)

---

## 1. Context (背景与范式升级)

在传统的 CLI 软件设计中，文档与使用教程主要面向人类读者：
- 静态 `README.md` 往往篇幅过长，难以在 AI Agent 有限的上下文窗口中精准命中；
- 命令行 `--help` 只有在报错或显式询问时才被动触发，AI 无法先验掌握工具的边界与禁忌；
- 跨端协同下（本地 Windows + 远端 Linux VPS），两端的 AI 智能体（如本地 Antigravity 与云端 CloudCLI / OpenCode）缺乏统一的行为指引，极易编写出带有平台污染（如 MSYS2 盘符转换、SSH RemoteCommand 冲突）的错误脚本。

因此，**将产品核心能力直接封装为标准智能体技能 (`.agents/skills/git-shadow/SKILL.md`)** 成为必由之路：
1. **开箱即知（Zero-Prompt Usability）**：外部用户或新 AI Agent 接手项目时，系统自动挂载该技能，无需人类教导即刻掌握最佳调用实践；
2. **一举两得（Dual-Purpose Living Spec）**：在产品自身迭代创作时，该技能是最高保真的操作 Cheatsheet 与验收规范，防止功能散落与认知腐化；
3. **闭环自举（Self-Bootstrapping）**：工具不仅能被 AI 操作，还能自我注入、自我同步至跨端环境，让远端云上 AI 具备完全同等的控制力。

---

## 2. Decision (架构决策与核心契约)

### 2.1 技能第一分发契约 (`SkillDrivenDistribution`)
- 本项目在根目录设立标准工作区技能目录：
  ```text
  .agents/skills/git-shadow/
  └── SKILL.md
  ```
- **契约规范**：
  1. **元数据声明**：文件头部严格包含 YAML frontmatter（`name: git-shadow`，清晰描述其跨端代码与影子投射、Web 优先与服务治理定位）；
  2. **防错警示内嵌**：显式注入跨平台陷阱指引（MSYS2 盘符清洗、SSH 非交互参数 `-o RemoteCommand=none -o RequestTTY=no`、Diff-First 密钥保护）；
  3. **标准场景速查表**：为常见场景（首次配对、启动 Web、远端服务治理、排错诊断）提供一行直达的命令模版。

---

### 2.2 创作沉淀与交付一举两得律 (`DualPurposeCheatsheet`)
- **单一真源原则**：当 `git-shadow` 新增命令（如 `service [load|unload|status]` 或 `web --watch`）时，**必须且只能同步更新两处**：
  - 架构真源：`docs/decisions/`（解释为什么这样设计）；
  - 技能速查：`.agents/skills/git-shadow/SKILL.md`（展示人类与 AI 怎么用）。
- **消除散落笔记**：杜绝在根目录新建临时使用说明，以 `.agents/skills/` 作为活的执行契约。

---

### 2.3 跨端自举与镜像注入契约 (`AgentSelfBootstrapping`)

```text
               ┌────────────────────────────────────────────────────────┐
               │         git-shadow 跨端技能自举闭环 (Self-Bootstrap)       │
               └────────────────────────────────────────────────────────┘
                                            │
                       [本地仓库] .agents/skills/git-shadow/
                                            │
               ┌────────────────────────────┴───────────────────────────┐
               ▼                                                        ▼
    【自生注入 (Self-Inject)】                               【影子投影 (Shadow-Push)】
    git shadow skill init                                   git shadow web / push
    向任意新工程自动注入技能规范                                同步至远端 Linux VPS 工作区
               │                                                        │
               ▼                                                        ▼
    本地任何新 Agent 即开即用                               远端 CloudCLI / OpenCode
    (Claude Code / Cursor / AGY)                            直接学会操作远端 git shadow
```

1. **项目自生注入**：
   - 提供 `git shadow skill init` 命令（或项目脚手架），可将预编译的 `SKILL.md` 模板一键注入到任意本地 Git 仓库的 `.agents/skills/git-shadow/` 中，让每一个使用 `git-shadow` 的项目天生具备 AI 理解力。
2. **跨端镜像流转**：
   - 根目录的 `.gitshadow` 允许同步 `.agents/` 技能文件（或由 Git 本身同步）；
   - 远端 Linux VPS 接收到 `.agents/skills/git-shadow/SKILL.md` 后，远端运行的 AI Agent（如 CloudCLI 终端、OpenCode 等）也能无缝识别此技能，从而自主执行 `git shadow pull` 或 `git shadow service status`，实现真正的双端智能体自举。

---

## 3. Consequences (收益与演进)

1. **零学习成本**：人类开发者只需让 AI “帮我把代码推到 aws 并打开 web”，AI 会自动根据 skill 精准调用 `git shadow web aws`，无需人类背诵参数；
2. **防腐防衰老**：开发新功能与维护技能文档合二为一，文档永远与最新实现同频；
3. **生态可自举**：从一个工具演变为一套可被其他项目轻松吸收的 AI 协作标准件。
