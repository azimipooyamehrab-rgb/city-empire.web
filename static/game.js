(() => {
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => new Intl.NumberFormat('fa-IR').format(Number(n || 0));
  const params = new URLSearchParams(window.location.search);
  let token = sessionStorage.getItem('cityEmpireToken') || '';
  let latestState = null;
  let busy = false;

  function notify(message, isError = false) {
    const box = $('notice');
    box.textContent = message;
    box.classList.toggle('error', isError);
    box.classList.remove('hidden');
    window.clearTimeout(notify.timer);
    notify.timer = window.setTimeout(() => box.classList.add('hidden'), 6500);
  }

  function render(state) {
    latestState = state;
    $('gems').textContent = fmt(state.gems);
    $('wood').textContent = fmt(state.wood);
    $('iron').textContent = fmt(state.iron);
    $('soldiers').textContent = fmt(state.soldiers);
    $('level').textContent = fmt(state.level);
    $('citizens').textContent = fmt(state.citizens);
    $('houses').textContent = fmt(state.houses);
    $('dragons').textContent = fmt(state.dragons);
    $('wins').textContent = fmt(state.wins);
    $('reputation').textContent = `${fmt(state.reputation)}٪`;
    $('repBar').style.width = `${Math.max(0, Math.min(100, Number(state.reputation || 0)))}%`;
    $('xpText').textContent = `${fmt(state.xp)} / ${fmt(state.xp_needed)} XP`;
    $('xpBar').style.width = `${Math.max(0, Math.min(100, 100 * Number(state.xp || 0) / Math.max(1, Number(state.xp_needed || 100)) ))}%`;
    $('commanderName').textContent = state.name || 'فرمانده';
    $('cityTitle').textContent = `امپراتوری ${state.name || 'من'}`;
    $('citizenScene').textContent = `${fmt(state.citizens)} شهروند`;
    $('houseScene').textContent = `${fmt(state.houses)} خانه`;
    $('shieldState').textContent = state.shield_seconds > 0 ? `🛡️ سپر فعال است · ${fmt(state.shield_seconds)} ثانیه` : '🛡️ سپر دفاعی غیرفعال';
    $('eventTimer').textContent = `رویداد بعدی: ${fmt(state.event_seconds)} ثانیه`;
    const list = $('activityList');
    list.replaceChildren();
    (state.log || []).slice(0, 6).forEach((entry) => {
      const item = document.createElement('div');
      item.className = 'activity-item';
      const dot = document.createElement('span');
      dot.className = 'activity-dot';
      const text = document.createElement('span');
      text.textContent = entry;
      item.append(dot, text);
      list.appendChild(item);
    });
    if (!list.children.length) list.innerHTML = '<div class="loading-row">هنوز گزارشی ثبت نشده است.</div>';
  }

  async function api(path, body) {
    const options = { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json' } };
    if (token) options.headers.Authorization = `Bearer ${token}`;
    if (body) options.body = JSON.stringify(body);
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || 'ارتباط با سرور ناموفق بود.');
    return data;
  }

  async function enterGame() {
    const launch = params.get('launch');
    if (launch) {
      try {
        const data = await api('/api/session', { launch });
        token = data.token;
        sessionStorage.setItem('cityEmpireToken', token);
        render(data.state);
        window.history.replaceState({}, document.title, window.location.pathname);
        return;
      } catch (error) {
        notify(error.message, true);
      }
    }
    if (!token) {
      $('activityList').innerHTML = '<div class="loading-row">برای ورود امن، ابتدا در ربات بله فرمان /play را بفرست و از دکمهٔ ورود استفاده کن.</div>';
      notify('این نسخه برای اتصال به ربات بله طراحی شده است. در بله /play را بفرست و از لینک اختصاصی وارد شو.', true);
      return;
    }
    try {
      const data = await api('/api/state');
      render(data.state);
      if (data.event) notify(data.event);
    } catch (error) {
      sessionStorage.removeItem('cityEmpireToken');
      token = '';
      notify(error.message, true);
    }
  }

  async function runAction(action, button) {
    if (busy) return;
    if (!token) {
      notify('ابتدا از طریق دکمهٔ ورود در ربات بله وارد بازی شو.', true);
      return;
    }
    busy = true;
    $('busyOverlay').classList.remove('hidden');
    document.querySelectorAll('[data-action]').forEach((el) => { el.disabled = true; });
    try {
      const data = await api('/api/action', { action });
      render(data.state);
      notify([data.message, data.event].filter(Boolean).join(' '));
    } catch (error) {
      notify(error.message, true);
    } finally {
      busy = false;
      $('busyOverlay').classList.add('hidden');
      document.querySelectorAll('[data-action]').forEach((el) => { el.disabled = false; });
    }
  }

  document.querySelectorAll('[data-action]').forEach((button) => {
    button.addEventListener('click', () => runAction(button.dataset.action, button));
  });
  $('refreshButton').addEventListener('click', async () => {
    try { const data = await api('/api/state'); render(data.state); if (data.event) notify(data.event); }
    catch (error) { notify(error.message, true); }
  });
  enterGame();
  window.setInterval(() => {
    if (latestState) {
      latestState.event_seconds = Math.max(0, Number(latestState.event_seconds || 0) - 1);
      if (latestState.shield_seconds > 0) latestState.shield_seconds -= 1;
      $('eventTimer').textContent = `رویداد بعدی: ${fmt(latestState.event_seconds)} ثانیه`;
      $('shieldState').textContent = latestState.shield_seconds > 0 ? `🛡️ سپر فعال است · ${fmt(latestState.shield_seconds)} ثانیه` : '🛡️ سپر دفاعی غیرفعال';
    }
  }, 1000);
})();
