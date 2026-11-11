
(() => {
  const lobbyEl = document.getElementById("lobby");
  const roomEl = document.getElementById("room");
  const lobbyError = document.getElementById("lobbyError");
  const chatError = document.getElementById("chatError");
  let names = { user: "你", aion: "AI", connor: "另一位伴侣" };
  let opponent = "aion";
  let current = null;
  let busy = false;

  const $ = (id) => document.getElementById(id);
  const showError = (el, text) => {
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
  };
  const goLounge = () => { location.href = "/playground"; };
  const setOpponent = (value) => {
    opponent = value;
    document.querySelectorAll(".choice").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.opponent === value ? "true" : "false");
    });
  };

  async function api(url, options) {
    const response = await fetch(url, {
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options && options.headers) },
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch {}
    if (!response.ok) {
      const detail = data.detail || data.error || data.message || `请求失败（${response.status}）`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function renderLobby(data) {
    names = data.names || names;
    $("challengerName").textContent = `${names.user || "你"}来开局`;
    $("aionChoiceName").textContent = names.aion || "AI";
    $("connorChoiceName").textContent = names.connor || "另一位伴侣";
    if (!data.available) {
      showError(lobbyError, "棋盘引擎还没准备好。房间页已经就位，请确认本机引擎正在运行。");
    } else {
      showError(lobbyError, "");
    }
    const list = $("resumeList");
    list.innerHTML = "";
    const games = data.games || [];
    $("resumeSection").hidden = !games.length;
    for (const game of games) {
      const link = document.createElement("a");
      link.href = `/spicy-board?game=${encodeURIComponent(game.id)}`;
      link.className = "saved";
      const title = document.createElement("strong");
      title.textContent = (game.players || []).filter(Boolean).join(" 与 ");
      const status = document.createElement("span");
      status.textContent = `${game.flavor || ""} · ${game.turn ? "轮到 " + game.turn : "继续这一局"} ›`;
      link.append(title, status);
      list.append(link);
    }
  }

  function pieceClass(name) {
    if (!current) return "p1";
    return name === current.p1_name ? "p1" : "p2";
  }

  function textBlock(className, title, value) {
    if (value == null || value === "") return null;
    const box = document.createElement("div");
    box.className = className;
    if (title) {
      const head = document.createElement("b");
      head.textContent = title;
      box.append(head);
      box.append(document.createTextNode(" "));
    }
    const body = typeof value === "string" ? value : (value.content || value.text || value.say || JSON.stringify(value));
    box.append(document.createTextNode(body));
    return box;
  }

  function renderBoard(data) {
    const boardEl = $("board");
    boardEl.innerHTML = "";
    const board = data.board || {};
    const tiles = board.tiles || [];
    const well = document.createElement("div");
    well.className = "well";
    const turn = board.turn || (data.event && data.event.who) || "";
    const strong = document.createElement("strong");
    strong.textContent = turn ? `轮到 ${turn}` : "天鹅绒棋盘";
    const span = document.createElement("span");
    const dice = data.event && data.event.dice;
    span.textContent = dice ? `刚才掷出 ${dice}` : "点下面的掷骰，走到哪一格就停在哪一格";
    well.append(strong, span);
    boardEl.append(well);
    for (const tile of tiles) {
      const el = document.createElement("div");
      el.className = `tile ${tile.kind}${tile.kind !== "task" ? " special" : ""}`;
      el.style.gridRowStart = String((tile.row || 0) + 1);
      el.style.gridColumnStart = String((tile.col || 0) + 1);
      const idx = document.createElement("span");
      idx.className = "idx";
      idx.textContent = String(tile.index);
      const emoji = document.createElement("span");
      emoji.className = "emoji";
      emoji.textContent = tile.emoji || "";
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = tile.label || "";
      el.append(idx, emoji, label);
      if (tile.owner) {
        const owner = document.createElement("span");
        owner.className = "owner";
        owner.title = tile.owner + " 的地盘";
        el.append(owner);
      }
      if (tile.occupants && tile.occupants.length) {
        const tokens = document.createElement("div");
        tokens.className = "tokens";
        for (const name of tile.occupants) {
          const token = document.createElement("span");
          token.className = "token " + pieceClass(name);
          token.title = name;
          tokens.append(token);
        }
        el.append(tokens);
      }
      boardEl.append(el);
    }
  }

  function renderScoreboard(data) {
    const board = data.board || {};
    const coins = board.coins || {};
    const laps = board.laps || {};
    const hands = board.hands || {};
    const make = (name, side) => {
      const card = document.createElement("div");
      card.className = "player-card" + ((board.turn === name) ? " active" : "");
      const b = document.createElement("b");
      b.textContent = name || side;
      const small = document.createElement("small");
      const hand = (hands[name] || []).join("、") || "空手牌";
      const lap = (laps[name] ?? 0);
      small.textContent = `金币 ${coins[name] ?? 0} · 第 ${lap + 1} 圈 · ${hand}`;
      card.append(b, small);
      return card;
    };
    const mid = document.createElement("div");
    mid.className = "turn-pill";
    mid.textContent = (data.flavor || "") + (data.event && data.event.game_over ? " · 本局结束" : "");
    $("scoreboard").replaceChildren(make(data.p1_name, "P1"), mid, make(data.p2_name, "P2"));
  }

  function extractTask(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    return value.content || value.text || value.task || value.say || "";
  }

  function renderEvent(data) {
    const ev = data.event || {};
    const card = $("eventCard");
    card.replaceChildren();
    const title = document.createElement("h2");
    title.textContent = ev.game_over ? "终局" : (ev.tile ? `停在「${ev.tile}」` : "这一轮");
    card.append(title);
    if (ev.settled) card.append(textBlock("say", "上一轮结算", ev.settled));
    if (ev.say) card.append(textBlock("say", "", ev.say));
    const task = extractTask(ev.task);
    if (task) card.append(textBlock("task", "任务", task));
    const truth = extractTask(ev.truth);
    if (truth) card.append(textBlock("truth", "真心话", truth));
    if (ev.mystery) card.append(textBlock("say", "未知格", ev.mystery));
    if (ev.card) card.append(textBlock("say", "功能卡", ev.card.name ? `${ev.card.name}：${ev.card.description || ""}` : ev.card));
    if (ev.toll) card.append(textBlock("say", "过路费", ev.toll));
    if (ev.duel) card.append(textBlock("say", "对决", ev.duel));
    if (ev.identity_reminder) card.append(textBlock("say", "身份提醒", ev.identity_reminder));
    if (ev.active_limits) card.append(textBlock("say", "安全设置", ev.active_limits));
    if (ev.intensity_note) card.append(textBlock("say", "强度", ev.intensity_note));
    if (ev.final && ev.final.result) card.append(textBlock("say", "终局结果", ev.final.result));
    if (ev.hint) card.append(textBlock("say", "下一步", ev.hint));
  }

  function addAction(label, handler, kind) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = kind || "ghost";
    btn.textContent = label;
    btn.addEventListener("click", handler);
    $("actions").append(btn);
  }

  async function run(task) {
    if (busy) return;
    busy = true;
    try {
      const data = await task();
      paintRoom(data);
    } catch (err) {
      showError(chatError, err.message || String(err));
    } finally {
      busy = false;
    }
  }

  function renderActions(data) {
    const ev = data.event || {};
    $("actions").replaceChildren();
    const who = ev.who || data.board.turn || data.p1_name;
    if (ev.game_over || data.status === "finished") {
      addAction("查看终局", () => run(() => api(`/api/spicy-board/${data.id}/finish`, { method: "POST", body: "{}" })), "primary");
      addAction("返回大厅", () => { history.replaceState({}, "", "/spicy-board"); showLobby(); });
      return;
    }
    addAction("掷骰", () => run(() => api(`/api/spicy-board/${data.id}/roll`, { method: "POST", body: "{}" })), "primary");
    if (ev.task || ev.truth) {
      addAction("跳过这道", () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "skip", who }) })));
      addAction("换一道", () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "swap", who }) })));
    }
    if (ev.action_needed === "toll" || ev.toll) {
      addAction("交过路费", () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "pay_toll", who }) })));
      addAction("用差遣抵", () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "serve_toll", who }) })));
    }
    if (ev.action_needed === "super" || (ev.tile === "super") || (extractTask(ev.task) && ev.action_needed === "super")) {
      addAction("做完超级任务", () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "done", who }) })));
      addAction("花 8 币买断", () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "buyout", who }) })));
    }
    if (ev.tile === "shop" || ev.action_needed === "shop") {
      addAction("摸一张功能卡", () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "buy_card", who }) })));
    }
    if (ev.action_needed === "duel" || ev.duel) {
      addAction(`判 ${data.p1_name} 赢`, () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "duel_result", winner: data.p1_name }) })));
      addAction(`判 ${data.p2_name} 赢`, () => run(() => api(`/api/spicy-board/${data.id}/action`, { method: "POST", body: JSON.stringify({ action: "duel_result", winner: data.p2_name }) })));
    }
  }

  function renderChat(data) {
    const log = $("chatLog");
    log.replaceChildren();
    for (const message of data.messages || []) {
      const bubble = document.createElement("div");
      bubble.className = "bubble" + (message.sender === "user" ? " user" : "");
      const b = document.createElement("b");
      const label = message.sender === "user" ? (data.p1_name || names.user) : (data.names && data.names[message.sender]) || data.p2_name || message.sender;
      b.textContent = label;
      bubble.append(b, document.createTextNode(message.text || ""));
      log.append(bubble);
    }
    log.scrollTop = log.scrollHeight;
    showError(chatError, data.chat_error || "");
  }

  function paintRoom(data) {
    current = data;
    lobbyEl.hidden = true;
    roomEl.hidden = false;
    renderScoreboard(data);
    renderBoard(data);
    renderEvent(data);
    renderActions(data);
    renderChat(data);
  }

  async function showLobby() {
    lobbyEl.hidden = false;
    roomEl.hidden = true;
    current = null;
    try {
      renderLobby(await api("/api/spicy-board"));
    } catch (err) {
      showError(lobbyError, err.message || String(err));
    }
  }

  async function openGame(id) {
    const data = await api(`/api/spicy-board/${encodeURIComponent(id)}`);
    history.replaceState({}, "", `/spicy-board?game=${encodeURIComponent(id)}`);
    paintRoom(data);
  }

  $("lobbyBack").addEventListener("click", goLounge);
  $("roomBack").addEventListener("click", () => { history.replaceState({}, "", "/spicy-board"); showLobby(); });
  document.querySelectorAll(".choice").forEach((btn) => {
    btn.addEventListener("click", () => setOpponent(btn.dataset.opponent));
  });
  const confirmBox = $("setupConfirmed");
  const startBtn = $("startGame");
  confirmBox.addEventListener("change", () => { startBtn.disabled = !confirmBox.checked; });
  startBtn.addEventListener("click", () => run(async () => {
    const redline = $("redline").value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
    const data = await api("/api/spicy-board", {
      method: "POST",
      body: JSON.stringify({
        opponent,
        flavor: $("flavor").value,
        game_length: Number($("gameLength").value),
        identity_mode: $("identityMode").value,
        p1_sex: $("p1Sex").value,
        p1_role: $("p1Role").value,
        p2_sex: $("p2Sex").value,
        p2_role: $("p2Role").value,
        redline,
        open_anal: $("openAnal").checked,
      }),
    });
    history.replaceState({}, "", `/spicy-board?game=${encodeURIComponent(data.id)}`);
    return data;
  }));
  $("chatForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!current) return;
    const input = $("chatInput");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    run(() => api(`/api/spicy-board/${current.id}/chat`, { method: "POST", body: JSON.stringify({ text }) }));
  });

  const params = new URLSearchParams(location.search);
  const gameId = params.get("game");
  if (gameId) {
    openGame(gameId).catch((err) => { showError(lobbyError, err.message || String(err)); showLobby(); });
  } else {
    showLobby();
  }
})();
