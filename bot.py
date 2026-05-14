import os
import asyncio
import logging
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
API_URL = os.getenv("API_URL", "https://cyberx302.langame.ru/public_api/products/list")
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "10"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))  # 1 час

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID не задан в переменных окружения")

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==================== РАБОТА С API ====================
async def fetch_low_stock_products(session: aiohttp.ClientSession, threshold: int):
    """Получает список товаров и возвращает те, у которых остаток ниже порога."""
    try:
        url = str(API_URL).strip()
        logger.info(f"Запрос к API: {url}")
        
        # Добавляем только User-Agent для имитации браузера
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"API вернул статус {response.status}. Тело ответа: {error_text[:200]}")
                return []
            
            data = await response.json()
            logger.info(f"Ответ API получен, тип данных: {type(data)}")
            
            # Пробуем разные варианты структуры ответа
            products = []
            
            # Вариант 1: {"products": [...]}
            if isinstance(data, dict) and "products" in data:
                products = data.get("products", [])
            # Вариант 2: просто массив товаров
            elif isinstance(data, list):
                products = data
            # Вариант 3: {"data": {"products": [...]}}
            elif isinstance(data, dict) and "data" in data and "products" in data["data"]:
                products = data["data"].get("products", [])
            else:
                logger.warning(f"Неизвестная структура API: {str(data)[:200]}")
                return []
            
            if not products:
                logger.warning("API не вернул список товаров")
                return []
            
            low_stock = [p for p in products if p.get("quantity", 0) < threshold]
            logger.info(f"Найдено товаров: {len(products)}, с низким остатком: {len(low_stock)}")
            return low_stock
            
    except aiohttp.ClientError as e:
        logger.error(f"Ошибка соединения с API: {e}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при запросе к API: {e}")
        return []


async def notify_low_stock():
    """Проверяет остатки и отправляет уведомление администратору."""
    async with aiohttp.ClientSession() as session:
        low_stock = await fetch_low_stock_products(session, LOW_STOCK_THRESHOLD)
        if not low_stock:
            logger.info("Нет товаров с низким остатком")
            return
        
        message = "⚠️ <b>ВНИМАНИЕ! Заканчиваются товары:</b>\n\n"
        for product in low_stock:
            name = product.get("name", product.get("title", "Без названия"))
            qty = product.get("quantity", product.get("stock", "?"))
            message += f"• {name} — осталось: {qty} шт.\n"
        
        # Добавляем время проверки
        message += f"\n📅 Проверка выполнена: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        
        try:
            await bot.send_message(ADMIN_CHAT_ID, message, parse_mode="HTML")
            logger.info(f"Уведомление отправлено: {len(low_stock)} товаров")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление: {e}")


# ==================== ФОНОВАЯ ЗАДАЧА ====================
async def scheduled_checker():
    """Периодическая проверка остатков."""
    # Ждём 10 секунд перед первым запуском, чтобы бот успел инициализироваться
    await asyncio.sleep(10)
    while True:
        now = datetime.now()
        logger.info(f"Запуск плановой проверки в {now.strftime('%H:%M:%S')}")
        await notify_low_stock()
        await asyncio.sleep(CHECK_INTERVAL)


# ==================== КОМАНДЫ БОТА ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Бот мониторинга остатков запущен!\n\n"
        "📋 Команды:\n"
        "/check — ручная проверка остатков\n"
        "/status — статус бота\n\n"
        "Уведомления о заканчивающихся товарах будут приходить автоматически."
    )
    logger.info(f"Пользователь {message.from_user.id} использовал /start")


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Проверка статуса бота"""
    await message.answer(
        "✅ Бот работает\n"
        f"📊 Интервал проверки: {CHECK_INTERVAL} сек.\n"
        f"⚠️ Порог остатка: {LOW_STOCK_THRESHOLD} шт.\n"
        f"🔗 API: {API_URL}"
    )


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    """Ручная проверка остатков"""
    await message.answer("🔄 Проверяю остатки, подождите...")
    async with aiohttp.ClientSession() as session:
        low_stock = await fetch_low_stock_products(session, LOW_STOCK_THRESHOLD)
        if low_stock:
            text = "⚠️ <b>Товары с низким остатком:</b>\n\n"
            for p in low_stock:
                name = p.get("name", p.get("title", "Без названия"))
                qty = p.get("quantity", p.get("stock", "?"))
                text += f"• {name} — осталось: {qty} шт.\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("✅ Все товары в достаточном количестве.")
    logger.info(f"Пользователь {message.from_user.id} использовал /check")


# ==================== ЗАПУСК (POLLING) ====================
async def main():
    logger.info("🚀 Бот запускается в режиме polling...")
    # Запускаем фоновую задачу
    asyncio.create_task(scheduled_checker())
    # Запускаем долгие опросы
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())