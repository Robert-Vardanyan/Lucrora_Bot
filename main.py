import asyncio
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from operator import itemgetter
import json # Импортируем json для парсинга user из initData

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

# === Инициализация FastAPI ===
app = FastAPI()

# CORS для Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://lucrora.vercel.app", "https://lucrora-bot.onrender.com", "http://localhost:8000"], # Добавил localhost для локальной разработки
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Инициализация Telegram-бота ===
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# === ИМИТАЦИЯ БАЗЫ ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ В ПАМЯТИ ===
# В реальном приложении это была бы настоящая база данных (PostgreSQL, MongoDB, Firestore и т.д.)
# Ключ: Telegram User ID (str)
# Значение: dict с данными пользователя (username, main_balance, bonus_balance, lucrum_balance и т.д.)
user_db = {}
# Пример зарегистрированного пользователя для тестирования:
# user_db['123456789'] = {
#     "username": "testuser",
#     "main_balance": 100.50,
#     "bonus_balance": 25.00,
#     "lucrum_balance": 50.00,
#     "total_invested": 200.00,
#     "total_withdrawn": 50.00
# }

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
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("initData")
    if not init_data or not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData")

    # --- ИЗМЕНЕНИЕ: Извлекаем user_id из init_data ---
    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=400, detail="User data not found in initData")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = str(user_info.get('id'))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid user data JSON in initData")

    # --- ИЗМЕНЕНИЕ: Проверяем, зарегистрирован ли пользователь ---
    if telegram_id in user_db:
        user_data = user_db[telegram_id]
        return JSONResponse({
            "ok": True,
            "isRegistered": True, # Пользователь зарегистрирован
            "main_balance": user_data.get("main_balance", 0.0),
            "bonus_balance": user_data.get("bonus_balance", 0.0),
            "lucrum_balance": user_data.get("lucrum_balance", 0.0),
            "total_invested": user_data.get("total_invested", 0.0),
            "total_withdrawn": user_data.get("total_withdrawn", 0.0),
            "username": user_data.get("username", "N/A") # Добавил username, чтобы frontend мог его отобразить
        })
    else:
        # Пользователь не зарегистрирован, возвращаем дефолтные значения и isRegistered=False
        return JSONResponse({
            "ok": True,
            "isRegistered": False, # Пользователь не зарегистрирован
            "main_balance": 0.0,
            "bonus_balance": 0.0,
            "lucrum_balance": 0.0,
            "total_invested": 0.0,
            "total_withdrawn": 0.0,
            "username": user_info.get("first_name", "Пользователь") # Используем имя из Telegram для нового пользователя
        })

# === НОВЫЙ ЭНДПОИНТ: Регистрация пользователя ===
@app.post("/api/register")
async def api_register(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")
    username = body.get("username")
    password = body.get("password") # В реальном приложении пароли НЕ хранятся в открытом виде!
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid user data JSON in initData")

    # Проверяем, не зарегистрирован ли пользователь уже
    if telegram_id in user_db:
        raise HTTPException(status_code=409, detail="User already registered")

    # Проверяем, не занято ли имя пользователя (простая проверка для примера)
    for uid, data in user_db.items():
        if data.get("username") == username:
            raise HTTPException(status_code=409, detail="Username already taken")

    # --- ИЗМЕНЕНИЕ: Регистрируем нового пользователя в имитированной БД ---
    user_db[telegram_id] = {
        "username": username,
        "password": password, # Опять же, в реальном приложении так не делают!
        "referral_code": referral_code,
        "main_balance": 0.0,
        "bonus_balance": 0.0,
        "lucrum_balance": 0.0,
        "total_invested": 0.0,
        "total_withdrawn": 0.0,
        "registration_date": "01.01.2025" # Заглушка
    }
    print(f"Пользователь {username} (ID: {telegram_id}) успешно зарегистрирован.")

    return JSONResponse({
        "ok": True,
        "message": "Registration successful!",
        "user_id": telegram_id,
        "username": username
    })


# === Запуск бота на старте FastAPI ===
@app.on_event("startup")
async def on_startup():
    print("🚀 FastAPI стартовал. Запускаем aiogram polling...")
    asyncio.create_task(start_bot())

# === Функция запуска polling ===
async def start_bot():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("🤖 Запускаем бота...")
        await dp.start_polling(bot)
    except Exception as e:
        print("❌ Ошибка при запуске бота:", e)