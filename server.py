import os
import sys
import json
import sqlite3
import random
import time
import re
import threading
import urllib.request
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Configuration & environment
from config import BOT_TOKEN as TELEGRAM_BOT_TOKEN, ADMIN_IDS as ADMIN_CHAT_IDS, DB_PATH, BASE_DIR

RECENT_BOOKING_IPS = {}  # ip -> timestamp

def validate_booking_data(payload, client_ip):
    # 1. Honeypot check (hidden bot field)
    if payload.get("website_url") or payload.get("email_check"):
        return False, "Spam detected"

    # 2. Anti-flood check (max 1 booking per 5 seconds per IP)
    now = time.time()
    last_time = RECENT_BOOKING_IPS.get(client_ip, 0)
    if now - last_time < 5:
        return False, "Слишком много запросов. Подождите несколько секунд перед повторной отправкой."

    name = payload.get("name", "").strip()
    phone = payload.get("phone", "").strip()

    if not name or not phone:
        return False, "Пожалуйста, укажите имя и номер телефона"

    # 3. Name validation
    if len(name) < 2 or len(name) > 35:
        return False, "Имя должно содержать от 2 до 35 символов"

    words = name.split()
    if len(words) > 3:
        return False, "Слишком много слов в имени (максимум 3)"

    for w in words:
        if len(w) < 2 or len(w) > 18:
            return False, f"Слово '{w}' некорректной длины"
        # Mixed case inside word like АВЛЫаолывао
        if re.search(r'[А-ЯA-Z]{2,}[а-яa-z]+|[а-яa-z]+[А-ЯA-Z]', w):
            return False, "Некорректный регистр букв в имени"

    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', name):
        return False, "Имя может содержать только буквы, пробелы или дефис"

    low = name.lower()
    # Must contain at least one vowel
    if not re.search(r'[аеёиоуыэюяaeiouy]', low):
        return False, "Пожалуйста, введите настоящее имя"

    # Reject keyboard mash: 4+ consecutive consonants
    if re.search(r'[бвгджзйклмнпрстфхцчшщbcdfghjklmnpqrstvwxyz]{4,}', low):
        return False, "Пожалуйста, проверьте правильность написания имени"

    # Reject 3+ consecutive vowels (e.g. euyieee)
    if re.search(r'[аеёиоуыэюяaeiouy]{3,}', low):
        return False, "Пожалуйста, проверьте правильность написания имени"

    # Impossible Russian combinations with Ы (e.g. АВЛЫаолывао)
    if re.search(r'ы[аеёиоуыэюя]', low):
        return False, "Пожалуйста, проверьте правильность написания имени"

    # Repetitive single character (e.g. ooooooooo)
    if re.search(r'(.)\1{3,}', low):
        return False, "Повторяющиеся буквы в имени"

    # 4. Phone validation: exactly 11 digits, must start with 7/8 and second digit 9
    digits = re.sub(r'\D', '', phone)
    if len(digits) != 11:
        return False, "Номер телефона должен содержать 11 цифр: +7 (9XX) XXX-XX-XX"

    if digits[0] not in ('7', '8'):
        return False, "Номер телефона должен начинаться с +7 или 8"

    if digits[1] != '9':
        return False, "Укажите действующий мобильный номер РФ (+7 9XX...)"

    # Disallow known dummy repeated sequences
    dummy_prefixes = ['9999999999', '9000000000', '9111111111', '9123456789', '9876543210']
    for dp in dummy_prefixes:
        if dp in digits:
            return False, "Пожалуйста, укажите настоящий действующий номер телефона"

    RECENT_BOOKING_IPS[client_ip] = now
    
    # Capitalize name nicely and normalize phone
    clean_name = " ".join(word.capitalize() for word in name.split())
    norm_phone = f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return True, (clean_name, norm_phone)

def send_telegram_booking_alert(booking):
    raw_phone = re.sub(r'\D', '', str(booking.get('phone', '')))
    if raw_phone.startswith('8'):
        raw_phone = '7' + raw_phone[1:]

    price_val = booking.get('price', 0)
    price_str = f"{price_val:,} ₽".replace(",", " ") if price_val else "По прайсу"

    text = (
        f"✨ <b>НОВАЯ ЗАПИСЬ С САЙТА!</b> ✨\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Клиент:</b> {booking.get('name')}\n"
        f"📱 <b>Телефон:</b> <code>{booking.get('phone')}</code>\n"
        f"💅 <b>Услуга:</b> {booking.get('service')}\n"
        f"💵 <b>Сумма:</b> <b>{price_str}</b>\n"
        f"🕒 <b>Дата и время:</b> <b>{booking.get('date')}</b>\n"
        f"💬 <b>Пожелание:</b> <i>{booking.get('notes') or 'Заявка с сайта'}</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Статус: 🟡 <b>Ожидает подтверждения</b>"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": f"conf:{booking.get('id')}"},
                {"text": "❌ Отклонить", "callback_data": f"canc:{booking.get('id')}"}
            ],
            [
                {"text": "🟣 Написать в MAX", "callback_data": f"max:{booking.get('id')}"}
            ]
        ]
    }

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in ADMIN_CHAT_IDS:
        try:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print(f"Failed to send Telegram notification to {chat_id}: {e}")

def notify_telegram_client(client_chat_id, text):
    if not client_chat_id or not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        payload = {
            "chat_id": client_chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Failed to send Telegram notification to client {client_chat_id}: {e}")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.create_function("clean_phone", 1, lambda s: re.sub(r'\D', '', str(s or ''))[-10:])
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        service TEXT NOT NULL,
        price INTEGER DEFAULT 0,
        date TEXT NOT NULL,
        status TEXT DEFAULT 'new',
        notes TEXT DEFAULT '',
        slot_date TEXT DEFAULT '',
        slot_time TEXT DEFAULT '',
        client_chat_id INTEGER DEFAULT 0,
        telegram_username TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Auto-migrate existing columns if table already existed
    cursor.execute("PRAGMA table_info(bookings)")
    existing_cols = [c[1] for c in cursor.fetchall()]
    new_cols = [
        ("slot_date", "TEXT DEFAULT ''"),
        ("slot_time", "TEXT DEFAULT ''"),
        ("client_chat_id", "INTEGER DEFAULT 0"),
        ("telegram_username", "TEXT DEFAULT ''")
    ]
    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_type}")

    # Ensure table schema is up to date without injecting sample test data
    conn.commit()
    conn.close()

class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # API: Get all bookings for Master
        if path == "/api/bookings":
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bookings ORDER BY created_at DESC")
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return self.send_json({"success": True, "bookings": rows})

        # API: Available slots for date
        elif path == "/api/available-slots":
            target_date = params.get("date", [""])[0].strip()
            if not target_date:
                target_date = datetime.now().strftime("%Y-%m-%d")

            standard_slots = ["10:00", "12:30", "15:00", "17:30", "20:00"]
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT slot_time, date FROM bookings 
                WHERE (slot_date = ? OR date LIKE ?) AND status != 'cancelled'
            """, (target_date, f"%{target_date}%"))
            rows = cursor.fetchall()
            conn.close()

            booked_times = set()
            for r in rows:
                if r["slot_time"]:
                    booked_times.add(r["slot_time"])
                else:
                    for s in standard_slots:
                        if s in r["date"]:
                            booked_times.add(s)

            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            cur_time_str = now.strftime("%H:%M")

            slot_list = []
            for s in standard_slots:
                is_past = (target_date == today_str and s <= cur_time_str)
                is_avail = (s not in booked_times) and (not is_past)
                slot_list.append({
                    "time": s,
                    "available": is_avail,
                    "reason": "past" if is_past else ("booked" if s in booked_times else "open")
                })

            return self.send_json({
                "success": True,
                "date": target_date,
                "slots": slot_list
            })

        # API: Single booking status check for client live tracking
        elif path == "/api/booking/status":
            booking_id = params.get("id", [""])[0].strip()
            if not booking_id:
                return self.send_json({"success": False, "error": "ID обязателен"}, 400)

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
            row = cursor.fetchone()
            conn.close()

            if row:
                b = dict(row)
                return self.send_json({
                    "success": True,
                    "booking": {
                        "id": b["id"],
                        "name": b["name"],
                        "service": b["service"],
                        "price": b["price"],
                        "date": b["date"],
                        "status": b["status"],
                        "notes": b["notes"],
                        "slot_date": b.get("slot_date", ""),
                        "slot_time": b.get("slot_time", ""),
                        "client_chat_id": b.get("client_chat_id", 0)
                    }
                })
            else:
                return self.send_json({"success": False, "error": "Запись не найдена"}, 404)

        # API: Client lookup by phone
        elif path == "/api/client/status":
            phone_param = params.get("phone", [""])[0].strip()
            clean_digits = re.sub(r'\D', '', phone_param)
            if len(clean_digits) < 7:
                return self.send_json({"success": False, "message": "Введите корректный номер телефона"}, 400)
            
            conn = get_db()
            cursor = conn.cursor()
            if len(clean_digits) >= 10:
                cursor.execute("SELECT * FROM bookings WHERE clean_phone(phone) = ? ORDER BY created_at DESC", (clean_digits[-10:],))
            else:
                cursor.execute("SELECT * FROM bookings WHERE clean_phone(phone) LIKE ? ORDER BY created_at DESC", (f"%{clean_digits}",))
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()

            if rows:
                return self.send_json({"success": True, "found": True, "bookings": rows})
            else:
                return self.send_json({"success": True, "found": False, "message": "Записей по этому номеру не найдено"})

        # Clean URLs mapping: /admin -> admin.html
        if path in ["/admin", "/admin/", "/login", "/login/"]:
            self.path = "/admin.html"
            return super().do_GET()

        # Static files fallback (index.html, admin.html, images)
        return super().do_GET()

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/admin", "/admin/", "/login", "/login/"]:
            self.path = "/admin.html"
        return super().do_HEAD()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            payload = json.loads(body_bytes.decode('utf-8'))
        except Exception:
            payload = {}

        # API: Create new booking from website form or manual admin
        if path == "/api/bookings":
            client_ip = self.headers.get('X-Real-IP') or self.headers.get('X-Forwarded-For') or self.client_address[0]
            is_valid, val_result = validate_booking_data(payload, client_ip)
            if not is_valid:
                return self.send_json({"success": False, "error": val_result}, 400)

            name, phone = val_result
            service = payload.get("service", "Маникюр")
            notes = payload.get("notes", "Заявка с сайта")
            slot_date = payload.get("slot_date", "").strip()
            slot_time = payload.get("slot_time", "").strip()
            telegram_username = payload.get("telegram_username", "").strip()
            client_chat_id = payload.get("client_chat_id", 0)
            status = payload.get("status", "new")

            clean_digits = re.sub(r'\D', '', phone)[-10:]

            conn = get_db()
            cursor = conn.cursor()

            # 1. Exact active duplicate check (same phone, same slot_date, same slot_time)
            if slot_date and slot_time:
                cursor.execute("""
                    SELECT * FROM bookings 
                    WHERE clean_phone(phone) = ? 
                      AND slot_date = ? 
                      AND slot_time = ? 
                      AND status != 'cancelled'
                """, (clean_digits, slot_date, slot_time))
                exact_row = cursor.fetchone()
                if exact_row:
                    b = dict(exact_row)
                    conn.close()
                    return self.send_json({
                        "success": False,
                        "error": f"У вас уже оформлена запись на это время (№ {b['id']}, {b['service']}). Мастер свяжется с вами или вы можете проверить статус во вкладке «Моя запись»."
                    }, 409)

            # 2. General slot conflict check (someone else booked this slot)
            if slot_date and slot_time:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM bookings 
                    WHERE slot_date = ? AND slot_time = ? AND status != 'cancelled'
                """, (slot_date, slot_time))
                if cursor.fetchone()["cnt"] > 0:
                    conn.close()
                    return self.send_json({
                        "success": False, 
                        "error": "Это время уже занято другим клиентом. Пожалуйста, выберите другое свободное окошко."
                    }, 409)

            # 3. Anti-duplicate / rapid re-submit check:
            # If same phone already has an unconfirmed 'new' booking created within the last 15 minutes
            cursor.execute("""
                SELECT * FROM bookings 
                WHERE clean_phone(phone) = ? 
                  AND status = 'new'
                ORDER BY created_at DESC LIMIT 1
            """, (clean_digits,))
            recent_pending = cursor.fetchone()
            if recent_pending:
                b = dict(recent_pending)
                try:
                    from datetime import datetime, timezone
                    created_raw = b.get("created_at", "")
                    # SQLite timestamp format: YYYY-MM-DD HH:MM:SS (UTC)
                    created_dt = datetime.strptime(created_raw.split(".")[0], "%Y-%m-%d %H:%M:%S")
                    diff_seconds = (datetime.now(timezone.utc).replace(tzinfo=None) - created_dt).total_seconds()
                except Exception:
                    diff_seconds = 0

                # If within 15 minutes (900s), return existing booking without duplicating
                if 0 <= diff_seconds < 900:
                    conn.close()
                    return self.send_json({
                        "success": True,
                        "booking": b,
                        "is_duplicate": True,
                        "message": f"Ваша заявка № {b['id']} на {b['date']} уже принята и ожидает подтверждения мастера."
                    })
            conn.close()

            # Format human-friendly date string
            date = payload.get("date", "").strip()
            if not date:
                if slot_date and slot_time:
                    try:
                        dt = datetime.strptime(slot_date, "%Y-%m-%d")
                        months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
                        weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                        m_str = months[dt.month - 1]
                        w_str = weekdays[dt.weekday()]
                        date = f"{dt.day} {m_str} ({w_str}), {slot_time}"
                    except Exception:
                        date = f"{slot_date}, {slot_time}"
                else:
                    date = "Сегодня, " + datetime.now().strftime("%H:%M")

            booking_id = "BK-" + str(random.randint(100, 999))
            
            # Extract price if present in service string
            match = re.search(r'(\d[\d\s]*)\s*₽', service)
            price = int(match.group(1).replace(" ", "")) if match else 2500

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO bookings (id, name, phone, service, price, date, status, notes, slot_date, slot_time, client_chat_id, telegram_username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (booking_id, name, phone, service, price, date, status, notes, slot_date, slot_time, client_chat_id, telegram_username))
            conn.commit()

            cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
            new_item = dict(cursor.fetchone())
            conn.close()

            # Trigger instant Telegram notification to admins in background thread
            threading.Thread(target=send_telegram_booking_alert, args=(new_item,), daemon=True).start()

            return self.send_json({"success": True, "booking": new_item})

        # API: Update booking (status or notes)
        elif path == "/api/bookings/update":
            booking_id = payload.get("id")
            if not booking_id:
                return self.send_json({"success": False, "error": "ID обязателен"}, 400)

            conn = get_db()
            cursor = conn.cursor()
            if "status" in payload:
                new_status = payload["status"]
                cursor.execute("UPDATE bookings SET status = ? WHERE id = ?", (new_status, booking_id))
                conn.commit()

                # Trigger automated notification to client if client_chat_id is linked
                cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
                b_row = cursor.fetchone()
                if b_row:
                    b = dict(b_row)
                    client_id = b.get("client_chat_id")
                    if client_id:
                        if new_status == "confirmed":
                            msg = (
                                f"🎉 <b>Ваша запись подтверждена!</b>\n\n"
                                f"🌸 Мастер: <b>Ангелина Осина</b>\n"
                                f"💅 Услуга: <b>{b.get('service')}</b>\n"
                                f"🕒 Дата и время: <b>{b.get('date')}</b>\n"
                                f"📍 Адрес: <b>г. Вологда, Центр (ул. Мира)</b>\n\n"
                                f"Ждём вас на идеальный маникюр! ✨"
                            )
                            threading.Thread(target=notify_telegram_client, args=(client_id, msg), daemon=True).start()
                        elif new_status == "cancelled":
                            msg = (
                                f"❌ <b>Запись отменена</b>\n\n"
                                f"Ваша запись на <b>{b.get('date')}</b> ({b.get('service')}) была отменена.\n"
                                f"Вы можете выбрать другое удобное время на сайте."
                            )
                            threading.Thread(target=notify_telegram_client, args=(client_id, msg), daemon=True).start()

            if "notes" in payload:
                cursor.execute("UPDATE bookings SET notes = ? WHERE id = ?", (payload["notes"], booking_id))
                conn.commit()
            conn.close()
            return self.send_json({"success": True})

        # API: Delete booking
        elif path == "/api/bookings/delete":
            booking_id = payload.get("id")
            if not booking_id:
                return self.send_json({"success": False, "error": "ID обязателен"}, 400)

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
            conn.commit()
            conn.close()
            return self.send_json({"success": True})

        # API: Master login
        elif path == "/api/login":
            username = payload.get("username", "").strip().lower()
            password = payload.get("password", "").strip()

            if password == "Angelina231200@" or (username in ["angelina", "admin", "alexey"] and password in ["Angelina231200@", "angelina2026"]):
                return self.send_json({"success": True, "token": "session_ok", "user": "Ангелина Осина"})
            else:
                return self.send_json({"success": False, "error": "Неверный логин или пароль"}, 401)

        self.send_error(404, "Endpoint not found")

def run(port=3000):
    init_db()
    server_address = ('', port)
    httpd = HTTPServer(server_address, AppHandler)
    print(f"Server with SQLite Database started at http://localhost:{port}/")
    print(f"Database file: {DB_PATH}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    port = 3000
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    run(port)
