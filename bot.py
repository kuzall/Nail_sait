import os
import sys
import asyncio
import sqlite3
import re
from datetime import datetime

# Windows console encoding safeguard
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)
from aiogram.client.default import DefaultBotProperties

# ==================== CONFIGURATION ====================
from config import BOT_TOKEN, ADMIN_IDS, DB_PATH, WEB_APP_URL

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ==================== DATABASE HELPERS ====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.create_function("clean_phone", 1, lambda s: re.sub(r'\D', '', str(s or ''))[-10:])
    return conn

def format_phone_link(phone: str) -> str:
    digits = re.sub(r'\D', '', str(phone))
    if digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits

def get_client_booking_actions_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить статус", callback_data=f"cstatus:{booking_id}")
        ],
        [
            InlineKeyboardButton(text="💬 Написать мастеру", url="https://t.me/kuzall"),
            InlineKeyboardButton(text="📍 Как добраться", url="https://yandex.ru/maps/-/CDT65-0A")
        ]
    ])

def render_client_booking_card(b: dict) -> str:
    status_icon = {
        "new": "🟡 В обработке (Ожидает подтверждения)",
        "confirmed": "🟢 Подтверждена! Мастер ждёт вас ✨",
        "completed": "✅ Процедура успешно выполнена",
        "cancelled": "🔴 Запись отменена"
    }.get(b.get("status", "new"), b.get("status", "new"))

    price_val = b.get("price", 0)
    discounted = int(round(price_val * 0.85)) if price_val else 0
    price_str = f"{discounted:,} ₽ (со скидкой 15%)".replace(",", " ") if discounted else "По прайсу"

    return (
        f"🌸 <b>Ваша запись в студию Ангелины:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎫 <b>Номер брони:</b> <code>№ {b['id']}</code>\n"
        f"💅 <b>Услуга:</b> {b['service']}\n"
        f"🕒 <b>Дата и время:</b> <b>{b['date']}</b>\n"
        f"💵 <b>Стоимость:</b> <b>{price_str}</b>\n"
        f"📍 <b>Адрес:</b> г. Вологда, Центр (ул. Мира / Батюшкова)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Статус: <b>{status_icon}</b>"
    )

# ==================== KEYBOARDS ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="📅 Записи на сегодня"),
            KeyboardButton(text="🗓 Записи на завтра")
        ],
        [
            KeyboardButton(text="📊 Статистика и доход"),
            KeyboardButton(text="📱 Открыть CRM", web_app=WebAppInfo(url=WEB_APP_URL))
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_client_keyboard() -> ReplyKeyboardMarkup:
    site_url = WEB_APP_URL.replace("/admin", "").rstrip("/")
    if not site_url.startswith("http"):
        site_url = "https://138.249.149.151.sslip.io"
    booking_url = f"{site_url}/#booking"
    kb = [
        [
            KeyboardButton(text="💅 Записаться на визит", web_app=WebAppInfo(url=booking_url))
        ],
        [
            KeyboardButton(text="📋 Моя запись"),
            KeyboardButton(text="📍 Адрес и контакты")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_schedule_picker_kb(rows: list) -> InlineKeyboardMarkup:
    buttons = []
    for idx, b in enumerate(rows, 1):
        time_clean = b['date'].replace("Сегодня, ", "").replace("Завтра, ", "")
        buttons.append([
            InlineKeyboardButton(
                text=f"👉 {idx}. {b['name']} ({time_clean})",
                callback_data=f"sel:{b['id']}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_booking_actions_kb(booking_id: str, phone: str, status: str = "new") -> InlineKeyboardMarkup:
    raw_phone = format_phone_link(phone)
    buttons = []
    
    # 1. Row: Status management
    if status == "new":
        buttons.append([
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"conf:{booking_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"canc:{booking_id}")
        ])
    elif status == "confirmed":
        buttons.append([
            InlineKeyboardButton(text="🎉 Завершить визит", callback_data=f"done:{booking_id}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"canc:{booking_id}")
        ])
    elif status == "cancelled":
        buttons.append([
            InlineKeyboardButton(text="🔄 Вернуть в новые", callback_data=f"conf:{booking_id}")
        ])

    # 2. Row: Messenger MAX
    buttons.append([
        InlineKeyboardButton(text="🟣 Написать в MAX", callback_data=f"max:{booking_id}")
    ])

    # 3. Row: Note & Reschedule (Заметка & Замена времени)
    buttons.append([
        InlineKeyboardButton(text="📝 Заметка", callback_data=f"note:{booking_id}"),
        InlineKeyboardButton(text="🔄 Замена времени", callback_data=f"resched:{booking_id}")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def render_schedule_summary(title: str, rows: list) -> str:
    total_sum = sum(r.get("price", 0) for r in rows if r.get("status") != "cancelled")
    sum_str = f"{total_sum:,} ₽".replace(",", " ")

    lines = [
        f"📅 <b>{title} ({len(rows)} чел.)</b>",
        f"💰 Ожидаемая выручка: <b>{sum_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
    ]

    for idx, b in enumerate(rows, 1):
        status_icon = {
            "new": "🟡 Новая",
            "confirmed": "🟢 Подтверждена",
            "completed": "✅ Завершена",
            "cancelled": "🔴 Отменена"
        }.get(b["status"], b["status"])

        price_val = b.get("price", 0)
        price_str = f"{price_val:,} ₽".replace(",", " ") if price_val else "По прайсу"
        
        # Prevent duplicate price text if service already includes it
        svc = b['service']
        svc_display = svc if (price_str and price_str in svc) else f"{svc} ({price_str})"

        lines.append(
            f"<b>{idx}. {b['name']}</b>\n"
            f"🕒 Время: <b>{b['date']}</b>\n"
            f"📱 Телефон: <code>{b['phone']}</code>\n"
            f"💅 Услуга: {svc_display}\n"
            f"Статус: <b>{status_icon}</b>"
        )
        if idx < len(rows):
            lines.append("────────────────────")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>👇 Нажмите на кнопку нужного клиента ниже, чтобы открыть действия:</i>")
    return "\n".join(lines)

def render_booking_card(b: dict) -> str:
    status_emoji = {
        "new": "🟡 Новая заявка",
        "confirmed": "🟢 Подтверждена",
        "completed": "✅ Завершена",
        "cancelled": "🔴 Отменена"
    }.get(b["status"], b["status"])

    price_val = b.get("price", 0)
    price_str = f"{price_val:,} ₽".replace(",", " ") if price_val else "По прайсу"

    return (
        f"🌸 <b>Запись клиента: {b['name']}</b> (№ {b['id']})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Дата и время:</b> <b>{b['date']}</b>\n"
        f"📱 <b>Телефон:</b> <code>{b['phone']}</code>\n"
        f"💅 <b>Услуга:</b> {b['service']}\n"
        f"💵 <b>Сумма:</b> <b>{price_str}</b>\n"
        f"💬 <b>Заметка:</b> <i>{b.get('notes', 'Заявка с сайта') or '—'}</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Статус: <b>{status_emoji}</b>"
    )

# ==================== HANDLERS ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    name = message.from_user.first_name or "Клиент"
    
    # Check deep link e.g. /start BK-105 or /start 105
    text_parts = message.text.split() if message.text else []
    deep_code = text_parts[1].strip() if len(text_parts) > 1 else ""

    if deep_code:
        clean_code = deep_code.upper()
        if not clean_code.startswith("BK-") and clean_code.isdigit():
            clean_code = f"BK-{clean_code}"

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bookings WHERE id = ? OR id = ?", (clean_code, deep_code))
        row = cursor.fetchone()
        if row:
            b = dict(row)
            cursor.execute("UPDATE bookings SET client_chat_id = ?, telegram_username = ? WHERE id = ?",
                           (user_id, message.from_user.username or "", b["id"]))
            
            # Auto-bind any other active bookings for this client's phone
            if b.get("phone"):
                cursor.execute("""
                    UPDATE bookings 
                    SET client_chat_id = ?, telegram_username = ? 
                    WHERE clean_phone(phone) = clean_phone(?) AND status != 'cancelled'
                """, (user_id, message.from_user.username or "", b["phone"]))
            conn.commit()
            conn.close()

            await message.answer(
                f"🌸 <b>Здравствуйте, {name}!</b>\n\n"
                f"✅ Ваша запись <b>№ {b['id']}</b> успешно подключена к Telegram-боту!\n"
                f"🔔 <i>Как только мастер подтвердит окошко или изменит время, вы сразу получите уведомление в этот чат.</i>",
                reply_markup=get_client_keyboard()
            )

            await message.answer(
                render_client_booking_card(b),
                reply_markup=get_client_booking_actions_kb(b["id"])
            )

            # Alert admin
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"📱 Клиент <b>{name}</b> подключил Telegram к записи <b>№ {b['id']}</b> ({b['name']}). Теперь бот уведомит его при подтверждении."
                    )
                except Exception:
                    pass
            return
        conn.close()

    if user_id in ADMIN_IDS:
        await message.answer(
            f"👋 <b>Привет, {name}!</b>\n\n"
            f"Вы авторизованы как <b>Администратор студии Ангелины</b> ✨\n\n"
            f"Сюда мгновенно приходят все новые записи с сайта с возможностью "
            f"подтверждать их в один клик.\n\n"
            f"Используйте кнопки меню внизу для быстрого доступа к расписанию:",
            reply_markup=get_main_keyboard()
        )
    else:
        site_url = WEB_APP_URL.replace("/admin", "").rstrip("/")
        if not site_url.startswith("http"):
            site_url = "https://138.249.149.151.sslip.io"
        await message.answer(
            f"Здравствуйте, {name}! 🌸\n\n"
            f"Добро пожаловать в студию ногтевого сервиса <b>Ангелины Осиной</b> в Вологде.\n\n"
            f"Здесь вы можете записаться на процедуру, а также отслеживать статус своих визитов.\n\n"
            f"<i>Выберите нужное действие в меню ниже:</i>",
            reply_markup=get_client_keyboard()
        )

@dp.message(F.text == "📋 Моя запись")
async def cmd_my_booking(message: Message):
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM bookings 
        WHERE client_chat_id = ? 
        ORDER BY created_at DESC LIMIT 3
    """, (user_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        await message.answer(
            "📋 <b>У вас пока нет активных записей, привязанных к этому Telegram.</b>\n\n"
            "💬 Чтобы привязать запись и отслеживать статус, <b>отправьте в чат номер вашей брони</b> "
            "(например: <code>BK-104</code>) или <b>номер телефона</b>, указанный при записи.\n\n"
            "Либо нажмите «💅 Записаться на визит», чтобы оформить новую запись со скидкой 15%!",
            reply_markup=get_client_keyboard()
        )
        return

    await message.answer(
        f"🌸 <b>Ваши активные записи ({len(rows)}):</b>\n"
        f"<i>Нажмите «🔄 Обновить статус» на карточке для проверки в реальном времени.</i>",
        reply_markup=get_client_keyboard()
    )
    for b in rows:
        await message.answer(
            render_client_booking_card(b),
            reply_markup=get_client_booking_actions_kb(b["id"])
        )

@dp.message(F.text == "📍 Адрес и контакты")
async def cmd_contacts(message: Message):
    await message.answer(
        "🌸 <b>Студия ногтевого сервиса Ангелины Осиной</b>\n\n"
        "📍 <b>Адрес:</b> г. Вологда, Центр (ул. Мира / Батюшкова)\n"
        "🕒 <b>График работы:</b> Ежедневно: 09:00 — 21:00\n"
        "📱 <b>Телефон / Мессенджер MAX:</b> <code>+7 (992) 282-11-58</code>\n"
        "✈️ <b>Telegram мастера:</b> @kuzall\n\n"
        "<i>Уютная студия в центре Вологды, стерильные инструменты и авторский уход.</i>",
        reply_markup=get_client_keyboard()
    )

@dp.message(F.text == "📅 Записи на сегодня")
@dp.message(Command("today"))
async def cmd_today(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM bookings 
        WHERE date LIKE '%сегодня%' OR date LIKE '%Сегодня%'
        ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        await message.answer(
            "📅 <b>На сегодня записей нет</b>\n"
            "Все свободные окна доступны для клиентов.",
            reply_markup=get_main_keyboard()
        )
        return

    # Clean unified schedule message with one-click selector buttons
    await message.answer(
        render_schedule_summary("Записи на сегодня", rows),
        reply_markup=get_schedule_picker_kb(rows)
    )

@dp.message(F.text == "🗓 Записи на завтра")
@dp.message(Command("tomorrow"))
async def cmd_tomorrow(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM bookings 
        WHERE date LIKE '%завтра%' OR date LIKE '%Завтра%'
        ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        await message.answer(
            "🗓 <b>На завтра записей пока нет</b>.",
            reply_markup=get_main_keyboard()
        )
        return

    # Clean unified schedule message with one-click selector buttons
    await message.answer(
        render_schedule_summary("Записи на завтра", rows),
        reply_markup=get_schedule_picker_kb(rows)
    )

@dp.message(F.text == "📊 Статистика и доход")
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total FROM bookings")
    total = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as cnt FROM bookings WHERE status = 'new'")
    new_cnt = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM bookings WHERE status = 'confirmed'")
    conf_cnt = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM bookings WHERE status = 'completed'")
    comp_cnt = cursor.fetchone()["cnt"]

    cursor.execute("SELECT SUM(price) as rev FROM bookings WHERE status IN ('confirmed', 'completed')")
    rev = cursor.fetchone()["rev"] or 0

    conn.close()

    rev_str = f"{rev:,} ₽".replace(",", " ")

    text = (
        f"📊 <b>Общая статистика студии:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📁 Всего записей в базе: <b>{total}</b>\n"
        f"🟡 Новых заявок: <b>{new_cnt}</b>\n"
        f"🟢 Подтверждённых: <b>{conf_cnt}</b>\n"
        f"✅ Выполненных визитов: <b>{comp_cnt}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Выручка (подтверждено + завершено):</b>\n"
        f"👉 <b>{rev_str}</b>\n\n"
        f"<i>Открыть полную таблицу и календарь можно по кнопке «📱 Открыть CRM»</i>"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

# ==================== SELECTION & ACTIONS ====================
@dp.callback_query(F.data.startswith("sel:"))
async def on_select_booking(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        b = dict(row)
        await callback.answer(f"Клиент: {b['name']}")
        await callback.message.answer(
            render_booking_card(b),
            reply_markup=get_booking_actions_kb(b["id"], b["phone"], b["status"])
        )
    else:
        await callback.answer("Запись не найдена", show_alert=True)

@dp.callback_query(F.data.startswith("max:"))
async def on_max_click(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        b = dict(row)
        raw_phone = format_phone_link(b["phone"])
        await callback.answer(f"📱 MAX: +{raw_phone}", show_alert=True)
        await callback.message.answer(
            f"🟣 <b>Связь с клиентом в мессенджере MAX:</b>\n"
            f"👤 Клиент: <b>{b['name']}</b>\n"
            f"📱 Телефон: <code>+{raw_phone}</code>\n\n"
            f"<i>Скопируйте номер выше или найдите контакт в приложении MAX для переписки.</i>"
        )
    else:
        await callback.answer("Запись не найдена", show_alert=True)

@dp.callback_query(F.data.startswith("note:"))
async def on_note_click(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        b = dict(row)
        current_note = b.get('notes') or 'Заметок пока нет'
        await callback.answer()
        await callback.message.answer(
            f"📝 <b>Заметка к записи № {b['id']} ({b['name']}):</b>\n\n"
            f"«<i>{current_note}</i>»\n\n"
            f"<i>Для редактирования заметки откройте «📱 Открыть CRM» или измените на сайте.</i>"
        )
    else:
        await callback.answer("Запись не найдена", show_alert=True)

@dp.callback_query(F.data.startswith("resched:"))
async def on_resched_click(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        b = dict(row)
        buttons = [
            [
                InlineKeyboardButton(text="🕒 Перенести на +30 мин", callback_data=f"shift:{booking_id}:30"),
                InlineKeyboardButton(text="🕒 Перенести на +1 час", callback_data=f"shift:{booking_id}:60")
            ],
            [
                InlineKeyboardButton(text="🗓 Перенести на Завтра", callback_data=f"shift:{booking_id}:tomorrow")
            ],
            [
                InlineKeyboardButton(text="🔙 Назад к карточке", callback_data=f"sel:{booking_id}")
            ]
        ]
        await callback.answer()
        await callback.message.answer(
            f"🔄 <b>Замена времени для {b['name']}:</b>\n"
            f"Текущее время: <b>{b['date']}</b>\n\n"
            f"Выберите быстрое действие или измените дату через календарь в CRM:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    else:
        await callback.answer("Запись не найдена", show_alert=True)

@dp.callback_query(F.data.startswith("shift:"))
async def on_shift_action(callback: CallbackQuery):
    parts = callback.data.split(":")
    booking_id = parts[1]
    action = parts[2]
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        await callback.answer("Запись не найдена", show_alert=True)
        return

    b = dict(row)
    cur_date = b["date"]
    new_date = cur_date

    if action == "tomorrow":
        new_date = cur_date.replace("Сегодня", "Завтра") if "Сегодня" in cur_date else f"Завтра, 14:00"
    elif action == "30":
        new_date = f"{cur_date} (+30м)"
    elif action == "60":
        new_date = f"{cur_date} (+1ч)"

    cursor.execute("UPDATE bookings SET date = ? WHERE id = ?", (new_date, booking_id))
    conn.commit()
    conn.close()

    await callback.answer(f"✅ Время заменено на: {new_date}", show_alert=True)
    await callback.message.edit_text(
        f"✅ <b>Время успешно заменено!</b>\n"
        f"👤 Клиент: <b>{b['name']}</b>\n"
        f"🕒 Новое время: <b>{new_date}</b>\n\n"
        f"<i>Статус обновлён в календаре на сайте и в базе.</i>"
    )

    client_id = b.get("client_chat_id")
    if client_id:
        try:
            await bot.send_message(
                chat_id=client_id,
                text=(
                    f"🔄 <b>Время вашего визита изменено мастером:</b>\n\n"
                    f"💅 <b>Услуга:</b> {b['service']}\n"
                    f"🕒 <b>Новое время:</b> <b>{new_date}</b>\n"
                    f"📍 <b>Адрес:</b> г. Вологда, Центр (ул. Мира)\n\n"
                    f"<i>Если у вас изменились планы, напишите мастеру в MAX: +7 (992) 282-11-58</i>"
                )
            )
        except Exception:
            pass

# ==================== STATUS ACTIONS ====================
@dp.callback_query(F.data.startswith("conf:"))
async def on_confirm(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (booking_id,))
    conn.commit()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        b = dict(row)
        await callback.answer("✅ Запись подтверждена!", show_alert=True)
        await callback.message.edit_text(
            render_booking_card(b),
            reply_markup=get_booking_actions_kb(b["id"], b["phone"], "confirmed")
        )

        client_id = b.get("client_chat_id")
        if client_id:
            try:
                await bot.send_message(
                    chat_id=client_id,
                    text=(
                        f"🎉 <b>Ваша запись подтверждена мастером!</b>\n\n"
                        f"🌸 Мастер: <b>Ангелина Осина</b>\n"
                        f"💅 Услуга: <b>{b['service']}</b>\n"
                        f"🕒 Дата и время: <b>{b['date']}</b>\n"
                        f"📍 Адрес: <b>г. Вологда, Центр (ул. Мира / Батюшкова)</b>\n\n"
                        f"Ждём вас на идеальный маникюр! ✨"
                    )
                )
            except Exception:
                pass
    else:
        await callback.answer("Запись не найдена", show_alert=True)

@dp.callback_query(F.data.startswith("canc:"))
async def on_cancel(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
    conn.commit()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        b = dict(row)
        await callback.answer("❌ Запись отклонена", show_alert=True)
        await callback.message.edit_text(
            render_booking_card(b),
            reply_markup=get_booking_actions_kb(b["id"], b["phone"], "cancelled")
        )

        client_id = b.get("client_chat_id")
        if client_id:
            try:
                site_url = WEB_APP_URL.replace("/admin", "").rstrip("/")
                await bot.send_message(
                    chat_id=client_id,
                    text=(
                        f"❌ <b>Запись отменена</b>\n\n"
                        f"Ваша запись на <b>{b['date']}</b> ({b['service']}) была отменена.\n"
                        f"Вы можете выбрать другое удобное время: {site_url}"
                    )
                )
            except Exception:
                pass
    else:
        await callback.answer("Запись не найдена", show_alert=True)

@dp.callback_query(F.data.startswith("done:"))
async def on_done(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE bookings SET status = 'completed' WHERE id = ?", (booking_id,))
    conn.commit()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        b = dict(row)
        await callback.answer("🎉 Визит успешно завершён!", show_alert=True)
        await callback.message.edit_text(
            render_booking_card(b),
            reply_markup=None
        )
    else:
        await callback.answer("Запись не найдена", show_alert=True)

# ==================== CLIENT STATUS REFRESH & LOOKUP ====================
@dp.callback_query(F.data.startswith("cstatus:"))
async def on_client_status_refresh(callback: CallbackQuery):
    booking_id = callback.data.split(":")[1]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    b = dict(row)
    status_label = {
        "new": "🟡 Ожидает подтверждения",
        "confirmed": "🟢 Подтверждена мастером!",
        "completed": "✅ Процедура выполнена",
        "cancelled": "🔴 Запись отменена"
    }.get(b.get("status", "new"), b.get("status", "new"))

    await callback.answer(f"Статус: {status_label}")
    try:
        await callback.message.edit_text(
            render_client_booking_card(b),
            reply_markup=get_client_booking_actions_kb(b["id"])
        )
    except Exception:
        pass

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_client_text_lookup(message: Message):
    if message.from_user.id in ADMIN_IDS:
        return
    
    # Ignore predefined reply keyboard buttons
    if message.text in ["📋 Моя запись", "📍 Адрес и контакты", "💅 Записаться на визит"]:
        return

    text = message.text.strip()
    user_id = message.from_user.id
    name = message.from_user.first_name or "Клиент"

    # 1. Booking ID lookup (e.g. "BK-104", "#BK-104", "104", "bk104")
    clean_id = text.upper().replace("#", "").strip()
    if clean_id.startswith("BK") and not clean_id.startswith("BK-"):
        clean_id = f"BK-{clean_id[2:]}"
    elif clean_id.isdigit() and len(clean_id) <= 5:
        clean_id = f"BK-{clean_id}"

    conn = get_db()
    cursor = conn.cursor()
    
    rows = []
    if clean_id.startswith("BK-"):
        cursor.execute("SELECT * FROM bookings WHERE id = ?", (clean_id,))
        r = cursor.fetchone()
        if r:
            rows = [dict(r)]

    # 2. Phone lookup (if text contains 10+ digits)
    if not rows:
        digits = re.sub(r'\D', '', text)
        if len(digits) >= 10:
            clean_10 = digits[-10:]
            cursor.execute("SELECT * FROM bookings WHERE clean_phone(phone) = ? ORDER BY created_at DESC LIMIT 3", (clean_10,))
            rows = [dict(r) for r in cursor.fetchall()]

    if rows:
        # Bind client_chat_id
        for b in rows:
            cursor.execute("UPDATE bookings SET client_chat_id = ?, telegram_username = ? WHERE id = ?",
                           (user_id, message.from_user.username or "", b["id"]))
        conn.commit()
        conn.close()

        await message.answer(
            f"🌸 Здравствуйте, {name}! Найдено записей: <b>{len(rows)}</b>.\n"
            f"✅ Уведомления подключены к этому чату!",
            reply_markup=get_client_keyboard()
        )
        for b in rows:
            await message.answer(
                render_client_booking_card(b),
                reply_markup=get_client_booking_actions_kb(b["id"])
            )
        return

    conn.close()

    # Friendly hint if input was not recognized
    await message.answer(
        "🌸 <b>Студия ногтевого сервиса Ангелины Осиной</b>\n\n"
        "Чтобы проверить статус вашей записи, отправьте в этот чат:\n"
        "• <b>Номер брони</b> (например: <code>BK-104</code>)\n"
        "• или <b>Номер телефона</b>, указанный при записи.\n\n"
        "Либо нажмите «💅 Записаться на визит» в меню ниже:",
        reply_markup=get_client_keyboard()
    )

# ==================== MAIN RUNNER ====================
async def main():
    print(f"🚀 Angelina Studio Bot started successfully!")
    print(f"Admins: {ADMIN_IDS}")
    print(f"Database: {DB_PATH}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")
