import asyncio
import html
import json
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

# ===================== SOZLAMALAR =====================
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("XATO: .env faylida BOT_TOKEN topilmadi!")

ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

CHANNELS = [ch.strip() for ch in os.getenv("CHANNELS", "").split(",") if ch.strip()]

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ===================== TUGMA MATNLARI (bir joyda) =====================
BTN_SEARCH = "🎬 Kino qidirish (Kod orqali)"
BTN_ABOUT = "ℹ️ Bot haqida"
BTN_ADMIN = "⚙️ Admin panel"
BTN_ADD = "🎬 Kino qo'shish"
BTN_DELETE = "🗑 Kino o'chirish"
BTN_STATS = "📊 Statistika"
BTN_LIST = "📋 Kinolar ro'yxati"
BTN_EXIT = "🚪 Paneldan chiqish"
BTN_CANCEL = "❌ Bekor qilish"
BTN_DONE = "✅ Tayyor"

# ===================== MA'LUMOTLAR BAZASI (JSON fayl) =====================
DB_FILE = Path(os.getenv("DATA_FILE", str(Path(__file__).parent / "data.json")))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def normalize_movie(movie: dict) -> dict:
    """Eski formatdagi (bitta video) kinoni yangi formatga (qismlar ro'yxati) o'tkazadi."""
    if "parts" in movie:
        return movie
    return {
        "name": movie.get("name", ""),
        "parts": [{"file_id": movie["file_id"], "type": movie.get("type", "video")}],
    }


def load_db():
    if DB_FILE.exists():
        try:
            data = json.loads(DB_FILE.read_text(encoding="utf-8"))
            movies = {code: normalize_movie(m) for code, m in data.get("movies", {}).items()}
            return movies, set(data.get("users", []))
        except Exception as e:
            logging.error("data.json o'qishda xato: %s", e)
    return {}, set()


def save_db():
    try:
        DB_FILE.write_text(
            json.dumps(
                {"movies": MOVIES_DB, "users": sorted(ALL_USERS)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        logging.error("data.json yozishda xato: %s", e)


MOVIES_DB, ALL_USERS = load_db()
ADD_LOCK = asyncio.Lock()  # bir vaqtda kelgan videolar bir-birini buzmasligi uchun


# ===================== HOLATLAR =====================
class AddMovieState(StatesGroup):
    waiting_for_code = State()
    waiting_for_name = State()
    waiting_for_video = State()


class DeleteMovieState(StatesGroup):
    waiting_for_code = State()


# ===================== YORDAMCHI FUNKSIYALAR =====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def check_subscriptions(user_id: int) -> bool:
    if not CHANNELS:
        return True
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                return False
        except Exception as e:
            logging.warning(
                "%s kanalini tekshirib bo'lmadi (bot kanalda admin emasmi?): %s",
                channel,
                e,
            )
            continue
    return True


def get_subscription_keyboard():
    builder = InlineKeyboardBuilder()
    for i, channel in enumerate(CHANNELS, 1):
        username = channel.replace("@", "")
        builder.button(text=f"📢 {i}-kanalga obuna bo'lish", url=f"https://t.me/{username}")
    builder.button(text="✅ Obunani tekshirish", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()


def get_main_menu(user_id: int):
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_SEARCH)
    builder.button(text=BTN_ABOUT)
    if is_admin(user_id):
        builder.button(text=BTN_ADMIN)
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)


def get_admin_panel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_ADD)
    builder.button(text=BTN_DELETE)
    builder.button(text=BTN_STATS)
    builder.button(text=BTN_LIST)
    builder.button(text=BTN_EXIT)
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_CANCEL)
    return builder.as_markup(resize_keyboard=True)


def get_upload_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_DONE)
    builder.button(text=BTN_CANCEL)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


async def send_movie(message: types.Message, code: str, movie: dict):
    """Kinoning barcha qismlarini ketma-ket yuboradi."""
    parts = movie["parts"]
    total = len(parts)

    if total > 1:
        await message.answer(f"🎞 Bu kino <b>{total} qismdan</b> iborat. Hammasi yuborilmoqda...")

    for i, part in enumerate(parts, 1):
        caption = (
            f"🎬 Nomi: <b>{html.escape(movie['name'])}</b>\n"
            f"📌 Kodi: <code>{html.escape(code)}</code>"
        )
        if total > 1:
            caption += f"\n🎞 Qism: <b>{i}/{total}</b>"
        try:
            if part.get("type") == "document":
                await message.answer_document(
                    document=part["file_id"], caption=caption, protect_content=True
                )
            else:
                await message.answer_video(
                    video=part["file_id"], caption=caption, protect_content=True
                )
        except Exception as e:
            logging.error("%s kodli kinoning %s-qismini yuborib bo'lmadi: %s", code, i, e)
            await message.answer(f"⚠️ {i}-qismni yuborib bo'lmadi.")
        if i < total:
            await asyncio.sleep(0.5)  # Telegram limitiga tushmaslik uchun


# ===================== /start, /panel =====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        save_db()

    if not is_admin(user_id) and not await check_subscriptions(user_id):
        await message.answer(
            "⚠️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'lishingiz shart:\n\n"
            "Obuna bo'lgach, <b>✅ Obunani tekshirish</b> tugmasini bosing:",
            reply_markup=get_subscription_keyboard(),
        )
        return

    await message.answer(
        f"Assalomu alaykum, {html.escape(message.from_user.full_name)}!\n"
        "Kino botiga xush kelibsiz. Kerakli bo'limni tanlang:",
        reply_markup=get_main_menu(user_id),
    )


@dp.message(Command("panel", "admin"))
async def cmd_panel(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizda bu buyruqdan foydalanish huquqi yo'q!")
        return
    await message.answer(
        "👨‍💻 Boshqaruv paneliga xush kelibsiz.\nNimani o'zgartiramiz?",
        reply_markup=get_admin_panel_keyboard(),
    )


@dp.callback_query(F.data == "check_sub")
async def process_check_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await check_subscriptions(user_id):
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            "✅ Rahmat! Obuna tasdiqlandi.",
            reply_markup=get_main_menu(user_id),
        )
        await callback.answer()
    else:
        await callback.answer(
            "❌ Hamma kanallarga obuna bo'lmadingiz yoki bot kanalda admin emas!",
            show_alert=True,
        )


# ===================== BEKOR QILISH (barcha holatlarda ishlaydi) =====================
@dp.message(F.text == BTN_CANCEL)
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    kb = get_admin_panel_keyboard() if is_admin(user_id) else get_main_menu(user_id)
    await message.answer("❌ Amaliyot bekor qilindi.", reply_markup=kb)


# ===================== ADMIN PANEL =====================
@dp.message(F.text == BTN_ADMIN)
async def open_admin_panel(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        await message.answer("❌ Huquqingiz yo'q!")
        return
    await message.answer("👨‍💻 Boshqaruv paneli:", reply_markup=get_admin_panel_keyboard())


@dp.message(F.text == BTN_EXIT)
async def exit_panel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚪 Panelni tark etdingiz. Qayta kirish uchun /panel yoki /admin yuboring.",
        reply_markup=get_main_menu(message.from_user.id),
    )


# ---------- Kino qo'shish ----------
@dp.message(F.text == BTN_ADD)
async def start_add_movie(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🎬 Kino uchun kod kiriting:", reply_markup=get_cancel_keyboard())
    await state.set_state(AddMovieState.waiting_for_code)


@dp.message(AddMovieState.waiting_for_code, F.text)
async def process_movie_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    if code in MOVIES_DB:
        await message.answer(
            f"⚠️ <code>{html.escape(code)}</code> kodi allaqachon mavjud. Boshqa kod kiriting:"
        )
        return
    await state.update_data(code=code)
    await message.answer("📝 Kino nomini kiriting:")
    await state.set_state(AddMovieState.waiting_for_name)


@dp.message(AddMovieState.waiting_for_name, F.text)
async def process_movie_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip(), parts=[])
    await message.answer(
        "📤 Endi kino videosini yuboring.\n\n"
        "Kino qismlardan iborat bo'lsa, <b>1-qism, 2-qism, 3-qism...</b> videolarini "
        "tartib bilan <b>birma-bir</b> yuboring.\n"
        "Hammasini yuborib bo'lgach, <b>✅ Tayyor</b> tugmasini bosing.",
        reply_markup=get_upload_keyboard(),
    )
    await state.set_state(AddMovieState.waiting_for_video)


@dp.message(AddMovieState.waiting_for_video, F.text == BTN_DONE)
async def finish_add_movie(message: types.Message, state: FSMContext):
    async with ADD_LOCK:
        data = await state.get_data()
        parts = data.get("parts", [])
        if not parts:
            await message.answer("⚠️ Hali birorta ham video yuborilmadi. Avval video yuboring.")
            return

        code = data.get("code")
        name = data.get("name")
        MOVIES_DB[code] = {"name": name, "parts": parts}
        save_db()
        await state.clear()

    await message.answer(
        "✅ Kino muvaffaqiyatli qo'shildi!\n\n"
        f"📌 Kod: <code>{html.escape(code)}</code>\n"
        f"🎬 Nomi: <b>{html.escape(name)}</b>\n"
        f"🎞 Qismlar soni: <b>{len(parts)}</b>",
        reply_markup=get_admin_panel_keyboard(),
    )


@dp.message(AddMovieState.waiting_for_video, F.video | F.document)
async def process_movie_video(message: types.Message, state: FSMContext):
    if message.video:
        part = {"file_id": message.video.file_id, "type": "video"}
    else:
        part = {"file_id": message.document.file_id, "type": "document"}

    async with ADD_LOCK:
        data = await state.get_data()
        parts = data.get("parts", [])
        parts.append(part)
        await state.update_data(parts=parts)

    await message.answer(
        f"✅ {len(parts)}-qism qabul qilindi.\n"
        "Keyingi qismni yuboring yoki <b>✅ Tayyor</b> tugmasini bosing."
    )


@dp.message(AddMovieState.waiting_for_video)
async def not_a_video(message: types.Message):
    await message.answer("⚠️ Iltimos, matn emas, aynan video fayl yuboring yoki ✅ Tayyor tugmasini bosing!")


# ---------- Kino o'chirish ----------
@dp.message(F.text == BTN_DELETE)
async def start_delete_movie(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🗑 O'chirmoqchi bo'lgan kinoning kodini kiriting:",
        reply_markup=get_cancel_keyboard(),
    )
    await state.set_state(DeleteMovieState.waiting_for_code)


@dp.message(DeleteMovieState.waiting_for_code, F.text)
async def process_delete_movie_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    if code in MOVIES_DB:
        del MOVIES_DB[code]
        save_db()
        await state.clear()
        await message.answer(
            f"✅ <code>{html.escape(code)}</code> kodli kino (barcha qismlari bilan) bazadan o'chirildi!",
            reply_markup=get_admin_panel_keyboard(),
        )
    else:
        await message.answer("❌ Bunday kodli kino topilmadi. Qaytadan kiriting yoki bekor qiling:")


# ---------- Statistika va ro'yxat ----------
@dp.message(F.text == BTN_STATS)
async def show_statistics(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📊 <b>Bot statistikasi:</b>\n\n"
        f"👥 Foydalanuvchilar soni: <b>{len(ALL_USERS)}</b> ta\n"
        f"🎬 Kinolar soni: <b>{len(MOVIES_DB)}</b> ta"
    )


@dp.message(F.text == BTN_LIST)
async def show_movies_list(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if not MOVIES_DB:
        await message.answer("📭 Hozircha kinolar bazasi bo'sh.")
        return

    lines = ["📋 <b>Bazadagi kinolar:</b>\n"]
    for code, info in MOVIES_DB.items():
        n = len(info["parts"])
        parts_text = f" | 🎞 {n} qism" if n > 1 else ""
        lines.append(
            f"📌 Kod: <code>{html.escape(code)}</code> | Nomi: <b>{html.escape(info['name'])}</b>{parts_text}"
        )
    text = "\n".join(lines)

    # Telegram xabar limiti 4096 belgi — bo'lib yuboramiz
    for i in range(0, len(text), 4000):
        await message.answer(text[i : i + 4000])


# ===================== ODDIY FOYDALANUVCHI =====================
@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()

    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        save_db()

    if not is_admin(user_id) and not await check_subscriptions(user_id):
        await message.answer(
            "⚠️ Avval kanallarga obuna bo'ling!",
            reply_markup=get_subscription_keyboard(),
        )
        return

    if text == BTN_SEARCH:
        await message.answer("🔍 Kinoning kodini yuboring:")
        return

    if text == BTN_ABOUT:
        await message.answer("🤖 Bu bot orqali kinolarni kodlar yordamida tez topishingiz mumkin.")
        return

    movie = MOVIES_DB.get(text)
    if not movie:
        await message.answer("❓ Bunday kodli kino topilmadi yoki noto'g'ri buyruq kiritildi.")
        return

    await send_movie(message, text, movie)


# ===================== ISHGA TUSHIRISH =====================
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())