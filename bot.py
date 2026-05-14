import os
import asyncio
import logging
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ==================== НАСТРОЙКИ ====================
# Переменные окружения (задаются в Railway)
BOT_TOKEN = os.getenv("8799876662:AAFgzZbDYDK3Bluzc9uCf5fswPtmu6qsjqQ")
ADMIN_CHAT_ID = os.getenv("-5278416334")
API_URL = os.getenv("https://cyberx302.langame.ru/public_api/products/list")
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "5"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))  # 1 час

# Webhook-домен (выдаётся Railway автоматически)
RAILWAY_PUBLIC_DOMAIN = os.getenv("https://superbot-production-5df4.up.railway.app/")
WEBHOOK_PATH = "/webhook"

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token="8799876662:AAFgzZbDYDK3Bluzc9uCf5fswPtmu6qsjqQ")
dp = Dispatcher()


# ==================== РАБОТА С API ====================
async def fetch_low_stock_products(session: aiohttp.ClientSession, threshold: int):
    """Получает список товаров и возвращает те, у которых остаток ниже порога."""
    try:
        async with session.get(API_URL) as response:
            if response.status != 200:
                logger.error(f"API вернул статус {response.status}")
                return []

            data = await response.json()
            # Предполагаемая структура: {"products": [{"name": "...", "quantity": N}, ...]}
            products = data.get("products", [])
            low_stock = [p for p in products if p.get("quantity", 0) < threshold]
            return low_stock
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


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    """Ручная проверка остатков."""
    await message.answer("🔄 Проверяю остатки...")
    async with aiohttp.ClientSession() as session:
        low_stock = await fetch_low_stock_products(session, LOW_STOCK_THRESHOLD)
        if low_stock:
            # Отправляем уведомление тому, кто вызвал команду
            text = "⚠️ Товары с низким остатком:\n\n"
            for p in low_stock:
                text += f"• {p.get('name', 'Без названия')} — {p.get('quantity', '?')} шт.\n"
            await message.answer(text)
        else:
            await message.answer("✅ Все товары в достаточном количестве.")


# ==================== НАСТРОЙКА ВЕБХУКА ====================
async def on_startup(bot: Bot):
    """Выполняется при запуске приложения."""
    # Запускаем фоновую задачу планировщика
    asyncio.create_task(scheduled_checker())

    # Устанавливаем вебхук, если у нас есть домен
    if RAILWAY_PUBLIC_DOMAIN:
        webhook_url = f"https://{RAILWAY_PUBLIC_DOMAIN}{WEBHOOK_PATH}"
        await bot.set_webhook(webhook_url)
        logger.info(f"Webhook установлен: {webhook_url}")
    else:
        logger.warning("RAILWAY_PUBLIC_DOMAIN не задан, вебхук не установлен")


async def on_shutdown(bot: Bot):
    """Выполняется при остановке приложения."""
    await bot.delete_webhook()
    logger.info("Webhook удалён")


# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================
def main():
    # Создаём aiohttp-приложение
    app = web.Application()

    # Регистрируем обработчик вебхуков
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    # Устанавливаем функции запуска и остановки
    app.on_startup.append(lambda _: on_startup(bot))
    app.on_shutdown.append(lambda _: on_shutdown(bot))

    # Запускаем приложение
    port = int(os.getenv("PORT", "8080"))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()