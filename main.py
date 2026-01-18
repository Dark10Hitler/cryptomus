import os
import hashlib
import json
import base64
import asyncio
import requests
from fastapi import FastAPI, Request
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

load_dotenv()

# --- Configuration ---
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
CRYPTOMUS_KEY = os.getenv("CRYPTOMUS_API_KEY")
CRYPTOMUS_MERCHANT = os.getenv("CRYPTOMUS_MERCHANT_ID")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

# --- Database ---
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True)
    username = Column(String)
    balance = Column(Integer, default=3)

Base.metadata.create_all(bind=engine)

# --- Initialization ---
app = FastAPI()
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# --- Keyboards ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 My Profile")],
        [KeyboardButton(text="🚀 Start Using")]
    ], resize_keyboard=True)

def refill_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Add Credits", callback_data="refill_menu")]
    ])

def prices_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 10 Credits — $2", callback_data="buy_2_10")],
        [InlineKeyboardButton(text="🔥 30 Credits — $4", callback_data="buy_4_30")],
        [InlineKeyboardButton(text="🚀 100 Credits — $10", callback_data="buy_10_100")]
    ])

# --- Helper Functions ---
async def call_gpt5(user_prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_URL,
        "X-Title": "Viral AI Script Bot"
    }
    payload = {
        "model": "openai/gpt-5-nano",
        "messages": [
            {
                "role": "system", 
                "content": "You are an expert viral content creator. Write high-engagement video scripts in English. Focus on hooks, pacing, and calls to action."
            },
            {"role": "user", "content": user_prompt}
        ]
    }
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
        return res.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"AI Error: {str(e)}"

def create_invoice(user_id, amount, count):
    payload = {
        "amount": amount,
        "currency": "USD",
        "order_id": f"{user_id}_{count}_{os.urandom(2).hex()}",
        "url_callback": f"{RENDER_URL}/cryptomus_webhook"
    }
    data_json = json.dumps(payload)
    sign = hashlib.md5((base64.b64encode(data_json.encode()).decode() + CRYPTOMUS_KEY).encode()).hexdigest()
    headers = {"merchant": CRYPTOMUS_MERCHANT, "sign": sign, "Content-Type": "application/json"}
    res = requests.post("https://api.cryptomus.com/v1/payment", headers=headers, data=data_json)
    return res.json().get("result", {}).get("url")

# --- Bot Handlers ---

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
    await message.answer(
        "👋 Welcome! I am your AI Scriptwriter powered by GPT-5 Nano.\n"
        "You have **3 free credits** to start.", 
        reply_markup=main_kb()
    )

@dp.message(F.text == "👤 My Profile")
async def view_profile(message: types.Message):
    uid = str(message.from_user.id)
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == uid).first()
    text = (f"🗂 **User Profile**\n\n"
            f"👤 Username: @{user.username or 'N/A'}\n"
            f"🆔 ID: `{user.user_id}`\n"
            f"💰 Balance: **{user.balance}** credits")
    db.close()
    await message.answer(text, reply_markup=refill_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "refill_menu")
async def show_prices(callback: types.CallbackQuery):
    await callback.message.answer("Choose your credit pack:", reply_markup=prices_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    _, price, count = callback.data.split("_")
    url = create_invoice(callback.from_user.id, price, count)
    if url:
        await callback.message.answer(f"🔗 [Click here to pay ${price}]({url})", parse_mode="Markdown")
    else:
        await callback.answer("Error creating invoice. Try again later.", show_alert=True)
    await callback.answer()

@dp.message(F.text == "🚀 Start Using")
async def start_gen(message: types.Message):
    await message.answer("What is your video about? Send me your idea (e.g., 'How to save money' or 'Pizza recipe').")

@dp.message()
async def handle_prompt(message: types.Message):
    if message.text in ["👤 My Profile", "🚀 Start Using"]: return
    
    uid = str(message.from_user.id)
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == uid).first()
    
    if user.balance <= 0:
        await message.answer("❌ Out of credits. Please refill in your profile!", reply_markup=refill_kb())
        db.close()
        return

    msg = await message.answer("🤖 GPT-5 Nano is thinking...")
    result = await call_gpt5(message.text)
    
    user.balance -= 1
    db.commit()
    db.close()
    
    await msg.edit_text(f"{result}\n\n📉 Remaining credits: {user.balance}")

# --- Webhook ---
@app.post("/cryptomus_webhook")
async def webhook(request: Request):
    data = await request.json()
    if data.get('status') in ['paid', 'completed']:
        order_id = data.get('order_id')
        try:
            u_id, count, _ = order_id.split('_')
            db = SessionLocal()
            user = db.query(User).filter(User.user_id == u_id).first()
            if user:
                user.balance += int(count)
                db.commit()
                await bot.send_message(u_id, f"✅ Payment confirmed! {count} credits added to your balance.")
            db.close()
        except: pass
    return {"status": "ok"}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
