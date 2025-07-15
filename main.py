# Assuming this is your main FastAPI file
import asyncio
import email
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from operator import itemgetter
import json
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.methods import SetWebhook, DeleteWebhook # Импортируем методы для вебхуков

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Query # Импортируем Query для параметров GET запроса
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from dotenv import load_dotenv
from passlib.context import CryptContext

# --- Импортируем наши ORM-модели и утилиты БД ---
from app.database import engine, create_db_tables, drop_db_tables, get_async_session
from app.models import User, UserAccountStatus, UserRole, Investment, Transaction, Referral # Ensure User is imported

# --- Импортируем новый роутер для инвестиций ---
from app.routers import investments #

# --- Импортируем функцию для проверки подписи initData ---
from app.utils import check_webapp_signature

# --- Импортируем реферальную систему ---
from app import referrals

# --- Импортируем роутеры для аутентификации и транзакций ---
from app.transactions import router as transactions_router

# --- Импортируем роутер для игр ---
from app.routers import games

# === Загрузка переменных окружения ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL") # URL вашего Mini App
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")
DROP_DB_ON_STARTUP = os.getenv("DROP_DB_ON_STARTUP", "False").lower() == "true"

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
REFRESH_TOKEN_SECRET_KEY = os.getenv("REFRESH_TOKEN_SECRET_KEY") 
ALGORITHM = "HS256"

# === ВРЕМЯ ЖИЗНИ ТОКЕНОВ ===
ACCESS_TOKEN_EXPIRE_MINUTES = 120 # Например, 30 минут
REFRESH_TOKEN_EXPIRE_DAYS = 14   # Например, 7 дней для "Remember Me"

# === Инициализация контекста для хеширования паролей ===
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")



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


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ JWT (ОБНОВЛЕНО) ===

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM) # Используем JWT_SECRET_KEY
    return encoded_jwt

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, REFRESH_TOKEN_SECRET_KEY, algorithm=ALGORITHM) # Используем REFRESH_TOKEN_SECRET_KEY
    return encoded_jwt

def verify_access_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid Access Token: User ID missing")
        return int(user_id)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid Access Token: Signature or expiration invalid")

def verify_refresh_token(token: str):
    try:
        payload = jwt.decode(token, REFRESH_TOKEN_SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid Refresh Token: User ID missing")
        return int(user_id)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid Refresh Token: Signature or expiration invalid")

# --- Зависимость для получения токена из заголовка Authorization ---
security = HTTPBearer()


# ================================================
# ================================================

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


# ================================================
# ================================================

# === АУТЕНТИФИКАЦИЯ / РЕГИСТРАЦИЯ / СЕССИИ ===

@app.post("/api/register")
async def api_register(request: Request, db: AsyncSession = Depends(get_async_session)):
    try:
        data = await request.json()
        init_data = data.get("initData")
        username = data.get("username")
        password = data.get("password")
        remember_me = data.get("rememberMe", False) # НОВОЕ: флаг "Remember Me"
        phone_number = data.get("phone_number") 
        email = data.get("email")               

        if not init_data or not username or not password:
            raise HTTPException(status_code=400, detail="Missing required data.")

        if not check_webapp_signature(init_data, BOT_TOKEN):
            raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")

        user_data_tg_str = dict(parse_qsl(init_data)).get('user')
        if not user_data_tg_str:
            raise HTTPException(status_code=400, detail="Telegram user data not found in initData")

        user_info_tg = json.loads(user_data_tg_str)
        telegram_id = int(user_info_tg.get('id'))
        first_name = user_info_tg.get('first_name')
        last_name = user_info_tg.get('last_name')

        existing_user = await db.execute(select(User).filter_by(id=telegram_id))
        if existing_user.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="User already registered.")

        # Хэширование пароля
        hashed_password = pwd_context.hash(password)

        new_user = User(
            id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            password_hash=hashed_password,
            status=UserAccountStatus.active, # Дефолтный статус активный
            role=UserRole.user, # Дефолтная роль пользователь
            phone_number=phone_number,
            email=email
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # === ГЕНЕРАЦИЯ ОБОИХ ТОКЕНОВ ===
        access_token = create_access_token(data={"sub": str(new_user.id)})
        refresh_token = create_refresh_token(data={"sub": str(new_user.id)})

        print(f"Пользователь {username} (ID: {telegram_id}) успешно зарегистрирован. Выданы токены.")

        return {
            "ok": True,
            "message": "Registration successful!",
            "user_id": str(telegram_id),
            "username": username,
            "access_token": access_token,
            "refresh_token": refresh_token, # НОВОЕ: Отправляем Refresh Token
            "token_type": "bearer",
            "isRegistered": True,
            "main_balance": float(new_user.main_balance),
            "bonus_balance": float(new_user.bonus_balance),
            "lucrum_balance": float(new_user.lucrum_balance),
            "total_invested": float(new_user.total_invested),
            "total_withdrawn": float(new_user.total_withdrawn),
            "first_name": new_user.first_name,
            "last_name": new_user.last_name,
            "status": new_user.status.value,
            "role": new_user.role.value,
            "email": new_user.email,
            "phone_number": new_user.phone_number
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error during registration: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

# ================================================

@app.post("/api/login")
async def api_login(request: Request, db: AsyncSession = Depends(get_async_session)):
    try:
        data = await request.json()
        init_data = data.get("initData")
        email = data.get("email")
        password = data.get("password")
        remember_me = data.get("rememberMe", False) # НОВОЕ: флаг "Remember Me"

        if not init_data or not email or not password:
            raise HTTPException(status_code=400, detail="Missing required data.")

        if not check_webapp_signature(init_data, BOT_TOKEN):
            raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")

        user_data_tg_str = dict(parse_qsl(init_data)).get('user')
        if not user_data_tg_str:
            raise HTTPException(status_code=400, detail="Telegram user data not found in initData")

        user_info_tg = json.loads(user_data_tg_str)
        telegram_id_from_tg = int(user_info_tg.get('id'))

        user_query = await db.execute(select(User).filter_by(email=email))
        user = user_query.scalar_one_or_none()

        if not user or not pwd_context.verify(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid username or password.")

        # Проверяем, что Telegram ID из initData совпадает с ID пользователя в БД
        if user.id != telegram_id_from_tg:
            print(f"Предупреждение: Пользователь {email} (ID: {user.id}) пытается войти с другим Telegram ID ({telegram_id_from_tg}).")
            # Можно запретить вход или отправить уведомление. Для простоты сейчас запретим.
            raise HTTPException(status_code=403, detail="Telegram ID mismatch. Please login from the correct Telegram account.")

        # Обновляем статус и дату последнего входа
        user.last_login_date = datetime.now(timezone.utc)
        user.status = UserAccountStatus.active
        await db.commit()
        await db.refresh(user)

        # === ГЕНЕРАЦИЯ ОБОИХ ТОКЕНОВ ===
        access_token = create_access_token(data={"sub": str(user.id)})
        refresh_token = create_refresh_token(data={"sub": str(user.id)})

        print(f"Пользователь {email} (ID: {user.id}) успешно вошел в систему. Выданы токены.")

        return {
            "ok": True,
            "message": "Login successful!",
            "isRegistered": True,
            "access_token": access_token,
            "refresh_token": refresh_token, # НОВОЕ: Отправляем Refresh Token
            "token_type": "bearer",
            "main_balance": float(user.main_balance),
            "bonus_balance": float(user.bonus_balance),
            "lucrum_balance": float(user.lucrum_balance),
            "total_invested": float(user.total_invested),
            "total_withdrawn": float(user.total_withdrawn),
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "registration_date": user.registration_date.isoformat() if user.registration_date else None,
            "status": user.status.value,
            "role": user.role.value
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error during login: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


# ================================================

# Обновление Access Token с использованием Refresh Token
@app.post("/api/refresh-token")
async def refresh_access_token(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
    credentials: HTTPAuthorizationCredentials = Depends(security) # Здесь ожидаем Refresh Token в заголовке
):
    """
    Обновляет Access Token, используя Refresh Token.
    """
    print("Получен запрос на обновление токена.")
    try:
        refresh_token = credentials.credentials
        user_id_from_refresh = verify_refresh_token(refresh_token) # Верифицируем Refresh Token

        user = await db.get(User, user_id_from_refresh)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        # Дополнительные проверки (например, если Refresh Token был отозван в БД, что требует отдельной таблицы RefreshTokens)
        # Для простоты, пока просто проверяем статус пользователя.
        if user.status == UserAccountStatus.banned:
            raise HTTPException(status_code=403, detail="Account is banned. Access denied.")
        if user.status == UserAccountStatus.logged_out:
            raise HTTPException(status_code=401, detail="Account was logged out from another session. Please re-login.")

        # Генерируем новый Access Token
        new_access_token = create_access_token(data={"sub": str(user.id)})
        print(f"Access Token обновлен для пользователя {user.username} (ID: {user.id}).")

        return {
            "ok": True,
            "access_token": new_access_token,
            "token_type": "bearer",
            "message": "Access token refreshed."
        }
    except HTTPException as e:
        print(f"Ошибка при обновлении токена: {e.detail}")
        raise e
    except Exception as e:
        print(f"Неизвестная ошибка при обновлении токена: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error during token refresh: {e}")


# ================================================


# Проверка сессии (используется при запуске Mini App)
@app.post("/api/check-session")
async def check_user_session(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
    credentials: HTTPAuthorizationCredentials = Depends(security) # Ожидаем Access Token
):
    """
    Проверяет валидность Access Token сессии и возвращает данные пользователя, если сессия активна.
    Также принимает initData для дополнительной верификации Telegram ID, связанного с токеном.
    """
    print("Получен запрос на проверку сессии.")
    try:
        body = await request.json()
        init_data = body.get("initData")
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    if not init_data:
        raise HTTPException(status_code=403, detail="Missing Telegram initData")

    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")

    user_data_tg_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_tg_str:
        raise HTTPException(status_code=400, detail="Telegram user data not found in initData")

    try:
        user_info_tg = json.loads(user_data_tg_str)
        telegram_id_from_tg = int(user_info_tg.get('id'))
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid Telegram user data JSON or ID.")

    try:
        user_id_from_access = verify_access_token(credentials.credentials) # Верифицируем Access Token

        if user_id_from_access != telegram_id_from_tg:
            print(f"Предупреждение: ID из токена ({user_id_from_access}) не совпадает с ID из initData ({telegram_id_from_tg}).")
            raise HTTPException(status_code=403, detail="Access Token does not match Telegram user ID.")

        user = await db.get(User, user_id_from_access)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        # Проверки статуса аккаунта
        if user.status == UserAccountStatus.banned:
            raise HTTPException(status_code=403, detail="Account is banned. Access denied.")
        if user.status == UserAccountStatus.logged_out:
            # Если статус logged_out, даже если access token валиден, мы хотим принудительно разлогинить
            raise HTTPException(status_code=401, detail="Account was logged out from another session. Please re-login.")
        if user.status == UserAccountStatus.inactive:
            raise HTTPException(status_code=401, detail="Account is inactive. Please re-login.")


        print(f"Сессия для пользователя {user.username} (ID: {user.id}) подтверждена. Статус: {user.status.value}, Роль: {user.role.value}")
        return {
            "ok": True,
            "isLoggedIn": True,
            "message": "Session is valid.",
            "user_id": str(user.id),
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "main_balance": float(user.main_balance),
            "bonus_balance": float(user.bonus_balance),
            "lucrum_balance": float(user.lucrum_balance),
            "total_invested": float(user.total_invested),
            "total_withdrawn": float(user.total_withdrawn),
            "registration_date": user.registration_date.isoformat() if user.registration_date else None,
            "status": user.status.value,
            "role": user.role.value
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Ошибка при проверке сессии: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


# ================================================


# ОБНОВЛЕННЫЙ ЭНДПОИНТ: Выход из системы
@app.post("/api/logout")
async def api_logout(
    db: AsyncSession = Depends(get_async_session),
    credentials: HTTPAuthorizationCredentials = Depends(security) # Ожидаем Access Token
):
    """
    Выходит из системы, помечая пользователя как 'logged_out' в БД.
    Отозвать refresh token можно, если хранить их в БД и удалять при логауте.
    Пока просто устанавливаем статус.
    """
    try:
        user_id = verify_access_token(credentials.credentials) # Верифицируем Access Token
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        user.status = UserAccountStatus.logged_out # Устанавливаем статус "вышел"
        await db.commit()
        print(f"Пользователь {user.username} (ID: {user.id}) вышел из системы (статус в БД: logged_out).")
        return {"ok": True, "message": "Successfully logged out."}
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Ошибка при выходе из системы: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


# ================================================

# Для верификации Telegram initData
@app.post("/api/verify-telegram-init")
async def verify_telegram_init(request: Request):
    """
    Верифицирует Telegram initData и возвращает данные пользователя Telegram.
    НЕ делает запросов к вашей БД.
    """
    try:
        data = await request.json()
        init_data = data.get("initData")

        if not init_data:
            raise HTTPException(status_code=400, detail="Missing initData")

        if not check_webapp_signature(init_data, BOT_TOKEN):
            raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")

        user_data_tg_str = dict(parse_qsl(init_data)).get('user')
        if not user_data_tg_str:
            raise HTTPException(status_code=400, detail="Telegram user data not found in initData")

        user_info_tg = json.loads(user_data_tg_str)
        # Возвращаем только публичные данные Telegram пользователя
        return {
            "ok": True,
            "telegram_id": user_info_tg.get('id'),
            "message": "Telegram initData verified."
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error verifying Telegram initData: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


# ================================================

# НОВЫЙ ЭНДПОИНТ: Проверка зарегистрирован ли пользователь в нашей БД по Telegram ID
@app.post("/api/is-user-registered")
async def is_user_registered(request: Request, db: AsyncSession = Depends(get_async_session)):
    """
    Проверяет, зарегистрирован ли пользователь в нашей системе по Telegram ID.
    Предполагает, что initData уже проверена.
    """
    try:
        data = await request.json()
        telegram_id = data.get("telegram_id") # Получаем ID, который уже был верифицирован

        if not telegram_id:
            raise HTTPException(status_code=400, detail="Missing Telegram ID.")

        user = await db.get(User, telegram_id)
        if user:
            print(f"Пользователь с ID {telegram_id} найден в БД. Статус: {user.status.value}, Роль: {user.role.value}")
            return {
                "ok": True,
                "isRegistered": True,
                "username": user.username,
                "status": user.status.value, # Возвращаем статус и роль
                "role": user.role.value
            }
        else:
            return {
                "ok": True,
                "isRegistered": False,
                "message": "User not registered in our system."
            }
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error checking user registration: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")





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
app.include_router(games.router)