# 持续自动同步、平台差异与租约自毁规范

> 本参考文件阐述 `git-shadow` 的持续后台监听机制、范式 A 与范式 B 的协同模式、Windows/Linux 平台差异，以及防范僵尸进程的心跳租约自毁机制。

---

## 1. 双轨协同范式

```text
┌─────────────────────────────────┬─────────────────────────────────┐
│ 范式 A：前台随行会话监听 (Secondary)│ 范式 B：远端独立服务治理 (Primary)    │
│ Ephemeral Session Watcher       │ Autonomous Remote Daemon        │
├─────────────────────────────────┼─────────────────────────────────┤
│ 触发: git shadow run ... --watch│ 触发: git shadow service load   │
│ 承载: 本地客户端终端子线程      │ 承载: Linux 宿主常驻后台守护服务 │
│ 治理: Ctrl+C 退出即销毁，零残留 │ 治理: 显式加载/卸载，带 10min 租约│
│ 场景: 短平快桌面编码、即开即用  │ 场景: 长期开发、断线可恢复任务  │
└─────────────────────────────────┴─────────────────────────────────┘
```

---

## 2. 远端内存防御与租约自毁契约 (`LeaseBasedAutoTeardown`)

VPS（特别在 1C1G/1C2G 规格下）系统内存与进程资源极其宝贵。
为彻底根绝“断网或关机后远端残留无尽僵尸进程”的问题，范式 B 建立了严格的**心跳租约超期自毁机制**：

1. **租约时效**：服务启动时绑定 `--lease-ttl 600`（10 分钟）；
2. **心跳刷新**：本地客户端存活期间，后台定期（约 60 秒）刷新远端租约时间戳：
   ```text
   touch ~/.local/share/git-shadow/services/<project-hash>/lease
   ```
3. **内核自检**：远端服务守护进程定期比对当前时间戳与 `lease` 文件的最后修改时间；
4. **超时自毁**：一旦本地客户端离线超过 10 分钟未刷新租约，守护进程自动执行优雅退出，清理 Socket、PID 和 Lease 文件，**主动向 Linux 内核归还全部内存与句柄**。

---

## 3. 跨平台监听现状与已知开销注意事项

1. **Linux 宿主（远端/本地）**：
   - 依赖原生 `inotify` 接口（`IN_MODIFY`, `IN_CLOSE_WRITE`, `IN_CREATE`, `IN_DELETE` 等）；
   - 在无文件读写时内核休眠，CPU 占用恒定 `0.00%`，内存占用 `< 5 MB`。

2. **Windows 宿主（本地控制端）**：
   - 当前版本的 `LocalChangeWatcher` 仅内置了 Linux `inotify`，在 Windows 下 `self.native` 恒为 `False`；
   - 目前采用 `time.sleep(0.3s)` 循环并轮询 `shadow_snapshot()` 暴力扫描；
   - **避坑红线**：
     - 若启用 `--watch`，本地会每隔 2 秒发起一次 `submit_shadow_pull`，在未引入持久 SSH Master 连接前，会频繁启动 SSH 子进程；
     - 建议在跨国弱网环境下，避免在 Windows 本地长时间保持全天候高频轮询，优先采用单次 `push` / `pull` 或单次 `run` 启动。

---

## 4. 双端 Edge 架构演进路线 (Roadmap)

当前实现为**非对称模型**（远端为 Edge Agent，本地为 CLI 子线程）。
后续演进目标：
- **本地常驻 Local Edge Daemon**：在本地引入系统级服务，脱离终端窗口生命周期；
- **长连接通道复用**：双端 Edge 通过持久管道或长连接复用（SSH ControlMaster），将 2 秒高频轮询彻底改造为纯双向事件驱动推送。
