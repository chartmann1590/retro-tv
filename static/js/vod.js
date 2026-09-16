// Retroflix Video On Demand Frontend Logic
let VOD_CATALOG = null;
let SEARCH_TIMER = null;
let CURRENT_SHOW_DATA = null;
let FOCUSED_ROW_IDX = 0;
let FOCUSED_CARD_IDX = 0;

async function initVod() {
  await loadCatalog();
  setupKeyboardNav();
}

async function loadCatalog() {
  const container = document.getElementById('vodRowsContainer');
  try {
    const res = await tvApi('/api/vod/catalog');
    VOD_CATALOG = res;
    renderBillboard(res.featured);
    renderCategories(res.categories);
  } catch (err) {
    container.innerHTML = `<div class="warn">Failed to load On Demand library: ${esc(err.message)}</div>`;
  }
}

function renderBillboard(item) {
  if (!item) {
    document.getElementById('vodBillboard').hidden = true;
    return;
  }
  document.getElementById('vodBillboard').hidden = false;
  
  const backdrop = document.getElementById('billboardBackdrop');
  if (item.artwork) {
    backdrop.style.backgroundImage = `linear-gradient(to top, #0b1320 10%, rgba(11,19,32,0.7) 60%, rgba(11,19,32,0.95) 100%), url(${item.artwork})`;
  } else {
    backdrop.style.backgroundImage = 'none';
  }

  document.getElementById('billboardTitle').textContent = item.title || item.name || 'Featured Title';
  document.getElementById('billboardKind').textContent = (item.kind || 'movie').toUpperCase();
  document.getElementById('billboardYear').textContent = item.year || (item.season_count ? `${item.season_count} Seasons` : '');
  document.getElementById('billboardYear').hidden = !item.year && !item.season_count;
  document.getElementById('billboardQuality').textContent = item.resolution || 'HD';
  document.getElementById('billboardGenre').textContent = item.genre || '';
  document.getElementById('billboardDesc').textContent = item.description || 'Watch now on demand.';

  const playBtn = document.getElementById('billboardPlayTV');
  const browserBtn = document.getElementById('billboardWatchBrowser');
  const infoBtn = document.getElementById('billboardMoreInfo');

  if (item.kind === 'show') {
    playBtn.textContent = '📺 VIEW EPISODES';
    playBtn.onclick = () => openShowModal(item.id);
    browserBtn.hidden = true;
    infoBtn.onclick = () => openShowModal(item.id);
  } else {
    const mediaId = item.media_id;
    playBtn.textContent = '▶ PLAY ON TV';
    playBtn.onclick = () => playMediaOnTV(mediaId, item.title, item.year ? String(item.year) : '');
    browserBtn.hidden = false;
    browserBtn.onclick = () => { location.href = `/watch?vod=${mediaId}`; };
    infoBtn.onclick = () => openMovieModal(item);
  }
}

function renderCategories(categories) {
  const container = document.getElementById('vodRowsContainer');
  if (!categories || !categories.length) {
    container.innerHTML = '<div class="empty-state"><p>No on-demand titles found in library.</p></div>';
    return;
  }

  container.innerHTML = categories.map((cat, rowIdx) => {
    return `
      <section class="vod-row-section" data-row="${rowIdx}" data-cat-id="${esc(cat.id)}" data-cat-type="${esc(cat.type)}">
        <div class="vod-row-header">
          <h2 class="vod-row-title">${esc(cat.title)} <span class="vod-badge-tag">${esc(cat.badge || '')}</span></h2>
          <div class="vod-row-arrows">
            <button class="ghost vod-arrow-btn" onclick="scrollCarousel(${rowIdx}, -1)" aria-label="Scroll left">‹</button>
            <button class="ghost vod-arrow-btn" onclick="scrollCarousel(${rowIdx}, 1)" aria-label="Scroll right">›</button>
          </div>
        </div>
        <div class="vod-carousel" id="carousel-${rowIdx}">
          ${(cat.items || []).map((item, cardIdx) => renderCardHtml(item, rowIdx, cardIdx)).join('')}
        </div>
      </section>
    `;
  }).join('');
}

function renderCardHtml(item, rowIdx, cardIdx) {
  const title = esc(item.title || item.name || 'Untitled');
  const isShow = item.kind === 'show';
  const subtitle = esc(item.subtitle || (isShow ? `${item.episode_count || 0} Episodes` : `${item.year || ''}`));
  const badge = isShow ? 'SERIES' : (item.kind === 'episode' ? 'EPISODE' : 'MOVIE');
  const artwork = item.artwork ? `<img src="${esc(item.artwork)}" alt="${title}" loading="lazy" class="vod-card-img" onerror="this.style.display='none';this.nextElementSibling.hidden=false;">` : '';
  const fallback = `<div class="vod-card-fallback" ${item.artwork ? 'hidden' : ''}><span>${title.slice(0, 1).toUpperCase()}</span><small>${title}</small></div>`;

  return `
    <div class="vod-card" data-row="${rowIdx}" data-col="${cardIdx}" tabindex="0" onclick="onCardClick(${rowIdx}, ${cardIdx})" onkeydown="onCardKeydown(event, ${rowIdx}, ${cardIdx})">
      <div class="vod-card-media">
        ${artwork}
        ${fallback}
        <span class="vod-card-badge">${badge}</span>
      </div>
      <div class="vod-card-details">
        <h3 class="vod-card-title">${title}</h3>
        <p class="vod-card-sub">${subtitle}</p>
      </div>
      <div class="vod-card-hover-actions">
        <button class="btn vod-quick-play" onclick="onQuickPlay(event, ${rowIdx}, ${cardIdx})">▶ Play</button>
      </div>
    </div>
  `;
}

function getCardItem(rowIdx, cardIdx) {
  if (!VOD_CATALOG || !VOD_CATALOG.categories) return null;
  const cat = VOD_CATALOG.categories[rowIdx];
  return cat ? cat.items[cardIdx] : null;
}

function onCardClick(rowIdx, cardIdx) {
  const item = getCardItem(rowIdx, cardIdx);
  if (!item) return;
  if (item.kind === 'show') {
    openShowModal(item.id);
  } else if (item.kind === 'episode') {
    playMediaOnTV(item.media_id, item.title, item.subtitle);
  } else {
    openMovieModal(item);
  }
}

function onQuickPlay(event, rowIdx, cardIdx) {
  event.stopPropagation();
  const item = getCardItem(rowIdx, cardIdx);
  if (!item) return;
  if (item.kind === 'show') {
    openShowModal(item.id);
  } else {
    playMediaOnTV(item.media_id, item.title, item.subtitle || item.year);
  }
}

function scrollCarousel(rowIdx, direction) {
  const el = document.getElementById(`carousel-${rowIdx}`);
  if (!el) return;
  const card = el.querySelector('.vod-card');
  const cardWidth = card ? card.offsetWidth + 16 : 220;
  el.scrollBy({ left: direction * cardWidth * 3, behavior: 'smooth' });
}

// Category filter tabs
function filterCategory(type, btn) {
  document.querySelectorAll('.vod-chip').forEach(c => {
    c.classList.remove('active');
    c.setAttribute('aria-selected', 'false');
  });
  if (btn) {
    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
  }

  const rows = document.querySelectorAll('.vod-row-section');
  rows.forEach(r => {
    const catId = r.dataset.catId;
    const catType = r.dataset.catType;
    if (type === 'all') {
      r.hidden = false;
    } else if (type === 'recent') {
      r.hidden = catId !== 'recently-added';
    } else if (type === 'movies') {
      r.hidden = catType !== 'movie' && catId !== 'feature-films';
    } else if (type === 'shows') {
      r.hidden = catType !== 'show' && catId !== 'tv-series';
    }
  });
}

// Search functionality
function onVodSearchInput(q) {
  clearTimeout(SEARCH_TIMER);
  const clearBtn = document.getElementById('vodClearSearch');
  clearBtn.hidden = !q.trim();
  if (!q.trim()) {
    clearVodSearch();
    return;
  }
  SEARCH_TIMER = setTimeout(() => runVodSearch(q.trim()), 200);
}

async function runVodSearch(q) {
  const searchSection = document.getElementById('vodSearchResultsSection');
  const catalogSection = document.getElementById('vodCatalogSection');
  const grid = document.getElementById('vodSearchGrid');
  const countBadge = document.getElementById('vodSearchCount');

  try {
    const res = await tvApi('/api/vod/search?q=' + encodeURIComponent(q));
    const items = [...(res.movies || []), ...(res.shows || []), ...(res.episodes || [])];
    
    catalogSection.hidden = true;
    searchSection.hidden = false;
    countBadge.textContent = String(items.length);

    if (!items.length) {
      grid.innerHTML = '<div class="empty-state"><p>No titles matching your search.</p></div>';
      return;
    }

    grid.innerHTML = items.map((item, idx) => {
      const isShow = item.kind === 'show';
      const title = esc(item.title || item.name || 'Untitled');
      const subtitle = esc(item.subtitle || (isShow ? `${item.episode_count || 0} Episodes` : (item.year ? String(item.year) : (item.show_name || ''))));
      const badge = isShow ? 'SERIES' : (item.kind === 'episode' ? 'EPISODE' : 'MOVIE');
      const artwork = item.artwork ? `<img src="${esc(item.artwork)}" alt="${title}" loading="lazy" class="vod-card-img" onerror="this.style.display='none';this.nextElementSibling.hidden=false;">` : '';
      const fallback = `<div class="vod-card-fallback" ${item.artwork ? 'hidden' : ''}><span>${title.slice(0, 1).toUpperCase()}</span><small>${title}</small></div>`;

      return `
        <div class="vod-card search-card" tabindex="0" onclick="onSearchResultClick(${idx})" data-idx="${idx}">
          <div class="vod-card-media">
            ${artwork}
            ${fallback}
            <span class="vod-card-badge">${badge}</span>
          </div>
          <div class="vod-card-details">
            <h3 class="vod-card-title">${title}</h3>
            <p class="vod-card-sub">${subtitle}</p>
          </div>
        </div>
      `;
    }).join('');

    window._VOD_SEARCH_ITEMS = items;
  } catch (err) {
    grid.innerHTML = `<div class="warn">Search failed: ${esc(err.message)}</div>`;
  }
}

function clearVodSearch() {
  document.getElementById('vodSearch').value = '';
  document.getElementById('vodClearSearch').hidden = true;
  document.getElementById('vodSearchResultsSection').hidden = true;
  document.getElementById('vodCatalogSection').hidden = false;
}

function onSearchResultClick(idx) {
  const items = window._VOD_SEARCH_ITEMS;
  if (!items || !items[idx]) return;
  const item = items[idx];
  if (item.kind === 'show') {
    openShowModal(item.id);
  } else if (item.kind === 'episode') {
    playMediaOnTV(item.media_id, `${item.show_name} S${item.season}E${item.episode}`, item.title);
  } else {
    openMovieModal(item);
  }
}

// Show Modal
async function openShowModal(showId) {
  const modal = document.getElementById('vodShowModal');
  modal.hidden = false;
  document.body.classList.add('modal-open');

  try {
    const res = await tvApi('/api/vod/show/' + showId);
    CURRENT_SHOW_DATA = res.show;
    const s = res.show;
    
    document.getElementById('modalShowTitle').textContent = s.name;
    document.getElementById('modalShowSeasonCount').textContent = `${s.season_count || 1} Season${s.season_count === 1 ? '' : 's'}`;
    document.getElementById('modalShowEpCount').textContent = `${s.episode_count || 0} Episodes`;
    document.getElementById('modalShowDesc').textContent = s.description || 'No show description available.';

    const poster = document.getElementById('modalShowPoster');
    if (s.artwork) {
      poster.innerHTML = `<img src="${esc(s.artwork)}" alt="${esc(s.name)}">`;
    } else {
      poster.innerHTML = `<div class="vod-card-fallback"><span>${s.name.slice(0, 1).toUpperCase()}</span></div>`;
    }

    const select = document.getElementById('modalSeasonSelect');
    select.innerHTML = (s.seasons || []).map(sn => `<option value="${sn.season}">Season ${sn.season} (${sn.episodes.length} episodes)</option>`).join('');
    
    if (s.seasons && s.seasons.length > 0) {
      renderModalSeason(s.seasons[0].season);
    }
  } catch (err) {
    document.getElementById('modalEpisodesList').innerHTML = `<div class="warn">Could not load show: ${esc(err.message)}</div>`;
  }
}

function renderModalSeason(seasonNum) {
  if (!CURRENT_SHOW_DATA || !CURRENT_SHOW_DATA.seasons) return;
  const seasonObj = CURRENT_SHOW_DATA.seasons.find(s => String(s.season) === String(seasonNum));
  const list = document.getElementById('modalEpisodesList');
  if (!seasonObj || !seasonObj.episodes.length) {
    list.innerHTML = '<p class="hint">No episodes found in this season.</p>';
    return;
  }

  list.innerHTML = seasonObj.episodes.map(ep => {
    const epNum = `S${String(ep.season || 1).padStart(2, '0')}E${String(ep.episode || 1).padStart(2, '0')}`;
    const epTitle = esc(ep.title || `Episode ${ep.episode}`);
    const desc = esc(ep.description || '');
    const runtime = ep.runtime ? `${Math.round(ep.runtime / 60)} min` : '';
    const img = ep.artwork ? `<img src="${esc(ep.artwork)}" alt="${epTitle}" class="vod-ep-thumb" onerror="this.style.display='none'">` : '';

    return `
      <div class="vod-ep-row">
        <div class="vod-ep-thumb-wrap">${img}<span class="vod-ep-badge">${epNum}</span></div>
        <div class="vod-ep-main">
          <div class="vod-ep-header">
            <h4>${epTitle}</h4>
            <span class="vod-ep-runtime">${runtime}</span>
          </div>
          <p class="vod-ep-desc">${desc}</p>
        </div>
        <div class="vod-ep-actions">
          <button class="btn vod-play-btn" onclick="playMediaOnTV(${ep.media_id}, '${esc(CURRENT_SHOW_DATA.name)} ${epNum}', '${esc(ep.title)}')">▶ Play on TV</button>
          <a class="btn ghost vod-browser-btn" href="/watch?vod=${ep.media_id}">💻 Browser</a>
        </div>
      </div>
    `;
  }).join('');
}

function hideShowModal() {
  document.getElementById('vodShowModal').hidden = true;
  document.body.classList.remove('modal-open');
}

function closeShowModal(e) {
  if (e.target.id === 'vodShowModal') hideShowModal();
}

// Movie Modal
function openMovieModal(item) {
  const modal = document.getElementById('vodMovieModal');
  modal.hidden = false;
  document.body.classList.add('modal-open');

  document.getElementById('modalMovieTitle').textContent = item.title;
  document.getElementById('modalMovieYear').textContent = item.year || '--';
  document.getElementById('modalMovieDuration').textContent = item.duration ? `${Math.round(item.duration / 60)} min` : '--';
  document.getElementById('modalMovieQuality').textContent = item.resolution || '1080p';
  document.getElementById('modalMovieGenre').textContent = item.genre || '';
  document.getElementById('modalMovieDesc').textContent = item.description || 'No plot description available.';

  const poster = document.getElementById('modalMoviePoster');
  if (item.artwork) {
    poster.innerHTML = `<img src="${esc(item.artwork)}" alt="${esc(item.title)}">`;
  } else {
    poster.innerHTML = `<div class="vod-card-fallback"><span>${item.title.slice(0, 1).toUpperCase()}</span></div>`;
  }

  document.getElementById('modalMoviePlayTV').onclick = () => {
    playMediaOnTV(item.media_id, item.title, item.year ? String(item.year) : '');
    hideMovieModal();
  };
  document.getElementById('modalMovieWatchBrowser').onclick = () => {
    location.href = `/watch?vod=${item.media_id}`;
  };
}

function hideMovieModal() {
  document.getElementById('vodMovieModal').hidden = true;
  document.body.classList.remove('modal-open');
}

function closeMovieModal(e) {
  if (e.target.id === 'vodMovieModal') hideMovieModal();
}

// Playback Trigger
async function playMediaOnTV(mediaId, title, subtitle) {
  try {
    notify(`Tuning TV to "${title}" on demand…`);
    const res = await tvApi('/api/vod/play', { media_id: mediaId });
    if (res.ok) {
      notify(`▶ Now Playing on TV: ${title}`);
    } else {
      notify(`Playback error: ${res.error || 'Check player logs'}`);
    }
  } catch (err) {
    notify(`Failed to play on TV: ${err.message}`);
  }
}

// Keyboard Navigation (Arrow Keys + Enter)
function setupKeyboardNav() {
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    if (e.key === 'Escape') {
      hideShowModal();
      hideMovieModal();
      return;
    }
  });
}

function onCardKeydown(e, rowIdx, cardIdx) {
  if (e.key === 'Enter') {
    e.preventDefault();
    onCardClick(rowIdx, cardIdx);
  }
}

document.addEventListener('DOMContentLoaded', initVod);
