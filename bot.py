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
API_URL = os.getenv("API_URL")
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "10"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))  # 1 час

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID не задан в переменных окружения")
if not API_URL:
    raise ValueError("API_URL не задан в переменных окружения")

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
        # Убедимся, что URL строка
        url = str(API_URL)
        logger.info(f"Запрос к API: {url}")
        async with session.get(url) as response:
            if response.status != 200:
                logger.error(f"API вернул статус {response.status}")
                return []
            data = await response.json()
            # Предполагаемая структура: {"products": [{"name": "...", "quantity": N}, ...]}
            # Если структура другая, нужно адаптировать
            products = data.get("products", [])
            if not products:
                logger.warning("API не вернул список products, проверьте структуру ответа")
                return []
            low_stock = [p for p in products if p.get("quantity", 0) < threshold]
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
            name = product.get("name", "Без названия")
            qty = product.get("quantity", "?")
            message += f"• {name} — осталось: {qty} шт.\n"

        try:
            await bot.send_message(ADMIN_CHAT_ID, message, parse_mode="HTML")
            logger.info(f"Уведомление отправлено: {len(low_stock)} товаров")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление: {e}")


# ==================== ФОНОВАЯ ЗАДАЧА (ПЛАНИРОВЩИК) ====================
async def scheduled_checker():
    """Фоновая задача: периодически вызывает проверку остатков."""
    while True:
        now = datetime.now()
        logger.info(f"Запуск плановой проверки в {now.strftime('%H:%M:%S')}")
        await notify_low_stock()
        await asyncio.sleep(CHECK_INTERVAL)


# ==================== КОМАНДЫ БОТА ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Бот мониторинга остатков запущен!\n"
        "Команда /check — ручная проверка.\n"
        "Уведомления о заканчивающихся товарах будут приходить автоматически."
    )
    logger.info(f"Пользователь {message.from_user.id} использовал /start")


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    """Ручная проверка остатков."""
    await message.answer("🔄 Проверяю остатки...")
    async with aiohttp.ClientSession() as session:
        low_stock = await fetch_low_stock_products(session, LOW_STOCK_THRESHOLD)
        if low_stock:
            text = "⚠️ Товары с низким остатком:\n\n"
            for p in low_stock:
                text += f"• {p.get('name', 'Без названия')} — {p.get('quantity', '?')} шт.\n"
            await message.answer(text)
        else:
            await message.answer("✅ Все товары в достаточном количестве.")
    logger.info(f"Пользователь {message.from_user.id} использовал /check")


# ==================== ЗАПУСК БОТА (POLLING) ====================
async def main():
    # Запускаем фоновую задачу планировщика
    asyncio.create_task(scheduled_checker())
    # Запускаем polling
    logger.info("Бот запущен в режиме polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())