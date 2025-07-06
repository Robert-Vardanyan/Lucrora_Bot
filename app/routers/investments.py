# app/routers/investments.py

import hmac
import hashlib
import json
from urllib.parse import parse_qsl
from operator import itemgetter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request # Добавляем Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from decimal import Decimal # Используем Decimal для точных финансовых расчетов

# Импортируем модели и функцию для получения сессии БД
from app.database import get_async_session
from app.models import InvestmentPackage, User, Investment, Transaction # Убедитесь, что Transaction импортирован

import os
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

router = APIRouter()

# --- Вспомогательная функция для проверки initData (перенесена из main.py) ---
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

# --- Зависимость для получения Telegram User ID из initData ---
async def get_telegram_user_id(request: Request) -> int:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный формат JSON запроса.")

    init_data = body.get("telegramInitData")
    if not init_data or not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверные или отсутствующие Telegram initData.")

    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Данные пользователя не найдены в initData.")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = int(user_info.get('id'))
        return telegram_id
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный формат данных пользователя или Telegram ID в initData.")


# --- Pydantic модели для валидации данных ---

# Модель для ответа при получении списка пакетов
class InvestmentPackageResponse(BaseModel):
    id: int
    name: str
    min_amount: Decimal
    max_amount: Decimal | None # Python 3.10+ синтаксис
    daily_roi_percentage: Decimal
    duration_days: int
    description: str | None # Python 3.10+ синтаксис
    is_active: bool

    # Позволяет Pydantic читать данные из ORM-объектов SQLAlchemy
    class Config:
        from_attributes = True

# Модель для запроса на покупку пакета
class BuyPackageRequest(BaseModel):
    package_id: int
    # Если вы захотите позволить пользователю вводить сумму в пределах min/max,
    # раскомментируйте это поле и используйте его в логике.
    # amount_invested: Decimal | None = None 
    
# --- Эндпоинты API ---

@router.get("/api/investment_packages", response_model=list[InvestmentPackageResponse])
async def get_investment_packages(db: AsyncSession = Depends(get_async_session)):
    """
    Возвращает список всех активных инвестиционных пакетов.
    """
    try:
        # Выбираем только активные пакеты и сортируем по минимальной сумме
        stmt = select(InvestmentPackage).where(InvestmentPackage.is_active == True).order_by(InvestmentPackage.min_amount)
        result = await db.execute(stmt)
        packages = result.scalars().all()
        return packages
    except Exception as e:
        # Логируем ошибку для отладки
        print(f"Ошибка при получении инвестиционных пакетов: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Произошла ошибка на сервере при получении пакетов.")


@router.post("/api/buy_investment")
async def buy_investment(
    request_body: BuyPackageRequest, # Переименовал, чтобы не конфликтовать с FastAPI Request
    db: AsyncSession = Depends(get_async_session),
    telegram_user_id: int = Depends(get_telegram_user_id) # Используем новую зависимость для получения ID
):
    """
    Обрабатывает запрос на покупку инвестиционного пакета.
    """
    # Получаем пользователя из БД по Telegram ID
    user = await db.get(User, telegram_user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден. Пожалуйста, зарегистрируйтесь.")

    # Получаем информацию о пакете
    package = await db.get(InvestmentPackage, request_body.package_id)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Инвестиционный пакет не найден.")
    if not package.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Этот инвестиционный пакет недоступен для покупки.")

    # Сумма для списания (используем min_amount пакета)
    amount_to_deduct = package.min_amount 
    
    # Проверяем, достаточно ли средств у пользователя
    if user.main_balance < amount_to_deduct:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недостаточно средств на основном балансе.")

    try:
        # 1. Списываем средства с баланса пользователя
        user.main_balance -= amount_to_deduct
        user.total_invested += amount_to_deduct # Увеличиваем общую инвестированную сумму

        # 2. Создаем новую запись об инвестиции
        end_date = datetime.now(datetime.timezone.utc) + timedelta(days=package.duration_days) # Расчет даты окончания

        new_investment = Investment(
            user_id=user.id,
            package_id=package.id,
            amount_invested=amount_to_deduct, # Записываем фактически инвестированную сумму
            start_date=datetime.now(datetime.timezone.utc),
            end_date=end_date,
            current_earned=Decimal('0.00'), # В начале заработок 0
            is_active=True # Считаем активной сразу после покупки
        )
        db.add(new_investment)

        # 3. Записываем транзакцию (рекомендуется для аудита)
        new_transaction = Transaction(
            user_id=user.id,
            type='investment_purchase',
            amount=-amount_to_deduct, # Отрицательная сумма, так как это расход
            currency='₤', # Или 'LCR', в зависимости от вашей системы
            status='completed',
            description=f"Покупка пакета '{package.name}'"
        )
        db.add(new_transaction)

        await db.commit()
        await db.refresh(user) # Обновляем объект пользователя, чтобы вернуть актуальный баланс

        # Возвращаем актуальный баланс пользователя после покупки
        return {
            "message": f"Пакет '{package.name}' успешно куплен за ₤{amount_to_deduct}!",
            "new_main_balance": user.main_balance,
            "new_total_invested": user.total_invested
        }

    except Exception as e:
        await db.rollback() # Откатываем транзакцию в случае ошибки
        print(f"Ошибка при покупке инвестиционного пакета: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Произошла ошибка при обработке покупки.")