"""心弦好感度 - 插件页面 Web API（AstrBot 原生 dashboard）。

通过 ``context.register_web_api`` 注册到 AstrBot 主面板（端口 6185），
在 ``/#/plugin-page/astrbot_plugin_xinxian/dashboard`` 内嵌页面里被前端调用。
handler 使用 Flask 的 ``jsonify`` / ``request``；鉴权由主面板继承，插件无需自行处理。
"""

from __future__ import annotations

PLUGIN_NAME = "astrbot_plugin_xinxian"

try:  # dashboard 服务基于 Quart（异步 Flask），用 quart 的 jsonify/request
    from quart import jsonify, request
except ImportError:  # pragma: no cover
    jsonify = None
    request = None


class PageApi:
    """心弦 dashboard 后端 API。"""

    def __init__(self, favor, storage, impressions=None) -> None:
        self._favor = favor
        self._storage = storage
        self._impressions = impressions

    def register(self, context) -> None:
        """注册原生 Web API；框架不支持（无 register_web_api 或无 Flask）时静默跳过。"""
        reg = getattr(context, "register_web_api", None)
        if reg is None or jsonify is None:
            return
        reg(f"/{PLUGIN_NAME}/logs", self.handle_logs, ["GET"], "心弦 好感度变动记录")
        reg(f"/{PLUGIN_NAME}/groups", self.handle_groups, ["GET"], "心弦 有记录的群列表")
        reg(f"/{PLUGIN_NAME}/users", self.handle_users, ["GET"], "心弦 当前好感总览")
        reg(f"/{PLUGIN_NAME}/undo", self.handle_undo, ["GET"], "心弦 撤销/预览一次变动")
        reg(f"/{PLUGIN_NAME}/member", self.handle_member, ["GET"], "心弦 成员详情（印象/标签/近期评审）")
        reg(f"/{PLUGIN_NAME}/refresh_impression", self.handle_refresh_impression, ["GET"], "心弦 立即刷新成员印象")

    # ---------------- handlers ----------------

    async def handle_logs(self):
        """变动流水：?group_id=&user_id=&limit=&offset=，按时间倒序。"""
        try:
            group_id = (request.args.get("group_id") or "").strip() or None
            user_id = (request.args.get("user_id") or "").strip() or None
            limit = max(1, min(int(request.args.get("limit", 300)), 1000))
            offset = max(0, int(request.args.get("offset", 0)))
            logs = await self._storage.query_logs(group_id, user_id, limit, offset)
            return jsonify({"success": True, "logs": logs, "count": len(logs)})
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "error": str(e)})

    async def handle_groups(self):
        """有当前好感记录的群列表（含每群人数，供前端群筛选下拉）。"""
        try:
            groups = await self._storage.distinct_groups()
            return jsonify({"success": True, "groups": groups})
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "error": str(e)})

    async def handle_users(self):
        """当前总览：?group_id=&limit=，按有效好感降序。"""
        try:
            group_id = (request.args.get("group_id") or "").strip() or None
            limit = max(1, min(int(request.args.get("limit", 500)), 2000))
            users = await self._favor.standings(group_id, limit)
            return jsonify({"success": True, "users": users, "count": len(users)})
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "error": str(e)})

    async def handle_member(self):
        """成员详情：?group_id=&user_id= → 基础信息 + 印象/标签 + 最近 judge 理由。"""
        try:
            group_id = (request.args.get("group_id") or "").strip()
            user_id = (request.args.get("user_id") or "").strip()
            if not group_id or not user_id:
                return jsonify({"success": False, "error": "缺少 group_id/user_id"})
            rec = await self._favor.get(group_id, user_id)
            logs = await self._storage.query_logs(group_id, user_id, limit=30)
            judged = [r for r in logs if r.get("source") == "judge"][:10]
            return jsonify({"success": True, "member": {
                "group_id": group_id,
                "user_id": user_id,
                "nickname": rec.nickname or "",
                "favor": rec.favor,
                "level": self._favor.level_of(rec.favor).name,
                "relationship": self._favor.relationship_label(rec.relationship) if rec.relationship else "",
                "is_master": self._favor.is_master(user_id),
                "impression": rec.impression or "",
                "tags": rec.parsed_tags(),
                "impression_at": rec.impression_at,
                "recent_judges": [
                    {"ts": r.get("ts"), "delta": r.get("delta"),
                     "reason": r.get("reason") or "", "message": r.get("message") or ""}
                    for r in judged
                ],
            }})
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "error": str(e)})

    async def handle_refresh_impression(self):
        """立即刷新成员印象：?group_id=&user_id=（与 undo 同为 GET 副作用风格）。"""
        try:
            group_id = (request.args.get("group_id") or "").strip()
            user_id = (request.args.get("user_id") or "").strip()
            if not group_id or not user_id:
                return jsonify({"success": False, "error": "缺少 group_id/user_id"})
            if self._impressions is None:
                return jsonify({"success": False, "error": "印象功能未启用"})
            ok, msg = await self._impressions.refresh_now(group_id, user_id)
            return jsonify({"success": ok, "message": msg})
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "error": str(e)})

    async def handle_undo(self):
        """撤销/预览：?id=<log_id>&dry=1。dry=1 只预览（返回当前/撤销后好感），否则真撤销。"""
        try:
            log_id = int(request.args.get("id"))
            dry = (request.args.get("dry") or "").strip() == "1"
            if dry:
                info = await self._favor.undo_preview(log_id)
                return jsonify({"success": True, "preview": info})
            rec = await self._favor.undo_log(log_id)
            return jsonify({"success": True, "favor": rec.favor})
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "error": str(e)})
