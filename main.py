import os
import asyncio
import logging
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

# --- 0. Настройка Логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# --- 1. Конфигурация ---
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

# --- 2. База данных ---
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True)
    username = Column(String)
    balance = Column(Integer, default=10)
    last_bonus = Column(DateTime, nullable=True)

Base.metadata.create_all(bind=engine)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus TIMESTAMP;"))
        conn.commit()
    except Exception: pass

# --- 3. Инициализация ---
app = FastAPI()
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# --- 4. Меню ---
def get_main_menu():
    buttons = [
        [InlineKeyboardButton(text="🚀 Create New Script", callback_data="start_ai")],
        [InlineKeyboardButton(text="👤 My Profile / Balance", callback_data="view_profile")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_profile_menu():
    buttons = [
        [InlineKeyboardButton(text="🎁 Get Daily +5 Credits", callback_data="get_bonus")],
        [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- 5. Логика AI ---
async def fetch_ai_script(prompt):
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-5-nano",
        "messages": [
            {"role": "system", "content": "You are a viral scriptwriter. Write a short, engaging script in English."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
        return res.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "⚠️ AI service is currently unavailable. Please try again."

# --- 6. Обработчики ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
        if not user:
            yesterday = datetime.utcnow() - timedelta(days=1)
            user = User(user_id=str(message.from_user.id), username=message.from_user.username, balance=10, last_bonus=yesterday)
            db.add(user)
            db.commit()
    except Exception: pass
    finally: db.close()
    
    await message.answer("👋 **Hello!** I am your AI Scriptwriter.\n\n👇 Choose an option:", reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "view_profile")
async def callback_profile(callback: types.CallbackQuery):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(callback.from_user.id)).first()
    
    now = datetime.utcnow()
    if user.last_bonus is None:
        timer_text = "🎁 **Daily bonus available!**"
    else:
        next_bonus_time = user.last_bonus + timedelta(days=1)
        wait_time = next_bonus_time - now
        if wait_time.total_seconds() > 0:
            hours, remainder = divmod(int(wait_time.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            timer_text = f"⏳ Next bonus in: **{hours}h {minutes}m**"
        else:
            timer_text = "🎁 **Daily bonus available!**"

    text_content = (
        "📋 **YOUR PROFILE**\n──────────────────\n"
        f"👤 **User:** @{user.username or 'NoName'}\n"
        f"🆔 **ID:** `{user.user_id}`\n"
        f"💰 **Balance:** `{user.balance}` scripts\n"
        "──────────────────\n"
        f"{timer_text}"
    )
    db.close()
    try: await callback.message.edit_text(text_content, reply_markup=get_profile_menu(), parse_mode="Markdown")
    except TelegramBadRequest: pass
    await callback.answer()

@dp.callback_query(F.data == "get_bonus")
async def callback_bonus(callback: types.CallbackQuery):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(callback.from_user.id)).first()
    now = datetime.utcnow()
    
    if user.last_bonus is None or now > user.last_bonus + timedelta(days=1):
        user.balance += 5
        user.last_bonus = now
        db.commit()
        await callback.answer("✅ +5 credits added!", show_alert=True)
        db.close()
        await callback_profile(callback) 
    else:
        db.close()
        await callback.answer("❌ Too early! Check the timer.", show_alert=True)

@dp.callback_query(F.data == "start_ai")
async def callback_ai_prompt(callback: types.CallbackQuery):
    await callback.message.answer("📝 **Write your video topic below:**")
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def callback_main(callback: types.CallbackQuery):
    try: await callback.message.edit_text("🏠 **Main Menu**", reply_markup=get_main_menu(), parse_mode="Markdown")
    except TelegramBadRequest: pass
    await callback.answer()

@dp.message()
async def handle_ai_request(message: types.Message):
    if not message.text or message.text.startswith("/"): return
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
    if not user or user.balance <= 0:
        await message.answer("❌ **Insufficient credits!**", reply_markup=get_main_menu())
        db.close()
        return
    
    wait_msg = await message.answer("🤖 **Generating script...**")
    user.balance -= 1
    db.commit()
    rem_balance = user.balance
    db.close()

    script = await fetch_ai_script(message.text)
    await wait_msg.edit_text(f"🎬 **RESULT:**\n\n{script}\n\n📉 Credits left: `{rem_balance}`", reply_markup=get_main_menu(), parse_mode="Markdown")

# --- 7. Запуск (С ИСПРАВЛЕНИЕМ ПОРТА) ---
async def start_bot_delayed():
    # Ждем 3 секунды, чтобы FastAPI успел запуститься и привязаться к порту
    await asyncio.sleep(3)
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot, allowed_updates=["message", "callback_query"]))

@app.on_event("startup")
async def on_startup():
    # Запускаем бота отдельно от основного потока
    asyncio.create_task(start_bot_delayed())

@app.get("/")
async def root():
    return {"status": "OK", "message": "Bot is running"}

if __name__ == "__main__":
    import uvicorn
    # ВАЖНО: host должен быть 0.0.0.0
    port = int(os.getenv("PORT", 10000)) # Render обычно использует 10000
    uvicorn.run(app, host="0.0.0.0", port=port)
