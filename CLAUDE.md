# invoice-bot — CLAUDE.md

## מה הפרויקט עושה
בוט טלגרם שמקבל תמונה/PDF של חשבונית, מחלץ פרטים עם Claude Vision,
ומזין אוטומטית לחשבונית ירוקה (Morning) דרך ה-API שלהם.

---

## Tech Stack

| רכיב | פרטים |
|------|--------|
| שפה | Python 3.11 (`.python-version=3.11` — לא 3.13, יש בעיות עם python-telegram-bot) |
| Telegram | python-telegram-bot==20.7 (async, webhook/polling) |
| AI | anthropic==0.25.0 — Claude Vision לחילוץ נתוני חשבונית |
| HTTP | requests==2.31.0 |
| Deploy | Railway — webhook mode, PORT מ-env |

---

## מבנה קבצים

```
invoice-bot/
├── main.py          # כל הקוד — handlers, API calls, mappings
├── requirements.txt
├── Procfile         # web: python main.py
└── .python-version  # 3.11
```

הקוד מאוחד בקובץ אחד (`main.py`). אין מסד נתונים, אין tests, אין packages פנימיים.

---

## Environment Variables (Railway)

```
TELEGRAM_TOKEN       # טוקן הבוט
ANTHROPIC_API_KEY    # מפתח API של Anthropic
GREEN_INVOICE_ID     # מזהה חשבון בחשבונית ירוקה
GREEN_INVOICE_SECRET # סוד חשבון בחשבונית ירוקה
WEBHOOK_URL          # כתובת Railway (אם ריק — רץ ב-polling)
PORT                 # ברירת מחדל: 8080
```

---

## Development Commands

```bash
# התקנה
pip install -r requirements.txt

# הרצה מקומית (polling mode, ללא webhook)
export TELEGRAM_TOKEN=... ANTHROPIC_API_KEY=... GREEN_INVOICE_ID=... GREEN_INVOICE_SECRET=...
python main.py

# הרצה במצב debug
DEBUG=true python main.py

# בדיקת lint
flake8 main.py
```

---

## בדיקה לפני Deploy

לפני כל push — בדוק ידנית בבוט (`t.me/invoice_mnpfbot`) שהזרימה עובדת:

### ✅ צ'קליסט בדיקה

**1. בסיסי — חשבונית הוצאה**
- [ ] שלח תמונת חשבונית → הבוט מחלץ פרטים נכון (ספק, סכום, תאריך)
- [ ] בחר "הוצאה" → מוצגת קטגוריה מוצעת
- [ ] אשר קטגוריה → מוצגת בחירת תשלום (ברירת מחדל: כרטיס אשראי)
- [ ] אשר → מוצג סיכום עם כל הפרטים
- [ ] לחץ "אישור והזנה" → מתקבל מזהה מסמך מחשבונית ירוקה

**2. ודא בחשבונית ירוקה**
- [ ] היכנס לממשק חשבונית ירוקה → ההוצאה מופיעה עם הסיווג הנכון

**3. זרימות נוספות (לפי הצורך)**
- [ ] PDF — וודא שחילוץ עובד גם על PDF ולא רק תמונה
- [ ] ביטול — לחץ "ביטול" בסיכום → הבוט מאשר ביטול, ללא הזנה
- [ ] קטגוריה ידנית — ענה "לא" לקטגוריה המוצעת → מוצגת רשימה מלאה
- [ ] אמצעי תשלום ידני — ענה "לא" לכרטיס אשראי → מוצגות כל האפשרויות
- [ ] `/debug` — מחזיר רשימת סיווגים (בדיקת חיבור ל-API)

**4. edge cases**
- [ ] חשבונית ללא מספר חשבונית — לא אמורה להיכשל (שגיאה 3306)
- [ ] תאריך בפורמט DD/MM/YYYY — מתפרש נכון

---

## Deploy לייצור

```bash
git add .
git commit -m "תיאור"
git push
# Railway מתעדכן אוטומטית
```

---

## Green Invoice API

- Base URL: `https://api.greeninvoice.co.il/api/v1`
- Auth: `POST /account/token` → מחזיר `token` (Bearer)
- הוצאה: `POST /expenses` — `documentType=20`, `paymentType`, `accountingClassification`
- הכנסה: `POST /documents` — `type=305`, `vatType=0`
- סיווגים: `GET /accounting/classifications/map` — חיפוש לפי **שם** (לא קוד, יש כפילויות)

### דוגמת בקשה תקינה — הוצאה

```json
POST /expenses
Authorization: Bearer <token>

{
  "description": "🚗 דלק ונסיעות | תדלוק",
  "date": "2026-03-15",
  "amount": 200.00,
  "vat": 26.55,
  "currency": "ILS",
  "reportingDate": "2026-03-01",
  "documentType": 20,
  "supplier": { "name": "סונול" },
  "paymentType": 3,
  "accountingClassification": {
    "irsCode": 3567,
    "id": "<classification-id-from-map>"
  }
}
```

> `number` (מספר חשבונית) — שדה אופציונלי, שלח רק אם קיים.

### מיפוי קטגוריות → סיווגי חשבון

| קטגוריה | irsCode | classification name |
|---------|---------|---------------------|
| דלק ונסיעות | 3567 | דלק רכב 4769834 |
| ציוד ומחשבים | 3680 | תוכנה |
| טלפון / שיווק / שכירות | 3500 | הוצאות הנהלה וכלליות |
| אוכל / השכלה / אחר | 1390 | עלויות אחרות |

---

## כללי עבודה

- **קובץ יחיד** — כל הקוד ב-`main.py`. אל תפצל לקבצים אלא אם מתחייב.
- **אין DB** — state מאוחסן ב-`context.user_data` (in-memory, per-user).
- **async** — כל Telegram handlers הם `async def`. פניות ל-Green Invoice הן sync (requests).
- **reportingDate** — תמיד `YYYY-MM-01` (יום ראשון בחודש).
- **invoiceNumber** — שולחים רק אם קיים (שדה ריק גורם לשגיאה 3306).
- **supplier** — שדה `supplier: {name: "..."}` ולא `vendor` ישיר.
- לא לשדרג Python ל-3.13 — python-telegram-bot 20.7 לא תואם.
- **שגיאות** — תמיד לוג traceback מלא ל-stderr (`import traceback; traceback.print_exc()`), לא רק `str(e)`.
- **לפני תיקון באג** — בדוק אם זה async/sync conflict: Telegram handlers הם async, אבל `requests` הוא sync. קריאה ל-`requests` מתוך handler היא תקינה, אבל אסור לקרוא לפונקציית `async` מתוך קוד sync רגיל.
- **Async/Sync Bridge** — אם קריאה ל-Green Invoice API גורמת לחסימה של ה-event loop, עטוף אותה ב-`run_in_executor`:
  ```python
  loop = asyncio.get_event_loop()
  result = await loop.run_in_executor(None, lambda: create_expense(token, inv))
  ```

---

## פקודות הבוט

| פקודה | תיאור |
|-------|-------|
| `/start` | הודעת פתיחה |
| `/debug` | מציג את רשימת הסיווגים מהחשבון (עד 20) |

---

## API Reference

- **תיעוד רשמי:** https://www.greeninvoice.co.il/api-docs/
- **טיפול בשגיאות:** בכל שגיאת API — לוג את `errorCode` ו-`message` מתוך ה-JSON:
  ```python
  except requests.HTTPError as e:
      err = e.response.json()
      print(f"Green Invoice error {err.get('errorCode')}: {err.get('message')}", file=sys.stderr)
  ```

### שגיאות נפוצות

| קוד | סיבה | פתרון |
|-----|------|--------|
| 3306 | `number` נשלח כ-string ריק | שלח את השדה רק אם קיים (`if inv.get("invoice_number")`) |
| 400 | `reportingDate` בפורמט שגוי | וודא פורמט `YYYY-MM-01` בדיוק |
| 401/403 | טוקן פג תוקף או חסרות הרשאות | בדוק `GREEN_INVOICE_SECRET` — קבל טוקן חדש |

---

## בעיות ידועות / לא מומש

- הכנסות — הקוד קיים אבל לא נבדק מקצה לקצה
- חשבוניות ישראל (מספר הקצאה) — לא מטופל
