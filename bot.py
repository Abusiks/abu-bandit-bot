# pip install pytelegrambotapi

import telebot
from telebot import types
import json
import os
import time
import random  # 💥 для крит-кликов и немного рандома

# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("BOT_TOKEN")

DATA_FILE = "game_data.json"

CHARACTERS = ["Гитин", "Abus", "Махач", "Джамал", "Азамат", "Омаров", "Зайпа"]
MAX_LEVEL_PER_CHAR = 10

MAX_EARN_UPGRADE = 25
LATYAO_DURATION = 5 * 60  # 5 минут в секундах
LATYAO_COST = 1000        # жиркоинов

# ✅ УМЕНЬШЕННАЯ СТОИМОСТЬ УЛУЧШЕНИЙ
EARN_UPGRADE_BASE_COST = 250  # было 1000, теперь проще качаться

# 🎁 ЕЖЕДНЕВНЫЙ БОНУС
DAILY_COOLDOWN = 24 * 60 * 60          # 24 часа
DAILY_BASE_REWARD = 500                # базовая награда
DAILY_STREAK_BONUS = 250               # прибавка за каждый день стрика
DAILY_MAX_STREAK_FOR_BONUS = 7         # после 7 дней награда перестаёт расти

# 💥 КРИТ-КЛИК
CRIT_CHANCE = 0.05      # 5% шанс
CRIT_MULTIPLIER = 5     # x5 от обычного клика

# 🏅 ДОСТИЖЕНИЯ
ACHIEVEMENTS_DEFS = {
    "coins_1000": {
        "title": "Жирный старт",
        "desc": "Накопи 1000 жиркоинов на балансе.",
        "reward": 200,
    },
    "coins_10000": {
        "title": "Местный олигарх",
        "desc": "Накопи 10 000 жиркоинов на балансе.",
        "reward": 1000,
    },
    "first_latyao": {
        "title": "Острый любитель",
        "desc": "Купи Латяо хотя бы один раз.",
        "reward": 500,
    },
    "first_max_char": {
        "title": "Первый максимум",
        "desc": "Докачай любого абу-бандита до 10 уровня.",
        "reward": 1000,
    },
}

# --- Механики (как и раньше) ---
# 1) Прокачка заработка:
#    - earn_upgrade = 0: 1 жиркоин/клик
#    - уровень 1: 25/клик, каждый следующий +1 (до 49)
#
# 2) Цены уровней персонажей:
#    - первый персонаж: 1500, 2000, ..., 6000
#    - следующему +20% (1.2 ** index)
#
# 3) Улучшения заработка:
#    - стоимость следующего уровня = 250 * номер_уровня
#      (1-й = 250, 2-й = 500, 3-й = 750 и т.д.)


# ================== ХРАНЕНИЕ ДАННЫХ ==================

user_data = {}  # {str(user_id): {...}}


def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        except Exception:
            user_data = {}
    else:
        user_data = {}


def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
    except Exception:
        # в бою лучше логировать ошибку
        pass


def get_user_id(message_or_call):
    if hasattr(message_or_call, "from_user"):
        return str(message_or_call.from_user.id)
    return str(message_or_call.message.from_user.id)


def get_display_name(telegram_user):
    return telegram_user.first_name or telegram_user.username or f"Игрок_{telegram_user.id}"


def ensure_user(message):
    """Создаёт запись пользователя, если её ещё нет, и добавляет новые поля для старых."""
    uid = get_user_id(message)
    if uid not in user_data:
        user_data[uid] = {
            "coins": 0,
            "levels": [0] * len(CHARACTERS),
            "current_char": 0,
            "earn_upgrade": 0,
            "latyao_until": 0,
            "name": get_display_name(message.from_user),
            "created_at": time.time(),
            # 🎁 Ежедневный бонус
            "last_daily": 0,
            "daily_streak": 0,
            # 🏅 Достижения
            "achievements": [],
        }
        save_data()
    else:
        u = user_data[uid]
        name_now = get_display_name(message.from_user)
        if u.get("name") != name_now:
            u["name"] = name_now
        # гарантируем наличие новых полей у старых игроков
        u.setdefault("last_daily", 0)
        u.setdefault("daily_streak", 0)
        u.setdefault("achievements", [])
        save_data()

    return user_data[uid]


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ИГРЫ ==================

def is_latyao_active(user):
    return time.time() < user.get("latyao_until", 0)


def get_base_earn_per_click(user):
    """Базовый заработок без Латяо, с учётом уровня улучшения."""
    lvl = user.get("earn_upgrade", 0)
    if lvl == 0:
        return 1
    return 25 + (lvl - 1)


def get_effective_earn_per_click(user):
    """Фактический заработок за клик с учётом Латяо."""
    base = get_base_earn_per_click(user)
    if is_latyao_active(user):
        return base * 2
    return base


def get_level_cost(char_index: int, next_level: int) -> int:
    base_first_char = 1500 + (next_level - 1) * 500
    factor = 1.2 ** char_index
    return int(base_first_char * factor)


def get_next_upgrade_cost(user):
    """Стоимость следующего уровня улучшения заработка."""
    current = user.get("earn_upgrade", 0)
    if current >= MAX_EARN_UPGRADE:
        return None
    next_level = current + 1
    return EARN_UPGRADE_BASE_COST * next_level


def get_max_available_character_index(user) -> int:
    """Максимально доступный персонаж (следующий открывается после 10 уровня предыдущего)."""
    levels = user.get("levels", [0] * len(CHARACTERS))
    max_index = 0
    for i in range(len(CHARACTERS) - 1):
        if levels[i] >= MAX_LEVEL_PER_CHAR:
            max_index = i + 1
        else:
            break
    return max_index


def calculate_power(user):
    """Сила пользователя для лидерборда."""
    levels = user.get("levels", [0] * len(CHARACTERS))
    best_char = 0
    for i, lvl in enumerate(levels):
        if lvl > 0:
            best_char = i
    best_level = levels[best_char]
    total_levels = sum(levels)
    coins = user.get("coins", 0)
    return (best_char, best_level, total_levels, coins)


def format_stats(user):
    levels = user["levels"]
    cur_idx = user["current_char"]
    cur_name = CHARACTERS[cur_idx]
    cur_level = levels[cur_idx]
    coins = user["coins"]
    earn_lvl = user["earn_upgrade"]
    per_click = get_effective_earn_per_click(user)
    base_per_click = get_base_earn_per_click(user)

    latyao_str = "нет"
    if is_latyao_active(user):
        left = int(user["latyao_until"] - time.time())
        if left < 0:
            left = 0
        minutes = left // 60
        seconds = left % 60
        latyao_str = f"активно ещё {minutes} мин {seconds} сек"

    streak = user.get("daily_streak", 0)
    lines = [
        f"<b>👤 Имя:</b> {user.get('name', 'Игрок')}",
        f"<b>💰 Жиркоины:</b> {coins}",
        "",
        f"<b>🧨 Текущий абу-бандит:</b> {cur_name} (уровень {cur_level}/{MAX_LEVEL_PER_CHAR})",
        "",
        "<b>📈 Прокачка заработка:</b>",
        f"• уровень улучшения: {earn_lvl}/{MAX_EARN_UPGRADE}",
        f"• базовый заработок: {base_per_click} жиркоинов/клик",
        f"• текущий заработок (с учётом Латяо): {per_click} жиркоинов/клик",
        "",
        f"<b>🔥 Латяо:</b> {latyao_str}",
        "",
        f"<b>🎁 Ежедневный стрик:</b> {streak} дней подряд",
        "",
        "<b>📊 Прогресс по персонажам:</b>"
    ]

    for i, lvl in enumerate(levels):
        lines.append(f"  {i+1}. {CHARACTERS[i]} — уровень {lvl}/{MAX_LEVEL_PER_CHAR}")

    return "\n".join(lines)


def main_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Кликнуть 💰", "Улучшения ⚙")
    kb.row("Уровень ⬆", "Латяо 🔥")
    kb.row("Ежедневный бонус 🎁", "Достижения 🏅")
    kb.row("Статистика 📊", "Лидерборд 🏆")
    kb.row("Выбор персонажа 👤")
    return kb


# ================== ДОСТИЖЕНИЯ ==================

def try_unlock_achievement(user, chat_id, key):
    """Проверяет условие достижения и, если выполнено, выдаёт награду."""
    if key not in ACHIEVEMENTS_DEFS:
        return

    if key in user.get("achievements", []):
        return  # уже получено

    coins = user.get("coins", 0)
    levels = user.get("levels", [0] * len(CHARACTERS))

    # Условия
    if key == "coins_1000" and coins < 1000:
        return
    if key == "coins_10000" and coins < 10000:
        return
    if key == "first_latyao":
        # само условие проверяется вызовом из do_latyao — тут просто даём ачивку
        pass
    if key == "first_max_char":
        # условие проверяется при докачке персонажа до 10 уровня
        pass

    # Если дошли сюда — достижение выполнено
    user["achievements"].append(key)
    reward = ACHIEVEMENTS_DEFS[key]["reward"]
    user["coins"] = user.get("coins", 0) + reward
    save_data()

    title = ACHIEVEMENTS_DEFS[key]["title"]
    desc = ACHIEVEMENTS_DEFS[key]["desc"]
    msg = (
        f"🏅 <b>Новое достижение!</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc}\n\n"
        f"Награда: <b>{reward}</b> жиркоинов.\n"
        f"Текущий баланс: <b>{user['coins']}</b>."
    )
    bot.send_message(chat_id, msg)


def format_achievements(user):
    lines = ["<b>🏅 Достижения:</b>\n"]
    unlocked = set(user.get("achievements", []))

    for key, data in ACHIEVEMENTS_DEFS.items():
        mark = "✅" if key in unlocked else "❌"
        lines.append(
            f"{mark} <b>{data['title']}</b>\n"
            f"   {data['desc']}\n"
            f"   Награда: {data['reward']} жиркоинов\n"
        )

    lines.append(f"\nВсего открыто: <b>{len(unlocked)}</b> из {len(ACHIEVEMENTS_DEFS)}.")
    return "\n".join(lines)


# ================== ЕЖЕДНЕВНЫЙ БОНУС ==================

def get_daily_reward_and_update(user):
    """Считает награду за ежедневный бонус и обновляет стрик."""
    now = time.time()
    last = user.get("last_daily", 0)
    streak = user.get("daily_streak", 0)

    if last == 0:
        # первый раз
        streak = 1
    else:
        diff = now - last
        if diff < DAILY_COOLDOWN:
            return None, None  # ещё рано, пусть проверка будет выше
        # если зашёл не позже чем через 48 часов — продолжаем стрик, иначе сбрасываем
        if diff <= DAILY_COOLDOWN * 2:
            streak += 1
        else:
            streak = 1

    user["daily_streak"] = streak
    user["last_daily"] = now

    effective_streak = min(streak, DAILY_MAX_STREAK_FOR_BONUS)
    reward = DAILY_BASE_REWARD + (effective_streak - 1) * DAILY_STREAK_BONUS
    return reward, streak


# ================== ИНИЦИАЛИЗАЦИЯ БОТА ==================

load_data()
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")  # HTML для нормального интерфейса


# ================== ОБРАБОТЧИКИ КОМАНД ==================

@bot.message_handler(commands=["start"])
def cmd_start(message):
    user = ensure_user(message)
    text = (
        "<b>👋 Добро пожаловать в игру с абу-бандитами!</b>\n\n"
        "Ты начинаешь с самого слабого — <b>Гитина</b>.\n"
        "Зарабатывай жиркоины кликами, прокачивай заработок, "
        "проходи уровни персонажей и продвигайся к самым мощным абу-бандитам.\n\n"
        "<b>Что есть в игре сейчас:</b>\n"
        "• Кликер с улучшениями заработка\n"
        "• 7 абу-бандитов по 10 уровней каждый\n"
        "• Латяо, удваивающий доход на 5 минут\n"
        "• Ежедневный бонус с серией\n"
        "• Достижения с наградами\n"
        "• Лидерборд сильнейших игроков\n\n"
        "<b>Команды:</b>\n"
        "/click, /upgrade, /levelup, /latyao, /daily, /achievements,\n"
        "/stats, /leaderboard, /choose\n"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu_keyboard())


@bot.message_handler(commands=["help"])
def cmd_help(message):
    text = (
        "<b>ℹ️ Справка по игре</b>\n\n"
        "<b>💰 Заработок:</b>\n"
        "• Базовый заработок: 1 жиркоин за клик.\n"
        "• 25 уровней улучшений: 1-й даёт 25/клик, каждый следующий +1.\n"
        f"• Стоимость улучшений снижена: {EARN_UPGRADE_BASE_COST} × номер уровня.\n\n"
        "<b>🧨 Персонажи:</b>\n"
        "• 7 абу-бандитов, у каждого по 10 уровней.\n"
        "• Цена уровней растёт, а у следующих персонажей +20% к ценам.\n"
        "• Новый абу-бандит открывается после 10 уровня предыдущего.\n\n"
        "<b>🔥 Латяо:</b>\n"
        "• Удваивает заработок на 5 минут.\n"
        f"• Стоит {LATYAO_COST} жиркоинов.\n\n"
        "<b>🎁 Ежедневный бонус:</b>\n"
        "• Можно получать раз в 24 часа.\n"
        "• За серию дней подряд награда растёт.\n\n"
        "<b>🏅 Достижения:</b>\n"
        "• За прогресс и особые действия можно получать ачивки и бонусные монеты.\n"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu_keyboard())


# ----- КЛИК (с критом) -----

@bot.message_handler(commands=["click"])
def cmd_click(message):
    do_click(message)


@bot.message_handler(func=lambda m: m.text == "Кликнуть 💰")
def btn_click(message):
    do_click(message)


def do_click(message):
    user = ensure_user(message)
    base_earn = get_effective_earn_per_click(user)

    crit = random.random() < CRIT_CHANCE
    if crit:
        earn = base_earn * CRIT_MULTIPLIER
    else:
        earn = base_earn

    user["coins"] += earn
    save_data()

    extra = ""
    if is_latyao_active(user):
        extra += " (с учётом Латяо 🔥)"
    if crit:
        extra += " <b>КРИТ!</b> 💥"

    bot.send_message(
        message.chat.id,
        f"Ты кликнул и заработал <b>{earn}</b> жиркоинов{extra}!\n"
        f"Текущий баланс: <b>{user['coins']}</b> жиркоинов."
    )

    # Проверяем достижения по монетам
    try_unlock_achievement(user, message.chat.id, "coins_1000")
    try_unlock_achievement(user, message.chat.id, "coins_10000")


# ----- МЕНЮ УЛУЧШЕНИЙ -----

@bot.message_handler(commands=["upgrade"])
def cmd_upgrade(message):
    show_upgrade_menu(message.chat.id, ensure_user(message))


@bot.message_handler(func=lambda m: m.text == "Улучшения ⚙")
def btn_upgrade(message):
    show_upgrade_menu(message.chat.id, ensure_user(message))


def show_upgrade_menu(chat_id, user, call_message_id=None, edit=False):
    cost = get_next_upgrade_cost(user)
    if cost is None:
        text = (
            "<b>⚙ Улучшения заработка</b>\n\n"
            "У тебя уже <b>максимальный</b> уровень улучшения заработка! 🔝\n\n"
            f"Текущий доход: <b>{get_base_earn_per_click(user)}</b> жиркоинов за клик "
            "(без учёта Латяо)."
        )
        if edit and call_message_id is not None:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call_message_id,
                text=text,
                parse_mode="HTML"
            )
        else:
            bot.send_message(chat_id, text)
        return

    text = (
        "<b>⚙ Улучшения заработка</b>\n\n"
        f"Текущий уровень улучшения: <b>{user['earn_upgrade']}</b> / {MAX_EARN_UPGRADE}\n"
        f"Базовый доход: <b>{get_base_earn_per_click(user)}</b> жиркоинов/клик\n\n"
        f"Следующий уровень будет стоить: <b>{cost}</b> жиркоинов.\n"
        f"После улучшения доход станет: <b>{get_base_earn_per_click(user) + 1}</b> жиркоинов/клик.\n\n"
        f"Текущий баланс: <b>{user['coins']}</b> жиркоинов."
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить улучшение ✅", callback_data="upgrade_buy"))
    kb.add(types.InlineKeyboardButton("Закрыть меню ❌", callback_data="upgrade_close"))

    if edit and call_message_id is not None:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call_message_id,
            text=text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data in ["upgrade_buy", "upgrade_close"])
def callback_upgrade(call):
    uid = get_user_id(call)
    if uid not in user_data:
        bot.answer_callback_query(call.id, "Игрок не найден. Напиши /start.")
        return

    user = user_data[uid]

    if call.data == "upgrade_close":
        bot.answer_callback_query(call.id, "Меню закрыто.")
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None
            )
        except Exception:
            pass
        return

    cost = get_next_upgrade_cost(user)
    if cost is None:
        bot.answer_callback_query(call.id, "У тебя уже максимальный уровень!")
        show_upgrade_menu(call.message.chat.id, user, call.message.message_id, edit=True)
        return

    if user["coins"] < cost:
        bot.answer_callback_query(
            call.id,
            f"Недостаточно жиркоинов: нужно {cost}, у тебя {user['coins']}."
        )
        show_upgrade_menu(call.message.chat.id, user, call.message.message_id, edit=True)
        return

    user["coins"] -= cost
    user["earn_upgrade"] += 1
    save_data()

    bot.answer_callback_query(call.id, "Улучшение куплено! ✅")
    show_upgrade_menu(call.message.chat.id, user, call.message.message_id, edit=True)


# ----- УРОВНИ ПЕРСОНАЖЕЙ -----

@bot.message_handler(commands=["levelup"])
def cmd_levelup(message):
    do_levelup(message)


@bot.message_handler(func=lambda m: m.text == "Уровень ⬆")
def btn_levelup(message):
    do_levelup(message)


def do_levelup(message):
    user = ensure_user(message)
    cur_idx = user["current_char"]
    cur_name = CHARACTERS[cur_idx]
    levels = user["levels"]
    cur_lvl = levels[cur_idx]

    if cur_lvl >= MAX_LEVEL_PER_CHAR:
        bot.send_message(
            message.chat.id,
            f"🔝 <b>{cur_name}</b> уже имеет максимальный уровень {MAX_LEVEL_PER_CHAR}.\n"
            "Попробуй открыть следующего абу-бандита через /choose."
        )
        return

    next_level = cur_lvl + 1
    cost = get_level_cost(cur_idx, next_level)

    if user["coins"] < cost:
        bot.send_message(
            message.chat.id,
            "Недостаточно жиркоинов для повышения уровня.\n"
            f"Нужно: <b>{cost}</b>, у тебя: <b>{user['coins']}</b>."
        )
        return

    user["coins"] -= cost
    levels[cur_idx] = next_level
    save_data()

    msg = (
        f"✅ <b>{cur_name}</b> повышен до уровня <b>{next_level}/{MAX_LEVEL_PER_CHAR}</b>!\n"
        f"Списано <b>{cost}</b> жиркоинов.\n"
        f"Текущий баланс: <b>{user['coins']}</b>."
    )

    if next_level == MAX_LEVEL_PER_CHAR:
        max_available = get_max_available_character_index(user)
        if max_available > cur_idx:
            next_name = CHARACTERS[cur_idx + 1]
            msg += (
                f"\n\n🎉 Ты полностью прокачал <b>{cur_name}</b>!\n"
                f"Теперь тебе доступен следующий абу-бандит: <b>{next_name}</b>.\n"
                "Используй /choose или кнопку «Выбор персонажа 👤»."
            )
        # достижение за первый максимальный персонаж
        try_unlock_achievement(user, message.chat.id, "first_max_char")

    bot.send_message(message.chat.id, msg)


# ----- ЛАТЯО -----

@bot.message_handler(commands=["latyao"])
def cmd_latyao(message):
    do_latyao(message)


@bot.message_handler(func=lambda m: m.text == "Латяо 🔥")
def btn_latyao(message):
    do_latyao(message)


def do_latyao(message):
    user = ensure_user(message)

    if user["coins"] < LATYAO_COST:
        bot.send_message(
            message.chat.id,
            "Недостаточно жиркоинов для покупки Латяо.\n"
            f"Нужно: <b>{LATYAO_COST}</b>, у тебя: <b>{user['coins']}</b>."
        )
        return

    user["coins"] -= LATYAO_COST
    now = time.time()
    current_until = user.get("latyao_until", 0)
    if current_until > now:
        user["latyao_until"] = current_until + LATYAO_DURATION
    else:
        user["latyao_until"] = now + LATYAO_DURATION

    save_data()

    left = int(user["latyao_until"] - time.time())
    minutes = left // 60
    seconds = left % 60

    bot.send_message(
        message.chat.id,
        "🔥 <b>Латяо активировано!</b>\n"
        f"Заработок удвоен на <b>{minutes} мин {seconds} сек</b>.\n"
        f"Текущий баланс: <b>{user['coins']}</b>."
    )

    # достижение за первый Латяо
    try_unlock_achievement(user, message.chat.id, "first_latyao")


# ----- ЕЖЕДНЕВНЫЙ БОНУС -----

@bot.message_handler(commands=["daily"])
def cmd_daily(message):
    do_daily(message)


@bot.message_handler(func=lambda m: m.text == "Ежедневный бонус 🎁")
def btn_daily(message):
    do_daily(message)


def do_daily(message):
    user = ensure_user(message)
    now = time.time()
    last = user.get("last_daily", 0)

    if last != 0 and now - last < DAILY_COOLDOWN:
        # ещё рано
        remaining = int(DAILY_COOLDOWN - (now - last))
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        seconds = remaining % 60
        bot.send_message(
            message.chat.id,
            "🎁 Ты уже получал ежедневный бонус сегодня.\n"
            f"Следующий будет доступен через: <b>{hours:02d}:{minutes:02d}:{seconds:02d}</b>."
        )
        return

    reward, streak = get_daily_reward_and_update(user)
    if reward is None:
        # теоретически не дойдём сюда, но на всякий случай
        bot.send_message(message.chat.id, "Что-то пошло не так с ежедневным бонусом.")
        return

    user["coins"] += reward
    save_data()

    bot.send_message(
        message.chat.id,
        f"🎁 <b>Ежедневный бонус!</b>\n\n"
        f"Твой стрик: <b>{streak}</b> дней подряд.\n"
        f"Ты получил: <b>{reward}</b> жиркоинов.\n"
        f"Текущий баланс: <b>{user['coins']}</b>."
    )

    # достижения по монетам тоже могут сработать
    try_unlock_achievement(user, message.chat.id, "coins_1000")
    try_unlock_achievement(user, message.chat.id, "coins_10000")


# ----- СТАТИСТИКА -----

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    do_stats(message)


@bot.message_handler(func=lambda m: m.text == "Статистика 📊")
def btn_stats(message):
    do_stats(message)


def do_stats(message):
    user = ensure_user(message)
    bot.send_message(message.chat.id, format_stats(user))


# ----- ДОСТИЖЕНИЯ (команда и кнопка) -----

@bot.message_handler(commands=["achievements"])
def cmd_achievements(message):
    do_achievements(message)


@bot.message_handler(func=lambda m: m.text == "Достижения 🏅")
def btn_achievements(message):
    do_achievements(message)


def do_achievements(message):
    user = ensure_user(message)
    bot.send_message(message.chat.id, format_achievements(user))


# ----- ЛИДЕРБОРД -----

@bot.message_handler(commands=["leaderboard"])
def cmd_leaderboard(message):
    do_leaderboard(message)


@bot.message_handler(func=lambda m: m.text == "Лидерборд 🏆")
def btn_leaderboard(message):
    do_leaderboard(message)


def do_leaderboard(message):
    if not user_data:
        bot.send_message(message.chat.id, "Пока нет ни одного игрока.")
        return

    sorted_players = sorted(
        user_data.items(),
        key=lambda item: calculate_power(item[1]),
        reverse=True
    )

    lines = ["<b>🏆 Лидерборд:</b>"]
    max_show = min(10, len(sorted_players))

    for idx in range(max_show):
        uid, u = sorted_players[idx]
        levels = u.get("levels", [0] * len(CHARACTERS))
        best_char = 0
        for i, lvl in enumerate(levels):
            if lvl > 0:
                best_char = i
        best_lvl = levels[best_char]
        name = u.get("name", f"Игрок_{uid}")
        lines.append(
            f"{idx+1}. <b>{name}</b> — {CHARACTERS[best_char]} "
            f"(уровень {best_lvl}), всего уровней: {sum(levels)}, монет: {u.get('coins', 0)}"
        )

    uid_me = get_user_id(message)
    my_pos = None
    for idx, (uid, _) in enumerate(sorted_players):
        if uid == uid_me:
            my_pos = idx + 1
            break

    if my_pos is not None:
        lines.append(f"\nТвоя позиция: <b>{my_pos}</b> из {len(sorted_players)}.")
    else:
        lines.append("\nТы ещё не в лидерборде. Нажми «Кликнуть 💰» и начинай путь!")

    bot.send_message(message.chat.id, "\n".join(lines))


# ----- ВЫБОР ПЕРСОНАЖА -----

@bot.message_handler(commands=["choose"])
def cmd_choose(message):
    do_choose(message)


@bot.message_handler(func=lambda m: m.text == "Выбор персонажа 👤")
def btn_choose(message):
    do_choose(message)


def do_choose(message):
    user = ensure_user(message)
    levels = user["levels"]
    max_available = get_max_available_character_index(user)

    kb = types.InlineKeyboardMarkup()
    for i, name in enumerate(CHARACTERS):
        if i > max_available:
            break
        lvl = levels[i]
        text = f"{i+1}. {name} (уровень {lvl}/{MAX_LEVEL_PER_CHAR})"
        if i == user["current_char"]:
            text = "✅ " + text
        kb.add(types.InlineKeyboardButton(text=text, callback_data=f"choose_char_{i}"))

    bot.send_message(
        message.chat.id,
        "Выбери абу-бандита, с которым хочешь играть:",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("choose_char_"))
def callback_choose_char(call):
    uid = get_user_id(call)
    if uid not in user_data:
        bot.answer_callback_query(call.id, "Игрок не найден. Напиши /start.")
        return

    user = user_data[uid]
    max_available = get_max_available_character_index(user)
    levels = user["levels"]

    try:
        idx = int(call.data.split("_")[-1])
    except Exception:
        bot.answer_callback_query(call.id, "Ошибка выбора персонажа.")
        return

    if idx > max_available:
        bot.answer_callback_query(call.id, "Этот абу-бандит ещё не доступен!")
        return

    user["current_char"] = idx
    save_data()
    bot.answer_callback_query(call.id, f"Теперь ты играешь за {CHARACTERS[idx]}!")
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )
    except Exception:
        pass

    bot.send_message(
        call.message.chat.id,
        f"Ты выбрал абу-бандита: <b>{CHARACTERS[idx]}</b> "
        f"(уровень {levels[idx]}/{MAX_LEVEL_PER_CHAR}).",
        reply_markup=main_menu_keyboard()
    )


# ----- ОБРАБОТКА ПРОЧЕГО ТЕКСТА -----

@bot.message_handler(content_types=["text"])
def fallback(message):
    ensure_user(message)
    bot.send_message(
        message.chat.id,
        "Не понял сообщение 🤔\n"
        "Используй кнопки снизу или команду /help.",
        reply_markup=main_menu_keyboard()
    )


# ================== ЗАПУСК ==================

if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling()



