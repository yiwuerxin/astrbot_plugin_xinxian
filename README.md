# 心弦好感度（astrbot_plugin_xinxian）

为小千人格打造的 AstrBot 好感度系统插件。

**规则事件与 LLM 情绪评估双驱动** · 厌恶到挚爱七级好感阶段（支持负值与一位小数）· 每群独立记录 · 对话时自动注入态度指引 · 指令 / LLM 工具 / 跨插件 API 三通道。

## 功能特性

- **双引擎增减**：明确事件规则（每日首聊）+ LLM 情绪评估（判断对小千的态度微调，可关）
- **防通胀经济学**：评审五档制（敌意/冷淡/中性/友好/热情，模型只判档位、分值配置定）+ 证据门槛（非中性必须逐字引用原话证据）+ 噪声地板 + 阶段乘数（关系越深越难推进）+ 负面权重放大 + 同日重复衰减——寒暄不再加分，好感度按真实关系的节奏增长
- **七级好感阶段**：厌恶 → 陌生 → 认识 → 友好 → 亲密 → 挚友 → 挚爱，每级附带态度指引；好感度可为负（被得罪/被讨厌到「厌恶」时，小千态度明显冷淡抗拒）
- **一位小数精度**：好感度与增减分值精确到小数点后一位（如 50.5），规则分值、每日限幅、评估幅度均可配小数
- **每群独立**：同一 QQ 在不同群的好感度分开记录（主键 = 群号 + QQ 号）
- **主人身份**：按 QQ 号认定（昵称不可信），主人有专属身份标识，但数值照常涨跌
- **提示词注入**：每次对话自动把对方的好感度档案注入 system_prompt，小千自然地对亲密的人更软、对厌恶的人有距离
- **防刷机制**：单事件冷却 + 每日双向限幅（净增/净降各限）+ 数值上下限封顶
- **可扩展**：core 纯领域逻辑可单测，存储后端可替换，跨插件 API 稳定承诺

## 目录结构

```
astrbot_plugin_xinxian/
├── main.py                  # 组合根：装配模块 + Star 薄壳钩子（@filter 全在此）
├── metadata.yaml            # 插件元数据
├── _conf_schema.json        # 配置项（WebUI 可视化编辑）
├── core/                    # 领域核心层（纯 Python，不依赖 AstrBot，可单测）
│   ├── models.py            #   数据模型：FavorRecord / LevelDef / FavorChange
│   ├── levels.py            #   等级表：七级阈值（含厌恶负值区间）与态度指引，可配置
│   ├── events.py            #   事件类型与规则匹配器
│   ├── decimal.py           #   一位小数精度工具（round1 收敛 / fmt 显示）
│   └── identity.py          #   主人判定（只认 QQ 号）
├── storage/                 # 持久化层
│   ├── base.py              #   StorageBackend 抽象接口（换后端只动这里）
│   ├── sqlite_backend.py    #   SQLite 实现（WAL + 全局锁，锁内读-改-写）
│   └── migrations.py        #   schema 版本迁移（PRAGMA user_version）
├── services/                # 应用服务层
│   ├── favor_service.py     #   增减/查询/防刷（唯一数值入口）
│   ├── judge_service.py     #   LLM 情绪评估（静默降级，任何失败不影响对话）
│   └── inject_service.py    #   好感度档案注入（只追加不覆盖）
├── api/                     # 对外接口层（纯 handler 函数）
│   ├── facade.py            #   XinxianFacade：跨插件稳定 API
│   ├── llm_tools.py         #   LLM 工具逻辑
│   ├── commands.py          #   聊天指令逻辑
│   └── listeners.py         #   群消息监听与注入逻辑
├── resources/prompts/       # 提示词模板（注入模板、评估模板，可自行修改）
└── tests/test_core.py       # 核心层单元测试（29 个用例，含负值/小数/迁移）
```

模块依赖单向：`api → services → core/storage`，`core` 不依赖任何外层，低耦合高内聚。

## 安装

1. AstrBot WebUI → 插件管理 → 从 GitHub 安装：`https://github.com/yiwuerxin/astrbot_plugin_xinxian`
2. 或手动：clone 本仓库到 `AstrBot/data/plugins/` 下，重启 AstrBot
3. 无第三方依赖（仅标准库），无需安装 requirements

## 配置（WebUI 插件页）

| 配置 | 默认 | 说明 |
|---|---|---|
| master_ids | "" | 主人 QQ 号，逗号分隔 |
| max_favor / min_favor | 100 / -100 | 好感度上限 / 下限（支持负值，下限应与「厌恶」等级阈值一致） |
| default_favor | 0 | 新成员初始好感度 |
| daily_cap_up / daily_cap_down | 4 / 8 | 每日净增/净降上限（支持一位小数）；默认降上限更宽（负性偏向） |
| rules.* | 见 schema | 每日首次互动的开关、分值（支持一位小数） |
| judge.enabled / provider_id | true / "" | LLM 评估开关与模型（建议选便宜小模型，留空跟随会话） |
| judge.only_when_at_or_reply | true | 仅@/回复时评估，省成本 |
| judge.follow_persona / bot_name | true / 小千 | 评估提示词跟随 AstrBot 当前人格（切人格后按新人格的名字与人设评审）；关闭则固定用 bot_name |
| judge.attitude_deltas | 见 schema | 五档分值映射：敌意 -2.5 / 冷淡 -0.8 / 中性 0 / 友好 +0.6 / 热情 +1.8（模型只判档位，分值在此定） |
| economy.* | 见 schema | 防通胀经济学（仅评审路径）：噪声地板 0.5 / 负面权重 1.5 / 同日重复衰减 0.25 / 阶段乘数（挚友 0.35、挚爱 0.2） |
| levels.* | 见 schema | 七级阈值（含「厌恶」负值区间）与态度指引文本 |
| inject.enabled | true | 提示词注入开关；template 可自定义 |

## 使用

**聊天指令**
- `/好感度` — 查自己
- `/好感排行` — 本群榜单
- `/好感设置 QQ号 数值`（管理员，数值支持一位小数与负值，如 `/好感设置 123456 -30` 或 `50.5`）
- `/好感重置 [QQ号]`（管理员，不带参数清空整群）

**LLM 工具**（小千自主调用）
- `query_favor(target?)` — 查某成员好感度
- `query_favor_ranking()` — 查本群排行

**跨插件 API**

```python
star = context.get_registered_star("astrbot_plugin_xinxian")
api = star.star_cls.api            # XinxianFacade 实例

favor = await api.get_favor(group_id, user_id)        # 数值
info = await api.get_level(group_id, user_id)         # {favor, level, guidance, is_master}
new_value = await api.add_favor(group_id, user_id, 5) # 增减（受每日限幅）
await api.set_favor(group_id, user_id, 80)            # 设定
ranking = await api.get_ranking(group_id, 10)         # 排行
api.is_master(user_id)                                # 主人判定
```

API 承诺向后兼容：只增不改。

## 开发与测试

```bash
pip install pytest
pytest tests/ -v    # 18 个核心层用例，不依赖 AstrBot 环境
```

**扩展指南**
- 新事件类型：`core/events.py` 加 EventType 与规则构造，`listeners.py` 提供判定输入
- 新存储后端：实现 `storage/base.py` 的 StorageBackend，在 `main.py` 替换装配即可
- 新指令/工具：`api/` 加 handler 函数，`main.py` 加薄壳方法（**@filter 只能挂在 Star 方法上**）

## 数据与隐私

全部数据仅存于本地 `data/plugin_data/astrbot_plugin_xinxian/xinxian.db`（SQLite），不上传任何服务器。LLM 评估仅将单条消息文本发往配置的模型提供商。

## License

AGPL-3.0
