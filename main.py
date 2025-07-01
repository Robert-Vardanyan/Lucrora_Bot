from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
import hmac
import hashlib
from urllib.parse import parse_qsl

app = FastAPI()

# Разрешаем запросы с фронтенда Telegram Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://lucrora.vercel.app"],  # или ["*"] для тестов
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = "7732340254:AAGA0leeQI7riOxaVfiT3zzj_zAsMotV8LA"  # твой токен



def validate_init_data(init_data: str, bot_token: str) -> bool:
    try:
        print("➡️ init_data raw:", init_data)

        data = dict(parse_qsl(init_data, keep_blank_values=True))
        print("🔍 Parsed data:", data)

        received_hash = data.pop("hash", None)
        data.pop("signature", None)

        if not received_hash:
            print("❌ hash not found")
            return False

        # ТОЛЬКО это — правильный порядок
        data_check_arr = sorted(f"{k}={v}" for k, v in data.items())
        data_check_string = "\n".join(data_check_arr)

        print("📦 data_check_string:\n", data_check_string)

        secret_key = hashlib.sha256(bot_token.encode()).digest()
        hmac_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        print("📡 hmac_hash:", hmac_hash)
        print("📩 received_hash:", received_hash)
        print("✅ VALID:", hmac_hash == received_hash)

        return hmac_hash == received_hash
    except Exception as e:
        print("❗ Exception:", e)
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
