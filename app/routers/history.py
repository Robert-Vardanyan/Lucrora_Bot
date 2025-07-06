# app/routers/history.py

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.database import get_async_session
from app.models import Transaction, User # Убедитесь, что Transaction и User импортированы
from app.schemas import TransactionSchema # Вам нужно будет определить эту Pydantic схему
from app.utils import check_webapp_signature # Предполагается, что она также используется здесь
import os

router = APIRouter()

# Загрузка токена бота для проверки подписи WebApp initData
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Если вы еще не определили Pydantic схему, сделайте это.
# Она нужна для сериализации данных из SQLAlchemy моделей в JSON-ответы.
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal

class TransactionSchema(BaseModel):
    id: int
    user_id: int
    type: str
    amount: Decimal # Используйте Decimal для точных финансовых расчетов
    currency: str
    timestamp: datetime
    status: str
    description: Optional[str] = None
    txid: Optional[str] = None

    class Config:
        from_attributes = True # Для совместимости Pydantic с ORM моделями

@router.get("/api/transactions", response_model=List[TransactionSchema])
async def get_user_transactions(
    # user_id должен поступать из initData или аутентификационного токена для безопасности
    # Здесь для примера используется Query параметр, но в продакшене используйте безопасный метод.
    user_id: int = Query(..., description="ID пользователя, чьи транзакции нужно получить"),
    transaction_type: Optional[str] = Query(None, alias="type", description="Фильтр по типу транзакции (например, 'deposit', 'withdrawal', 'game_win,game_loss')"),
    init_data: Optional[str] = Query(None, alias="initData", description="Telegram WebApp initData для проверки подписи"),
    db: AsyncSession = Depends(get_async_session)
):
    # ! ВАЖНО: В РЕАЛЬНОМ ПРИЛОЖЕНИИ ТАК НЕ ДЕЛАЮТ.
    # ! `user_id` ДОЛЖЕН БЫТЬ ПОЛУЧЕН ИЗ ПРОВЕРЕННЫХ `init_data`
    # ! ИЛИ ИЗ АУТЕНТИФИКАЦИОННОГО ТОКЕНА.
    # ! Этот Query-параметр `user_id` и `init_data` здесь ТОЛЬКО ДЛЯ ДЕМОНСТРАЦИИ.
    # ! Правильный подход: получить user_id из init_data, а затем проверить initData.

    if not init_data:
        raise HTTPException(status_code=403, detail="Missing Telegram initData.")

    # Проверяем подпись init_data
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not configured on the server.")

    # Получаем user_id из init_data для проверки, что переданный user_id соответствует
    import json
    from urllib.parse import parse_qsl

    try:
        parsed_init_data = dict(parse_qsl(init_data))
        user_data_str = parsed_init_data.get('user')
        if not user_data_str:
            raise HTTPException(status_code=400, detail="User data not found in initData")
        
        user_info = json.loads(user_data_str)
        telegram_id_from_init = int(user_info.get('id'))
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user data JSON or Telegram ID in initData")

    # Проверка, что user_id в URL соответствует user_id из проверенных initData
    if user_id != telegram_id_from_init:
        raise HTTPException(status_code=403, detail="Provided user_id does not match Telegram initData.")

    # Теперь проверяем подпись. Делайте это после извлечения user_id, чтобы убедиться, что init_data валидна
    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")


    query = select(Transaction).filter(Transaction.user_id == user_id)

    if transaction_type:
        # Обрабатываем множественные типы для фильтра 'games' (game_win, game_loss)
        if ',' in transaction_type:
            types_list = transaction_type.split(',')
            # Используем .in_() для более читаемого запроса по списку значений
            query = query.filter(Transaction.type.in_(types_list)) 
        else:
            query = query.filter(Transaction.type == transaction_type)

    transactions = (await db.execute(query.order_by(Transaction.timestamp.desc()))).scalars().all()
    return transactions