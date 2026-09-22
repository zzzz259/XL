#!/usr/bin/env bash
set -Eeuo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$SERVER_DIR/.venv/bin/python}"
CONFIG_PATH="${CONFIG_PATH:-$SERVER_DIR/config.toml}"
VERIFY_DATA_DIR="${VERIFY_DATA_DIR:-$SERVER_DIR/data/full-production-$(date +%Y%m%d-%H%M%S)}"
UNIT_NAME="${UNIT_NAME:-xl-updata-full-production}"
concurrency=1

[[ -x "$PYTHON_BIN" ]] || { echo "Python 虚拟环境不存在: $PYTHON_BIN"; exit 1; }
[[ -f "$CONFIG_PATH" ]] || { echo "配置文件不存在: $CONFIG_PATH"; exit 1; }
command -v systemd-run >/dev/null 2>&1 || { echo "缺少 systemd-run"; exit 1; }

PYTHON_CODE='import json, sys
from dataclasses import asdict, replace
from pathlib import Path
from server_app.config import load_config
from server_app.pipeline import UpdatePipeline
from server_app.processor import ProductionUpdateProcessor

config = load_config(sys.argv[1])
config = replace(config, data_dir=Path(sys.argv[2]).resolve())
result = UpdatePipeline(config.data_dir, processor=ProductionUpdateProcessor(config)).run_once()
print(json.dumps(asdict(result), ensure_ascii=False, default=str))
raise SystemExit(0 if result.error is None else 1)'

echo "启动低并发完整生产: unit=$UNIT_NAME concurrency=$concurrency data_dir=$VERIFY_DATA_DIR"
exec systemd-run --user \
  --unit="$UNIT_NAME" \
  --collect \
  --property=WorkingDirectory="$SERVER_DIR" \
  --property=CPUQuota=100% \
  --property=CPUAffinity=0 \
  --property=MemoryMax=2G \
  --property=MemorySwapMax=512M \
  --property=TasksMax=8 \
  --property=Nice=10 \
  --property=TimeoutStartSec=6h \
  "$PYTHON_BIN" -c "$PYTHON_CODE" "$CONFIG_PATH" "$VERIFY_DATA_DIR"
