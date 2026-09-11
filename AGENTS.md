# AGENTS.md

AI 编码代理在本仓库工作的通用约定。**只写规则，不写教程**——机制细节、背景叙事、评审标准全文与版本历史在 [ARCHITECTURE.md](ARCHITECTURE.md)，需要时再读。

## 项目概述

`astrbot_plugin_xinxian`（心弦好感度）是 AstrBot 4.x 插件：为 persona「小千」维护每用户、每群的好感分（支持负值、一位小数），自动变动的唯一引擎是 LLM 情感评审（规则引擎 v1.21 已删），当前分数注入 system prompt 使语气随亲密度变化。层级/关系/主人三层正交覆盖数值，另有可选的时间衰减、成员印象标签、与 `astrbot_plugin_maisoul` 的数值面情绪耦合。

领域词、配置键、命令、提示词、代码注释一律**中文**；代码标识符英文。

## 环境与命令

- 无构建步骤、无第三方运行时依赖（标准库 + AstrBot 框架）。唯一可选项 **Pillow**：仅排行图渲染需要，`api/rank_image.py` 局部导入——缺它插件照常加载。
- 无独立 runner：实机运行 clone 进 `AstrBot/data/plugins/astrbot_plugin_xinxian/` 后重启 AstrBot。
- CI（PR 触发；push 仅 main）：pytest 矩阵（3.10/3.12，离线无 AstrBot）+ AST 检查 + 敏感数据与纯净交付扫描 + black 26.5.1 格式门。另有 CodeQL 占位工作流（仅手动触发，仓库转公开后改回自动）。

```bash
pip install pytest black==26.5.1    # 测试与格式检查依赖
pytest tests/ -q                    # 全量套件（数量以输出为准，文档不硬编码）
pytest tests/test_core.py::TestFavorService -v                             # 单个测试类
pytest tests/test_core.py::TestMigration::test_v1_to_v2_round_trip -v    # 单个用例
black --check .                     # 格式门（CI 强制）
python3 - <<'EOF'                   # AST 检查：__init__ 内禁止局部变量先用后赋值（与 CI 同版）
import ast, sys
tree = ast.parse(open('main.py', encoding='utf-8').read())
init = next(n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == '__init__')
assigned = {}
def _mark(target, lineno):
    # 赋值目标形态全覆盖：普通/增强/注解赋值、for/with 绑定、
    # except as、海象——漏一种就有"先用后定义"逃逸
    if isinstance(target, ast.Name):
        assigned.setdefault(target.id, lineno)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for el in target.elts:
            _mark(el, lineno)
for node in ast.walk(init):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            _mark(t, node.lineno)
    elif isinstance(node, ast.AugAssign):
        _mark(node.target, node.lineno)
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        # 纯注解（x: int 无值）不绑定运行时值，不记——记了会掩盖真实的先用后定义
        _mark(node.target, node.lineno)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        _mark(node.target, node.lineno)
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                _mark(item.optional_vars, node.lineno)
    # 推导式目标不记：Py3 推导式是独立作用域，不绑定 __init__ 局部名
    elif isinstance(node, ast.ExceptHandler) and node.name:
        assigned.setdefault(node.name, node.lineno)
    elif isinstance(node, ast.NamedExpr):
        _mark(node.target, node.lineno)
bad = [n.id for n in ast.walk(init)
       if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
       and n.id in assigned and assigned[n.id] > n.lineno]
sys.exit(f"ORDER BUG: {bad}" if bad else 0)
EOF
```

## 测试规则

- 测试密闭靠**桩**：`test_core.py` 头部在无 AstrBot 的环境注入最小桩 `astrbot.api.logger`（容器内有真框架时用真的）。因此只依赖 `astrbot.api.logger` 的模块都离线可跑：`core/`、`storage/`、`services/favor_service`、`services/inject_service`、`api/emotion_bridge` 及惰性解析 logger 的模块。
- 禁止在测试里 import 需要真实框架面的模块——`services/judge_service`（要 `astrbot.api.star.Context`）、`api/listeners`、`api/page_api`、`main.py`：桩满足不了，会破坏离线可跑。
- **目录名陷阱**：`test_core.py` 把插件父目录插上 `sys.path` 后按包名 `astrbot_plugin_xinxian` 导入。被测目录必须叫这个名字（CI 检出布局满足）；本地用 worktree/挂载验证时，若旁边存在同名目录会 import 到错树、得到假结果。
- 触碰 `main.py` 的每次提交必跑上面的 AST 检查——#31/#35 两次生产事故都是 `__init__` 里配置字典先用后定义的 `UnboundLocalError`。
- 新逻辑必须配新测试，不是"没坏就行"；失败路径要真的失败过一次，不是空验证。

## 架构规则

- 单向分层 `main.py → api/ → services/ → core/ + storage/`；`core/` 零框架 import（不许模块级 `import astrbot`，需要 logger 就惰性解析）。
- `main.py` 是组合根、`@filter` 钩子唯一挂载点：钩子方法只做一行转发，逻辑写在 `api/` 的 handler。无个人自查命令（已刻意删除），唯一查询面是全群排行图。
- `FavorService` 是数值变动唯一咽喉，入口只有 `apply_judge` / `change` / `set_favor` / undo；新增变动方式必须走既有入口，禁止 ad-hoc `sqlite3` 写库。
- 主写路径 `apply_favor_change` 单事务（数值+当日额度+流水+冷却一个 commit，任一步失败整体回滚）；数值语义与 `apply_delta` 共用 `_compute_favor_write` 单一实现。
- judge 冷却必须在 provider 调用**前** `touch_event` 预留（`apply_judge` 恒传 `cooldown_key=None`）。
- `round1()` 在**每个写边界**调用（`core/decimal.py` 单一来源）——漏一处浮点噪声就可能翻掉 `clamped` 标志，专项测试盯防。
- 衰减读写不对称是承重设计：读只显示有效值不动库；写必须先衰减到当下再叠加、正向巩固半衰期；禁止新增绕过预衰减的写路径。
- 后台任务一律经 `Deps.registry.spawn`（持强引用，裸 `create_task` 会被 GC）；评审/印象/衰减/联动全部 fail-silent：任何失败降级为"无变化"，绝不把异常抛进聊天管线。
- 注入对 `system_prompt` **只追加**，绝不覆盖。
- `api/facade.py` 跨插件 API 只加不改签名。
- 跨插件探测唯一实现 `core/maisoul_probe.py::resolve_maisoul_api`——禁止再复制探测逻辑（历史分叉副本静默失明数日）。
- 群监听器是纯观察者：`priority=1000`（框架 `sort(key=-priority)` 大者先跑），不发声、不 stop_event。
- schema 迁移三处联动：`MIGRATIONS` 追加 + `SCHEMA_VERSION` 递增 + `pragma_version.py` 加分支（PRAGMA 不能参数化）；每版本独立事务且显式 `BEGIN`。
- SQL 一律全字面量 + `?` 参数化，**表名也不插值**。
- 配置默认值/类型只从 `_conf_schema.json` 读，不硬编码；`from_config` 必须接受部分配置；数值配置钳到安全范围。
- 拼音↔中文键位映射唯一来源 `core/naming.py`；judge prompt 的档位区间从锚点渲染（`tier_ranges_line` → `{tier_ranges}`），绝不硬编码进模板文本。

## 代码风格

- black 26.5.1 锁版本，新改动必须干净。
- 注释写**约束与契约**（为什么不能这样做），不写改动过程叙事。

## 红线与禁令（公开仓库，生产运行）

**绝不**：
- 真实用户数据进任何提交或 PR 文本：QQ 号、昵称/外号、群号、聊天摘录、真实好感数值、真实评审理由/印象、真实人格名。示例一律占位：QQ `123456789`、昵称「阿狸」、数值 `50.0`。
- 凭据、服务器路径、部署细节进仓库——只属于 `AGENTS.local.md`（已 gitignore）；未跟踪的 `.mimosa/` 同样不提交。
- 数据外发超出"消息文本 → 用户配置的评审 provider"：无遥测、无 phone-home、无硬编码 URL fetch，违者直接拒。
- 独立 server/端口/鉴权——面板挂 AstrBot 主面板（`register_web_api`），继承框架鉴权。
- 新增存储消息内容的列而不论证：必须说明数据流（碰什么数据、去哪、是否最小化）并保持 ≤200 字截断（对齐 `favor_log.message`）。
- 合并 PR（owner yiwuerxin 审合每一个，绝不自动/经 API 合并）；触碰 `xinxian.db*`；读取插件目录外的资源文件。

**先问再做**：修改 `.github/workflows/`；与他人 PR 的版本号冲突协调。

**开 PR 后自查**：经 API 拉取 PR 正文，grep 真实标识（QQ/昵称/群号/数值），命中立即 PATCH。

## Git 与 PR 规范

- 分支命名：`feat/<scope>-<topic>` / `fix/<topic>` / `chore/<topic>`。
- 提交：中文 Conventional Commits（`feat(webui): …`）；摘要一行、动词开头；正文写**为什么**（改前问题、理由、副作用），72 列手动换行；不列文件清单；禁止"修复 bug/更新代码"式空摘要。
- PR 四段：**改动简述**（用户可感知的变化，即 changelog 条目）/ **为什么改**（业务问题或用户可见 bug）/ **核心改动**（只挑 1–2 个关键文件或风险点，UI 改动附前后截图）/ **测试情况**。合格线：reviewer 不点开 Files changed 就能看懂。
- 流程：从 main 开分支 → `pytest tests/ -q` 绿 → push → 开 PR 到 main → **停**，报告 PR 链接等 owner。
- 版本：`metadata.yaml` 与 `main.py @register()` 字符串同步 bump、与代码同 PR；`fix`→patch、`feat`→minor、`BREAKING CHANGE`→major。
- 发版：owner 合并后打 annotated tag（`vX.Y.Z`，tag 注解正文即 Release Notes：一句题辞 + 主要新增/优化/修复分组 + 显式 PR 链接），push tag 触发 `release.yml`。部署仅 owner 合并后进行（细节见 AGENTS.local.md）。
- 外部 PR 评审：逐 hunk 核对红线与正确性，**不信 PR 正文声明**，本地 fetch head 验证；必要时自行 rebase 解冲突（对 PR 分支强推已授权）。

## 文档索引

| 文件 | 用途 |
|---|---|
| `ARCHITECTURE.md` | 架构与机制深读、生产评审标准全文、版本历史表（发版时在此补行） |
| `AGENTS.local.md` | 本机私有：目录布局/部署/凭据/网络/待办（gitignored，**绝不提交**） |
| `README.md` | 用户文档 |
| `_conf_schema.json` | 全部配置的唯一真相（默认值/类型） |
| `resources/prompts/` | 提示词模板（judge/inject），`inject.template` 配置可覆盖内置模板 |
| `REFACTOR_NOTES.md` | 开发笔记（export-ignore，不随发布包） |
