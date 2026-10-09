import os
import json
import base64
import sqlite3
import secrets
import string
import logging
from datetime import datetime, timedelta
from threading import Lock

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ==================================================
# ALI VIP BOT - CONFIGURATION
# ==================================================

BOT_TOKEN = os.getenv("8932533833:AAFdTtLA_lFN2b1n6z9CW1nInvkrW833FFs", "")
ADMIN_ID = int(os.getenv("8932533833", "0"))

GH_OWNER = "ali12hchcfxkzy"
GH_REPO = "ALI-VIP-BOT-ALI"
GH_BRANCH = "main"
GH_TOKEN = os.getenv("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", "")

WORKER_URL = os.getenv(
    "WORKER_URL",
    "https://hidden-sound-6927.dyskwrddyskwrd504.workers.dev/verify"
)

DB = os.getenv("DB_PATH", "ali_vip.db")
SYNC_LOCK = Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("ALI-VIP")


# ==================================================
# DATABASE
# ==================================================

def db():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("PRAGMA busy_timeout = 20000")
    return con


def now():
    return datetime.now()


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                banned INTEGER NOT NULL DEFAULT 0,
                devices INTEGER NOT NULL DEFAULT 0,
                last_seen TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                key TEXT,
                created_at TEXT NOT NULL
            )
        """)


def log_action(user_id, action, key=""):
    with db() as con:
        con.execute(
            """INSERT INTO logs(user_id, action, key, created_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, action, key, now().isoformat())
        )


def is_admin(update):
    return (
        update.effective_user is not None
        and ADMIN_ID != 0
        and update.effective_user.id == ADMIN_ID
    )


async def deny(update):
    msg = update.effective_message
    if msg:
        await msg.reply_text("❌ ليس لديك صلاحية استخدام هذا الأمر.")


def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    )


def create_key(days):
    created = now()
    expires = created + timedelta(days=days)

    with db() as con:
        for _ in range(20):
            key = generate_key()
            try:
                con.execute(
                    """INSERT INTO keys
                       (key, created_at, expires_at)
                       VALUES (?, ?, ?)""",
                    (key, created.isoformat(), expires.isoformat())
                )
                return key, expires
            except sqlite3.IntegrityError:
                continue

    raise RuntimeError("تعذر إنشاء مفتاح فريد.")


def key_status(banned, expires_at):
    if banned:
        return "🚫 محظور"
    if now() >= datetime.fromisoformat(expires_at):
        return "⚫ منتهي"
    return "🟢 فعال"


# ==================================================
# GITHUB KEY SYNC
# ==================================================

def github_headers():
    if not GH_TOKEN:
        raise RuntimeError("GH_TOKEN غير مضبوط.")

    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ALI-VIP-BOT"
    }


def sync_github():
    """ينشر حالة المفاتيح فقط في keylist.json."""
    with SYNC_LOCK:
        with db() as con:
            rows = con.execute(
                "SELECT key, expires_at, banned FROM keys"
            ).fetchall()

        today = now().date()
        data = {}

        for key, expiry_text, banned in rows:
            expiry_dt = datetime.fromisoformat(expiry_text)
            data[key] = {
                "active": bool(
                    not banned and expiry_dt > now()
                ),
                "expiry": expiry_dt.date().isoformat()
            }

        api = (
            f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}"
            "/contents/keylist.json"
        )
        headers = github_headers()

        response = requests.get(
            api,
            headers=headers,
            params={"ref": GH_BRANCH},
            timeout=20
        )

        if response.status_code == 200:
            sha = response.json()["sha"]
        elif response.status_code == 404:
            sha = None
        else:
            raise RuntimeError(
                f"GitHub read error: {response.status_code}"
            )

        payload = {
            "message": "Update ALI VIP keys",
            "content": base64.b64encode(
                json.dumps(data, indent=2).encode("utf-8")
            ).decode("ascii"),
            "branch": GH_BRANCH
        }

        if sha:
            payload["sha"] = sha

        response = requests.put(
            api,
            headers=headers,
            json=payload,
            timeout=20
        )

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"GitHub write error: {response.status_code}"
            )

        logger.info("GitHub sync successful")


async def sync_or_report(update):
    try:
        sync_github()
        return True
    except Exception:
        logger.exception("GitHub sync failed")
        if update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ فشلت المزامنة مع GitHub. "
                "راجع GH_TOKEN وصلاحيات المستودع."
            )
        return False


# ==================================================
# MAIN MENU
# ==================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 توليد مفاتيح", callback_data="generate")],
        [
            InlineKeyboardButton("🔎 إدارة المفاتيح", callback_data="manage"),
            InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")
        ],
        [
            InlineKeyboardButton("📝 النشاط", callback_data="activity"),
            InlineKeyboardButton("🚫 المحظورة", callback_data="banned")
        ],
        [InlineKeyboardButton("📋 سجل العمليات", callback_data="logs")],
        [InlineKeyboardButton("☁️ مزامنة GitHub", callback_data="sync")],
        [InlineKeyboardButton("🌐 رابط التحقق", callback_data="config")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await deny(update)
        return

    await update.effective_message.reply_text(
        "🚀 ALI VIP BOT\n\n"
        "أهلًا بك في لوحة الإدارة.\n\n"
        "الأوامر:\n"
        "/gen 30d 5 — إنشاء 5 مفاتيح لمدة 30 يومًا\n"
        "/find KEY — البحث عن مفتاح\n"
        "/renew KEY 30d — تجديد مفتاح\n"
        "/ban KEY — حظر مفتاح\n"
        "/unban KEY — فك الحظر\n"
        "/restart KEY — تصفير بيانات الجهاز\n"
        "/sync — مزامنة GitHub\n"
        "/getconfig — رابط التحقق",
        reply_markup=main_keyboard()
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await deny(update)
        return

    await update.effective_message.reply_text(
        "🚀 ALI VIP — القائمة الرئيسية",
        reply_markup=main_keyboard()
    )


# ==================================================
# GENERATE KEYS: /gen 30d 5
# ==================================================

async def gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await deny(update)
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "الاستخدام:\n/gen 30d 5"
        )
        return

    duration = context.args[0].lower()
    if not duration.endswith("d"):
        await update.effective_message.reply_text(
            "اكتب المدة مثل 30d."
        )
        return

    try:
        days = int(duration[:-1])
        quantity = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ المدة أو العدد غير صحيح."
        )
        return

    if not 1 <= days <= 3650 or not 1 <= quantity <= 100:
        await update.effective_message.reply_text(
            "❌ المدة من 1 إلى 3650 يومًا والعدد من 1 إلى 100."
        )
        return

    created = []
    try:
        for _ in range(quantity):
            key, expiry = create_key(days)
            created.append(
                f"🔑 {key}\n📅 {expiry.strftime('%Y-%m-%d %H:%M')}"
            )
            log_action(
                update.effective_user.id,
                f"إنشاء مفتاح لمدة {days} يومًا",
                key
            )
    except Exception:
        logger.exception("Key creation failed")
        await update.effective_message.reply_text(
            "❌ حدث خطأ أثناء إنشاء المفاتيح."
        )
        return

    if not await sync_or_report(update):
        await update.effective_message.reply_text(
            "تم حفظ المفاتيح محليًا، لكن لم تتم مزامنتها. "
            "استخدم /sync بعد إصلاح الإعدادات."
        )
        return

    await update.effective_message.reply_text(
        f"✅ تم إنشاء {quantity} مفتاح\n\n"
        + "\n\n".join(created)
    )


# ==================================================
# FIND KEY
# ==================================================

async def find_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await deny(update)
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "الاستخدام:\n/find KEY"
        )
        return

    key = context.args[0].upper()
    with db() as con:
        row = con.execute(
            """SELECT key, created_at, expires_at, banned,
                      devices, last_seen
               FROM keys WHERE key = ?""",
            (key,)
        ).fetchone()

    if not row:
        await update.effective_message.reply_text(
            "❌ المفتاح غير موجود."
        )
        return

    key, created, expiry, banned, devices, last_seen = row
    await update.effective_message.reply_text(
        f"🔎 تفاصيل المفتاح\n\n"
        f"🔑 {key}\n"
        f"📅 الإنشاء: {created}\n"
        f"⏳ الانتهاء: {expiry}\n"
        f"📱 عداد الأجهزة: {devices}\n"
        f"📡 آخر اتصال: {last_seen or 'لا يوجد'}\n"
        f"📌 الحالة: {key_status(banned, expiry)}"
    )


# ==================================================
# RENEW KEY: /renew KEY 30d
# ==================================================

async def renew_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await deny(update)
        return

    if len(context.args) != 2 or not context.args[1].lower().endswith("d"):
        await update.effective_message.reply_text(
            "الاستخدام:\n/renew KEY 30d"
        )
        return

    key = context.args[0].upper()

    try:
        days = int(context.args[1][:-1])
    except ValueError:
        days = 0

    if not 1 <= days <= 3650:
        await update.effective_message.reply_text("❌ مدة غير صحيحة.")
        return

    with db() as con:
        row = con.execute(
            "SELECT expires_at FROM keys WHERE key = ?", (key,)
        ).fetchone()

        if not row:
            await update.effective_message.reply_text(
                "❌ المفتاح غير موجود."
            )
            return

        old_expiry = datetime.fromisoformat(row[0])
        new_expiry = max(old_expiry, now()) + timedelta(days=days)

        con.execute(
            """UPDATE keys SET expires_at = ?, banned = 0
               WHERE key = ?""",
            (new_expiry.isoformat(), key)
        )

    log_action(update.effective_user.id, "تجديد مفتاح", key)

    if await sync_or_report(update):
        await update.effective_message.reply_text(
            f"✅ تم التجديد\n🔑 {key}\n"
            f"📅 {new_expiry.strftime('%Y-%m-%d %H:%M')}"
        )


# ==================================================
# BAN / UNBAN
# ==================================================

async def change_ban(update, context, banned):
    if not is_admin(update):
        await deny(update)
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "الاستخدام:\n/ban KEY\n/unban KEY"
        )
        return

    key = context.args[0].upper()
    with db() as con:
        cur = con.execute(
            "UPDATE keys SET banned = ? WHERE key = ?",
            (int(banned), key)
        )
        if cur.rowcount == 0:
            await update.effective_message.reply_text(
                "❌ المفتاح غير موجود."
            )
            return

    action = "حظر مفتاح" if banned else "فك حظر مفتاح"
    log_action(update.effective_user.id, action, key)

    if await sync_or_report(update):
        await update.effective_message.reply_text(
            f"{'🚫 تم حظر' if banned else '✅ تم فك الحظر عن'} {key}"
        )


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await change_ban(update, context, True)


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await change_ban(update, context, False)


# ==================================================
# RESET DEVICE COUNTER
# ==================================================

async def restart_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await deny(update)
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "الاستخدام:\n/restart KEY"
        )
        return

    key = context.args[0].upper()
    with db() as con:
        cur = con.execute(
            """UPDATE keys SET devices = 0, last_seen = NULL
               WHERE key = ?""",
            (key,)
        )
        if cur.rowcount == 0:
            await update.effective_message.reply_text(
                "❌ المفتاح غير موجود."
            )
            return

    log_action(update.effective_user.id, "تصفير عداد الأجهزة", key)
    await update.effective_message.reply_text(
        f"🔄 تم تصفير عداد الأجهزة للمفتاح {key}"
    )


# ==================================================
# BUTTON PAGES
# ==================================================

async def button_handler(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("غير مسموح.", show_alert=True)
        return

    await query.answer()
    action = query.data

    if action == "generate":
        text = "🔑 توليد مفاتيح\n\nمثال:\n/gen 30d 5"

    elif action == "manage":
        text = (
            "🔎 إدارة المفاتيح\n\n"
            "/find KEY\n/renew KEY 30d\n"
            "/ban KEY\n/unban KEY\n/restart KEY"
        )

    elif action == "stats":
        with db() as con:
            total = con.execute(
                "SELECT COUNT(*) FROM keys"
            ).fetchone()[0]
            banned_count = con.execute(
                "SELECT COUNT(*) FROM keys WHERE banned=1"
            ).fetchone()[0]
            rows = con.execute(
                "SELECT expires_at FROM keys WHERE banned=0"
            ).fetchall()

        active = sum(
            datetime.fromisoformat(r[0]) > now() for r in rows
        )
        expired = total - banned_count - active

        text = (
            "📊 إحصائيات ALI VIP\n\n"
            f"🔑 الإجمالي: {total}\n"
            f"🟢 الفعالة: {active}\n"
            f"⚫ المنتهية: {max(0, expired)}\n"
            f"🚫 المحظورة: {banned_count}"
        )

    elif action == "activity":
        start_day = now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        with db() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM logs WHERE created_at >= ?",
                (start_day,)
            ).fetchone()[0]
        text = f"📝 نشاط اليوم\n\nعدد العمليات: {count}"

    elif action == "banned":
        with db() as con:
            rows = con.execute(
                "SELECT key FROM keys WHERE banned=1 ORDER BY id DESC LIMIT 30"
            ).fetchall()
        text = "🚫 المفاتيح المحظورة:\n\n" + (
            "\n".join(r[0] for r in rows) if rows else "لا توجد مفاتيح محظورة."
        )

    elif action == "logs":
        with db() as con:
            rows = con.execute(
                """SELECT action, key, created_at FROM logs
                   ORDER BY id DESC LIMIT 15"""
            ).fetchall()
        text = "📋 سجل العمليات:\n\n" + (
            "\n\n".join(
                f"{a}\n🔑 {k or '-'}\n🕒 {t}" for a, k, t in rows
            ) if rows else "لا توجد عمليات مسجلة."
        )

    elif action == "sync":
        try:
            sync_github()
            text = "✅ تمت مزامنة GitHub بنجاح."
        except Exception:
            logger.exception("GitHub sync failed")
            text = "❌ فشلت المزامنة. راجع GH_TOKEN وصلاحيات GitHub."

    elif action == "config":
        text = f"🌐 رابط التحقق:\n{WORKER_URL}\n\nطريقة التحقق: POST /verify"

    else:
        text = "اختيار غير معروف."

    await query.edit_message_text(
        text[:4000],
        reply_markup=main_keyboard()
    )


# ==================================================
# SYNC / CONFIG COMMANDS
# ==================================================

async def sync_command(update, context):
    if not is_admin(update):
        await deny(update)
        return

    if await sync_or_report(update):
        await update.effective_message.reply_text(
            "✅ تمت مزامنة المفاتيح مع GitHub."
        )


async def getconfig(update, context):
    if not is_admin(update):
        await deny(update)
        return

    await update.effective_message.reply_text(
        f"🌐 رابط التحقق:\n{WORKER_URL}"
    )


# ==================================================
# RUN BOT
# ==================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("أضف BOT_TOKEN في متغيرات الاستضافة.")
    if not ADMIN_ID:
        raise RuntimeError("أضف ADMIN_ID في متغيرات الاستضافة.")
    if not GH_TOKEN:
        raise RuntimeError("أضف GH_TOKEN في متغيرات الاستضافة.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("gen", gen))
    app.add_handler(CommandHandler("find", find_key))
    app.add_handler(CommandHandler("renew", renew_key))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("restart", restart_key))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("getconfig", getconfig))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("ALI VIP BOT is starting")
    app.run_polling()


if __name__ == "__main__":
    main()
    