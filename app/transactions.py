# app/transactions.py (создайте этот новый файл или добавьте в существующий роутер)

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
import json

from app.database import get_async_session
from app.models import Transaction, User # Убедитесь, что импортировали User и Transaction
from app.utils import check_webapp_signature, parse_qsl # Повторно используйте ваши утилитарные функции

import os
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

router = APIRouter(prefix="/api", tags=["transactions"])

# --- Эндпоинт для получения транзакций пользователя ---
@router.get("/transactions")
async def get_transactions(
    telegram_init_data: str = Query(..., alias="initData"), # Ожидаем initData из параметра запроса
    type: Optional[str] = Query(None), # Необязательный фильтр для типа(ов) транзакций
    db: AsyncSession = Depends(get_async_session)
):
    if not telegram_init_data:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Отсутствует Telegram initData.")
    
    # 1. Проверка подписи Telegram InitData
    if not check_webapp_signature(telegram_init_data, BOT_TOKEN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверная подпись Telegram initData.")

    # 2. Извлечение ID пользователя из initData
    user_data_str = dict(parse_qsl(telegram_init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Данные пользователя не найдены в initData.")
    try:
        telegram_user_info = json.loads(user_data_str)
        user_id = int(telegram_user_info.get('id'))
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный JSON данных пользователя или Telegram ID в initData.")

    # 3. Построение запроса
    query = select(Transaction).where(Transaction.user_id == user_id)

    # Применение фильтра по типу, если он предоставлен
    if type:
        # Если параметр type содержит запятую, разделите его на несколько типов
        # например, "game_win,game_loss" -> ['game_win', 'game_loss']
        transaction_types = [t.strip() for t in type.split(',')]
        query = query.where(Transaction.type.in_(transaction_types))

    # Сортировка по времени для хронологической истории
    query = query.order_by(Transaction.timestamp.desc())

    # 4. Выполнение запроса
    result = await db.execute(query)
    transactions = result.scalars().all()

    # 5. Возврат транзакций
    # Преобразование Numeric (Decimal) в float для JSON-сериализации
    # Возможно, вы захотите использовать модель Pydantic для вывода для лучшего контроля
    # Для простоты мы преобразуем на лету здесь.
    return [
        {
            "id": tx.id,
            "user_id": tx.user_id,
            "type": tx.type,
            "amount": float(tx.amount), # Преобразование в float
            "currency": tx.currency,
            "timestamp": tx.timestamp.isoformat(), # Преобразование datetime в ISO-строку
            "status": tx.status,
            "description": tx.description,
            "txid": tx.txid,
        }
        for tx in transactions
    ]