# app/routers/investments.py

import hmac
import hashlib
import json
from urllib.parse import parse_qsl
from operator import itemgetter
from datetime import datetime, timedelta, timezone # Добавляем timezone

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
    package_cost_lcr: Decimal # Стоимость пакета, пришедшая с фронтенда (в LCR)
    initData: str # initData для верификации пользователя

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
@router.post("/api/create_stars_invoice")
async def create_stars_invoice(
    request_body: CreateStarsInvoiceRequest, 
    db: AsyncSession = Depends(get_async_session)
):
    """
    Создает инвойс для покупки инвестиционного пакета через Telegram Stars.
    Возвращает payload для tg.openInvoice().
    """
    print(f"Received request_body (Pydantic parsed): package_id={request_body.package_id}, "
          f"package_cost_lcr={request_body.package_cost_lcr} (type: {type(request_body.package_cost_lcr)}), "
          f"initData_len={len(request_body.initData)}")
    
    # initData уже валидируется в get_telegram_user_id, но нам нужен сам объект пользователя
    print(f"DEBUG: Validating initData length: {len(request_body.initData)}")
    user_id = await get_telegram_user_id_from_init_data(request_body.initData)
    print(f"DEBUG: User ID from initData: {user_id}")
    user = await db.get(User, user_id)
    print(f"DEBUG: Retrieved user from DB: {user}") 
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден.")

    # 1. Получаем информацию о пакете
    investment_package = await db.get(InvestmentPackage, request_body.package_id)
    if not investment_package or not investment_package.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Инвестиционный пакет не найден или неактивен.")
    
    # Убедимся, что запрошенная стоимость совпадает со стоимостью пакета
    # Это важная проверка безопасности, чтобы фронтенд не отправил некорректную сумму
    if investment_package.min_amount != request_body.package_cost_lcr:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверная стоимость пакета. Пожалуйста, обновите страницу.")

    # 2. Определяем стоимость в Telegram Stars
    # !!! ВАЖНО: Здесь тебе нужно определить логику конвертации LCR в Stars.
    # Пример: 1 LCR = 10 Stars. Stars должны быть целыми числами!
    stars_per_lcr_rate = Decimal("10") # НАСТРОЙ ЭТО ЗНАЧЕНИЕ ПО СВОЕЙ ЭКОНОМИКЕ!
    stars_amount = int(investment_package.min_amount * stars_per_lcr_rate) 
    
    if stars_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Стоимость в Stars должна быть положительной.")

    # 3. Создаем уникальный payment_payload (идентификатор транзакции)
    # Это должно быть уникальное значение, которое позволит тебе идентифицировать
    # платеж после его завершения через Webhook.
    # Формат: "order_prefix:user_id:package_id:timestamp"
    order_id = f"investpurchase:{user.id}:{investment_package.id}:{int(datetime.now(timezone.utc).timestamp())}"
    invoice_payload = order_id # Используем order_id как payload

    # Возвращаем данные, необходимые фронтенду для openInvoice
    return {
        "ok": True,
        "invoice_payload": invoice_payload,
        "stars_amount": stars_amount,
        "message": f"Готовность к оплате {stars_amount} ⭐ за пакет '{investment_package.name}'."
    }

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