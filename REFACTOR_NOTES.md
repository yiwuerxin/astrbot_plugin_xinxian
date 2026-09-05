# REFACTOR_NOTES — v1.30.0（GOAL 双插件加固重构）

本次按外部审查报告与 GOAL 任务书完成 P0 修复、结构重构与机制移植。
**所有新配置键有默认值、新机制默认关闭或保持既有默认**，升级后行为不变。

## 升级注意事项（含数据迁移说明）

- **数据库自动迁移 v8 → v9**：`favor` 表新增 `points` 列（印象带权点，JSON 数组）。
  首次加载自动 `ALTER TABLE ADD COLUMN`，存量数据不动、无需备份操作；**绝不覆盖
  xinxian.db\***（与历次升级一致）。
- schema 新增键：`judge.timeout_sec`（60）、`inject.anti_injection`（true）、
  `impression.points_mode`（false）。旧配置无需手改。
- 管理面板前端随插件目录一起同步（pages/dashboard）；undo/刷新印象端点已改 POST，
  **旧前端配新后端会 404**——请整体同步后重载插件。
- 与 maisoul v6.16.0 的联动（P-H）：双方装好后在 maisoul 开 `xinxian_link` 即可，
  任一插件缺失自动静默降级。
- 升级冒烟：`/好感排行` 正常出图（或无 Pillow 时降级文字）；面板流水页搜索正常。

## 修复（Phase 1，编号对应 GOAL）

- **X1** 流水查询 user_id 默认精确匹配（原 LIKE '%..%' 让 QQ 互为子串时把他人流水
  算进本人同日衰减/修复期/近期印象/印象汇总/里程碑——跨用户数据污染）；模糊仅
  WebUI 搜索显式 `fuzzy=1`。
- **X2** 注入链路（每次对话必经路径）加运行期防护 + 自定义模板装配期静态校验
  （未知占位符报配置错误并回落默认模板，不再在每次 LLM 请求上抛 KeyError）。
- **X3** 评审/印象汇总 LLM 调用加超时（judge.timeout_sec，默认 60s，0=关）——
  provider 挂起不再永久滞留任务并卡死该用户冷却。
- **X4** 撤销单事务化（StorageBackend.apply_undo）：校验→反向→流水→标记同一
  事务，消灭并发双击双倍反向扣分的 TOCTOU；钳边界零流水不追加但照标 reversed。
- **X5**（上游 PR #58 已修）每日限幅回退默认与 schema 对齐 4/8。

## 重构（Phase 2）

- **X6**（已由 PR #58 的 ImpressionService 拆分达成）FavorService 不再反向依赖评审服务。
- **X7** 面板取数一律经 FavorService（不再直拿 storage）；undo/refresh_impression
  改 POST（GET 会被预取/分享 URL 误触发副作用）；参数兼容查询串与 JSON body。
- **X8** 排行图 PIL 渲染放线程池；临时 PNG 发送后延迟 5s 删除（经注册表），渲染前清扫兜底。
- **X9** 四个重读路径（ranking/list_favor/distinct_groups/query_logs）经 to_thread，
  面板千行级 fetchall 不再阻塞回复；原子性契约更新（读路径让出、读写序列仍直行）。
- **X10** core/taskregistry.py（与 maisoul 同一实现模式）：评审/印象/延迟清理统一
  入册，terminate 先 cancel_and_wait 再关存储。

## 机制移植（Phase 3）

- **P-F 反注入**：评审文本先剥引用前缀/转发占位；好感档案块附防注入声明
  （inject.anti_injection 默认开）。
- **P-C 带权印象点**（`impression.points_mode` 默认关）：LLM 提 (point, weight)；
  SequenceMatcher≥0.6 相似合并（权重求和）；>10 点按 weight×时间权重（1h=1.0→
  24h=0.7→7d=0.95→30d=0.1 非单调阶梯）加权随机保留 10 条，挤出并入长印象；
  好感负向变化新点 ×1.5（损失厌恶）；分析提示词把其他群友匿名化为 用户A/B。
  关闭时维持一句话印象（legacy 路径零变化）。
- **P-H** facade 新增 `get_profile`（跨插件 API 只增不改）。
- **P-G** 属 maisoul 侧（上游已实现）。

## 验证

- `pytest tests/ -q`（容器内）：204 项全绿（本次新增 20+ 项，先失败后通过）。
- AST `__init__` 顺序检查通过；`grep LIKE` 仅 WebUI 搜索路径（fuzzy 显式）；
  `grep create_task` 仅 TaskRegistry 本体与无注册表测试兜底。
