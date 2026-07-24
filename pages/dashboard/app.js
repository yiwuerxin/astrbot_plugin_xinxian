const { createApp, ref, onMounted } = Vue;

createApp({
  setup() {
    const bridge = window.AstrBotPluginPage;
    const loading = ref(false);
    const errorMsg = ref("");
    const logs = ref([]);
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
    const SOURCE_LABEL = { rule: "规则", judge: "评估", admin: "管理员", tool: "工具", api: "API", set: "设定" };
    const sourceLabel = (s) => SOURCE_LABEL[s] || s || "-";

    const fetchGroups = async () => {
      try {
        const data = await bridge.apiGet("groups");
        groups.value = (data && data.groups) || [];
      } catch (e) {
        /* 群列表非关键，忽略 */
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

    onMounted(async () => {
      try {
        await bridge?.ready?.();
      } catch (e) {
        /* ignore */
      }
      await fetchGroups();
      await fetchLogs();
    });

    return {
      loading, errorMsg, logs, groups,
      filterGroup, filterUser, filterLimit,
      fmtTime, fmtNum, fmtDelta, sourceLabel, fetchLogs,
    };
  },
  template: `
    <div class="xx-page">
      <header class="xx-header">
        <h2>心弦 · 好感度变动记录</h2>
        <div class="xx-filters">
          <select v-model="filterGroup" @change="fetchLogs">
            <option value="">全部群</option>
            <option v-for="g in groups" :key="g.group_id" :value="g.group_id">
              {{ g.group_id }}（{{ g.count }}）
            </option>
          </select>
          <input class="xx-input" v-model="filterUser" placeholder="按 QQ 号筛选" @keyup.enter="fetchLogs">
          <select v-model="filterLimit" @change="fetchLogs">
            <option :value="100">最近 100</option>
            <option :value="300">最近 300</option>
            <option :value="1000">最近 1000</option>
          </select>
          <button class="xx-btn" @click="fetchLogs" :disabled="loading">
            {{ loading ? "加载中…" : "刷新" }}
          </button>
        </div>
      </header>

      <p v-if="errorMsg" class="xx-error">{{ errorMsg }}</p>

      <div class="xx-table-wrap">
        <table class="xx-table">
          <thead>
            <tr>
              <th>时间</th><th>群</th><th>QQ</th><th>增减</th><th>变化</th><th>原因</th><th>来源</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!logs.length">
              <td colspan="7" class="xx-empty">{{ loading ? "加载中…" : "暂无记录" }}</td>
            </tr>
            <tr v-for="r in logs" :key="r.id">
              <td class="xx-mono">{{ fmtTime(r.ts) }}</td>
              <td class="xx-mono">{{ r.group_id }}</td>
              <td class="xx-mono">{{ r.user_id }}</td>
              <td :class="r.delta > 0 ? 'xx-up' : (r.delta < 0 ? 'xx-down' : 'xx-zero')">{{ fmtDelta(r.delta) }}</td>
              <td class="xx-mono">{{ fmtNum(r.favor_before) }} → {{ fmtNum(r.favor_after) }}</td>
              <td>{{ r.reason || "-" }}</td>
              <td><span class="xx-tag">{{ sourceLabel(r.source) }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `,
}).mount("#app");
