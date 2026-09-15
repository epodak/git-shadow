#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
用法: scripts/acceptance_vps.sh <ssh-host> [--dest <remote-dir>] [--no-push] [--keep-service]

在仅配置 SSH 的 Linux VPS 上验收 git-shadow 的 edge 安装与项目级 service。
默认会执行一次 git-shadow push；脚本结束时卸载本次加载的 service。
EOF
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

REMOTE_HOST="$1"
shift
REMOTE_DIR=""
DO_PUSH=1
KEEP_SERVICE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)
            [[ $# -ge 2 ]] || { echo "--dest 需要远端目录" >&2; exit 2; }
            REMOTE_DIR="$2"
            shift 2
            ;;
        --no-push)
            DO_PUSH=0
            shift
            ;;
        --keep-service)
            KEEP_SERVICE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SERVICE_LOADED=0

run_cli() {
    PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m git_shadow.cli "$@"
}

cleanup() {
    if [[ "$SERVICE_LOADED" == 1 && "$KEEP_SERVICE" == 0 ]]; then
        run_cli service "$REMOTE_HOST" unload >/dev/null 2>&1 || true
    fi
}

trap cleanup EXIT
trap 'exit 130' INT TERM

echo "[1/4] 检查 SSH 与 VPS standalone edge 安装: $REMOTE_HOST"
run_cli edge install "$REMOTE_HOST"

echo "[2/4] 加载项目级 VPS service"
run_cli service "$REMOTE_HOST" load
SERVICE_LOADED=1

echo "[3/4] 核验 service 状态与 Lease"
STATUS_OUTPUT="$(run_cli service "$REMOTE_HOST" status)"
printf '%s\n' "$STATUS_OUTPUT"
if ! grep -q '"status": "running"' <<<"$STATUS_OUTPUT"; then
    echo "service 未进入 running 状态" >&2
    exit 1
fi

if [[ "$DO_PUSH" == 1 ]]; then
    echo "[4/4] 执行一次真实 Git/Shadow 分层 push"
    PUSH_ARGS=(push "$REMOTE_HOST" --service)
    if [[ -n "$REMOTE_DIR" ]]; then
        PUSH_ARGS+=(--dest "$REMOTE_DIR")
    fi
    run_cli "${PUSH_ARGS[@]}"
else
    echo "[4/4] 已通过安装、加载和状态验收（--no-push）"
fi

if [[ "$KEEP_SERVICE" == 1 ]]; then
    echo "验收完成：按 --keep-service 保留 VPS service。"
else
    echo "验收完成：退出时已安排卸载 VPS service。"
fi
