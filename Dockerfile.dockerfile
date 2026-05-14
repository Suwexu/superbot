# Используем официальный образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код бота
COPY bot.py .

# Открываем порт, который будет использовать Railway (обычно 8080)
EXPOSE 8080

# Команда запуска бота
CMD ["python", "bot.py"]