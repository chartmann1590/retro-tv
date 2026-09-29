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
let leagueObservedPlay = '';
let leagueEventTimer = null;
let leagueTvChannel = null;
let leagueTvPreparingId = null;

async function leagueWaitForTvGame(channel, gameId, requestId) {
  for (let attempt = 0; attempt < 90; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    const response = await fetch('/api/tv-games/status');
    if (!response.ok) continue;
    const status = await response.json();
    if (status.request_id !== requestId || status.request_channel !== channel ||
        String(status.request_game_id) !== String(gameId)) {
      throw new Error('A different game selection replaced this request');
    }
    if (status.request_status === 'ready') return;
    if (status.request_status === 'failed') throw new Error(status.request_error || 'TV could not prepare this game');
  }
  throw new Error('TV preparation timed out. Check the TV and try again.');
}

function leagueScoringType(play, sport) {
  const text = String(play?.text || '').trim().toLowerCase();
  const kind = String(play?.type || '').toLowerCase();
  if (kind.includes('touchdown') || text.includes('touchdown')) return 'TOUCHDOWN';
  if (kind.includes('field goal') && play?.scoringPlay) return 'FIELD GOAL';
  if (text.startsWith('goal!') || (['soccer', 'hockey'].includes(sport) && kind.includes('goal') && play?.scoringPlay)) return 'GOAL';
  if (kind.includes('home run') || text.includes('home run')) return 'HOME RUN';
  if (play?.scoringPlay) return sport === 'soccer' || sport === 'hockey' ? 'GOAL' : 'SCORE';
  return '';
}

function leagueClearEvent() {
  clearTimeout(leagueEventTimer);
  document.getElementById('leagueEvent').hidden = true;
  document.getElementById('leagueBroadcast').classList.remove('celebrating');
}

function leagueCelebrate(play, sport) {
  const type = leagueScoringType(play, sport);
  if (!type) return;
  leagueClearEvent();
  leagueText('leagueEventType', type);
  leagueText('leagueEventKicker', '● LIVE SCORING PLAY');
  leagueText('leagueEventText', play.text || 'The score has changed.');
  document.getElementById('leagueEvent').hidden = false;
  document.getElementById('leagueBroadcast').classList.add('celebrating');
  leagueEventTimer = setTimeout(leagueClearEvent, 15000);
}

function leagueShowRecap(recap) {
  const panel = document.getElementById('leagueRecap');
  panel.hidden = !recap;
  if (!recap) return;
  leagueText('leagueRecapHeadline', recap.headline);
  leagueText('leagueRecapResult', recap.result);
  const highlights = document.getElementById('leagueRecapHighlights');
  highlights.className = 'league-recap-highlights';
  highlights.replaceChildren();
  for (const play of recap.highlights || []) {
    const row = document.createElement('div');
    row.className = 'league-recap-highlight';
    const label = document.createElement('b');
    label.textContent = `${play.label} ${play.clock || ''}`;
    const description = play.text || '';
    const short = description.length > 125 ? `${description.slice(0, 122).trimEnd()}…` : description;
    row.append(label, document.createTextNode(short));
    highlights.append(row);
  }
  const leader = document.getElementById('leagueRecapLeader');
  leader.className = 'league-recap-leader';
  leader.replaceChildren();
  if (recap.leader) {
    const name = document.createElement('b');
    name.textContent = recap.leader.name;
    leader.append(`${recap.leader.category}: `, name, ` · ${recap.leader.stat}`);
  }
}

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
    document.getElementById('leagueWatchTv').disabled = true;
    leagueClearEvent();
    leagueShowRecap(null);
    leagueCurrentKey = '';
    leagueLatestPlay = '';
    leagueNewestKey = '';
    leagueSpokenPlay = '';
    leagueObservedPlay = '';
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
  document.getElementById('leagueWatchTv').disabled = !!leagueTvPreparingId;
  leagueText('leagueWatchTv', leagueTvPreparingId ? 'PREPARING TV…' : '▶ WATCH GAME ON TV');
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
    leagueClearEvent();
    leagueShowRecap(null);
    leagueCurrentKey = key;
    leagueLatestPlay = '';
    leagueNewestKey = '';
    leagueSpokenPlay = '';
    leagueObservedPlay = '';
  }
  try {
    const route = `${encodeURIComponent(game.league)}/${encodeURIComponent(game.id)}`;
    const detailResponse = await fetch(`/api/sports/game/${route}?sport=${encodeURIComponent(game.sport || '')}&live=${status.isLive ? 1 : 0}&field=1`, {cache: 'no-store'});
    if (!detailResponse.ok) throw new Error('Game detail unavailable');
    const detail = await detailResponse.json();
    if (key !== leagueCurrentKey) return;
    document.getElementById('leagueField').innerHTML = detail.fieldSvg || '<span>Field view unavailable</span>';
    leagueShowRecap(status.isFinal ? detail.recap || null : null);
    if (status.isFinal) leagueClearEvent();
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
    const previousIndex = leagueObservedPlay ? plays.findIndex(play => leaguePlayKey(play) === leagueObservedPlay) : -1;
    const newPlays = previousIndex >= 0 ? plays.slice(previousIndex + 1) : newest ? [newest] : [];
    const scorePlay = status.isLive ? [...newPlays].reverse().find(play => leagueScoringType(play, game.sport)) : null;
    if (scorePlay) leagueCelebrate(scorePlay, game.sport);
    leagueObservedPlay = playKey;
    const recapText = detail.recap ? `${detail.recap.result} ${(detail.recap.highlights || [])[0]?.text || ''}`.trim() : '';
    leagueLatestPlay = status.isFinal ? recapText : newest && newest.text || '';
    leagueNewestKey = status.isFinal ? `recap:${key}` : scorePlay ? leaguePlayKey(scorePlay) : playKey;
    leagueText('leagueSpeak', leagueSpeechEnabled ? '🔊 ANNOUNCER ON' : status.isFinal ? '🔊 READ RECAP' : '🔊 READ PLAYS');
    if ((status.isLive || status.isFinal) && leagueSpeechEnabled && leagueNewestKey && leagueNewestKey !== leagueSpokenPlay) {
      leagueSpokenPlay = leagueNewestKey;
      if (scorePlay) leagueLatestPlay = scorePlay.text || leagueLatestPlay;
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
    const response = await fetch('/api/sports/scores?live=1', {cache: 'no-store'});
    if (!response.ok) throw new Error('Scores unavailable');
    const data = await response.json();
    if (data._unavailable) throw new Error('Scores unavailable');
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

function initLeagueBroadcast(league, timezone, channel) {
  leagueTimezone = timezone || leagueTimezone;
  leagueTvChannel = channel;
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
    leagueText('leagueSpeak', leagueSpeechEnabled ? '🔊 ANNOUNCER ON' : leagueSelectedGame?.status?.isFinal ? '🔊 READ RECAP' : '🔊 READ PLAYS');
    if (leagueSpeechEnabled && leagueLatestPlay) {
      leagueSpokenPlay = leagueNewestKey;
      leagueSpeak(leagueLatestPlay);
    } else if (leagueAudio) leagueAudio.pause();
  });
  document.getElementById('leagueWatchTv').addEventListener('click', async () => {
    const game = leagueSelectedGame;
    if (!game || leagueTvChannel == null) return;
    leagueTvPreparingId = String(game.id);
    document.getElementById('leagueWatchTv').disabled = true;
    leagueText('leagueWatchTv', 'PREPARING TV…');
    try {
      const response = await fetch('/api/tv-games', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'pin', channel: leagueTvChannel, game_id: game.id})
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'Game selection failed');
      await leagueWaitForTvGame(leagueTvChannel, game.id, result.request_id);
      leagueText('leagueUpdated', `${game.awayTeam?.abbreviation || 'AWAY'} @ ${game.homeTeam?.abbreviation || 'HOME'} selected for TV`);
      leagueText('leagueWatchTv', '✓ GAME SELECTED');
    } catch (error) {
      leagueText('leagueUpdated', error.message);
      leagueText('leagueWatchTv', '▶ WATCH GAME ON TV');
    } finally {
      leagueTvPreparingId = null;
      document.getElementById('leagueWatchTv').disabled = false;
    }
  });
  document.addEventListener('visibilitychange', () => {if (!document.hidden) leagueRefresh(league);});
  leagueRefresh(league);
  setInterval(() => leagueRefresh(league), 2000);
  setInterval(updateLeagueCountdown, 1000);
}
