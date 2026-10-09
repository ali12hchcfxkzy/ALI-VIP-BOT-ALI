
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

# =========================================================
# إعدادات البوت
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = 8821125770

GH_OWNER = "ali12hchcfxkzy"
GH_REPO = "ALI-VIP-BOT-ALI"
GH_BRANCH = "main"
GH_TOKEN = os.getenv("GH_TOKEN", "")

KEY_FILE = "keylist.json"
WORKER_URL = os.getenv(
    "WORKER_URL",
    "https://hidden-sound-6927.dyskwrddyskwrd504.workers.dev/verify"
)

DB = "ali_vip.db"
SYNC_LOCK = Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("ALI-VIP")


# =========================================================
# قاعدة البيانات
# =========================================================

def db():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("PRAGMA busy_timeout = 20000")
    return con


def now():
    return datetime.now()


def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            banned INTEGER DEFAULT 0,
            devices INTEGER DEFAULT 0,
            last_seen TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            key TEXT,
            created_at TEXT
        )
    """)

    con.commit()
    con.close()


# =========================================================
# أدوات مساعدة
# =========================================================

def log_action(user_id, action, key=""):
    con = db()
    con.execute(
        """
        INSERT INTO logs(user_id, action, key, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, action, key, now().isoformat())
    )
    con.commit()
    con.close()


def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    )


def create_key(days):
    con = db()
    try:
        for _ in range(10):
            key = generate_key()
            created = now()
            expires = created + timedelta(days=days)

            try:
                con.execute(
                    """
                    INSERT INTO keys
                    (key, created_at, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        key,
                        created.isoformat(),
                        expires.isoformat()
                    )
                )
                con.commit()
                return key, expires
            except sqlite3.IntegrityError:
                continue

        raise RuntimeError("تعذر إنشاء مفتاح فريد.")
    finally:
        con.close()


def is_admin(update):
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


def format_status(banned, expires_at):
    if banned:
        return "🚫 محظور"

    expiry = datetime.fromisoformat(expires_at)

    if now() >= expiry:
        return "⚫ منتهي"

    return "🟢 فعال"


# =========================================================
# مزامنة GitHub
# =========================================================

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
    """
    ينشر حالة المفاتيح في keylist.json.
    لا يرسل قاعدة البيانات أو السجلات إلى GitHub.
    """
    with SYNC_LOCK:
        con = db()
        try:
            rows = con.execute(
                "SELECT key, expires_at, banned FROM keys"
            ).fetchall()
        finally:
            con.close()

        data = {}

        for key, expires_at, banned in rows:
            expiry_dt = datetime.fromisoformat(expires_at)
            expiry_date = expiry_dt.date()

            data[key] = {
                "active": bool(
                    not banned and now() < expiry_dt
                ),
                "expiry": expiry_date.isoformat()
            }

        api = (
            f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}"
            f"/contents/{KEY_FILE}"
        )

        headers = github_headers()

        current = requests.get(
            api,
            headers=headers,
            params={"ref": GH_BRANCH},
            timeout=20
        )

        if current.status_code == 200:
            sha = current.json()["sha"]
        elif current.status_code == 404:
            # ينشئ الملف إذا لم يكن موجودًا.
            sha = None
        else:
            raise RuntimeError(
                f"GitHub read failed: {current.status_code}"
            )

        payload = {
            "message": "Update ALI VIP keys",
            "content": base64.b64encode(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2
                ).encode("utf-8")
            ).decode("ascii"),
            "branch": GH_BRANCH
        }

        if sha:
            payload["sha"] = sha

        result = requests.put(
            api,
            headers=headers,
            json=payload,
            timeout=20
        )

        if result.status_code not in (200, 201):
            raise RuntimeError(
                f"GitHub write failed: {result.status_code}"
            )

        logger.info("GitHub key list synchronized.")


async def sync_or_report(update):
    try:
        sync_github()
        return True
    except Exception:
        logger.exception("GitHub synchronization failed.")
        await update.effective_message.reply_text(
            "⚠️ فشلت مزامنة GitHub. "
            "راجع GH_TOKEN وصلاحيات المستودع ثم استخدم /sync."
        )
        return False


# =========================================================
# لوحة الإدارة
# =========================================================

def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                "🔑 توليد مفاتيح جديدة",
                callback_data="generate"
            )
        ],
        [
            InlineKeyboardButton(
                "🔑 إدارة المفاتيح",
                callback_data="manage"
            ),
            InlineKeyboardButton(
                "📊 الإحصائيات",
                callback_data="stats"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 المتصلين الآن",
                callback_data="online"
            ),
            InlineKeyboardButton(
                "📝 النشاط اليومي",
                callback_data="activity"
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 المفاتيح المحظورة",
                callback_data="banned"
            ),
            InlineKeyboardButton(
                "📋 سجل العمليات",
                callback_data="logs"
            )
        ],
        [
            InlineKeyboardButton(
                "⚠️ المحاولات الفاشلة",
                callback_data="failed"
            ),
            InlineKeyboardButton(
                "ℹ️ معلومات النظام",
                callback_data="system"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "❌ ليس لديك صلاحية استخدام لوحة الإدارة."
        )
        return

    await update.effective_message.reply_text(
        "🚀 ALI VIP\n\n"
        "👋 مرحبًا بك في لوحة الإدارة\n\n"
        "⚡ الأوامر:\n"
        "/menu — القائمة الرئيسية\n"
        "/find KEY — تفاصيل مفتاح\n"
        "/gen 30d 5 — إنشاء 5 مفاتيح لمدة 30 يومًا\n"
        "/renew KEY 30d — تجديد مفتاح\n"
        "/restart KEY — تصفير عداد الأجهزة\n"
        "/ban KEY — حظر مفتاح\n"
        "/unban KEY — فك الحظر\n"
        "/getconfig — رابط التحقق\n"
        "/sync — مزامنة GitHub",
        reply_markup=main_keyboard()
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    await update.effective_message.reply_text(
        "🚀 ALI VIP\n\nاختر العملية:",
        reply_markup=main_keyboard()
    )


# =========================================================
# توليد المفاتيح: /gen 30d 5
# =========================================================

async def gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "الاستخدام الصحيح:\n/gen 30d 5"
        )
        return

    duration = context.args[0].lower()
    quantity_text = context.args[1]

    if not duration.endswith("d"):
        await update.effective_message.reply_text(
            "❌ اكتب المدة بهذا الشكل: 30d"
        )
        return

    try:
        days = int(duration[:-1])
        quantity = int(quantity_text)
    except ValueError:
        await update.effective_message.reply_text(
            "❌ المدة أو العدد غير صحيح."
        )
        return

    if not 1 <= days <= 3650:
        await update.effective_message.reply_text(
            "❌ المدة يجب أن تكون من يوم إلى 3650 يومًا."
        )
        return

    if not 1 <= quantity <= 100:
        await update.effective_message.reply_text(
            "❌ العدد يجب أن يكون بين 1 و100."
        )
        return

    result = []

    try:
        for _ in range(quantity):
            key, expires = create_key(days)

            result.append(
                f"🔑 {key}\n"
                f"📅 {expires.strftime('%Y-%m-%d %H:%M')}"
            )

            log_action(
                update.effective_user.id,
                f"إنشاء مفتاح لمدة {days} يوم",
                key
            )
    except Exception:
        logger.exception("Key generation failed.")
        await update.effective_message.reply_text(
            "❌ حدث خطأ أثناء إنشاء المفاتيح."
        )
        return

    if not await sync_or_report(update):
        return

    await update.effective_message.reply_text(
        f"✅ تم إنشاء {quantity} مفتاح\n"
        f"⏳ المدة: {days} يوم\n\n"
        + "\n\n".join(result)
    )


# =========================================================
# تجديد مفتاح: /renew KEY 30d
# =========================================================

async def renew_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "الاستخدام:\n/renew KEY 30d"
        )
        return

    key = context.args[0].upper()
    duration = context.args[1].lower()

    if not duration.endswith("d"):
        await update.effective_message.reply_text(
            "اكتب المدة مثل 30d."
        )
        return

    try:
        days = int(duration[:-1])
    except ValueError:
        await update.effective_message.reply_text("المدة غير صحيحة.")
        return

    if not 1 <= days <= 3650:
        await update.effective_message.reply_text("المدة غير مسموحة.")
        return

    con = db()
    try:
        row = con.execute(
            "SELECT expires_at FROM keys WHERE key = ?",
            (key,)
        ).fetchone()

        if not row:
            await update.effective_message.reply_text(
                "❌ المفتاح غير موجود."
            )
            return

        old_expiry = datetime.fromisoformat(row[0])
        base = max(old_expiry, now())
        new_expiry = base + timedelta(days=days)

        con.execute(
            "UPDATE keys SET expires_at = ?, banned = 0 WHERE key = ?",
            (new_expiry.isoformat(), key)
        )
        con.commit()
    finally:
        con.close()

    log_action(
        update.effective_user.id,
        f"تجديد مفتاح لمدة {days} يوم",
        key
    )

    if not await sync_or_report(update):
        return

    await update.effective_message.reply_text(
        f"✅ تم تجديد المفتاح:\n{key}\n"
        f"📅 الانتهاء: {new_expiry.strftime('%Y-%m-%d %H:%M')}"
    )


# =========================================================
# رابط التحقق: /getconfig
# =========================================================

async def getconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    await update.effective_message.reply_text(
        f"🌐 رابط التحقق:\n{WORKER_URL}\n\n"
        "هذا endpoint للتحقق عبر POST، "
        "وليس رابطًا لتحميل قائمة المفاتيح."
    )


# =========================================================
# البحث عن مفتاح: /find KEY
# =========================================================

async def find_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "الاستخدام:\n/find KEY"
        )
        return

    key = context.args[0].upper()
    con = db()

    try:
        row = con.execute(
            """
            SELECT key, created_at, expires_at,
                   banned, devices, last_seen
            FROM keys
            WHERE key = ?
            """,
            (key,)
        ).fetchone()
    finally:
        con.close()

    if not row:
        await update.effective_message.reply_text(
            "❌ المفتاح غير موجود."
        )
        return

    key, created_at, expires_at, banned, devices, last_seen = row

    await update.effective_message.reply_text(
        f"🔎 تفاصيل المفتاح\n\n"
        f"🔑 المفتاح: {key}\n"
        f"📅 الإنشاء: {created_at}\n"
        f"⏳ الانتهاء: {expires_at}\n"
        f"📱 عداد الأجهزة: {devices}\n"
        f"📡 آخر اتصال: {last_seen or 'لا يوجد'}\n"
        f"📌 الحالة: {format_status(banned, expires_at)}"
    )


# =========================================================
# الحظر وفك الحظر
# =========================================================

async def change_ban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    banned: bool
):
    if not is_admin(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "الاستخدام:\n/ban KEY\nأو\n/unban KEY"
        )
        return

    key = context.args[0].upper()
    con = db()

    try:
        cur = con.execute(
            "UPDATE keys SET banned = ? WHERE key = ?",
            (int(banned), key)
        )

        if cur.rowcount == 0:
            await update.effective_message.reply_text(
                "❌ المفتاح غير موجود."
            )
            return

        con.commit()
    finally:
        con.close()

    log_action(
        update.effective_user.id,
        "حظر مفتاح" if banned else "فك حظر مفتاح",
        key
    )

    if not await sync_or_report(update):
        return

    message = "🚫 تم حظر المفتاح" if banned else "✅ تم فك الحظر"
    await update.effective_message.reply_text(f"{message}:\n{key}")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await change_ban(update, context, True)


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await change_ban(update, context, False)


# =========================================================
# تصفير عداد الأجهزة: /restart KEY
# =========================================================

async def restart_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "الاستخدام:\n/restart KEY"
        )
        return

    key = context.args[0].upper()
    con = db()

    try:
        cur = con.execute(
            """
            UPDATE keys
            SET devices = 0, last_seen = NULL
            WHERE key = ?
            """,
            (key,)
        )

        if cur.rowcount == 0:
            await update.effective_message.reply_text(
                "❌ المفتاح غير موجود."
            )
            return

        con.commit()
    finally:
        con.close()

    log_action(
        update.effective_user.id,
        "تصفير عداد الأجهزة",
        key
    )

    await update.effective_message.reply_text(
        f"🔄 تم تصفير عداد الأجهزة للمفتاح:\n{key}"
    )


# =========================================================
# الإحصائيات
# =========================================================

async def stats_button(query):
    con = db()

    try:
        total = con.execute(
            "SELECT COUNT(*) FROM keys"
        ).fetchone()[0]

        banned = con.execute(
            "SELECT COUNT(*) FROM keys WHERE banned = 1"
        ).fetchone()[0]

        rows = con.execute(
            "SELECT expires_at FROM keys WHERE banned = 0"
        ).fetchall()
    finally:
        con.close()

    current = now()
    active = 0

    for (expiry,) in rows:
        if datetime.fromisoformat(expiry) > current:
            active += 1

    expired = total - banned - active

    await query.edit_message_text(
        f"📊 إحصائيات ALI VIP\n\n"
        f"🔑 إجمالي المفاتيح: {total}\n"
        f"🟢 الفعالة: {active}\n"
        f"⚫ المنتهية: {max(expired, 0)}\n"
        f"🚫 المحظورة: {banned}",
        reply_markup=main_keyboard()
    )


async def online_button(query):
    await query.edit_message_text(
        "👥 المتصلون الآن\n\n"
        "لا توجد بيانات اتصال حقيقية حتى الآن.\n"
        "يلزم أن يرسل التطبيق وقت آخر اتصال إلى API "
        "حتى تظهر أرقام فعلية.",
        reply_markup=main_keyboard()
    )


async def activity_button(query):
    start_day = now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    con = db()
    try:
        count = con.execute(
            "SELECT COUNT(*) FROM logs WHERE created_at >= ?",
            (start_day.isoformat(),)
        ).fetchone()[0]
    finally:
        con.close()

    await query.edit_message_text(
        f"📝 النشاط اليومي\n\n"
        f"📅 {now().strftime('%Y-%m-%d')}\n"
        f"⚡ عدد العمليات: {count}",
        reply_markup=main_keyboard()
    )


async def banned_button(query):
    con = db()
    try:
        rows = con.execute(
            """
            SELECT key FROM keys
            WHERE banned = 1
            ORDER BY id DESC LIMIT 30
            """
        ).fetchall()
    finally:
        con.close()

    text = "🚫 المفاتيح المحظورة:\n\n"

    if rows:
        text += "\n".join(row[0] for row in rows)
    else:
        text = "🚫 لا توجد مفاتيح محظورة."

    await query.edit_message_text(
        text,
        reply_markup=main_keyboard()
    )


async def logs_button(query):
    con = db()
    try:
        rows = con.execute(
            """
            SELECT action, key, created_at
            FROM logs
            ORDER BY id DESC LIMIT 20
            """
        ).fetchall()
    finally:
        con.close()

    if not rows:
        text = "📋 لا توجد عمليات مسجلة."
    else:
        entries = []
        for action, key, created_at in rows:
            entries.append(
                f"⚡ {action}\n"
                f"🔑 {key or '-'}\n"
                f"🕐 {created_at}"
            )
        text = "📋 آخر العمليات:\n\n" + "\n\n".join(entries)
