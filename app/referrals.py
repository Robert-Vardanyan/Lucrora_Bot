# app/referrals.py
import json
from decimal import Decimal
from typing import List, Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_async_session
from app.models import User, Referral # Make sure Referral is imported from app.models
from app.utils import check_webapp_signature, parse_qsl # Assuming parse_qsl is also in app.utils

from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

router = APIRouter(prefix="/api", tags=["referrals"])

# Pydantic Schemas for API Response
class ReferralDetails(BaseModel):
    username: str
    bonus_earned: float # Convert Decimal to float for JS

class ReferralLevelData(BaseModel):
    level: int
    referrals: List[ReferralDetails]
    earned_from_level: float # Convert Decimal to float for JS
    # You might want to define commission_rate here if it's dynamic
    # For now, we'll assume a static rate on frontend or define it on backend.
    # For simplicity, let's assume rates are hardcoded for display,
    # but the earned_from_level is calculated correctly by backend.

class ReferralSystemResponse(BaseModel):
    ok: bool = True
    message: str = "Referral data fetched successfully"
    referral_link: str
    total_referral_earnings: float # Convert Decimal to float for JS
    active_referrals_count: int
    referral_network_levels: List[ReferralLevelData]

# Hardcoded commission rates for example. In a real app, these might be in DB or config.
# Key is level, value is percentage.
COMMISSION_RATES = {
    1: Decimal('0.5'), # 0.5%
    2: Decimal('0.4'), # 0.4%
    3: Decimal('0.3'), # 0.3%
    4: Decimal('0.2'), # 0.2%
    5: Decimal('0.1'), # 0.1%
    # Add more levels if needed
}

@router.post("/referral_data")
async def get_referral_data(request: Request, db: AsyncSession = Depends(get_async_session)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: Invalid JSON")

    init_data = body.get("telegramInitData")

    if not init_data:
        raise HTTPException(status_code=403, detail="Missing Telegram initData.")

    if not check_webapp_signature(init_data, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData signature.")

    user_data_str = dict(parse_qsl(init_data)).get('user')
    if not user_data_str:
        raise HTTPException(status_code=400, detail="User data not found in initData.")

    try:
        user_info = json.loads(user_data_str)
        telegram_id = int(user_info.get('id'))
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user data JSON or Telegram ID in initData.")

    # Fetch the current user
    stmt_user = select(User).where(User.id == telegram_id)
    current_user = (await db.execute(stmt_user)).scalar_one_or_none()

    if not current_user:
        raise HTTPException(status_code=404, detail="User not found.")

    # 1. Generate Referral Link
    # Replace 'YourBot' with your actual bot username
    referral_link = f"https://t.me/YourBot?start=ref_{current_user.id}"

    # 2. Calculate Total Referral Earnings
    # Sum of bonus_earned for all referrals made by this user
    total_earnings_stmt = select(func.sum(Referral.bonus_earned)).where(
        Referral.referrer_id == current_user.id
    )
    total_referral_earnings = (await db.execute(total_earnings_stmt)).scalar_one_or_none() or Decimal('0.00')

    # 3. Calculate Active Referrals Count
    # For simplicity, let's define "active" as someone who has at least one investment.
    # This requires joining with the Investment table if you have one.
    # For now, let's just count all direct referrals who have registered.
    active_referrals_count_stmt = select(func.count(Referral.id)).where(
        Referral.referrer_id == current_user.id
    )
    active_referrals_count = (await db.execute(active_referrals_count_stmt)).scalar_one()

    # 4. Build Referral Network Levels
    referral_network_levels: List[ReferralLevelData] = []

    # Level 1 Referrals (Direct referrals)
    level1_referrals_stmt = select(Referral, User).join(User, Referral.referred_id == User.id).where(
        Referral.referrer_id == current_user.id,
        Referral.referral_level == 1
    )
    level1_results = (await db.execute(level1_referrals_stmt)).all()

    level1_details = []
    level1_earned_total = Decimal('0.00')
    for referral, referred_user in level1_results:
        level1_details.append(ReferralDetails(
            username=referred_user.username,
            bonus_earned=float(referral.bonus_earned)
        ))
        level1_earned_total += referral.bonus_earned

    referral_network_levels.append(ReferralLevelData(
        level=1,
        referrals=level1_details,
        earned_from_level=float(level1_earned_total)
    ))

    # Level 2 Referrals (Referrals of Level 1 referrals)
    # This requires a more complex query, potentially recursive or multiple joins.
    # For a simple 2-level system, we can:
    # 1. Get all users directly referred by the current user (Level 1)
    # 2. Then get all users referred by those Level 1 users (Level 2)

    level1_referred_ids = [ref.referred_id for ref, _ in level1_results]
    
    if level1_referred_ids:
        level2_referrals_stmt = select(Referral, User).join(User, Referral.referred_id == User.id).where(
            Referral.referrer_id.in_(level1_referred_ids),
            Referral.referral_level == 1 # This level in the Referral table is relative to *their* referrer
        )
        level2_results = (await db.execute(level2_referrals_stmt)).all()

        level2_details = []
        level2_earned_total = Decimal('0.00')
        for referral, referred_user in level2_results:
            level2_details.append(ReferralDetails(
                username=referred_user.username,
                bonus_earned=float(referral.bonus_earned)
            ))
            level2_earned_total += referral.bonus_earned

        referral_network_levels.append(ReferralLevelData(
            level=2,
            referrals=level2_details,
            earned_from_level=float(level2_earned_total)
        ))
    
    # Sort levels for consistent display
    referral_network_levels.sort(key=lambda x: x.level)

    return ReferralSystemResponse(
        referral_link=referral_link,
        total_referral_earnings=float(total_referral_earnings),
        active_referrals_count=active_referrals_count,
        referral_network_levels=referral_network_levels
    )