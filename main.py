import os
import json
import base64
import requests
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GREEN_INVOICE_ID = os.environ["GREEN_INVOICE_ID"]
GREEN_INVOICE_SECRET = os.environ["GREEN_INVOICE_SECRET"]

GREEN_INVOICE_BASE = "https://api.greeninvoice.co.il/api/v1"


# ── Green Invoice helpers ──────────────────────────────────────────────────────

def get_token():
    r = requests.post(
        f"{GREEN_INVOICE_BASE}/account/token",
        json={"id": GREEN_INVOICE_ID, "secret": GREEN_INVOICE_SECRET},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]


def create_expense(token: str, inv: dict):
    headers = {"Authorization": f"Bearer {token}"}

    # חודש דיווח - נגזר מתאריך החשבונית
    date_str = inv.get("date") or ""
    from datetime import date as date_cls
    import re
    parsed_date = None
    if date_str:
        m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date_str)
        if m:
            parsed_date = date_cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            m = re.match(r'(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})', date_str)
            if m:
                parsed_date = date_cls(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if not parsed_date:
        parsed_date = date_cls.today()
    date_str = parsed_date.strftime("%Y-%m-%d")
    reporting_date = parsed_date.strftime("%Y-%m-01")  # יום ראשון בחודש

    payload = {
        "description": inv.get("description") or inv.get("vendor") or "הוצאה",
        "date": date_str,
        "amount": inv.get("total_amount") or 0,
        "vat": inv.get("vat_amount") or 0,
        "currency": inv.get("currency") or "ILS",
        "reportingDate": reporting_date,
        "documentType": 20,
        "supplier": {"name": inv.get("vendor") or ""},
    }
    if inv.get("invoice_number"):
        payload["number"] = inv["invoice_number"]
    r = requests.post(f"{GREEN_INVOICE_BASE}/expenses", json=payload, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def create_income(token: str, inv: dict):
    headers = {"Authorization": f"Bearer {token}"}
    price = inv.get("amount_before_vat") or inv.get("total_amount") or 0
    payload = {
        "type": 305,          # חשבונית מס קבלה
        "lang": "he",
        "date": inv.get("date") or "",
        "description": inv.get("description") or "",
        "currency": inv.get("currency") or "ILS",
        "vatType": 0,         # מע"מ רגיל
        "income": [
            {
                "description": inv.get("description") or inv.get("vendor") or "הכנסה",
                "quantity": 1,
                "price": price,
                "currency": inv.get("currency") or "ILS",
                "vatType": 0,
            }
        ],
    }
    r = requests.post(f"{GREEN_INVOICE_BASE}/documents", json=payload, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Claude Vision ──────────────────────────────────────────────────────────────

def extract_invoice(file_bytes: bytes, mime_type: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    b64 = base64.standard_b64encode(file_bytes).decode()

    if mime_type == "application/pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": b64},
        }

    prompt = (
        'חלץ פרטים מהחשבונית והחזר JSON בלבד (ללא טקסט נוסף):\n'
        '{\n'
        '  "vendor": "שם הספק",\n'
        '  "invoice_number": "מספר חשבונית או null",\n'
        '  "date": "YYYY-MM-DD או null",\n'
        '  "description": "תיאור קצר",\n'
        '  "amount_before_vat": <מספר או null>,\n'
        '  "vat_amount": <מספר או null>,\n'
        '  "total_amount": <מספר>,\n'
        '  "currency": "ILS",\n'
        '  "suggested_category": "אחת מהאפשרויות: דלק ונסיעות / ציוד ומחשבים / טלפון ותקשורת / שיווק ופרסום / שכירות / אוכל וארוחות / השכלה והדרכה / אחר"\n'
        '}'
    )

    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": [content_block, {"type": "text", "text": prompt}]}],
    )

    text = msg.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ── Telegram handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "שלום! 👋\n\n"
        "שלח לי תמונה או PDF של חשבונית ואני אזין אותה לחשבונית ירוקה."
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if msg.photo:
        tg_file = await msg.photo[-1].get_file()
        mime_type = "image/jpeg"
    elif msg.document:
        tg_file = await msg.document.get_file()
        mime_type = msg.document.mime_type or "image/jpeg"
    else:
        await msg.reply_text("שלח תמונה או PDF של חשבונית.")
        return

    await msg.reply_text("⏳ מעבד את החשבונית...")

    try:
        file_bytes = bytes(await tg_file.download_as_bytearray())
        inv = extract_invoice(file_bytes, mime_type)
        context.user_data["invoice"] = inv
    except Exception as e:
        await msg.reply_text(f"❌ לא הצלחתי לקרוא את החשבונית.\nשגיאה: {e}")
        return

    def fmt(val, prefix="₪"):
        return f"{prefix}{val:,.2f}" if val is not None else "לא זוהה"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💸 הוצאה", callback_data="type_expense"),
            InlineKeyboardButton("💰 הכנסה", callback_data="type_income"),
        ]
    ])

    await msg.reply_text(
        f"🧾 מצאתי את הפרטים הבאים:\n\n"
        f"🏪 ספק: {inv.get('vendor') or 'לא זוהה'}\n"
        f"📄 מס׳ חשבונית: {inv.get('invoice_number') or 'לא זוהה'}\n"
        f"📅 תאריך: {inv.get('date') or 'לא זוהה'}\n"
        f"📝 תיאור: {inv.get('description') or 'לא זוהה'}\n"
        f"💵 לפני מע״מ: {fmt(inv.get('amount_before_vat'))}\n"
        f"🧾 מע״מ: {fmt(inv.get('vat_amount'))}\n"
        f"💰 סה״כ: {fmt(inv.get('total_amount'))}\n\n"
        f"מה סוג החשבונית?",
        reply_markup=keyboard,
    )


EXPENSE_CATEGORIES = [
    ("🚗 דלק ונסיעות",     "cat_fuel"),
    ("💻 ציוד ומחשבים",    "cat_equipment"),
    ("📱 טלפון ותקשורת",   "cat_phone"),
    ("📢 שיווק ופרסום",    "cat_marketing"),
    ("🏢 שכירות",          "cat_rent"),
    ("🍽️ אוכל וארוחות",   "cat_food"),
    ("📚 השכלה והדרכה",    "cat_education"),
    ("📋 אחר",             "cat_other"),
]

CAT_LABELS = {cb: label for label, cb in EXPENSE_CATEGORIES}

# מיפוי מהערך שClaude מחזיר לתווית
CLAUDE_CAT_MAP = {
    "דלק ונסיעות":      "🚗 דלק ונסיעות",
    "ציוד ומחשבים":     "💻 ציוד ומחשבים",
    "טלפון ותקשורת":    "📱 טלפון ותקשורת",
    "שיווק ופרסום":     "📢 שיווק ופרסום",
    "שכירות":           "🏢 שכירות",
    "אוכל וארוחות":     "🍽️ אוכל וארוחות",
    "השכלה והדרכה":     "📚 השכלה והדרכה",
    "אחר":              "📋 אחר",
}


async def handle_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    inv_type = query.data.replace("type_", "")
    context.user_data["type"] = inv_type

    if inv_type == "expense":
        inv = context.user_data.get("invoice", {})
        suggested = CLAUDE_CAT_MAP.get(inv.get("suggested_category", ""), "")

        if suggested:
            # Claude הציע קטגוריה - שאל אם נכון
            context.user_data["suggested_category"] = suggested
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ כן", callback_data="cat_confirm_yes"),
                InlineKeyboardButton("❌ לא, בחר אחרת", callback_data="cat_confirm_no"),
            ]])
            await query.edit_message_text(
                f"זיהיתי קטגוריה: {suggested}\nנכון?",
                reply_markup=keyboard,
            )
        else:
            await show_category_list(query)
    else:
        await show_confirmation(query, context)


async def show_category_list(query):
    rows = []
    for i in range(0, len(EXPENSE_CATEGORIES), 2):
        row = [InlineKeyboardButton(label, callback_data=cb)
               for label, cb in EXPENSE_CATEGORIES[i:i+2]]
        rows.append(row)
    await query.edit_message_text(
        "באיזה קטגוריה לרשום את ההוצאה?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def handle_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cat_confirm_yes":
        context.user_data["category"] = context.user_data.get("suggested_category", "")
        await show_confirmation(query, context)
    else:
        await show_category_list(query)


async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["category"] = CAT_LABELS[query.data]
    await show_confirmation(query, context)


async def show_confirmation(query, context: ContextTypes.DEFAULT_TYPE):
    inv = context.user_data.get("invoice", {})
    inv_type = context.user_data.get("type", "expense")
    category = context.user_data.get("category", "")
    label = "הוצאה 💸" if inv_type == "expense" else "הכנסה 💰"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ אישור והזנה", callback_data="confirm"),
            InlineKeyboardButton("❌ ביטול", callback_data="cancel"),
        ]
    ])

    cat_line = f"🗂️ קטגוריה: {category}\n" if category else ""

    await query.edit_message_text(
        f"סיכום לאישור:\n\n"
        f"📋 סוג: {label}\n"
        f"{cat_line}"
        f"🏪 ספק: {inv.get('vendor') or 'לא זוהה'}\n"
        f"📅 תאריך: {inv.get('date') or 'לא זוהה'}\n"
        f"📝 תיאור: {inv.get('description') or 'לא זוהה'}\n"
        f"💰 סה״כ: ₪{inv.get('total_amount') or 0:,.2f}\n\n"
        f"להזין לחשבונית ירוקה?",
        reply_markup=keyboard,
    )


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("ביטול. שלח חשבונית חדשה כשתרצה. 👍")
        return

    inv = context.user_data.get("invoice", {})
    inv_type = context.user_data.get("type", "expense")
    category = context.user_data.get("category", "")

    await query.edit_message_text("⏳ מזין לחשבונית ירוקה...")

    try:
        token = get_token()
        if category:
            inv = {**inv, "description": f"{category} | {inv.get('description') or inv.get('vendor') or ''}"}
        result = create_expense(token, inv) if inv_type == "expense" else create_income(token, inv)
        doc_id = result.get("id") or result.get("documentId") or "—"
        await query.edit_message_text(f"✅ הוזן בהצלחה!\n\nמזהה מסמך: {doc_id}")
    except requests.HTTPError as e:
        await query.edit_message_text(f"❌ שגיאה מחשבונית ירוקה:\n{e.response.text}")
    except Exception as e:
        await query.edit_message_text(f"❌ שגיאה: {e}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(handle_type, pattern=r"^type_"))
    app.add_handler(CallbackQueryHandler(handle_category_confirm, pattern=r"^cat_confirm_"))
    app.add_handler(CallbackQueryHandler(handle_category, pattern=r"^cat_"))
    app.add_handler(CallbackQueryHandler(handle_confirm, pattern=r"^(confirm|cancel)$"))

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    port = int(os.environ.get("PORT", 8080))

    if webhook_url:
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="/webhook",
            webhook_url=f"{webhook_url}/webhook",
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
