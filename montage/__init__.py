"""montage-core — 中文优先、Agent 驱动的 AI 视频生产系统。

核心设计原则：
1. Agent 是导演，代码只提供工具与状态管理。
2. 一切产物（brief/script/scene_plan/shot_prompts/...）都是可校验的 JSON artifact。
3. 工具通过注册表统一发现，选型器按能力路由，新增供应商零改代码。
4. 提示词工程是知识资产：中文剧情语义由 LLM 写，英文画质层由 builder 常量兜底。
"""

__version__ = "0.1.0"
