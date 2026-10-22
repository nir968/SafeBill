// Shared by login.html and signup.html - both are deliberately Alpine-free
// (see either file's own comment on why: one form each, not enough
// interactivity to justify loading a whole reactivity framework), so this
// is plain global functions loaded via a <script> tag, not an Alpine
// store/component the way dashboard.js's shared state is.

// Extracts a displayable message from a FastAPI error response - `detail`
// is a plain string for every HTTPException this app raises itself (see
// app/routers/auth.py), but FastAPI/Pydantic's own 422 responses use a
// *list* of validation-error objects instead
// ({"detail": [{"msg": "...", ...}, ...]}). Rendering that list directly
// as text would show "[object Object]" instead of anything readable, so
// both shapes are handled here rather than assuming detail is always a
// string.
function extractErrorMessage(data, fallback) {
    if (!data || !data.detail) return fallback;
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail) && data.detail.length > 0) {
        const first = data.detail[0];
        return (first && (first.msg || first.message)) || fallback;
    }
    return fallback;
}

// This page's strings in the visitor's language, rendered by the server
// into window.SAFEBILL_I18N (see app/i18n.py) before this file loads.
const T = window.SAFEBILL_I18N || {};

// Hebrew pages show the API's own (Hebrew) error wording, exactly as
// before. English pages can't, so they use an English message for known
// status codes and the page's English fallback for any other Hebrew text.
function localizedError(response, data, fallback, statusMessages = {}) {
    const message = extractErrorMessage(data, fallback);
    if (document.documentElement.lang !== 'en') return message;
    if (statusMessages[response.status]) return statusMessages[response.status];
    return /[\u0590-\u05FF]/.test(message) ? fallback : message;
}

const _EYE_ICON =
    '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z" /><circle cx="12" cy="12" r="3" /></svg>';

const _EYE_OFF_ICON =
    '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-11-7-11-7a18.9 18.9 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 5c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24" /><path d="M1 1l22 22" /></svg>';

// Wires a show/hide toggle button to a password input - reused for every
// password field on both pages (login's single field, signup's password
// + password-confirm) instead of duplicating this per input. Silently
// does nothing if either id is missing rather than throwing, so a page
// that only has one of the two elements (there isn't one today, but
// nothing forces that to stay true) doesn't break on load.
function setupPasswordToggle(inputId, buttonId) {
    const input = document.getElementById(inputId);
    const button = document.getElementById(buttonId);
    if (!input || !button) return;

    button.innerHTML = _EYE_ICON;
    button.addEventListener('click', () => {
        const isHidden = input.type === 'password';
        input.type = isHidden ? 'text' : 'password';
        button.innerHTML = isHidden ? _EYE_OFF_ICON : _EYE_ICON;
        button.setAttribute('aria-label', isHidden ? T.hide_password : T.show_password);
    });
}

// Shows the Telegram 2FA code step (the #twofa-form block on login.html /
// reset_password.html), sends a code right away, and calls onVerified()
// once POST /auth/2fa/verify accepts it. See app/two_factor.py.
function startTwoFactorStep({ purpose, resetToken = null, onVerified }) {
    const form = document.getElementById('twofa-form');
    const input = document.getElementById('twofa-code');
    const error = document.getElementById('twofa-error');
    const notice = document.getElementById('twofa-notice');
    const submit = document.getElementById('twofa-submit');
    const resend = document.getElementById('twofa-resend');

    const show = (el, text) => { el.textContent = text; el.classList.remove('hidden'); };
    const hide = (el) => el.classList.add('hidden');
    const post = (url, extra) => fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ purpose, reset_token: resetToken, ...extra }),
    });

    async function sendCode() {
        hide(error);
        hide(notice);
        try {
            const response = await post('/auth/2fa/generate', {});
            if (response.ok) {
                show(notice, T.twofa_sent);
                return;
            }
            const data = await response.json().catch(() => null);
            show(error, localizedError(response, data, T.twofa_send_failed, {
                401: T.err_session_expired, 429: T.err_code_cooldown, 502: T.err_code_send,
            }));
        } catch (networkError) {
            show(error, T.network_error);
        }
    }

    form.classList.remove('hidden');
    input.focus();
    resend.addEventListener('click', sendCode);
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        hide(error);
        submit.disabled = true;
        try {
            const response = await post('/auth/2fa/verify', { code: input.value.trim() });
            if (response.ok) {
                // Button stays disabled - onVerified navigates/resubmits.
                await onVerified();
                return;
            }
            const data = await response.json().catch(() => null);
            show(error, localizedError(response, data, T.twofa_verify_failed, {
                400: T.err_code_wrong, 401: T.err_session_expired, 429: T.err_code_too_many,
            }));
        } catch (networkError) {
            show(error, T.network_error);
        }
        submit.disabled = false;
    });

    sendCode();
}
