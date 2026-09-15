"""
git_shadow.probe
远端全维度探针：自动感知远端系统、工作区目录树、AI 应用版本与更新状态、Web 服务
"""

import json
from typing import Dict, Any, List, Optional
from .utils import Colors, format_size, log_info, log_warn, log_success, log_error

class RemoteProbe:
    def __init__(self, host: str):
        self.host = host
        self.data: Dict[str, Any] = {}

    def scan(self, engine) -> Dict[str, Any]:
        """执行远端全景探测脚本"""
        probe_sh = r"""
        # 1. 系统与基础信息
        R_USER=$(whoami)
        R_HOME=$HOME
        R_OS=$(uname -s 2>/dev/null || echo "Unknown")
        R_ARCH=$(uname -m 2>/dev/null || echo "Unknown")

        # 2. 探查工作区目录
        CANDIDATES="workspace wkspace projects code dev app"
        DETECTED_WORKSPACES=""
        for c in $CANDIDATES; do
            if [ -d "$R_HOME/$c" ]; then
                DETECTED_WORKSPACES="$DETECTED_WORKSPACES $c"
            fi
        done

        # 3. 探查 AI 编程应用版本
        VER_CLOUDCLI=$(cloudcli version 2>/dev/null || cloudcli -v 2>/dev/null || echo "")
        VER_OPENCODE=$(opencode --version 2>/dev/null || echo "")
        VER_CLAUDE=$(claude --version 2>/dev/null | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | head -n1 || echo "")
        VER_CODEX=$(codex --version 2>/dev/null | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | head -n1 || echo "")
        VER_AIDER=$(aider --version 2>/dev/null | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | head -n1 || echo "")
        VER_COMMANDCODE=$(commandcode --version 2>/dev/null | head -n1 || echo "")

        # 4. 探查 AI 应用是否有可用更新 (轻量化检查全球 npm/pnpm registry，超时 3s 防卡顿)
        OUTDATED_INFO=$(timeout 3 pnpm outdated -g 2>/dev/null || true)

        UP_CLAUDE=$(echo "$OUTDATED_INFO" | grep -i "claude-code" | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | tail -n1 || echo "")
        UP_CLOUDCLI=$(echo "$OUTDATED_INFO" | grep -i "cloudcli" | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | tail -n1 || echo "")
        UP_OPENCODE=$(echo "$OUTDATED_INFO" | grep -i "opencode" | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | tail -n1 || echo "")
        UP_CODEX=$(echo "$OUTDATED_INFO" | grep -i "codex" | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | tail -n1 || echo "")
        UP_COMMANDCODE=$(echo "$OUTDATED_INFO" | grep -i "command-code" | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | tail -n1 || echo "")

        # 5. 探查平台层运行环境版本 (用户自管，不提示更新)
        VER_NODE=$(node -v 2>/dev/null || echo "")
        VER_PNPM=$(pnpm -v 2>/dev/null || echo "")
        VER_PYTHON=$(python3 -V 2>/dev/null | awk '{print $2}' || echo "")
        VER_GIT=$(git --version 2>/dev/null | awk '{print $3}' || echo "")

        # 6. 探查 CloudCLI Web 服务 (端口 3001 & local-server.json)
        CLOUDCLI_PID=""
        CLOUDCLI_PORT=""
        if [ -f "$R_HOME/.cloudcli/local-server.json" ]; then
            CLOUDCLI_PID=$(grep -o '"pid": *[0-9]*' "$R_HOME/.cloudcli/local-server.json" | head -n1 | grep -o '[0-9]*')
            CLOUDCLI_PORT=$(grep -o '"port": *[0-9]*' "$R_HOME/.cloudcli/local-server.json" | head -n1 | grep -o '[0-9]*')
        fi

        CLOUDCLI_RUNNING=0
        if [ -n "$CLOUDCLI_PID" ]; then
            if ps -p "$CLOUDCLI_PID" >/dev/null 2>&1; then
                CLOUDCLI_RUNNING=1
            fi
        fi

        # 7. 探查 GitHub SSH 连通性
        GITHUB_AUTH_MSG=$(ssh -T -o StrictHostKeyChecking=accept-new -o ConnectTimeout=4 git@github.com 2>&1 || true)
        GITHUB_OK=0
        GITHUB_USER=""
        if echo "$GITHUB_AUTH_MSG" | grep -q "successfully authenticated"; then
            GITHUB_OK=1
            GITHUB_USER=$(echo "$GITHUB_AUTH_MSG" | grep -o 'Hi [^!]*' | sed 's/Hi //')
        fi

        # 组合 JSON 输出
        cat <<EOF
{
  "user": "$R_USER",
  "home": "$R_HOME",
  "os": "$R_OS",
  "arch": "$R_ARCH",
  "workspaces": "$DETECTED_WORKSPACES",
  "apps": {
    "cloudcli":    {"installed": "$([ -n "$VER_CLOUDCLI" ] && echo 1 || echo 0)", "version": "$VER_CLOUDCLI", "latest": "$UP_CLOUDCLI"},
    "opencode":    {"installed": "$([ -n "$VER_OPENCODE" ] && echo 1 || echo 0)", "version": "$VER_OPENCODE", "latest": "$UP_OPENCODE"},
    "claude":      {"installed": "$([ -n "$VER_CLAUDE" ] && echo 1 || echo 0)", "version": "$VER_CLAUDE", "latest": "$UP_CLAUDE"},
    "codex":       {"installed": "$([ -n "$VER_CODEX" ] && echo 1 || echo 0)", "version": "$VER_CODEX", "latest": "$UP_CODEX"},
    "commandcode": {"installed": "$([ -n "$VER_COMMANDCODE" ] && echo 1 || echo 0)", "version": "$VER_COMMANDCODE", "latest": "$UP_COMMANDCODE"},
    "aider":       {"installed": "$([ -n "$VER_AIDER" ] && echo 1 || echo 0)", "version": "$VER_AIDER", "latest": ""}
  },
  "platforms": {
    "node": "$VER_NODE",
    "pnpm": "$VER_PNPM",
    "python3": "$VER_PYTHON",
    "git": "$VER_GIT"
  },
  "cloudcli_service": {
    "running": $CLOUDCLI_RUNNING,
    "pid": "$CLOUDCLI_PID",
    "port": "$CLOUDCLI_PORT"
  },
  "github": {
    "ok": $GITHUB_OK,
    "user": "$GITHUB_USER"
  }
}
EOF
        """
        res = engine._run_ssh(probe_sh.strip(), capture=True, check=False)
        try:
            out = res.stdout.strip()
            start_idx = out.find("{")
            end_idx = out.rfind("}")
            if start_idx != -1 and end_idx != -1:
                json_str = out[start_idx:end_idx+1]
                self.data = json.loads(json_str)
            else:
                self.data = {"error": f"Invalid probe response: {out}"}
        except Exception as e:
            self.data = {"error": str(e), "raw": res.stdout}

        return self.data

    def get_preferred_workspace_dir(self, repo_name: str) -> str:
        """根据远端实际情况计算最佳工作区存放路径"""
        home = self.data.get("home", "~")
        ws_list = [w for w in self.data.get("workspaces", "").split() if w]
        if "wkspace" in ws_list:
            return f"{home}/wkspace/{repo_name}"
        elif "workspace" in ws_list:
            return f"{home}/workspace/{repo_name}"
        elif "projects" in ws_list:
            return f"{home}/projects/{repo_name}"
        else:
            return f"{home}/{repo_name}"

    def get_available_agents(self) -> List[Dict[str, str]]:
        """获取所有可用的 AI Agent 列表（包括终端与 Web 应用）"""
        agents = []
        cc_srv = self.data.get("cloudcli_service", {})
        if cc_srv.get("running"):
            cc_ver = self.data.get("apps", {}).get("cloudcli", {}).get("version", "")
            ver_tag = f" (v{cc_ver})" if cc_ver else ""
            agents.append({
                "id": "cloudcli",
                "name": f"CloudCLI Web 远程工作台{ver_tag} (https://cli.daduiot.com)",
                "type": "web",
                "url": "https://cli.daduiot.com"
            })

        apps = self.data.get("apps", {})
        tool_names = [
            ("opencode", "OpenCode CLI"),
            ("claude", "Claude Code (CLI)"),
            ("codex", "Codex CLI"),
            ("commandcode", "Command Code"),
            ("aider", "Aider")
        ]
        for tid, title in tool_names:
            app_info = apps.get(tid, {})
            if str(app_info.get("installed")) == "1":
                ver = app_info.get("version", "")
                ver_tag = f" (v{ver})" if ver else ""
                agents.append({
                    "id": tid,
                    "name": f"{title}{ver_tag}",
                    "type": "terminal",
                    "cmd": tid
                })

        agents.append({"id": "shell", "name": "Bash 交互式终端 (默认环境)", "type": "terminal", "cmd": "$SHELL -l"})
        return agents

    def display_report(self):
        """格式化打印远端环境诊断报告"""
        if "error" in self.data:
            log_error(f"探针获取失败: {self.data['error']}")
            return

        print(f"\n{Colors.BOLD}{Colors.CYAN}═══════════════════════════════════════════════════════════════{Colors.RESET}")
        print(f"{Colors.BOLD} 🛰️  远端主机健康与环境全景诊断 [{self.host}]{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}═══════════════════════════════════════════════════════════════{Colors.RESET}\n")

        # 1. 系统与身份
        user = self.data.get("user", "unknown")
        home = self.data.get("home", "unknown")
        os_info = f"{self.data.get('os')} ({self.data.get('arch')})"
        print(f"{Colors.BOLD}🖥️  系统与身份:{Colors.RESET}")
        print(f"  • 用户主目录:   {Colors.CYAN}{home}{Colors.RESET} ({user})")
        print(f"  • 操作系统:     {os_info}")

        ws = self.data.get("workspaces", "").strip()
        ws_display = " ".join([f"~/{w}" for w in ws.split()]) if ws else "未发现标准工作区目录"
        print(f"  • 感知工作目录: {Colors.GREEN}{ws_display}{Colors.RESET}")

        # 2. GitHub 认证
        gh = self.data.get("github", {})
        if gh.get("ok"):
            print(f"\n{Colors.BOLD}🔐 GitHub SSH 鉴权:{Colors.RESET} {Colors.GREEN}✔ 已就绪 (用户: {gh.get('user')}){Colors.RESET}")
        else:
            print(f"\n{Colors.BOLD}🔐 GitHub SSH 鉴权:{Colors.RESET} {Colors.YELLOW}⚠ 未就绪 (可运行 `git shadow auth sync {self.host}` 一键同步凭证){Colors.RESET}")

        # 3. Web 应用 / CloudCLI
        cc_srv = self.data.get("cloudcli_service", {})
        cc_app = self.data.get("apps", {}).get("cloudcli", {})
        cc_ver = cc_app.get("version", "")
        print(f"\n{Colors.BOLD}🌐 Web 远程工作台 (CloudCLI):{Colors.RESET}")
        if cc_srv.get("running"):
            ver_text = f"v{cc_ver}" if cc_ver else ""
            print(f"  • 运行状态:     {Colors.GREEN}✔ 正在运行{Colors.RESET} {Colors.DIM}(PID: {cc_srv.get('pid')}, 端口: {cc_srv.get('port')}){Colors.RESET} {Colors.CYAN}{ver_text}{Colors.RESET}")
            print(f"  • 接入地址:     {Colors.CYAN}https://cli.daduiot.com{Colors.RESET} (支持推送后自动弹窗)")
        else:
            print(f"  • 运行状态:     {Colors.DIM}未运行或未启动{Colors.RESET}")

        # 4. 应用侧：AI 编程智能体 (包含版本号与更新检查)
        apps = self.data.get("apps", {})
        print(f"\n{Colors.BOLD}🤖 AI 编程应用 (应用侧 · 版本与更新):{Colors.RESET}")

        display_list = [
            ("cloudcli", "CloudCLI (UI)"),
            ("claude", "Claude Code"),
            ("opencode", "OpenCode CLI"),
            ("codex", "Codex CLI"),
            ("commandcode", "Command Code"),
            ("aider", "Aider")
        ]

        for app_id, app_label in display_list:
            item = apps.get(app_id, {})
            is_installed = str(item.get("installed")) == "1"
            ver = item.get("version", "")
            latest = item.get("latest", "")

            if is_installed:
                # 检查是否有更新
                if latest and latest != ver:
                    update_badge = f"{Colors.YELLOW}↑ 可更新 (当前 v{ver} -> 最新 v{latest}){Colors.RESET}"
                else:
                    update_badge = f"{Colors.GREEN}✔ 最新{Colors.RESET} {Colors.DIM}(v{ver}){Colors.RESET}"
                print(f"  • {app_label:<16} {update_badge}")
            else:
                print(f"  • {app_label:<16} {Colors.DIM}○ 未安装{Colors.RESET}")

        # 5. 平台侧运行环境 (仅展示版本，用户自管，不提示更新)
        plats = self.data.get("platforms", {})
        print(f"\n{Colors.BOLD}📦 基础运行环境 (平台侧 · 用户自管):{Colors.RESET}")
        p_list = []
        if plats.get("node"):
            p_list.append(f"Node {Colors.CYAN}{plats['node']}{Colors.RESET}")
        if plats.get("pnpm"):
            p_list.append(f"pnpm {Colors.CYAN}v{plats['pnpm']}{Colors.RESET}")
        if plats.get("python3"):
            p_list.append(f"Python {Colors.CYAN}{plats['python3']}{Colors.RESET}")
        if plats.get("git"):
            p_list.append(f"Git {Colors.CYAN}{plats['git']}{Colors.RESET}")

        print("  " + "  |  ".join(p_list) if p_list else "  (未检测到环境)")
        print("")
