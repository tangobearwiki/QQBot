(function () {
  "use strict";

  let bridge = window.AstrBotPluginPage || window.AstrBotPluginView || null;

  function getBridge() {
    return (
      window.AstrBotPluginPage ||
      window.AstrBotPluginView ||
      bridge ||
      null
    );
  }

  function waitForBridge(timeoutMs = 5000) {
    return new Promise((resolve) => {
      const started = Date.now();
      const timer = window.setInterval(() => {
        const found = getBridge();
        if (found || Date.now() - started >= timeoutMs) {
          window.clearInterval(timer);
          resolve(found);
        }
      }, 100);
    });
  }

  function loadLocalBridge() {
    return new Promise((resolve) => {
      if (getBridge()) {
        resolve(true);
        return;
      }
      const script = document.createElement("script");
      script.src = "./bridge.js?fallback=1";
      script.dataset.localBridge = "1";
      script.onload = () => resolve(Boolean(getBridge()));
      script.onerror = () => resolve(false);
      document.head.appendChild(script);
    });
  }
  const state = { hourly: [], recent: [], timer: null, actionTimer: null };

  const STATUS = {
    pending: ["待确认", ""],
    confirmed: ["已确认", "good"],
    playing: ["联机中", "good"],
    completed: ["已完成", "good"],
    expired: ["已超时", "warn"],
    rejected: ["已拒绝", "bad"],
    cancelled: ["已取消", "bad"],
  };

  function $(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const node = $(id);
    if (node) node.textContent = String(value);
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
  }

  function formatTime(value) {
    if (!value) return "-";
    return String(value).replace("T", " ").slice(5, 16);
  }

  function formatWait(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    if (total < 60) return `${total} 秒`;
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return `${minutes} 分`;
    return `${Math.floor(minutes / 60)} 时 ${minutes % 60} 分`;
  }

  function showActionResult(message, ok) {
    const node = $("actionResult");
    if (!node) {
      window.alert(message);
      return;
    }
    node.hidden = false;
    node.textContent = message;
    node.className = `action-result ${ok ? "ok" : "error"}`;
    if (state.actionTimer) window.clearTimeout(state.actionTimer);
    state.actionTimer = window.setTimeout(() => {
      node.hidden = true;
    }, 6000);
  }

  async function adminAction(action, payload) {
    try {
      const body = { action, ...(payload || {}) };
      const bridgeApi = getBridge();
      let result;
      if (bridgeApi && typeof bridgeApi.apiPost === "function") {
        try {
          result = await withTimeout(
            bridgeApi.apiPost("admin", body),
            10000,
            "管理接口超时",
          );
        } catch (error) {
          console.warn("[stardew] bridge admin failed, fallback to fetch", error);
        }
      }
      if (!result) {
        const urls = [
          "/api/v1/plugins/extensions/astrbot_plugin_stardew_match/admin",
          "/api/plug/astrbot_plugin_stardew_match/admin",
        ];
        let lastError = null;
        for (const url of urls) {
          try {
            const response = await fetch(url, {
              method: "POST",
              credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            result = await response.json();
            break;
          } catch (error) {
            lastError = error;
          }
        }
        if (!result && lastError) throw lastError;
      }
      const data = result && result.data ? result.data : result;
      const failed = Boolean(data && data.ok === false);
      const message =
        data && data.message
          ? data.message
          : failed
            ? "操作失败"
            : "操作完成";
      showActionResult(message, !failed);
      await load();
    } catch (error) {
      showActionResult(
        `操作失败：${error && error.message ? error.message : error}`,
        false,
      );
    }
  }

  function withTimeout(promise, ms, message) {
    let timer = null;
    const timeout = new Promise((_, reject) => {
      timer = window.setTimeout(
        () => reject(new Error(message || "请求超时")),
        ms,
      );
    });
    return Promise.race([promise, timeout]).finally(() => {
      if (timer) window.clearTimeout(timer);
    });
  }

  function diagText() {
    return [
      `iframe=${window.parent !== window}`,
      `bridge=${Boolean(getBridge())}`,
      `path=${window.location.pathname}`,
    ].join(" ");
  }

  async function fetchStats() {
    const errors = [];
    const bridgeApi = getBridge();
    if (bridgeApi && typeof bridgeApi.apiGet === "function") {
      try {
        return await withTimeout(
          bridgeApi.apiGet("stats"),
          8000,
          "页面桥接无响应",
        );
      } catch (error) {
        errors.push(`bridge: ${error && error.message ? error.message : error}`);
      }
    }
    const urls = [
      "/api/v1/plugins/extensions/astrbot_plugin_stardew_match/stats",
      "/api/plug/astrbot_plugin_stardew_match/stats",
    ];
    for (const url of urls) {
      try {
        const response = await fetch(url, {
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
      } catch (error) {
        errors.push(
          `${url}: ${error && error.message ? error.message : error}`,
        );
      }
    }
    throw new Error(errors.join(" | "));
  }

  function unwrap(payload) {
    if (payload && payload.data && payload.data.today) return payload.data;
    return payload || {};
  }

  function renderPools(pools) {
    const wrap = $("pools");
    if (!wrap) return;
    wrap.textContent = "";
    const max = Math.max(1, ...pools.map((item) => Number(item.count) || 0));
    pools.forEach((item) => {
      const row = document.createElement("div");
      row.className = "pool-row";

      const name = document.createElement("span");
      name.className = "pool-name";
      name.textContent = item.label || item.pool;

      const track = document.createElement("div");
      track.className = "pool-track";
      const fill = document.createElement("div");
      fill.className = "pool-fill";
      fill.style.width = `${Math.round(((Number(item.count) || 0) / max) * 100)}%`;
      track.appendChild(fill);

      const count = document.createElement("span");
      count.className = "pool-count";
      count.textContent = String(Number(item.count) || 0);

      row.append(name, track, count);
      wrap.appendChild(row);
    });
  }

  function renderRecent(rows) {
    const body = $("recentBody");
    if (!body) return;
    const showClosed = $("showClosed") ? $("showClosed").checked : false;
    const visible = (rows || [])
      .filter(
        (row) =>
          showClosed || !["cancelled", "expired"].includes(row.status),
      )
      .slice(0, 12);
    body.textContent = "";
    if (!visible.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 8;
      td.className = "empty";
      td.textContent = showClosed
        ? "暂无匹配记录"
        : "暂无匹配记录（可勾选上方显示已取消 / 已过期）";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    visible.forEach((row) => {
      const tr = document.createElement("tr");
      const statusInfo = STATUS[row.status] || [row.status, ""];

      const participants = (row.participants || []).map(
        (p) =>
          `${p.nickname || p.qq}（${p.qq}${p.have ? `，${p.have}人` : ""}）`,
      );
      const cells = [
        `#${row.id}`,
        participants.length ? participants.join(" + ") : `${row.player_a} ↔ ${row.player_b}`,
        row.pool || "-",
        row.modded || "-",
        `${row.party_size || "-"} 人`,
      ];
      cells.forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });

      const statusTd = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `status ${row.forced ? "warn" : statusInfo[1]}`.trim();
      badge.textContent = row.forced
        ? `强制 · ${statusInfo[0]}`
        : statusInfo[0];
      statusTd.appendChild(badge);
      tr.appendChild(statusTd);

      const timeTd = document.createElement("td");
      timeTd.textContent = formatTime(row.created_at);
      tr.appendChild(timeTd);

      const actionTd = document.createElement("td");
      if (["pending", "confirmed", "playing"].includes(row.status)) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button small danger";
        button.textContent = "解散";
        button.addEventListener("click", () =>
          row.room_id
            ? adminAction("dissolve_room", { room_id: row.room_id })
            : adminAction("dissolve", { match_id: row.id }),
        );
        actionTd.appendChild(button);
      } else {
        actionTd.textContent = "-";
      }
      tr.appendChild(actionTd);

      body.appendChild(tr);
    });
  }

  function renderRooms(rows) {
    const body = $("roomBody");
    if (!body) return;
    body.textContent = "";
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 7;
      td.className = "empty";
      td.textContent = "暂无开放房间";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      const memberText = (row.members || [])
        .map((m) => `${m.nickname}（${m.qq}，${m.have}人）`)
        .join("、");
      const cells = [
        `#${row.id}`,
        `${row.target} 人`,
        `${row.current}/${row.target}`,
        memberText || "-",
        `${row.missing} 人`,
        formatWait(row.wait_seconds),
      ];
      cells.forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });

      const actionTd = document.createElement("td");
      const conditions = document.createElement("button");
      conditions.type = "button";
      conditions.className = "button small";
      conditions.textContent = "查看条件";
      conditions.addEventListener("click", () => showRoomConditions(row));
      const force = document.createElement("button");
      force.type = "button";
      force.className = "button small danger";
      force.textContent = "强制满员";
      force.addEventListener("click", () =>
        adminAction("force_fill", { room_id: row.id }),
      );
      const dissolve = document.createElement("button");
      dissolve.type = "button";
      dissolve.className = "button small";
      dissolve.textContent = "解散";
      dissolve.addEventListener("click", () =>
        adminAction("dissolve_room", { room_id: row.id }),
      );
      actionTd.append(conditions, force, dissolve);
      tr.appendChild(actionTd);
      body.appendChild(tr);
    });
  }

  function showRoomConditions(row) {
    const modal = $("roomModal");
    const body = $("roomModalBody");
    const title = $("roomModalTitle");
    if (!modal || !body || !title) return;
    title.textContent = `房间 #${row.id} 条件`;
    body.textContent = "";

    const detail = document.createElement("dl");
    detail.className = "room-detail";
    const rows = [
      ["目标人数", `${row.target} 人`],
      ["当前人数", `${row.current}/${row.target}`],
      ["还差", `${row.missing} 人`],
      ["平台", row.pool || "-"],
      ["模式", row.modded || "-"],
      ["可玩时段", row.slots || "-"],
      ["等待", formatWait(row.wait_seconds)],
      ["匹配条件", "同平台池、同模式、时段有交集、性别偏好双方满足"],
    ];
    rows.forEach(([label, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      detail.append(dt, dd);
    });
    body.appendChild(detail);

    const members = row.members || [];
    const heading = document.createElement("p");
    heading.className = "hint";
    heading.textContent = `成员（${members.length} 人）：`;
    body.appendChild(heading);
    const list = document.createElement("ul");
    list.className = "room-members";
    members.forEach((member) => {
      const item = document.createElement("li");
      item.textContent =
        `${member.nickname}（QQ ${member.qq}）` +
        ` · 我方 ${member.have} 人` +
        ` · 性别 ${member.gender || "保密"}` +
        ` · 希望对方 ${member.prefer_gender || "不限"}`;
      list.appendChild(item);
    });
    body.appendChild(list);
    modal.hidden = false;
  }

  function closeRoomConditions() {
    const modal = $("roomModal");
    if (modal) modal.hidden = true;
  }

  function renderReports(rows) {
    const body = $("reportBody");
    if (!body) return;
    body.textContent = "";
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "empty";
      td.textContent = "暂无举报";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      const cells = [
        `#${row.id}`,
        row.reporter,
        row.target,
        row.reason || "-",
        formatTime(row.created_at),
      ];
      cells.forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });

      const actionTd = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button small";
      button.textContent = "标记处理";
      button.addEventListener("click", () =>
        adminAction("resolve_report", { report_id: row.id }),
      );
      actionTd.appendChild(button);
      tr.appendChild(actionTd);
      body.appendChild(tr);
    });
  }

  function drawChart(hourly) {
    const canvas = $("hourlyChart");
    if (!canvas) return;
    const width = Math.max(320, canvas.clientWidth || 600);
    const height = 200;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const values = Array.from({ length: 24 }, (_, i) => {
      const item = hourly.find((entry) => entry.hour === String(i).padStart(2, "0"));
      return Number(item ? item.count : 0) || 0;
    });
    const max = Math.max(1, ...values);
    const left = 10;
    const right = 10;
    const top = 18;
    const bottom = 26;
    const chartWidth = width - left - right;
    const chartHeight = height - top - bottom;
    const barWidth = chartWidth / 24;

    const line = cssVar("--line") || "#e5e7eb";
    const accent = cssVar("--accent") || "#2563eb";
    const muted = cssVar("--muted") || "#6b7280";
    const text = cssVar("--text") || "#1f2937";

    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 2; i += 1) {
      const y = top + (chartHeight / 2) * i;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(width - right, y);
      ctx.stroke();
    }

    values.forEach((value, index) => {
      const barHeight = (value / max) * chartHeight;
      const x = left + index * barWidth + 2;
      const y = top + chartHeight - barHeight;
      ctx.fillStyle = accent;
      ctx.globalAlpha = value > 0 ? 0.9 : 0.15;
      ctx.fillRect(x, y, Math.max(2, barWidth - 4), Math.max(2, barHeight));
      ctx.globalAlpha = 1;

      if (value > 0) {
        ctx.fillStyle = text;
        ctx.font = "11px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(String(value), x + (barWidth - 4) / 2, y - 4);
      }
      if (index % 3 === 0) {
        ctx.fillStyle = muted;
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(String(index).padStart(2, "0"), x + (barWidth - 4) / 2, height - 8);
      }
    });
  }

  function render(data) {
    const today = data.today || {};
    setText("signups", today.signups ?? 0);
    setText("matches", today.matches ?? 0);
    setText("rate", `${today.rate ?? 0}%`);
    setText("waiting", today.waiting ?? 0);
    setText("groups", today.active_groups ?? 0);
    setText("reports", today.reports_open ?? 0);
    setText("updated", `更新于 ${new Date().toLocaleTimeString()}`);

    state.hourly = data.hourly || [];
    state.recent = data.recent || [];
    renderPools(data.pools || []);
    renderRecent(state.recent);
    renderRooms(data.rooms || []);
    renderReports(data.reports || []);
    drawChart(state.hourly);
  }

  async function load() {
    try {
      const payload = await fetchStats();
      const data = unwrap(payload);
      if (!data || data.ok === false) {
        throw new Error((data && data.error && data.error.message) || "接口返回异常");
      }
      render(data);
      const error = $("error");
      if (error) error.hidden = true;
    } catch (error) {
      const node = $("error");
      if (node) {
        node.hidden = false;
        node.textContent = `统计加载失败：${error && error.message ? error.message : error} ｜ ${diagText()}`;
      }
    }
  }

  async function start() {
    bridge = await waitForBridge(6000);
    if (!bridge) {
      await loadLocalBridge();
      bridge = await waitForBridge(4000);
    }
    if (bridge && typeof bridge.ready === "function") {
      try {
        await withTimeout(bridge.ready(), 3000, "页面桥接初始化超时");
      } catch (error) {
        console.warn("[stardew] bridge ready timeout", error);
      }
    }
    const refresh = $("refresh");
    if (refresh) {
      refresh.addEventListener("click", load);
    }
    const showClosed = $("showClosed");
    if (showClosed) {
      showClosed.addEventListener("change", () =>
        renderRecent(state.recent),
      );
    }

    const roomModal = $("roomModal");
    if (roomModal) {
      roomModal.addEventListener("click", (event) => {
        if (event.target === roomModal) closeRoomConditions();
      });
    }
    const roomModalClose = $("roomModalClose");
    if (roomModalClose) {
      roomModalClose.addEventListener("click", closeRoomConditions);
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeRoomConditions();
    });

    const scanBtn = $("scanBtn");
    if (scanBtn) {
      scanBtn.addEventListener("click", () => adminAction("scan", {}));
    }
    const banBtn = $("banBtn");
    if (banBtn) {
      banBtn.addEventListener("click", () => {
        const qq = $("banQQ") ? $("banQQ").value.trim() : "";
        const seconds = $("banDuration")
          ? Number($("banDuration").value) || 0
          : 0;
        const reason = $("banReason") ? $("banReason").value.trim() : "";
        adminAction("ban", { qq, seconds, reason });
      });
    }
    const unbanBtn = $("unbanBtn");
    if (unbanBtn) {
      unbanBtn.addEventListener("click", () => {
        const qq = $("unbanQQ") ? $("unbanQQ").value.trim() : "";
        adminAction("unban", { qq });
      });
    }

    await load();
    state.timer = window.setInterval(load, 5000);
    if (bridge && typeof bridge.onContext === "function") {
      bridge.onContext(() => drawChart(state.hourly));
    }
    window.addEventListener("resize", () => drawChart(state.hourly));
  }

  start();
})();
