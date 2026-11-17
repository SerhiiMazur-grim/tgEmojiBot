from html import escape

from app.db import Pack, PendingRequest, Stats, TelethonSession, UserInfo
from app.ui.dates import msk_date
from app.ui.emoji import custom, t


def help_text() -> str:
    return "\n".join([
        f"{t('info')} <b>Как это работает</b>", "",
        "1. Откройте «Новый эмодзи» или «Новые стикеры».",
        "2. Пришлите ссылки или сами эмодзи, можно вперемешку и по частям.",
        "3. Нажмите «Создать пак».",
        "4. Управляйте паками в разделах «Ваши эмодзи» и «Ваши стикеры».",
        "5. «Сгенерировать пост» пришлёт картинку с эмодзи пака и текст для канала.",
    ])


def account_text(session: TelethonSession | None, status: str) -> str:
    head = f"{t('gear')} <b>Общий аккаунт</b>"
    if session is None:
        return f"{head}\n\nАккаунт не подключён. Добавьте сессию."
    lines = [head, "", f"Имя: {escape(session.label)}", f"ID: <code>{session.account_id}</code>"]
    if status:
        lines.append(f"Статус: {escape(status)}")
    return "\n".join(lines)


def account_delete_confirm() -> str:
    return (f"{t('warn')} Удалить сессию? Созданные паки останутся, но управлять ими будет нельзя, "
            "пока сессия того же аккаунта не будет добавлена снова.")


def backend_text() -> str:
    return (f"{t('bot')} <b>Чем создавать</b>\n\nВыбор действует только для администратора. "
            "Пользователи всегда создают через общий аккаунт.")


def add_prompt() -> str:
    return f"{t('token')} Пришлите токен бота от @BotFather. Сообщение с токеном я удалю."


def bad_token() -> str:
    return f"{t('error')} Токен не подошёл. Проверьте его и пришлите ещё раз."


def net_error() -> str:
    return f"{t('error')} Не удалось связаться с Telegram. Попробуйте позже."


def worker_added(username: str) -> str:
    return f"{t('done')} Бот @{escape(username)} добавлен."


def session_prompt() -> str:
    return (f"{t('token')} Пришлите StringSession отдельного аккаунта. "
            "Сообщение со строкой я удалю.")


def session_bad(reason: str) -> str:
    return f"{t('error')} Сессия не подошла: {escape(reason)}."


def session_added(label: str) -> str:
    return f"{t('done')} Telethon-сессия {escape(label)} добавлена."


def reading() -> str:
    return f"{t('wait')} Читаю набор..."


def progress(done: int, total: int, verb: str = "Создаю пак") -> str:
    return f"{t('wait')} {verb}: {done} из {total} ({done * 100 // total}%)"


def done(title: str, skipped: int) -> str:
    lines = [f"{t('done')} Готово: <b>{escape(title)}</b>"]
    if skipped:
        lines.append(f"Не поместилось в лимит: {skipped}")
    return "\n".join(lines)


def clone_failed(reason: str) -> str:
    return f"{t('error')} Не получилось: {escape(reason)}"


def request_sent() -> str:
    return f"{t('wait')} Заявка отправлена администратору. Я напишу, когда её рассмотрят."


def need_access() -> str:
    return f"{t('warn')} Доступ по заявке. Отправьте /start, чтобы подать её."


def request_rejected() -> str:
    return f"{t('error')} Заявка отклонена."


def approved() -> str:
    return f"{t('done')} Доступ открыт. Отправьте /start."


def rejected() -> str:
    return f"{t('error')} Заявка отклонена."


def admin_request(name: str, uid: int, username: str | None) -> str:
    who = f"{escape(name)} (<code>{uid}</code>)"
    if username:
        who += f" @{escape(username)}"
    return f"{t('admin')} <b>Заявка на доступ</b>\n{who}"


def admin_decided(original_html: str, approved: bool) -> str:
    return f"{original_html}\n\n" + (f"{t('done')} Одобрено" if approved else f"{t('error')} Отклонено")


def admin_unreachable() -> str:
    return f"{t('warn')} Не удалось отправить заявку администратору. Попробуйте позже."


def blocked() -> str:
    return f"{t('error')} Доступ закрыт."


def request_missing() -> str:
    return "Заявка не найдена."


def already_decided() -> str:
    return "Заявка уже рассмотрена."


def bad_request_data() -> str:
    return "Некорректные данные кнопки."


def no_access_alert() -> str:
    return "Нет доступа"


def admin_only() -> str:
    return "Только для администратора"


def blocked_alert() -> str:
    return "Доступ закрыт"


def panel_text() -> str:
    return f"{t('admin')} <b>Админ-панель</b>"


def requests_text(items: list[PendingRequest], total: int) -> str:
    lines = [f"{t('mail')} <b>Заявки на доступ</b>", ""]
    if not items:
        lines.append("Новых заявок нет.")
    for r in items:
        lines.append(f"{escape(r.name)} (<code>{r.user_id}</code>)")
    if total > len(items) > 0:
        lines += ["", f"Всего ожидают: {total}"]
    return "\n".join(lines)


def stats_text(s: Stats) -> str:
    kinds = {"token": "по токену", "managed": "созданные", "telethon": "Telethon"}
    by_kind = ", ".join(f"{kinds.get(k, k)}: {n}" for k, n in sorted(s.clones_by_kind.items())) or "пока нет"
    return "\n".join([
        f"{t('stats')} <b>Статистика</b>", "",
        f"Допущено: {s.allowed}",
        f"Ждут решения: {s.pending}",
        f"Заблокировано: {s.blocked}",
        f"Ботов по токену: {s.workers_token}",
        f"Созданных ботов: {s.workers_managed}",
        f"Telethon-сессий: {s.telethon}",
        f"Паков склонировано: {s.clones_total} (за 7 дней: {s.clones_7d})",
        f"По типу воркера: {by_kind}",
    ])


def users_text(items: list[UserInfo], total: int) -> str:
    lines = [f"{t('users')} <b>Пользователи</b>", ""]
    if not items:
        lines.append("Пока никого нет.")
    for u in items:
        tag = f" @{escape(u.username)}" if u.username else ""
        lines.append(f"{escape(u.name)}{tag} (<code>{u.user_id}</code>)")
    if total > len(items) > 0:
        lines += ["", f"Всего допущено: {total}"]
    return "\n".join(lines)


def user_card(info: UserInfo, packs: int, blocked: bool, allowed: bool) -> str:
    status = "заблокирован" if blocked else ("допущен" if allowed else "нет доступа")
    nick = f"@{escape(info.username)}" if info.username else "ник не указан"
    return "\n".join([
        f"{t('users')} <b>{escape(info.name)}</b>", "",
        f"ID: <code>{info.user_id}</code>",
        f"Ник: {nick}",
        f"Статус: {status}",
        f"С нами с: {escape(info.first_seen[:10])}",
        f"Паков склонировано: {packs}",
    ])


def search_prompt() -> str:
    return f"{t('info')} Пришлите id или @ник пользователя."


def user_not_found() -> str:
    return f"{t('warn')} Не нашёл такого пользователя. Попробуйте ещё раз."


def cannot_touch_admin() -> str:
    return "Администратора нельзя отозвать или заблокировать"


def user_not_found_alert() -> str:
    return "Пользователь не найден"


def add_choice() -> str:
    return f"{t('add')} <b>Добавить бота</b>\n\nВыберите способ."


def create_bot_hint(username: str) -> str:
    return (f"Нажмите кнопку «Создать бота» внизу и выберите имя. Остальное @{escape(username)} сделает сам. "
            "Если кнопка не сработала, обновите Telegram или добавьте бота по токену.")


def managed_failed() -> str:
    return f"{t('error')} Не удалось получить токен бота от Telegram. Добавьте бота по токену."


def title_invalid(limit: int) -> str:
    return f"{t('warn')} Название должно быть от 1 до {limit} символов. Пришлите ещё раз."


KIND_NAMES = {"emoji": "эмодзи", "sticker": "стикеры"}
LIMITS = {"emoji": 200, "sticker": 120}


def home_text() -> str:
    return f"{t('hello')} <b>Эмодзи и стикеры</b>\n\nСоздавайте паки из чужих наборов и управляйте своими."


def packs_text(kind: str, total: int) -> str:
    head = f"{t('list')} <b>Ваши {KIND_NAMES[kind]}</b>"
    return f"{head}\n\nПока ничего нет." if not total else f"{head}\n\nВсего паков: {total}"


def pack_text(p: Pack) -> str:
    return "\n".join([
        f"{t('pin')} <b>{escape(p.title)}</b>", "",
        f"Заполнено: {p.count} из {LIMITS[p.kind]}",
        f"Изменён: {msk_date(p.updated_at)}",
    ])


def pack_missing_alert() -> str:
    return "Раздел недоступен"


def post_wait() -> str:
    return f"{t('wait')} Рисую картинку пака..."


def post_ready() -> str:
    return f"{t('done')} Пост готов. Впишите тему после «-» и в хэштег."


def post_failed(reason: str) -> str:
    return f"{t('error')} Не удалось сделать пост: {escape(reason)}"


def op_busy() -> str:
    return f"{t('wait')} Операция уже идёт. Попробуйте через пару минут."


def op_partial(done: int, reason: str) -> str:
    return f"{t('warn')} Выполнено только {done}. Причина: {escape(reason)}"


def new_prompt(kind: str) -> str:
    what = "эмодзи" if kind == "emoji" else "стикеры"
    how = "эмодзи и ссылки" if kind == "emoji" else "стикеры и ссылки"
    return (f"{t('new')} <b>Новые {what}</b>\n\nПришлите {how} в любом порядке, можно одним сообщением. "
            f"Из ссылки можно взять часть: <code>t.me/addemoji/Pack 4, 6-8</code>")


def _pack_url(kind: str, name: str) -> str:
    return f"t.me/{'addemoji' if kind == 'emoji' else 'addstickers'}/{escape(name)}"


def link_input(kind: str) -> str:
    return (f"{t('link')} Пришлите конец ссылки. Для <code>{_pack_url(kind, 'my_pack')}</code> это "
            "<code>my_pack</code>.\n\nЛатинские буквы, цифры и _, начало с буквы.")


def link_input_pack(kind: str, name: str) -> str:
    return (f"{t('link')} Сейчас: {_pack_url(kind, name)}\n\nПришлите новый конец ссылки. "
            "Латинские буквы, цифры и _, начало с буквы.")


def link_confirm(kind: str, old: str, new: str) -> str:
    return (f"{t('warn')} <b>Изменить ссылку?</b>\n\nБыло: {_pack_url(kind, old)}\nСтанет: {_pack_url(kind, new)}\n\n"
            "Telegram не меняет ссылку у готового пака, поэтому он будет создан заново, а старый удалён. "
            "Старая ссылка перестанет работать, а у тех, кто добавил пак, его нужно добавить заново.")


def link_old_left(kind: str, old: str) -> str:
    return f"{t('warn')} Новый пак создан, но старый удалить не удалось: {_pack_url(kind, old)}"


def cart_text(kind: str, count: int, title: str | None, note: str, short_name: str | None = None) -> str:
    lines = [f"{t('pin')} <b>В новом паке: {count} из {LIMITS[kind]}</b>"]
    if title:
        lines.append(f"Название: {escape(title)}")
    if short_name:
        lines.append(f"Ссылка: {_pack_url(kind, short_name)}")
    if note:
        lines += ["", escape(note)]
    if count:
        lines += ["", "Пришлите ещё или создайте пак."]
    return "\n".join(lines)


def title_input() -> str:
    return f"{t('edit')} Пришлите название пака, от 1 до 64 символов."


def pack_done(title: str, skipped: int) -> str:
    return done(title, skipped)


def pack_partial(link: str, count: int, reason: str) -> str:
    return (f"{t('warn')} Пак создан не полностью: добавлено {count}.\n{escape(link)}\n"
            f"Причина: {escape(reason)}")


def pick_section() -> str:
    return f"{t('info')} Выберите раздел в меню."


def rename_prompt(title: str) -> str:
    return f"{t('edit')} Сейчас: <b>{escape(title)}</b>\n\nПришлите новое название, от 1 до 64 символов."


def rename_confirm(old: str, new: str) -> str:
    return f"{t('edit')} Переименовать пак?\n\nБыло: {escape(old)}\nСтанет: <b>{escape(new)}</b>"


def delete_confirm(title: str, count: int) -> str:
    return (f"{t('warn')} Удалить пак <b>{escape(title)}</b>?\n\n"
            f"В нём {count} шт. Вернуть его будет нельзя.")


def pack_deleted(title: str) -> str:
    return f"{t('done')} Пак <b>{escape(title)}</b> удалён."


def preview_lines(stickers, start: int, stop: int, numbered: bool = True) -> str:
    parts = []
    for i in range(start, min(stop, len(stickers))):
        s = stickers[i]
        emoji = custom(s.custom_emoji_id, s.emoji) if s.custom_emoji_id else ""
        parts.append(f"{i + 1} {emoji}".strip() if numbered else emoji)
    return "  ".join(parts)


def remove_prompt(title: str, preview: str) -> str:
    return (f"{t('delete')} <b>{escape(title)}</b>\n\n{preview}\n\n"
            "Пришлите номера: <code>20-30</code> или <code>4, 6-8</code>")


def _numbers(positions: list[int], limit: int = 40) -> str:
    shown = ", ".join(str(p) for p in positions[:limit])
    return shown + (f" и ещё {len(positions) - limit}" if len(positions) > limit else "")


def remove_confirm(positions: list[int], preview: str) -> str:
    body = f"Номера: {_numbers(positions)}"
    if preview:
        body += f"\n\n{preview}"
    return f"{t('warn')} Удалить {len(positions)} шт.?\n\n{body}"


def remove_out_of_range(bad: list[int], count: int) -> str:
    return f"{t('warn')} В паке только {count} шт. Нет номеров: {_numbers(bad)}. Пришлите ещё раз."


def remove_all_refused() -> str:
    return f"{t('warn')} Нельзя удалить всё содержимое. Удалите пак целиком."


def add_prompt_pack(title: str) -> str:
    return (f"{t('add')} Добавление в <b>{escape(title)}</b>\n\nПришлите ссылку на весь пак, эмодзи или часть набора: "
            "<code>t.me/addemoji/Pack 4, 6-8</code>")


def add_confirm(adding: int, after: int, limit: int, note: str) -> str:
    lines = [f"{t('add')} Добавятся: {adding}, станет: {after} из {limit}"]
    if note:
        lines += ["", escape(note)]
    return "\n".join(lines)
