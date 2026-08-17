import os
import sys
import tempfile
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

# 测试不应向项目 logs/ 写入运行时文件。
os.environ.setdefault("XL_LOG_DIR", tempfile.mkdtemp(prefix="xl-test-logs-"))
