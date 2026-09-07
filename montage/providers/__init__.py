"""providers — 供应商适配器包。

统一模式：每个供应商一个模块，公开 BaseTool 子类（通过模块级 TOOLS 或类扫描
被 registry 自动发现）。所有远程调用走 providers.http（纯标准库），
失败返回 ToolResult(success=False, error=...) 而非抛异常。

适配器按"接口契约确定度"分级（docstring 标注）：
- 确定：契约已核实（如 DashScope ASR 异步转写、Agnes 图片生成）。
- 待联调：契约来自公开文档/经验，需真实密钥验证后微调。
"""
