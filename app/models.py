# app/models.py

from sqlalchemy import Column, BigInteger, String, Numeric, DateTime, Boolean, Text, Integer, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base # Импортируем Base из нашего database.py

# Таблица: users
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

    # Определяем взаимосвязи с другими моделями
    investments = relationship("Investment", back_populates="owner")
    transactions = relationship("Transaction", back_populates="user_rel")
    referred_by = relationship("Referral", foreign_keys='Referral.referred_id', back_populates="referred_user")
    referrals_made = relationship("Referral", foreign_keys='Referral.referrer_id', back_populates="referrer_user")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"

# Таблица: investments
class Investment(Base):
    __tablename__ = "investments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    package_name = Column(String(255), nullable=False)
    amount_invested = Column(Numeric(18, 2), nullable=False)
    daily_roi_percentage = Column(Numeric(5, 2), nullable=False)
    duration_days = Column(Integer, nullable=False)
    start_date = Column(DateTime(timezone=True), server_default=func.now())
    end_date = Column(DateTime(timezone=True), nullable=True) # Будет рассчитываться
    current_earned = Column(Numeric(18, 2), default=0.00)
    is_active = Column(Boolean, default=True)

    # Определяем взаимосвязь с моделью User
    owner = relationship("User", back_populates="investments")

    def __repr__(self):
        return f"<Investment(id={self.id}, user_id={self.user_id}, package='{self.package_name}')>"

# Таблица: transactions
class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    type = Column(String(50), nullable=False) # 'deposit', 'withdrawal', 'earning', 'game_win', 'game_loss', 'referral_bonus', 'conversion'
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(10), nullable=False) # 'LCR', 'USD', 'RUB'
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(50), default='completed') # 'pending', 'completed', 'failed', 'processing'
    description = Column(Text, nullable=True)
    txid = Column(String(255), nullable=True) # Для крипто-транзакций

    # Определяем взаимосвязь с моделью User
    user_rel = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction(id={self.id}, user_id={self.user_id}, type='{self.type}', amount={self.amount})>"

# Таблица: referrals
class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(BigInteger, ForeignKey("users.id"), nullable=False) # Кто пригласил
    referred_id = Column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False) # Кого пригласили
    referral_level = Column(Integer, default=1)
    bonus_earned = Column(Numeric(18, 2), default=0.00)
    join_date = Column(DateTime(timezone=True), server_default=func.now())

    # Определяем взаимосвязи с моделью User
    referrer_user = relationship("User", foreign_keys=[referrer_id], back_populates="referrals_made")
    referred_user = relationship("User", foreign_keys=[referred_id], back_populates="referred_by")

    def __repr__(self):
        return f"<Referral(id={self.id}, referrer={self.referrer_id}, referred={self.referred_id})>"