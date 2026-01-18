import os
import hashlib
import json
import base64
import asyncio
import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

# --- Конфигурация ---
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CRYPTOMUS_KEY = os.getenv("CRYPTOMUS_API_KEY")
CRYPTOMUS_MERCHANT = os.getenv("CRYPTOMUS_MERCHANT_ID")
# Render автоматически выдает URL, лучше всего задать его в Environment Variables как RENDER_EXTERNAL_URL
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://scenaries.onrender.com")

# --- База Данных ---
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True)
    balance = Column(Integer, default=3)

Base.metadata.create_all(bind=engine)

# --- Инициализация ---
app = FastAPI()
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# --- Логика Оплаты ---
def create_cryptomus_invoice(user_id: str, amount: str, count: int):
    payload = {
        "amount": amount,
        "currency": "USD",
        "order_id": f"{user_id}_{count}_{os.urandom(2).hex()}",
        "url_callback": f"{RENDER_URL}/cryptomus_webhook",
        "lifetime": 3600
    }
    
    data_json = json.dumps(payload)
    data_base64 = base64.b64encode(data_json.encode()).decode()
    sign = hashlib.md5((data_base64 + CRYPTOMUS_KEY).encode()).hexdigest()
    
    headers = {
        "merchant": CRYPTOMUS_MERCHANT,
        "sign": sign,
        "Content-Type": "application/json"
    }
    
    try:
        res = requests.post("https://api.cryptomus.com/v1/payment", headers=headers, data=data_json, timeout=15)
        response_data = res.json()
        print(f"Cryptomus Invoice Created: {response_data}") # Лог в Render
        if response_data.get("state") == 0:
            return response_data.get("result", {}).get("url")
    except Exception as e:
        print(f"Ошибка создания счета: {e}")
    return None

# --- Обработка команд бота ---

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    user_id = str(message.from_user.id)
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()
    
    if not user:
        user = User(user_id=user_id, balance=3)
        db.add(user)
        db.commit()
        welcome_text = "🎁 Добро пожаловать! Вам начислено **3 бесплатных лимита**.\n\n"
    else:
        welcome_text = f"👤 Ваш баланс: **{user.balance}** запросов.\n\n"
    
    db.close()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 10 Сценариев — $2", callback_data="buy_2_10")],
        [InlineKeyboardButton(text="🔥 30 Сценариев — $4", callback_data="buy_4_30")],
        [InlineKeyboardButton(text="🚀 100 Сценариев — $10", callback_data="buy_10_100")],
        [InlineKeyboardButton(text="🌐 Перейти на сайт", url="https://aura-dialogue-stream.vercel.app")]
    ])

    await message.answer(
        f"{welcome_text}Я — AI-ассистент для создания вирального контента. "
        "Выберите пакет лимитов для пополнения баланса:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("buy_"))
async def process_payment(callback: types.CallbackQuery):
    _, price, count = callback.data.split("_")
    uid = str(callback.from_user.id)
    pay_url = create_cryptomus_invoice(uid, price, int(count))
    
    if pay_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить сейчас", url=pay_url)]
        ])
        await callback.message.answer(
            f"🏷 Заказ: {count} лимитов\n💰 Сумма: ${price}\n\n"
            "После оплаты баланс пополнится автоматически в течение пары минут.",
            reply_markup=kb
        )
    else:
        await callback.answer("Ошибка создания счета. Попробуйте позже.", show_alert=True)

# --- Вебхук для начисления баланса ---
@app.post("/cryptomus_webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        print(f"Webhook received: {data}") # Важный лог для отладки
        
        if data.get('status') in ['paid', 'completed']:
            order_id = data.get('order_id')
            parts = order_id.split('_')
            if len(parts) >= 2:
                u_id, count = parts[0], int(parts[1])
                db = SessionLocal()
                user = db.query(User).filter(User.user_id == u_id).first()
                if user:
                    user.balance += count
                    db.commit()
                    print(f"Success! Added {count} credits to user {u_id}")
                    try:
                        await bot.send_message(u_id, f"✅ Оплата подтверждена! Вам начислено {count} лимитов. Обновите страницу на сайте.")
                    except Exception as e:
                        print(f"TG Notification Error: {e}")
                db.close()
    except Exception as e:
        print(f"Webhook Error: {e}")
    return {"status": "ok"}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    # Порт берем из переменной окружения Render
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)