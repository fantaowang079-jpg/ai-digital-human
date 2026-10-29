"""Provider 层：可插拔的模型接入模块。

每个 provider 实现统一接口，上层编排逻辑（pipeline.py）不感知具体实现，
新增模型只需在这里注册即可。
"""
