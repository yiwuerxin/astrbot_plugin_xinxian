"""已保存配置的默认值迁移。

AstrBot 的配置持久化规则：已保存的键永远优先于 `_conf_schema.json` 的
默认值，schema 默认值变更不会自动写回存量配置。因此"只改默认值"对保存
过配置的旧安装不生效。唯一安全的迁移时机是存值与已知旧默认**逐字一致**；
用户手改过的值与任何旧默认都不一致，绝不覆盖。
"""

# v1.24.0 → v1.24.1（PR #41）：主人版态度指引语义从"低段位＝赌气别扭"
# 改为"正负号分界"——正值低段不再有冲突叙事。保存过 v1.24.0 默认值的
# 老安装需要迁移。新值必须与当前 schema 默认值保持一致。
_MASTER_GUIDANCE_V124 = {
    "mosheng": (
        "冷战/别扭期，爱答不理、说话带刺，但心里在等主人先低头",
        "有点生分，提不起劲亲昵，但不生气也不冷战——正常说话，就是不黏人，像各忙各的老熟人",
    ),
    "renshi": (
        "小别扭还没消，嘴上不饶人，可心里在意主人、盼着主人来哄",
        "关系不错，温和亲近，相处自在，感情在慢慢升温——还没到特别黏的程度，但已经是能自然说话撒娇的关系",
    ),
    "youhao": (
        "和好了，会撒娇耍赖、跟主人要专属待遇",
        "感情稳定，会撒娇耍赖、跟主人要专属待遇，偶尔耍小性子也理直气壮",
    ),
}


def migrate_saved_defaults(cfg: dict) -> list:
    """把与旧默认逐字一致的已存值迁移到新默认。

    返回被迁移的配置键路径（`levels.<档>.master_guidance`）；空列表＝无需
    迁移。存值与旧默认不一致（用户自定义过）或键不存在时一律不动。
    """
    changed = []
    levels = cfg.get("levels")
    if not isinstance(levels, dict):
        return changed
    for key, (old, new) in _MASTER_GUIDANCE_V124.items():
        tier = levels.get(key)
        if isinstance(tier, dict) and tier.get("master_guidance") == old:
            tier["master_guidance"] = new
            changed.append(f"levels.{key}.master_guidance")
    return changed
