import os
import asyncio
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

# --- Конфигурация ---
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

# --- База данных ---
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True)
    username = Column(String)
    balance = Column(Integer, default=10)
    last_bonus = Column(DateTime, default=datetime.utcnow() - timedelta(days=1))

Base.metadata.create_all(bind=engine)

# Авто-миграция (на всякий случай)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus TIMESTAMP;"))
        conn.commit()
    except Exception: pass

# --- Бот и Приложение ---
app = FastAPI()
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# --- Клавиатуры (Inline под текстом) ---
def get_main_menu():
    buttons = [
        [InlineKeyboardButton(text="🚀 Create New Script", callback_data="start_ai")],
        [InlineKeyboardButton(text="👤 My Profile / Balance", callback_data="view_profile")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_profile_menu():
    buttons = [
        [InlineKeyboardButton(text="🎁 Get Daily +5 Credits", callback_data="get_bonus")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Логика AI ---
async def fetch_ai_script(prompt):
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-5-nano", # Или используй gpt-3.5-turbo для надежности
        "messages": [
            {"role": "system", "content": "You are a professional viral scriptwriter. Write a structured script in English with a strong hook."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
        return res.json()['choices'][0]['message']['content']
    except Exception:
        return "⚠️ AI service is currently unavailable. Please try again in a minute."

# --- Обработчики сообщений ---

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
    if not user:
        user = User(user_id=str(message.from_user.id), username=message.from_user.username, balance=10)
        db.add(user)
        db.commit()
    db.close()
    
    welcome_text = (
        f"👋 **Welcome, {message.from_user.first_name}!**\n\n"
        "I am your AI Scriptwriter. I can create viral content for your videos.\n\n"
        "✨ **What can I do?** Just send me a topic and I'll write a script!"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "view_profile")
async def callback_profile(callback: types.CallbackQuery):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(callback.from_user.id)).first()
    
    # Расчет времени до следующего бонуса
    now = datetime.utcnow()
    next_bonus_time = user.last_bonus + timedelta(days=1)
    wait_time = next_bonus_time - now
    
    if wait_time.total_seconds() > 0:
        hours, remainder = divmod(int(wait_time.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        timer_text = f"⏳ Next refill in: **{hours}h {minutes}m**"
    else:
        timer_text = "🎁 **Daily bonus is available!**"

    profile_text = (
        "📋 **YOUR ACCOUNT INFO**\n"
        "──────────────────\n"
        f"👤 **User:** @{user.username or 'N/A'}\n"
        f"🆔 **ID:** `{user.user_id}`\n"
        f"💰 **Balance:** `{user.balance}` scripts\n"
        "──────────────────\n"
        f"{timer_text}"
    )
    db.close()
    await callback.message.edit_text(profile_text, reply_markup=get_profile_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "get_bonus")
async def callback_bonus(callback: types.CallbackQuery):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(callback.from_user.id)).first()
    
    now = datetime.utcnow()
    if now > user.last_bonus + timedelta(days=1):
        user.balance += 5
        user.last_bonus = now
        db.commit()
        await callback.answer("✅ Success! +5 credits added to your balance.", show_alert=True)
    else:
        await callback.answer("❌ Bonus is not available yet. Come back later!", show_alert=True)
    
    db.close()
    await callback_profile(callback)

@dp.callback_query(F.data == "start_ai")
async def callback_ai_prompt(callback: types.CallbackQuery):
    await callback.message.answer("📝 **Send me the topic of your video.**\nExample: 'How to lose weight in 30 days' or 'Top 5 crypto tips'.")
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def callback_main(callback: types.CallbackQuery):
    await callback.message.edit_text("🏠 **Main Menu**\nChoose an action below:", reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.message()
async def handle_ai_request(message: types.Message):
    if not message.text or message.text.startswith("/"): return

    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
    
    if not user or user.balance <= 0:
        await message.answer("❌ **Insufficient balance!**\nGet your daily bonus in the profile menu.", reply_markup=get_main_menu())
        db.close()
        return

    wait_msg = await message.answer("🤖 **AI is thinking...** Please wait.")
    
    # Тратим баланс
    user.balance -= 1
    db.commit()
    rem_balance = user.balance
    db.close()

    script = await fetch_ai_script(message.text)
    
    final_text = (
        f"🎬 **YOUR SCRIPT:**\n\n{script}\n\n"
        "──────────────────\n"
        f"💰 **Balance left:** `{rem_balance}`"
    )
    await wait_msg.edit_text(final_text, reply_markup=get_main_menu(), parse_mode="Markdown")

# --- Запуск ---
@app.on_event("startup")
async def on_startup():
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))

@app.get("/")
async def root():
    return {"status": "Bot is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
