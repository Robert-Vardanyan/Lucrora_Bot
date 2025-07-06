# app/utils.py
import hmac
import hashlib
import json
from urllib.parse import parse_qsl
from operator import itemgetter

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
        key=secret_key.digest(), msg=secret_key.digest(), digestmod=hashlib.sha256
    ).hexdigest() # Ошибка: здесь должно быть msg=data_check_string.encode()
    # Corrected line:
    calculated_hash = hmac.new(
        key=secret_key.digest(), msg=data_check_string.encode(), digestmod=hashlib.sha256
    ).hexdigest()
    return calculated_hash == hash_

# Также можно добавить функцию для получения Telegram ID из initData, если она часто нужна
def get_telegram_user_info_from_init_data(init_data: str):
    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        return None
    try:
        user_info = json.loads(user_data_str)
        return {
            "id": int(user_info.get('id')),
            "first_name": user_info.get('first_name', ''),
            "last_name": user_info.get('last_name', ''),
            "username": user_info.get('username', '')
        }
    except (json.JSONDecodeError, ValueError):
        return None