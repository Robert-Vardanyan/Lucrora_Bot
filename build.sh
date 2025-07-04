#!/usr/bin/env bash

# Устанавливаем необходимые системные пакеты
# build-essential предоставляет GCC и другие основные инструменты сборки
# libpq-dev предоставляет заголовочные файлы и библиотеки для PostgreSQL
apt-get update && apt-get install -y build-essential libpq-dev

# Теперь можно продолжить с установкой Python зависимостей
# Render обычно запускает pip install -r requirements.txt автоматически после build.sh
# Но если вы хотите быть уверенным, можете добавить это здесь:
# pip install -r requirements.txt