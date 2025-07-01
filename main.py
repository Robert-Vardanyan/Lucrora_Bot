from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
import hmac
import hashlib

app = FastAPI()

# Разрешаем запросы с фронтенда Telegram Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://lucrora.vercel.app"],  # или ["*"] для тестов
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = "..."  # твой токен

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
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("initData")
    if not init_data or not validate_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData")

    return JSONResponse({
        "ok": True,
        "main_balance": 84,
        "bonus_balance": 16,
    })
