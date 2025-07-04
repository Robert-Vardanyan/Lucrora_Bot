# main.py

import asyncio
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from operator import itemgetter
import json

from aiogram import Bot, Dispatcher
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession # Для зависимостей
from sqlalchemy import select # Для выполнения SELECT запросов
from sqlalchemy.exc import IntegrityError # Для обработки ошибок уникальности

from dotenv import load_dotenv

# --- Импортируем наши ORM-модели и утилиты БД ---
from app.database import engine, create_db_tables, drop_db_tables, get_async_session
from app.models import User, Investment, Transaction, Referral # Импортируем все модели

# === Загрузка переменных окружения ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")
# Новая переменная для управления сбросом/созданием БД
# Установите в .env: DROP_DB_ON_STARTUP=True для сброса и создания заново
# Или DROP_DB_ON_STARTUP=False (или просто не устанавливайте) для обычного старта
DROP_DB_ON_STARTUP = os.getenv("DROP_DB_ON_STARTUP", "False").lower() == "true"

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
async def api_init(request: Request, db: AsyncSession = Depends(get_async_session)): # Внедряем сессию БД
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
        telegram_id = int(user_info.get('id')) # Преобразуем в int для использования с BigInteger
        first_name = user_info.get('first_name', '')
        last_name = user_info.get('last_name', '')
        username_tg = user_info.get('username', '') # Username из Telegram
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user data JSON or Telegram ID in initData")

    # --- Использование SQLAlchemy ORM для запроса пользователя ---
    # stmt = select(User).where(User.id == telegram_id)
    # result = await db.execute(stmt)
    # user = result.scalar_one_or_none()

    # Более простой способ для запроса по первичному ключу
    user = await db.get(User, telegram_id) # Запрос пользователя по первичному ключу

    if user:
        # Пользователь найден, он зарегистрирован
        return {
            "ok": True,
            "isRegistered": True,
            "main_balance": float(user.main_balance),
            "bonus_balance": float(user.bonus_balance),
            "lucrum_balance": float(user.lucrum_balance),
            "total_invested": float(user.total_invested),
            "total_withdrawn": float(user.total_withdrawn),
            "username": user.username,
            "first_name": user.first_name
        }
    else:
        # Пользователь не найден, он не зарегистрирован
        return {
            "ok": True,
            "isRegistered": False,
            "main_balance": 0.0,
            "bonus_balance": 0.0,
            "lucrum_balance": 0.0,
            "total_invested": 0.0,
            "total_withdrawn": 0.0,
            "username": username_tg or first_name or "Пользователь",
            "first_name": first_name
        }

# === НОВЫЙ ЭНДПОИНТ: Регистрация пользователя ===
@app.post("/api/register")
async def api_register(request: Request, db: AsyncSession = Depends(get_async_session)): # Внедряем сессию БД
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")
    username = body.get("username")
    password = body.get("password") # НАПОМИНАНИЕ: В реальном приложении НЕ храните пароли в открытом виде!
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
        telegram_id = int(user_info.get('id'))
        first_name = user_info.get('first_name', '')
        last_name = user_info.get('last_name', '')
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user data JSON or Telegram ID in initData")

    # --- Использование SQLAlchemy ORM для регистрации пользователя ---
    try:
        # Проверяем, не зарегистрирован ли пользователь уже по Telegram ID
        existing_user_by_id = await db.get(User, telegram_id)
        if existing_user_by_id:
            raise HTTPException(status_code=409, detail="User already registered with this Telegram ID")

        # Проверяем, не занято ли имя пользователя
        stmt_check_username = select(User).where(User.username == username)
        existing_user_by_username = (await db.execute(stmt_check_username)).scalar_one_or_none()
        if existing_user_by_username:
            raise HTTPException(status_code=409, detail="Username already taken")

        # Создаем нового пользователя
        new_user = User(
            id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            password_hash=password # НАПОМИНАНИЕ: Замените на хеширование!
        )
        db.add(new_user)
        await db.commit() # Сохраняем пользователя, чтобы его ID был доступен для реферала
        await db.refresh(new_user) # Обновляем объект, чтобы получить актуальные данные из БД (например, даты по умолчанию)

        # Если есть реферальный код, добавляем запись в таблицу referrals
        if referral_code:
            stmt_referrer = select(User).where(User.username == referral_code)
            referrer = (await db.execute(stmt_referrer)).scalar_one_or_none()
            if referrer:
                new_referral = Referral(
                    referrer_id=referrer.id,
                    referred_id=new_user.id,
                    referral_level=1
                )
                db.add(new_referral)
                await db.commit() # Сохраняем реферальную связь
                print(f"Добавлена реферальная связь: {referrer.username} (ID: {referrer.id}) пригласил {new_user.username} (ID: {new_user.id})")
            else:
                print(f"Реферальный код '{referral_code}' не найден.")
                # Опционально: можно вернуть ошибку, если реферальный код обязателен

        print(f"Пользователь {username} (ID: {telegram_id}) успешно зарегистрирован в БД.")

        return {
            "ok": True,
            "message": "Registration successful!",
            "user_id": str(telegram_id), # Возвращаем str, так как на фронтенде может быть str
            "username": username
        }
    except IntegrityError as e:
        await db.rollback() # Откатываем транзакцию в случае ошибки
        print(f"Ошибка целостности при регистрации пользователя: {e}")
        # Пример обработки конкретных ошибок IntegrityError
        if "users_username_key" in str(e): # Замените на фактическое имя уникального индекса, если оно другое
             raise HTTPException(status_code=409, detail="Username already taken.")
        # Добавьте другие проверки на уникальность, если они нужны
        raise HTTPException(status_code=500, detail=f"Database integrity error during registration: {e}")
    except Exception as e:
        await db.rollback() # Откатываем транзакцию в случае любой другой ошибки
        print(f"Общая ошибка при регистрации пользователя: {e}")
        raise HTTPException(status_code=500, detail=f"Database error during registration: {e}")


# === Запуск бота и подключение к БД на старте FastAPI ===
@app.on_event("startup")
async def on_startup():
    print("🚀 FastAPI стартовал.")
    try:
        if DROP_DB_ON_STARTUP:
            print("❗ Переменная DROP_DB_ON_STARTUP=True. Удаляю все таблицы...")
            await drop_db_tables()
            print("✅ Все таблицы успешно удалены.")

        print("Создаю/проверяю таблицы базы данных...")
        await create_db_tables()
        print("✅ Структура базы данных готова.")
    except Exception as e:
        print(f"❌ Ошибка при инициализации базы данных: {e}")
        # В продакшене, возможно, стоит завершить работу приложения, если нет подключения к БД
        raise

    print("Запускаем aiogram polling...")
    asyncio.create_task(start_bot())

# === Закрытие пула подключений к БД при завершении работы FastAPI ===
@app.on_event("shutdown")
async def on_shutdown():
    # Движок SQLAlchemy закрывается автоматически при завершении процесса,
    # но можно добавить явное закрытие, если требуется.
    # await engine.dispose()
    print("FastAPI завершил работу.")

# === Функция запуска polling ===
async def start_bot():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("🤖 Запускаем бота...")
        await dp.start_polling(bot)
    except Exception as e:
        print("❌ Ошибка при запуске бота:", e)