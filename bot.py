import json
import time
import threading
import random
import os
from typing import Dict, Any
import telebot
from telebot import types
import html

import os
TOKEN = os.environ.get("BOT_TOKEN", "8141899217:AAE5MLvsdRG5-mLcw9P-652paocFNvZ9lEg")
QUESTIONS_FILE = "questions.json"
USERS_FILE = "users.json"
ADMIN_FILE = "admin.json"
QUESTION_TIMEOUT = 30.0  # секунд на вопрос
ADMIN_PASSWORD = "1234567887654321"
# --------------------------------------------

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

timers: Dict[str, threading.Timer] = {}
answer_locks: Dict[str, threading.Lock] = {}
message_map: Dict[str, Dict[str, Any]] = {}

def load_json(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

questions_data = load_json(QUESTIONS_FILE, {"channel": "", "questions": []})
questions_list = questions_data.get("questions", [])

users_data = load_json(USERS_FILE, {"participants": {}, "completed": []})
admin_data = load_json(ADMIN_FILE, {"admin_id": None})

def ensure_user_struct(user_id: str, username: str):
    if user_id not in users_data["participants"]:
        q_ids = [q["id"] for q in questions_list]
        random.shuffle(q_ids)
        users_data["participants"][user_id] = {
            "username": username or "",
            "question_ids": q_ids,
            "answers": [-2] * len(q_ids),
            "start_time": None,
            "current_question": 0,
            "end_time": None
        }
        save_json(USERS_FILE, users_data)

def get_question_by_id(qid: int):
    for q in questions_list:
        if q["id"] == qid:
            return q
    return None

def user_completed(user_id: str) -> bool:
    return user_id in users_data.get("completed", [])

def start_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(types.KeyboardButton("Начать викторину"))
    kb.row(types.KeyboardButton("Правила"), types.KeyboardButton("Моя статистика"))
    return kb

def rules_text():
    return (
        "📜 <b>Правила викторины</b>\n\n"
        "• 1 попытка на пользователя 👤\n"
        "• 30 секунд на вопрос ⏱️\n"
        "• Уникальный порядок вопросов 🔀\n\n"
        "В конце вы увидите свой счёт и время 📊\n\n"
        "▶️ Нажмите <b>Начать викторину</b>"
    )

def welcome_text():
    return (
        "<b>Добро пожаловать!</b>\n\n"
        "Это бот‑викторина. Нажмите «Начать викторину», чтобы перейти к подтверждению старта.\n\n"
        "Если хотите — сначала прочитайте правила."
    )

def send_question(user_id: int):
    uid = str(user_id)
    part = users_data["participants"].get(uid)
    if not part:
        return
    cur = part["current_question"]
    if cur >= len(part["question_ids"]):
        finish_quiz(user_id)
        return

    qid = part["question_ids"][cur]
    q = get_question_by_id(qid)
    if not q:
        part["current_question"] += 1
        save_json(USERS_FILE, users_data)
        send_question(user_id)
        return

    markup = types.InlineKeyboardMarkup()
    for idx, opt in enumerate(q["options"]):
        cb = f"ans|{uid}|{cur}|{idx}"
        btn = types.InlineKeyboardButton(opt, callback_data=cb)
        markup.add(btn)

    text = f"<b>Вопрос {cur+1}/{len(part['question_ids'])}</b>\n\n{q['question']}\n\nОсталось 30 секунд"
    sent = bot.send_message(user_id, text, reply_markup=markup)

    message_map[uid] = {"msg_id": sent.message_id, "q_index": cur}

    def on_timeout():
        with answer_locks.setdefault(uid, threading.Lock()):
            if users_data["participants"][uid]["answers"][cur] != -2:
                return
            users_data["participants"][uid]["answers"][cur] = -1
            users_data["participants"][uid]["current_question"] += 1
            save_json(USERS_FILE, users_data)
            try:
                bot.delete_message(user_id, sent.message_id)
            except:
                pass
            send_question(user_id)

    t = threading.Timer(QUESTION_TIMEOUT, on_timeout)
    timers[uid] = t
    t.start()

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("ans|"))
def handle_answer(call: types.CallbackQuery):
    data = call.data.split("|")
    if len(data) != 4:
        return
    _, uid, q_index_str, opt_str = data
    user_id = call.from_user.id
    if str(user_id) != uid:
        try:
            bot.answer_callback_query(call.id, "Это не ваш вопрос", show_alert=False)
        except:
            pass
        return

    q_index = int(q_index_str)
    opt_index = int(opt_str)

    lock = answer_locks.setdefault(uid, threading.Lock())
    with lock:
        part = users_data["participants"].get(uid)
        if not part:
            bot.answer_callback_query(call.id, "Ошибка: пользователь не найден", show_alert=False)
            return
        if part["answers"][q_index] != -2:
            bot.answer_callback_query(call.id, "Ответ уже записан", show_alert=False)
            return

        part["answers"][q_index] = opt_index
        if part["start_time"] is None:
            part["start_time"] = time.time()
        part["current_question"] += 1
        save_json(USERS_FILE, users_data)

        try:
            bot.answer_callback_query(call.id, "Ответ записан", show_alert=False)
        except:
            pass

        t = timers.pop(uid, None)
        if t:
            t.cancel()
        msg_info = message_map.pop(uid, None)
        if msg_info:
            try:
                bot.delete_message(user_id, msg_info["msg_id"])
            except:
                pass

        if part["current_question"] >= len(part["question_ids"]):
            finish_quiz(user_id)
        else:
            send_question(user_id)

def finish_quiz(user_id: int):
    uid = str(user_id)
    part = users_data["participants"].get(uid)
    if not part:
        return
    if part.get("end_time") is None:
        part["end_time"] = time.time()
    correct = 0
    for idx, qid in enumerate(part["question_ids"]):
        q = get_question_by_id(qid)
        if not q:
            continue
        ans = part["answers"][idx]
        if ans >= 0 and ans == q["correct"]:
            correct += 1
    total = len(part["question_ids"])
    duration = part["end_time"] - (part["start_time"] or part["end_time"])
    users_data.setdefault("completed", [])
    if uid not in users_data["completed"]:
        users_data["completed"].append(uid)
    save_json(USERS_FILE, users_data)

    text = (
        f"<b>Тест завершён</b>\n\n"
        f"Правильных ответов: <b>{correct}/{total}</b>\n"
        f"Время прохождения: <b>{duration:.1f} секунд</b>\n\n"
        "Спасибо за участие!"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Посмотреть мою статистику", callback_data=f"mystat|{uid}"))
    bot.send_message(user_id, text, reply_markup=kb)

    admin_id = admin_data.get("admin_id")
    if admin_id:
        try:
            bot.send_message(admin_id, f"Новый участник прошёл тест: @{part.get('username','-')} — {correct}/{total}, {duration:.1f}s")
        except:
            pass

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("mystat|"))
def handle_mystat(call: types.CallbackQuery):
    _, uid = call.data.split("|")
    if str(call.from_user.id) != uid:
        bot.answer_callback_query(call.id, "Это не ваша статистика", show_alert=False)
        return
    part = users_data["participants"].get(uid)
    if not part:
        bot.answer_callback_query(call.id, "Статистика не найдена", show_alert=False)
        return
    if part.get("end_time") is None:
        bot.answer_callback_query(call.id, "Вы ещё не завершили тест", show_alert=False)
        return
    correct = 0
    for idx, qid in enumerate(part["question_ids"]):
        q = get_question_by_id(qid)
        if not q:
            continue
        ans = part["answers"][idx]
        if ans >= 0 and ans == q["correct"]:
            correct += 1
    duration = part["end_time"] - (part["start_time"] or part["end_time"])
    # plain text for alert (no HTML tags)
    plain = f"Ваша статистика:\nПравильных: {correct}/{len(part['question_ids'])}\nВремя: {duration:.1f}s"
    bot.answer_callback_query(call.id, plain, show_alert=True)

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    uid = str(message.from_user.id)
    username = message.from_user.username or message.from_user.first_name or ""
    ensure_user_struct(uid, username)
    kb = start_keyboard()
    # Отправляем приветственное сообщение, отличное от правил
    bot.send_message(message.chat.id, welcome_text(), reply_markup=kb)

@bot.message_handler(commands=["admin"])
def cmd_admin(message: types.Message):
    parts = message.text.strip().split()
    if len(parts) != 2 or parts[1] != ADMIN_PASSWORD:
        bot.reply_to(message, "Неверный формат или пароль. Используйте: /admin <пароль>")
        return
    admin_data["admin_id"] = message.from_user.id
    save_json(ADMIN_FILE, admin_data)
    bot.reply_to(message, "Вы назначены администратором. Используйте /stats для просмотра статистики.")

@bot.message_handler(commands=["stats"])
def cmd_stats(message: types.Message):
    admin_id = admin_data.get("admin_id")
    if admin_id is None or message.from_user.id != admin_id:
        bot.reply_to(message, "Команда доступна только администратору.")
        return
    send_admin_stats(message.chat.id, edit_message_id=None)

def build_admin_stats_text():
    completed = users_data.get("completed", [])
    rows = []
    for uid in completed:
        part = users_data["participants"].get(uid)
        if not part:
            continue
        correct = 0
        for idx, qid in enumerate(part["question_ids"]):
            q = get_question_by_id(qid)
            if not q:
                continue
            ans = part["answers"][idx]
            if ans >= 0 and ans == q["correct"]:
                correct += 1
        duration = (part.get("end_time") or time.time()) - (part.get("start_time") or part.get("end_time") or time.time())
        
        # Получаем username или альтернативную идентификацию
        username = part.get("username", "").strip()
        # Если username пустой, начинается с @, или слишком короткий - используем альтернативу
        if not username or username.startswith('@'):
            # Пытаемся получить номер телефона из данных пользователя (если он у вас есть)
            # В текущей структуре нет телефона, так что используем user_id или "Пользователь ID"
            user_id_display = f"id{uid}" if uid else "id???"
            display_name = user_id_display
        else:
            display_name = f"@{username}"
            
        rows.append({
            "uid": uid,
            "display_name": display_name,
            "username": part.get("username", ""),
            "correct": correct,
            "time": duration
        })
    
    rows.sort(key=lambda r: (-r["correct"], r["time"]))
    lines = ["<b>Статистика прошедших</b>\n"]
    for r in rows:
        lines.append(f"{r['display_name']} — {r['correct']} правильных, {r['time']:.1f}s")
    if not rows:
        lines.append("Пока никто не прошёл тест.")
    return "\n".join(lines)

def send_admin_stats(chat_id: int, edit_message_id: int = None):
    text = build_admin_stats_text()
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Обновить статистику", callback_data="admin_refresh"))
    if edit_message_id:
        try:
            bot.edit_message_text(text, chat_id, edit_message_id, reply_markup=kb)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "admin_refresh")
def handle_admin_refresh(call: types.CallbackQuery):
    admin_id = admin_data.get("admin_id")
    if admin_id is None or call.from_user.id != admin_id:
        bot.answer_callback_query(call.id, "Только админ может обновлять статистику", show_alert=False)
        return
    try:
        send_admin_stats(call.message.chat.id, edit_message_id=call.message.message_id)
        bot.answer_callback_query(call.id, "Статистика обновлена", show_alert=False)
    except Exception:
        bot.answer_callback_query(call.id, "Ошибка при обновлении", show_alert=False)

@bot.message_handler(func=lambda m: m.text is not None)
def handle_text(m: types.Message):
    text = m.text.strip().lower()
    uid = str(m.from_user.id)
    username = m.from_user.username or m.from_user.first_name or ""
    ensure_user_struct(uid, username)
    if text == "правила":
        # Отдельное сообщение с правилами (отличное от /start)
        bot.send_message(m.chat.id, rules_text())
    elif text == "начать викторину" or text == "start":
        if user_completed(uid):
            bot.send_message(m.chat.id, "Вы уже проходили тест. Повторно пройти нельзя.")
            return
        part = users_data["participants"].get(uid)
        if part is None:
            ensure_user_struct(uid, username)
            part = users_data["participants"][uid]
        if part.get("current_question", 0) < len(part.get("question_ids", [])) and part.get("start_time") is not None and uid not in users_data.get("completed", []):
            bot.send_message(m.chat.id, "Вы уже начали тест. Пожалуйста, отвечайте на текущие вопросы.")
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Старт теста", callback_data=f"start_quiz|{uid}"))
        bot.send_message(m.chat.id, "Готовы начать викторину? Нажмите кнопку ниже, чтобы стартовать.", reply_markup=kb)
    elif text == "моя статистика":
        if user_completed(uid):
            part = users_data["participants"].get(uid)
            correct = 0
            for idx, qid in enumerate(part["question_ids"]):
                q = get_question_by_id(qid)
                if not q:
                    continue
                ans = part["answers"][idx]
                if ans >= 0 and ans == q["correct"]:
                    correct += 1
            duration = (part.get("end_time") or time.time()) - (part.get("start_time") or time.time())
            bot.send_message(m.chat.id, f"Ваша статистика:\nПравильных: <b>{correct}/{len(part['question_ids'])}</b>\nВремя: <b>{duration:.1f}s</b>")
        else:
            bot.send_message(m.chat.id, "Вы ещё не завершили тест.")
    else:
        bot.send_message(m.chat.id, "Неизвестная команда. Используйте кнопки на клавиатуре.")

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("start_quiz|"))
def handle_start_quiz(call: types.CallbackQuery):
    _, uid = call.data.split("|")
    if str(call.from_user.id) != uid:
        bot.answer_callback_query(call.id, "Это не ваша кнопка старта", show_alert=False)
        return
    part = users_data["participants"].get(uid)
    if not part:
        bot.answer_callback_query(call.id, "Ошибка: пользователь не найден", show_alert=False)
        return
    if user_completed(uid):
        bot.answer_callback_query(call.id, "Вы уже завершили тест ранее", show_alert=False)
        return
    if part.get("start_time") is not None and part.get("current_question", 0) < len(part.get("question_ids", [])):
        bot.answer_callback_query(call.id, "Вы уже начали тест", show_alert=False)
        return
    part["start_time"] = time.time()
    part["current_question"] = 0
    part["answers"] = [-2] * len(part["question_ids"])
    part["end_time"] = None
    save_json(USERS_FILE, users_data)
    try:
        bot.answer_callback_query(call.id, "Тест стартует", show_alert=False)
    except:
        pass
    bot.send_message(call.message.chat.id, "Тест начат! Удачи.")
    send_question(int(uid))

def shutdown():
    for t in list(timers.values()):
        try:
            t.cancel()
        except:
            pass

if __name__ == "__main__":
    print("Bot started...")
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except KeyboardInterrupt:
        shutdown()
    except Exception as e:
        print("Error:", e)
        shutdown()
