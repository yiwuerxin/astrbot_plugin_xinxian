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
    const SOURCE_LABEL = { rule: "规则", judge: "评估", admin: "管理员", tool: "工具", api: "API", set: "设定" };
    const sourceLabel = (s) => SOURCE_LABEL[s] || s || "-";

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
      fetchLogs, fetchUsers, refresh, switchTab,
    };
  },
  template: `
    <div class="xx-page">
      <header class="xx-header">
        <h2>心弦 · 好感度面板</h2>
        <nav class="xx-tabs">
          <button class="xx-tab" :class="{ active: tab === 'users' }" @click="switchTab('users')">当前总览</button>
          <button class="xx-tab" :class="{ active: tab === 'logs' }" @click="switchTab('logs')">变动流水</button>
        </nav>
      </header>

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
              <td class="xx-mono">
                {{ fmtNum(u.favor) }}
                <span v-if="u.decayed" class="xx-tag" :title="'原 ' + fmtNum(u.stored_favor)">衰减</span>
              </td>
              <td>{{ u.level }}</td>
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
