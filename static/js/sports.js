/**
 * Retro-TV Sports Center Client Controller
 * Powers the interactive sports screen, real-time scoreboard, SVG gameplay field radar,
 * Kokoro TTS play-by-play speech announcements, and on-TV remote sync.
 */

let ALL_GAMES = [];
let ALL_NEWS = [];
let SELECTED_GAME = null;
let SELECTED_GAME_DETAIL = null;
let CURRENT_FILTER = 'all';
let SEARCH_TIMER = null;
let SCORE_REFRESH_TIMER = null;

document.addEventListener('DOMContentLoaded', () => {
  initSports();
});

async function initSports() {
  await Promise.all([loadScores(), loadNews()]);
  setupKeyboardNav();
  startScorePolling();
}

function startScorePolling() {
  stopScorePolling();
  SCORE_REFRESH_TIMER = setInterval(() => {
    if (!document.hidden) {
      refreshScoresAndActiveGame();
    }
  }, 20000);
}

function stopScorePolling() {
  if (SCORE_REFRESH_TIMER) {
    clearInterval(SCORE_REFRESH_TIMER);
    SCORE_REFRESH_TIMER = null;
  }
}

async function refreshScoresAndActiveGame() {
  try {
    const res = await fetch('/api/sports/scores');
    const data = await res.json();
    if (data.games && data.games.length) {
      ALL_GAMES = data.games;
      if (SELECTED_GAME) {
        const updated = ALL_GAMES.find(g => String(g.id) === String(SELECTED_GAME.id));
        if (updated) {
          SELECTED_GAME = updated;
          setBillboardGame(updated);
        }
      }
      renderDashboard();
    }
    const modal = document.getElementById('gameModal');
    if (modal && !modal.hidden && SELECTED_GAME) {
      const sportParam = SELECTED_GAME.sport ? `?sport=${SELECTED_GAME.sport}` : '';
      const detRes = await fetch(`/api/sports/game/${SELECTED_GAME.league}/${SELECTED_GAME.id}${sportParam}`);
      const detData = await detRes.json();
      if (detData.game) {
        SELECTED_GAME_DETAIL = detData.game;
        renderModalField(SELECTED_GAME, SELECTED_GAME_DETAIL);
        renderModalPlays(SELECTED_GAME_DETAIL);
        renderModalBoxscore(SELECTED_GAME_DETAIL);
      }
    }
  } catch (err) {
    console.warn('Background sports telemetry refresh failed:', err);
  }
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    refreshScoresAndActiveGame();
  }
});

async function loadScores() {
  try {
    const res = await fetch('/api/sports/scores');
    const data = await res.json();
    ALL_GAMES = data.games || [];
    renderDashboard();
  } catch (err) {
    console.error('Failed to load sports scores:', err);
    document.getElementById('sportsRowsContainer').innerHTML = 
      '<div class="sports-error-state"><p>Could not connect to ArenaPulse sports radar. Please check service connection.</p></div>';
  }
}

async function loadNews() {
  try {
    const res = await fetch('/api/sports/news');
    const data = await res.json();
    ALL_NEWS = data.articles || [];
  } catch (err) {
    console.warn('Failed to load sports news:', err);
  }
}

function renderDashboard() {
  if (!ALL_GAMES.length) {
    document.getElementById('sportsRowsContainer').innerHTML = 
      '<div class="sports-empty-state"><p>No live or scheduled games found right now.</p></div>';
    return;
  }

  // Select spotlight game: preserve current selection if valid, else live game first, then close upcoming matchup
  const live = ALL_GAMES.filter(g => g.status?.isLive);
  const upcoming = ALL_GAMES.filter(g => g.status?.isScheduled);
  const featured = (SELECTED_GAME ? ALL_GAMES.find(g => String(g.id) === String(SELECTED_GAME.id)) : null) || live[0] || upcoming[0] || ALL_GAMES[0];
  setBillboardGame(featured);

  // Build category carousels
  const rows = [];
  if (live.length) {
    rows.push({ id: 'row-live', title: '🔴 Live Action In Progress', badge: `${live.length} LIVE`, games: live });
  }

  const football = ALL_GAMES.filter(g => g.sport === 'football');
  if (football.length) {
    rows.push({ id: 'row-football', title: '🏈 Football (NFL & College)', badge: `${football.length} GAMES`, games: football });
  }

  const baseball = ALL_GAMES.filter(g => g.sport === 'baseball');
  if (baseball.length) {
    rows.push({ id: 'row-baseball', title: '⚾ Baseball (MLB)', badge: `${baseball.length} GAMES`, games: baseball });
  }

  const basketball = ALL_GAMES.filter(g => g.sport === 'basketball');
  if (basketball.length) {
    rows.push({ id: 'row-basketball', title: '🏀 Basketball (NBA, WNBA & NCAA)', badge: `${basketball.length} GAMES`, games: basketball });
  }

  const soccer = ALL_GAMES.filter(g => g.sport === 'soccer');
  if (soccer.length) {
    rows.push({ id: 'row-soccer', title: '⚽ Soccer (Premier League, MLS, UEFA)', badge: `${soccer.length} MATCHES`, games: soccer });
  }

  const hockey = ALL_GAMES.filter(g => g.sport === 'hockey');
  if (hockey.length) {
    rows.push({ id: 'row-hockey', title: '🏒 Hockey (NHL)', badge: `${hockey.length} GAMES`, games: hockey });
  }

  const finals = ALL_GAMES.filter(g => g.status?.isFinal);
  if (finals.length) {
    rows.push({ id: 'row-finals', title: '🏆 Recent Final Scores', badge: `${finals.length} COMPLETED`, games: finals });
  }

  const container = document.getElementById('sportsRowsContainer');
  container.innerHTML = rows.map(r => renderRowHtml(r)).join('');
}

function renderRowHtml(row) {
  return `
    <section class="sports-row-section" id="${row.id}">
      <div class="sports-row-header">
        <h2 class="sports-row-title">
          <span>${esc(row.title)}</span>
          <span class="sports-badge-tag">${esc(row.badge)}</span>
        </h2>
        <div class="sports-row-arrows">
          <button class="btn ghost sports-arrow-btn" onclick="scrollCarousel('${row.id}', -1)" aria-label="Scroll left">‹</button>
          <button class="btn ghost sports-arrow-btn" onclick="scrollCarousel('${row.id}', 1)" aria-label="Scroll right">›</button>
        </div>
      </div>
      <div class="sports-carousel" id="${row.id}-carousel">
        ${row.games.map(g => renderGameCardHtml(g)).join('')}
      </div>
    </section>
  `;
}

function renderGameCardHtml(g) {
  const home = g.homeTeam || {};
  const away = g.awayTeam || {};
  const status = g.status || {};
  const isLive = !!status.isLive;
  const isFinal = !!status.isFinal;
  const league = (g.leagueName || g.league || '').toUpperCase();

  let badgeClass = 'sports-card-badge';
  let badgeText = league;
  if (isLive) {
    badgeClass += ' card-badge-live';
    badgeText = `● LIVE · ${status.period || ''}`;
  } else if (isFinal) {
    badgeClass += ' card-badge-final';
    badgeText = 'FINAL';
  }

  const awayScore = (isLive || isFinal) ? away.score : '--';
  const homeScore = (isLive || isFinal) ? home.score : '--';

  const awayRank = away.rank ? `<span class="sports-rank">#${away.rank}</span> ` : '';
  const homeRank = home.rank ? `<span class="sports-rank">#${home.rank}</span> ` : '';

  const sportIcon = getSportIcon(g.sport);

  return `
    <div class="sports-card ${isLive ? 'sports-card-live' : ''}" 
         tabindex="0"
         data-game-id="${esc(g.id)}" 
         data-league="${esc(g.league)}"
         onclick="onGameCardClick('${esc(g.league)}', '${esc(g.id)}')">
      <div class="${badgeClass}">${esc(badgeText)}</div>

      <!-- Teams & Scores -->
      <div class="sports-card-teams">
        <div class="sports-card-team-row">
          <div class="sports-card-team-name">
            ${away.logo ? `<img class="sports-team-logo-sm" src="${esc(away.logo)}" alt="" onerror="this.style.display='none'">` : ''}
            <span>${awayRank}${esc(away.shortDisplayName || away.abbreviation || 'Away')}</span>
          </div>
          <div class="sports-card-score">${esc(String(awayScore))}</div>
        </div>

        <div class="sports-card-team-row">
          <div class="sports-card-team-name">
            ${home.logo ? `<img class="sports-team-logo-sm" src="${esc(home.logo)}" alt="" onerror="this.style.display='none'">` : ''}
            <span>${homeRank}${esc(home.shortDisplayName || home.abbreviation || 'Home')}</span>
          </div>
          <div class="sports-card-score">${esc(String(homeScore))}</div>
        </div>
      </div>

      <!-- Mini Field Radar Graphic Box -->
      <div class="sports-card-field-preview">
        <span class="sports-card-sport-icon">${sportIcon}</span>
        <span class="sports-card-status-sub">${esc(status.shortDetail || status.detail || 'Scheduled')}</span>
      </div>

      <!-- Footer Info -->
      <div class="sports-card-footer">
        <span>${esc(g.odds?.details || g.broadcasts?.[0] || g.venue?.city || '')}</span>
      </div>
    </div>
  `;
}

function getSportIcon(sport) {
  switch ((sport || '').toLowerCase()) {
    case 'football': return '🏈';
    case 'baseball': return '⚾';
    case 'basketball': return '🏀';
    case 'hockey': return '🏒';
    case 'soccer': return '⚽';
    default: return '🏆';
  }
}

async function setBillboardGame(g) {
  if (!g) return;
  SELECTED_GAME = g;

  const home = g.homeTeam || {};
  const away = g.awayTeam || {};
  const status = g.status || {};
  const isLive = !!status.isLive;
  const isFinal = !!status.isFinal;

  document.getElementById('billboardAwayName').textContent = away.displayName || 'Away Team';
  document.getElementById('billboardAwayRec').textContent = away.recordSummary ? `(${away.recordSummary})` : '';
  document.getElementById('billboardAwayScore').textContent = (isLive || isFinal) ? away.score : '--';
  if (away.logo) {
    const el = document.getElementById('billboardAwayLogo');
    el.src = away.logo;
    el.style.display = 'inline-block';
  }

  document.getElementById('billboardHomeName').textContent = home.displayName || 'Home Team';
  document.getElementById('billboardHomeRec').textContent = home.recordSummary ? `(${home.recordSummary})` : '';
  document.getElementById('billboardHomeScore').textContent = (isLive || isFinal) ? home.score : '--';
  if (home.logo) {
    const el = document.getElementById('billboardHomeLogo');
    el.src = home.logo;
    el.style.display = 'inline-block';
  }

  document.getElementById('billboardLeague').textContent = (g.leagueName || g.league || 'SPORTS').toUpperCase();
  document.getElementById('billboardStatus').textContent = status.detail || (isLive ? 'Live' : 'Today');
  document.getElementById('billboardBroadcast').textContent = g.broadcasts?.[0] ? `TV: ${g.broadcasts.join(', ')}` : 'ESPN';
  document.getElementById('billboardOdds').textContent = g.odds?.details ? `Spread: ${g.odds.details}` : '';

  // Description
  const descParts = [];
  if (g.venue?.name) descParts.push(`Live from ${g.venue.name}`);
  if (g.venue?.city) descParts.push(`${g.venue.city}, ${g.venue.state || ''}`);
  if (g.weather?.temperature) descParts.push(`Weather: ${g.weather.temperature}°F ${g.weather.condition || ''}`);
  if (isLive && status.displayClock) descParts.push(`Clock: ${status.displayClock}`);
  document.getElementById('billboardDesc').textContent = descParts.join(' • ') || 'Live matchup telemetry and play-by-play.';

  // Status tag
  const tagEl = document.getElementById('billboardTag');
  if (isLive) {
    tagEl.className = 'sports-billboard-tag live-tag';
    tagEl.textContent = '● LIVE ACTION ON FIELD';
  } else if (isFinal) {
    tagEl.className = 'sports-billboard-tag';
    tagEl.textContent = '🏆 FINAL MATCH RESULT';
  } else {
    tagEl.className = 'sports-billboard-tag';
    tagEl.textContent = '★ FEATURED MATCHUP';
  }

  // Load gameplay field SVG
  loadBillboardField(g);
}

async function loadBillboardField(g) {
  const frame = document.getElementById('billboardFieldFrame');
  const statusEl = document.getElementById('billboardFieldStatus');

  try {
    const res = await fetch(`/api/sports/field/${g.sport || 'football'}/${g.league}/${g.id}`);
    const data = await res.json();
    if (data.svg) {
      frame.innerHTML = data.svg;
      statusEl.textContent = data.downDistance || data.situation || 'FIELD RADAR ACTIVE';
    }
  } catch (err) {
    frame.innerHTML = '<div class="sports-field-placeholder">Gameplay field radar ready</div>';
  }
}

// Carousel Horizontal Scroll
function scrollCarousel(rowId, dir) {
  const c = document.getElementById(`${rowId}-carousel`);
  if (!c) return;
  const amount = (c.clientWidth * 0.75) * dir;
  c.scrollBy({ left: amount, behavior: 'smooth' });
}

// Filter Chips
function filterSports(filter, btn) {
  CURRENT_FILTER = filter;
  document.querySelectorAll('.sports-chip').forEach(b => {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
  if (btn) {
    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
  }

  const newsSec = document.getElementById('sportsNewsSection');
  const catSec = document.getElementById('sportsCatalogSection');
  const searchSec = document.getElementById('sportsSearchResultsSection');

  searchSec.hidden = true;

  if (filter === 'news') {
    catSec.hidden = true;
    newsSec.hidden = false;
    renderNewsGrid();
    return;
  }

  newsSec.hidden = true;
  catSec.hidden = false;

  // Filter rows
  const allRows = document.querySelectorAll('.sports-row-section');
  allRows.forEach(row => {
    if (filter === 'all') {
      row.style.display = '';
    } else if (filter === 'live') {
      row.style.display = row.id === 'row-live' ? '' : 'none';
    } else if (filter === 'final') {
      row.style.display = row.id === 'row-finals' ? '' : 'none';
    } else {
      row.style.display = row.id === `row-${filter}` ? '' : 'none';
    }
  });
}

function renderNewsGrid() {
  const grid = document.getElementById('sportsNewsGrid');
  document.getElementById('sportsNewsCount').textContent = ALL_NEWS.length;
  if (!ALL_NEWS.length) {
    grid.innerHTML = '<p class="hint">No news articles fetched yet.</p>';
    return;
  }
  grid.innerHTML = ALL_NEWS.map(art => `
    <div class="sports-news-card">
      ${art.image ? `<div class="sports-news-img"><img src="${esc(art.image)}" alt="" onerror="this.parentElement.style.display='none'"></div>` : ''}
      <div class="sports-news-content">
        <div class="sports-pill">${esc((art.league || 'SPORTS').toUpperCase())}</div>
        <h3 class="sports-news-title">${esc(art.headline)}</h3>
        <p class="sports-news-desc">${esc(art.description || '')}</p>
        <div class="sports-news-footer">
          <span class="sports-news-byline">${esc(art.byline || 'Sports Wire')}</span>
          <button class="btn ghost sports-btn-sm" onclick="speakNewsArticle(${esc(JSON.stringify(art.headline + '. ' + (art.description || '')))})">🔊 Listen</button>
        </div>
      </div>
    </div>
  `).join('');
}

// Search
function onSportsSearchInput(val) {
  clearTimeout(SEARCH_TIMER);
  const q = (val || '').trim();
  const clearBtn = document.getElementById('sportsClearSearch');
  clearBtn.hidden = !q;

  if (!q) {
    document.getElementById('sportsSearchResultsSection').hidden = true;
    document.getElementById('sportsCatalogSection').hidden = false;
    return;
  }

  SEARCH_TIMER = setTimeout(() => executeSearch(q), 250);
}

function clearSportsSearch() {
  const input = document.getElementById('sportsSearch');
  input.value = '';
  document.getElementById('sportsClearSearch').hidden = true;
  document.getElementById('sportsSearchResultsSection').hidden = true;
  document.getElementById('sportsCatalogSection').hidden = false;
  input.focus();
}

function executeSearch(query) {
  const q = query.toLowerCase();
  const matches = ALL_GAMES.filter(g => {
    const text = `${g.name || ''} ${g.shortName || ''} ${g.league || ''} ${g.sport || ''} ${g.homeTeam?.displayName || ''} ${g.awayTeam?.displayName || ''}`.toLowerCase();
    return text.includes(q);
  });

  const searchSec = document.getElementById('sportsSearchResultsSection');
  const catSec = document.getElementById('sportsCatalogSection');
  const newsSec = document.getElementById('sportsNewsSection');
  const grid = document.getElementById('sportsSearchGrid');
  const countEl = document.getElementById('sportsSearchCount');

  newsSec.hidden = true;
  catSec.hidden = true;
  searchSec.hidden = false;

  countEl.textContent = matches.length;
  if (!matches.length) {
    grid.innerHTML = '<p class="hint">No games found matching your search.</p>';
    return;
  }
  grid.innerHTML = matches.map(g => renderGameCardHtml(g)).join('');
}

// Deep Game Center Modal
async function onGameCardClick(league, gameId) {
  const game = ALL_GAMES.find(g => g.id === gameId);
  if (game) {
    setBillboardGame(game);
  }
  openGameModal(league, gameId);
}

function openSelectedGameModal() {
  if (SELECTED_GAME) {
    openGameModal(SELECTED_GAME.league, SELECTED_GAME.id);
  }
}

async function openGameModal(league, gameId) {
  const modal = document.getElementById('gameModal');
  modal.hidden = false;
  document.body.style.overflow = 'hidden';

  const game = ALL_GAMES.find(g => g.id === gameId) || SELECTED_GAME || {};
  const home = game.homeTeam || {};
  const away = game.awayTeam || {};
  const status = game.status || {};
  const isLive = !!status.isLive;
  const isFinal = !!status.isFinal;

  document.getElementById('modalLeague').textContent = (game.leagueName || game.league || 'LEAGUE').toUpperCase();
  document.getElementById('modalGameTitle').textContent = `${away.displayName || 'Away'} @ ${home.displayName || 'Home'}`;
  document.getElementById('modalGameClock').textContent = status.detail || 'Scheduled';

  document.getElementById('modalAwayName').textContent = away.shortDisplayName || away.abbreviation || 'Away';
  document.getElementById('modalAwayScore').textContent = (isLive || isFinal) ? away.score : '--';
  if (away.logo) {
    const el = document.getElementById('modalAwayLogo');
    el.src = away.logo;
    el.style.display = 'inline-block';
  }

  document.getElementById('modalHomeName').textContent = home.shortDisplayName || home.abbreviation || 'Home';
  document.getElementById('modalHomeScore').textContent = (isLive || isFinal) ? home.score : '--';
  if (home.logo) {
    const el = document.getElementById('modalHomeLogo');
    el.src = home.logo;
    el.style.display = 'inline-block';
  }

  // Info tab fields
  document.getElementById('modalVenue').textContent = `${game.venue?.name || 'Stadium'}, ${game.venue?.city || ''}`;
  document.getElementById('modalWeather').textContent = game.weather ? `${game.weather.temperature}°F, ${game.weather.condition || ''}` : 'Dome / Weather N/A';
  document.getElementById('modalOdds').textContent = game.odds ? `${game.odds.details || ''} (O/U: ${game.odds.overUnder || 'N/A'})` : 'No betting lines';
  document.getElementById('modalBroadcast').textContent = (game.broadcasts || ['TV']).join(', ');

  // Fetch full game details (plays, boxscore, field)
  try {
    const res = await fetch(`/api/sports/game/${league}/${gameId}`);
    SELECTED_GAME_DETAIL = await res.json();
    renderModalField(game, SELECTED_GAME_DETAIL);
    renderModalPlays(SELECTED_GAME_DETAIL);
    renderModalBoxscore(SELECTED_GAME_DETAIL);
  } catch (err) {
    console.error('Failed to load game detail:', err);
  }
}

function renderModalField(game, detail) {
  const container = document.getElementById('modalFieldContainer');
  fetch(`/api/sports/field/${game.sport || 'football'}/${game.league}/${game.id}`)
    .then(r => r.json())
    .then(data => {
      if (data.svg) {
        container.innerHTML = data.svg;
      }
    })
    .catch(() => {
      container.innerHTML = '<div class="sports-field-placeholder">Field visualization ready</div>';
    });
}

function renderModalPlays(detail) {
  const listEl = document.getElementById('modalPlaysList');
  const plays = detail.visualPlays || detail.plays || [];

  if (!plays.length) {
    listEl.innerHTML = '<p class="hint">No play-by-play telemetry available for this game yet.</p>';
    return;
  }

  // Reverse plays to show latest first
  const displayPlays = [...plays].reverse().slice(0, 50);
  listEl.innerHTML = displayPlays.map(p => {
    const isScoring = !!p.scoringPlay;
    const text = p.text || '';
    const time = p.clock || p.time || '';
    const downDist = p.end?.downDistanceText || p.start?.downDistanceText || '';

    return `
      <div class="sports-play-item ${isScoring ? 'scoring-play' : ''}">
        <div class="sports-play-meta">
          <span class="sports-play-clock">${esc(time || (p.period || ''))}</span>
          ${downDist ? `<span class="sports-play-downdist">${esc(downDist)}</span>` : ''}
          ${isScoring ? '<span class="sports-play-scorebadge">★ SCORING PLAY</span>' : ''}
        </div>
        <div class="sports-play-text">${esc(text)}</div>
        <button class="btn ghost sports-btn-sm" onclick="speakPlayText(${esc(JSON.stringify(text))})">🔊 Speak</button>
      </div>
    `;
  }).join('');
}

function renderModalBoxscore(detail) {
  const leadersGrid = document.getElementById('modalLeadersGrid');
  const tableEl = document.getElementById('modalBoxscoreTable');

  // Leaders
  const leaders = detail.boxscore?.players || [];
  if (detail.header?.competitions?.[0]?.leaders) {
    const compLeaders = detail.header.competitions[0].leaders;
    leadersGrid.innerHTML = compLeaders.map(cat => {
      const leader = cat.leaders?.[0];
      if (!leader) return '';
      const ath = leader.athlete || {};
      return `
        <div class="sports-leader-card">
          <div class="sports-leader-title">${esc(cat.displayName || cat.name)}</div>
          <div class="sports-leader-row">
            ${ath.headshot ? `<img class="sports-leader-headshot" src="${esc(ath.headshot)}" alt="">` : ''}
            <div>
              <div class="sports-leader-name">${esc(ath.displayName || ath.name || 'Leader')}</div>
              <div class="sports-leader-stat">${esc(leader.displayValue || '')}</div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  } else {
    leadersGrid.innerHTML = '';
  }

  // Teams boxscore summary
  const teams = detail.boxscore?.teams || [];
  if (teams.length) {
    tableEl.innerHTML = `
      <table class="sports-table">
        <thead>
          <tr>
            <th>Team</th>
            <th>Statistics</th>
          </tr>
        </thead>
        <tbody>
          ${teams.map(t => `
            <tr>
              <td><b>${esc(t.team?.displayName || 'Team')}</b></td>
              <td>${(t.statistics || []).slice(0, 6).map(s => `<span>${esc(s.label)}: <b>${esc(s.displayValue)}</b></span>`).join(' &bull; ')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  } else {
    tableEl.innerHTML = '<p class="hint">Box score statistics will be posted during and after the game.</p>';
  }
}

function switchModalTab(tabName, btn) {
  document.querySelectorAll('.sports-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.sports-tab-content').forEach(c => {
    c.hidden = true;
    c.classList.remove('active');
  });

  if (btn) btn.classList.add('active');

  if (tabName === 'plays') {
    const el = document.getElementById('tabPlays');
    el.hidden = false;
    el.classList.add('active');
  } else if (tabName === 'boxscore') {
    const el = document.getElementById('tabBoxscore');
    el.hidden = false;
    el.classList.add('active');
  } else if (tabName === 'info') {
    const el = document.getElementById('tabInfo');
    el.hidden = false;
    el.classList.add('active');
  }
}

function closeGameModal(event) {
  if (event.target.id === 'gameModal') {
    hideGameModal();
  }
}

function hideGameModal() {
  document.getElementById('gameModal').hidden = true;
  document.body.style.overflow = '';
}

// TTS Announcer Audio Speech
async function playTTS(text, voice) {
  try {
    notify('🎙️ Synthesizing announcer audio...');
    const res = await fetch('/api/sports/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice })
    });
    if (!res.ok) throw new Error('TTS service unavailable');

    const blob = await res.blob();
    const audioUrl = URL.createObjectURL(blob);
    const audio = document.getElementById('ttsAudioPlayer');
    audio.src = audioUrl;
    audio.play();
    notify('🔊 Playing Kokoro Announcer audio');
  } catch (err) {
    notify('Speech synthesis error: ' + err.message);
  }
}

function speakCurrentMatchup() {
  if (!SELECTED_GAME) return;
  const away = SELECTED_GAME.awayTeam?.displayName || 'Away';
  const home = SELECTED_GAME.homeTeam?.displayName || 'Home';
  const status = SELECTED_GAME.status?.detail || 'Scheduled';
  const text = `Welcome to Retro Sports. Today's featured matchup: The ${away} versus the ${home}. Game status: ${status}.`;
  playTTS(text, 'am_michael');
}

function speakModalPlay() {
  if (SELECTED_GAME_DETAIL?.visualPlays?.length) {
    const p = SELECTED_GAME_DETAIL.visualPlays[SELECTED_GAME_DETAIL.visualPlays.length - 1];
    speakPlayText(p.text);
  } else {
    speakCurrentMatchup();
  }
}

function speakPlayText(text) {
  if (!text) return;
  playTTS(text, 'am_michael');
}

function speakNewsArticle(text) {
  playTTS(text, 'af_nicole');
}

// TV Actions
async function openGameOnTV(game = null) {
  const g = game || SELECTED_GAME;
  const payload = { action: 'open' };
  if (g) {
    payload.game_id = g.id;
    payload.league = g.league;
    payload.sport = g.sport;
  }
  try {
    const res = await fetch('/api/tv-sports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const st = await res.json();
    if (st.visible) {
      notify('📺 Sports Center opened on TV screen');
    } else {
      notify('Opened Sports on TV');
    }
  } catch (err) {
    notify('TV error: ' + err.message);
  }
}

async function openGameOnTVFromModal() {
  const g = SELECTED_GAME;
  hideGameModal();
  await openGameOnTV(g);
}

async function tuneSportsChannel() {
  try {
    const res = await fetch('/api/tune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: 38 })
    });
    notify('📡 Tuned to Channel 38 Retro Sports');
  } catch (err) {
    notify('Tune error: ' + err.message);
  }
}

function setupKeyboardNav() {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      hideGameModal();
    }
  });
}
