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
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL") # e.g., https://cryptomus-4djo.onrender.com

# --- Database Setup ---
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True)
    username = Column(String)
    balance = Column(Integer, default=3)

# --- DATABASE FIX: Drop and Recreate to avoid 'username' column error ---
# NOTE: Run this once, then you can comment out 'drop_all' if you want to keep data.
# Base.metadata.drop_all(bind=engine) 
Base.metadata.create_all(bind=engine)

# --- Bot & App Initialization ---
app = FastAPI()
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# --- Keyboards ---
def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 My Profile")],
        [KeyboardButton(text="🚀 Start Using")]
    ], resize_keyboard=True)

def refill_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Add Credits", callback_data="refill_menu")]
    ])

def pricing_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 10 Credits — $2", callback_data="buy_2_10")],
        [InlineKeyboardButton(text="🔥 30 Credits — $4", callback_data="buy_4_30")],
        [InlineKeyboardButton(text="🚀 100 Credits — $10", callback_data="buy_10_100")]
    ])

# --- Logic: AI Generation ---
async def call_ai_model(prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_URL,
        "X-Title": "Viral Script Bot"
    }
    data = {
        "model": "openai/gpt-5-nano",
        "messages": [
            {"role": "system", "content": "You are a professional viral script writer. Provide engaging, high-hook scripts in English."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=60)
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"AI Service Error: {str(e)}"

# --- Logic: Payments ---
def create_payment(user_id, amount, credits_count):
    order_id = f"{user_id}_{credits_count}_{os.urandom(2).hex()}"
    payload = {
        "amount": amount,
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

# --- Bot Handlers ---
@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    uid = str(message.from_user.id)
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == uid).first()
    if not user:
        user = User(user_id=uid, username=message.from_user.username, balance=3)
        db.add(user)
        db.commit()
    db.close()
    await message.answer(
        "👋 **Welcome to Viral AI Scriptwriter!**\n\n"
        "I use GPT-5 Nano to create scripts for your Reels/Shorts/TikTok.\n"
        "🎁 You have **3 free credits** to start!",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "👤 My Profile")
async def profile_handler(message: types.Message):
    uid = str(message.from_user.id)
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == uid).first()
    text = (f"🗂 **User Profile**\n\n"
            f"👤 Username: @{user.username or 'N/A'}\n"
            f"🆔 ID: `{user.user_id}`\n"
            f"💰 Balance: **{user.balance}** credits")
    db.close()
    await message.answer(text, reply_markup=refill_button(), parse_mode="Markdown")

@dp.callback_query(F.data == "refill_menu")
async def show_pricing(callback: types.CallbackQuery):
    await callback.message.answer("Choose your credit pack:", reply_markup=pricing_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def handle_payment(callback: types.CallbackQuery):
    _, price, count = callback.data.split("_")
    pay_url = create_payment(callback.from_user.id, price, count)
    if pay_url:
        await callback.message.answer(f"💳 [Click here to pay ${price} via Cryptomus]({pay_url})", parse_mode="Markdown")
    else:
        await callback.answer("Payment error. Check your API keys.", show_alert=True)

@dp.message(F.text == "🚀 Start Using")
async def prompt_intro(message: types.Message):
    await message.answer("Describe your video idea (e.g., 'A day in the life of a coder' or 'Healthy breakfast recipe').")

@dp.message()
async def generation_handler(message: types.Message):
    if message.text in ["👤 My Profile", "🚀 Start Using"]: return
    
    uid = str(message.from_user.id)
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == uid).first()
    
    if not user or user.balance <= 0:
        await message.answer("❌ Out of credits. Please refill in your profile.", reply_markup=refill_button())
        db.close()
        return

    wait_msg = await message.answer("🤖 GPT-5 Nano is writing your script...")
    script = await call_ai_model(message.text)
    
    user.balance -= 1
    db.commit()
    db.close()
    
    await wait_msg.edit_text(f"{script}\n\n📉 **Remaining balance: {user.balance}**")

# --- Webhook Endpoint ---
@app.post("/cryptomus_webhook")
async def cryptomus_webhook(request: Request):
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
                await bot.send_message(u_id, f"✅ Payment successful! {count} credits added.")
            db.close()
        except: pass
    return {"status": "ok"}

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
