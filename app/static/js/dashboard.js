// SafeBill dashboard client script.
//
// Registers Alpine components via the "alpine:init" event, which Alpine
// fires right before it scans the DOM for x-data. This script tag and the
// Alpine CDN tag in dashboard.html are both `defer`, and defer scripts run
// in document order, so this file loads (and attaches the listener)
// before Alpine's own script executes - the ordering Alpine's own docs
// recommend for registering components this way.

// user_id is server-rendered (see GET /dashboard in app/routers/web.py)
// but this file is served as a static asset, never through Jinja - so the
// value crosses the boundary via a data-* attribute on <body> rather than
// being templated directly into this .js file.
const SAFEBILL_USER_ID = Number(document.body.dataset.userId);

// Thin module-level wrapper around the Alpine.store('app') translation
// helper (registered below) - lets every plain JS method in this file
// call t('key') directly instead of the more verbose
// Alpine.store('app').t('key') at every call site. Only resolves
// Alpine.store('app') when actually invoked, not at definition time, so
// it's safe to define before the store itself is registered further down.
function t(key) {
    return Alpine.store('app').t(key);
}

document.addEventListener('alpine:init', () => {
    // Alpine.store(), not a component-scoped Alpine.data() - lang/t()
    // need to be callable from inside billUpload/billEditor/chatWidget's
    // own JS methods (e.g. `this.errorMessage = ...t('error_network')`),
    // not just from HTML directive expressions. Alpine's up-the-DOM-tree
    // scope resolution (how openCreateModal() below reaches billEditor()
    // from billUpload()'s nested scope) only works inside x-text/@click/
    // etc. expressions - it doesn't apply to plain `this.x` calls inside
    // a component's own method bodies, since `this` there is just that
    // component's own reactive object. Alpine.store() is reachable
    // identically from both: $store.app.t(...) in HTML, and
    // Alpine.store('app').t(...) from anywhere in JS.
    Alpine.store('app', {
        lang: 'he',

        translations: {
            he: {
                page_title: 'לוח הבקרה - SafeBill',
                dashboard_subtitle: 'לוח בקרה חכם',
                about_link: 'אודות',

                about_title: 'אודות SafeBill',
                about_body:
                    'SafeBill היא יוזמה חכמה לניהול חשבוניות, שפותחה כפרויקט מתקדם במדעי המחשב במכללה האקדמית למינהל (The College of Management Academic Studies). המערכת משלבת בינה מלאכותית לזיהוי וניתוח אוטומטי של חשבוניות, לצד מעקב תשלומים ותובנות פיננסיות אישיות.',
                privacy_title: 'פרטיות ואבטחה',
                privacy_body_1:
                    'תמונות וקבצי PDF של חשבוניות אינן נשמרות לצמיתות בשרתי המערכת בשום שלב - הן מעובדות בזיכרון בלבד לצורך חילוץ הנתונים, ונמחקות מיידית עם סיום התהליך. רק הנתונים המחולצים (שם ספק, סכום, תאריך וקטגוריה) נשמרים במסד הנתונים.',
                privacy_body_2:
                    'לצורך חילוץ הנתונים, התמונה מועברת פעם אחת למודל בינה מלאכותית לעיבוד - ואינה נשמרת על ידי המערכת בשום שלב, לפני, במהלך או אחרי העיבוד.',
                close: 'סגור',

                onboarding_step1_title: 'שלב 1: העלאת חשבוניות',
                onboarding_step1_body: 'גררו תמונה או PDF של חשבונית לאזור ההעלאה, או צלמו אותה ישירות מהנייד.',
                onboarding_step2_title: 'שלב 2: ניתוח חכם',
                onboarding_step2_body:
                    'בינה מלאכותית מזהה את הפרטים אוטומטית - ותמיד אפשר לערוך ידנית אם משהו לא מדויק.',
                onboarding_step3_title: 'שלב 3: מעקב ותשלום',
                onboarding_step3_body:
                    'כל החשבוניות שלכם במקום אחד, מסודרות לפי סטטוס, כדי שלא תפספסו תאריך תשלום.',
                skip: 'דלג',
                previous: 'הקודם',
                next: 'הבא',
                got_it: 'הבנתי',

                upload_section_title: 'העלאת חשבונית חדשה',
                upload_zone_aria: 'העלאת חשבונית - גררו קובץ, צלמו או לחצו לבחירה',
                uploading_status: 'מעלה ומנתח את החשבונית...',
                upload_success_saved: 'החשבונית נשמרה בהצלחה, מרענן...',
                upload_partial_saved_title: 'החשבונית נשמרה',
                upload_partial_saved_message: 'שירות הזיהוי האוטומטי אינו זמין כרגע - ניתן לעדכן את הפרטים ידנית מהטבלה',
                refreshing: 'מרענן...',
                try_again_hint: 'לחצו כדי לנסות שוב',
                manual_add: 'הוספה ידנית',
                drop_zone_prompt: 'גררו קובץ לכאן, צלמו או לחצו לבחירה',
                file_types_hint: 'PDF, PNG או JPG',

                error_unsupported_file: 'סוג קובץ לא נתמך. יש להעלות PDF, PNG או JPG.',
                error_file_too_large: 'הקובץ גדול מדי.',
                error_duplicate_bill: 'החשבונית כבר קיימת במערכת.',
                error_not_a_bill: 'לא הצלחתי לזהות את זה כחשבונית תקינה.',
                error_scan_unavailable: 'לא ניתן להעלות חשבונית כעת, אפשר להוסיף את החשבונית באופן ידני.',
                error_generic_save: 'הייתה בעיה בשמירת החשבונית.',
                error_network: 'לא הצלחתי להתחבר לשרת.',

                summary_title: 'סיכום',
                pending_bills_count_label: 'חשבוניות ממתינות לתשלום',
                pending_total_label: 'סך הכל ממתין',
                bills_count_suffix: 'חשבוניות',
                all_paid_message: 'הכל שולם! אין חשבוניות ממתינות.',

                regular_bills_title: 'חשבוניות שוטפות',
                tickets_title: 'דוחות וקנסות',
                th_institution: 'מוסד',
                th_amount: 'סכום',
                th_due_date: 'תאריך יעד',
                th_status: 'סטטוס',
                th_category: 'קטגוריה',
                th_reference: 'אסמכתא',
                th_actions: 'פעולות',
                th_payment: 'תשלום',
                unknown_date: 'לא ידוע',
                status_paid: 'שולם',
                status_analyzed: 'פתוח',
                status_extraction_failed: 'נדרש עדכון ידני',
                status_pending_analysis: 'בעיבוד',
                edit_bill: 'עריכת חשבונית',
                mark_paid_aria: 'סמן כשולם',
                no_bills_saved: 'עדיין אין חשבוניות שמורות.',
                no_tickets: 'אין דוחות או קנסות רשומים.',

                // Category labels, keyed by the literal Hebrew string
                // stored in bills.category (see ALLOWED_CATEGORIES in
                // app/vision.py) rather than a separate category_x naming
                // scheme - lets a row's raw category be looked up
                // directly via t(bill.category), no second mapping layer.
                חשמל: 'חשמל',
                מים: 'מים',
                'ארנונה ומיסים': 'ארנונה ומיסים',
                'תקשורת ואינטרנט': 'תקשורת ואינטרנט',
                גז: 'גז',
                ביטוחים: 'ביטוחים',
                'רכב ותחבורה': 'רכב ותחבורה',
                'קנסות ודוחות': 'קנסות ודוחות',
                'שירותים ומנויים': 'שירותים ומנויים',
                'הוצאה כללית': 'הוצאה כללית',

                add_bill_title: 'הוספת חשבונית חדשה',
                field_currency: 'מטבע',
                cancel: 'ביטול',
                save: 'שמירה',
                saving: 'שומר...',
                add: 'הוספה',
                delete: 'מחק',
                deleting: 'מוחק...',
                delete_confirm: 'למחוק לצמיתות את החשבונית מ"{institution}"?',
                error_institution_required: 'יש להזין שם מוסד.',
                error_amount_positive: 'הסכום חייב להיות גדול מאפס.',
                error_save_failed: 'שמירת השינויים נכשלה.',
                error_create_failed: 'הוספת החשבונית נכשלה.',
                error_delete_failed: 'מחיקת החשבונית נכשלה.',
                error_pay_failed: 'סימון החשבונית כשולמה נכשל.',

                chat_agent_name: 'סוכן SafeBill',
                chat_agent_subtitle: 'שאל אותי על החשבוניות שלך',
                chat_toggle_aria: "פתח צ'אט עם סוכן SafeBill",
                chat_greeting:
                    'היי! אפשר לשאול אותי כמה נשאר לך לשלם, לחפש חשבונית לפי מוסד או קטגוריה, ועוד. איך אפשר לעזור?',
                chat_input_placeholder: 'שאל שאלה...',
                chat_typing: 'כותב...',
                chat_send_aria: 'שלח',
                chat_error_generic: 'שגיאה בתקשורת עם הסוכן.',
                chat_error_no_answer: 'מצטער, לא הצלחתי לענות על זה.',

                footer_credit: 'פותח ע"י Nir Halfon',
                footer_rights: 'SafeBill (2026) · כל הזכויות שמורות',
            },
            en: {
                page_title: 'Dashboard - SafeBill',
                dashboard_subtitle: 'Smart Dashboard',
                about_link: 'About',

                about_title: 'About SafeBill',
                about_body:
                    'SafeBill is a smart bill management initiative, developed as an advanced computer science project at The College of Management Academic Studies. The system combines AI-driven bill recognition and analysis with payment tracking and personal financial insights.',
                privacy_title: 'Privacy & Security',
                privacy_body_1:
                    'Bill images and PDFs are never permanently stored on our servers, at any stage - they are processed in memory only for data extraction, and deleted immediately once that finishes. Only the extracted data (vendor name, amount, date, and category) is saved to the database.',
                privacy_body_2:
                    'To extract the data, the image is sent once to an AI model for processing - it is not stored by the system at any point, before, during, or after that processing.',
                close: 'Close',

                onboarding_step1_title: 'Step 1: Upload Bills',
                onboarding_step1_body:
                    'Drag an image or PDF of a bill into the upload area, or take a photo of it directly from your phone.',
                onboarding_step2_title: 'Step 2: Smart Analysis',
                onboarding_step2_body:
                    "AI identifies the details automatically - and you can always edit them manually if something isn't quite right.",
                onboarding_step3_title: 'Step 3: Track & Pay',
                onboarding_step3_body: 'All your bills in one place, organized by status, so you never miss a due date.',
                skip: 'Skip',
                previous: 'Previous',
                next: 'Next',
                got_it: 'Got it',

                upload_section_title: 'Upload New Bill',
                upload_zone_aria: 'Upload a bill - drag a file, take a photo, or click to choose',
                uploading_status: 'Uploading and analyzing the bill...',
                upload_success_saved: 'Bill saved successfully, refreshing...',
                upload_partial_saved_title: 'Bill saved',
                upload_partial_saved_message:
                    'The automatic recognition service is currently unavailable - you can update the details manually from the table',
                refreshing: 'Refreshing...',
                try_again_hint: 'Click to try again',
                manual_add: 'Manual Add',
                drop_zone_prompt: 'Drag a file here, take a photo, or click to choose',
                file_types_hint: 'PDF, PNG, or JPG',

                error_unsupported_file: 'Unsupported file type. Please upload a PDF, PNG, or JPG.',
                error_file_too_large: 'The file is too large.',
                error_duplicate_bill: 'This bill already exists in the system.',
                error_not_a_bill: "Couldn't recognize this as a valid bill.",
                error_scan_unavailable: 'Cannot upload a bill right now - you can add the bill manually.',
                error_generic_save: 'There was a problem saving the bill.',
                error_network: "Couldn't connect to the server.",

                summary_title: 'Summary',
                pending_bills_count_label: 'Bills pending payment',
                pending_total_label: 'Total pending',
                bills_count_suffix: 'bills',
                all_paid_message: 'All paid! No pending bills.',

                regular_bills_title: 'Regular Bills',
                tickets_title: 'Fines & Tickets',
                th_institution: 'Institution',
                th_amount: 'Amount',
                th_due_date: 'Due Date',
                th_status: 'Status',
                th_category: 'Category',
                th_reference: 'Reference',
                th_actions: 'Actions',
                th_payment: 'Payment',
                unknown_date: 'Unknown',
                status_paid: 'Paid',
                status_analyzed: 'Open',
                status_extraction_failed: 'Needs manual update',
                status_pending_analysis: 'Processing',
                edit_bill: 'Edit bill',
                mark_paid_aria: 'Mark as paid',
                no_bills_saved: 'No bills saved yet.',
                no_tickets: 'No fines or tickets recorded.',

                חשמל: 'Electricity',
                מים: 'Water',
                'ארנונה ומיסים': 'Arnona & Taxes',
                'תקשורת ואינטרנט': 'Communication & Internet',
                גז: 'Gas',
                ביטוחים: 'Insurances',
                'רכב ותחבורה': 'Car & Transportation',
                'קנסות ודוחות': 'Fines & Tickets',
                'שירותים ומנויים': 'Services & Subscriptions',
                'הוצאה כללית': 'General Expense',

                add_bill_title: 'Add New Bill',
                field_currency: 'Currency',
                cancel: 'Cancel',
                save: 'Save',
                saving: 'Saving...',
                add: 'Add',
                delete: 'Delete',
                deleting: 'Deleting...',
                delete_confirm: 'Permanently delete the bill from "{institution}"?',
                error_institution_required: 'Please enter an institution name.',
                error_amount_positive: 'The amount must be greater than zero.',
                error_save_failed: 'Saving the changes failed.',
                error_create_failed: 'Adding the bill failed.',
                error_delete_failed: 'Deleting the bill failed.',
                error_pay_failed: 'Marking the bill as paid failed.',

                chat_agent_name: 'SafeBill Agent',
                chat_agent_subtitle: 'Ask me about your bills',
                chat_toggle_aria: 'Open chat with the SafeBill agent',
                chat_greeting:
                    "Hi! You can ask me how much you still owe, search for a bill by institution or category, and more. How can I help?",
                chat_input_placeholder: 'Ask a question...',
                chat_typing: 'Typing...',
                chat_send_aria: 'Send',
                chat_error_generic: 'Error communicating with the agent.',
                chat_error_no_answer: "Sorry, I couldn't answer that.",

                footer_credit: 'Developed by: Nir Halfon',
                footer_rights: 'SafeBill (2026) · All rights reserved',
            },
        },

        t(key) {
            return this.translations[this.lang][key] || key;
        },

        toggleLang() {
            this.lang = this.lang === 'he' ? 'en' : 'he';
            document.documentElement.lang = this.lang;
            document.title = this.t('page_title');
        },
    });

    Alpine.data('billUpload', () => ({
        isDragging: false,
        uploading: false,
        result: null,
        errorMessage: null,
        // True only for the 502/504 "AI scanner unavailable" case - a
        // plain flag the template checks directly, rather than comparing
        // errorMessage against t('error_scan_unavailable') (which also
        // changes with the active language) from an inline x-show
        // expression.
        scanUnavailable: false,

        // Client-side check only - saves the user a wasted round trip for
        // an obviously-too-big file. The server (settings.max_upload_bytes
        // in app/config.py, currently 10 MB) is the real, authoritative
        // limit regardless; a mismatch here just means a slightly late or
        // slightly early rejection message, never a bypass.
        _maxBytes: 10 * 1024 * 1024,
        _allowedTypes: ['image/png', 'image/jpeg', 'image/jpg', 'application/pdf'],

        formatMoney(amount, currency) {
            return `${Number(amount).toFixed(2)} ${currency}`;
        },

        _isAllowedFile(file) {
            if (this._allowedTypes.includes(file.type)) return true;
            // Some browsers report an empty `type` for drag-dropped or
            // camera-captured files - fall back to the extension rather
            // than reject a valid file.
            return /\.(pdf|png|jpe?g)$/i.test(file.name || '');
        },

        // Fires on file-picker selection, camera capture, and drop -
        // whichever produced the FileList, this is the single place a
        // selected file turns into an upload.
        handleFiles(fileList) {
            const file = fileList && fileList[0];
            if (!file || this.uploading) return;

            this.result = null;
            this.errorMessage = null;
            this.scanUnavailable = false;

            if (!this._isAllowedFile(file)) {
                this.errorMessage = t('error_unsupported_file');
                return;
            }
            if (file.size > this._maxBytes) {
                this.errorMessage = t('error_file_too_large');
                return;
            }

            this.upload(file);
        },

        async upload(file) {
            this.uploading = true;
            try {
                // Raw bytes as the body, not FormData/multipart - matches
                // POST /bills/upload's contract exactly (app/routers/
                // sanitizer.py), which reads the whole request body as the
                // image/PDF bytes themselves and never parses multipart at
                // all. A File object is a valid fetch body on its own, so
                // no wrapping is needed either way.
                const response = await fetch(`/bills/upload?user_id=${SAFEBILL_USER_ID}`, {
                    method: 'POST',
                    headers: { 'Content-Type': file.type || 'application/octet-stream' },
                    body: file,
                });

                if (response.status === 202) {
                    this.result = await response.json();
                    // Same-server-render refresh rather than a hand-rolled
                    // client-side row insert - guarantees the table matches
                    // exactly what a fresh page load would produce (money
                    // formatting, status pill logic, etc.), with no second
                    // rendering path to keep in sync with dashboard.html.
                    setTimeout(() => window.location.reload(), 1600);
                    return;
                }

                // 502/504 specifically mean the AI scanner itself is
                // unavailable (provider error / timed out) rather than a
                // problem with this particular file - 409/413/422 below
                // are all deterministic rejections (duplicate, too large,
                // not a valid bill) unrelated to AI availability, so they
                // keep their own specific messages instead.
                if (response.status === 502 || response.status === 504) {
                    this.errorMessage = t('error_scan_unavailable');
                    this.scanUnavailable = true;
                    return;
                }

                // Mirrors telegram_bot.py's _process_bill_upload status-code
                // handling so the same upload failure reads the same way
                // regardless of which surface (bot or dashboard) hit it.
                const messages = {
                    409: t('error_duplicate_bill'),
                    422: t('error_not_a_bill'),
                    413: t('error_file_too_large'),
                };
                if (messages[response.status]) {
                    this.errorMessage = messages[response.status];
                } else {
                    const detail = await response.json().catch(() => null);
                    this.errorMessage = (detail && detail.detail) || t('error_generic_save');
                }
            } catch (networkError) {
                this.errorMessage = t('error_network');
            } finally {
                this.uploading = false;
            }
        },
    }));

    Alpine.data('billEditor', () => ({
        open: false,
        saving: false,
        deleting: false,
        payingBillId: null,
        errorMessage: null,
        form: {
            id: null,
            institution: '',
            amount: '',
            currency: 'ILS',
            due_date: '',
            status: 'pending_analysis',
            category: '',
            asmachta: '',
        },

        // buttonEl is the clicked pencil-icon <button>, whose data-*
        // attributes (rendered from bill.* by Jinja - see the table row
        // in dashboard.html) are the only source of the bill's current
        // values here; there's no separate fetch to load them.
        openEditModal(buttonEl) {
            const d = buttonEl.dataset;
            this.form = {
                id: Number(d.id),
                institution: d.institution,
                // Deliberately left as the raw string from the DOM, not
                // Number(...) - avoids a float round-trip on a monetary
                // value; sent to the server as-is and parsed there
                // straight into a Decimal.
                amount: d.amount,
                currency: d.currency,
                due_date: d.dueDate || '',
                status: d.status,
                category: d.category || '',
                asmachta: d.asmachta || '',
            };
            this.errorMessage = null;
            this.open = true;
        },

        // Manual Add fallback - opens the same modal with an empty form
        // and no id, rather than a separate creation UI. save() below
        // reads form.id to decide POST (create) vs PATCH (update), so
        // this is the only thing that distinguishes "create" mode: an
        // empty form.
        openCreateModal() {
            this.form = {
                id: null,
                institution: '',
                amount: '',
                currency: 'ILS',
                due_date: '',
                status: 'pending_analysis',
                category: '',
                asmachta: '',
            };
            this.errorMessage = null;
            this.open = true;
        },

        close() {
            this.open = false;
        },

        async save() {
            const institution = this.form.institution.trim();
            const amountNumber = Number(this.form.amount);

            if (!institution) {
                this.errorMessage = t('error_institution_required');
                return;
            }
            if (!Number.isFinite(amountNumber) || amountNumber <= 0) {
                this.errorMessage = t('error_amount_positive');
                return;
            }

            // form.id is only ever set by openEditModal() (from an
            // existing bill's data-id) - openCreateModal() always starts
            // it null, so this is a reliable create-vs-update signal.
            const isCreate = !this.form.id;
            const url = isCreate
                ? `/bills?user_id=${SAFEBILL_USER_ID}`
                : `/bills/${this.form.id}?user_id=${SAFEBILL_USER_ID}`;

            this.saving = true;
            this.errorMessage = null;
            try {
                const response = await fetch(url, {
                    method: isCreate ? 'POST' : 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        institution,
                        amount: this.form.amount,
                        // Previously omitted here even though the
                        // currency <select> was already bound to
                        // form.currency - the dropdown updated local
                        // state but PATCH /bills/{id} had no currency
                        // field to receive it, so a changed currency
                        // silently never saved. Fixed together with this
                        // change since both endpoints now accept it
                        // (see BillUpdateRequest/BillCreateRequest in
                        // app/main.py).
                        currency: this.form.currency,
                        due_date: this.form.due_date || null,
                        status: this.form.status,
                        category: this.form.category || null,
                        asmachta: this.form.asmachta || null,
                    }),
                });

                if (!response.ok) {
                    const detail = await response.json().catch(() => null);
                    this.errorMessage =
                        (detail && detail.detail) ||
                        (isCreate ? t('error_create_failed') : t('error_save_failed'));
                    return;
                }

                // Full reload, same as the upload flow - the table (status
                // pill included, so an "extraction_failed" row correctly
                // stops showing נדרש עדכון ידני once its status changes)
                // ends up exactly as a fresh GET /dashboard would render
                // it, with no second client-side row-patching path to
                // maintain.
                window.location.reload();
            } catch (networkError) {
                this.errorMessage = t('error_network');
            } finally {
                this.saving = false;
            }
        },

        async deleteBill() {
            // Irreversible - DELETE /bills/{id} is a hard delete, not a
            // status flip (see app/main.py::delete_bill). A native confirm()
            // is the one blocking-dialog exception in this app - everywhere
            // else uses inline error text instead, but this is the only
            // destructive, unrecoverable action in the whole edit flow.
            if (!confirm(t('delete_confirm').replace('{institution}', this.form.institution))) return;

            this.deleting = true;
            this.errorMessage = null;
            try {
                const response = await fetch(`/bills/${this.form.id}?user_id=${SAFEBILL_USER_ID}`, {
                    method: 'DELETE',
                });

                if (!response.ok) {
                    const detail = await response.json().catch(() => null);
                    this.errorMessage = (detail && detail.detail) || t('error_delete_failed');
                    return;
                }

                window.location.reload();
            } catch (networkError) {
                this.errorMessage = t('error_network');
            } finally {
                this.deleting = false;
            }
        },

        // Quick-pay: marks a bill paid directly from the table row, no
        // modal involved. Reuses PUT /bills/{id}/pay (app/main.py) - the
        // same endpoint the edit modal's "paid" status option ultimately
        // relies on - without its optional asmachta param; a confirmation
        // number can still be added later via the edit modal if wanted.
        // No confirm() guard here unlike deleteBill() - this only flips a
        // status field and is trivially reversible from the edit modal,
        // not a destructive, unrecoverable action.
        async quickPay(billId) {
            if (this.payingBillId) return;
            this.payingBillId = billId;
            try {
                const response = await fetch(`/bills/${billId}/pay?user_id=${SAFEBILL_USER_ID}`, {
                    method: 'PUT',
                });

                if (!response.ok) {
                    const detail = await response.json().catch(() => null);
                    alert((detail && detail.detail) || t('error_pay_failed'));
                    return;
                }

                window.location.reload();
            } catch (networkError) {
                alert(t('error_network'));
            } finally {
                this.payingBillId = null;
            }
        },
    }));

    Alpine.data('chatWidget', () => ({
        open: false,
        sending: false,
        input: '',
        // Set once at component creation, not reactively re-translated on
        // a later toggleLang() - chat history reads as a log of what was
        // actually shown/said at the time, and retroactively rewriting
        // past turns would be actively wrong for the agent's own replies
        // (see the note on send() below), so the greeting - the one line
        // here that's pure UI chrome, not an agent response - doesn't
        // special-case itself out of that same log-like behavior either.
        messages: [
            {
                sender: 'agent',
                text: t('chat_greeting'),
            },
        ],

        toggle() {
            this.open = !this.open;
            if (this.open) {
                this.$nextTick(() => this.$refs.chatInput?.focus());
            }
        },

        close() {
            this.open = false;
        },

        scrollToBottom() {
            this.$nextTick(() => {
                const el = this.$refs.chatMessages;
                if (el) el.scrollTop = el.scrollHeight;
            });
        },

        // Note on scope: this only translates the *client-side* fallback
        // messages below (network/parse failures this page detects
        // itself). A successful reply's text comes straight from
        // POST /api/chat, which always answers in Hebrew regardless of
        // this page's lang - see _AGENT_SYSTEM_PROMPT's "Always answer in
        // Hebrew" rule in app/agent.py. Making the agent itself bilingual
        // would mean changing that prompt, a separate, larger change this
        // dashboard-only i18n pass doesn't attempt.
        async send() {
            const text = this.input.trim();
            if (!text || this.sending) return;

            this.messages.push({ sender: 'user', text });
            this.input = '';
            this.sending = true;
            this.scrollToBottom();

            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: SAFEBILL_USER_ID, message: text }),
                });

                if (!response.ok) {
                    let detail = t('chat_error_generic');
                    try {
                        const errBody = await response.json();
                        detail = errBody.detail || detail;
                    } catch (parseError) {
                        // body wasn't JSON - keep the generic message
                    }
                    this.messages.push({ sender: 'agent', text: detail });
                } else {
                    const data = await response.json();
                    this.messages.push({
                        sender: 'agent',
                        text: data.reply || t('chat_error_no_answer'),
                    });
                }
            } catch (networkError) {
                this.messages.push({ sender: 'agent', text: t('error_network') });
            } finally {
                this.sending = false;
                this.scrollToBottom();
            }
        },
    }));

    Alpine.data('aboutModal', () => ({
        open: false,
    }));

    Alpine.data('onboarding', () => ({
        open: false,
        step: 1,
        totalSteps: 3,

        // Alpine calls init() automatically once, right when this
        // component is created - the natural place to check whether the
        // tutorial has already been dismissed on a previous visit.
        init() {
            try {
                if (!localStorage.getItem('tutorialSeen')) {
                    this.open = true;
                }
            } catch (e) {
                // localStorage unavailable (e.g. private browsing) -
                // default to not auto-showing rather than let this throw
                // during Alpine's page-wide init.
            }
        },

        next() {
            if (this.step < this.totalSteps) {
                this.step++;
            } else {
                this.finish();
            }
        },

        prev() {
            if (this.step > 1) this.step--;
        },

        goTo(step) {
            this.step = step;
        },

        finish() {
            this.open = false;
            try {
                localStorage.setItem('tutorialSeen', 'true');
            } catch (e) {
                // Best-effort - worst case the tutorial just shows again
                // on the next visit, which is a harmless degradation.
            }
        },
    }));
});
