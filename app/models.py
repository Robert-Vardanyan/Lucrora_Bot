# app/models.py

from sqlalchemy import Column, BigInteger, String, Numeric, DateTime, Boolean, Text, Integer, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base # Импортируем Base из нашего database.py

# --- Таблица: users ---
class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True) # Telegram User ID
    username = Column(String(255), unique=True, nullable=False, index=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    registration_date = Column(DateTime(timezone=True), server_default=func.now())
    main_balance = Column(Numeric(18, 2), default=0.00)
    bonus_balance = Column(Numeric(18, 2), default=0.00)
    lucrum_balance = Column(Numeric(18, 2), default=0.00)
    total_invested = Column(Numeric(18, 2), default=0.00)
    total_withdrawn = Column(Numeric(18, 2), default=0.00)
    password_hash = Column(String(255), nullable=True) # Для хеша пароля

    last_daily_bonus_claim = Column(DateTime(timezone=True), nullable=True)

    investments = relationship("Investment", back_populates="owner")
    transactions = relationship("Transaction", back_populates="user_rel")
    referred_by = relationship("Referral", foreign_keys='Referral.referred_id', back_populates="referred_user")
    referrals_made = relationship("Referral", foreign_keys='Referral.referrer_id', back_populates="referrer_user")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"
# --- Таблица: `investment_packages`
class InvestmentPackage(Base):
    __tablename__ = "investment_packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True) # Название пакета (Bronze, Gold, Diamond)
    min_amount = Column(Numeric(18, 2), nullable=False) # Минимальная сумма для этого пакета
    max_amount = Column(Numeric(18, 2), nullable=True) # Максимальная сумма (может быть NULL)
    daily_roi_percentage = Column(Numeric(5, 2), nullable=False) # Ежедневный процент ROI
    duration_days = Column(Integer, nullable=False) # Срок действия пакета в днях
    description = Column(Text, nullable=True) # Подробное описание пакета
    is_active = Column(Boolean, default=True) # Доступен ли пакет для покупки
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    investments_made = relationship("Investment", back_populates="package_details")

    def __repr__(self):
        return f"<InvestmentPackage(id={self.id}, name='{self.name}', daily_roi={self.daily_roi_percentage})>"


# --- Таблица: `investments`
class Investment(Base):
    __tablename__ = "investments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("investment_packages.id"), nullable=False) 
    amount_invested = Column(Numeric(18, 2), nullable=False)
    start_date = Column(DateTime(timezone=True), server_default=func.now())
    end_date = Column(DateTime(timezone=True), nullable=True) # Будет рассчитываться
    current_earned = Column(Numeric(18, 2), default=0.00)
    is_active = Column(Boolean, default=True) # Флаг активности конкретной инвестиции
    
    # НОВОЕ ПОЛЕ: Для хранения ID платежа Telegram Stars
    stars_payment_charge_id = Column(String(255), unique=True, nullable=True, index=True) 
    # Это поле будет содержать 'telegram_payment_charge_id' из успешного платежа
    # Делаем его unique=True, чтобы гарантировать, что один и тот же платеж не будет обработан дважды.

    owner = relationship("User", back_populates="investments")
    package_details = relationship("InvestmentPackage", back_populates="investments_made") 

    def __repr__(self):
        return f"<Investment(id={self.id}, user_id={self.user_id}, package_id={self.package_id}, amount={self.amount_invested})>"


# --- Таблица: `transactions` 
class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    type = Column(String(50), nullable=False) 
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(10), nullable=False) 
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(50), default='completed') 
    description = Column(Text, nullable=True)
    txid = Column(String(255), nullable=True) 

    user_rel = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction(id={self.id}, user_id={self.user_id}, type='{self.type}', amount={self.amount})>"


# --- Таблица: `referrals` 
class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(BigInteger, ForeignKey("users.id"), nullable=False) # Кто пригласил
    referred_id = Column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False) # Кого пригласили
    referral_level = Column(Integer, default=1)
    bonus_earned = Column(Numeric(18, 2), default=0.00)
    join_date = Column(DateTime(timezone=True), server_default=func.now())

    referrer_user = relationship("User", foreign_keys=[referrer_id], back_populates="referrals_made")
    referred_user = relationship("User", foreign_keys=[referred_id], back_populates="referred_by")

    def __repr__(self):
        return f"<Referral(id={self.id}, referrer={self.referrer_id}, referred={self.referred_id})>"