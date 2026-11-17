(() => {
  "use strict";

  const SIDES = [{ key: "winners", title: "Winners bracket" }, { key: "losers", title: "Losers bracket" }, { key: "grand_final", title: "Grand final" }];
  const STATUS_LABELS = { pending: "Pending", ready: "Ready", active: "Live", completed: "Final" };
  const STATUS_ICONS = { pending: "🟡", active: "🟢", completed: "🔴" };
  const THEME_KEY = "tourneycore.theme";
  const DEFAULT_REFRESH_MS = 10000;
  const API_BASE = (window.TOURNEYCORE_API_BASE || document.querySelector('meta[name="tourneycore-api"]')?.content || "").replace(/\/+$/, "");

  const state = { systems: [], currentId: null, dashboard: null, timer: null, refreshMs: DEFAULT_REFRESH_MS, loading: false, userRole: 'guest' };
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  const dom = {
    systemSelect: document.getElementById("system-select"), refreshButton: document.getElementById("refresh-button"),
    themeButton: document.getElementById("theme-button"), alert: document.getElementById("alert"),
    hero: document.getElementById("hero"), heroStatus: document.getElementById("hero-status"), heroTitle: document.getElementById("hero-title"),
    heroDescription: document.getElementById("hero-description"), stats: document.getElementById("stats"),
    champion: document.getElementById("champion"), championName: document.getElementById("champion-name"),
    standingsPanel: document.getElementById("standings-panel"), standingsBody: document.getElementById("standings-body"), standingsHint: document.getElementById("standings-hint"),
    brackets: document.getElementById("brackets"), empty: document.getElementById("empty-state"), footerStatus: document.getElementById("footer-status"),
  };

  // Add the showMatchInfo to global scope so inline onclick can find it
  window.showMatchInfo = function(matchId) {
      if(tg && tg.showAlert) {
          tg.showAlert(`Match ID: ${matchId}`);
      } else {
          alert(`Match ID: ${matchId}`);
      }
  };

  function node(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined && text !== null) el.textContent = String(text); return el; }
  function show(element, visible) { if (element) element.hidden = !visible; }
  function capitalize(value) { return value ? value.charAt(0).toUpperCase() + value.slice(1) : ""; }
  function setAlert(message) { dom.alert.textContent = message || ""; show(dom.alert, !!message); }

  function readStoredTheme() { try { return localStorage.getItem(THEME_KEY); } catch { return null; } }
  function storeTheme(theme) { try { localStorage.setItem(THEME_KEY, theme); } catch {} }
  function applyTheme(theme) { document.documentElement.dataset.theme = theme; dom.themeButton.textContent = theme === "dark" ? "🌙" : "☀️"; }
  function initialTheme() { const stored = readStoredTheme(); if (stored) return stored; if (tg && tg.colorScheme) return tg.colorScheme; return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; }

  function setupTelegram() { if (tg) { try { tg.ready(); tg.expand(); } catch {} } }

  async function fetchJson(path, options = {}) {
    const headers = { Accept: "application/json", ...options.headers };
    if (tg && tg.initData) headers['X-Telegram-Init-Data'] = tg.initData;
    const response = await fetch(API_BASE + path, { ...options, headers, cache: "no-store" });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { const body = await response.json(); if (body && body.detail) detail = body.detail; } catch {}
      throw new Error(detail);
    }
    return response.json();
  }

  function configureRefresh() { const raw = Number(new URLSearchParams(window.location.search).get("refresh")); if (raw >= 3000) state.refreshMs = raw; }

  async function loadSystems() {
    try {
      const data = await fetchJson("/api/systems");
      state.systems = Array.isArray(data.systems) ? data.systems : [];
      populateSystemSelect();
      if (!state.systems.length) return renderEmpty(data.userRole || 'guest');
      dom.systemSelect.value = state.systems[0].id;
      await loadDashboard({ systemId: state.systems[0].id });
    } catch (error) { setAlert(`Could not load tournaments: ${error.message}`); }
  }

  function populateSystemSelect() {
    const options = state.systems.map(s => node("option", null, `${STATUS_ICONS[s.status] || "⚪"} ${s.name}`));
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
      state.currentId = id; state.dashboard = data; state.userRole = data.userRole || 'guest'; setAlert(""); render(data);
    } catch (error) { if (!silent) setAlert(`Could not load bracket: ${error.message}`); }
    finally { state.loading = false; dom.refreshButton.classList.remove("is-spinning"); scheduleRefresh(); }
  }

  function scheduleRefresh() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = window.setInterval(() => { if (!document.hidden) loadDashboard({ silent: true }); }, state.refreshMs);
  }

  function stat(value, label) { const card = node("div", "stat"); card.append(node("div", "stat__value", value), node("div", "stat__label", label)); return card; }

  function render(data) {
    show(dom.empty, false); renderHero(data); renderChampion(data.champion); renderStandings(data); renderBrackets(data);
  }

  function renderEmpty(userRole) {
    show(dom.empty, true); show(dom.hero, false); show(dom.champion, false); show(dom.standingsPanel, false);
    dom.brackets.replaceChildren(); dom.systemSelect.replaceChildren();
    show(document.getElementById('admin-panel'), userRole === 'admin' || userRole === 'coworker');
    show(document.getElementById('promote-btn'), userRole === 'admin');
    dom.footerStatus.textContent = "No tournaments";
  }

  function renderHero(data) {
    const sys = data.system || {}, counts = data.counts || {}, role = data.userRole || 'guest';
    dom.heroTitle.textContent = sys.name || "Tournament"; dom.heroDescription.textContent = sys.description || "";
    show(dom.heroDescription, Boolean(sys.description));
    dom.heroStatus.textContent = sys.registration_open ? `${STATUS_ICONS[sys.status] || "⚪"} ${capitalize(sys.status)} · registration open` : `${STATUS_ICONS[sys.status] || "⚪"} ${capitalize(sys.status)}`;
    dom.stats.replaceChildren(stat(counts.players || 0, "Players"), stat(counts.active || 0, "Live"), stat(counts.ready || 0, "Ready"), stat(counts.completed || 0, "Played"));
    show(document.getElementById('admin-panel'), role === 'admin' || role === 'coworker');
    show(document.getElementById('player-panel'), role === 'player');
    show(document.getElementById('promote-btn'), role === 'admin');
    show(dom.hero, true);
    dom.footerStatus.textContent = `Role: ${role} | ${(tg && tg.initData) ? "Auth" : "No Auth"}`;
  }

  function renderChampion(champion) { if (!champion) { show(dom.champion, false); return; } dom.championName.textContent = champion.name; show(dom.champion, true); }

  function renderStandings(data) {
    const players = Array.isArray(data.players) ? data.players : [];
    show(dom.standingsPanel, players.length > 0);
    dom.standingsHint.textContent = `${players.length} players`;
    const rows = players.map((p, i) => {
      const r = node("tr"); if (p.eliminated || p.withdrawn) r.classList.add("is-out");
      const nameCell = node("td", "player"); nameCell.append(node("span", "player__name", p.name));
      if (p.seed) nameCell.append(node("span", "player__seed", `#${p.seed}`));
      const statCell = node("td"); statCell.append(node("span", p.eliminated || p.withdrawn ? "pill pill--muted" : "pill", p.status));
      r.append(node("td", "rank", i + 1), nameCell, node("td", "record", `${p.wins}–${p.losses}`), statCell);
      return r;
    });
    dom.standingsBody.replaceChildren(...rows);
  }

  function renderBrackets(data) {
    const brackets = data.brackets || {}, sections = [];
    for (const side of SIDES) {
      const matches = (brackets[side.key] || []).filter(m => !m.is_dead);
      if (matches.length) sections.push(bracketSection(side.title, matches));
    }
    if (!sections.length) { dom.brackets.replaceChildren(node("p", "bracket__empty", "The bracket has not been seeded yet.")); return; }
    dom.brackets.replaceChildren(...sections);
  }

  function bracketSection(title, matches) {
    const section = node("section", "bracket"), head = node("div", "bracket__head");
    head.append(node("h2", null, title), node("span", "bracket__count", `${matches.length} matches`));
    section.append(head);
    const rounds = new Map(); matches.forEach(m => { if (!rounds.has(m.round)) rounds.set(m.round, []); rounds.get(m.round).push(m); });
    const grid = node("div", "rounds");
    [...rounds.keys()].sort((a,b)=>a-b).forEach(r => {

      const col = node("div", "round");

      const roundTitle = node("div", "round__title", `Round ${r} - Playoffs`);
      col.append(roundTitle);

      const list = node("div", "round__matches");

      rounds.get(r).forEach(match => {
        const wrapper = document.createElement('div');

        let matchHtml = `
          <div class="match ${match.status === 'active' ? 'match--active' : ''}">
            
            <!-- دکمه i در سمت راست -->
            <div class="match__info" onclick="showMatchInfo('${match.id}')">i</div>

            <!-- بازیکن اول -->
            <div class="slot ${match.winner && match.winner.id === (match.player1 ? match.player1.id : null) ? 'slot--winner' : ''}">
              <div class="slot__logo">
                <img src="https://ui-avatars.com/api/?name=${match.player1 ? match.player1.name : '?'}&background=random&color=fff" alt="logo">
              </div>
              <div class="slot__name">${match.player1 ? match.player1.name : 'TBD'}</div>
              <div class="slot__score">${match.score ? match.score.player1 : ''}</div>
            </div>

            <!-- بازیکن دوم -->
            <div class="slot ${match.winner && match.winner.id === (match.player2 ? match.player2.id : null) ? 'slot--winner' : ''}">
              <div class="slot__logo">
                <img src="https://ui-avatars.com/api/?name=${match.player2 ? match.player2.name : '?'}&background=random&color=fff" alt="logo">
              </div>
              <div class="slot__name">${match.player2 ? match.player2.name : 'TBD'}</div>
              <div class="slot__score">${match.score ? match.score.player2 : ''}</div>
            </div>

          </div>
        `;

        wrapper.innerHTML = matchHtml;
        const card = wrapper.firstElementChild;

        if ((state.userRole === 'admin' || state.userRole === 'coworker') && !match.is_bye && match.status !== 'completed' && match.player1 && match.player2) {
          card.style.cursor = "pointer";
          card.style.border = "1px solid var(--accent)";
          card.title = "Click to record score";
          card.addEventListener("click", (e) => {
              if (e.target.classList.contains('match__info')) return;
              promptForScore(match);
          });
        }

        list.append(card);
      });

      col.append(list);
      grid.append(col);
    });
    section.append(grid); return section;
  }

  function promptForScore(match) {
    askPrompt(`Enter score for ${match.player1.name}:`, (score1Str) => {
      const s1 = parseInt(score1Str, 10);
      if (isNaN(s1)) return alert("Invalid score!");

      askPrompt(`Enter score for ${match.player2.name}:`, async (score2Str) => {
        const s2 = parseInt(score2Str, 10);
        if (isNaN(s2)) return alert("Invalid score!");

        let winner_id = null, loser_id = null;
        if (s1 > s2) { winner_id = match.player1.id; loser_id = match.player2.id; }
        else if (s2 > s1) { winner_id = match.player2.id; loser_id = match.player1.id; }
        else return alert("Ties are not allowed!");

        try {
          await fetchJson(`/api/matches/${match.id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ score_player1: s1, score_player2: s2, winner_id, loser_id })
          });
          await loadDashboard({ manual: true });
        } catch (err) { alert(`Error: ${err.message}`); }
      });
    });
  }

  function askConfirm(message, callback) { if (tg && tg.showConfirm) { tg.showConfirm(message, callback); } else { callback(confirm(message)); } }
  function askPrompt(message, callback) {
    if (tg && tg.showPopup) { const val = prompt(message); if(val) callback(val); } else { const val = prompt(message); if (val) callback(val); }
  }

  function bindEvents() {
    dom.systemSelect.addEventListener("change", (e) => selectSystem(e.target.value));
    dom.refreshButton.addEventListener("click", () => loadDashboard({ manual: true }));
    dom.themeButton.addEventListener("click", () => { const n = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; storeTheme(n); applyTheme(n); });

    document.getElementById('btn-create-tournament')?.addEventListener('click', () => {
      askPrompt("Enter tournament name:", async (name) => {
        try { await fetchJson('/api/systems', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); await loadSystems(); } catch (err) { alert(`Error: ${err.message}`); }
      });
    });

    document.getElementById('btn-delete-tournament')?.addEventListener('click', () => {
      if (!state.currentId) return alert("Select a tournament first!");
      askConfirm("Delete this tournament permanently?", async (ok) => {
        if (!ok) return;
        try { await fetchJson(`/api/systems/${state.currentId}`, { method: 'DELETE' }); await loadSystems(); } catch (err) { alert(`Error: ${err.message}`); }
      });
    });

    document.getElementById('btn-add-player')?.addEventListener('click', () => {
      if (!state.currentId) return alert("Select a tournament first!");
      askPrompt("Enter player's name:", async (name) => {
        try { await fetchJson(`/api/systems/${state.currentId}/players`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); await loadDashboard({ manual: true }); } catch (err) { alert(`Error: ${err.message}`); }
      });
    });

    document.getElementById('btn-start-bracket')?.addEventListener('click', () => {
      if (!state.currentId) return alert("Select a tournament first!");
      askConfirm("Start the bracket? Registration will be closed.", async (ok) => {
        if (!ok) return;
        try { await fetchJson(`/api/systems/${state.currentId}/start`, { method: 'POST' }); await loadDashboard({ manual: true }); } catch (err) { alert(`Error: ${err.message}`); }
      });
    });

    document.getElementById('promote-btn')?.addEventListener('click', () => {
      askPrompt("Enter Telegram ID to promote:", async (tgId) => {
        try { await fetchJson('/api/roles/promote', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ telegram_id: Number(tgId), new_role: 'coworker' }) }); alert("Promoted!"); } catch (err) { alert(`Error: ${err.message}`); }
      });
    });
  }

  async function bootstrap() { applyTheme(initialTheme()); setupTelegram(); bindEvents(); configureRefresh(); await loadSystems(); scheduleRefresh(); }
  bootstrap();
})();