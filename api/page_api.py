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

    def __init__(self, favor, storage) -> None:
        self._favor = favor
        self._storage = storage

    def register(self, context) -> None:
        """注册原生 Web API；框架不支持（无 register_web_api 或无 Flask）时静默跳过。"""
        reg = getattr(context, "register_web_api", None)
        if reg is None or jsonify is None:
            return
        reg(f"/{PLUGIN_NAME}/logs", self.handle_logs, ["GET"], "心弦 好感度变动记录")
        reg(f"/{PLUGIN_NAME}/groups", self.handle_groups, ["GET"], "心弦 有记录的群列表")
        reg(f"/{PLUGIN_NAME}/users", self.handle_users, ["GET"], "心弦 当前好感总览")

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
        """有变动记录的群列表（供前端群筛选下拉）。"""
        try:
            rows = await self._storage.query_logs(limit=1000)
            seen: dict[str, int] = {}
            for r in rows:
                seen[r["group_id"]] = seen.get(r["group_id"], 0) + 1
            groups = [{"group_id": g, "count": c} for g, c in sorted(seen.items())]
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
