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

# --- 0. Настройка Логирования (Чтобы видеть ошибки кнопок) ---
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
    last_bonus = Column(DateTime, default=datetime.utcnow() - timedelta(days=1))

# Создание таблиц и миграция
Base.metadata.create_all(bind=engine)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus TIMESTAMP;"))
        conn.commit()
    except Exception: pass

# --- 3. Инициализация Бота ---
app = FastAPI()
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# --- 4. Меню (Клавиатуры) ---
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

# --- 6. Обработчики (Handlers) ---

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    logger.info(f"Command /start from {message.from_user.id}")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
        if not user:
            user = User(user_id=str(message.from_user.id), username=message.from_user.username, balance=10)
            db.add(user)
            db.commit()
    except Exception as e:
        logger.error(f"DB Error on start: {e}")
    finally:
        db.close()
    
    welcome_text = (
        f"👋 **Hello, {message.from_user.first_name}!**\n\n"
        "I am your AI Scriptwriter.\n"
        "Sending a topic will cost **1 credit**.\n\n"
        "👇 Choose an option:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")

# --- ОБРАБОТЧИК ПРОФИЛЯ ---
@dp.callback_query(F.data == "view_profile")
async def callback_profile(callback: types.CallbackQuery):
    logger.info(f"Button pressed: view_profile by {callback.from_user.id}")
    
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(callback.from_user.id)).first()
    
    # Расчет таймера
    now = datetime.utcnow()
    next_bonus_time = user.last_bonus + timedelta(days=1)
    wait_time = next_bonus_time - now
    
    if wait_time.total_seconds() > 0:
        hours, remainder = divmod(int(wait_time.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        timer_text = f"⏳ Next bonus in: **{hours}h {minutes}m**"
    else:
        timer_text = "🎁 **Daily bonus available!**"

    text_content = (
        "📋 **YOUR PROFILE**\n"
        "──────────────────\n"
        f"👤 **User:** @{user.username or 'NoName'}\n"
        f"🆔 **ID:** `{user.user_id}`\n"
        f"💰 **Balance:** `{user.balance}` scripts\n"
        "──────────────────\n"
        f"{timer_text}"
    )
    db.close()
    
    try:
        await callback.message.edit_text(text_content, reply_markup=get_profile_menu(), parse_mode="Markdown")
    except TelegramBadRequest:
        # Игнорируем ошибку, если текст не изменился (пользователь нажал кнопку дважды)
        await callback.answer()
    
    await callback.answer() # Важно! Убирает часики

# --- ОБРАБОТЧИК БОНУСА ---
@dp.callback_query(F.data == "get_bonus")
async def callback_bonus(callback: types.CallbackQuery):
    logger.info(f"Button pressed: get_bonus by {callback.from_user.id}")
    
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(callback.from_user.id)).first()
    
    now = datetime.utcnow()
    if now > user.last_bonus + timedelta(days=1):
        user.balance += 5
        user.last_bonus = now
        db.commit()
        await callback.answer("✅ +5 credits added!", show_alert=True)
        # Обновляем текст профиля сразу
        db.close()
        # Вызываем функцию профиля, чтобы обновить текст
        await callback_profile(callback) 
    else:
        db.close()
        await callback.answer("❌ Too early! Check the timer.", show_alert=True)

# --- ОБРАБОТЧИК СОЗДАНИЯ СКРИПТА ---
@dp.callback_query(F.data == "start_ai")
async def callback_ai_prompt(callback: types.CallbackQuery):
    logger.info("Button pressed: start_ai")
    await callback.message.answer("📝 **Write your video topic below:**\n(e.g., 'Fitness tips for beginners')")
    await callback.answer()

# --- КНОПКА НАЗАД ---
@dp.callback_query(F.data == "back_to_main")
async def callback_main(callback: types.CallbackQuery):
    logger.info("Button pressed: back_to_main")
    try:
        await callback.message.edit_text("🏠 **Main Menu**", reply_markup=get_main_menu(), parse_mode="Markdown")
    except TelegramBadRequest:
        pass
    await callback.answer()

# --- ОБРАБОТЧИК ТЕКСТА (AI) ---
@dp.message()
async def handle_ai_request(message: types.Message):
    if not message.text or message.text.startswith("/"): return
    
    logger.info(f"Received text prompt from {message.from_user.id}")

    db = SessionLocal()
    user = db.query(User).filter(User.user_id == str(message.from_user.id)).first()
    
    if not user or user.balance <= 0:
        await message.answer("❌ **Insufficient credits!**\nGo to Profile -> Get Daily Bonus.", reply_markup=get_main_menu())
        db.close()
        return

    wait_msg = await message.answer("🤖 **Generating script...**")
    
    user.balance -= 1
    db.commit()
    rem_balance = user.balance
    db.close()

    script = await fetch_ai_script(message.text)
    
    final_text = (
        f"🎬 **RESULT:**\n\n{script}\n\n"
        f"📉 Credits left: `{rem_balance}`"
    )
    await wait_msg.edit_text(final_text, reply_markup=get_main_menu(), parse_mode="Markdown")

# --- 7. Запуск (Polling) ---
@app.on_event("startup")
async def on_startup():
    # Удаляем старые вебхуки, чтобы polling работал корректно
    await bot.delete_webhook(drop_pending_updates=True)
    # Явно указываем allowed_updates, чтобы кнопки работали
    asyncio.create_task(
        dp.start_polling(
            bot, 
            allowed_updates=["message", "callback_query"]
        )
    )

@app.get("/")
async def root():
    return {"status": "Bot is operational"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
