# app/routers/games.py
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
import json
import random
from urllib.parse import parse_qsl

from app.database import get_async_session
from app.models import User # Убедись, что импортируешь модель User
from app.utils import check_webapp_signature
import os

# Загружаем BOT_TOKEN из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

router = APIRouter(
    prefix="/api/games",
    tags=["Games"]
)

# --- Вспомогательная функция для получения пользователя по initData ---
async def get_current_user_from_init_data(
    init_data: str,
    db: AsyncSession = Depends(get_async_session)
) -> User:
    """
    Проверяет initData и возвращает объект пользователя.
    """
    if not init_data:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing Telegram initData.")

    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Telegram initData signature.")

    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User data not found in initData")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = int(user_info.get('id'))
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user data JSON or Telegram ID in initData")

    user = await db.get(User, telegram_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    
    return user

@router.post("/daily_bonus")
async def get_daily_bonus(request: Request, db: AsyncSession = Depends(get_async_session)):
    """
    Эндпоинт для получения ежедневного бонуса.
    """
    try:
        body = await request.json()
        init_data = body.get("initData")
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bad Request: Invalid JSON")

    user = await get_current_user_from_init_data(init_data, db)

    # Проверка на cooldown (24 часа)
    if user.last_daily_bonus_claim:
        time_since_last_claim = datetime.utcnow() - user.last_daily_bonus_claim
        if time_since_last_claim < timedelta(days=1):
            remaining_time = timedelta(days=1) - time_since_last_claim
            hours, remainder = divmod(remaining_time.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return {
                "ok": False,
                "message": f"Вы уже получили ежедневный бонус. Повторите попытку через {hours} ч. {minutes} мин.",
                "bonus_balance": float(user.bonus_balance)
            }

    # Начисление случайного бонуса от 0.5 до 5.0
    bonus_amount = round(random.uniform(0.5, 5.0), 2)
    user.bonus_balance += bonus_amount
    user.last_daily_bonus_claim = datetime.utcnow()

    try:
        await db.commit()
        await db.refresh(user)
        return {
            "ok": True,
            "message": f"Поздравляем! Вы получили {bonus_amount} ₤s ежедневного бонуса!",
            "bonus_balance": float(user.bonus_balance)
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ошибка при начислении бонуса: {e}")


@router.post("/play")
async def play_game(request: Request, db: AsyncSession = Depends(get_async_session)):
    """
    Эндпоинт для начала игры.
    """
    try:
        body = await request.json()
        init_data = body.get("initData")
        game_id = body.get("game_id")
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bad Request: Invalid JSON")

    user = await get_current_user_from_init_data(init_data, db)

    # Определяем стоимость игры
    game_costs = {
        "wheel_of_fortune": 1.00,
        "higher_lower": 0.50
    }

    cost = game_costs.get(game_id)
    if cost is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный ID игры.")

    if user.bonus_balance < cost:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недостаточно средств на бонусном балансе.")

    user.bonus_balance -= cost

    # Здесь будет логика конкретной игры
    # Для примера, просто списываем и сообщаем о начале игры
    game_result_message = ""
    if game_id == "wheel_of_fortune":
        # Пример: имитация выигрыша или проигрыша
        win_chance = random.random() # от 0 до 1
        if win_chance < 0.6: # 60% шанс проиграть
            game_result_message = "К сожалению, вы ничего не выиграли."
        else:
            win_amount = round(random.uniform(cost * 1.5, cost * 5), 2) # Выигрыш от 1.5 до 5 раз от ставки
            user.bonus_balance += win_amount
            game_result_message = f"Поздравляем! Вы выиграли {win_amount} ₤s!"
            
    elif game_id == "higher_lower":
        # Логика для "Больше/Меньше" - пока заглушка, нужно реализовать на фронте и бэкенде
        game_result_message = "Игра 'Больше/Меньше' пока не полностью реализована."
        # Можно добавить логику, где клиент отправляет свой выбор, а сервер генерирует число и определяет выигрыш
    
    # ... Добавь логику для других игр здесь ...

    try:
        await db.commit()
        await db.refresh(user)
        return {
            "ok": True,
            "message": f"Вы сыграли в {game_id.replace('_', ' ')}. {game_result_message}",
            "bonus_balance": float(user.bonus_balance),
            "game_outcome": game_result_message # Добавим исход игры
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ошибка при обработке игры: {e}")