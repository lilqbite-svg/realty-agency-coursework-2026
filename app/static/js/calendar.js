/* Native calendar v1. No CDN, paid plugins or client-side schedule authority. */
(() => {
  'use strict';
  const configNode = document.getElementById('calendar-config');
  if (!configNode) return;
  const config = JSON.parse(configNode.textContent);
  const $ = id => document.getElementById(id);
  const content = $('calendar-content'), dialog = $('calendar-dialog'), form = $('calendar-form');
  const field = name => form.elements.namedItem(name);
  const dateFromKey = key => new Date(`${key}T12:00:00Z`);
  const keyFromDate = date => date.toISOString().slice(0, 10);
  const addDays = (key, n) => { const d = dateFromKey(key); d.setUTCDate(d.getUTCDate() + n); return keyFromDate(d); };
  const moscowToday = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  const labelDate = key => new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'}).format(dateFromKey(key));
  let anchor = moscowToday(), mode = 'month', events = [], loadSequence = 0, draggedId = null;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function feedback(message, failed = false) {
    $('calendar-feedback').textContent = message;
    $('calendar-feedback').className = `flash calendar-feedback ${failed ? 'danger' : 'success'}`;
  }
  function period() {
    if (mode === 'month') {
      const d = dateFromKey(anchor); d.setUTCDate(1);
      const first = keyFromDate(d), start = addDays(first, -((d.getUTCDay() + 6) % 7));
      return {start, end: addDays(start, 42)};
    }
    if (mode === 'week') {
      const start = addDays(anchor, -((dateFromKey(anchor).getUTCDay() + 6) % 7));
      return {start, end: addDays(start, 7)};
    }
    return {start: anchor, end: addDays(anchor, mode === 'day' ? 1 : 30)};
  }
  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || (response.status === 400 ? 'Форма устарела или содержит неверные данные. Обновите страницу.' : 'Не удалось выполнить действие. Обновите страницу и повторите.'));
    return data;
  }
  async function post(url, payload) {
    return fetchJson(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content}, body: JSON.stringify(payload)});
  }
  async function reload() {
    const sequence = ++loadSequence, range = period();
    $('calendar-date').value = anchor;
    const query = new URLSearchParams({start: `${range.start}T00:00:00+03:00`, end: `${range.end}T00:00:00+03:00`, status: $('calendar-status').value});
    if ($('calendar-agent')?.value) query.set('agent_id', $('calendar-agent').value);
    content.setAttribute('aria-busy', 'true'); content.classList.add('calendar-loading');
    try {
      const data = await fetchJson(`${config.eventsUrl}?${query}`);
      if (sequence !== loadSequence) return;
      events = data.events; render(range, data.unscheduled);
    } catch (error) {
      if (sequence === loadSequence) feedback(error.message, true);
    } finally {
      if (sequence === loadSequence) { content.setAttribute('aria-busy', 'false'); content.classList.remove('calendar-loading'); }
    }
  }
  function dayEvents(key) {
    return events.filter(event => event.local_start.slice(0, 10) === key);
  }
  function eventButton(event, detailed = false) {
    const button = node('button', undefined, `calendar-event calendar-status ${event.status}`);
    button.type = 'button'; button.dataset.eventId = event.id;
    const time = event.local_start ? event.local_start.slice(11, 16) : 'Время не назначено';
    button.append(node('strong', `${time} · ${event.status_label}`), node('span', event.title));
    if (detailed) button.append(node('span', `${event.client_name} · ${event.agent_name} · ${event.duration_minutes} мин`));
    button.setAttribute('aria-label', `${time}, ${event.status_label}, ${event.title}, ${event.client_name}. Открыть показ.`);
    button.addEventListener('click', () => editEvent(event.id));
    if (event.start && ['requested', 'confirmed'].includes(event.status)) {
      button.draggable = true;
      button.addEventListener('dragstart', e => { draggedId = event.id; e.dataTransfer.setData('text/plain', String(event.id)); e.dataTransfer.effectAllowed = 'move'; });
      button.addEventListener('dragend', () => { draggedId = null; document.querySelectorAll('.calendar-drop-target').forEach(el => el.classList.remove('calendar-drop-target')); });
    }
    return button;
  }
  function droppable(cell, key, hour = null) {
    cell.addEventListener('dragover', e => { if (draggedId !== null) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; cell.classList.add('calendar-drop-target'); } });
    cell.addEventListener('dragleave', e => { if (!cell.contains(e.relatedTarget)) cell.classList.remove('calendar-drop-target'); });
    cell.addEventListener('drop', async e => {
      e.preventDefault(); cell.classList.remove('calendar-drop-target');
      const event = events.find(item => item.id === draggedId); draggedId = null;
      if (!event) return;
      const time = hour === null ? event.local_start.slice(11, 16) : `${String(hour).padStart(2, '0')}:00`;
      try {
        await post(`${config.eventsUrl}/${event.id}/move`, {viewing_date: `${key}T${time}+03:00`, version: event.version});
        feedback('Показ перенесен. Изменение сохранено.');
      } catch (error) { feedback(error.message, true); }
      // The DOM stays at the old position until the server accepts the move.
      // Reload also restores the latest server state after any rejection.
      await reload();
    });
  }
  function newButton(key, hour = 10) {
    const button = node('button', '+', 'calendar-add'); button.type = 'button';
    button.setAttribute('aria-label', `Назначить показ на ${labelDate(key)}, ${hour}:00`);
    button.addEventListener('click', () => openForm(null, {viewing_date: `${key}T${String(hour).padStart(2, '0')}:00`}));
    return button;
  }
  function render(range, unscheduled) {
    content.replaceChildren();
    if (mode === 'month') $('calendar-period').textContent = new Intl.DateTimeFormat('ru-RU', {month: 'long', year: 'numeric', timeZone: 'UTC'}).format(dateFromKey(anchor));
    else $('calendar-period').textContent = mode === 'day' ? labelDate(anchor) : `${labelDate(range.start)} — ${labelDate(addDays(range.end, -1))}`;
    if (mode === 'month' || mode === 'week') {
      const grid = node('div', undefined, `calendar-grid calendar-${mode}`);
      ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].forEach(day => grid.append(node('div', day, 'calendar-weekday')));
      for (let key = range.start; key < range.end; key = addDays(key, 1)) {
        const cell = node('div', undefined, `calendar-cell${key === moscowToday() ? ' today' : ''}${mode === 'month' && key.slice(0, 7) !== anchor.slice(0, 7) ? ' outside' : ''}`);
        const header = node('div', undefined, 'calendar-day-head');
        header.append(node('span', `${Number(key.slice(8))}.${key.slice(5, 7)}`), newButton(key)); cell.append(header);
        dayEvents(key).forEach(event => cell.append(eventButton(event)));
        droppable(cell, key); grid.append(cell);
      }
      content.append(grid);
    } else if (mode === 'day') {
      const day = node('div', undefined, 'calendar-day');
      for (let hour = 0; hour < 24; hour++) {
        const row = node('div', undefined, 'calendar-hour'), slot = node('div', undefined, 'calendar-hour-slot');
        row.append(node('div', `${String(hour).padStart(2, '0')}:00`, 'calendar-hour-label'));
        slot.append(newButton(anchor, hour));
        dayEvents(anchor).filter(event => Number(event.local_start.slice(11, 13)) === hour).forEach(event => slot.append(eventButton(event, true)));
        droppable(slot, anchor, hour); row.append(slot); day.append(row);
      }
      content.append(day); day.scrollTop = 8 * 58;
    } else {
      const list = node('div', undefined, 'calendar-list');
      const upcoming = events.filter(event => anchor !== moscowToday() || new Date(event.end) >= new Date());
      let previous = '';
      upcoming.forEach(event => {
        const key = event.local_start.slice(0, 10);
        if (key !== previous) { list.append(node('h3', labelDate(key))); previous = key; }
        list.append(eventButton(event, true));
      });
      if (!upcoming.length) list.append(node('p', 'В выбранном периоде нет предстоящих показов.', 'muted'));
      content.append(list);
    }
    const waiting = $('calendar-unscheduled'); waiting.replaceChildren();
    unscheduled.forEach(event => waiting.append(eventButton(event, true)));
    if (!unscheduled.length) waiting.append(node('p', 'Нет запросов без назначенного времени.', 'muted'));
  }
  function dateRequired() { field('viewing_date').required = ['confirmed', 'completed'].includes(field('status').value); }
  function openForm(event = null, defaults = {}) {
    form.reset(); $('calendar-form-error').textContent = '';
    const values = event ? {...event, event_id: event.id, viewing_date: event.local_start.slice(0, 16)} : {event_id: '', version: '', status: 'confirmed', duration_minutes: 60, ...defaults};
    Object.entries(values).forEach(([name, value]) => { const input = field(name); if (input) input.value = value ?? ''; });
    $('calendar-dialog-title').textContent = event ? `Показ №${event.id}` : 'Назначить показ';
    $('calendar-record-link').hidden = !event;
    if (event) $('calendar-record-link').href = event.url;
    dateRequired(); dialog.showModal();
  }
  async function editEvent(id) {
    try { openForm(await fetchJson(`${config.eventsUrl}/${id}`)); }
    catch (error) { feedback(error.message, true); }
  }
  form.addEventListener('submit', async e => {
    e.preventDefault(); if (!form.reportValidity()) return;
    const payload = Object.fromEntries(new FormData(form));
    const id = payload.event_id; delete payload.event_id;
    if (payload.viewing_date) payload.viewing_date += '+03:00';
    $('calendar-save').disabled = true; $('calendar-form-error').textContent = '';
    try {
      await post(`${config.eventsUrl}${id ? `/${id}` : ''}`, payload);
      dialog.close(); feedback('Показ сохранен.'); await reload();
    } catch (error) { $('calendar-form-error').textContent = error.message; }
    finally { $('calendar-save').disabled = false; }
  });
  field('status').addEventListener('change', dateRequired);
  field('request_id').addEventListener('change', () => { const option = field('request_id').selectedOptions[0]; if (option.dataset.client) field('client_id').value = option.dataset.client; });
  field('property_id').addEventListener('change', () => { if (config.admin) { const agent = field('property_id').selectedOptions[0].dataset.agent; if (agent) field('agent_id').value = agent; } });
  $('calendar-close').addEventListener('click', () => dialog.close()); $('calendar-cancel').addEventListener('click', () => dialog.close());
  $('calendar-new').addEventListener('click', () => openForm());
  $('calendar-today').addEventListener('click', () => { anchor = moscowToday(); reload(); });
  $('calendar-date').addEventListener('change', e => { if (e.target.value) { anchor = e.target.value; reload(); } });
  ['calendar-status', 'calendar-agent'].forEach(id => $(id)?.addEventListener('change', reload));
  function navigate(step) {
    if (mode === 'month') { const d = dateFromKey(anchor); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + step); anchor = keyFromDate(d); }
    else anchor = addDays(anchor, step * (mode === 'week' ? 7 : mode === 'list' ? 30 : 1));
    reload();
  }
  $('calendar-prev').addEventListener('click', () => navigate(-1)); $('calendar-next').addEventListener('click', () => navigate(1));
  document.querySelectorAll('[data-calendar-view]').forEach(button => button.addEventListener('click', () => {
    mode = button.dataset.calendarView;
    document.querySelectorAll('[data-calendar-view]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    reload();
  }));
  reload();
  if (Object.keys(config.prefill).length) openForm(null, config.prefill);
})();
