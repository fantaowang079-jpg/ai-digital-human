# tests/conftest.py
# 让测试从仓库根目录导入 backend，并固定 LLM provider，避免依赖本机 .env 的真实 Key。

import os
import sys

os.environ["LLM_PROVIDER"] = "mock"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
