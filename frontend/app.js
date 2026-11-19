(() => {
  "use strict";

  const SIDES = [
    { key: "winners", title: "براکت برندگان" },
    { key: "losers", title: "براکت بازندگان" },
    { key: "grand_final", title: "فینال بزرگ" },
  ];
  const STATUS_LABELS = {
    pending: "در انتظار",
    ready: "آماده",
    active: "در حال برگزاری",
    completed: "پایان‌یافته",
  };
  const PLAYER_STATUS_LABELS = {
    registered: "ثبت‌نام‌شده",
    active: "فعال",
    eliminated: "حذف‌شده",
    withdrawn: "انصراف‌داده",
    champion: "قهرمان",
  };
  const ROLE_LABELS = {
    admin: "مدیر",
    coworker: "همکار",
    player: "بازیکن",
    guest: "مهمان",
  };
  const STATUS_ICONS = { pending: "🟡", active: "🟢", completed: "🔴" };
  const THEME_KEY = "tourneycore.theme";
  const DEFAULT_REFRESH_MS = 10000;
  const API_BASE = (
    window.TOURNEYCORE_API_BASE ||
    document.querySelector('meta[name="tourneycore-api"]')?.content ||
    ""
  ).replace(/\/+$/, "");

  const state = {
    systems: [],
    currentId: null,
    dashboard: null,
    timer: null,
    refreshMs: DEFAULT_REFRESH_MS,
    loading: false,
    userRole: "guest",
  };
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  const dom = {
    systemSelect: document.getElementById("system-select"),
    refreshButton: document.getElementById("refresh-button"),
    themeButton: document.getElementById("theme-button"),
    alert: document.getElementById("alert"),
    hero: document.getElementById("hero"),
    heroStatus: document.getElementById("hero-status"),
    heroTitle: document.getElementById("hero-title"),
    heroDescription: document.getElementById("hero-description"),
    stats: document.getElementById("stats"),
    champion: document.getElementById("champion"),
    championName: document.getElementById("champion-name"),
    standingsPanel: document.getElementById("standings-panel"),
    standingsBody: document.getElementById("standings-body"),
    standingsHint: document.getElementById("standings-hint"),
    brackets: document.getElementById("brackets"),
    empty: document.getElementById("empty-state"),
    footerStatus: document.getElementById("footer-status"),
  };

  function showAlert(message) {
    if (tg && tg.showAlert) tg.showAlert(message);
    else alert(message);
  }

  window.showMatchInfo = function(matchId) {
    showAlert(`شناسه مسابقه: ${matchId}`);
  };

  // Single match notification with delivery report
  window.promptForNotification = function(matchId) {
    askPrompt("متن پیام خود را برای بازیکنان این مسابقه بنویسید:", async (message) => {
      if (!message) return;
      try {
        const res = await fetchJson(`/api/matches/${encodeURIComponent(matchId)}/notify`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
        });
        showAlert(res.report || "پیام با موفقیت ارسال شد!");
      } catch (err) {
        showAlert(`خطا: ${err.message}`);
      }
    });
  };

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }

  function show(element, visible) {
    if (element) element.hidden = !visible;
  }

  function setAlert(message) {
    dom.alert.textContent = message || "";
    show(dom.alert, !!message);
  }

  function readStoredTheme() {
    try { return localStorage.getItem(THEME_KEY); } catch { return null; }
  }

  function storeTheme(theme) {
    try { localStorage.setItem(THEME_KEY, theme); } catch {}
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    dom.themeButton.textContent = theme === "dark" ? "🌙" : "☀️";
  }

  function initialTheme() {
    const stored = readStoredTheme();
    if (stored) return stored;
    if (tg && tg.colorScheme) return tg.colorScheme;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function setupTelegram() {
    if (tg) {
      try { tg.ready(); tg.expand(); } catch {}
    }
  }

  async function fetchJson(path, options = {}) {
    const headers = { Accept: "application/json", ...options.headers };
    if (tg && tg.initData) headers["X-Telegram-Init-Data"] = tg.initData;
    const response = await fetch(API_BASE + path, { ...options, headers, cache: "no-store" });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        if (body && body.detail) detail = body.detail;
      } catch {}
      throw new Error(detail);
    }
    return response.json();
  }

  function configureRefresh() {
    const raw = Number(new URLSearchParams(window.location.search).get("refresh"));
    if (raw >= 3000) state.refreshMs = raw;
  }

  async function loadSystems() {
    try {
      const data = await fetchJson("/api/systems");
      state.systems = Array.isArray(data.systems) ? data.systems : [];
      populateSystemSelect();
      if (!state.systems.length) return renderEmpty(data.userRole || "guest");
      dom.systemSelect.value = state.systems[0].id;
      await loadDashboard({ systemId: state.systems[0].id });
    } catch (error) {
      setAlert(`خطا در بارگذاری تورنمنت‌ها: ${error.message}`);
    }
  }

  function populateSystemSelect() {
    const options = state.systems.map((s) => node("option", null, `${STATUS_ICONS[s.status] || "⚪"} ${s.name}`));
    options.forEach((opt, i) => { opt.value = state.systems[i].id; });
    dom.systemSelect.replaceChildren(...options);
  }

  function selectSystem(systemId) {
    if (!systemId) return;
    state.currentId = systemId;
    dom.systemSelect.value = systemId;
    loadDashboard({ systemId });
  }

  async function loadDashboard({ systemId, silent = false, manual = false } = {}) {
    const id = systemId || state.currentId || dom.systemSelect.value;
    if (!id || state.loading) return;
    state.loading = true;
    if (manual) dom.refreshButton.classList.add("is-spinning");
    try {
      const data = await fetchJson(`/api/systems/${encodeURIComponent(id)}`);
      state.currentId = id;
      state.dashboard = data;
      state.userRole = data.userRole || "guest";
      setAlert("");
      render(data);
    } catch (error) {
      if (!silent) setAlert(`خطا در بارگذاری جدول مسابقات: ${error.message}`);
    } finally {
      state.loading = false;
      dom.refreshButton.classList.remove("is-spinning");
      scheduleRefresh();
    }
  }

  function scheduleRefresh() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = window.setInterval(() => {
      if (!document.hidden) loadDashboard({ silent: true });
    }, state.refreshMs);
  }

  function stat(value, label) {
    const card = node("div", "stat");
    card.append(node("div", "stat__value", value), node("div", "stat__label", label));
    return card;
  }

  function render(data) {
    show(dom.empty, false);
    renderHero(data);
    renderChampion(data.champion);
    renderStandings(data);
    renderBrackets(data);
  }

  function renderEmpty(userRole) {
    show(dom.empty, true);
    show(dom.hero, false);
    show(dom.champion, false);
    show(dom.standingsPanel, false);
    dom.brackets.replaceChildren();
    dom.systemSelect.replaceChildren();
    show(document.getElementById("admin-panel"), userRole === "admin" || userRole === "coworker");
    show(document.getElementById("promote-btn"), userRole === "admin");
    dom.footerStatus.textContent = "تورنمنتی وجود ندارد";
  }

  function renderHero(data) {
    const sys = data.system || {};
    const counts = data.counts || {};
    const role = data.userRole || "guest";

    // Banner display logic using safe DOM APIs (XSS-safe)
    let banner = document.getElementById("broadcast-banner");
    if (!banner) {
      banner = node("div", "broadcast-banner");
      banner.id = "broadcast-banner";
      dom.hero.parentNode.insertBefore(banner, dom.hero);
    }

    banner.replaceChildren();

    let announcements = [];
    try {
      if (sys.announcement) {
        announcements = JSON.parse(sys.announcement);
        if (!Array.isArray(announcements)) {
          announcements = [{ text: sys.announcement, date: new Date().toISOString() }];
        }
      }
    } catch (e) {
      if (sys.announcement) {
        announcements = [{ text: sys.announcement, date: new Date().toISOString() }];
      }
    }

    if (announcements.length > 0) {
      const title = node("div", "broadcast-title");
      title.append(document.createTextNode("📢 "), node("strong", null, "اطلاعیه‌های تورنمنت"));
      banner.appendChild(title);

      const list = node("div", "broadcast-list");

      announcements.forEach((a) => {
        const item = node("div", "broadcast-item");

        // Convert Gregorian date to Jalali (fa-IR) and extract time
        const dateObj = new Date(a.date);
        const dateStr = dateObj.toLocaleDateString("fa-IR");
        const timeStr = dateObj.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });

        item.append(
          node("span", "broadcast-time", `${dateStr} - ساعت ${timeStr}`),
          node("span", "broadcast-text", a.text || "")
        );
        list.appendChild(item);
      });

      banner.appendChild(list);
      banner.style.display = "block";
    } else {
      banner.style.display = "none";
    }

    const statusLabel = STATUS_LABELS[sys.status] || sys.status || "";
    dom.heroTitle.textContent = sys.name || "تورنمنت";
    dom.heroDescription.textContent = sys.description || "";
    show(dom.heroDescription, Boolean(sys.description));
    dom.heroStatus.textContent = sys.registration_open
      ? `${STATUS_ICONS[sys.status] || "⚪"} ${statusLabel} · ثبت‌نام باز است`
      : `${STATUS_ICONS[sys.status] || "⚪"} ${statusLabel}`;

    dom.stats.replaceChildren(
      stat(counts.players || 0, "بازیکنان"),
      stat(counts.active || 0, "در جریان"),
      stat(counts.ready || 0, "آماده"),
      stat(counts.completed || 0, "انجام‌شده")
    );

    show(document.getElementById("admin-panel"), role === "admin" || role === "coworker");
    show(document.getElementById("player-panel"), role === "player");
    show(document.getElementById("promote-btn"), role === "admin");
    show(dom.hero, true);

    const roleLabel = ROLE_LABELS[role] || role;
    const authLabel = tg && tg.initData ? "احراز هویت‌شده" : "بدون احراز هویت";
    dom.footerStatus.textContent = `نقش: ${roleLabel} | ${authLabel}`;
  }

  function renderChampion(champion) {
    if (!champion) {
      show(dom.champion, false);
      return;
    }
    dom.championName.textContent = champion.name;
    show(dom.champion, true);
  }

  function renderStandings(data) {
    const players = Array.isArray(data.players) ? data.players : [];
    show(dom.standingsPanel, players.length > 0);
    dom.standingsHint.textContent = `${players.length} بازیکن`;
    const rows = players.map((p, i) => {
      const r = node("tr");
      if (p.eliminated || p.withdrawn) r.classList.add("is-out");
      const nameCell = node("td", "player");
      nameCell.append(node("span", "player__name", p.name));
      if (p.seed) nameCell.append(node("span", "player__seed", `#${p.seed}`));
      const statusText = PLAYER_STATUS_LABELS[p.status] || p.status;
      const statCell = node("td");
      statCell.append(node("span", p.eliminated || p.withdrawn ? "pill pill--muted" : "pill", statusText));
      r.append(node("td", "rank", i + 1), nameCell, node("td", "record", `${p.wins}–${p.losses}`), statCell);
      return r;
    });
    dom.standingsBody.replaceChildren(...rows);
  }

  function renderBrackets(data) {
    const brackets = data.brackets || {};
    const sections = [];
    for (const side of SIDES) {
      const matches = (brackets[side.key] || []).filter((m) => !m.is_dead);
      if (matches.length) sections.push(bracketSection(side.title, matches));
    }
    if (!sections.length) {
      dom.brackets.replaceChildren(node("p", "bracket__empty", "قرعه‌کشی و چینش براکت هنوز انجام نشده است."));
      return;
    }
    dom.brackets.replaceChildren(...sections);
  }

  // Build a player slot DOM element safely without innerHTML
  function createMatchSlot(player, scoreVal, winner) {
    const isWinner = Boolean(winner && player && winner.id === player.id);
    const slot = node("div", isWinner ? "slot slot--winner" : "slot");

    const logoWrap = node("div", "slot__logo");
    const img = document.createElement("img");
    const avatarName = encodeURIComponent(player && player.name ? player.name : "?");
    img.src = `https://ui-avatars.com/api/?name=${avatarName}&background=random&color=fff`;
    img.alt = "آواتار بازیکن";
    logoWrap.append(img);

    const nameEl = node("div", "slot__name", player && player.name ? player.name : "نامشخص");
    const scoreText = scoreVal !== undefined && scoreVal !== null ? String(scoreVal) : "";
    const scoreEl = node("div", "slot__score", scoreText);

    slot.append(logoWrap, nameEl, scoreEl);
    return slot;
  }

  function bracketSection(title, matches) {
    const section = node("section", "bracket");
    const head = node("div", "bracket__head");
    head.append(node("h2", null, title), node("span", "bracket__count", `${matches.length} مسابقه`));
    section.append(head);

    const rounds = new Map();
    matches.forEach((m) => {
      if (!rounds.has(m.round)) rounds.set(m.round, []);
      rounds.get(m.round).push(m);
    });

    const grid = node("div", "rounds");
    [...rounds.keys()].sort((a, b) => a - b).forEach((r) => {
      const col = node("div", "round");
      col.append(node("div", "round__title", `دور ${r} - حذفی`));
      const list = node("div", "round__matches");

      rounds.get(r).forEach((match) => {
        const card = node("div", match.status === "active" ? "match match--active" : "match");

        const infoBtn = node("div", "match__info", "i");
        infoBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          window.showMatchInfo(match.id);
        });
        card.append(infoBtn);

        if (state.userRole === "admin" || state.userRole === "coworker") {
          const notifyBtn = node("div", "match__notify", "🔔");
          notifyBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            window.promptForNotification(match.id);
          });
          card.append(notifyBtn);
        }

        card.append(
          createMatchSlot(match.player1, match.score ? match.score.player1 : "", match.winner),
          createMatchSlot(match.player2, match.score ? match.score.player2 : "", match.winner)
        );

        if (
          (state.userRole === "admin" || state.userRole === "coworker") &&
          !match.is_bye &&
          match.status !== "completed" &&
          match.player1 &&
          match.player2
        ) {
          card.style.cursor = "pointer";
          card.style.border = "1px solid var(--accent)";
          card.title = "برای ثبت نتیجه کلیک کنید";
          card.addEventListener("click", () => promptForScore(match));
        }

        list.append(card);
      });

      col.append(list);
      grid.append(col);
    });

    section.append(grid);
    return section;
  }

  function promptForScore(match) {
    askPrompt(`امتیاز ${match.player1.name} را وارد کنید:`, (score1Str) => {
      const s1 = parseInt(score1Str, 10);
      if (isNaN(s1)) return showAlert("امتیاز واردشده نامعتبر است!");
      askPrompt(`امتیاز ${match.player2.name} را وارد کنید:`, async (score2Str) => {
        const s2 = parseInt(score2Str, 10);
        if (isNaN(s2)) return showAlert("امتیاز واردشده نامعتبر است!");
        let winner_id = null;
        let loser_id = null;
        if (s1 > s2) {
          winner_id = match.player1.id;
          loser_id = match.player2.id;
        } else if (s2 > s1) {
          winner_id = match.player2.id;
          loser_id = match.player1.id;
        } else {
          return showAlert("نتیجه مساوی مجاز نیست!");
        }

        try {
          await fetchJson(`/api/matches/${encodeURIComponent(match.id)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ score_player1: s1, score_player2: s2, winner_id, loser_id }),
          });
          await loadDashboard({ manual: true });
        } catch (err) {
          showAlert(`خطا: ${err.message}`);
        }
      });
    });
  }

  function askConfirm(message, callback) {
    if (tg && tg.showConfirm) {
      tg.showConfirm(message, callback);
    } else {
      callback(confirm(message));
    }
  }

  // Allow empty string submission (e.g. optional fields or clearing announcements) while ignoring Cancel
  function askPrompt(message, callback) {
    const val = prompt(message);
    if (val !== null) callback(val.trim());
  }

  function bindEvents() {
    dom.systemSelect.addEventListener("change", (e) => selectSystem(e.target.value));
    dom.refreshButton.addEventListener("click", () => loadDashboard({ manual: true }));
    dom.themeButton.addEventListener("click", () => {
      const n = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      storeTheme(n);
      applyTheme(n);
    });

    // Broadcast banner button in admin panel
    const adminActions = document.getElementById("admin-actions") || document.getElementById("admin-panel");
    if (adminActions && !document.getElementById("btn-broadcast")) {
      const broadcastBtn = document.createElement("button");
      broadcastBtn.id = "btn-broadcast";
      broadcastBtn.type = "button";
      broadcastBtn.className = "icon-button action-btn action-btn--broadcast";
      broadcastBtn.textContent = "📢 ثبت اطلاعیه";
      adminActions.appendChild(broadcastBtn);

      broadcastBtn.addEventListener("click", () => {
        if (!state.currentId) return showAlert("ابتدا یک تورنمنت انتخاب کنید!");
        askPrompt(
          "متن اطلاعیه را بنویسید تا در بالای مینی‌اپ نمایش داده شود:\n(برای حذف تمام اطلاعیه‌ها، این کادر را کاملاً خالی بگذارید)",
          async (message) => {
            try {
              await fetchJson(`/api/systems/${encodeURIComponent(state.currentId)}/broadcast`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: message || null }), // Sends null if empty
              });
              await loadDashboard({ manual: true });
            } catch (err) {
              showAlert(`خطا: ${err.message}`);
            }
          }
        );
      });
    }

    document.getElementById("btn-create-tournament")?.addEventListener("click", () => {
      askPrompt("نام تورنمنت جدید را وارد کنید:", async (name) => {
        if (!name) return;
        try {
          await fetchJson("/api/systems", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
          });
          await loadSystems();
        } catch (err) {
          showAlert(`خطا: ${err.message}`);
        }
      });
    });

    document.getElementById("btn-delete-tournament")?.addEventListener("click", () => {
      if (!state.currentId) return showAlert("ابتدا یک تورنمنت انتخاب کنید!");
      askConfirm("آیا از حذف دائمی این تورنمنت مطمئن هستید؟", async (ok) => {
        if (!ok) return;
        try {
          await fetchJson(`/api/systems/${encodeURIComponent(state.currentId)}`, { method: "DELETE" });
          await loadSystems();
        } catch (err) {
          showAlert(`خطا: ${err.message}`);
        }
      });
    });

    // Add player with optional Telegram username
    document.getElementById("btn-add-player")?.addEventListener("click", () => {
      if (!state.currentId) return showAlert("ابتدا یک تورنمنت انتخاب کنید!");
      askPrompt("اسم بازیکن را وارد کنید:", (name) => {
        if (!name) return;
        askPrompt("یوزرنیم تلگرام بازیکن را وارد کنید (مثال: @ali - اگر ندارد خالی بگذارید):", async (telegramUsername) => {
          try {
            await fetchJson(`/api/systems/${encodeURIComponent(state.currentId)}/players`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name, telegram_username: telegramUsername || null }),
            });
            await loadDashboard({ manual: true });
          } catch (err) {
            showAlert(`خطا: ${err.message}`);
          }
        });
      });
    });

    document.getElementById("btn-start-bracket")?.addEventListener("click", () => {
      if (!state.currentId) return showAlert("ابتدا یک تورنمنت انتخاب کنید!");
      askConfirm("قرعه‌کشی و شروع براکت انجام شود؟ با این کار ثبت‌نام بسته خواهد شد.", async (ok) => {
        if (!ok) return;
        try {
          await fetchJson(`/api/systems/${encodeURIComponent(state.currentId)}/start`, { method: "POST" });
          await loadDashboard({ manual: true });
        } catch (err) {
          showAlert(`خطا: ${err.message}`);
        }
      });
    });

    document.getElementById("promote-btn")?.addEventListener("click", () => {
      askPrompt("آیدی عددی تلگرام کاربر را برای ارتقا به همکار وارد کنید:", async (tgId) => {
        if (!tgId || isNaN(Number(tgId))) return;
        try {
          await fetchJson("/api/roles/promote", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ telegram_id: Number(tgId), new_role: "coworker" }),
          });
          showAlert("کاربر با موفقیت به همکار ارتقا یافت!");
        } catch (err) {
          showAlert(`خطا: ${err.message}`);
        }
      });
    });
  }

  async function bootstrap() {
    applyTheme(initialTheme());
    setupTelegram();
    bindEvents();
    configureRefresh();
    await loadSystems();
    scheduleRefresh();
  }

  bootstrap();
})();