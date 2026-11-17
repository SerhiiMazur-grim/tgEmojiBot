import asyncio
import os
import sys
import time
import traceback
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.token import TokenValidationError
from aiohttp_socks import ProxyConnector
from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key
from telethon import functions, types
from telethon.errors import (ApiIdInvalidError, AuthRestartError, FloodWaitError, PasswordHashInvalidError,
                             PhoneCodeExpiredError, PhoneCodeInvalidError, PhoneNumberBannedError,
                             PhoneNumberFloodError, PhoneNumberInvalidError, SendCodeUnavailableError,
                             SessionPasswordNeededError)

from app.cloner import mtproto
from app.config import Config, load_config
from app.factory import make_bot
from app.ui.emoji import EMO

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
ERROR_PATH = BASE_DIR / "error.txt"
PROXY_SCHEMES = ("socks5", "socks4", "http")
TELEGRAM_URL = "https://api.telegram.org/"
SECRET_KEYS = ("BOT_TOKEN", "TG_API_HASH", "TG_SESSION", "SESSION_KEY", "PROXY_URL")
REQUEST_TIMEOUT = 30
STEP_PAUSE = 2
CONFIRM_DELAY = 10

COLOR = sys.stdout.isatty()
BOLD = "1;97"
WHITE = "97"
BLUE = "94"
GREEN = "92"
RED = "91"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def clear() -> None:
    if COLOR:
        print("\033[2J\033[3J\033[H", end="", flush=True)


def screen(title: str, *lines: str) -> None:
    clear()
    print(paint(BOLD, title))
    for line in lines:
        print(paint(WHITE, line))
    print()


def good(text: str) -> None:
    print(paint(GREEN, text))


def bad(text: str) -> None:
    print(paint(RED, text))


def read(prompt: str, danger: str = "", end: str = ": ") -> str:
    label = paint(BLUE, prompt) + (paint(RED, danger) if danger else "") + paint(BLUE, end)
    try:
        return input(label + ("\033[97m" if COLOR else "")).strip()
    finally:
        if COLOR:
            print("\033[0m", end="", flush=True)


def pause() -> None:
    time.sleep(STEP_PAUSE)


def wait_enter() -> None:
    read("Нажмите Enter, чтобы продолжить", end="")


def wait_close() -> None:
    with suppress(EOFError, KeyboardInterrupt):
        read("Enter - чтобы закрыть", end="")


def hidden_values(env: dict, extra: tuple) -> list[str]:
    values = [env.get(k, "") for k in SECRET_KEYS] + list(extra)
    for value in list(values):
        with suppress(ValueError):
            values.append(urlsplit(value).password or "")
    return sorted({v for v in values if v}, key=len, reverse=True)


def log_error(env: dict, where: str, error: Exception, extra: tuple) -> None:
    text = "".join(traceback.format_exception(error))
    for value in hidden_values(env, extra):
        text = text.replace(value, "<скрыто>")
    with suppress(OSError):
        with open(ERROR_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n{text}\n")
        os.chmod(ERROR_PATH, 0o600)


def report(env: dict, where: str, error: Exception, *extra: str) -> None:
    log_error(env, where, error, extra)
    bad(f"{where}: {type(error).__name__}. Подробности в error.txt.")


def load_env() -> dict:
    if not ENV_PATH.exists():
        return {}
    return {k: v or "" for k, v in dotenv_values(ENV_PATH).items()}


def write_env(env: dict) -> None:
    tmp = ENV_PATH.with_name(".env.tmp")
    try:
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        for key, value in env.items():
            set_key(str(tmp), key, value)
        with suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, ENV_PATH)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def no_echo():
    if os.name == "nt" or not sys.stdin.isatty():
        yield
        return
    import termios
    saved = termios.tcgetattr(sys.stdin)
    silent = termios.tcgetattr(sys.stdin)
    silent[3] &= ~termios.ECHO
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, silent)
    try:
        yield
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved)


def drop_typed_input() -> None:
    if not sys.stdin.isatty():
        return
    if os.name == "nt":
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    else:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)


def confirm_write() -> bool:
    exists = ENV_PATH.exists()
    screen("Применить изменения?",
           "Файл .env будет перезаписан введёнными значениями." if exists
           else "Будет создан файл .env с введёнными значениями.")
    print(paint(BOLD, "В файле .env хранятся токены бота и аккаунта Telegram, не передавайте этот файл никому!!!"))
    print(paint(WHITE, "Чтобы отменить, введите n или нажмите Ctrl+C."))
    print()
    with no_echo():
        for left in range(CONFIRM_DELAY, 0, -1):
            print(paint(WHITE, f"\rМожно будет ввести через {left} сек.  "), end="", flush=True)
            time.sleep(1)
        drop_typed_input()
    print("\r" + " " * 40 + "\r", end="", flush=True)
    if exists:
        answer = read("Нажмите Enter, чтобы перезаписать файл, ", "старый файл будет ОЧИЩЕН", ". ")
    else:
        answer = read("Нажмите Enter, чтобы записать файл", end=". ")
    return not answer


def ask(prompt: str, current: str = "") -> str:
    suffix = " (Enter - оставить сохранённое)" if current else ""
    return read(f"{prompt}{suffix}") or current


def valid_proxy(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme in PROXY_SCHEMES and bool(parts.hostname) and parts.port is not None
    except ValueError:
        return False


def parse_ids(raw: str) -> list[int] | None:
    items = [x for x in raw.replace(" ", "").split(",") if x]
    return [int(x) for x in items] if items and all(x.isascii() and x.isdigit() for x in items) else None


def ask_proxy(current: str) -> str:
    while True:
        if current:
            raw = read("Прокси (Enter - оставить сохранённое, 0 - без прокси)")
        else:
            raw = read("Прокси (нажмите Enter, если прокси не нужен, например, если сервер в Германии)")
        if raw == "0":
            return ""
        if not raw:
            return current
        if valid_proxy(raw):
            return raw
        bad("Неверный формат прокси.")


async def check_telegram(proxy: str) -> None:
    connector = ProxyConnector.from_url(proxy, rdns=True) if proxy else None
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async with session.get(TELEGRAM_URL, allow_redirects=False):
            pass


async def setup_proxy(env: dict) -> None:
    screen("Подключение прокси",
           "Прокси нужен, если Telegram недоступен напрямую.",
           "Формат: socks5://user:pass@host:port, socks4://host:port или http://host:port")
    current = env.get("PROXY_URL", "")
    while True:
        proxy = ask_proxy(current)
        try:
            await check_telegram(proxy)
        except Exception as e:
            report(env, "Прокси не подошел" if proxy else "Telegram недоступен напрямую", e, proxy)
            continue
        env["PROXY_URL"] = proxy
        good("Прокси работает." if proxy else "Telegram доступен напрямую.")
        pause()
        return


async def get_me(token: str, proxy: str):
    bot = make_bot(token, proxy or None)
    try:
        return await asyncio.wait_for(bot.get_me(), REQUEST_TIMEOUT)
    finally:
        await bot.session.close()


async def setup_bot(env: dict):
    screen("Подключение бота.",
           "Желательно, чтобы владелец бота был с Telegram Premium, чтобы работали премиум-эмодзи.",
           "Токен бота выдаёт @BotFather.")
    current = env.get("BOT_TOKEN", "")
    while True:
        token = ask("Токен бота", current)
        if not token:
            bad("Токен не может быть пустым.")
            continue
        try:
            me = await get_me(token, env.get("PROXY_URL", ""))
        except (TelegramUnauthorizedError, TokenValidationError):
            bad("Telegram отклонил токен. Проверьте его и введите заново.")
            current = ""
            continue
        except Exception as e:
            report(env, "Нет связи с Telegram", e, token)
            continue
        env["BOT_TOKEN"] = token
        good(f"Бот работает: @{me.username}.")
        pause()
        return me


def ask_admins(env: dict) -> list[int]:
    screen("Введите ID администраторов через запятую.", "Свой id можно узнать у @userinfobot.")
    while True:
        ids = parse_ids(ask("ADMIN_IDS", env.get("ADMIN_IDS", "")))
        if ids:
            env["ADMIN_IDS"] = ",".join(str(i) for i in ids)
            return ids
        bad("Нужны числа через запятую.")


def is_unreachable(e: Exception) -> bool:
    return isinstance(e, TelegramForbiddenError) or "chat not found" in str(e).lower()


async def send_probe(bot, chat_id: int, **kwargs):
    msg = await bot.send_message(chat_id, **kwargs)
    with suppress(Exception):
        await bot.delete_message(chat_id, msg.message_id)
    return msg


async def text_emoji_works(bot, chat_id: int) -> bool:
    e = EMO["done"]
    text = f'<tg-emoji emoji-id="{e.text_id}">{e.fallback}</tg-emoji> Проверка'
    msg = await send_probe(bot, chat_id, text=text, parse_mode="HTML")
    return any(x.type == "custom_emoji" for x in msg.entities or [])


async def button_emoji_works(bot, chat_id: int) -> bool:
    button_id = EMO["done"].button_id
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Проверка", callback_data="setup", icon_custom_emoji_id=button_id)]])
    try:
        msg = await send_probe(bot, chat_id, text="Проверка", reply_markup=markup)
    except TelegramBadRequest as e:
        if is_unreachable(e):
            raise
        return False
    return bool(msg.reply_markup) and msg.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == button_id


async def check_premium(env: dict, me, chat_id: int) -> None:
    screen("Проверка премиум-эмодзи", "Первому администратору отправляются пробные сообщения, они сразу удаляются.")
    bot = make_bot(env["BOT_TOKEN"], env.get("PROXY_URL") or None)
    try:
        while True:
            try:
                text_ok = await asyncio.wait_for(text_emoji_works(bot, chat_id), REQUEST_TIMEOUT)
                button_ok = await asyncio.wait_for(button_emoji_works(bot, chat_id), REQUEST_TIMEOUT)
                break
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                if not is_unreachable(e):
                    report(env, "Проверка премиум-эмодзи не прошла", e)
                    wait_enter()
                    return
                read(f"Откройте @{me.username}, нажмите Старт с аккаунта {chat_id} и затем Enter здесь", end="")
            except EOFError:
                raise
            except Exception as e:
                report(env, "Проверка премиум-эмодзи не прошла", e)
                wait_enter()
                return
    finally:
        await bot.session.close()
    for label, works in (("в тексте", text_ok), ("на кнопках", button_ok)):
        (good if works else bad)(f"Премиум-эмодзи {label}: " + ("работают." if works else "НЕ работают."))
    if text_ok and button_ok:
        pause()
        return
    print(paint(WHITE, "Владельцу бота нужен Telegram Premium или имя, купленное на Fragment."))
    print(paint(WHITE, "Без этого бот работает, но показывает обычные символы."))
    wait_enter()


def make_config(env: dict) -> Config:
    admins = frozenset(int(x) for x in env["ADMIN_IDS"].split(","))
    return Config(env["BOT_TOKEN"], admins, env.get("DB_PATH", "bot.db"), env.get("PROXY_URL") or None,
                  int(env["TG_API_ID"]), env["TG_API_HASH"], env["SESSION_KEY"])


def code_destination(sent_type) -> str:
    auth = types.auth
    if isinstance(sent_type, auth.SentCodeTypeApp):
        return "Код отправлен в сам аккаунт: чат от имени Telegram на устройстве, где выполнен вход."
    if isinstance(sent_type, (auth.SentCodeTypeSms, auth.SentCodeTypeSmsWord, auth.SentCodeTypeSmsPhrase,
                              auth.SentCodeTypeFirebaseSms)):
        return "Код отправлен по SMS."
    if isinstance(sent_type, auth.SentCodeTypeEmailCode):
        return f"Код отправлен на почту {sent_type.email_pattern}."
    if isinstance(sent_type, auth.SentCodeTypeCall):
        return "Код продиктуют в телефонном звонке."
    if isinstance(sent_type, auth.SentCodeTypeMissedCall):
        return f"Код придёт входящим звонком с номера, начинающегося на {sent_type.prefix}. Код - последние цифры номера."
    if isinstance(sent_type, auth.SentCodeTypeFlashCall):
        return "Код придёт входящим звонком. Код - последние цифры номера."
    if isinstance(sent_type, auth.SentCodeTypeFragmentSms):
        return f"Код отправлен на Fragment: {sent_type.url}"
    return ""


def show_code_info(sent) -> None:
    destination = code_destination(sent.type)
    if destination:
        good(destination)
    print(paint(WHITE, "Код мог прийти на почту, в SMS или от имени Telegram в самом аккаунте."))


async def send_code(client, phone: str):
    settings = types.CodeSettings()
    request = functions.auth.SendCodeRequest(phone, client.api_id, client.api_hash, settings)
    for attempt in range(3):
        try:
            return await client(request)
        except AuthRestartError:
            if attempt == 2:
                raise


async def request_code(client):
    while True:
        phone = read("Номер телефона, например +79990000000").replace(" ", "").replace("-", "").lstrip("+")
        if not phone.isdigit():
            bad("Неверный номер телефона.")
            continue
        try:
            sent = await send_code(client, phone)
        except PhoneNumberInvalidError:
            bad("Telegram отклонил номер телефона.")
            continue
        except PhoneNumberBannedError:
            bad("Этот номер заблокирован в Telegram.")
            continue
        except PhoneNumberFloodError:
            bad("Слишком много запросов кода для этого номера. Попробуйте позже.")
            continue
        if isinstance(sent, types.auth.SentCodePaymentRequired):
            bad("Telegram требует оплату отправки кода на этот номер. Используйте другой номер.")
            continue
        if isinstance(sent, types.auth.SentCode) and isinstance(sent.type, types.auth.SentCodeTypeSetUpEmailRequired):
            bad("Telegram требует привязать почту к аккаунту. Привяжите её в приложении Telegram и повторите.")
            continue
        return phone, sent


async def resend_code(client, phone: str, sent):
    try:
        new = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
    except SendCodeUnavailableError:
        bad("Других способов отправки кода нет.")
        return sent
    if not isinstance(new, types.auth.SentCode):
        bad("Telegram не отправил код другим способом.")
        return sent
    show_code_info(new)
    return new


async def submit_code(client, phone: str, sent, code: str) -> None:
    if isinstance(sent.type, types.auth.SentCodeTypeEmailCode):
        verification = types.EmailVerificationCode(code)
        await client(functions.auth.SignInRequest(phone, sent.phone_code_hash, email_verification=verification))
    else:
        await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)


async def enter_password(client) -> None:
    while True:
        password = read("Пароль двухэтапной проверки")
        if not password:
            continue
        try:
            await client.sign_in(password=password)
            return
        except PasswordHashInvalidError:
            bad("Неверный пароль. Попробуйте ещё раз.")


async def sign_in(client):
    while True:
        phone, sent = await request_code(client)
        if isinstance(sent, types.auth.SentCodeSuccess):
            return await client.get_me()
        show_code_info(sent)
        while True:
            hint = " (Enter - отправить другим способом)" if sent.next_type else ""
            code = read(f"Введите код{hint}").replace(" ", "")
            try:
                if not code:
                    if sent.next_type:
                        sent = await resend_code(client, phone, sent)
                    continue
                await submit_code(client, phone, sent, code)
            except PhoneCodeInvalidError:
                bad("Неверный код. Попробуйте ещё раз.")
                continue
            except PhoneCodeExpiredError:
                bad("Код устарел. Введите номер заново.")
                break
            except SessionPasswordNeededError:
                await enter_password(client)
            return await client.get_me()


async def authorize(cfg: Config):
    client = mtproto.make_client("", cfg)
    try:
        await client.connect()
        me = await sign_in(client)
        return client.session.save(), me
    finally:
        with suppress(Exception):
            await client.disconnect()


def account_label(me) -> str:
    name = " ".join(x for x in (me.first_name, me.last_name) if x)
    return f'"{name}" @{me.username}' if me.username else f'"{name}"'


def ask_api(env: dict) -> None:
    screen("Авторизация в Telethon, необходимо создание APP на официальной странице Telegram.",
           "api_id и api_hash нужны для официального подключения к Telegram, через него создаются стикерпаки и эмодзипаки.",
           "Создать приложение: войти на https://my.telegram.org/apps , заполнить название и короткое имя, скопировать оба значения.",
           "Можно выполнить на аккаунте без Telegram Premium.")
    while True:
        raw = ask("api_id", env.get("TG_API_ID", ""))
        if raw.isascii() and raw.isdigit():
            env["TG_API_ID"] = raw
            break
        bad("api_id должен быть числом.")
    while True:
        value = ask("api_hash", env.get("TG_API_HASH", ""))
        if value:
            env["TG_API_HASH"] = value
            return
        bad("api_hash не может быть пустым.")


def login_screen() -> None:
    screen("Авторизация в Telegram",
           "Рекомендуется использовать пустой твинк, ведь бот будет иметь полный доступ к аккаунту.",
           "Через этот аккаунт будут создаваться эмодзипаки и стикерпаки, присутствие Telegram Premium необязательно.",
           "Сессия будет записана только в файл .env в этой папке и никуда не передаётся.")


async def setup_telethon(env: dict) -> None:
    ask_api(env)
    login_screen()
    if env.get("TG_SESSION"):
        try:
            info = await mtproto.check_session(env["TG_SESSION"], make_config(env))
        except mtproto.SessionError as e:
            bad(f"Сохранённая сессия не работает: {e}. Нужен новый вход.")
        else:
            good(f"Сохранённая сессия работает: {info.label}.")
            if read("Войти в другой аккаунт? y - да, Enter - нет").lower() != "y":
                return
    while True:
        try:
            session, me = await authorize(make_config(env))
            break
        except ApiIdInvalidError:
            env["TG_API_ID"], env["TG_API_HASH"] = "", ""
            ask_api(env)
            login_screen()
            bad("Telegram отклонил прежние api_id и api_hash.")
        except FloodWaitError as e:
            bad(f"Telegram просит подождать {e.seconds} сек. Попробуйте позже.")
        except EOFError:
            raise
        except Exception as e:
            report(env, "Вход не удался", e)
    env["TG_SESSION"] = session
    good(f"Вы успешно вошли в {account_label(me)}.")
    pause()


async def collect(env: dict) -> None:
    screen("Подготовка окружения.", "Настройки будут записаны в файл .env (конфиг) в этой папке в самом конце.")
    read("Нажмите Enter, чтобы начать", end="")
    if not env.get("SESSION_KEY"):
        env["SESSION_KEY"] = Fernet.generate_key().decode()
    if not env.get("DB_PATH"):
        env["DB_PATH"] = "bot.db"
    await setup_proxy(env)
    me = await setup_bot(env)
    admins = ask_admins(env)
    await check_premium(env, me, admins[0])
    await setup_telethon(env)
    load_config(env)


async def main() -> None:
    env = load_env()
    try:
        await collect(env)
        if not confirm_write():
            screen("Отменено.", "Файл .env не изменён.")
            wait_close()
            return
        write_env(env)
    except EOFError:
        raise
    except Exception as e:
        print()
        report(env, "Подготовка не завершена, файл .env не изменён", e)
        raise SystemExit(1) from None
    screen("Готово, файл .env записан.", "Запуск бота: start.bat в Windows, bash start.sh в Linux.")
    wait_close()


def run() -> None:
    with suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")
    if COLOR and os.name == "nt":
        os.system("")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print(paint("0", "\nПрервано. Файл .env не изменён."))
        sys.exit(1)


if __name__ == "__main__":
    run()
