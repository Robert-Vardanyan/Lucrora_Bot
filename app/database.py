# app/database.py

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool # Для Render.com или других облачных провайдеров, использующих connection pool на своей стороне

import os
from dotenv import load_dotenv

load_dotenv() # Загружаем переменные окружения

DATABASE_URL = os.getenv("DATABASE_URL")

# Проверяем, что DATABASE_URL установлен
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set.")

# Создаем асинхронный движок SQLAlchemy
# poolclass=NullPool может быть полезен, если провайдер БД (например, Render.com)
# имеет свой собственный пул подключений, и вы не хотите, чтобы SQLAlchemy
# создавал дополнительный пул, который может конфликтовать.
# Если вы используете локальную БД или другой провайдер, который не управляет пулом,
# можно убрать poolclass=NullPool или использовать QueuePool (по умолчанию).
engine = create_async_engine(DATABASE_URL, echo=True, poolclass=NullPool)

# Создаем базовый класс для декларативных моделей SQLAlchemy
Base = declarative_base()

# Создаем фабрику асинхронных сессий
# autoflush=False отключает автоматическую отправку изменений в БД после каждой операции,
# что может быть полезно для оптимизации, но требует явного flush() перед commit() если нужно.
# bind=engine привязывает сессии к нашему движку.
AsyncSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False # Объекты не истекают после коммита, можно использовать их дальше
)

# Функция для получения асинхронной сессии (для зависимостей FastAPI)
async def get_async_session():
    async with AsyncSessionLocal() as session:
        yield session

# Функции для создания и удаления всех таблиц
async def create_db_tables():
    """Создает все таблицы в базе данных на основе ORM-моделей."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Все таблицы базы данных успешно созданы.")

async def drop_db_tables():
    """Удаляет все таблицы из базы данных."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("Все таблицы базы данных успешно удалены.")