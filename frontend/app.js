/* TourneyCore mini-app — read-only live bracket.
 *
 * The page talks to three endpoints:
 *   GET /api/systems        -> tournament selector
 *   GET /api/systems/<id>   -> dashboard payload
 *   GET /api/health         -> liveness probe
 *
 * All rendering is done with DOM APIs (never innerHTML) so player names are
 * always treated as text, and all bracket rules live on the server.
 */
(() => {
  "use strict";

  const SIDES = [
    { key: "winners", title: "Winners bracket" },
    { key: "losers", title: "Losers bracket" },
    { key: "grand_final", title: "Grand final" },
  ];

  const STATUS_LABELS = {
    pending: "Pending",
    ready: "Ready",
    active: "Live",
    completed: "Final",
  };

  const THEME_KEY = "tourneycore.theme";
  const DEFAULT_REFRESH_MS = 10000;

  const state = {
    systems: [],
    currentId: null,
    dashboard: null,
    timer: null,
    refreshMs: DEFAULT_REFRESH_MS,
    loading: false,
  };

  const tg =
    window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

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

  /* ------------------------------------------------------------------ */
  /* Small helpers                                                       */
  /* ------------------------------------------------------------------ */
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function show(element, visible) {
    element.hidden = !visible;
  }

  function capitalize(value) {
    if (!value) return "";
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function setAlert(message) {
    if (!message) {
      dom.alert.textContent = "";
      show(dom.alert, false);
      return;
    }
    dom.alert.textContent = message;
    show(dom.alert, true);
  }

  /* ------------------------------------------------------------------ */
  /* Theme                                                               */
  /* ------------------------------------------------------------------ */
  function readStoredTheme() {
    try {
      return localStorage.getItem(THEME_KEY);
    } catch {
      return null;
    }
  }

  function storeTheme(theme) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* Private mode: the theme simply is not persisted. */
    }
  }

  function applyTheme(theme) {
    const resolved = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = resolved;
    dom.themeButton.textContent = resolved === "dark" ? "🌙" : "☀️";
    dom.themeButton.title =
      resolved === "dark" ? "Switch to light theme" : "Switch to dark theme";
  }

  function initialTheme() {
    const stored = readStoredTheme();
    if (stored === "dark" || stored === "light") return stored;
    if (tg && (tg.colorScheme === "dark" || tg.colorScheme === "light")) {
      return tg.colorScheme;
    }
    const prefersLight =
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches;
    return prefersLight ? "light" : "dark";
  }

  function setupTelegram() {
    if (!tg) return;
    try {
      tg.ready();
      tg.expand();
    } catch {
      /* Older clients may not implement every method. */
    }
    if (typeof tg.onEvent === "function") {
      tg.onEvent("themeChanged", () => {
        if (!readStoredTheme()) applyTheme(initialTheme());
      });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Data                                                                */
  /* ------------------------------------------------------------------ */
  async function fetchJson(path) {
    const response = await fetch(path, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        if (body && body.detail) detail = body.detail;
      } catch {
        /* Non-JSON error page. */
      }
      throw new Error(detail);
    }
    return response.json();
  }

  function configureRefresh() {
    const raw = Number(
      new URLSearchParams(window.location.search).get("refresh")
    );
    if (Number.isFinite(raw) && raw >= 3000 && raw <= 60000) {
      state.refreshMs = raw;
    }
  }

  async function loadSystems() {
    try {
      const data = await fetchJson("/api/systems");
      state.systems = Array.isArray(data.systems) ? data.systems : [];
      populateSystemSelect();

      if (!state.systems.length) {
        renderEmpty();
        return;
      }

      const preferred = new URLSearchParams(window.location.search).get(
        "system"
      );
      const target =
        state.systems.find((system) => system.id === preferred) ||
        state.systems.find((system) => system.status === "running") ||
        state.systems[0];

      dom.systemSelect.value = target.id;
      await loadDashboard({ systemId: target.id });
    } catch (error) {
      setAlert(`Could not load tournaments: ${error.message}`);
      dom.footerStatus.textContent = "Offline";
    }
  }

  function populateSystemSelect() {
    const options = state.systems.map((system) =>
      node("option", null, `${system.name} — ${system.status}`)
    );
    options.forEach((option, index) => {
      option.value = state.systems[index].id;
    });
    dom.systemSelect.replaceChildren(...options);
  }

  function selectSystem(systemId) {
    if (!systemId) return;
    state.currentId = systemId;
    dom.systemSelect.value = systemId;
    const url = new URL(window.location.href);
    url.searchParams.set("system", systemId);
    window.history.replaceState({}, "", url);
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
      setAlert("");
      render(data);
      const stamp = new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
      dom.footerStatus.textContent = `Updated ${stamp} · auto-refresh ${Math.round(
        state.refreshMs / 1000
      )}s`;
    } catch (error) {
      if (!silent) setAlert(`Could not load the bracket: ${error.message}`);
      dom.footerStatus.textContent = "Connection problem — retrying…";
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

  function connectStream() {
    if (!("EventSource" in window)) return;

    let stream;
    try {
      stream = new EventSource("/api/stream");
    } catch {
      return;
    }

    // Coalesce bursts of changes (one CLI command rewrites several files).
    let pending = null;
    stream.addEventListener("update", () => {
      if (pending) return;
      pending = window.setTimeout(() => {
        pending = null;
        if (!document.hidden) loadDashboard({ silent: true });
      }, 300);
    });
    // EventSource reconnects on its own; the interval stays as a fallback.
  }

  /* ------------------------------------------------------------------ */
  /* Rendering                                                           */
  /* ------------------------------------------------------------------ */
  function render(data) {
    show(dom.empty, false);
    renderHero(data);
    renderChampion(data.champion);
    renderStandings(data);
    renderBrackets(data);
  }

  function renderEmpty() {
    show(dom.empty, true);
    show(dom.hero, false);
    show(dom.champion, false);
    show(dom.standingsPanel, false);
    dom.brackets.replaceChildren();
    dom.systemSelect.replaceChildren();
    dom.footerStatus.textContent = "No tournaments";
  }

  function stat(value, label) {
    const card = node("div", "stat");
    card.append(node("div", "stat__value", value));
    card.append(node("div", "stat__label", label));
    return card;
  }

  function renderHero(data) {
    const system = data.system || {};
    const counts = data.counts || {};

    dom.heroTitle.textContent = system.name || "Tournament";
    dom.heroDescription.textContent = system.description || "";
    show(dom.heroDescription, Boolean(system.description));

    const status = capitalize(system.status);
    dom.heroStatus.textContent = system.registration_open
      ? `${status} · registration open`
      : status;

    dom.stats.replaceChildren(
      stat(counts.players || 0, "Players"),
      stat(counts.active || 0, "Live"),
      stat(counts.ready || 0, "Ready"),
      stat(counts.completed || 0, "Played")
    );

    show(dom.hero, true);
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
    dom.standingsHint.textContent =
      players.length === 1 ? "1 player" : `${players.length} players`;

    const rows = players.map((player, index) => {
      const row = node("tr");
      if (player.eliminated || player.withdrawn) row.classList.add("is-out");

      const nameCell = node("td", "player");
      nameCell.append(node("span", "player__name", player.name));
      if (player.seed) {
        nameCell.append(node("span", "player__seed", `#${player.seed}`));
      }

      const statusCell = node("td");
      statusCell.append(
        node(
          "span",
          player.eliminated || player.withdrawn ? "pill pill--muted" : "pill",
          player.status
        )
      );

      row.append(
        node("td", "rank", index + 1),
        nameCell,
        node("td", "record", `${player.wins}–${player.losses}`),
        statusCell
      );
      return row;
    });

    dom.standingsBody.replaceChildren(...rows);
  }

  function renderBrackets(data) {
    const brackets = data.brackets || {};
    const sections = [];

    for (const side of SIDES) {
      const matches = (brackets[side.key] || []).filter(
        (match) => !match.is_dead
      );
      if (matches.length) {
        sections.push(bracketSection(side.title, matches));
      }
    }

    if (!sections.length) {
      dom.brackets.replaceChildren(
        node("p", "bracket__empty", "The bracket has not been seeded yet.")
      );
      return;
    }

    dom.brackets.replaceChildren(...sections);
  }

  function bracketSection(title, matches) {
    const section = node("section", "bracket");

    const head = node("div", "bracket__head");
    head.append(node("h2", null, title));
    head.append(
      node(
        "span",
        "bracket__count",
        matches.length === 1 ? "1 match" : `${matches.length} matches`
      )
    );
    section.append(head);

    const rounds = new Map();
    for (const match of matches) {
      if (!rounds.has(match.round)) rounds.set(match.round, []);
      rounds.get(match.round).push(match);
    }

    const grid = node("div", "rounds");
    for (const round of [...rounds.keys()].sort((a, b) => a - b)) {
      const column = node("div", "round");
      column.append(node("h3", "round__title", `Round ${round}`));
      const list = node("div", "round__matches");
      for (const match of rounds.get(round)) list.append(matchCard(match));
      column.append(list);
      grid.append(column);
    }
    section.append(grid);
    return section;
  }

  function scoreText(score) {
    if (!score) return "";
    const first = score.player1;
    const second = score.player2;
    const hasFirst = first !== null && first !== undefined;
    const hasSecond = second !== null && second !== undefined;
    if (!hasFirst && !hasSecond) return "";
    return `${hasFirst ? first : "–"}–${hasSecond ? second : "–"}`;
  }

  function matchCard(match) {
    const card = node("article", `match match--${match.status}`);
    if (match.is_bye) card.classList.add("match--bye");

    const head = node("div", "match__head");
    head.append(node("span", "match__round", `R${match.round}`));
    head.append(
      node(
        "span",
        `pill pill--${match.status}`,
        STATUS_LABELS[match.status] || match.status
      )
    );
    card.append(head);

    card.append(slotRow(match, match.player1, 1));
    card.append(slotRow(match, match.player2, 2));

    if (match.is_bye) {
      const winner = match.winner ? match.winner.name : "next round";
      card.append(node("p", "match__note", `Bye → ${winner}`));
    } else if (match.status === "completed") {
      const score = scoreText(match.score);
      if (score) card.append(node("p", "match__note", `Score ${score}`));
    }

    return card;
  }

  function slotRow(match, player, slot) {
    const row = node("div", "slot");
    if (!player) {
      row.classList.add("slot--empty");
      row.append(node("span", "slot__name", "TBD"));
      return row;
    }

    const isWinner = Boolean(match.winner && match.winner.id === player.id);
    if (isWinner) row.classList.add("slot--winner");
    if (match.status === "completed" && !isWinner) {
      row.classList.add("slot--loser");
    }

    row.append(node("span", "slot__name", player.name));

    const score = slot === 1 ? match.score.player1 : match.score.player2;
    if (match.status === "completed" && score !== null && score !== undefined) {
      row.append(node("span", "slot__score", score));
    }
    return row;
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */
  function bindEvents() {
    dom.systemSelect.addEventListener("change", (event) => {
      selectSystem(event.target.value);
    });

    dom.refreshButton.addEventListener("click", () => {
      loadDashboard({ manual: true });
    });

    dom.themeButton.addEventListener("click", () => {
      const next =
        document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      storeTheme(next);
      applyTheme(next);
    });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) loadDashboard({ silent: true });
    });

    window.addEventListener("online", () => {
      loadDashboard({ silent: true });
    });
  }

  async function bootstrap() {
    applyTheme(initialTheme());
    setupTelegram();
    bindEvents();
    configureRefresh();
    await loadSystems();
    scheduleRefresh();
    connectStream();
  }

  bootstrap();
})();
