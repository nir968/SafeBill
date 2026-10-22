"""
Language for the server-rendered entry pages (landing + auth pages).

The visitor's choice lives in the `safebill_lang` cookie, set client-side
by the EN/HE switcher (static/js/lang.js) and by the dashboard's own
toggle (dashboard.js). The server reads it so every page renders directly
in the chosen language and direction - no flash of Hebrew before a script
swaps the text. The dashboard keeps its own translations in dashboard.js;
this table only covers the pages that don't load it.

The Hebrew strings are the exact wording these pages used before they
were bilingual. Server-side error messages from the API stay Hebrew; the
auth pages substitute an English equivalent for the common ones (see
localizedError in static/js/auth.js).
"""

from fastapi import Request

LANG_COOKIE = "safebill_lang"
SUPPORTED_LANGS = ("he", "en")
DEFAULT_LANG = "he"

TEXT = {
    "he": {
        "language_switch": "שפה",
        "home_link": "SafeBill - דף הבית",
        # Landing
        "landing_page_title": "SafeBill - ניהול חשבונות חכם",
        "landing_headline": "ניהול חשבונות חכם, במקום אחד",
        "landing_subtitle": "העלו חשבון ותנו לבינה המלאכותית לעשות את השאר",
        "landing_login": "התחברות",
        "landing_signup": "יצירת חשבון",
        # Shared form text
        "email_label": "אימייל",
        "password_label": "סיסמה",
        "new_password_label": "סיסמה חדשה",
        "confirm_password_label": "אימות סיסמה",
        "show_password": "הצג סיסמה",
        "hide_password": "הסתר סיסמה",
        "network_error": "לא הצלחתי להתחבר לשרת.",
        "back_to_login": "חזרה להתחברות",
        "req_length": "לפחות 8 תווים",
        "req_uppercase": "אות גדולה (A-Z)",
        "req_lowercase": "אות קטנה (a-z)",
        "req_number": "ספרה (0-9)",
        "req_special": "תו מיוחד (!@#$% וכו')",
        "password_requirements_unmet": "הסיסמה אינה עומדת בדרישות המפורטות למעלה.",
        "passwords_mismatch": "הסיסמאות אינן תואמות.",
        # Login
        "login_page_title": "התחברות",
        "login_heading": "ברוכים השבים",
        "login_subtitle": "התחברו כדי להמשיך מאיפה שעצרתם",
        "login_submit": "התחברות",
        "login_submitting": "מתחבר...",
        "login_failed": "ההתחברות נכשלה.",
        "forgot_link": "שכחתם סיסמה?",
        "new_here": "חדשים כאן?",
        "create_account_link": "יצירת חשבון",
        # Signup
        "signup_page_title": "הרשמה",
        "signup_heading": "יצירת חשבון",
        "signup_subtitle": "כל החשבונות שלכם במקום אחד, בניהול חכם",
        "signup_submit": "הרשמה",
        "signup_submitting": "נרשם...",
        "signup_failed": "ההרשמה נכשלה.",
        "signup_success": "נרשמת בהצלחה! מתחבר למערכת...",
        "have_account": "כבר יש לכם חשבון?",
        "login_link": "התחברות",
        "benefit_upload": "העלאת חשבון בתמונה או PDF - בלי הקלדה ידנית",
        "benefit_ai": "בינה מלאכותית מזהה את הפרטים אוטומטית",
        "benefit_all_in_one": "כל החשבונות שלכם במקום אחד, בלי לפספס תשלום",
        # Forgot password
        "forgot_page_title": "שחזור סיסמה",
        "forgot_heading": "שכחתם את הסיסמה?",
        "forgot_subtitle": "הזינו את האימייל שלכם ונשלח אליכם קישור לאיפוס",
        "forgot_submit": "שליחת קישור",
        "forgot_submitting": "שולח...",
        "forgot_sent": "הקישור נשלח",
        "forgot_failed": "משהו השתבש, נסו שוב.",
        "forgot_success": "אם קיים חשבון עם כתובת האימייל הזו, נשלח אליו קישור לאיפוס הסיסמה.",
        "remembered": "נזכרתם?",
        # Reset password
        "reset_page_title": "איפוס סיסמה",
        "reset_heading": "בחירת סיסמה חדשה",
        "reset_subtitle": "בחרו סיסמה חזקה לחשבון שלכם",
        "reset_submit": "איפוס סיסמה",
        "reset_submitting": "מאפס...",
        "reset_failed": "איפוס הסיסמה נכשל.",
        "reset_success": "הסיסמה אופסה בהצלחה! מעביר אותך ללוח הבקרה...",
        "reset_invalid_link_before": "קישור לא תקין. בקשו קישור חדש לאיפוס סיסמה מ",
        "reset_invalid_link_cta": "כאן",
        "reset_invalid_link_after": ".",
        # Two-factor step
        "twofa_title": "אימות דו-שלבי",
        "twofa_login_desc": "הזינו את הקוד שנשלח לטלגרם שלכם כדי להשלים את ההתחברות",
        "twofa_reset_desc": "לאבטחת החשבון, הזינו את הקוד שנשלח לטלגרם שלכם",
        "twofa_code_label": "קוד אימות",
        "twofa_verify": "אימות",
        "twofa_resend": "שליחת קוד חדש",
        "twofa_sent": "שלחנו קוד בן 6 ספרות לטלגרם שלך.",
        "twofa_send_failed": "לא הצלחנו לשלוח קוד, נסו שוב.",
        "twofa_verify_failed": "האימות נכשל.",
        # English stand-ins for Hebrew API errors (unused on Hebrew pages,
        # which show the API's own wording; kept so both tables match).
        "err_invalid_credentials": "אימייל או סיסמה שגויים",
        "err_email_taken": "כבר קיים חשבון עם כתובת האימייל הזו",
        "err_reset_link_expired": "קישור איפוס הסיסמה פג תוקף או שאינו תקין - בקשו קישור חדש",
        "err_code_wrong": "קוד שגוי או שפג תוקפו",
        "err_code_too_many": "יותר מדי ניסיונות - בקשו קוד חדש",
        "err_code_cooldown": "כבר נשלח קוד - נסו שוב בעוד רגע",
        "err_code_send": "לא הצלחנו לשלוח את הקוד לטלגרם - נסו שוב בעוד רגע",
        "err_session_expired": "תוקף ההתחברות פג - התחברו מחדש",
    },
    "en": {
        "language_switch": "Language",
        "home_link": "SafeBill - home",
        "landing_page_title": "SafeBill - Smart bill management",
        "landing_headline": "Smart bill management, all in one place",
        "landing_subtitle": "Upload a bill and let AI take care of the rest",
        "landing_login": "Sign in",
        "landing_signup": "Create account",
        "email_label": "Email",
        "password_label": "Password",
        "new_password_label": "New password",
        "confirm_password_label": "Confirm password",
        "show_password": "Show password",
        "hide_password": "Hide password",
        "network_error": "Couldn't connect to the server.",
        "back_to_login": "Back to sign in",
        # Kept short: these share a two-column grid with the Hebrew layout.
        "req_length": "At least 8 characters",
        "req_uppercase": "Uppercase (A-Z)",
        "req_lowercase": "Lowercase (a-z)",
        "req_number": "Number (0-9)",
        "req_special": "Symbol (!@#$%)",
        "password_requirements_unmet": "The password doesn't meet the requirements above.",
        "passwords_mismatch": "The passwords don't match.",
        "login_page_title": "Sign in",
        "login_heading": "Welcome back",
        "login_subtitle": "Sign in to pick up where you left off",
        "login_submit": "Sign in",
        "login_submitting": "Signing in...",
        "login_failed": "Sign-in failed.",
        "forgot_link": "Forgot password?",
        "new_here": "New here?",
        "create_account_link": "Create an account",
        "signup_page_title": "Sign up",
        "signup_heading": "Create your account",
        "signup_subtitle": "All your bills in one place, managed smartly",
        "signup_submit": "Create account",
        "signup_submitting": "Creating account...",
        "signup_failed": "Sign-up failed.",
        "signup_success": "You're all set! Signing you in...",
        "have_account": "Already have an account?",
        "login_link": "Sign in",
        "benefit_upload": "Upload a bill as a photo or PDF - no manual typing",
        "benefit_ai": "AI picks out the details automatically",
        "benefit_all_in_one": "All your bills in one place, never miss a payment",
        "forgot_page_title": "Forgot password",
        "forgot_heading": "Forgot your password?",
        "forgot_subtitle": "Enter your email and we'll send you a reset link",
        "forgot_submit": "Send reset link",
        "forgot_submitting": "Sending...",
        "forgot_sent": "Link sent",
        "forgot_failed": "Something went wrong, please try again.",
        "forgot_success": "If an account exists for this email, we've sent it a password reset link.",
        "remembered": "Remembered it?",
        "reset_page_title": "Reset password",
        "reset_heading": "Choose a new password",
        "reset_subtitle": "Pick a strong password for your account",
        "reset_submit": "Reset password",
        "reset_submitting": "Resetting...",
        "reset_failed": "Password reset failed.",
        "reset_success": "Password reset! Taking you to your dashboard...",
        "reset_invalid_link_before": "This link is invalid. Request a new reset link ",
        "reset_invalid_link_cta": "here",
        "reset_invalid_link_after": ".",
        "twofa_title": "Two-step verification",
        "twofa_login_desc": "Enter the code we sent to your Telegram to finish signing in",
        "twofa_reset_desc": "To keep your account secure, enter the code we sent to your Telegram",
        "twofa_code_label": "Verification code",
        "twofa_verify": "Verify",
        "twofa_resend": "Send a new code",
        "twofa_sent": "We sent a 6-digit code to your Telegram.",
        "twofa_send_failed": "Couldn't send a code, please try again.",
        "twofa_verify_failed": "Verification failed.",
        "err_invalid_credentials": "Incorrect email or password.",
        "err_email_taken": "An account with this email already exists.",
        "err_reset_link_expired": "This reset link has expired or is invalid - request a new one.",
        "err_code_wrong": "That code is wrong or has expired.",
        "err_code_too_many": "Too many attempts - request a new code.",
        "err_code_cooldown": "A code was just sent - please wait a moment before requesting another.",
        "err_code_send": "We couldn't send the code to Telegram - try again shortly.",
        "err_session_expired": "Your sign-in expired - please sign in again.",
    },
}


def get_lang(request: Request) -> str:
    lang = request.cookies.get(LANG_COOKIE)
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def page_context(request: Request) -> dict:
    """Template context for the landing and auth pages. `t` raises
    KeyError on an unknown key, so a typo fails loudly at render time."""
    lang = get_lang(request)
    text = TEXT[lang]
    return {"lang": lang, "dir": "rtl" if lang == "he" else "ltr", "text": text, "t": text.__getitem__}
