#!/usr/bin/env bash
set -Eeuo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
  elif command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
  else
    PYTHON_BIN=""
  fi
fi

if [[ -z "$PYTHON_BIN" ]] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "缺少 Python 3.11 或 3.12。请先安装可用的 Python 运行时和 pip。"
  exit 1
fi
if ! command -v java >/dev/null 2>&1; then
  echo "缺少 Java 21。请先执行：sudo dnf install -y java-21-openjdk-headless"
  exit 1
fi
if [[ ! -f "$SERVER_DIR/../xl_updata_tool/tools/lua/unluac.jar" ]]; then
  echo "缺少相邻目录 ../xl_updata_tool/tools/lua/unluac.jar。请同时上传 xl_updata_tool/app 和 xl_updata_tool/tools/lua。"
  exit 1
fi

cd "$SERVER_DIR"
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data/catalogs data/character_data data/character_cards data/portraits data/versions logs
if [[ ! -f config.toml ]]; then
  cp config.toml.example config.toml
fi
mkdir -p "$HOME/.config/systemd/user"
cp deploy/xl-updata-server.service "$HOME/.config/systemd/user/xl-updata-server.service"
systemctl --user daemon-reload
echo "安装完成。启动命令：systemctl --user enable --now xl-updata-server.service"
