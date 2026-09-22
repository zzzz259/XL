#!/usr/bin/env bash
set -Eeuo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$SERVER_DIR/.venv/bin/python}"

[[ -x "$PYTHON_BIN" ]] || { echo "Python 虚拟环境不存在: $PYTHON_BIN"; exit 1; }
[[ -f "$SERVER_DIR/config.toml" ]] || { echo "缺少 config.toml"; exit 1; }
[[ -f "$SERVER_DIR/../xl_updata_tool/tools/lua/unluac.jar" ]] || { echo "缺少 unluac.jar"; exit 1; }
"$PYTHON_BIN" -c "import UnityPy; import tomllib; print('python dependencies: OK')"
command -v node >/dev/null 2>&1 || { echo "Node 不在 PATH"; exit 1; }
node -v
[[ -d "$SERVER_DIR/renderer/node_modules" ]] || { echo "renderer/node_modules 不存在，请先运行 scripts/install_renderer.sh"; exit 1; }
"$PYTHON_BIN" -c "from pathlib import Path; files=list(Path('$SERVER_DIR/server_app').rglob('*.py'))+[Path('$SERVER_DIR/run_server.py')]; [compile(p.read_text(encoding='utf-8'), str(p), 'exec') for p in files]; print(f'compiled {len(files)} server files')"
command -v java >/dev/null 2>&1 || { echo "Java 不在 PATH"; exit 1; }
java -version 2>&1 | head -n 1
echo "server checks: OK"
