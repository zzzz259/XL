#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/home/admin/xl_qqbot"
OUTBOX_DIR="/home/admin/xl_updata_server/data/outbox"
PYTHON="python3.11"

print_usage() {
    cat <<EOF
用法: $0 [项目源码目录]
默认从当前目录复制到 ${INSTALL_DIR}
EOF
}

SRC_DIR="${1:-$(pwd)}"

echo "==> 安装 xl_qqbot 到 ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

# 复制源码
cp -r "${SRC_DIR}/bot_app" "${SRC_DIR}/requirements.txt" \
    "${SRC_DIR}/config.toml.example" "${SRC_DIR}/scripts" \
    "${SRC_DIR}/deploy" "${SRC_DIR}/tests" "${INSTALL_DIR}/" 2>/dev/null || true

# 创建 python3.11 venv
if ! command -v ${PYTHON} &>/dev/null; then
    echo "错误: 未找到 ${PYTHON}，请先安装 Python 3.11"
    exit 1
fi

${PYTHON} -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# 初始化配置
cd "${INSTALL_DIR}"
if [ ! -f config.toml ]; then
    cp config.toml.example config.toml
fi

# 初始化数据目录
mkdir -p data/sent
if [ -d "${OUTBOX_DIR}" ]; then
    echo "==> 检测到上游 outbox: ${OUTBOX_DIR}"
else
    echo "==> 警告: 未检测到上游 outbox 目录 ${OUTBOX_DIR}，请确认 xl_updata_server 已部署"
fi

# 安装 systemd 用户服务
mkdir -p ~/.config/systemd/user
cp "${INSTALL_DIR}/deploy/xl-qqbot.service" ~/.config/systemd/user/xl-qqbot.service
sed -i "s|/home/admin/xl_qqbot|${INSTALL_DIR}|g" ~/.config/systemd/user/xl-qqbot.service
systemctl --user daemon-reload

cat <<EOF

安装完成。

下一步：
1. 编辑 ${INSTALL_DIR}/config.toml，填入 appid / secret / 目标群 openid。
2. （可选）配置 [upload].file_base_url 以使用 URL 直传；留空将走分片上传，需实测。
3. 启动并设为开机自启：
   systemctl --user start xl-qqbot
   systemctl --user enable xl-qqbot
4. 查看日志：
   journalctl --user -u xl-qqbot -f

EOF
