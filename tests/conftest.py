# tests/conftest.py
# 让测试从仓库根目录导入 backend，并固定 LLM provider，避免依赖本机 .env 的真实 Key。

import os
import sys
import tempfile

os.environ["LLM_PROVIDER"] = "mock"
# 测试不要污染本机真实的 data/memory.db
os.environ["MEMORY_DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="dh_memory_test_"), "memory.db"
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
