# app/database.py

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool # Для Render.com или других облачных провайдеров, использующих connection pool на своей стороне
from sqlalchemy import select # Импортируем select для проверки существования пакетов

import os
from dotenv import load_dotenv

# Импортируем модели здесь, чтобы они были доступны для Base.metadata.create_all
# и для инициализации пакетов. Важно: models.py должен импортировать Base из database.py
# чтобы избежать циклического импорта, models.py не должен импортировать database.py целиком.
# Лучше импортировать Base из .database
# from app.models import InvestmentPackage, User # Закомментировано, так как Base.metadata.create_all сам найдет все модели, унаследованные от Base

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

# --- Данные для начальной инициализации инвестиционных пакетов ---
def _initial_investment_packages_data():
    """Возвращает список словарей с данными для начальных инвестиционных пакетов."""
    return [
        {'name': 'Bronze Plan', 'min_amount': 100.00, 'max_amount': 500.00, 'daily_roi_percentage': 0.50, 'duration_days': 30, 'description': 'Наш начальный план. Идеально подходит для новичков.', 'is_active': True},
        {'name': 'Silver Plan', 'min_amount': 501.00, 'max_amount': 2000.00, 'daily_roi_percentage': 0.75, 'duration_days': 45, 'description': 'Популярный план со сбалансированным доходом.', 'is_active': True},
        {'name': 'Gold Plan', 'min_amount': 2001.00, 'max_amount': 10000.00, 'daily_roi_percentage': 1.00, 'duration_days': 60, 'description': 'Премиальный план для более значительных инвестиций.', 'is_active': True},
        {'name': 'Diamond Plan', 'min_amount': 10001.00, 'max_amount': None, 'daily_roi_percentage': 1.25, 'duration_days': 90, 'description': 'Эксклюзивный план с максимальной прибылью.', 'is_active': True},
    ]

async def initialize_investment_packages(session: AsyncSession):
    """
    Инициализирует базовые инвестиционные пакеты в базе данных,
    если их еще нет.
    """
    # Импортируем InvestmentPackage здесь, чтобы избежать циклического импорта
    # если models.py импортирует что-то из database.py
    from app.models import InvestmentPackage 

    print("Проверяю и инициализирую инвестиционные пакеты...")
    for pkg_data in _initial_investment_packages_data():
        # Проверяем, существует ли пакет с таким именем
        stmt = select(InvestmentPackage).where(InvestmentPackage.name == pkg_data['name'])
        existing_package = (await session.execute(stmt)).scalar_one_or_none()
        
        if not existing_package:
            # Если не существует, создаем и добавляем
            new_package = InvestmentPackage(**pkg_data)
            session.add(new_package)
            print(f"Добавлен инвестиционный пакет: {pkg_data['name']}")
        else:
            print(f"Инвестиционный пакет '{pkg_data['name']}' уже существует.")
    
    await session.commit() # Фиксируем изменения после добавления всех пакетов
    print("Инициализация инвестиционных пакетов завершена.")


# Функции для создания и удаления всех таблиц
async def create_db_tables():
    """Создает все таблицы в базе данных на основе ORM-моделей и инициализирует базовые данные."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Все таблицы базы данных успешно созданы.")
    
    # После создания таблиц, инициализируем инвестиционные пакеты
    async with AsyncSessionLocal() as session:
        await initialize_investment_packages(session)


async def drop_db_tables():
    """Удаляет все таблицы из базы данных."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("Все таблицы базы данных успешно удалены.")