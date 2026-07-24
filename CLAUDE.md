# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`astrbot_plugin_xinxian`（心弦好感度）is an AstrBot 4.x plugin that maintains a per-user, per-group "favorability" score for a chatbot persona named 小千 (Xiaoqian). Favor changes through two engines: deterministic rule matching (keyword/@/reply/daily-first) and an LLM sentiment "judge". The current score is injected into the LLM system prompt so the persona's tone tracks closeness (厌恶 → 陌生 → 认识 → 友好 → 亲密 → 挚友 → 挚爱, supporting negative values and one-decimal precision).

Domain terms, config keys, commands, prompts, and code comments are **Chinese**; code identifiers are English. Match this convention.

## Commands

No build step and no third-party runtime deps — only the Python standard library plus the AstrBot framework (imported as `astrbot.api.*`). There is no `requirements.txt`, `pyproject.toml`, lint, or type-check config.

```bash
pip install pytest                 # only test dependency
pytest tests/ -v                   # run the full suite
pytest tests/test_core.py::TestFavorService -v          # one test class
pytest tests/test_core.py::TestMigration::test_v1_to_v2_round_trip -v   # one test
```

Tests are hermetic: they exercise only `core/` and `storage/` (plus `FavorService`, which transitively imports only those), so **they run without AstrBot installed**. `tests/test_core.py` inserts the plugin's parent directory onto `sys.path` and imports as the `astrbot_plugin_xinxian` package, so invoke pytest from anywhere. Do not import `services.judge_service`, `services.inject_service`, `api/`, or `main.py` in tests — those pull in `astrbot` and break hermeticity.

The plugin has no standalone runner. To run it live, clone into `AstrBot/data/plugins/astrbot_plugin_xinxian/` and restart AstrBot. All persisted data lives in `data/plugin_data/astrbot_plugin_xinxian/xinxian.db` (SQLite, local only).

## Architecture

Strict one-way layering: `main.py` → `api/` → `services/` → `core/` + `storage/`. `core/` depends on nothing outer and is the unit-testable domain core.

**`main.py` is the composition root and the *only* place `@filter` hooks live.** AstrBot scans `@filter` decorators only on `Star` subclass instance methods, so `XinxianPlugin` carries thin shell methods (`_on_group_msg`, `_on_llm_req`, `_cmd_*`, `_tool_*`) that just forward to pure handler functions in `api/`. When adding a command/listener/LLM-tool, add the logic as a handler in `api/` and a one-line shell method here — do not put logic in the shell. The constructor wires the full graph in order: `storage → levels → services → Deps → facade`.

**`services/favor_service.py::FavorService` is the single chokepoint for every numeric change.** All mutations funnel through `_apply_one`, which enforces three anti-abuse layers in order: (1) per-event cooldown, (2) bidirectional daily cap (`daily_cap_up`/`daily_cap_down` on net daily gain), (3) min/max clamp at the storage write. Entry points: `apply_rules` (rule engine), `apply_judge` (LLM engine — its own cooldown lives in `JudgeService`, passed `cooldown_key=None`), `change` (API/admin, no event cooldown, still daily-capped), and `set_favor` (admin set, bypasses daily cap entirely, hard-clamps to range). If you add a new way to change favor, go through one of these.

**Two favor engines run per group message** (`api/listeners.py::on_group_message`): `RuleMatcher.match` → `apply_rules`, then `JudgeService.judge` → `apply_judge`. The judge engine is **fail-silent by design** — any provider/parse/cooldown miss returns `None` and never affects the conversation. Identity is always taken from the event (`get_group_id`/`get_sender_id`/`get_self_id`); never parse identity out of injected prompt text.

**`core/decimal.py` is the single source of truth for one-decimal rounding and display.** The whole float-drift control strategy is: call `round1()` at *every write boundary* (storage `apply_delta`/`set_value`, `set_favor`, and inside `_cap_by_daily`). Several tests (`test_decimal_accumulation`, `test_clamped_not_tripped_by_float_noise`) exist specifically to catch regressions where a missing `round1` lets IEEE-754 noise flip the `clamped` flag. When touching any code path that computes a delta, preserved value, or cap remainder, keep applying `round1` at the boundary. `fmt()` renders integers without a trailing `.0`.

**Storage is backend-abstracted.** `services` depend only on `storage/base.py::StorageBackend` (ABC). `SQLiteBackend` is the default: synchronous `sqlite3` with `check_same_thread=False`, a module-level `threading.Lock`, WAL, and `busy_timeout`; read-modify-write happens atomically inside the lock. To add a backend (JSON/Redis/…), implement the ABC and swap the one line in `main.py` that constructs `SQLiteBackend` — upper layers are agnostic.

**Schema evolution uses `PRAGMA user_version`** (`storage/migrations.py`). To add a version: append a DDL/DML list to `MIGRATIONS` and bump `SCHEMA_VERSION`. Each version runs in its own transaction; note the explicit `BEGIN` is mandatory because a version's first statement may be DDL, which would otherwise auto-commit and defeat rollback. v2 (INTEGER→REAL affinity for negatives/decimals) is the worked example of the table-rebuild pattern.

## Cross-plugin API and config

**`api/facade.py::XinxianFacade`** is the stable public API for other AstrBot plugins. It is constructed as `self.api` on the Star instance and accessed via `context.get_registered_star("astrbot_plugin_xinxian").star_cls.api`. Its contract is **additive only — never rename or change a method signature**, only add new ones.

`_conf_schema.json` defines all WebUI-editable config (read defaults/types there; don't hardcode). `core/levels.py::LevelTable.from_config` and `core/events.py::RuleMatcher.from_config` each fall back to built-in defaults for any missing key, so partial config is always valid. Note the `_LEVEL_KEYS` map translates the Chinese level names (厌恶/陌生/…) to their pinyin config keys (yanwu/mosheng/…).

## Conventions to preserve

- Prompt injection (`services/inject_service.py`) is **append-only** to `system_prompt` — it never overwrites, so it coexists with other injecting plugins. Keep it that way.
- Master identity trusts QQ number only, never nickname (`core/identity.py`); master status is a text overlay in the injected profile, it does not alter the numeric logic.
- Prompt templates live in `resources/prompts/` and are loaded once via `_read_resource` in `main.py`; a non-empty `inject.template` config overrides the bundled `inject_template.txt`. The judge prompt expects the model to reply `态度:`/`分值:` lines, parsed by a regex in `JudgeService._parse`.
