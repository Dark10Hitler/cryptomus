import os
import hashlib
import json
import base64
import asyncio
import requests
from fastapi import FastAPI, Request
from sqlalchemy import create_engine, Column, Integer, String, text
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

load_dotenv()

# --- Конфигурация ---
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
CRYPTOMUS_KEY = os.getenv("CRYPTOMUS_API_KEY")
CRYPTOMUS_MERCHANT = os.getenv("CRYPTOMUS_MERCHANT_ID")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

# --- База данных ---
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True)
    username = Column(String)
    balance = Column(Integer, default=3)

# Создаем таблицы
Base.metadata.create_all(bind=engine)

# АВТО-ИСПРАВЛЕНИЕ: Добавляем колонку username, если её нет в старой базе
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR;"))
        conn.commit()
    except Exception as e:
        print(f"Migration notice: {e}")

# --- Инициализация ---
app = FastAPI()
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# --- Клавиатуры ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 My Profile")],
        [KeyboardButton(text="🚀 Start Using")]
    ], resize_keyboard=True)

def profile_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Add Credits", callback_data="refill_menu")]
    ])

def pricing_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 10 Scripts — $2", callback_data="buy_2_10")],
        [InlineKeyboardButton(text="🔥 30 Scripts — $4", callback_data="buy_4_30")],
        [InlineKeyboardButton(text="🚀 100 Scripts — $10", callback_data="buy_10_100")]
    ])

# --- Логика Cryptomus ---
def create_cryptomus_invoice(user_id, amount, count):
    order_id = f"{user_id}_{count}_{os.urandom(2).hex()}"
    payload = {
        "amount": str(amount),
        "currency": "USD",
        "order_id": order_id,
        "url_callback": f"{RENDER_URL}/cryptomus_webhook"
    }
    data_json = json.dumps(payload)
    data_base64 = base64.b64encode(data_json.encode()).decode()
    sign = hashlib.md5((data_base64 + CRYPTOMUS_KEY).encode()).hexdigest()
    
    headers = {"merchant": CRYPTOMUS_MERCHANT, "sign": sign, "Content-Type": "application/json"}
    try:
        res = requests.post("https://api.cryptomus.com/v1/payment", headers=headers, data=data_json)
        return res.json().get("result", {}).get("url")
    except:
        return None

# --- Логика AI ---
async def fetch_ai_script(prompt):
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-5-nano",
        "messages": [
            {"role": "system", "content": "You are a viral video scriptwriter. Write a high-hook English script."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
        return res.json()['choices'][0]['message']['content']
    except:
        return "⚠️ AI service is busy. Please try again later."

# --- Обработчики бота ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    uid = str(message.from_user.id)
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == uid).first()
    if not user:
        user = User(user_id=uid, username=message.from_user.username, balance=3)
        db.add(user)
        db.commit()
    db.close()
    await message.answer(f"Hi {message.from_user.first_name}! 🚀\nI'm your AI Scriptwriter. You have 3 free credits.", reply_markup=main_kb())

@dp.message(F.text == "👤 My Profile")
async def cmd_profile(message: types.Message):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
    db.close()
    await message.answer(f"👤 @{user.username}\n💰 Balance: {user.balance} scripts", reply_markup=profile_kb())

@dp.callback_query(F.data == "refill_menu")
async def refill_menu(callback: types.CallbackQuery):
    await callback.message.answer("Select a package:", reply_markup=pricing_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    _, price, count = callback.data.split("_")
    url = create_cryptomus_invoice(callback.from_user.id, price, count)
    if url:
        await callback.message.answer(f"🔗 [Pay ${price} via Cryptomus]({url})", parse_mode="Markdown")
    else:
        await callback.answer("Payment error!")

@dp.message(F.text == "🚀 Start Using")
async def cmd_use(message: types.Message):
    await message.answer("What is the video topic?")

@dp.message()
async def handle_request(message: types.Message):
    if message.text in ["👤 My Profile", "🚀 Start Using"]: return
    
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
    
    if user.balance <= 0:
        await message.answer("❌ No credits left!", reply_markup=profile_kb())
        db.close()
        return

    status = await message.answer("🤖 Writing...")
    script = await fetch_ai_script(message.text)
    
    user.balance -= 1
    db.commit()
    db.close()
    await status.edit_text(f"{script}\n\n📉 Credits: {user.balance}")

# --- Webhook & Startup ---
@app.post("/cryptomus_webhook")
async def webhook(request: Request):
    data = await request.json()
    if data.get('status') in ['paid', 'completed']:
        u_id, count, _ = data.get('order_id').split('_')
        db = SessionLocal()
        user = db.query(User).filter(User.user_id = u_id).first()
        if user:
            user.balance += int(count)
            db.commit()
            await bot.send_message(u_id, f"✅ Balance updated: +{count}")
        db.close()
    return {"status": "ok"}

@app.on_event("startup")
async def on_startup():
    # Удаляем вебхуки перед запуском, чтобы избежать Conflict
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
