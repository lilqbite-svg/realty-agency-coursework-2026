document.addEventListener('DOMContentLoaded', () => {
  const menu = document.querySelector('.menu-btn');
  const nav = document.querySelector('#mainNav');
  menu?.addEventListener('click', () => {
    const open = nav.classList.toggle('mobile-open');
    menu.setAttribute('aria-expanded', String(open));
  });
  document.querySelectorAll('[data-submit]').forEach(select => {
    select.addEventListener('change', () => document.getElementById(select.dataset.submit).requestSubmit());
  });
  document.querySelectorAll('[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => { if (!confirm(form.dataset.confirm)) event.preventDefault(); });
  });
  document.querySelectorAll('[data-favorite]').forEach(button => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(button.dataset.favorite, {
          method: 'POST', credentials: 'same-origin', headers: {
            'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
            'Accept': 'application/json'
          }
        });
        if (response.redirected) { location.assign(response.url); return; }
        const result = await response.json();
        if (response.status === 401 && result.login_url) { location.assign(result.login_url); return; }
        if (!response.ok) throw new Error(result.error || 'Не удалось изменить избранное.');
        document.querySelectorAll('[data-favorite]').forEach(other => {
          if (other.dataset.favorite !== button.dataset.favorite) return;
          other.classList.toggle('is-active', result.favorite);
          other.setAttribute('aria-pressed', String(result.favorite));
          other.textContent = other.classList.contains('favorite-text')
            ? (result.favorite ? '♥ В избранном' : '♡ Добавить в избранное')
            : (result.favorite ? '♥' : '♡');
        });
      } catch (error) { alert(error.message); }
      finally { button.disabled = false; button.removeAttribute('aria-busy'); }
    });
  });
  document.querySelectorAll('[data-gallery]').forEach(button => {
    button.addEventListener('click', () => document.querySelector('#mainPhoto').src = button.dataset.gallery);
  });
  document.querySelector('[data-preview]')?.addEventListener('change', event => {
    const area = document.querySelector('#photoPreview');
    area.querySelectorAll('img').forEach(img => URL.revokeObjectURL(img.src));
    area.replaceChildren();
    [...event.target.files].forEach(file => {
      const img = new Image(); img.src = URL.createObjectURL(file); img.alt = 'Предпросмотр фотографии'; area.append(img);
    });
  });
});
