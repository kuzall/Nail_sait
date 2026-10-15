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
    return conn

def format_phone_link(phone: str) -> str:
    digits = re.sub(r'\D', '', str(phone))
    if digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits

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
    name = message.from_user.first_name or "Мастер"
    
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
        await message.answer(
            f"Здравствуйте, {name}! 🌸\n\n"
            f"Это бот студии ногтевого сервиса <b>Ангелины</b> в Вологде.\n\n"
            f"Вы можете записаться на идеальный маникюр на нашем сайте:\n"
            f"👉 https://138.249.149.151.sslip.io/\n\n"
            f"<i>Ваш ID: <code>{user_id}</code></i>"
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
