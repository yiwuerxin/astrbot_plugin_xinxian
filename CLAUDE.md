# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`astrbot_plugin_xinxian`（心弦好感度）is an AstrBot 4.x plugin that maintains a per-user, per-group "favorability" score for a chatbot persona named 小千 (Xiaoqian). Favor changes through two engines: a deterministic rule that grants a small bonus on the day's first interaction (`daily_first`), and an LLM sentiment "judge". The current score is injected into the LLM system prompt so the persona's tone tracks closeness (厌恶 → 陌生 → 认识 → 友好 → 亲密 → 挚友 → 挚爱, supporting negative values and one-decimal precision).

Three orthogonal overlays sit on top of the raw number: **level** (cold→warm, from thresholds), **relationship** (friend/lover/family/… — the *role*, independent of warmth), and **master** (a text-only identity marker that never alters numeric logic). Optionally, **time decay** pulls stale scores toward a baseline.

Domain terms, config keys, commands, prompts, and code comments are **Chinese**; code identifiers are English. Match this convention.

## Commands

No build step and no third-party runtime deps — only the Python standard library plus the AstrBot framework (imported as `astrbot.api.*`). There is no `requirements.txt`, `pyproject.toml`, lint, or type-check config. The only extra is **Pillow** for rank-image rendering: `commands.build_rank_image` imports `api/rank_image.py` locally (and that module does `from PIL import …` at its top), so the plugin loads fine without Pillow — only rendering an image needs it.

```bash
pip install pytest                 # only test dependency
pytest tests/ -v                   # run the full suite (51 tests)
pytest tests/test_core.py::TestFavorService -v          # one test class
pytest tests/test_core.py::TestMigration::test_v1_to_v2_round_trip -v   # one test
```

Tests are hermetic: they exercise only `core/` and `storage/` (plus `FavorService` and `InjectService`, which transitively import only those), so **they run without AstrBot installed**. `tests/test_core.py` inserts the plugin's parent directory onto `sys.path` and imports as the `astrbot_plugin_xinxian` package, so invoke pytest from anywhere. Do not import `services/judge_service`, `api/`, or `main.py` in tests — those pull in `astrbot` and break hermeticity. (The current test count is **68**; `README.md`'s "18/29" counts are stale — don't "fix" tests downward to match them. On the dev host, `/usr/bin/python3` is PEP-668 managed: `pip3 install pytest --break-system-packages` once, then `python3 -m pytest tests/ -q`.)

The plugin has no standalone runner. To run it live, clone into `AstrBot/data/plugins/astrbot_plugin_xinxian/` and restart AstrBot. All persisted data lives in `data/plugin_data/astrbot_plugin_xinxian/xinxian.db` (SQLite, local only).

## Architecture

Strict one-way layering: `main.py` → `api/` → `services/` → `core/` + `storage/`. `core/` depends on nothing outer and is the unit-testable domain core.

**`main.py` is the composition root and the *only* place `@filter` hooks live.** AstrBot scans `@filter` decorators only on `Star` subclass instance methods, so `XinxianPlugin` carries thin shell methods (`_on_group_msg`, `_on_llm_req`, `_cmd_*`, `_tool_*`) that just forward to pure handler functions in `api/`. When adding a command/listener/LLM-tool, add the logic as a handler in `api/` and a one-line shell method here — do not put logic in the shell. The constructor wires the full graph in order: `storage → levels → services → Deps → facade → PageApi`. There is **no self-query command** (e.g. `/好感度`); the personal-query command was deliberately removed and all querying is now the all-group ranking image. `README.md` still lists it — stale.

**`services/favor_service.py::FavorService` is the single chokepoint for every numeric change.** All mutations funnel through `_apply_one`, which enforces three anti-abuse layers in order: (1) per-event cooldown, (2) bidirectional daily cap (`daily_cap_up`/`daily_cap_down` on net daily gain), (3) min/max clamp at the storage write. Entry points: `apply_rules` (rule engine), `apply_judge` (LLM engine — its own cooldown lives in `JudgeService`, passed `cooldown_key=None`), `change` (API/admin, no event cooldown, still daily-capped), `set_favor` (admin set, bypasses daily cap entirely, hard-clamps to range), and `undo_preview`/`undo_log` (dashboard undo — reverse-delta via `set_favor` with `source="undo"`, marks the original log row `reversed`). `set_relationship` and `touch_nickname` write only their own column and never touch favor. If you add a new way to change favor, go through one of these.

**Two favor engines run per group message** (`api/listeners.py::on_group_message`): `RuleMatcher.match` → `apply_rules`, then `JudgeService.judge` → `apply_judge`. The judge engine is **fail-silent by design** — any provider/parse/cooldown miss returns `None` and never affects the conversation. Identity is always taken from the event (`get_group_id`/`get_sender_id`/`get_self_id`); never parse identity out of injected prompt text. The listener also opportunistically caches the sender's nickname (in-memory dedupe → write only on change).

**`core/decimal.py` is the single source of truth for one-decimal rounding and display.** The whole float-drift control strategy is: call `round1()` at *every write boundary* (storage `apply_delta`/`set_value`, `set_favor`, inside `_cap_by_daily`, and the `daily_gain` read). Several tests (`test_decimal_accumulation`, `test_clamped_not_tripped_by_float_noise`, `test_daily_cap_decimal`) exist specifically to catch regressions where a missing `round1` lets IEEE-754 noise flip the `clamped` flag. When touching any code path that computes a delta, preserved value, or cap remainder, keep applying `round1` at the boundary. `fmt()` renders integers without a trailing `.0`.

**Time decay** (`core/decay.py::effective_favor`, config `decay.*`, off by default) pulls a stale score toward `baseline` after `grace_days` idle, at `per_day` per day, never crossing the baseline. It is applied in two distinct places, and the asymmetry is load-bearing: on **read** (`FavorService._effective`, used by `get`/`ranking`/`standings`) the *effective* value is shown but the stored value is untouched; on **write** (`SQLiteBackend.apply_delta`, inside the lock) the stored value is first decayed to "now" before the new delta is added, so a single interaction can't erase long-term coldness — decay gets locked in. Never add a write path that skips this pre-decay.

**Relationships** (`core/relationship.py`, config `relationship.*`, off by default) tag each member with a role key (`friend`/`soulmate`/`lover`/`family`/`disliked`, plus any custom label). Stored in `favor.relationship` (v4 column), they are **orthogonal to favor** — warmth is the number, role is the label. `RelationshipTable.resolve` maps a stored key → `(label, guidance)`; unknown values become custom labels with a generic guidance. The role feeds two surfaces: an injected "你们的关系" block, and the dashboard/rank-image display label.

**Storage is backend-abstracted.** `services` depend only on `storage/base.py::StorageBackend` (ABC). `SQLiteBackend` is the default: synchronous `sqlite3` with `check_same_thread=False`, a module-level `threading.Lock`, WAL, and `busy_timeout`; read-modify-write happens atomically inside the lock. To add a backend (JSON/Redis/…), implement the ABC and swap the one line in `main.py` that constructs `SQLiteBackend` — upper layers are agnostic.

**Schema evolution uses `PRAGMA user_version`** (`storage/migrations.py`; current `SCHEMA_VERSION = 6`). To add a version: append a DDL/DML list to `MIGRATIONS` and bump `SCHEMA_VERSION`. Each version runs in its own transaction; note the explicit `BEGIN` is mandatory because a version's first statement may be DDL, which would otherwise auto-commit and defeat rollback. v2 (INTEGER→REAL affinity for negatives/decimals, via the build-new-table/copy/drop/rename rebuild) is the worked example of a non-trivial migration. The pattern for adding a per-member field is: new `ALTER TABLE … ADD COLUMN` migration → add the field to `FavorRecord` → thread it through `SELECT`/`INSERT … ON CONFLICT` in `SQLiteBackend` → surface it. (See v4 `relationship` and v5 `nickname`.) `favor_log` (v3) is the change journal that powers both the dashboard and injected "近期印象"; v6 added `message` (triggering speech, judge path only) and `reversed` (undo flag).

**Prompt injection** (`services/inject_service.py`) renders one block from a template and **appends** it to `system_prompt` — never overwrites, so it coexists with other injecting plugins. The template placeholders are `{nickname}` `{user_id}` `{master_line}` `{favor}` `{max_favor}` `{level_name}` `{level_guidance}` `{recent_events}` `{relationship}`. `{recent_events}` and `{relationship}` render to empty strings when there is nothing to show, so a template *without* those placeholders simply disables those features. `recent_events` comes from `FavorService.recent_events` (last `memory_count` changes within `memory_days`). If `persona_anchor` is non-empty, it is appended after the block (anti-out-of-character anchor); it does not move into the template.

**Rank image + dashboard** are the two read surfaces, both driven by `FavorService.standings` (effective favor, level, relationship, idle days, decayed flag). `api/rank_image.py::render_ranking` is pure PIL: a column-major grid (top-left highest, down then next column), querier highlighted, 千咲 color palette, CJK font auto-probed from a candidate list with PIL-default fallback. `api/page_api.py::PageApi` registers four GET handlers on AstrBot's **main dashboard** via `context.register_web_api` (logs / groups / users / undo; served by the framework on its own port, auth inherited — no standalone server/port/auth in this plugin); the frontend is `pages/dashboard/` (Vue 3 via CDN, mounted through the AstrBotPluginPage bridge). `register_web_api` or Quart may be absent in older frameworks — `PageApi.register` fails silent.

## Cross-plugin API and config

**`api/facade.py::XinxianFacade`** is the stable public API for other AstrBot plugins. It is constructed as `self.api` on the Star instance and accessed via `context.get_registered_star("astrbot_plugin_xinxian").star_cls.api`. Its contract is **additive only — never rename or change a method signature**, only add new ones. Current methods: `get_favor`, `get_level`, `add_favor` (daily-capped), `set_favor`, `get_ranking`, `is_master`, `get_relationship`, `set_relationship`.

`_conf_schema.json` defines all WebUI-editable config (read defaults/types there; don't hardcode). `core/levels.py::LevelTable.from_config`, `core/events.py::RuleMatcher.from_config`, and `core/relationship.py::RelationshipTable.from_config` each fall back to built-in defaults for any missing key, so partial config is always valid. Note the `_LEVEL_KEYS` map translates the Chinese level names (厌恶/陌生/…) to their pinyin config keys (yanwu/mosheng/…).

## Development workflow (this host)

Two trees exist on this host — keep their roles straight:

- **Git working copy** (where changes are made): `/www/wwwroot/astrbot_plugin_xinxian` — NOT inside a running AstrBot tree. Local `main` tracks `origin/main` = `https://github.com/yiwuerxin/astrbot_plugin_xinxian.git`. **All changes are delivered as PRs to that repo** — never commit directly to `main`.
- **Production runtime**: Docker container `astrbot` (`soulter/astrbot:latest`, AstrBot 4.26.7) bind-mounts host `/www/server/panel/data` → `/AstrBot/data`, so the live plugin dir is host `/www/server/panel/data/plugins/astrbot_plugin_xinxian` (= container path `/AstrBot/data/plugins/astrbot_plugin_xinxian`). It is a plain file copy, **not** a git repo. Live SQLite data: `/www/server/panel/data/plugin_data/astrbot_plugin_xinxian/xinxian.db` (WAL active — never copy over it; only sync code files).

Workflow for every change:

1. Branch from `main` in the git copy using the repo's convention: `feat/<scope>-<topic>` / `fix/<topic>` / `chore/<topic>` (see history: `feat/webui-chisaki-theme`, `fix/undo-get-modal`).
2. Make changes, run `pytest tests/ -q` (must stay green), commit with conventional-style Chinese summaries (`feat(webui): …`, `fix(inject): …`).
3. Push with `git push -u origin <branch>` — credentials come from a local credential helper reading `tokens.txt` in the repo root (gitignored, never commit it); `~/.git-credentials` also has GitHub entries.
4. Open the PR to `main` (title mirrors the branch intent, e.g. "feat(rank-image): …"), then merge after review. Bump `metadata.yaml` `version` on release commits (`chore(release): vX.Y.Z`).
5. **Deploy to production**: sync code files from the git copy into the production dir (rsync is unavailable on this host; use `cp`/`tar`, excluding `.git __pycache__ .pytest_cache .mimosa tokens.txt`), then reload the plugin (AstrBot WebUI 插件管理 → 重载, or `docker restart astrbot` as last resort — the bot is live, prefer plugin reload). Never touch `xinxian.db*`.

Version-number caveat: `metadata.yaml` is the source of truth; the `@register(...)` string in `main.py` currently lags behind (`1.17.0` vs metadata `1.18.0`) — keep them in sync when bumping.

An untracked `.mimosa/` directory (security-scan artifacts) may exist — leave it out of commits.

## Conventions to preserve

- Injection is **append-only** to `system_prompt`; master identity trusts QQ number only, never nickname (`core/identity.py`); master status is a text overlay in the injected profile, it does not alter the numeric logic.
- The judge prompt expects the model to reply `态度:`/`分值:` (optionally `理由:` in narrative mode) lines, parsed by a regex in `JudgeService._parse`; `_parse` also force-corrects the sign against the attitude word and clamps to `max_abs_delta`. `narrative_reason` forces the session (main) model and switches to `judge_prompt_narrative.txt`; otherwise a separate cheap `provider_id` (or session fallback) is used. `context_window` pulls recent text-only conversation turns into the judge call. **The judge prompt follows the AstrBot persona**: `JudgeService._persona_ctx` resolves the session's effective persona (conv.persona_id → `persona_manager.resolve_selected_persona`, same source as the main chain) per evaluation; templates get `{persona_name}` and `{persona_block}` (persona summary, ≤500 chars, empty-safe) via `core/judge_prompt.py::render`. Legacy custom templates containing only `{text}` keep working (`judge.follow_persona` = false pins it to `judge.bot_name`).
- `text_wake` lets a plain (non-`/`) message trigger the ranking image when it exactly matches a configured phrase; the `_xinxian_cmd_done` flag on the event prevents the `/` command and the wake path from both firing.
- Prompt templates live in `resources/prompts/` and are loaded once via `_read_resource` in `main.py`; a non-empty `inject.template` config overrides the bundled `inject_template.txt`.
