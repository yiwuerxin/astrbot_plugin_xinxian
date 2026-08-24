# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`astrbot_plugin_xinxian`（心弦好感度）is an AstrBot 4.x plugin that maintains a per-user, per-group "favorability" score for a chatbot persona named 小千 (Xiaoqian). All automatic favor changes come from a single engine: an LLM sentiment "judge" (the former deterministic rule engine — daily-first bonus — was removed in v1.21). The current score is injected into the LLM system prompt so the persona's tone tracks closeness (厌恶 → 陌生 → 认识 → 友好 → 亲密 → 挚友 → 挚爱, supporting negative values and one-decimal precision).

Three orthogonal overlays sit on top of the raw number: **level** (cold→warm, from thresholds), **relationship** (friend/lover/family/… — the *role*, independent of warmth), and **master** (a text-only identity marker that never alters numeric logic). Optionally, **memory-style time decay** (v1.23, exponential forgetting curve with interaction-consolidated half-life) continuously pulls scores toward a baseline — active chatters decay slowly, silent ones fast. **Member impressions & tags** (v1.22) give 小千 a one-line "who this person is" profile per member, injected into the prompt.

Domain terms, config keys, commands, prompts, and code comments are **Chinese**; code identifiers are English. Match this convention.

## Commands

No build step and no third-party runtime deps — only the Python standard library plus the AstrBot framework (imported as `astrbot.api.*`). There is no `requirements.txt`, `pyproject.toml`, lint, or type-check config. The only extra is **Pillow** for rank-image rendering: `commands.build_rank_image` imports `api/rank_image.py` locally (and that module does `from PIL import …` at its top), so the plugin loads fine without Pillow — only rendering an image needs it.

```bash
pip install pytest                 # only test dependency
pytest tests/ -v                   # run the full suite (count per pytest output)
pytest tests/test_core.py::TestFavorService -v          # one test class
pytest tests/test_core.py::TestMigration::test_v1_to_v2_round_trip -v   # one test
python3 - <<'EOF'                  # AST check: no local var used before assignment in __init__ (guards the #31/#35 bug class)
import ast, sys
tree = ast.parse(open('main.py', encoding='utf-8').read())
init = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '__init__')
assigned = {}
for node in ast.walk(init):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name): assigned.setdefault(t.id, node.lineno)
bad = [n.id for n in ast.walk(init) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
       and n.id in assigned and assigned[n.id] > n.lineno]
sys.exit("ORDER BUG: %s" % bad if bad else 0)
EOF
```

**Run the AST check before every commit that touches `main.py`** — the #31 and #35 production outages were both UnboundLocalError from config dicts defined after their use site in `__init__`.

Tests are hermetic: they exercise only `core/` and `storage/` (plus `FavorService` and `InjectService`, which transitively import only those), so **they run without AstrBot installed**. `tests/test_core.py` inserts the plugin's parent directory onto `sys.path` and imports as the `astrbot_plugin_xinxian` package, so invoke pytest from anywhere. Do not import `services/judge_service`, `api/`, or `main.py` in tests — those pull in `astrbot` and break hermeticity. (Don't hardcode test counts in docs — they drift every release. On the dev host, `/usr/bin/python3` is PEP-668 managed: `pip3 install pytest --break-system-packages` once, then `python3 -m pytest tests/ -q`.)

The plugin has no standalone runner. To run it live, clone into `AstrBot/data/plugins/astrbot_plugin_xinxian/` and restart AstrBot. All persisted data lives in `data/plugin_data/astrbot_plugin_xinxian/xinxian.db` (SQLite, local only).

## Architecture

Strict one-way layering: `main.py` → `api/` → `services/` → `core/` + `storage/`. `core/` depends on nothing outer and is the unit-testable domain core.

**`main.py` is the composition root and the *only* place `@filter` hooks live.** AstrBot scans `@filter` decorators only on `Star` subclass instance methods, so `XinxianPlugin` carries thin shell methods (`_on_group_msg`, `_on_llm_req`, `_cmd_*`, `_tool_*`) that just forward to pure handler functions in `api/`. When adding a command/listener/LLM-tool, add the logic as a handler in `api/` and a one-line shell method here — do not put logic in the shell. The constructor wires the full graph in order: `storage → levels → services → Deps → facade → PageApi`. There is **no self-query command** (e.g. `/好感度`); the personal-query command was deliberately removed and all querying is now the all-group ranking image. `README.md` still lists it — stale.

**`services/favor_service.py::FavorService` is the single chokepoint for every numeric change.** All mutations funnel through `_apply_one`, which enforces the anti-abuse layers in order: (1) bidirectional daily cap (`daily_cap_up`/`daily_cap_down` on net daily gain, day boundary per the `timezone` config — default `Asia/Shanghai` since v1.26.5), (2) min/max clamp at the storage write. `default_favor` is honored on first write (`apply_delta(default_favor=...)`; `touch_nickname` pre-creates the row at `default_favor`). Entry points: `apply_judge` (the LLM engine — its cooldown lives in `JudgeService`, passed `cooldown_key=None`; runs the economy layer first), `change` (API/admin, still daily-capped), `set_favor` (admin set, bypasses daily cap entirely, hard-clamps to range), and `undo_preview`/`undo_log` (dashboard undo — reverse-delta via `set_favor` with `source="undo"`, marks the original log row `reversed`). `set_relationship` and `touch_nickname` write only their own column and never touch favor. If you add a new way to change favor, go through one of these.

**One favor engine runs per group message** (`api/listeners.py::on_group_message`): `JudgeService.judge` → `apply_judge` (the rule engine was removed in v1.21 — all automatic change is LLM-judged). Since v1.26.1 the whole judge+apply+nickname path runs in a **background `asyncio.create_task`** — AstrBot's pipeline awaits each stage serially (`pipeline/scheduler.py::_process_stages`), and a synchronous judge LLM call would block the reply for the very messages that trigger it (@/reply). Judging is a post-hoc score: it only affects the *next* message's injection, so the current message must not wait for it. Event-derived values (group/user/nick/text/chain flags/umo) are extracted **before** scheduling the task; the task body is fail-silent (exception → warning log only). The judge engine is **fail-silent by design** — any provider/parse/cooldown miss returns `None` and never affects the conversation. The per-user cooldown is **reserved via `touch_event` before the provider call** (the check and a post-call touch straddle real LLM I/O — a race window for concurrent messages); the trade-off is that failed evaluations also consume the cooldown window. Identity is always taken from the event (`get_group_id`/`get_sender_id`/`get_self_id`); never parse identity out of injected prompt text.

**`core/decimal.py` is the single source of truth for one-decimal rounding and display.** The whole float-drift control strategy is: call `round1()` at *every write boundary* (storage `apply_delta`/`set_value`, `set_favor`, inside `_cap_by_daily`, and the `daily_gain` read). Several tests (`test_decimal_accumulation`, `test_clamped_not_tripped_by_float_noise`, `test_daily_cap_decimal`) exist specifically to catch regressions where a missing `round1` lets IEEE-754 noise flip the `clamped` flag. When touching any code path that computes a delta, preserved value, or cap remainder, keep applying `round1` at the boundary. `fmt()` renders integers without a trailing `.0`.

**Time decay** (`core/decay.py`, config `decay.*`, off by default) follows the **exponential forgetting curve with consolidation**: `effective = baseline + (stored−baseline)·0.5^(idle_days/h)` — Ebbinghaus-shaped decay applied *continuously* (no grace-period cliff; even active chatters decay, just slowly). The half-life `h` is per-member (schema v8 column `half_life`, default 10 days) and **consolidates SM-2 style**: every positive delta write multiplies h by `half_life_growth` (default 1.3) up to `half_life_max` (default 60) — a daily chatter's h grows to the cap and loses only ~1.1%/day, a silent newcomer at h=10 loses ~6.6%/day. Negative/neutral deltas never reduce h (an offense doesn't erase "knowing you"). Read/write asymmetry is load-bearing: on **read** (`FavorService._effective`, needs the record's `half_life`) the *effective* value is shown but the stored value is untouched; on **write** (`SQLiteBackend.apply_delta`, inside the lock) the stored value is first decayed to "now", the new delta added, and — if positive — the consolidated h persisted in the same statement. Never add a write path that skips this pre-decay. Admin `set_favor` does not consolidate h.

**Relationships** (`core/relationship.py`, config `relationship.*`, off by default) tag each member with a role key (`friend`/`soulmate`/`lover`/`family`/`disliked`, plus any custom label). Stored in `favor.relationship` (v4 column), they are **orthogonal to favor** — warmth is the number, role is the label. `RelationshipTable.resolve` maps a stored key → `(label, guidance)`; unknown values become custom labels with a generic guidance. The role feeds two surfaces: an injected "你们的关系" block, and the dashboard/rank-image display label.

**Storage is backend-abstracted.** `services` depend only on `storage/base.py::StorageBackend` (ABC). `SQLiteBackend` is the default: synchronous `sqlite3` with `check_same_thread=False`, a module-level `threading.Lock`, WAL, and `busy_timeout`; read-modify-write happens atomically inside the lock. To add a backend (JSON/Redis/…), implement the ABC and swap the one line in `main.py` that constructs `SQLiteBackend` — upper layers are agnostic.

**Schema evolution uses `PRAGMA user_version`** (`storage/migrations.py`; current `SCHEMA_VERSION = 8`). To add a version: append a DDL/DML list to `MIGRATIONS`, bump `SCHEMA_VERSION`, AND add a branch to `storage/pragma_version.py` (`set_user_version` uses fixed literal per-version branches because PRAGMA cannot be parameterized). Each version runs in its own transaction; note the explicit `BEGIN` is mandatory because a version's first statement may be DDL, which would otherwise auto-commit and defeat rollback. v2 (INTEGER→REAL affinity for negatives/decimals, via the build-new-table/copy/drop/rename rebuild) is the worked example of a non-trivial migration. The pattern for adding a per-member field is: new `ALTER TABLE … ADD COLUMN` migration → add the field to `FavorRecord` → thread it through `SELECT`/`INSERT … ON CONFLICT` in `SQLiteBackend` → surface it. (See v4 `relationship` and v5 `nickname`.) `favor_log` (v3) is the change journal that powers both the dashboard and injected "近期印象"; v6 added `message` (triggering speech, judge path only) and `reversed` (undo flag); v7 added the per-member impression triple (`impression` text, `tags` JSON-array string, `impression_at`); v8 added `half_life` (per-member decay half-life in days, default 10).

**Prompt injection** (`services/inject_service.py`) renders one block from a template and **appends** it to `system_prompt` — never overwrites, so it coexists with other injecting plugins. The template placeholders are `{nickname}` `{user_id}` `{master_line}` `{favor}` `{max_favor}` `{level_name}` `{level_guidance}` `{disclosure}` `{recent_events}` `{relationship}`. `{disclosure}` (self-disclosure pacing per level, `core/levels.py::DEFAULT_DISCLOSURE`, config `levels.*.disclosure`, SPT-grounded: deeper level = deeper self-disclosure), `{recent_events}` and `{relationship}` render to empty strings when there is nothing to show, so a template *without* those placeholders simply disables those features. `recent_events` comes from `FavorService.recent_events` (last `memory_count` changes within `memory_days`; significant events with |delta| ≥ `memory_sig_threshold` get their window multiplied by `memory_sig_window_mult` — MemoryBank-style importance weighting; reversed rows are never injected). If `persona_anchor` is non-empty, it is appended after the block (anti-out-of-character anchor); it does not move into the template.

**Rank image + dashboard** are the two read surfaces, both driven by `FavorService.standings` (effective favor, level, relationship, idle days, decayed flag). `api/rank_image.py::render_ranking` is pure PIL: a column-major grid (top-left highest, down then next column), querier highlighted, 千咲 color palette, CJK font auto-probed from a candidate list with PIL-default fallback. `api/page_api.py::PageApi` registers four GET handlers on AstrBot's **main dashboard** via `context.register_web_api` (logs / groups / users / undo; served by the framework on its own port, auth inherited — no standalone server/port/auth in this plugin); the frontend is `pages/dashboard/` (Vue 3 via CDN, mounted through the AstrBotPluginPage bridge). `register_web_api` or Quart may be absent in older frameworks — `PageApi.register` fails silent.

## Cross-plugin API and config

**`api/facade.py::XinxianFacade`** is the stable public API for other AstrBot plugins. It is constructed as `self.api` on the Star instance and accessed via `context.get_registered_star("astrbot_plugin_xinxian").star_cls.api`. Its contract is **additive only — never rename or change a method signature**, only add new ones. Current methods: `get_favor`, `get_level`, `add_favor` (daily-capped), `set_favor`, `get_ranking`, `is_master`, `get_relationship`, `set_relationship`.

`_conf_schema.json` defines all WebUI-editable config (read defaults/types there; don't hardcode). `core/levels.py::LevelTable.from_config` and `core/relationship.py::RelationshipTable.from_config` each fall back to built-in defaults for any missing key, so partial config is always valid. Note the `_LEVEL_KEYS` map translates the Chinese level names (厌恶/陌生/…) to their pinyin config keys (yanwu/mosheng/…).

## Development workflow

1. Branch from `main` using the repo's convention: `feat/<scope>-<topic>` / `fix/<topic>` / `chore/<topic>` (see history: `feat/webui-chisaki-theme`, `fix/undo-get-modal`).
2. Make changes, run `pytest tests/ -q` (must stay green), commit with conventional-style Chinese summaries (`feat(webui): …`, `fix(inject): …`).
3. Push the branch and open a PR to `main` (title mirrors the branch intent, e.g. "feat(rank-image): …"), then **STOP — do not merge it**. The owner (yiwuerxin) reviews and merges every PR; merging is never automated, never via API. Report the PR URL and wait.
4. Bump `metadata.yaml` `version` on release commits (`chore(release): vX.Y.Z`), keeping the `@register(...)` string in `main.py` in sync (both read `1.23.0` since #36).

Host-specific details — working-copy/production directory layout, deploy procedure, credentials location, network quirks, and the current pending-deploy state — live in **`CLAUDE.local.md`**, which is gitignored. **Never commit that file or anything from it.**

An untracked `.mimosa/` directory (security-scan artifacts) may exist — leave it out of commits.

## Production review standard — this repo is public

This plugin runs in production for many users. Every PR is reviewed against this standard before merge; the reviewer (human or agent) must verify each item and state the result in the PR.

### 1. Privacy & data (the hard rules)

- **No real user data in any commit or PR text**: QQ numbers, nicknames/外号, group IDs, chat excerpts, real favor values, real judge reasons/impressions. Examples in README / `_conf_schema.json` / code comments / tests must use obvious placeholders (`123456789`, `阿狸`, round numbers like `50.0`). PR titles/bodies read like a product changelog: behavior changes only, no production numbers or scenes.
- **Credentials & infra never in the repo**: tokens, host paths, server/container layout, deploy runbooks, production state. These belong in `CLAUDE.local.md` only (gitignored).
- **Data-flow inventory (what a new feature must answer)**: what user data does it touch (message text? QQ? nickname?), where does it go (local SQLite only ⇄ sent to an LLM provider ⇄ rendered into prompts/images), and is it minimal? New columns/fields storing message content must be justified — `favor_log.message` (v6) stores at most a 200-char excerpt, judge path only; keep that bound for anything similar.
- **LLM egress is the only external flow**: message text goes to the configured judge provider (and nothing else). Any new feature that sends data to a third party beyond the configured provider is rejected outright. No telemetry, no phone-home, no fetch to any hardcoded URL.
- **Local-only storage**: everything persists in `data/plugin_data/astrbot_plugin_xinxian/xinxian.db`; the dashboard inherits AstrBot's own auth — never add a standalone server/port/credentials.

### 2. Correctness & robustness (production-grade code)

- `pytest tests/ -q` green (count grows with the change; new logic needs new tests, not just "didn't break"), plus the AST `__init__` order check when `main.py` changes (#31/#35 lineage).
- Fail-silent is the design for all enhancement paths (judge, impressions, decay): any provider/parse/storage failure must degrade to "no change", never raise into the chat pipeline. Background tasks hold references (`asyncio.create_task` result kept in a set — bare tasks can be GC'd mid-flight).
- Concurrency: the storage layer has a module lock; new write paths must go through `FavorService._apply_one`/existing backend methods — no ad-hoc `sqlite3` calls. Judge cooldown reserves via `touch_event` *before* the LLM call.
- Config never trusted blind: numeric configs clamped to sane ranges (see the half-life fuses), missing keys fall back to defaults, `from_config` must accept partial config.
- Resource reads (`resources/prompts/`, fonts) stay inside the plugin dir; SQL stays parameterized (no f-string SQL with user input — `query_logs`' LIKE on user_id is internal-use only, never fed raw user text).

### 3. External PRs (from outside contributors)

Review every diff hunk against §1 and §2 — do not trust the PR body's claims; verify locally (`git fetch pull/N/head` → run tests → probe the claimed bug on main). Rebase onto latest main resolving conflicts yourself when needed (force-push to the PR branch is authorized; state what the rebase did in a PR comment). Version numbers: coordinator bumps if colliding with an already-merged release.

### 4. Release & deploy discipline

- `metadata.yaml` and `@register()` version strings bumped together, same PR as the code. Version follows Conventional Commits: `fix:` → patch, `feat:` → minor, `feat!`/`BREAKING CHANGE` → major.
- PR self-check after opening: grep the PR body for real identifiers (QQ/nicknames/groups/values) and PATCH if any leak.
- Deploy only after owner merge: tar-sync code files (excluding `.git`/caches/`tokens.txt`/`CLAUDE.local.md`), reload plugin via dashboard API, verify the loaded version in logs, never touch `xinxian.db*`.

### 5. Commit & PR writing standard (industry norms, applies to external PRs too)

**Commit message** — Conventional Commits 1.0.0 (`conventionalcommits.org`; colloquially "Angular convention" — Angular is an adopter, not the author):
- Format `<type>(<scope>): <summary>`. Types: `build/ci/docs/feat/fix/perf/refactor/test` (feat = new feature, fix = bug fix, per spec). Scope optional (module name, e.g. `inject`, `webui`, `economy`).
- Summary: imperative, no trailing period, short — ≤72 chars is the hard line (GitHub truncates; ~50 preferred, a rule of thumb not a law). Chinese summaries carry more per char, so the practical bar is "one line, verb-first".
- Breaking changes: `feat!:`/`fix!:` before the colon, or a `BREAKING CHANGE:` footer.
- Body (optional but expected for non-trivial commits): the *why* — what was wrong before, the reasoning, side effects. Wrap at 72 cols (git never auto-wraps). Do NOT list files or per-file changes (the diff shows that). NEVER just "fix bug"/"update"/"修改代码".
- Name specific files ONLY for: file moves/renames (git shows those as delete+add — spell out "moved X to Y"), project-wide config changes (dependency versions, env vars), and external API/interface definitions.

**PR description** — four sections, reviewer-facing:
1. **改动简述** — one or two sentences, user-perceivable changes only (this doubles as the App-style changelog entry). No internal implementation details, no development narrative, no maintainer notes.
2. **为什么改（背景）** — the business problem or user-facing bug, not the code walkthrough (Google eng-practices: code shows *what*, the description must carry *why*).
3. **核心改动** — only the 1–2 pivotal files or risk points worth the reviewer's attention, never the full file list. UI changes require before/after screenshots or a short screen recording.
4. **测试情况** — what was verified.
- Self-test before submitting: a reviewer should grasp the change from the description alone without opening Files changed. If "优化" is all it says, it fails.

## Conventions to preserve

- Injection is **append-only** to `system_prompt`; master identity trusts QQ number only, never nickname (`core/identity.py`); master status is a text overlay in the injected profile plus a **dedicated per-level guidance set** (`core/levels.py::DEFAULT_MASTER_GUIDANCE`, config `levels.*.master_guidance`): when `is_master`, `LevelTable.guidance_of(favor, master=True)` swaps the 态度指引 line to master semantics — the split follows the sign, NOT the band: negative favor = 闹别扭 (grudge, not outsider-style厌恶), positive favor = normal positive relationship at varying closeness (生分→温和亲近→撒娇→黏人→依恋). Never frame positive low bands as conflict (别扭/冷战/和好). `InjectService.build_block` passes `is_master` through to `guidance_of`.
- The judge protocol (v1.21) is **tier-anchored free-scoring**: the model outputs a tier (`敌意/冷淡/中性/友好/热情`), its own numeric `分值`, and a mandatory verbatim `证据:` quote for any non-neutral tier. The score IS the model's judgment; the tier only bounds it — `core/judge_parse.py::parse` clamps the score into the tier's window (each tier's anchor in `judge.attitude_deltas` is its boundary: 友好 ≤0.6, 热情 ≥1.8, etc.), corrects direction mismatches to the tier's anchor, and force-demotes evidence-less non-neutral verdicts to neutral. Legacy `态度:/分值:` output is auto-mapped for backward compat; `max_abs_delta` is the global ceiling. The score ranges printed in the prompt templates are **rendered from the same anchors** (`core/judge_prompt.py::tier_ranges_line` → `{tier_ranges}`) — never hardcode tier ranges in prompt text; adjacent anchors form continuous windows (敌意 [敌意锚, 冷淡锚) … 热情 [热情锚, \|max_abs\|]). `narrative_reason` forces the session (main) model and switches to `judge_prompt_narrative.txt` (adds a `理由:` line); otherwise a separate cheap `provider_id` (or session fallback) is used. `context_window` pulls recent text-only conversation turns into the judge call. **The judge prompt follows the AstrBot persona**: `JudgeService._persona_ctx` resolves the session's effective persona (conv.persona_id → `persona_manager.resolve_selected_persona`, same source as the main chain) per evaluation; templates get `{persona_name}` and `{persona_block}` (persona summary, ≤500 chars, empty-safe) via `core/judge_prompt.py::render`. Legacy custom templates containing only `{text}` keep working (`judge.follow_persona` = false pins it to `judge.bot_name`).
- **Member impressions & tags** (v1.22, `core/impression.py` + `impression.*` config): every N effective judge deltas (default 8) `FavorService.maybe_refresh_impression` fires a background task that summarizes that member's last 20 judge log rows via one cheap LLM call (borrowing `JudgeService._resolve_provider`/`_bot_name` through `bind_summarizer`) into a one-line impression (≤80 chars) + ≤3 tags; deterministic `stats_tags` (常客/夜猫子/热情/毒舌) supplement LLM tags. Stored in the v7 columns, injected as `{impression}` (「TA 给你的印象」 block, empty-safe) in the inject template, shown in the WebUI users table + member-detail modal (`member`/`refresh_impression` endpoints) and editable via `/印象设置` (tags) / `/印象刷新` (immediate). All failures are silent — impressions are an enhancement, never a dependency.
- **Judge context extraction** (`core/judge_context.py::extract_history_text`): AstrBot 4.26 conversation history stores assistant replies as structured lists (`[{type:'think'|'text',...}]`); only `type=='text'` segments are extracted (think/images/tool calls skipped). `judge.roster` (free text, one mapping per line like `阿狸=123456789（群友外号示例）`) is injected into judge prompts via `{roster}` so the judge can resolve nicknames — set it in config when group members use 外号.
- **Anti-inflation economy** (`core/level_economy.py`, config `economy.*`, on by default, judge path only): noise floor (|delta| < 0.5 → 0), negative weight ×1.5 (negativity bias), stage multipliers on positive deltas only (挚爱 0.2 … 认识 1.0 — social penetration: shallow interactions can't advance deep stages), same-day repeat decay (Nth positive judge of the day × max(0.25, 1-0.25·N)), and **trust repair window** (v1.26: after a judge delta ≤ -`repair_threshold` (2.0), positives are ×`repair_factor` (0.5) for `repair_hours` (48h) — trust is destroyed fast and rebuilt slow; reversed offenses don't trigger it; `FavorService._in_repair_window` reads the log). Applied inside `FavorService.apply_judge` before the daily cap; zeroed deltas produce no log row. Cross-plugin `change` bypasses this layer. `daily_cap_up` default 4 / `daily_cap_down` 8. The rule engine (daily_first bonus) was **removed in v1.21** — `core/events.py`, `apply_rules`, `is_first_today`, and the `rules.*` config section are gone; favor now changes only via judge/API/admin/undo.
- `text_wake` lets a plain (non-`/`) message trigger the ranking image when it exactly matches a configured phrase; the `_xinxian_cmd_done` flag on the event prevents the `/` command and the wake path from both firing.
- Prompt templates live in `resources/prompts/` and are loaded once via `_read_resource` in `main.py`; a non-empty `inject.template` config overrides the bundled `inject_template.txt`.

## Version history & current state (2026-08-24)

| Version | PR | What |
|---|---|---|
| 1.19.0 | #29 | judge prompt follows AstrBot persona (`{persona_name}`/`{persona_block}`); PRAGMA whitelist hardening |
| 1.20.0 | #30 | five-tier free-scored judge + anti-inflation economy (noise floor / negative weight / stage multipliers / same-day decay); caps 4/8 |
| 1.21.0 | #33 | rule engine **removed** (daily_first gone); tier-anchored free scoring with evidence gate |
| 1.22.0 | #34 | member impressions & tags (schema v7, WebUI impression column + member modal + `/印象设置` `/印象刷新`); judge context extraction fix (4.26 structured history); `judge.roster` nickname map |
| 1.23.0 | #36 | Ebbinghaus-style decay: `effective = baseline + (stored−baseline)·0.5^(idle/h)`, per-member half-life (schema v8, base 10d, ×1.3 per positive interaction, max 60d) |
| 1.24.0 | #40 | per-level `master_guidance` (负好感＝闹别扭 semantics) |
| 1.24.1 | #41 | master guidance semantics fix for low-positive band |
| 1.25.0 | #42 | significance-weighted memory (`memory_sig_*`), disclosure ladder (`disclosure`), emotional mirroring line in inject template |
| 1.26.0 | #43 | trust repair window (major offense ⇒ positives ×0.5 for 48h) |
| 1.26.1 | #45 | judge+apply moved to a background `asyncio.create_task` — no longer blocks the reply to the triggering message |
| 1.26.2 | #44 | robustness batch: prompt tier ranges single-sourced from anchors (敌意 window mismatch fixed); judge cooldown reserved **before** the LLM call; impression refresh via JudgeService public API + held task refs; `default_favor` honored on first write; half-life fuse + config clamps; `timezone` config for day boundaries; rank image falls back to text without Pillow |
| 1.26.3 | #46 | `favor_log` message/reason truncated to 200 chars at write; production review standard added to this file |
| 1.26.4 | #47 | WebUI member-table row-border misalignment fix |
| 1.26.5 | #49 | daily-boundary timezone defaults to 东八区 (`Asia/Shanghai`) — no config needed |
| — | #37 | docs sync: README 目录/测试数对齐，CLAUDE 版本历史与待办 |

Hotfix lineage: #31 and #35 were identical `UnboundLocalError` production outages (config dict used before definition in `__init__`) — hence the mandatory AST check above.

Current pending state (deploy plans, production config) lives in `CLAUDE.local.md` — not in this file, which is public.
