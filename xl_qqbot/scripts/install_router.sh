#!/usr/bin/env bash
# 为 debug/test 级别服务创建隔离 worktree，复用仓库的长期分支。
set -euo pipefail

MAIN_DIR="/home/admin/xl_qqbot"

create_worktree() {
    local tier="$1" path="$2" branch="$3"
    if [ -d "${path}" ]; then
        echo "==> ${path} 已存在（使用 git -C ${path} pull --ff-only 更新）"
        return
    fi

    echo "==> 创建 ${tier} 级 worktree: ${path} (仓库分支 ${branch})"
    if git -C "${MAIN_DIR}" show-ref --verify --quiet "refs/heads/${branch}"; then
        git -C "${MAIN_DIR}" worktree add "${path}" "${branch}"
    elif git -C "${MAIN_DIR}" show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
        git -C "${MAIN_DIR}" worktree add --track -b "${branch}" "${path}" "origin/${branch}"
    else
        echo "错误: 缺少 ${branch} 分支。请先在 ${MAIN_DIR} 获取仓库长期分支。" >&2
        exit 1
    fi

    # 级别服务只需代码和配置；使用主服务共享的虚拟环境。
    cp -n "${MAIN_DIR}/config.toml" "${path}/config.toml" 2>/dev/null || true
    echo "    完成。代码更新后用 git pull --ff-only，再重启 xl-qqbot-${tier}。"
}

create_worktree "debug" "/home/admin/xl_qqbot-debug" "debug"
create_worktree "test" "/home/admin/xl-qqbot-test" "test"

cat <<'EOF'
==> 部署提示
1. venv 共享主项目，无需重建：/home/admin/xl_qqbot/.venv
2. 安装 unit：
     cp deploy/xl-qqbot-router.service deploy/xl-qqbot-prod.service \
        deploy/xl-qqbot-debug.service deploy/xl-qqbot-test.service \
        ~/.config/systemd/user/
     systemctl --user daemon-reload
3. 启动服务：
     systemctl --user start xl-qqbot-prod xl-qqbot-test xl-qqbot-debug xl-qqbot-router
4. 查看路由器日志：
     journalctl --user -u xl-qqbot-router -f
EOF
