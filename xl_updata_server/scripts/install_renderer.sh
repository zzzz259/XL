#!/usr/bin/env bash
# 安装 Node 渲染器依赖（需在 Alibaba Cloud Linux 3 / CentOS Stream / Fedora 等 dnf 系统上运行）
# 说明：本脚本会安装系统级原生依赖和 Node 模块；需要 root 或 sudo 权限。
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENDERER_DIR="$(cd "$SCRIPT_DIR/../renderer" && pwd)"

echo "安装系统依赖：nodejs cairo pango libjpeg-turbo giflib..."
# 需要 sudo/root 权限
dnf install -y nodejs cairo pango libjpeg-turbo giflib

echo "安装 Node 模块（使用 npmmirror 加速）..."
cd "$RENDERER_DIR"
npm install --registry=https://registry.npmmirror.com

echo "Node 渲染器安装完成：$RENDERER_DIR"
