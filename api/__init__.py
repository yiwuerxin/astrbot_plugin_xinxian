"""心弦好感度 - 对外接口层。

facade 为跨插件稳定 API；llm_tools / commands / listeners 为纯 handler 函数
（@filter 钩子必须挂在 main.py 的 Star 子类方法上，此处只做逻辑转发）。
"""
