"""心弦好感度 - 应用服务层。

编排 core 与 storage，对外提供业务能力。
注意：judge_service 依赖 AstrBot，请按需 from services.xxx import 具体模块，
本包 __init__ 不做汇聚导入以保持 core/storage 的可独立测试性。
"""
