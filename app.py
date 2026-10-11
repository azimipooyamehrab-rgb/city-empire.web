import base64
import hashlib
import hmac
import json
import os
import random
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
PLAYERS_FILE = DATA_DIR / "players.json"

BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "1999799698:YLw8Ew52muDtKfbDRbmEeMag3UnIbkG63I4").strip()
GAME_URL = os.getenv("GAME_URL", "").strip().rstrip("/")
APP_SECRET = os.getenv("APP_SECRET", "").strip()
if not APP_SECRET:
    # Suitable for local development only. Set a persistent secret in production.
    APP_SECRET = "local-dev-change-this-secret"

BALE_API = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}" if BALE_BOT_TOKEN else ""
app = Flask(__name__, static_folder=None)
DATA_LOCK = threading.RLock()
BOT_LOCK = threading.Lock()
BOT_THREAD_STARTED = False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_token(claims: dict) -> str:
    raw = json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload = _b64(raw)
    signature = _b64(hmac.new(APP_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return payload + "." + signature


def read_token(token: str, expected_kind: str):
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(hmac.new(APP_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        claims = json.loads(_unb64(payload).decode("utf-8"))
        if claims.get("kind") != expected_kind or int(claims.get("exp", 0)) < int(time.time()):
            return None
        return claims
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def fresh_player(player_id: str, name: str = "فرمانده") -> dict:
    return {
        "player_id": str(player_id),
        "name": (name or "فرمانده")[:28],
        "gems": 500,
        "wood": 250,
        "iron": 200,
        "soldiers": 80,
        "houses": 5,
        "citizens": 55,
        "level": 1,
        "xp": 0,
        "reputation": 100,
        "dragons": 0,
        "wins": 0,
        "shield_until": 0,
        "last_event": time.time(),
        "log": ["به امپراتوری تو خوش آمدی، فرمانده!"]
    }


def load_players() -> dict:
    try:
        with PLAYERS_FILE.open("r", encoding="utf-8") as file:
            value = json.load(file)
            return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_players(players: dict) -> None:
    temp = PLAYERS_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(players, file, ensure_ascii=False, indent=2)
    temp.replace(PLAYERS_FILE)


PLAYERS = load_players()


def add_log(player: dict, message: str) -> None:
    player.setdefault("log", []).insert(0, message)
    player["log"] = player["log"][:8]


def add_xp(player: dict, amount: int) -> str:
    player["xp"] += amount
    messages = [f"⭐ {amount} امتیاز تجربه گرفتی!"]
    while player["xp"] >= player["level"] * 100:
        player["xp"] -= player["level"] * 100
        player["level"] += 1
        player["gems"] += 100
        player["reputation"] = min(100, player["reputation"] + 5)
        messages.append(f"🎉 سطح جدید! اکنون سطح {player['level']} هستی و ۱۰۰ الماس پاداش گرفتی.")
    return " ".join(messages)


def maybe_random_event(player: dict) -> str | None:
    now = time.time()
    if now - float(player.get("last_event", now)) < 100:
        return None
    player["last_event"] = now
    event = random.choice(("trader", "people", "bandits", "materials", "festival"))
    if event == "trader":
        amount = random.randint(80, 220)
        player["gems"] += amount
        return f"🧳 رویداد: کاروان بازرگانان رسید؛ {amount} الماس دریافت کردی."
    if event == "people":
        amount = random.randint(3, 10)
        player["citizens"] += amount
        return f"🏘️ رویداد: {amount} شهروند تازه به شهر پیوستند."
    if event == "bandits":
        lost = min(player["soldiers"], random.randint(1, 8))
        player["soldiers"] -= lost
        player["reputation"] = max(0, player["reputation"] - 3)
        return f"🗡️ رویداد: راهزن‌ها حمله کردند؛ {lost} سرباز از دست رفت."
    if event == "materials":
        wood, iron = random.randint(20, 60), random.randint(10, 40)
        player["wood"] += wood
        player["iron"] += iron
        return f"⛏️ رویداد: معدن تازه پیدا شد؛ {wood} چوب و {iron} آهن گرفتی."
    bonus = random.randint(100, 260)
    player["gems"] += bonus
    player["reputation"] = min(100, player["reputation"] + 8)
    return f"🎊 رویداد: جشن شهر! {bonus} الماس گرفتی و اعتبارت بیشتر شد."


def current_game_state(player: dict) -> dict:
    keys = ("name", "gems", "wood", "iron", "soldiers", "houses", "citizens", "level", "xp", "reputation", "dragons", "wins", "shield_until", "log")
    state = {key: player.get(key) for key in keys}
    state["xp_needed"] = int(player.get("level", 1)) * 100
    state["shield_seconds"] = max(0, int(float(player.get("shield_until", 0)) - time.time()))
    state["event_seconds"] = max(0, 100 - int(time.time() - float(player.get("last_event", time.time()))))
    return state


def auth_player():
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    claims = read_token(token, "game")
    if not claims or not claims.get("player_id"):
        return None, (jsonify({"ok": False, "error": "نشست بازی معتبر نیست؛ از ربات دوباره وارد شو."}), 401)
    player_id = str(claims["player_id"])
    with DATA_LOCK:
        player = PLAYERS.get(player_id)
        if player is None:
            player = fresh_player(player_id, str(claims.get("name") or "فرمانده"))
            PLAYERS[player_id] = player
            save_players(PLAYERS)
        return player, None


def bale_send(chat_id, text, reply_markup=None):
    if not BALE_API:
        return False
    body = {"chat_id": chat_id, "text": str(text)[:4000]}
    if reply_markup:
        body["reply_markup"] = reply_markup
    try:
        response = requests.post(f"{BALE_API}/sendMessage", json=body, timeout=20)
        response.raise_for_status()
        data = response.json()
        return bool(data.get("ok"))
    except (requests.RequestException, ValueError) as exc:
        app.logger.warning("Bale sendMessage failed: %s", type(exc).__name__)
        return False


def keyboard():
    return {"keyboard": [[{"text": "🎮 بازی"}, {"text": "🏰 شهر من"}], [{"text": "📊 وضعیت"}, {"text": "❓ راهنما"}]], "resize_keyboard": True}


def make_game_url(chat_id, first_name="فرمانده") -> str | None:
    if not GAME_URL:
        return None
    token = make_token({"kind": "launch", "player_id": str(chat_id), "name": str(first_name)[:28], "exp": int(time.time()) + 60 * 60 * 24})
    return f"{GAME_URL}/?launch={token}"


def handle_bale_message(message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    sender = message.get("from") or {}
    first_name = sender.get("first_name") or "فرمانده"
    if chat_id is None or not text:
        return
    lowered = text.lower()
    if lowered.startswith("/start") or lowered in ("شروع", "شروع بازی"):
        bale_send(chat_id, "🏰 به CITY EMPIRE خوش آمدی!\nامپراتوری‌ات را بساز، منابع جمع کن و با اژدهاها بجنگ.\n\nبرای ورود به نسخهٔ گرافیکی روی «🎮 بازی» بزن.", keyboard())
    elif lowered.startswith("/play") or text in ("🎮 بازی", "بازی", "بازی گرافیکی"):
        url = make_game_url(chat_id, first_name)
        if url:
            markup = {"inline_keyboard": [[{"text": "⚔️ ورود به امپراتوری", "url": url}]]}
            bale_send(chat_id, "نسخهٔ گرافیکی شهر آماده است. برای باز کردن بازی روی دکمه بزن:", markup)
        else:
            bale_send(chat_id, "هنوز GAME_URL تنظیم نشده است. ابتدا سایت را روی یک میزبان HTTPS منتشر و سپس متغیر GAME_URL را تنظیم کن.", keyboard())
    elif lowered.startswith("/help") or text == "❓ راهنما":
        bale_send(chat_id, "راهنما:\n🎮 بازی — باز کردن بازی گرافیکی\n🏰 شهر من / 📊 وضعیت — برای دیدن وضعیت کامل وارد بازی شو.\n\nدکمهٔ بازی یک لینک ورود اختصاصی و زمان‌دار می‌سازد.", keyboard())
    elif text in ("🏰 شهر من", "📊 وضعیت"):
        bale_send(chat_id, "وضعیت و مدیریت کامل شهر در نسخهٔ گرافیکی در دسترس است. برای ورود روی «🎮 بازی» بزن.", keyboard())
    else:
        bale_send(chat_id, "این فرمان را نشناختم. از دکمه‌های منو استفاده کن یا /help را بفرست.", keyboard())


def bot_poll_loop() -> None:
    if not BALE_API:
        app.logger.info("BALE_BOT_TOKEN is not set; Bale polling is disabled.")
        return
    offset = 0
    while True:
        try:
            response = requests.get(f"{BALE_API}/getUpdates", params={"offset": offset, "timeout": 25, "limit": 50}, timeout=35)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                time.sleep(3)
                continue
            for update in data.get("result", []):
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                message = update.get("message")
                if message:
                    handle_bale_message(message)
        except (requests.RequestException, ValueError, TypeError) as exc:
            app.logger.warning("Bale polling failed: %s", type(exc).__name__)
            time.sleep(4)


def start_bot_thread() -> None:
    global BOT_THREAD_STARTED
    if not BALE_BOT_TOKEN:
        return
    with BOT_LOCK:
        if BOT_THREAD_STARTED:
            return
        thread = threading.Thread(target=bot_poll_loop, name="bale-polling", daemon=True)
        thread.start()
        BOT_THREAD_STARTED = True


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/static/<path:filename>")
def static_file(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.get("/health")
def health():
    return jsonify({"ok": True, "bale_configured": bool(BALE_BOT_TOKEN), "game_url_configured": bool(GAME_URL)})


@app.post("/api/session")
def create_session():
    body = request.get_json(silent=True) or {}
    launch = read_token(str(body.get("launch", "")), "launch")
    if not launch or not launch.get("player_id"):
        return jsonify({"ok": False, "error": "لینک ورود نامعتبر یا منقضی شده است. از ربات دوباره /play را بزن."}), 401
    player_id = str(launch["player_id"])
    with DATA_LOCK:
        player = PLAYERS.get(player_id)
        if player is None:
            player = fresh_player(player_id, str(launch.get("name") or "فرمانده"))
            PLAYERS[player_id] = player
        elif player.get("name") == "فرمانده" and launch.get("name"):
            player["name"] = str(launch["name"])[:28]
        save_players(PLAYERS)
        game_token = make_token({"kind": "game", "player_id": player_id, "name": player["name"], "exp": int(time.time()) + 60 * 60 * 24 * 30})
        return jsonify({"ok": True, "token": game_token, "state": current_game_state(player)})


@app.get("/api/state")
def api_state():
    player, error = auth_player()
    if error:
        return error
    with DATA_LOCK:
        event = maybe_random_event(player)
        if event:
            add_log(player, event)
            save_players(PLAYERS)
        return jsonify({"ok": True, "state": current_game_state(player), "event": event})


@app.post("/api/action")
def api_action():
    player, error = auth_player()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    action = str(body.get("action", ""))
    message = ""
    with DATA_LOCK:
        now = time.time()
        if action == "collect":
            amount = random.randint(35, 90)
            player["gems"] += amount
            message = f"💎 {amount} الماس از مالیات و بازرگانی شهر دریافت شد."
            message += " " + add_xp(player, 8)
        elif action == "mine":
            wood, iron = random.randint(20, 55), random.randint(10, 35)
            player["wood"] += wood
            player["iron"] += iron
            message = f"⛏️ معدن موفق بود؛ {wood} چوب و {iron} آهن استخراج کردی. " + add_xp(player, 10)
        elif action == "build":
            if player["wood"] < 100 or player["iron"] < 50:
                message = "منابع کافی نیست! برای ساخت خانه به ۱۰۰ چوب و ۵۰ آهن نیاز داری."
            else:
                player["wood"] -= 100
                player["iron"] -= 50
                player["houses"] += 1
                people = random.randint(4, 9)
                player["citizens"] += people
                message = f"🏡 خانه ساخته شد و {people} شهروند تازه وارد شدند. " + add_xp(player, 20)
        elif action == "train":
            if player["gems"] < 120:
                message = "برای آموزش ۱۰ سرباز به ۱۲۰ الماس نیاز داری."
            else:
                player["gems"] -= 120
                player["soldiers"] += 10
                message = "🛡️ ۱۰ سرباز تازه به ارتش پیوستند."
        elif action == "shield":
            if player["gems"] < 150:
                message = "برای فعال‌کردن سپر به ۱۵۰ الماس نیاز داری."
            else:
                player["gems"] -= 150
                player["shield_until"] = max(float(player.get("shield_until", 0)), now) + 120
                message = "✨ سپر دفاعی برای ۲ دقیقه فعال شد."
        elif action == "war":
            if player["shield_until"] > now:
                message = "برای حمله، ابتدا باید منتظر پایان سپر دفاعی بمانی."
            elif player["soldiers"] <= 0:
                message = "ارتشی نداری! ابتدا سرباز آموزش بده."
            else:
                enemy = random.randint(35, 85 + player["level"] * 12)
                power = player["soldiers"] + random.randint(0, 30)
                if power >= enemy:
                    gems, losses = random.randint(150, 350), min(player["soldiers"], random.randint(2, 9))
                    player["gems"] += gems
                    player["soldiers"] -= losses
                    player["wins"] += 1
                    player["reputation"] = min(100, player["reputation"] + 4)
                    message = f"🏆 پیروز شدی! قدرت دشمن {enemy} بود. {gems} الماس گرفتی و {losses} سرباز از دست دادی. " + add_xp(player, 25)
                else:
                    losses = min(player["soldiers"], random.randint(8, 18))
                    player["soldiers"] -= losses
                    player["reputation"] = max(0, player["reputation"] - 8)
                    message = f"💥 شکست خوردی؛ قدرت دشمن {enemy} بود و {losses} سرباز از دست رفتند."
        elif action == "dragon":
            if player["soldiers"] < 20:
                message = "برای نبرد با اژدها حداقل ۲۰ سرباز لازم است."
            else:
                dragon = random.randint(70, 100 + player["level"] * 20)
                power = player["soldiers"] + random.randint(15, 40)
                if power >= dragon:
                    prize = random.randint(350, 700)
                    losses = min(player["soldiers"], random.randint(5, 14))
                    player["gems"] += prize
                    player["soldiers"] -= losses
                    player["dragons"] += 1
                    message = f"🐉 اژدها شکست خورد! {prize} الماس و یک پیروزی افسانه‌ای! تلفات: {losses} سرباز. " + add_xp(player, 45)
                else:
                    losses = min(player["soldiers"], random.randint(12, 25))
                    player["soldiers"] -= losses
                    player["reputation"] = max(0, player["reputation"] - 10)
                    message = f"🔥 اژدها پیروز شد! {losses} سرباز از دست رفتند. برای نبرد بعدی ارتشت را تقویت کن."
        else:
            return jsonify({"ok": False, "error": "فرمان ناشناخته است."}), 400

        add_log(player, message)
        event = maybe_random_event(player)
        if event:
            add_log(player, event)
        save_players(PLAYERS)
        return jsonify({"ok": True, "message": message, "event": event, "state": current_game_state(player)})


start_bot_thread()

if __name__ == "__main__":
    # Use the built-in server only for local development, not production.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
