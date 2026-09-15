# ADR — Git 身份治理、比对先行与多账号决策律 (Git Identity Governance Law)

- **Status**: Accepted
- **Date**: 2026-09-15
- **Scope**: `git_shadow.auth` + 跨端 Git 凭证对齐 + SSH 路由治理
- **Canonical Owners**:
  - `GitIdentityGovernance` (Git 凭证与身份跨端治理)
  - `DiffFirstSync` (指纹与块级比对先行契约)
  - `MultiAccountResolution` (多 Git 账号上下文精准推断)
  - `AuthorIdentityAlignment` (Git 提交者身份伴随对齐)

---

## 1. Context (背景与真实世界痛点)

在 `git shadow auth sync` 的初代设计中，系统采用简单直接的“检测首个本地密钥 → 远端覆写写入”策略。在 Step 2 的工程实测与复盘中，发现该简易策略在面对真实世界多开发者、多机与多账号复杂场景时存在严重的架构缺陷：

1. **盲目覆写引发的灾难性破坏**：
   开发者 VPS 往往已有既有运维配置、跳板机路由或内网代理。粗暴的文件覆盖会导致用户已有定制配置丢失；盲目的全量重写破坏了远端现存配置的稳定性。
2. **多 Git 账号与密钥错配（403 陷阱）**：
   主力开发者普遍同时拥有“个人开源账号”（如 `id_ed25519` 对应 GitHub）与“公司/商业账号”（如 `id_ed25519_work` 对应企业 GitHub/GitLab）。若工具盲目抓取默认 key，远端拉取公司私有库时将直接遭遇权限拒绝（`403 Repository not found`）。
3. **职责越界（Scope Creep）**：
   `git-shadow` 的唯一使命是打通 Git 工作流与代码投射，绝不能膨胀为泛化的通用 SSH 主机运维工具。
4. **提交人身份丢失（Commit Identity Fracture）**：
   仅对齐 SSH 私钥只能解决 `git clone/pull`，远端 AI Agent 执行 `git commit` 时，极易因缺失 `user.name` 和 `user.email` 导致提交失败或沦为 `ubuntu@ip-xxx` 匿名提交，破坏贡献树归属。

---

## 2. Decision (架构决策与核心契约)

借鉴 **Canonical Document Ownership（单一概念单一所有权律）** 与最小介入原则，做出如下不可违反的架构决策：

### 2.1 Diff-First 比对先行与免毁损契约 (`DiffFirstSync`)
- **指纹比对前置**：在传输密钥前，先计算本地与远端公私钥的 SHA-256 指纹。若远端已存在匹配且权限合规的密钥，直接标记为 `✔ 已对齐 (跳过传输)`，禁止产生冗余写操作。
- **块级安全合并（Block-Level Merge）**：针对远端 `~/.ssh/config`，采用专有标记块识别（`# --- git-shadow: <domain> ---`）。仅对专有块执行增量更新或安全附加，严禁破坏、覆盖用户既有的全局配置与非 Git 路由。

### 2.2 Scoped Git Auth 范围铁律
- 同步行为严格限定在 **Git 托管服务鉴权范围**。
- 支持的目标托管商以当前仓库的 `remote.origin.url` 动态推导为主（GitHub / GitLab / Gitee / 自建企业 Git 域名），绝不越界干预服务器其他网络或 SSH 资产。

### 2.3 Context-Aware 多账号推断与治理 (`MultiAccountResolution`)
- **上下文感知推断**：优先以当前本地仓库关联的 Remote Host 作为依据。若本地 `~/.ssh/config` 中该 Host 显式声明了 `IdentityFile`，则自动锁定该专有密钥对。
- **多密钥安全枚举**：当无法唯一推断且存在多个 candidate keys（如 `id_ed25519`, `id_rsa`, `*_work`）时，系统必须提供交互式选择或支持 `--key <key_name>` 显式指定，严禁暗箱盲猜。

### 2.4 Author 提交者身份伴随对齐 (`AuthorIdentityAlignment`)
- 执行身份同步时，系统必须探查本地当前 Git 的 `user.name` 与 `user.email`（优先读取 local 仓库级，兜底 global 全局级）。
- 同步将作者身份对齐至远端对应工作区的 Git 配置中，确保远端 AI Agent 提交代码时作者信息 100% 吻合，防止断链。

---

## 3. Consequences (架构后果与状态边界)

- **正面收益**：
  - 彻底规避对远端已有 SSH 环境的破坏；
  - 解决团队多账号、个人/公司双轨开发时的权限错配；
  - 形成闭环的作者身份链条，远端 AI 产生的 commit 完美归属主开发者。
- **边界与限制**：
  - 身份治理仅服务于 Git 访问，不承担 VPS sudoer 权限、SSH 端口改派或网络防火墙调整；
  - 规范状态由本 ADR 统领，未来正文实现就地更新，禁止在历史后追加打架的面条补丁。
