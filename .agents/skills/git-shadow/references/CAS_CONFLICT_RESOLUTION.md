# CAS (Compare-And-Swap) 冲突检测与合流 SOP

> 本参考文件定义 `git-shadow` 在跨端同步私有配置与受保护影子文件时的 CAS 冲突检测、三方比对与原子回滚机制。

---

## 1. 为什么会发生 CAS 冲突？

`git-shadow` 严格拒绝像传统网盘或 rsync 那样粗暴覆盖。
每个 `.gitshadow` 文件在同步时，都携带其在上一次同步时记录的**基线指纹 (Base Hash)**。
当发生以下情况时，触发 CAS 冲突：
- 本地基于版本 A 修改了文件，生成版本 A'；
- 远端（如 VPS 上的 AI Agent）也同时基于版本 A 修改了该文件，生成了版本 A''；
- 远端的实际文件指纹已非版本 A，与本地提交的 Base Hash 不一致。

此时，VPS 边缘执行器会立即中断写操作，并抛出 `SHADOW_CAS_CONFLICT` 错误，保护现场绝不丢失任何一方的代码。

---

## 2. 冲突现象与识别

当执行 `git shadow push` 或后台 `--watch` 同步时，若出现以下提示：
```text
[WARN] 远端文件 [HELP.md] 存在冲突：远端基线已被修改 (预期: a1b2c3d4, 实际: e5f6a7b8)
[ERROR] VPS 分层同步任务未完成: shadow CAS 校验失败，已中止写入以防覆盖远端修改
```
或执行 `git shadow pull <host> --with-shadows` 时：
```text
[WARN] 检测到本地与远端同时修改了 [HELP.md]，已保留本地文件并将远端版本写入 [HELP.md.remote]
```

---

## 3. 标准处理流程 (3 步合流 SOP)

### 步骤 1：拉取并隔离生成三方版本
在本地终端执行：
```bash
git shadow pull <host> --with-shadows
```
- 本地原有文件保持不变；
- 远端的最新修改会被自动下载为 `<filename>.remote`（例如 `HELP.md.remote`）；
- 并在终端高亮提示冲突文件路径。

### 步骤 2：比对并合并差异
使用常规 diff 工具或 IDE 双栏比对：
```bash
# 快速查看终端 diff
diff -u HELP.md HELP.md.remote

# 或在 VS Code 中直接对比
code --diff HELP.md HELP.md.remote
```
将远端有价值的修改（如 AI 新增的配置项、开发日志）手工合并进本地文件（`HELP.md`），随后删除临时文件：
```bash
rm HELP.md.remote
```

### 步骤 3：更新本地基线并回推
合并完成后，直接执行一次静默推送：
```bash
git shadow push <host>
```
`git-shadow` 会重新计算本地最新合并版本的 SHA-256，将最新的远端基线对齐，冲突彻底解除。
