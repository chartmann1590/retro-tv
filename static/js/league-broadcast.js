/* Live ArenaPulse score and play coverage over the scheduled league TV feed. */
let leagueGames = [];
let leagueGameIndex = 0;
let leagueCurrentKey = '';
let leagueLatestPlay = '';
let leagueNewestKey = '';
let leagueSpokenPlay = '';
let leagueSpeechEnabled = false;
let leagueAudio = null;
let leagueLoading = false;
let leagueSelectedGame = null;
let leagueTimezone = 'America/New_York';

function updateLeagueCountdown() {
  const game = leagueSelectedGame;
  const date = game?.date ? new Date(game.date) : null;
  if (!game?.status?.isScheduled || !date || Number.isNaN(date.getTime())) {
    leagueText('leagueCountdown', '');
    return;
  }
  leagueText('leagueClock', new Intl.DateTimeFormat([], {
    timeZone: leagueTimezone, weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short'
  }).format(date));
  const seconds = Math.max(0, Math.ceil((date.getTime() - Date.now()) / 1000));
  if (!seconds) {
    leagueText('leagueCountdown', 'STARTING NOW');
    return;
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const secs = seconds % 60;
  const clock = [hours, minutes, secs].map(value => String(value).padStart(2, '0')).join(':');
  leagueText('leagueCountdown', `LIVE IN ${days ? `${days}D ` : ''}${clock}`);
}

function leagueText(id, value) {
  document.getElementById(id).textContent = value == null ? '' : String(value);
}

function leagueLogo(id, url) {
  const img = document.getElementById(id);
  img.hidden = !url;
  if (url) img.src = url;
}

function leaguePlayKey(play) {
  return `${play.id || ''}:${play.clock || play.time || ''}:${play.text || ''}`;
}

async function leagueSpeak(text) {
  if (!text) return;
  try {
    const response = await fetch('/api/sports/tts', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text})
    });
    if (!response.ok) throw new Error('Announcer unavailable');
    if (leagueAudio) {
      leagueAudio.pause();
      URL.revokeObjectURL(leagueAudio.src);
    }
    const url = URL.createObjectURL(await response.blob());
    leagueAudio = new Audio(url);
    leagueAudio.onended = () => URL.revokeObjectURL(url);
    await leagueAudio.play();
  } catch (error) {
    leagueText('leagueUpdated', 'Commentary unavailable');
  }
}

async function leagueRenderGame() {
  const game = leagueGames[leagueGameIndex];
  leagueSelectedGame = game || null;
  if (!game) {
    leagueCurrentKey = '';
    leagueLatestPlay = '';
    leagueNewestKey = '';
    leagueSpokenPlay = '';
    if (leagueAudio) leagueAudio.pause();
    leagueText('leagueAwayName', 'AWAY');
    leagueText('leagueHomeName', 'HOME');
    leagueText('leagueAwayRecord', '');
    leagueText('leagueHomeRecord', '');
    leagueText('leagueAwayScore', '--');
    leagueText('leagueHomeScore', '--');
    leagueLogo('leagueAwayLogo', '');
    leagueLogo('leagueHomeLogo', '');
    leagueText('leagueVenue', '');
    leagueText('leaguePlayCount', '0 PLAYS');
    document.getElementById('leagueLive').classList.remove('on');
    leagueText('leagueGameIndex', 'No games on the board');
    leagueText('leagueStatus', 'STANDBY');
    leagueText('leagueClock', 'Coverage resumes with the next matchup');
    updateLeagueCountdown();
    leagueText('leaguePlayList', 'ArenaPulse has no scheduled or live games for this league right now.');
    leagueText('leagueField', 'Field view appears when a matchup is available.');
    return;
  }
  const key = `${game.league}:${game.id}`;
  const away = game.awayTeam || {};
  const home = game.homeTeam || {};
  const status = game.status || {};
  const scored = status.isLive || status.isFinal;
  leagueText('leagueAwayName', away.shortDisplayName || away.displayName || away.abbreviation || 'Away');
  leagueText('leagueHomeName', home.shortDisplayName || home.displayName || home.abbreviation || 'Home');
  leagueText('leagueAwayRecord', away.recordSummary || '');
  leagueText('leagueHomeRecord', home.recordSummary || '');
  leagueText('leagueAwayScore', scored ? away.score : '--');
  leagueText('leagueHomeScore', scored ? home.score : '--');
  leagueLogo('leagueAwayLogo', away.logo);
  leagueLogo('leagueHomeLogo', home.logo);
  leagueText('leagueStatus', status.isLive ? '● LIVE' : status.isFinal ? 'FINAL' : 'UPCOMING');
  leagueText('leagueClock', status.shortDetail || status.detail || '');
  updateLeagueCountdown();
  leagueText('leagueGameIndex', `${leagueGameIndex + 1} / ${leagueGames.length} · ${away.abbreviation || 'AWAY'} @ ${home.abbreviation || 'HOME'}`);
  leagueText('leagueVenue', (game.venue || {}).name || '');
  document.getElementById('leagueLive').classList.toggle('on', !!status.isLive);
  if (key !== leagueCurrentKey) {
    leagueCurrentKey = key;
    leagueLatestPlay = '';
    leagueNewestKey = '';
    leagueSpokenPlay = '';
  }
  try {
    const route = `${encodeURIComponent(game.league)}/${encodeURIComponent(game.id)}`;
    const [detailResponse, fieldResponse] = await Promise.all([
      fetch(`/api/sports/game/${route}?sport=${encodeURIComponent(game.sport || '')}`),
      fetch(`/api/sports/field/${encodeURIComponent(game.sport || 'football')}/${route}`)
    ]);
    if (!detailResponse.ok || !fieldResponse.ok) throw new Error('Game detail unavailable');
    const detail = await detailResponse.json();
    const field = await fieldResponse.json();
    if (key !== leagueCurrentKey) return;
    document.getElementById('leagueField').innerHTML = field.svg || '<span>Field view unavailable</span>';
    const plays = detail.visualPlays || detail.plays || [];
    leagueText('leaguePlayCount', `${plays.length} PLAYS`);
    const list = document.getElementById('leaguePlayList');
    list.replaceChildren();
    if (!plays.length) leagueText('leaguePlayList', 'Play by play begins when the game starts.');
    for (const play of [...plays].reverse().slice(0, 5)) {
      const row = document.createElement('div');
      row.className = `league-play${play.scoringPlay ? ' scoring' : ''}`;
      const clock = document.createElement('small');
      clock.textContent = play.clock || play.time || play.period || 'PLAY';
      const body = document.createElement('span');
      body.textContent = play.text || '';
      row.append(clock, body);
      list.append(row);
    }
    const newest = plays.at(-1);
    const playKey = newest ? leaguePlayKey(newest) : '';
    leagueLatestPlay = newest && newest.text || '';
    leagueNewestKey = playKey;
    if (status.isLive && leagueSpeechEnabled && playKey && playKey !== leagueSpokenPlay) {
      leagueSpokenPlay = playKey;
      leagueSpeak(leagueLatestPlay);
    }
  } catch (error) {
    leagueText('leaguePlayList', 'Game updates are temporarily unavailable. Retrying…');
  }
}

async function leagueRefresh(league) {
  if (leagueLoading || document.hidden) return;
  leagueLoading = true;
  try {
    const response = await fetch('/api/sports/scores');
    if (!response.ok) throw new Error('Scores unavailable');
    const data = await response.json();
    const selected = leagueGames[leagueGameIndex];
    leagueGames = (data.games || []).filter(game => game.league === league).sort((a, b) => {
      const rank = game => game.status?.isLive ? 0 : game.status?.isScheduled ? 1 : 2;
      return rank(a) - rank(b);
    });
    leagueGameIndex = Math.max(0, leagueGames.findIndex(game => selected && String(game.id) === String(selected.id)));
    const info = (data.leagues || []).find(item => item.id === league);
    leagueText('leagueName', info?.name || league.toUpperCase());
    leagueText('leagueUpdated', `UPDATED ${new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}`);
    await leagueRenderGame();
  } catch (error) {
    leagueText('leagueUpdated', 'ArenaPulse reconnecting…');
  } finally {
    leagueLoading = false;
  }
}

function initLeagueBroadcast(league, timezone) {
  leagueTimezone = timezone || leagueTimezone;
  const video = document.getElementById('v');
  video.muted = true;
  document.getElementById('watchMute').textContent = 'UNMUTE';
  const switchGame = delta => {
    if (!leagueGames.length) return;
    leagueGameIndex = (leagueGameIndex + delta + leagueGames.length) % leagueGames.length;
    leagueRenderGame();
  };
  document.getElementById('leaguePrev').addEventListener('click', () => switchGame(-1));
  document.getElementById('leagueNext').addEventListener('click', () => switchGame(1));
  document.getElementById('leagueSpeak').addEventListener('click', () => {
    leagueSpeechEnabled = !leagueSpeechEnabled;
    leagueText('leagueSpeak', leagueSpeechEnabled ? '🔊 ANNOUNCER ON' : '🔇 READ PLAYS');
    if (leagueSpeechEnabled && leagueLatestPlay) {
      leagueSpokenPlay = leagueNewestKey;
      leagueSpeak(leagueLatestPlay);
    } else if (leagueAudio) leagueAudio.pause();
  });
  document.addEventListener('visibilitychange', () => {if (!document.hidden) leagueRefresh(league);});
  leagueRefresh(league);
  setInterval(() => leagueRefresh(league), 20000);
  setInterval(updateLeagueCountdown, 1000);
}
