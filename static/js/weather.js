'use strict';
function getConditionSvg(condition, isDay = true, size = 48) {
  const c = (condition || '').toLowerCase();
  if (c.includes('thunder') || c.includes('storm')) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none"><path d="M20 28a12 12 0 0 1 23-4 10 10 0 0 1 11 10 8 8 0 0 1-8 8H16a8 8 0 0 1 0-16 12 12 0 0 1 4 2z" fill="#7a8b9e"/><polygon points="30,40 24,52 32,52 28,64 42,48 34,48" fill="#f8cb63"/></svg>`;
  }
  if (c.includes('rain') || c.includes('shower') || c.includes('drizzle')) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none"><path d="M20 26a12 12 0 0 1 23-4 10 10 0 0 1 11 10 8 8 0 0 1-8 8H16a8 8 0 0 1 0-16 12 12 0 0 1 4 2z" fill="#8da4be"/><line x1="22" y1="44" x2="18" y2="54" stroke="#4aa3df" stroke-width="3" stroke-linecap="round"/><line x1="32" y1="44" x2="28" y2="54" stroke="#4aa3df" stroke-width="3" stroke-linecap="round"/><line x1="42" y1="44" x2="38" y2="54" stroke="#4aa3df" stroke-width="3" stroke-linecap="round"/></svg>`;
  }
  if (c.includes('snow') || c.includes('ice') || c.includes('sleet')) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none"><path d="M20 26a12 12 0 0 1 23-4 10 10 0 0 1 11 10 8 8 0 0 1-8 8H16a8 8 0 0 1 0-16 12 12 0 0 1 4 2z" fill="#b8c9dc"/><circle cx="22" cy="48" r="3" fill="#fff"/><circle cx="32" cy="52" r="3" fill="#fff"/><circle cx="42" cy="48" r="3" fill="#fff"/></svg>`;
  }
  if (c.includes('cloud') || c.includes('overcast')) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none"><circle cx="24" cy="24" r="10" fill="#f8cb63"/><path d="M24 32a12 12 0 0 1 23-4 10 10 0 0 1 11 10 8 8 0 0 1-8 8H20a8 8 0 0 1 0-16 12 12 0 0 1 4 2z" fill="#9db0c6"/></svg>`;
  }
  if (!isDay) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none"><path d="M42 16a18 18 0 1 1-24 24 16 16 0 0 0 24-24z" fill="#f8cb63"/></svg>`;
  }
  return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="14" fill="#f8cb63"/><g stroke="#f8cb63" stroke-width="3" stroke-linecap="round"><line x1="32" y1="6" x2="32" y2="12"/><line x1="32" y1="52" x2="32" y2="58"/><line x1="6" y1="32" x2="12" y2="32"/><line x1="52" y1="32" x2="58" y2="32"/><line x1="14" y1="14" x2="18" y2="18"/><line x1="46" y1="46" x2="50" y2="50"/><line x1="14" y1="50" x2="18" y2="46"/><line x1="46" y1="18" x2="50" y2="14"/></g></svg>`;
}

function loadWeather() {
  fetch('/api/weather')
    .then(r => r.json())
    .then(data => {
      if (!data) return;
      document.getElementById('weatherLocation').textContent = `${data.city || 'Local'}, ${data.state || ''}`;
      
      const forecast = data.forecast || [];
      const hourly = data.hourly || [];
      
      if (forecast.length > 0) {
        const cur = forecast[0];
        document.getElementById('currentTemp').innerHTML = `${cur.temperature}&deg;<span class="unit">${cur.temperatureUnit || 'F'}</span>`;
        document.getElementById('currentCondition').textContent = cur.shortForecast || 'Fair';
        document.getElementById('weatherNarrative').textContent = cur.detailedForecast || gettext("No detailed forecast available.");
        document.getElementById('weatherEmoji').innerHTML = getConditionSvg(cur.shortForecast, cur.isDaytime !== false, 72);
        
        document.getElementById('metricFeels').innerHTML = `${cur.temperature}&deg;`;
        document.getElementById('metricWind').textContent = cur.windSpeed || '-- mph';
        
        if (data.updated_at) {
          const dt = new Date(data.updated_at);
          document.getElementById('weatherUpdated').textContent = dt.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
        }
      }

      // Render Hourly
      const hourlyTrack = document.getElementById('hourlyTrack');
      if (hourly.length > 0) {
        hourlyTrack.innerHTML = '';
        hourly.slice(0, 14).forEach(h => {
          const card = document.createElement('div');
          card.className = 'hourly-card';
          const dt = new Date(h.startTime);
          const timeStr = dt.toLocaleTimeString([], {hour: 'numeric', hour12: true});
          const svg = getConditionSvg(h.shortForecast, h.isDaytime !== false, 32);
          card.innerHTML = `
            <div class="h-time">${timeStr}</div>
            <div class="h-icon">${svg}</div>
            <div class="h-temp">${h.temperature}&deg;</div>
            <div class="h-cond">${h.shortForecast}</div>
            <div class="h-wind">${h.windSpeed || ''}</div>
          `;
          hourlyTrack.appendChild(card);
        });
      } else {
        hourlyTrack.innerHTML = `<div class="weather-empty">${esc(gettext("Hourly forecast unavailable"))}</div>`;
      }

      // Render 7-day
      const daysGrid = document.getElementById('forecastDaysGrid');
      if (forecast.length > 0) {
        daysGrid.innerHTML = '';
        forecast.slice(0, 10).forEach(f => {
          const card = document.createElement('div');
          card.className = 'forecast-day-card';
          const svg = getConditionSvg(f.shortForecast, f.isDaytime !== false, 40);
          card.innerHTML = `
            <div class="f-day-name">${f.name}</div>
            <div class="f-icon">${svg}</div>
            <div class="f-temp">${f.temperature}&deg;<span class="f-unit">${f.temperatureUnit || 'F'}</span></div>
            <div class="f-cond">${f.shortForecast}</div>
            <div class="f-wind">${f.windSpeed || ''} ${f.windDirection || ''}</div>
          `;
          daysGrid.appendChild(card);
        });
      }
    })
    .catch(err => {
      console.warn('Weather fetch error:', err);
    });
}

let isPlayingAudio = false;
function toggleWeatherAudio() {
  const audio = document.getElementById('weatherAudio');
  const icon = document.getElementById('audioIcon');
  const text = document.getElementById('audioText');
  
  if (!isPlayingAudio) {
    audio.src = '/data/live_content/weather/parts/01.mp3?' + Date.now();
    audio.play().then(() => {
      isPlayingAudio = true;
      icon.textContent = '⏹';
      text.textContent = gettext("STOP NARRATION");
    }).catch(e => {
      console.log('Audio playback error', e);
    });
  } else {
    audio.pause();
    isPlayingAudio = false;
    icon.textContent = '🔊';
    text.textContent = gettext("LISTEN TO FORECAST");
  }
}

function onAudioEnded() {
  isPlayingAudio = false;
  document.getElementById('audioIcon').textContent = '🔊';
  document.getElementById('audioText').textContent = gettext('LISTEN TO FORECAST');
}

loadWeather();
setInterval(loadWeather, 60000);
