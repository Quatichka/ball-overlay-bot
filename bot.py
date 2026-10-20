"""Telegram-бот: присылаешь видео — он накладывает залипательные шарики.
Когда одна симуляция заканчивается, сразу включается другая случайная."""
import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from render import Options, render_video
from sims import MODE_TITLES, MODES

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or os.environ["TOKEN"]
MAX_SECONDS = int(os.getenv("MAX_SECONDS", "180"))   # длиннее не обрабатываем
WORKERS = int(os.getenv("WORKERS", "1"))              # сколько видео рендерить одновременно
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024                  # Bot API не даёт скачать больше 20 МБ
SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", "settings.json"))

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
sem = asyncio.Semaphore(WORKERS)

DEFAULTS = {"position": "bottom", "size": 0.55, "modes": list(MODES), "sound": True, "backdrop": True}
POSITIONS = {"bottom": "Низ", "center": "Центр", "top": "Верх"}
SIZES = {0.4: "S", 0.55: "M", 0.7: "L"}


# ---------------------------------------------------------------- настройки

def load_all():
    try:
        return json.loads(SETTINGS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


_settings = load_all()


def get(uid):
    return {**DEFAULTS, **_settings.get(str(uid), {})}


def save(uid, s):
    _settings[str(uid)] = s
    try:
        SETTINGS_FILE.write_text(json.dumps(_settings, ensure_ascii=False), "utf-8")
    except OSError:
        log.warning("не удалось сохранить настройки")


def keyboard(s):
    mark = lambda ok, text: ("✅ " if ok else "") + text
    rows = [
        [InlineKeyboardButton(text=mark(s["position"] == k, v), callback_data=f"pos:{k}") for k, v in POSITIONS.items()],
        [InlineKeyboardButton(text=mark(s["size"] == k, f"Размер {v}"), callback_data=f"size:{k}") for k, v in SIZES.items()],
        [InlineKeyboardButton(text=mark(s["sound"], "🔊 Звук ударов"), callback_data="sound"),
         InlineKeyboardButton(text=mark(s["backdrop"], "⚫ Подложка"), callback_data="bd")],
    ]
    modes = [InlineKeyboardButton(text=mark(k in s["modes"], t), callback_data=f"mode:{k}") for k, t in MODE_TITLES.items()]
    rows += [modes[i:i + 2] for i in range(0, len(modes), 2)]
    rows.append([InlineKeyboardButton(text="🎲 Все режимы", callback_data="allmodes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- команды

HELP = (
    "Пришли мне видео (до 20 МБ и до {sec} с), и я наложу на него залипательные шарики: "
    "побег из колец, крутящийся многоугольник, битву шариков и т.д.\n\n"
    "Когда одна симуляция заканчивается, сразу включается следующая случайная, "
    "так что вставка идёт до конца ролика.\n\n"
    "/settings — позиция, размер, звук, режимы\n/demo — пример на чёрном фоне"
)


@dp.message(CommandStart())
@dp.message(Command("help"))
async def start(m: Message):
    await m.answer(HELP.format(sec=MAX_SECONDS))


@dp.message(Command("settings"))
async def settings(m: Message):
    await m.answer("Настройки вставки:", reply_markup=keyboard(get(m.from_user.id)))


@dp.callback_query()
async def on_button(c: CallbackQuery):
    s = get(c.from_user.id)
    key, _, val = (c.data or "").partition(":")
    if key == "pos" and val in POSITIONS:
        s["position"] = val
    elif key == "size":
        s["size"] = float(val)
    elif key == "sound":
        s["sound"] = not s["sound"]
    elif key == "bd":
        s["backdrop"] = not s["backdrop"]
    elif key == "mode" and val in MODES:
        if val in s["modes"]:
            if len(s["modes"]) > 1:
                s["modes"] = [m for m in s["modes"] if m != val]
            else:
                return await c.answer("Должен остаться хотя бы один режим", show_alert=True)
        else:
            s["modes"] = s["modes"] + [val]
    elif key == "allmodes":
        s["modes"] = list(MODES)
    save(c.from_user.id, s)
    try:
        await c.message.edit_reply_markup(reply_markup=keyboard(s))
    except Exception:
        pass
    await c.answer()


@dp.message(Command("demo"))
async def demo(m: Message):
    await process(m, None)


@dp.message(F.video | F.animation | F.video_note | F.document)
async def on_video(m: Message):
    f = m.video or m.animation or m.video_note or m.document
    if m.document and not (m.document.mime_type or "").startswith("video/"):
        return await m.reply("Это не видео 🙂 Пришли mp4/mov.")
    if f.file_size and f.file_size > TG_DOWNLOAD_LIMIT:
        return await m.reply("Файл больше 20 МБ — Telegram не даёт ботам скачивать такие. Сожми видео и пришли снова.")
    dur = getattr(f, "duration", None)
    if dur and dur > MAX_SECONDS:
        return await m.reply(f"Видео длиннее {MAX_SECONDS} с. Обрежь его и пришли снова.")
    await process(m, f)


# ---------------------------------------------------------------- обработка

async def process(m: Message, file):
    s = get(m.from_user.id)
    status = await m.reply("⏳ В очереди…" if sem.locked() else "⏳ Начинаю…")
    loop = asyncio.get_running_loop()
    try:
        async with sem:
            tmp = Path(tempfile.mkdtemp(prefix="balls_"))
            try:
                src = None
                if file is not None:
                    await status.edit_text("📥 Скачиваю видео…")
                    src = str(tmp / "in.mp4")
                    await bot.download(file, destination=src)

                def progress(pct):
                    asyncio.run_coroutine_threadsafe(_edit(status, f"🎬 Рендерю… {pct}%"), loop)

                opts = Options(position=s["position"], size=s["size"], modes=s["modes"],
                               sound=s["sound"], backdrop=s["backdrop"])
                dst = str(tmp / "out.mp4")
                res = await asyncio.to_thread(render_video, src, dst, opts, progress, str(tmp), 20, MAX_SECONDS)
                await status.edit_text("📤 Отправляю…")
                caption = "Вставки: " + ", ".join(MODE_TITLES[k] for k in res["played"])
                await m.reply_video(FSInputFile(dst, filename="balls.mp4"), caption=caption, supports_streaming=True)
                await status.delete()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        log.exception("render failed")
        await _edit(status, f"❌ Не получилось: {str(e)[:300]}")


async def _edit(msg: Message, text):
    try:
        await msg.edit_text(text)
    except Exception:
        pass  # «message is not modified» и прочие мелочи


async def main():
    log.info("bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
