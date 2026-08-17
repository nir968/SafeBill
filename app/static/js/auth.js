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
        button.setAttribute('aria-label', isHidden ? 'הסתר סיסמה' : 'הצג סיסמה');
    });
}
