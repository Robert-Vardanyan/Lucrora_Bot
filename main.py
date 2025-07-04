import asyncio
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from operator import itemgetter
import json
import asyncpg

from aiogram import Bot, Dispatcher
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from dotenv import load_dotenv

# === Загрузка переменных окружения ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# === Инициализация FastAPI ===
app = FastAPI()

# CORS для Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://lucrora.vercel.app", "https://lucrora-bot.onrender.com", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Инициализация Telegram-бота ===
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- Пул подключений к базе данных ---
db_pool = None

# === Кнопка Mini App ===
webapp_button = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚀 Запустить Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]
])

# === Обработчик /start ===
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "👋 Привет! Нажми кнопку ниже, чтобы открыть Mini App:",
        reply_markup=webapp_button
    )
    await message.delete()

# === Подпись инициализации Mini App ===
def check_webapp_signature(init_data: str, token: str) -> bool:
    try:
        parsed_data = dict(parse_qsl(init_data))
    except ValueError:
        return False
    if "hash" not in parsed_data:
        return False

    hash_ = parsed_data.pop('hash')
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items(), key=itemgetter(0)))
    secret_key = hmac.new(
        key=b"WebAppData", msg=token.encode(), digestmod=hashlib.sha256
    )
    calculated_hash = hmac.new(
        key=secret_key.digest(), msg=data_check_string.encode(), digestmod=hashlib.sha256
    ).hexdigest()
    return calculated_hash == hash_

# === Эндпоинт инициализации Mini App ===
@app.post("/api/init")
async def api_init(request: Request):
    global db_pool

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("initData")
    if not init_data or not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData")

    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=400, detail="User data not found in initData")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = str(user_info.get('id'))
        first_name = user_info.get('first_name', '')
        last_name = user_info.get('last_name', '')
        username_tg = user_info.get('username', '')
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid user data JSON in initData")

    try:
        async with db_pool.acquire() as connection:
            user = await connection.fetchrow("SELECT * FROM users WHERE id = $1", int(telegram_id))

            if user:
                return JSONResponse({
                    "ok": True,
                    "isRegistered": True,
                    "main_balance": float(user['main_balance']),
                    "bonus_balance": float(user['bonus_balance']),
                    "lucrum_balance": float(user['lucrum_balance']),
                    "total_invested": float(user['total_invested']),
                    "total_withdrawn": float(user['total_withdrawn']),
                    "username": user['username'],
                    "first_name": user['first_name']
                })
            else:
                return JSONResponse({
                    "ok": True,
                    "isRegistered": False,
                    "main_balance": 0.0,
                    "bonus_balance": 0.0,
                    "lucrum_balance": 0.0,
                    "total_invested": 0.0,
                    "total_withdrawn": 0.0,
                    "username": username_tg or first_name or "Пользователь",
                    "first_name": first_name
                })
    except Exception as e:
        print(f"Ошибка БД при инициализации: {e}")
        raise HTTPException(status_code=500, detail=f"Database error during initialization: {e}")

# === Эндпоинт: Регистрация пользователя ===
@app.post("/api/register")
async def api_register(request: Request):
    global db_pool

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")
    username = body.get("username")
    password = body.get("password")
    referral_code = body.get("referralCode")

    if not init_data or not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=400, detail="User data not found in initData")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = str(user_info.get('id'))
        first_name = user_info.get('first_name', '')
        last_name = user_info.get('last_name', '')
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid user data JSON in initData")

    try:
        async with db_pool.acquire() as connection:
            existing_user_by_id = await connection.fetchrow("SELECT id FROM users WHERE id = $1", int(telegram_id))
            if existing_user_by_id:
                raise HTTPException(status_code=409, detail="User already registered with this Telegram ID")

            existing_user_by_username = await connection.fetchrow("SELECT id FROM users WHERE username = $1", username)
            if existing_user_by_username:
                raise HTTPException(status_code=409, detail="Username already taken")

            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO users (id, username, first_name, last_name, password_hash)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    int(telegram_id),
                    username,
                    first_name,
                    last_name,
                    password
                )

                if referral_code:
                    referrer = await connection.fetchrow("SELECT id FROM users WHERE username = $1", referral_code)
                    if referrer:
                        await connection.execute(
                            """
                            INSERT INTO referrals (referrer_id, referred_id, referral_level)
                            VALUES ($1, $2, $3)
                            """,
                            referrer['id'],
                            int(telegram_id),
                            1
                        )
                        print(f"Добавлена реферальная связь: {referrer['id']} пригласил {telegram_id}")
                    else:
                        print(f"Реферальный код '{referral_code}' не найден.")

            print(f"Пользователь {username} (ID: {telegram_id}) успешно зарегистрирован в БД.")

            return JSONResponse({
                "ok": True,
                "message": "Registration successful!",
                "user_id": telegram_id,
                "username": username
            })
    except asyncpg.exceptions.UniqueViolationError as e:
        if "users_username_key" in str(e):
            raise HTTPException(status_code=409, detail="Username already taken")
        elif "referrals_referred_id_key" in str(e):
            raise HTTPException(status_code=409, detail="User already registered with this Telegram ID via referral")
        else:
            raise HTTPException(status_code=500, detail=f"Database unique constraint error: {e}")
    except Exception as e:
        print(f"Ошибка при регистрации пользователя: {e}")
        raise HTTPException(status_code=500, detail=f"Database error during registration: {e}")

# === НОВЫЙ ЭНДПОИНТ: Выход из аккаунта ===
@app.post("/api/logout")
async def api_logout(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")
    
    # Проверяем initData для безопасности
    if not init_data or not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData")

    # В данной реализации, так как нет управления сессиями на бэкенде,
    # мы просто подтверждаем, что запрос на выход был валидным.
    # В реальном приложении здесь могла бы быть логика инвалидации токена сессии.
    print(f"Пользователь с initData: {init_data} запросил выход из аккаунта.")

    return JSONResponse({
        "ok": True,
        "message": "Logged out successfully."
    })


# === Запуск бота и подключение к БД на старте FastAPI ===
@app.on_event("startup")
async def on_startup():
    global db_pool
    print("🚀 FastAPI стартовал.")
    try:
        print(f"Попытка подключения к базе данных: {DATABASE_URL}")
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        print("✅ Подключение к базе данных установлено.")
    except Exception as e:
        print(f"❌ Ошибка подключения к базе данных: {e}")
        raise

    print("Запускаем aiogram polling...")
    asyncio.create_task(start_bot())

# === Закрытие пула подключений к БД при завершении работы FastAPI ===
@app.on_event("shutdown")
async def on_shutdown():
    global db_pool
    if db_pool:
        await db_pool.close()
        print("❌ Подключение к базе данных закрыто.")
    print("FastAPI завершил работу.")

# === Функция запуска polling ===
async def start_bot():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("🤖 Запускаем бота...")
        await dp.start_polling(bot)
    except Exception as e:
        print("❌ Ошибка при запуске бота:", e)

