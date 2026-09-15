# ADR — .gitshadow 声明式影子契约律 (The .gitshadow Projection Contract Law)

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: `git_shadow.scanner` + `.gitshadow` 规则文件 + 跨端投影契约
- **Canonical Owners**:
  - `GitShadowContract` (.gitshadow 声明式影子契约)
  - `WhitelistProjectionAxiom` (白名单投影公理 · 终结黑盒猜谜)

---

## 1. Context (背景与现实断层)

在 `git-shadow` 的早期实现中，系统试图“自动猜透”哪些被 `.gitignore` 保护的文件应该被投射到远端：
1. **双重含义的混淆**：`.gitignore` 拦截的文件既包含**敏感高价值资产**（`.env`, 私钥, 本地日志 `_dev_log/`），又包含**跨平台绝对不可传输的重型垃圾**（`node_modules/`, `venv/`, `dist/`, `.cache`）。
2. **暗箱黑盒猜测的溃败**：早期引擎在 Python 代码中硬编码维护 `DEFAULT_SHADOW_IGNORE` 正则黑名单。该机制不仅无法穷举所有语言的构建垃圾，而且粗暴误杀了用户合法的开发资产（如 `_dev_log/` 曾被代码悄悄抹杀，diff 对其完全装瞎）。
3. **用户直觉的终极解决方案**：在继承 `.gitignore` 的同时，引入项目根目录的显式契约文件 —— **`.gitshadow`**。

---

## 2. Decision (架构决策与核心契约)

### 2.1 职责明确分离 (Separation of Exclusion Concerns)
- **`.gitignore`（公有防线）**：定义什么绝对禁止提交到公共版本库（GitHub / GitLab）；
- **`.gitshadow`（私有投影白名单）**：定义在 `.gitignore` 保护的资产中，**哪些被合法授权投射到受信任的远端云端工作区**。

### 2.2 `.gitshadow` 成为公有可追踪资产
- **必须提交至 Git 并同步远端**：`.gitshadow` 仅包含匹配模式（如 `.env*`、`_dev_log/`），绝不含明文秘密，因此它属于公开透明的工程规范资产。
- 远端 VPS、本地客户端与将来的心跳同步守护进程，均以工作区根目录的 `.gitshadow` 作为单一真源。

### 2.3 白名单投影公理 (`WhitelistProjectionAxiom`)
- 引擎彻底抛弃在代码中硬编码黑名单猜谜的做法。
- 扫描逻辑确立为明确的双重收敛：
  ```text
  CandidateFiles = (IsIgnoredByGitIgnore OR IsUntracked)
  ShadowFiles = CandidateFiles ∩ MatchedByGitShadow
  ```
- 若项目中未显式配置 `.gitshadow`，系统优雅兼容默认轻量安全模板（`.env*`, `*.secret`, `config.local.json`, `_dev_log/`），但强烈推荐显式声明。

---

## 3. Consequences (后果与收益)

- **彻底杜绝静默吞文件**：用户想传什么、不传什么，看一眼项目根目录的 `.gitshadow` 一清二楚，透明度达到 100%；
- **与语言和框架无关**：不再需要在源码中随着前端、后端框架不断打补丁维护 `DEFAULT_SHADOW_IGNORE`；
- **自解释与可审计**：团队协作或跨机器迁移时，`.gitshadow` 自带文档与契约属性。
