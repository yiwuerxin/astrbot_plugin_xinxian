const { createApp, ref, computed, onMounted } = Vue;

createApp({
  setup() {
    const bridge = window.AstrBotPluginPage;
    const tab = ref("users");
    const loading = ref(false);
    const errorMsg = ref("");
    const logs = ref([]);
    const users = ref([]);
    const groups = ref([]);
    const filterGroup = ref("");
    const filterUser = ref("");
    const filterLimit = ref(300);

    const pad = (n) => String(n).padStart(2, "0");
    const fmtTime = (ts) => {
      if (!ts) return "-";
      const d = new Date(ts * 1000);
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };
    const fmtNum = (v) => {
      const n = Number(v || 0);
      return Number.isInteger(n) ? String(n) : n.toFixed(1);
    };
    const fmtDelta = (v) => {
      const n = Number(v || 0);
      const s = Number.isInteger(n) ? String(n) : n.toFixed(1);
      return n > 0 ? `+${s}` : s;
    };
    const fmtIdle = (d) =>
      d == null ? "从未" : d <= 0 ? "今天" : d === 1 ? "昨天" : `${d}天前`;
    const fmtUser = (u) => {
      const id = u.user_id;
      const nick = (u.nickname || "").trim();
      if (!nick) return `(${id})`;
      let w = 0, out = "";
      for (const ch of nick) {
        const cw = ch.codePointAt(0) > 0x2e80 ? 2 : 1;
        if (w + cw > 16) { out += "…"; break; }
        out += ch; w += cw;
      }
      return `${out} (${id})`;
    };
    const SOURCE_LABEL = { rule: "规则", judge: "评估", admin: "管理员", tool: "工具", api: "API", set: "设定", undo: "撤销" };
    const sourceLabel = (s) => SOURCE_LABEL[s] || s || "-";

    // 七级好感色温轴：冷蓝紫(厌恶) → 灰 → 玫瑰 → 绯红(挚爱)，与排行图千咲配色同源
    const LEVEL_COLORS = {
      "厌恶": "#5563b5",
      "陌生": "#8f8a96",
      "认识": "#9e7fa8",
      "友好": "#b96b84",
      "亲密": "#cd5067",
      "挚友": "#ac2432",
      "挚爱": "#7f1220",
    };
    const levelColor = (level) => LEVEL_COLORS[level] || "#8f8a96";
    const levelChipStyle = (level) => {
      const c = levelColor(level);
      return { color: c, background: c + "14", borderColor: c + "38" };
    };
    // 好感度色温条：中线为原点，正值向右(暖)、负值向左(冷)，满刻度 ±100
    const barStyle = (u) => {
      const v = Number(u.favor || 0);
      const w = Math.min(Math.abs(v), 100) / 2;
      return {
        width: w + "%",
        background: levelColor(u.level),
        ...(v >= 0 ? { left: "50%" } : { right: "50%" }),
      };
    };

    const undoPreview = ref(null);
    const openUndo = async (r) => {
      errorMsg.value = "";
      try {
        const data = await bridge.apiGet("undo", { id: r.id, dry: "1" });
        if (data && data.success === false) {
          errorMsg.value = data.error || "无法撤销";
          return;
        }
        undoPreview.value = { ...(data.preview || {}), id: r.id };
      } catch (e) {
        errorMsg.value = String(e);
      }
    };
    const closeUndo = () => { undoPreview.value = null; };
    const confirmUndo = async () => {
      const id = undoPreview.value && undoPreview.value.id;
      undoPreview.value = null;
      if (id == null) return;
      errorMsg.value = "";
      try {
        const data = await bridge.apiGet("undo", { id });
        if (data && data.success === false) {
          errorMsg.value = data.error || "撤销失败";
        } else {
          await fetchLogs();
        }
      } catch (e) {
        errorMsg.value = String(e);
      }
    };

    const selectedLog = ref(null);
    const showDetail = (r) => { selectedLog.value = r; };
    const closeDetail = () => { selectedLog.value = null; };

    const fetchGroups = async () => {
      try {
        const data = await bridge.apiGet("groups");
        groups.value = (data && data.groups) || [];
      } catch (e) {
        /* 非关键 */
      }
    };

    const fetchLogs = async () => {
      loading.value = true;
      errorMsg.value = "";
      try {
        const params = { limit: filterLimit.value };
        if (filterGroup.value) params.group_id = filterGroup.value;
        const u = filterUser.value.trim();
        if (u) params.user_id = u;
        const data = await bridge.apiGet("logs", params);
        if (data && data.success === false) {
          errorMsg.value = data.error || "加载失败";
          logs.value = [];
        } else {
          logs.value = (data && data.logs) || [];
        }
      } catch (e) {
        errorMsg.value = String(e);
        logs.value = [];
      } finally {
        loading.value = false;
      }
    };

    const fetchUsers = async () => {
      loading.value = true;
      errorMsg.value = "";
      try {
        const params = { limit: 1000 };
        if (filterGroup.value) params.group_id = filterGroup.value;
        const data = await bridge.apiGet("users", params);
        if (data && data.success === false) {
          errorMsg.value = data.error || "加载失败";
          users.value = [];
        } else {
          users.value = (data && data.users) || [];
        }
      } catch (e) {
        errorMsg.value = String(e);
        users.value = [];
      } finally {
        loading.value = false;
      }
    };

    const visibleUsers = computed(() => {
      const q = filterUser.value.trim();
      return q ? users.value.filter((u) => u.user_id.indexOf(q) !== -1) : users.value;
    });

    const refresh = () => (tab.value === "logs" ? fetchLogs() : fetchUsers());
    const switchTab = (t) => {
      tab.value = t;
      errorMsg.value = "";
      if (t === "logs" && !logs.value.length) fetchLogs();
      if (t === "users" && !users.value.length) fetchUsers();
    };

    onMounted(async () => {
      try {
        await bridge?.ready?.();
      } catch (e) {
        /* ignore */
      }
      await fetchGroups();
      await fetchUsers();
    });

    return {
      tab, loading, errorMsg, logs, users, visibleUsers, groups,
      filterGroup, filterUser, filterLimit,
      fmtTime, fmtNum, fmtDelta, fmtIdle, fmtUser, sourceLabel,
      levelColor, levelChipStyle, barStyle,
      undoPreview, openUndo, closeUndo, confirmUndo,
      selectedLog, showDetail, closeDetail,
      fetchLogs, fetchUsers, refresh, switchTab,
    };
  },
  template: `
    <div class="xx-page">
      <header class="xx-header">
        <div class="xx-title">
          <h2>心弦 · 好感度面板</h2>
          <p class="xx-sub">小千对每位群友的心意起伏 · 冷暖和远近，一目了然</p>
        </div>
        <nav class="xx-tabs">
          <button class="xx-tab" :class="{ active: tab === 'users' }" @click="switchTab('users')">当前总览</button>
          <button class="xx-tab" :class="{ active: tab === 'logs' }" @click="switchTab('logs')">变动流水</button>
        </nav>
      </header>
      <div class="xx-string"></div>

      <div class="xx-filters">
        <select v-model="filterGroup" @change="refresh">
          <option value="">全部群</option>
          <option v-for="g in groups" :key="g.group_id" :value="g.group_id">
            {{ g.group_id }}（{{ g.count }}）
          </option>
        </select>
        <input class="xx-input" v-model="filterUser" placeholder="按 QQ 号筛选（模糊）" @keyup.enter="tab === 'logs' && fetchLogs()">
        <select v-if="tab === 'logs'" v-model="filterLimit" @change="fetchLogs">
          <option :value="100">最近 100</option>
          <option :value="300">最近 300</option>
          <option :value="1000">最近 1000</option>
        </select>
        <button class="xx-btn" @click="refresh" :disabled="loading">
          {{ loading ? "加载中…" : "刷新" }}
        </button>
      </div>

      <p v-if="errorMsg" class="xx-error">{{ errorMsg }}</p>

      <!-- 当前总览 -->
      <div v-if="tab === 'users'" class="xx-table-wrap">
        <table class="xx-table">
          <thead>
            <tr>
              <th>群</th><th>QQ</th><th>好感(有效)</th><th>等级</th><th>关系</th><th>最近互动</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!visibleUsers.length">
              <td colspan="7" class="xx-empty">{{ loading ? "加载中…" : (users.length ? "无匹配成员" : "暂无成员") }}</td>
            </tr>
            <tr v-for="u in visibleUsers" :key="u.group_id + '_' + u.user_id">
              <td class="xx-mono">{{ u.group_id }}</td>
              <td class="xx-mono">{{ fmtUser(u) }}</td>
              <td>
                <div class="xx-favor">
                  <div class="xx-favor-top">
                    <span class="xx-favor-num" :style="{ color: levelColor(u.level) }">{{ fmtNum(u.favor) }}</span>
                    <span v-if="u.decayed" class="xx-tag" :title="'原 ' + fmtNum(u.stored_favor)">衰减</span>
                  </div>
                  <div class="xx-favor-bar"><i :style="barStyle(u)"></i></div>
                </div>
              </td>
              <td><span class="xx-level-chip" :style="levelChipStyle(u.level)"><i></i>{{ u.level }}</span></td>
              <td>{{ u.relationship || "-" }}</td>
              <td class="xx-mono">{{ fmtIdle(u.idle_days) }}</td>
              <td>
                <span v-if="u.decayed" class="xx-tag">衰减中</span>
                <span v-else-if="u.idle_days != null && u.idle_days >= 3" class="xx-tag">{{ u.idle_days }}天未动</span>
                <span v-else>-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 变动流水 -->
      <div v-if="tab === 'logs'" class="xx-table-wrap">
        <table class="xx-table">
          <thead>
            <tr>
              <th>时间</th><th>群</th><th>QQ</th><th>增减</th><th>变化</th><th>原因</th><th>来源</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!logs.length">
              <td colspan="8" class="xx-empty">{{ loading ? "加载中…" : "暂无记录" }}</td>
            </tr>
            <tr v-for="r in logs" :key="r.id" :class="{ 'xx-reversed': r.reversed }">
              <td class="xx-mono">{{ fmtTime(r.ts) }}</td>
              <td class="xx-mono">{{ r.group_id }}</td>
              <td class="xx-mono">{{ r.user_id }}</td>
              <td :class="r.delta > 0 ? 'xx-up' : (r.delta < 0 ? 'xx-down' : 'xx-zero')">{{ fmtDelta(r.delta) }}</td>
              <td class="xx-mono">{{ fmtNum(r.favor_before) }} → {{ fmtNum(r.favor_after) }}</td>
              <td>{{ r.reason || "-" }}</td>
              <td><span class="xx-tag">{{ sourceLabel(r.source) }}</span></td>
              <td class="xx-actions">
                <button class="xx-btn xx-btn-mini" @click="showDetail(r)">详情</button>
                <span v-if="r.reversed" class="xx-tag xx-muted">已撤销</span>
                <button v-else class="xx-btn xx-btn-undo" @click="openUndo(r)" :disabled="loading">撤销</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 变动详情弹窗：完整发言 + AI 原因 -->
      <div v-if="selectedLog" class="xx-modal" @click.self="closeDetail">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span>变动详情</span>
            <button class="xx-modal-close" @click="closeDetail">×</button>
          </header>
          <div class="xx-modal-meta xx-mono">
            {{ fmtTime(selectedLog.ts) }} · 群 {{ selectedLog.group_id }} · QQ {{ selectedLog.user_id }}
            · <span :class="selectedLog.delta > 0 ? 'xx-up' : (selectedLog.delta < 0 ? 'xx-down' : 'xx-zero')">{{ fmtDelta(selectedLog.delta) }}</span>
            · {{ fmtNum(selectedLog.favor_before) }} → {{ fmtNum(selectedLog.favor_after) }}
            · <span class="xx-tag">{{ sourceLabel(selectedLog.source) }}</span>
          </div>
          <section class="xx-modal-section">
            <h4>用户发言</h4>
            <p class="xx-modal-text">{{ selectedLog.message || "（本次变动非评估触发，无发言记录）" }}</p>
          </section>
          <section class="xx-modal-section">
            <h4>AI 原因</h4>
            <p class="xx-modal-text">{{ selectedLog.reason || "-" }}</p>
          </section>
        </div>
      </div>

      <!-- 撤销确认弹窗：预览撤销后好感 -->
      <div v-if="undoPreview" class="xx-modal" @click.self="closeUndo">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span>撤销这条变动？</span>
            <button class="xx-modal-close" @click="closeUndo">×</button>
          </header>
          <section class="xx-modal-section">
            <p class="xx-modal-text">
              撤销后好感度：<b class="xx-up" style="font-size:16px">{{ fmtNum(undoPreview.after) }}</b>
              <span class="xx-muted-text">（当前 {{ fmtNum(undoPreview.current) }}，本次 {{ fmtDelta(undoPreview.delta) }}）</span>
            </p>
            <p class="xx-modal-text xx-muted-text">原因：{{ undoPreview.reason || "-" }}</p>
          </section>
          <div class="xx-modal-actions">
            <button class="xx-btn xx-btn-mini" @click="closeUndo">取消</button>
            <button class="xx-btn xx-btn-danger" @click="confirmUndo">确认撤销</button>
          </div>
        </div>
      </div>
    </div>
  `,
}).mount("#app");
