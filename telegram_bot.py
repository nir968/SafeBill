import io
import os
import sys

import requests
import telebot
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:8000')
# Generous on purpose: PDF uploads go through a rasterization step before
# the vision model even sees them, so this needs to comfortably outlast
# the backend's own VLM_TIMEOUT_SECONDS rather than time out first and
# hide the backend's real (and more informative) error.
BACKEND_TIMEOUT_SECONDS = 200
# Plain DB reads/deletes never touch the vision pipeline, so they don't
# need BACKEND_TIMEOUT_SECONDS' generous headroom - a short, fail-fast
# timeout is better UX for these.
DB_QUERY_TIMEOUT_SECONDS = 15

# Same value as Settings.internal_api_key on the backend (see
# app/config.py) - proves to app/auth.py::get_authorized_user_id that this
# process is the trusted bot, entitled to act on behalf of whichever
# user_id it supplies (already Telegram-verified via message.from_user.id
# below, not user input this script itself trusts blindly). Every backend
# call in this file goes through the `backend` session below instead of
# the bare `requests` module specifically so this header is never
# forgotten on a future new call site - see get_authorized_user_id's own
# docstring for what happens on the backend if it's missing or wrong
# (401, same as no credential at all).
INTERNAL_API_KEY = os.getenv('INTERNAL_API_KEY')
if not INTERNAL_API_KEY:
    sys.exit(
        "INTERNAL_API_KEY is not set - every backend call in this bot would get a 401. "
        "Set it in .env to the same value as the backend's INTERNAL_API_KEY."
    )

backend = requests.Session()
backend.headers.update({'X-Internal-Api-Key': INTERNAL_API_KEY})

bot = telebot.TeleBot(TOKEN)

# Same table, duplicated (not imported) in app/telegram_client.py - this
# script is a deliberately separate, independently-deployable process
# (own requirements-bot.txt, no dependency on the `app` package) that
# only talks to the backend over HTTP, so it can't share this by import.
# Keep the two in sync if a currency is added.
_CURRENCY_SYMBOLS = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}


def _format_amount(amount, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}"


def _format_amount_suffix(amount, currency: str) -> str:
    """Amount-then-symbol ordering ("500₪"), for inline keyboard button
    labels specifically. Deliberately not the same ordering as
    _format_amount ("₪500", used everywhere else in this bot) - not
    consolidating the two, since changing the established ordering
    elsewhere wasn't asked for."""
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{amount}{symbol}" if symbol else f"{amount} {currency}"

# הגדרת רשימת הפקודות שיופיעו בתפריט של טלגרם
#
# BUG FIX: Telegram's setMyCommands requires bare command names - "lowercase
# English letters, digits and underscores only" per the Bot API docs. A
# leading "/" is none of those, so every BotCommand below used to be
# invalid, and Telegram rejects the *entire* batch if any one entry is
# invalid (there's no partial success) - that's why nothing ever showed up
# in the menu, even though the commands themselves worked fine (the
# @bot.message_handler(commands=[...]) decorators below already correctly
# used bare names, which is what actually dispatches messages - the menu
# is a separate, purely cosmetic registration that was silently failing).
commands = [
    telebot.types.BotCommand("last", "הצגת החשבוניות האחרונות"),
    telebot.types.BotCommand("stats", "סיכום הוצאות"),
    telebot.types.BotCommand("search", "חיפוש חשבוניות לפי מילה"),
    telebot.types.BotCommand("pending", "חשבוניות שטרם שולמו"),
    telebot.types.BotCommand("pay", "סימון חשבונית כשולמה"),
    telebot.types.BotCommand("edit", "עדכון סכום חשבונית"),
    telebot.types.BotCommand("export", "ייצוא כל החשבוניות לקובץ CSV"),
    telebot.types.BotCommand("delete_last", "מחיקת החשבונית האחרונה"),
    telebot.types.BotCommand("help", "עזרה והצגת מידע"),
]


def _register_bot_commands() -> None:
    """Called right before polling starts (not at import time) so a
    failure here is loud and attributable, instead of an exception during
    module import that could get lost above the rest of the startup
    output, or - if telebot's version ever stops raising on a rejected
    batch - silently doing nothing with no indication anything went
    wrong."""
    try:
        ok = bot.set_my_commands(commands)
        if ok:
            print(f"✅ נרשמו {len(commands)} פקודות בתפריט טלגרם")
        else:
            print("⚠️ טלגרם דחה את רשימת הפקודות (set_my_commands החזיר False)")
    except Exception as e:
        print(f"❌ נכשל רישום פקודות התפריט: {e}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    # `/start <token>` - the payload from the dashboard's "Connect
    # Telegram" deep link (see POST /auth/telegram/link-token in
    # app/routers/auth.py). Telegram appends it exactly like any other
    # command argument, so the same message.text.split(maxsplit=1)
    # pattern used by /search and /edit below applies here too. A bare
    # /start (no payload - the overwhelmingly common case) falls through
    # to the normal welcome text unchanged.
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        _handle_link_token(message, args[1].strip())
        return

    welcome_text = (
        "היי! איזה כיף לראות אותך כאן 👋\n\n"
        "אני SafeBill, העוזר האישי שלך למעקב אחרי חשבוניות.\n"
        "פשוט תצלם חשבונית, תשלח לי אותה כקובץ PDF, או תעביר אליי קובץ מהמייל, "
        "ואני כבר אעשה את כל העבודה השחורה! 📸📄✨"
    )
    bot.reply_to(message, welcome_text)


def _handle_link_token(message, token: str) -> None:
    """Redeems a /start deep-link token against POST /auth/telegram/link,
    connecting this Telegram account to whichever website account
    generated it. message.from_user.id is Telegram's own signed value
    for who actually tapped Start - not something this bot invents or
    trusts blindly from message text - which is exactly why the backend
    accepts it via the internal-API-key trusted path (see
    require_internal_api_key in app/auth.py) rather than needing the
    website session to somehow also be present here."""
    try:
        response = backend.post(
            f"{BACKEND_URL}/auth/telegram/link",
            json={"token": token, "telegram_id": message.from_user.id},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if not response.ok:
        bot.reply_to(message, f"⚠️ {_extract_error_detail(response)}")
        return

    email = response.json().get('email')
    text = "✅ החשבון שלך" + (f" ({email})" if email else "") + " קושר בהצלחה לטלגרם!\n"
    text += "מעכשיו תקבל התראות ותוכל לנהל את החשבונות שלך גם כאן."
    bot.reply_to(message, text)

@bot.message_handler(commands=['help'])
def send_help(message):
    text = (
        "💡 <b>מה תרצה לעשות?</b>\n\n"
        "📸 <b>להוסיף הוצאה:</b>\n"
        "פשוט צלם או שלח אלי קובץ PDF של קבלה, ואני אחלץ את הנתונים לבד.\n\n"
        "💸 <b>ניהול ותשלומים:</b>\n"
        "• מה נשאר לי לשלם?\n"
        "/pending\n\n"
        "• שילמת? בחר חשבונית מהרשימה ואשר תשלום:\n"
        "‎/pay\n\n"
        "• תיקון מהיר אם זיהיתי סכום לא מדויק:\n"
        "/edit ID Amount\n\n"   
        "📊 <b>סיכומים וחיפושים:</b>\n"
        "• כמה הוצאתי? סיכום נקי מופרד לפי סוגי מטבעות:\n"
        "/stats\n\n"
        "• שליפת החשבוניות האחרונות שנסרקו:\n"
        "/last\n\n"
        "• חיפוש הוצאה ספציפית:\n"
        "/search Text\n\n"
        "🛠️ <b>כלים נוספים:</b>\n"
        "• הורדת כל הנתונים השמורים לקובץ אקסל:\n"
        "/export\n\n"
        "• מחיקת החשבונית האחרונה שהועלתה:\n"
        "/delete_last"
    )
    bot.reply_to(message, text, parse_mode='HTML')

@bot.message_handler(commands=['last'])
def handle_last_bills(message):
    bot.reply_to(message, "שולף נתונים... ⏳")
    try:
        url = f"{BACKEND_URL}/bills/recent?user_id={message.from_user.id}"
        response = backend.get(url, timeout=BACKEND_TIMEOUT_SECONDS)

        if response.ok:
            bills = response.json()
            if not bills:
                bot.reply_to(message, "לא מצאתי חשבוניות קודמות.")
                return

            text = "🧾 **החשבוניות האחרונות שלך:**\n\n"
            for bill in bills:
                amount_str = _format_amount(bill['amount'], bill['currency'])
                text += f"▪️ {bill['institution']}: {amount_str}\n"
            bot.reply_to(message, text, parse_mode='Markdown')
        else:
            bot.reply_to(message, "הייתה בעיה בשליפת הנתונים מהשרת.")

    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"שגיאה בתקשורת: {str(e)}")


@bot.message_handler(commands=['stats'])
def handle_stats(message):
    bot.reply_to(message, "מחשב סיכום חודשי... 🧮")
    try:
        url = f"{BACKEND_URL}/bills/stats?user_id={message.from_user.id}"
        response = backend.get(url, timeout=BACKEND_TIMEOUT_SECONDS)

        if response.ok:
            data = response.json()
            totals = data.get('totals', {})
            if not totals:
                bot.reply_to(message, "עדיין אין מספיק נתונים.")
                return

            # One line per currency actually present - a single blended
            # sum across currencies would be meaningless (e.g. summing a
            # $ bill and a ₪ bill into one number).
            lines = [f"סך הכל ({currency}): {_format_amount(total, currency)}" for currency, total in totals.items()]
            text = "📊 **סיכום הוצאות:**\n" + "\n".join(lines)
            bot.reply_to(message, text, parse_mode='Markdown')
        else:
            bot.reply_to(message, "עדיין אין מספיק נתונים.")

    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"שגיאה בתקשורת: {str(e)}")


def _extract_error_detail(response) -> str:
    """FastAPI's HTTPException(...) responses carry the real reason in a
    `detail` field - fall back to raw text if the body isn't JSON."""
    try:
        return response.json().get('detail', response.text)
    except ValueError:
        return response.text


def _process_bill_upload(message, file_id: str) -> None:
    """Downloads a photo or PDF document from Telegram and forwards its
    bytes straight to the Sanitizer's /bills/upload endpoint. Shared by
    the photo and document handlers so there is exactly one upload path
    to get right, and the bytes only ever exist in memory here - never
    written to local disk, matching the backend's own memory-only design.
    """
    try:
        file_info = bot.get_file(file_id)
        file_bytes = bot.download_file(file_info.file_path)
    except Exception as e:
        bot.reply_to(message, f"לא הצלחתי להוריד את הקובץ מטלגרם: {e}")
        return

    url = f"{BACKEND_URL}/bills/upload?user_id={message.from_user.id}"
    try:
        response = backend.post(
            url,
            headers={'Content-Type': 'application/octet-stream'},
            data=file_bytes,
            timeout=BACKEND_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"לא הצלחתי להתחבר לשרת: {e}")
        return

    if response.status_code == 202:
        data = response.json()
        bill_id = data.get('bill_id')
        category = data.get('category')
        text = f"החשבונית נשמרה בהצלחה במסד הנתונים! ✅\nמזהה חשבונית: {bill_id}"
        if category:
            text += f"\n🏷️ קטגוריה: {category}"
        bot.reply_to(message, text)
    elif response.status_code == 409:
        bot.reply_to(message, "החשבונית כבר קיימת במערכת 🔁")
    elif response.status_code == 422:
        bot.reply_to(
            message,
            f"לא הצלחתי לזהות את זה כחשבונית תקינה 🤔\n{_extract_error_detail(response)}",
        )
    elif response.status_code == 413:
        bot.reply_to(message, "הקובץ גדול מדי. נסה קובץ או תמונה קטנים יותר.")
    elif response.status_code == 429:
        bot.reply_to(message, "שלחת יותר מדי חשבוניות בדקה האחרונה ⏳ נסה שוב בעוד דקה.")
    elif response.status_code == 504:
        bot.reply_to(message, "פענוח החשבונית לקח יותר מדי זמן. נסה שוב בעוד רגע.")
    elif response.status_code == 502:
        bot.reply_to(message, "יש בעיה זמנית בשירות פענוח החשבוניות. נסה שוב בעוד כמה דקות.")
    else:
        bot.reply_to(message, f"הייתה בעיה בשמירת החשבונית: {_extract_error_detail(response)}")


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    bot.reply_to(message, "קיבלתי! מתחיל לפענח את החשבונית... ⏳")
    _process_bill_upload(message, message.photo[-1].file_id)


@bot.message_handler(content_types=['document'])
def handle_document(message):
    mime_type = message.document.mime_type
    if mime_type != 'application/pdf':
        bot.reply_to(
            message,
            "כרגע אני יודע לעבד רק תמונות או קבצי PDF של חשבוניות 📄\n"
            f"קיבלתי קובץ מסוג {mime_type or 'לא ידוע'} ולא יכול לעבד אותו.",
        )
        return

    bot.reply_to(message, "קיבלתי PDF! מתחיל לפענח את החשבונית... ⏳")
    _process_bill_upload(message, message.document.file_id)

@bot.message_handler(commands=['search'])
def handle_search(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "⚠️ נא לציין מילות חיפוש. דוגמה: /search חשמל")
        return
    query = args[1]
    user_id = message.from_user.id

    try:
        response = backend.get(
            f"{BACKEND_URL}/bills/search",
            params={"user_id": user_id, "query": query},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if not response.ok:
        bot.reply_to(message, f"❌ הייתה בעיה בחיפוש: {_extract_error_detail(response)}")
        return

    bills = response.json()
    if not bills:
        bot.reply_to(message, f"🔍 לא נמצאו חשבוניות עבור '{query}'.")
        return

    text = f"🔍 תוצאות חיפוש עבור '{query}':\n"
    for b in bills:
        due_date = b['due_date'] or 'לא ידוע'
        amount_str = _format_amount(b['amount'], b['currency'])
        text += f"• {b['institution']}: {amount_str} (לפירעון: {due_date})\n"
    bot.reply_to(message, text)


@bot.message_handler(commands=['pending'])
def handle_pending(message):
    user_id = message.from_user.id

    try:
        response = backend.get(
            f"{BACKEND_URL}/bills/pending",
            params={"user_id": user_id},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if not response.ok:
        bot.reply_to(
            message, f"❌ הייתה בעיה בשליפת החשבוניות הממתינות: {_extract_error_detail(response)}"
        )
        return

    bills = response.json()
    if not bills:
        bot.reply_to(message, "✅ אין חשבוניות ממתינות לתשלום!")
        return

    text = "⏳ חשבוניות שטרם שולמו:\n"
    for b in bills:
        due_date = b['due_date'] or 'לא ידוע'
        amount_str = _format_amount(b['amount'], b['currency'])
        text += f"• {b['institution']}: {amount_str} (תאריך יעד: {due_date})\n"
    bot.reply_to(message, text)


@bot.message_handler(commands=['delete_last'])
def handle_delete_last(message):
    user_id = message.from_user.id

    try:
        response = backend.delete(
            f"{BACKEND_URL}/bills/last",
            params={"user_id": user_id},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if not response.ok:
        bot.reply_to(message, f"❌ הייתה בעיה במחיקת החשבונית: {_extract_error_detail(response)}")
        return

    data = response.json()
    if "error" in data:
        bot.reply_to(message, "⚠️ אין חשבוניות למחיקה.")
        return

    bot.reply_to(
        message, f"🗑️ החשבונית האחרונה נמחקה בהצלחה:\nמוסד: {data['institution']} (מזהה: {data['id']})"
    )


@bot.message_handler(commands=['pay'])
def handle_pay(message):
    """
    Interactive flow, not `/pay <bill_id>`: fetch pending bills, show one
    inline button per bill, and let the tap supply the id instead of
    asking the user to go find and type it. The rest of the flow is
    driven by handle_pay_callback (button tap) and process_asmachta_step
    (the follow-up text message), chained via
    bot.register_next_step_handler.
    """
    user_id = message.from_user.id

    try:
        response = backend.get(
            f"{BACKEND_URL}/bills/pending",
            params={"user_id": user_id},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if not response.ok:
        bot.reply_to(
            message, f"❌ הייתה בעיה בשליפת החשבוניות הממתינות: {_extract_error_detail(response)}"
        )
        return

    bills = response.json()
    if not bills:
        bot.reply_to(message, "✅ הכל שולם! אין לך חשבוניות ממתינות כרגע.")
        return

    keyboard = telebot.types.InlineKeyboardMarkup()
    for bill in bills:
        amount_str = _format_amount_suffix(bill['amount'], bill['currency'])
        button_text = f"{bill['institution']} - {amount_str}"
        keyboard.add(
            telebot.types.InlineKeyboardButton(button_text, callback_data=f"paybill_{bill['id']}")
        )

    bot.reply_to(message, "בחר את החשבונית ששילמת:", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith('paybill_'))
def handle_pay_callback(call):
    bot.answer_callback_query(call.id)
    bill_id = int(call.data.split('_', 1)[1])

    # Remove the keyboard so the same button can't be tapped again while
    # this bill is already mid-confirmation.
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

    prompt = bot.send_message(
        call.message.chat.id,
        'יש לך מספר אסמכתא לתשלום? שלח אותו עכשיו, או שלח "דלג" אם אין.',
    )
    bot.register_next_step_handler(prompt, process_asmachta_step, bill_id)


def process_asmachta_step(message, bill_id):
    """Persists the asmachta via the pay endpoint's `asmachta` param
    (app/models.py::Bill.asmachta), not just displaying it back."""
    text = (message.text or "").strip()
    asmachta = None if text == "דלג" else (text or None)
    user_id = message.from_user.id

    try:
        response = backend.put(
            f"{BACKEND_URL}/bills/{bill_id}/pay",
            params={"user_id": user_id, "asmachta": asmachta},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if response.status_code == 404:
        bot.reply_to(message, "⚠️ לא נמצאה חשבונית עם המזהה הזה.")
        return
    if not response.ok:
        bot.reply_to(message, f"❌ הייתה בעיה בסימון החשבונית כשולמה: {_extract_error_detail(response)}")
        return

    data = response.json()
    amount_str = _format_amount(data['amount'], data['currency'])
    confirmation = f"✅ החשבונית מ-{data['institution']} ({amount_str}) סומנה כשולמה!"
    if asmachta:
        confirmation += f"\n🔖 מספר אסמכתא: {asmachta}"
    bot.reply_to(message, confirmation)


@bot.message_handler(commands=['edit'])
def handle_edit(message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        bot.reply_to(message, "⚠️ נא לציין מזהה חשבונית וסכום חדש. דוגמה: /edit 42 150.5")
        return

    bill_id_str, new_amount_str = args[1], args[2]
    if not bill_id_str.isdigit():
        bot.reply_to(message, "⚠️ מזהה החשבונית חייב להיות מספר. דוגמה: /edit 42 150.5")
        return
    try:
        new_amount = float(new_amount_str)
        if new_amount <= 0:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "⚠️ הסכום החדש לא תקין. דוגמה: /edit 42 150.5")
        return

    bill_id = int(bill_id_str)
    user_id = message.from_user.id

    try:
        response = backend.put(
            f"{BACKEND_URL}/bills/{bill_id}/amount",
            params={"user_id": user_id, "new_amount": new_amount},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if response.status_code == 404:
        bot.reply_to(message, "⚠️ לא נמצאה חשבונית עם המזהה הזה.")
        return
    if not response.ok:
        bot.reply_to(message, f"❌ הייתה בעיה בעדכון הסכום: {_extract_error_detail(response)}")
        return

    data = response.json()
    amount_str = _format_amount(data['amount'], data['currency'])
    bot.reply_to(message, f"✏️ הסכום עודכן בהצלחה!\n{data['institution']}: {amount_str}")


@bot.message_handler(commands=['export'])
def handle_export(message):
    user_id = message.from_user.id
    bot.reply_to(message, "מכין קובץ ייצוא... 📤")

    try:
        response = backend.get(
            f"{BACKEND_URL}/bills/export",
            params={"user_id": user_id},
            timeout=DB_QUERY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if not response.ok:
        bot.reply_to(message, f"❌ הייתה בעיה בהפקת הקובץ: {_extract_error_detail(response)}")
        return

    csv_file = io.BytesIO(response.content)
    csv_file.name = "safebill_export.csv"
    bot.send_document(message.chat.id, csv_file, caption="📄 ריכוז כל החשבוניות שלך")


@bot.message_handler(content_types=['text'])
def handle_text_question(message):
    """
    Catch-all for plain text - registered last, on purpose. telebot
    dispatches to the first handler whose filters match, in registration
    order; every commands=[...] handler above is more specific (text
    content AND a matching command) and already claims its own messages,
    so only text that didn't match any of them reaches this one:
    ordinary natural-language questions, and unrecognized slash-commands.

    Forwards the question to the SafeBill Agent via the same POST
    /api/chat endpoint the dashboard's chat widget uses - this script
    never imports app.agent directly, same as it never imports anything
    else under app/; it only ever talks to the backend over HTTP.
    """
    text = (message.text or "").strip()
    if text.startswith('/'):
        bot.reply_to(message, "פקודה לא מוכרת. שלח /help לרשימת הפקודות.")
        return
    if not text:
        return

    user_id = message.from_user.id
    bot.send_chat_action(message.chat.id, 'typing')

    try:
        response = backend.post(
            f"{BACKEND_URL}/api/chat",
            params={"user_id": user_id},
            json={"message": text},
            # Generous, matching the upload timeout: a tool-use turn can
            # involve several Anthropic round trips (decide to call a
            # tool, run it, synthesize the answer), slower than the plain
            # DB-only commands above but not open-ended.
            timeout=BACKEND_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ שגיאה בתקשורת מול השרת: {e}")
        return

    if not response.ok:
        bot.reply_to(message, f"❌ הסוכן לא הצליח לענות: {_extract_error_detail(response)}")
        return

    reply = response.json().get('reply')
    bot.reply_to(message, reply or "מצטער, לא הצלחתי לענות על זה.")


print("🤖 הבוט מאזין להודעות...")
_register_bot_commands()
bot.infinity_polling()
