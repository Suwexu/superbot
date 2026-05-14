import os
import asyncio
import logging
import ssl
import tempfile
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

# Данные сертификата для mTLS
CERT_DATA = os.getenv("CLIENT_CERT_DATA")      # Содержимое сертификата (в виде строки PEM)
CERT_PASSWORD = os.getenv("CLIENT_CERT_PASSWORD")  # Пароль от сертификата
# Если сертификат закодирован в base64
CERT_BASE64 = os.getenv("CLIENT_CERT_BASE64")

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID не задан в переменных окружения")

# Проверка наличия сертификата
if not CERT_DATA and not CERT_BASE64:
    raise ValueError("Не задан сертификат: CLIENT_CERT_DATA или CLIENT_CERT_BASE64")

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==================== НАСТРОЙКА SSL С СЕРТИФИКАТОМ ====================
def get_ssl_context():
    """Создаёт SSL-контекст с клиентским сертификатом из переменных окружения"""
    try:
        # Получаем сертификат в формате PEM
        if CERT_BASE64:
            import base64
            cert_pem = base64.b64decode(CERT_BASE64).decode('utf-8')
        else:
            cert_pem = CERT_DATA
        
        # Создаём временные файлы для сертификата и ключа
        # (aiohttp требует файлы, а не строки)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as cert_file:
            cert_file.write(cert_pem)
            cert_path = cert_file.name
        
        # Если пароль есть, используем его для расшифровки
        ssl_context = ssl.create_default_context()
        
        if CERT_PASSWORD:
            # Загружаем сертификат с паролем (для PKCS#12 или зашифрованного PEM)
            # Сначала пробуем как зашифрованный PEM
            try:
                # Сохраняем ключ отдельно (если сертификат содержит и ключ, и пароль)
                ssl_context.load_cert_chain(cert_path, password=CERT_PASSWORD)
                logger.info("Сертификат загружен с паролем (PEM с паролем)")
            except Exception as e1:
                # Если не получилось, пробуем как PKCS#12 (.p12)
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile(mode='wb', suffix='.p12', delete=False) as p12_file:
                        # CERT_DATA может быть в base64 для .p12
                        p12_data = base64.b64decode(CERT_BASE64) if CERT_BASE64 else CERT_DATA.encode('utf-8')
                        p12_file.write(p12_data)
                        p12_path = p12_file.name
                    
                    # Используем openssl через subprocess для конвертации (если доступен)
                    import subprocess
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as pem_file:
                        pem_path = pem_file.name
                    
                    cmd = f"openssl pkcs12 -in {p12_path} -out {pem_path} -nodes -password pass:{CERT_PASSWORD}"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True)
                    
                    ssl_context.load_cert_chain(pem_path)
                    logger.info("Сертификат PKCS#12 сконвертирован и загружен")
                except Exception as e2:
                    logger.error(f"Не удалось загрузить сертификат: {e2}")
                    raise
        else:
            # Без пароля
            ssl_context.load_cert_chain(cert_path)
            logger.info("Сертификат загружен без пароля")
        
        return ssl_context, cert_path
        
    except Exception as e:
        logger.error(f"Ошибка создания SSL-контекста: {e}")
        raise


# Создаём SSL-контекст при старте
try:
    SSL_CONTEXT, TEMP_CERT_PATH = get_ssl_context()
    logger.info("SSL-контекст успешно создан")
except Exception as e:
    logger.error(f"Не удалось инициализировать SSL: {e}")
    raise


# ==================== РАБОТА С API ====================
async def fetch_low_stock_products(session: aiohttp.ClientSession, threshold: int):
    """Получает список товаров с mTLS-аутентификацией"""
    try:
        url = str(API_URL).strip()
        logger.info(f"Запрос к API: {url}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"
        }
        
        # Используем SSL-контекст с сертификатом
        async with session.get(url, headers=headers, ssl=SSL_CONTEXT) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"API вернул статус {response.status}. Тело: {error_text[:200]}")
                return []
            
            data = await response.json()
            logger.info(f"Ответ API получен, тип: {type(data)}")
            
            # Пробуем разные структуры ответа
            products = []
            if isinstance(data, dict) and "products" in data:
                products = data.get("products", [])
            elif isinstance(data, list):
                products = data
            elif isinstance(data, dict) and "data" in data and "products" in data["data"]:
                products = data["data"].get("products", [])
            
            if not products:
                logger.warning(f"Неизвестная структура: {str(data)[:200]}")
                return []
            
            low_stock = [p for p in products if p.get("quantity", 0) < threshold]
            logger.info(f"Всего товаров: {len(products)}, с низким остатком: {len(low_stock)}")
            return low_stock
            
    except Exception as e:
        logger.error(f"Ошибка при запросе к API: {e}")
        return []


async def notify_low_stock():
    """Проверяет остатки и отправляет уведомление"""
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
        
        message += f"\n📅 Проверка: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        
        try:
            await bot.send_message(ADMIN_CHAT_ID, message, parse_mode="HTML")
            logger.info(f"Уведомление отправлено: {len(low_stock)} товаров")
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")


async def scheduled_checker():
    """Фоновая периодическая проверка"""
    await asyncio.sleep(10)
    while True:
        logger.info(f"Плановая проверка в {datetime.now().strftime('%H:%M:%S')}")
        await notify_low_stock()
        await asyncio.sleep(CHECK_INTERVAL)


# ==================== КОМАНДЫ БОТА ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Бот мониторинга остатков запущен!\n\n"
        "/check — ручная проверка\n"
        "/status — статус бота\n\n"
        "📌 Уведомления будут приходить автоматически"
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    ssl_status = "✅ SSL с сертификатом" if SSL_CONTEXT else "⚠️ SSL не настроен"
    await message.answer(
        f"✅ Бот работает\n"
        f"{ssl_status}\n"
        f"⏱ Интервал: {CHECK_INTERVAL} сек\n"
        f"⚠️ Порог: {LOW_STOCK_THRESHOLD} шт\n"
        f"🔗 API: {API_URL[:50]}..."
    )


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    await message.answer("🔄 Проверяю остатки...")
    async with aiohttp.ClientSession() as session:
        low_stock = await fetch_low_stock_products(session, LOW_STOCK_THRESHOLD)
        if low_stock:
            text = "⚠️ <b>Товары с низким остатком:</b>\n\n"
            for p in low_stock:
                name = p.get("name", p.get("title", "Без названия"))
                qty = p.get("quantity", p.get("stock", "?"))
                text += f"• {name} — {qty} шт.\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("✅ Все товары в норме.")


# ==================== ЗАПУСК ====================
async def main():
    logger.info("🚀 Бот запущен (режим polling + mTLS)")
    asyncio.create_task(scheduled_checker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())