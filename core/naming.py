"""心弦好感度 - 配置键（拼音）↔ 领域名（中文）统一映射。

_conf_schema.json 的配置键用拼音 ASCII（WebUI 显示中文标题），代码内部
一律用中文领域名。此前 main.py / levels.py / level_economy.py 三处各自
维护互逆的映射表，现收拢到本模块单一真相源（v1.29.3）。
注意：等级表与评审五档是两套命名空间（youhao 在两边含义不同），分开声明。
"""

from __future__ import annotations

# 七级好感等级：配置键（拼音）→ 领域名（levels.*、economy.level_mult.* 用）
LEVEL_NAME_BY_KEY: dict[str, str] = {
    "yanwu": "厌恶",
    "mosheng": "陌生",
    "renshi": "认识",
    "youhao": "友好",
    "qinmi": "亲密",
    "zhiyou": "挚友",
    "zhiai": "挚爱",
}

# 反查：领域名 → 配置键
LEVEL_KEY_BY_NAME: dict[str, str] = {name: key for key, name in LEVEL_NAME_BY_KEY.items()}

# 评审五档：配置键（拼音）→ 档位名（judge.attitude_deltas.* 用）
TIER_NAME_BY_KEY: dict[str, str] = {
    "diyi": "敌意",
    "lengdan": "冷淡",
    "zhongxing": "中性",
    "youhao": "友好",
    "reqing": "热情",
}
