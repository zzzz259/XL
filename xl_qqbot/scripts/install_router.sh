#!/usr/bin/env bash
# 说明性脚本：为 debug/test 两个级别服务创建独立 worktree 目录。
#
# 目的：调试级服务跑各自 worktree 的代码（可随意改/崩），不影响正式服务。
# venv 共享主项目的 .venv；服务进程 PYTHONPATH 指向各自 worktree（见 deploy/*.service）。
# 前提：主项目 /home/admin/xl_qqbot 是一个 git 仓库（git worktree add 依赖 .git）。
set -euo pipefail

MAIN_DIR="/home/admin/xl_qqbot"
MAIN_BRANCH="${MAIN_BRANCH:-main}"

create_worktree() {
    local tier="$1" path="$2" branch="$3"
    if [ -d "${path}" ]; then
        echo "==> ${path} 已存在，跳过（git -C ${path} pull 可更新）"
        return
    fi
    echo "==> 创建 ${tier} 级 worktree: ${path} (分支 ${branch})"
    git -C "${MAIN_DIR}" worktree add "${path}" -b "${branch}" "${MAIN_BRANCH}" || \
        git -C "${MAIN_DIR}" worktree add "${path}" "${branch}"
    # 级别服务只需要：bot_app 源码 + 配置（config.toml 复制主项目现成的）
    cp -n "${MAIN_DIR}/config.toml" "${path}/config.toml" 2>/dev/null || true
    echo "    完成。改代码后 systemctl --user restart xl-qqbot-${tier}"
}

create_worktree "debug" "/home/admin/xl_qqbot-debug" "bot/debug"
create_worktree "test" "/home/admin/xl-qqbot-test" "bot/test"

cat <<'EOF'
==> 部署提示
1. venv 共享主项目，无需重建：/home/admin/xl_qqbot/.venv
2. 安装 unit：
     cp deploy/xl-qqbot-router.service deploy/xl-qqbot-prod.service \\
        deploy/xl-qqbot-debug.service deploy/xl-qqbot-test.service \\
        ~/.config/systemd/user/
     systemctl --user daemon-reload
3. 启动顺序（路由器最后也行，会重试转发失败）：
     systemctl --user start xl-qqbot-prod xl-qqbot-test xl-qqbot-debug xl-qqbot-router
4. 日志：journalctl --user -u xl-qqbot-router -f
EOF
