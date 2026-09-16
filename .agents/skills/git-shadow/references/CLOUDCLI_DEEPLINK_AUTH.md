# CloudCLI 鉴权自愈、JWT 自动颁发与深链集成 SOP

> 本参考文件记录 `git-shadow` 与 CloudCLI Web 平台的 API 交互契约、Token 过期自愈算法与 Session 幂等深链创建逻辑。

---

## 1. CloudCLI API 通信契约

远端边缘执行器（`git-shadow-edge-agent.py`）通过 `http://127.0.0.1:3000` 与本地部署的 CloudCLI 实例进行免网络代理的本地 IPC 交互：

1. **项目创建与复用**：
   - 接口：`POST /api/projects/create-project`
   - 请求体：`{"name": "<repo-name>", "path": "<remote-workspace-path>"}`
   - 幂等处理：若接口返回 HTTP 409 且 `error == "PROJECT_ALREADY_EXISTS"`，执行器自动退化调用 `GET /api/projects`，根据目录路径精确复用已有 `project_id`。

2. **会话创建**：
   - 接口：`POST /api/providers/sessions`
   - 请求体：`{"projectId": "<project-id>", "provider": "codex|claude|cursor|opencode", "initialPrompt": "..."}`
   - 响应体：`{"sessionId": "session-..."}`
   - 深链广播：生成深链格式为 `${CLOUDCLI_URL}/session/${sessionId}`，通过 JSONL `session.ready` 事件通知本地客户端。

---

## 2. HTTP 401 令牌过期自愈算法 (Token Auto-Minting)

### 痛点背景
CloudCLI Web 控制台用户登录 Token 有效期较短，且保存在浏览器 LocalStorage 中。远端边缘执行器如果在自动化请求中携带过期 Token，将导致会话创建失败（HTTP 401 Unauthorized）。

### 自愈核心逻辑
远端边缘执行器具备**基于本地 SQLite 鉴权库的 JWT 离线自愈能力**：
1. **定位配置与私钥**：
   - 读取 `~/.cloudcli/app.json`（获取配置端口与基地址）；
   - 打开 SQLite 数据库 `~/.cloudcli/auth.db`；
2. **提取 Secret 与用户信息**：
   - 从 `configs` 表读取 `jwt_secret`；
   - 从 `users` 表读取活跃用户记录（`id`, `username`, `email` 等）；
3. **本地离线签名（HS256）**：
   - 组装 Payload：
     ```json
     {
       "id": user_id,
       "username": username,
       "email": email,
       "iat": now,
       "exp": now + 7 * 86400
     }
     ```
   - 采用标准 Python `hashlib` 和 `hmac` 模块进行纯原生 HMAC-SHA256 签名，生成带有 7 天有效期的标准 JWT；
4. **注入请求头**：
   - 在后续所有向 CloudCLI 发起的 HTTP 请求中自动附加 `Authorization: Bearer <minted_jwt>`，实现完全零人工干预的 100% 自动提权与会话打通。

---

## 3. 乐观先行 (Optimistic Launch)

- 当用户敲击 `git shadow run <host> cloudcli` 时，本地客户端在 **0 毫秒** 瞬间通过默认浏览器直接打开 CloudCLI 控制台首页；
- 后台在 1~2 秒内流式打入代码与影子配置，并通过边缘执行器创建专属 Session；
- 创建成功后，浏览器无缝重定向或深链直接定位到当前新建的 Session，消灭所有白屏等待时间。
