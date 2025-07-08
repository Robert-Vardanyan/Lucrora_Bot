# app/routers/investments.py

import hmac
import hashlib
import json
from urllib.parse import parse_qsl
from operator import itemgetter
from datetime import datetime, timedelta, timezone # Добавляем timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from decimal import Decimal

from app.database import get_async_session
from app.models import InvestmentPackage, User, Investment, Transaction # Исправлено: Investment вместо UserInvestment
from app.utils import check_webapp_signature

import os
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# !!! ВАЖНО: Получаем SECRET_PAYMENT_TOKEN из переменных окружения
# ЭТО ОЧЕНЬ ВАЖНО: ЭТО ДОЛЖЕН БЫТЬ ТВОЙ СЕКРЕТНЫЙ ПЛАТЕЖНЫЙ ТОКЕН, ПОЛУЧЕННЫЙ ИЗ BOTFATHER
# В РЕЖИМЕ "TEST" (ТЕСТОВЫЙ ТОКЕН) ИЛИ "LIVE" (БОЕВОЙ ТОКЕН)
TELEGRAM_PAYMENT_PROVIDER_TOKEN = os.getenv("TELEGRAM_PAYMENT_PROVIDER_TOKEN") 

if not TELEGRAM_PAYMENT_PROVIDER_TOKEN:
    print("ВНИМАНИЕ: Переменная окружения TELEGRAM_PAYMENT_PROVIDER_TOKEN не установлена. Платежи Telegram Stars не будут работать.")
    # raise ValueError("Переменная окружения TELEGRAM_PAYMENT_PROVIDER_TOKEN не установлена. Это необходимо для платежей Telegram Stars.")


router = APIRouter()


# --- Зависимость для получения Telegram User ID из initData ---
async def get_telegram_user_id_from_init_data(init_data: str) -> int:
    print(f"DEBUG: Validating initData. Length: {len(init_data)}")
    if not init_data or not check_webapp_signature(init_data, BOT_TOKEN):
        print("DEBUG: initData validation failed.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверные или отсутствующие Telegram initData.")

    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        print("DEBUG: User data not found in initData.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Данные пользователя не найдены в initData.")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = int(user_info.get('id'))
        print(f"DEBUG: Successfully parsed Telegram ID: {telegram_id}")
        return telegram_id
    except (json.JSONDecodeError, ValueError) as e:
        print(f"DEBUG: Error parsing user data from initData: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Неверный формат данных пользователя или Telegram ID в initData: {e}")

# --- Pydantic модели для валидации данных ---

# Модель для ответа при получении списка пакетов
class InvestmentPackageResponse(BaseModel):
    id: int
    name: str
    min_amount: Decimal
    max_amount: Decimal | None
    daily_roi_percentage: Decimal
    duration_days: int
    description: str | None
    is_active: bool

    class Config:
        from_attributes = True

# Модель для запроса на создание инвойса Stars
class CreateStarsInvoiceRequest(BaseModel):
    package_id: int
    package_cost_lcr: Decimal
    initData: str

class CreateStarsInvoiceResponse(BaseModel):
    ok: bool
    invoice_link: str # <--- Теперь возвращаем полную ссылку
    invoice_payload: str
    stars_amount: int
    message: str

# --- Эндпоинты API ---

@router.get("/api/investment_packages", response_model=list[InvestmentPackageResponse])
async def get_investment_packages(db: AsyncSession = Depends(get_async_session)):
    """
    Возвращает список всех активных инвестиционных пакетов.
    """
    try:
        stmt = select(InvestmentPackage).where(InvestmentPackage.is_active == True).order_by(InvestmentPackage.min_amount)
        result = await db.execute(stmt)
        packages = result.scalars().all()
        return packages
    except Exception as e:
        print(f"Ошибка при получении инвестиционных пакетов: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Произошла ошибка на сервере при получении пакетов.")

# --- НОВЫЙ ЭНДПОИНТ: Создание инвойса для Telegram Stars ---
@router.post("/api/create_stars_invoice", response_model=CreateStarsInvoiceResponse)
async def create_stars_invoice_endpoint(
    request_body: CreateStarsInvoiceRequest, 
    db: AsyncSession = Depends(get_async_session)
):
    print(f"Received request_body (Pydantic parsed): package_id={request_body.package_id}, "
          f"package_cost_lcr={request_body.package_cost_lcr} (type: {type(request_body.package_cost_lcr)}), "
          f"initData_len={len(request_body.initData)}")

    # 1. Верификация initData и получение Telegram User ID
    telegram_user_id = await get_telegram_user_id_from_init_data(request_body.initData)
    print(f"DEBUG: User ID from initData: {telegram_user_id}")

    # 2. Получение пользователя из БД
    user = await db.get(User, telegram_user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден в системе.")
    print(f"DEBUG: Retrieved user from DB: {user}")

    # 3. Получение деталей инвестиционного пакета из БД
    investment_package = await db.get(InvestmentPackage, request_body.package_id)
    if not investment_package or not investment_package.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Инвестиционный пакет не найден или неактивен.")
    print(f"DEBUG: Retrieved package from DB: {investment_package}")

    # 4. Валидация стоимости пакета, пришедшей с фронтенда
    # Преобразуем Decimal(100.00) в int 100 для сравнения, если min_amount - целое число
    # Или лучше сравним как Decimal, квантуя их
    if investment_package.min_amount.quantize(Decimal('0.01')) != request_body.package_cost_lcr.quantize(Decimal('0.01')):
        print(f"DEBUG: Frontend cost: {request_body.package_cost_lcr}, Backend cost: {investment_package.min_amount}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверная стоимость пакета. Пожалуйста, обновите страницу.")

    # 5. Конвертация LCR в Stars (пример: 1 LCR = 10 Stars)
    # Используйте Decimal для точных расчетов
    LCR_TO_STARS_RATE = Decimal('10') # Пусть 1 LCR = 10 Stars
    stars_amount = int((request_body.package_cost_lcr * LCR_TO_STARS_RATE).quantize(Decimal('1'))) # Округляем до целых Stars
    
    if stars_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Количество Stars для оплаты должно быть положительным.")

    # 6. Формирование invoice_payload
    # Этот payload будет возвращен Telegram после успешной оплаты
    # Формат: "investpurchase:<user_id>:<package_id>:<timestamp>"
    timestamp = int(func.now().timestamp())
    invoice_payload = f"investpurchase:{telegram_user_id}:{request_body.package_id}:{timestamp}"
    print(f"DEBUG: Generated invoice_payload: {invoice_payload}")

    # 7. Генерация ссылки на инвойс через Telegram Bot API
    # Используйте ваш BOT_TOKEN (TOKEN_PAYMENT_PROVIDER_TEST_STARS или боевой)
    # NOTE: Используйте TEST токен для теста, а боевой для продакшена. 
    # В данном случае, это ваш "Telegram Payment Provider Token"
    payment_provider_token = BOT_TOKEN # Ваш тестовый или боевой токен провайдера

    if not payment_provider_token:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Telegram Payment Provider Token не настроен.")

    telegram_api_url = f"https://api.telegram.org/bot{payment_provider_token}/createInvoiceLink"
    
    # Подготовка данных для запроса createInvoiceLink
    # Убедитесь, что 'payload' совпадает с вашей `invoice_payload`
    # 'title' и 'description' будут отображаться пользователю в окне оплаты
    # 'price' должен быть в Stars
    invoice_params = {
        "title": f"Покупка '{investment_package.name}'",
        "description": f"Инвестиционный пакет {investment_package.name} за {request_body.package_cost_lcr} LCR",
        "payload": invoice_payload,
        "provider_token": payment_provider_token, # Это ваш Telegram Payment Provider Token
        "currency": "XTR", # Всегда XTR для Telegram Stars
        "prices": json.dumps([{"label": f"{request_body.package_cost_lcr} LCR ({stars_amount} Stars)", "amount": stars_amount}]),
        "max_tip_amount": 0, # Опционально, если не хотите чаевые
        "suggested_tip_amounts": [], # Опционально
        "start_parameter": f"invest_{request_body.package_id}", # Используется для deeplink, если пользователь запустит бота по ссылке инвойса
        "photo_url": "https://lucrora-bot.onrender.com/static/icon.png", # Замените на реальную ссылку к картинке вашего пакета
        "photo_width": 500,
        "photo_height": 500,
        "need_name": False, # Опционально, если нужны доп. данные
        "need_phone_number": False,
        "need_email": False,
        "send_email_to_provider": False,
        "send_phone_number_to_provider": False,
        "is_flexible": False # Для Stars всегда False
    }

    async with httpx.AsyncClient() as client:
        try:
            # Telegram API ожидает Content-Type: application/json для большинства методов, но для форм можно и multipart/form-data
            # Однако, для `createInvoiceLink` обычно JSON POST body работает
            tg_response = await client.post(telegram_api_url, json=invoice_params)
            tg_response.raise_for_status() # Выбросит исключение для 4xx/5xx ответов

            tg_data = tg_response.json()
            print(f"DEBUG: Telegram API createInvoiceLink response: {tg_data}")

            if tg_data.get('ok') and 'result' in tg_data:
                invoice_link = tg_data['result']
                print(f"DEBUG: Generated invoice_link: {invoice_link}")
                return CreateStarsInvoiceResponse(
                    ok=True,
                    invoice_link=invoice_link,
                    invoice_payload=invoice_payload,
                    stars_amount=stars_amount,
                    message=f"Готовность к оплате {stars_amount} ⭐ за пакет '{investment_package.name}'."
                )
            else:
                error_description = tg_data.get('description', 'Неизвестная ошибка Telegram API')
                print(f"ERROR: Telegram API createInvoiceLink failed: {error_description}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ошибка Telegram API: {error_description}")

        except httpx.RequestError as e:
            print(f"ERROR: Network error during Telegram API call: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Сетевая ошибка при обращении к Telegram API: {e}")
        except httpx.HTTPStatusError as e:
            print(f"ERROR: HTTP error from Telegram API: {e.response.status_code} - {e.response.text}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ошибка HTTP от Telegram API: {e.response.status_code} - {e.response.text}")
        except json.JSONDecodeError as e:
            print(f"ERROR: JSON decode error from Telegram API response: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ошибка декодирования JSON ответа Telegram API: {e}")
        except Exception as e:
            print(f"ERROR: Unexpected error in create_stars_invoice_endpoint: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Произошла непредвиденная ошибка: {e}")

# --- НОВЫЙ ЭНДПОИНТ: Обработка колбэков Telegram Payments (Webhook) ---
# Этот эндпоинт будет вызываться Telegram после успешной оплаты Stars
@router.post("/telegram_payment_webhook")
async def telegram_payment_webhook(request: Request, db: AsyncSession = Depends(get_async_session)):
    """
    Обрабатывает колбэки от Telegram Payments API (Stars).
    """
    try:
        update = await request.json()
        print(f"Получен Telegram Payment Webhook: {json.dumps(update, indent=2)}")

        if "pre_checkout_query" in update:
            # Это запрос Telegram о предварительной проверке платежа
            query = update["pre_checkout_query"]
            invoice_payload = query.get("invoice_payload")
            telegram_user_id = query["from"]["id"]
            total_amount_stars = query["total_amount"] # Сумма в Stars, которую оплачивает пользователь

            # Разбираем наш payload: "order_prefix:user_id:package_id:timestamp"
            try:
                parts = invoice_payload.split(':')
                if len(parts) != 4 or parts[0] != "investpurchase":
                    raise ValueError("Invalid format")
                
                order_prefix, user_id_str, package_id_str, timestamp_str = parts
                user_id = int(user_id_str)
                package_id = int(package_id_str)
            except ValueError as e:
                print(f"Invalid invoice_payload format: {invoice_payload}. Error: {e}")
                # Если payload не удалось распарсить, это ошибка.
                # Возвращаем False, чтобы Telegram отказал в платеже.
                return {"ok": False, "error": "Неверный формат идентификатора платежа."}

            # TODO: Дополнительные проверки (опционально, но рекомендуется для безопасности):
            # 1. Проверить, существует ли пользователь с telegram_user_id (должен совпадать с user_id)
            # 2. Проверить, существует ли InvestmentPackage с package_id
            # 3. Проверить, соответствует ли total_amount_stars ожидаемой стоимости пакета
            #    (получить InvestmentPackage.min_amount и конвертировать в Stars, как в /create_stars_invoice)
            
            user = await db.get(User, user_id)
            investment_package = await db.get(InvestmentPackage, package_id)
            
            if not user:
                return {"ok": False, "error": "Пользователь не найден."}
            if not investment_package or not investment_package.is_active:
                return {"ok": False, "error": "Инвестиционный пакет не найден или неактивен."}

            stars_per_lcr_rate = Decimal("10") # ДОЛЖНО СОВПАДАТЬ С ЗНАЧЕНИЕМ В /create_stars_invoice
            expected_stars_amount = int(investment_package.min_amount * stars_per_lcr_rate)

            if total_amount_stars != expected_stars_amount:
                print(f"Mismatch in stars amount. Expected: {expected_stars_amount}, Got: {total_amount_stars}")
                return {"ok": False, "error": "Неверная сумма Stars для покупки."}

            # Если все проверки прошли успешно, отвечаем Telegram, что все ок
            return {"ok": True} 

        elif "message" in update and "successful_payment" in update["message"]:
            # Это подтверждение успешного платежа
            successful_payment = update["message"]["successful_payment"]
            invoice_payload = successful_payment["invoice_payload"]
            telegram_user_id = update["message"]["from"]["id"]
            stars_amount_paid = successful_payment["total_amount"]
            telegram_payment_charge_id = successful_payment["telegram_payment_charge_id"]
            # provider_payment_charge_id = successful_payment.get("provider_payment_charge_id") # Для других провайдеров

            print(f"Successful payment for invoice_payload: {invoice_payload}")
            
            # Разбираем наш payload
            try:
                parts = invoice_payload.split(':')
                if len(parts) != 4 or parts[0] != "investpurchase":
                    raise ValueError("Invalid format")
                
                order_prefix, user_id_str, package_id_str, timestamp_str = parts
                user_id = int(user_id_str)
                package_id = int(package_id_str)
            except ValueError as e:
                print(f"Invalid invoice_payload format on successful payment: {invoice_payload}. Error: {e}")
                return {"ok": False} # Если payload некорректен, не можем обработать

            # 1. Получаем пользователя и пакет
            user = await db.get(User, user_id)
            investment_package = await db.get(InvestmentPackage, package_id)

            if not user or not investment_package or not investment_package.is_active:
                print(f"User {user_id} or Package {package_id} not found/inactive for successful payment.")
                # Логируем, но возвращаем True, чтобы Telegram не пытался повторно, так как деньги списаны.
                # Далее следует уведомить администратора.
                return {"ok": True} 

            # 2. Проверяем, не была ли транзакция уже обработана (идемпотентность)
            # Ищем существующую инвестицию с данным stars_payment_charge_id
            existing_investment = await db.execute(
                select(Investment).where(Investment.stars_payment_charge_id == telegram_payment_charge_id)
            )
            if existing_investment.scalar_one_or_none():
                print(f"Duplicate payment for charge ID: {telegram_payment_charge_id}. Skipping.")
                return {"ok": True} # Уже обработано, просто отвечаем OK.

            try:
                # 3. Создаем запись о новом инвестиционном плане пользователя
                start_date = datetime.now(timezone.utc)
                end_date = start_date + timedelta(days=investment_package.duration_days)

                new_user_investment = Investment( # Использование Investment, как в модели
                    user_id=user.id,
                    package_id=investment_package.id,
                    amount_invested=investment_package.min_amount, # Сумма инвестиции в LCR
                    start_date=start_date,
                    end_date=end_date,
                    daily_roi_percentage=investment_package.daily_roi_percentage, # Сохраняем ROI на момент покупки
                    stars_payment_charge_id=telegram_payment_charge_id, # Сохраняем ID платежа Telegram
                    status='active'
                )
                db.add(new_user_investment)

                # 4. Обновляем total_invested пользователя (если это основной функционал)
                user.total_invested += investment_package.min_amount

                # 5. Создаем запись о транзакции в истории
                purchase_transaction = Transaction(
                    user_id=user.id,
                    type='investment_purchase_stars', # Новый тип транзакции
                    amount=investment_package.min_amount, # Сумма в LCR для истории
                    currency='₤s', # Валюта LCR
                    timestamp=now_utc, # Используем теперь now_utc из импортов
                    status='completed',
                    description=f"Покупка инвестиционного пакета '{investment_package.name}' за {stars_amount_paid} ⭐. Stars Charge ID: {telegram_payment_charge_id}",
                    txid=telegram_payment_charge_id # Сохраняем ID платежа Stars как TXID
                )
                db.add(purchase_transaction)

                await db.commit()
                await db.refresh(user)
                await db.refresh(new_user_investment)
                await db.refresh(purchase_transaction)

                print(f"Investment package {investment_package.name} successfully purchased by user {user.id} for {stars_amount_paid} Stars. Investment ID: {new_user_investment.id}")
                return {"ok": True} # Сообщаем Telegram, что платеж успешно обработан

            except Exception as e:
                await db.rollback()
                print(f"CRITICAL ERROR: Failed to process successful payment for user {user.id}, package {package_id} with charge ID {telegram_payment_charge_id}: {e}")
                # Если произошла ошибка здесь, это серьезно: пользователь заплатил, а мы не обработали.
                # Нужно уведомить администратора и предоставить средства для ручной компенсации.
                # Возвращаем True, чтобы Telegram не пытался повторно, но логируем критическую ошибку.
                return {"ok": True} 

        else:
            print(f"Unknown webhook update received: {json.dumps(update, indent=2)}")
            return {"ok": False} # Неизвестный тип обновления

    except Exception as e:
        print(f"General error in telegram_payment_webhook handler: {e}")
        # Если здесь произошла ошибка, это может быть проблема с парсингом JSON или другая непредвиденная ошибка.
        # В этом случае Telegram может пытаться повторно отправить вебхук.
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")