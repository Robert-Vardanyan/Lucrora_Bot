# Assuming this is your main FastAPI file
import asyncio
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from operator import itemgetter
import json

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.methods import SetWebhook, DeleteWebhook # Импортируем методы для вебхуков

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from dotenv import load_dotenv
from passlib.context import CryptContext

# --- Импортируем наши ORM-модели и утилиты БД ---
from app.database import engine, create_db_tables, drop_db_tables, get_async_session
from app.models import User, Investment, Transaction, Referral # Ensure User is imported

# --- Импортируем новый роутер для инвестиций ---
from app.routers import investments # 

# --- Импортируем функцию для проверки подписи initData ---
from app.utils import check_webapp_signature 

# --- Импортируем реферальную систему ---
from app import referrals

# --- Импортируем роутеры для аутентификации и транзакций ---
from app.transactions import router as transactions_router


# === Загрузка переменных окружения ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL") # URL вашего Mini App
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL") 
DROP_DB_ON_STARTUP = os.getenv("DROP_DB_ON_STARTUP", "False").lower() == "true"


# === Инициализация FastAPI ===
app = FastAPI()

# === Инициализация контекста для хеширования паролей ===
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

    print(f"Получено сообщение от пользователя: {message.from_user.id} - start")
    
    await message.answer(
        "👋 Привет! Нажми кнопку ниже, чтобы открыть Mini App:",
        reply_markup=webapp_button
    )
    await message.delete() # Удаление сообщения может быть нежелательно для пользователя

# === Эндпоинт инициализации Mini App ===
@app.post("/api/init")
async def api_init(request: Request, db: AsyncSession = Depends(get_async_session)):
    # print("Получен запрос на инициализацию Mini App.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    # print(f"Получено тело запроса: {body}")
    init_data = body.get("initData")
    # print(f"Полученные данные инициализации: {init_data}")

    # Проверяем наличие init_data
    if not init_data:
        raise HTTPException(status_code=403, detail="Missing Telegram initData")

    # Теперь, когда мы уверены, что init_data существует, проверяем её подпись
    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")


    # print("Проверка подписи initData (если она была) прошла успешно.")
    user_data_str = dict(parse_qsl(init_data)).get('user')
    # print(f"Извлеченные данные пользователя: {user_data_str}")

    if not user_data_str:
        raise HTTPException(status_code=400, detail="User data not found in initData")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = int(user_info.get('id'))
        first_name = user_info.get('first_name', '')
        last_name = user_info.get('last_name', '')
        username_tg = user_info.get('username', '')
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user data JSON or Telegram ID in initData")

    # print(f"Пользователь: {first_name} {last_name} (ID: {telegram_id}, Username: {username_tg})")
    # print("Проверяю наличие пользователя в базе данных...")
    # print(f"Ищем пользователя с Telegram ID: {type(telegram_id)} - {telegram_id} ")
    user = await db.get(User, telegram_id)
    # print(f"Найден пользователь: {type(user)} {user}")

    if user:
        # print(f"Пользователь {user.username} (ID: {user.id}) уже зарегистрирован.")
        # # Возвращаем данные пользователя в формате, ожидаемом Mini App
        # print(f"Возвращаем данные пользователя: {user}")
        # print(f"Баланс пользователя: main_balance={user.main_balance}, bonus_balance={user.bonus_balance}, lucrum_balance={user.lucrum_balance}")
        # print(f"Инвестировано: {user.total_invested}, Выведено: {user.total_withdrawn}")
        # print(f"Имя пользователя: {user.username}, Имя: {user.first_name}")
        # print(f"Фамилия пользователя: {user.last_name}")
        # print(f"Дата регистрации пользователя: {user.registration_date}")
        return {
            "ok": True,
            "isRegistered": True,
            "main_balance": float(user.main_balance),
            "bonus_balance": float(user.bonus_balance),
            "lucrum_balance": float(user.lucrum_balance),
            "total_invested": float(user.total_invested),
            "total_withdrawn": float(user.total_withdrawn),
            "username": user.username,
            "first_name": user.first_name,
            # Ensure registration_date is sent as a string (e.g., ISO format)
            "registration_date": user.registration_date.isoformat() if user.registration_date else None
        }
    else:
        # print(f"Пользователь {first_name} (ID: {telegram_id}) не найден в базе данных.")
        return {
            "ok": True,
            "isRegistered": False,
            "main_balance": 0.0,
            "bonus_balance": 0.0,
            "lucrum_balance": 0.0,
            "total_invested": 0.0,
            "total_withdrawn": 0.0,
            "username": username_tg or first_name or "Пользователь",
            "first_name": first_name,
            "registration_date": None # No registration date if not registered
        }

# === Регистрация пользователя ===
@app.post("/api/register")
async def api_register(request: Request, db: AsyncSession = Depends(get_async_session)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")
    username = body.get("username")
    password = body.get("password")
    referral_code = body.get("referralCode")

    # 1. Проверяем наличие init_data
    if not init_data:
        raise HTTPException(status_code=403, detail="Missing Telegram initData.")

    # 2. Если init_data есть, проверяем её подпись
    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")


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

    try:
        existing_user_by_id = await db.get(User, telegram_id)
        if existing_user_by_id:
            raise HTTPException(status_code=409, detail="User already registered with this Telegram ID")

        stmt_check_username = select(User).where(User.username == username)
        existing_user_by_username = (await db.execute(stmt_check_username)).scalar_one_or_none()
        if existing_user_by_username:
            raise HTTPException(status_code=409, detail="Username already taken")

        hashed_password_bcrypt = pwd_context.hash(password)

        new_user = User(
            id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            password_hash=hashed_password_bcrypt
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

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
                await db.commit()
                print(f"Добавлена реферальная связь: {referrer.username} (ID: {referrer.id}) пригласил {new_user.username} (ID: {new_user.id})")
            else:
                print(f"Реферальный код '{referral_code}' не найден.")

        print(f"Пользователь {username} (ID: {telegram_id}) успешно зарегистрирован в БД.")

        return {
            "ok": True,
            "message": "Registration successful!",
            "user_id": str(telegram_id),
            "username": username
        }
    except IntegrityError as e:
        await db.rollback()
        print(f"Ошибка целостности при регистрации пользователя: {e}")
        if "users_username_key" in str(e):
            raise HTTPException(status_code=409, detail="Username already taken.")
        raise HTTPException(status_code=500, detail=f"Database integrity error during registration: {e}")
    except Exception as e:
        await db.rollback()
        print(f"Общая ошибка при регистрации пользователя: {e}")
        raise HTTPException(status_code=500, detail=f"Database error during registration: {e}")

# === ЭНДПОИНТ: Авторизация пользователя ===
@app.post("/api/login")
async def api_login(request: Request, db: AsyncSession = Depends(get_async_session)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")
    username = body.get("username")
    password = body.get("password")

    # 1. Проверяем наличие init_data
    if not init_data:
        raise HTTPException(status_code=403, detail="Missing Telegram initData.")

    # 2. Если init_data есть, проверяем её подпись
    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=400, detail="User data not found in initData")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = int(user_info.get('id'))
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user data JSON or Telegram ID in initData")

    stmt_user = select(User).where(User.username == username)
    user = (await db.execute(stmt_user)).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    if not pwd_context.verify(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    if user.id != telegram_id:
        raise HTTPException(status_code=403, detail="Account not linked to this Telegram ID. Please re-register or contact support.")

    print(f"Пользователь {username} (ID: {telegram_id}) успешно вошел в систему.")

    return {
        "ok": True,
        "message": "Login successful!",
        "isRegistered": True,
        "main_balance": float(user.main_balance),
        "bonus_balance": float(user.bonus_balance),
        "lucrum_balance": float(user.lucrum_balance),
        "total_invested": float(user.total_invested),
        "total_withdrawn": float(user.total_withdrawn),
        "username": user.username,
        "first_name": user.first_name,
        "registration_date": user.registration_date.isoformat() if user.registration_date else None
    }

# === Эндпоинт для повторной отправки письма (если нужно) ===
@app.post("/api/resend_email")
async def api_resend_email(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")
    email = body.get("email")

    # 1. Проверяем наличие init_data
    if not init_data:
        raise HTTPException(status_code=403, detail="Missing Telegram initData.")

    # 2. Если init_data есть, проверяем её подпись
    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")

    print(f"Запрос на повторную отправку письма на {email}")
    if email:
        return {"ok": True, "message": "Email has been sent."}
    else:
        raise HTTPException(status_code=400, detail="Email is required.")

# === Эндпоинт для обработки вебхуков от Telegram ===
# Этот эндпоинт будет получать все обновления от Telegram
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


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
        raise

    # --- Настройка вебхуков ---
    if not BOT_TOKEN or not BASE_WEBHOOK_URL:
        print("❌ Не указан BOT_TOKEN или BASE_WEBHOOK_URL. Вебхуки не будут настроены.")
        # Возможно, здесь стоит выйти из приложения или выбросить исключение
        return 

    webhook_url = f"{BASE_WEBHOOK_URL}/webhook"
    print(f"Устанавливаю вебхук на: {webhook_url}")
    try:
        await bot(SetWebhook(url=webhook_url))
        print("✅ Вебхук успешно установлен.")
    except Exception as e:
        print(f"❌ Ошибка при установке вебхука: {e}")
        # Если вебхук не удалось установить, это критическая ошибка для бота
        # Вы можете решить, стоит ли здесь остановить запуск приложения
        raise

    print("Aiogram вебхуки настроены и ожидают обновлений.")


# === Закрытие пула подключений к БД при завершении работы FastAPI ===
@app.on_event("shutdown")
async def on_shutdown():
    print("FastAPI завершил работу.")
    # При завершении работы рекомендуется удалить вебхук, чтобы избежать проблем.
    print("Удаляю вебхук...")
    try:
        await bot(DeleteWebhook())
    except Exception as e:
        print(f"❌ Ошибка при удалении вебхука: {e}")
    
    await bot.session.close() # Закрываем сессию бота при завершении работы

# === Регистрация роутеров  ===
app.include_router(investments.router) 
app.include_router(referrals.router)  
app.include_router(transactions_router)