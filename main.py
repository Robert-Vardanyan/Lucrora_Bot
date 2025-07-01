import asyncio
import hmac
import hashlib
from aiogram import Bot, Dispatcher
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Request, HTTPException
from starlette.responses import JSONResponse
import uvicorn
from threading import Thread
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")

app = FastAPI()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

webapp_button = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚀 Запустить Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]
])

@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "👋 Привет! Нажми кнопку ниже, чтобы открыть Mini App:",
        reply_markup=webapp_button
    )
    await message.delete()

def validate_init_data(init_data: str, bot_token: str) -> bool:
    try:
        data_parts = [part for part in init_data.split('&') if not part.startswith("hash=")]
        data_parts.sort()
        data_check_string = "\n".join(data_parts)
        received_hash = init_data.split("hash=")[-1]

        secret_key = hashlib.sha256(bot_token.encode()).digest()
        hmac_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        return hmac_hash == received_hash
    except Exception:
        return False

@app.post("/api/init")
async def api_init(request: Request):
    body = await request.json()
    init_data = body.get("initData")

    if not init_data or not validate_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="❌ Invalid Telegram initData")

    # Для примера возвращаем статичные балансы
    return JSONResponse({
        "ok": True,
        "main_balance": 84,
        "bonus_balance": 16,
    })

async def start_api():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)

    asyncio.create_task(start_api())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
