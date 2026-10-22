// EN/HE switcher on the landing and auth pages (_lang_switch.html). Saves
// the choice in the cookie the server reads to render every page in that
// language (app/i18n.py) - the dashboard reads the same cookie - then
// reloads so this page re-renders in it.
document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-set-lang]');
    if (!button || button.getAttribute('aria-pressed') === 'true') return;
    const secure = window.location.protocol === 'https:' ? '; secure' : '';
    document.cookie = `safebill_lang=${button.dataset.setLang}; path=/; max-age=31536000; samesite=lax${secure}`;
    window.location.reload();
});
