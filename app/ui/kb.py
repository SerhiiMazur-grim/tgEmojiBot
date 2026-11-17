from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db import Pack, PendingRequest, UserInfo, Worker
from app.ui.dates import msk_date
from app.ui.emoji import icon

AUTO_DANGER = {"Назад", "Отмена"}


def btn(text: str, *, cb: str | None = None, url: str | None = None, icon_name: str | None = None,
        icon_id: str | None = None, style: str | None = None) -> InlineKeyboardButton:
    if (cb is None) == (url is None):
        raise ValueError("нужен ровно один из cb / url")
    kwargs: dict = {"text": text}
    if cb is not None:
        kwargs["callback_data"] = cb
    else:
        kwargs["url"] = url
    icon_value = icon_id or (icon(icon_name) if icon_name else None)
    if icon_value:
        kwargs["icon_custom_emoji_id"] = icon_value
    style = style or ("danger" if text in AUTO_DANGER else None)
    if style:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)


def back(cb: str) -> list[InlineKeyboardButton]:
    return [btn("Назад", cb=cb, icon_name="back")]


def markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def _short(name: str, n: int = 16) -> str:
    return name if len(name) <= n else name[:n].rstrip()


def _nav(prefix: str, page: int, total: int, size: int) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(btn("Раньше", cb=f"{prefix}:{page - 1}", icon_name="back", style="primary"))
    if (page + 1) * size < total:
        row.append(btn("Дальше", cb=f"{prefix}:{page + 1}", icon_name="forward", style="primary"))
    return row


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [btn("Новый эмодзи", cb="new:emoji", icon_name="new", style="success")],
        [btn("Новые стикеры", cb="new:sticker", icon_name="new", style="success")],
        [btn("Ваши эмодзи", cb="menu:packs:emoji:0", icon_name="list", style="primary")],
        [btn("Ваши стикеры", cb="menu:packs:sticker:0", icon_name="list", style="primary")],
    ]
    if is_admin:
        rows.append([btn("Админ-панель", cb="adm:panel", icon_name="admin")])
    return markup(*rows)


def packs_kb(items: list[Pack], kind: str, page: int, total: int, size: int) -> InlineKeyboardMarkup:
    rows = [[btn(f"{_short(p.title, 24)} | {msk_date(p.updated_at)}", cb=f"menu:pack:{p.id}",
                 icon_id=p.first_emoji_id, icon_name="pin")] for p in items]
    nav = _nav(f"menu:packs:{kind}", page, total, size)
    if nav:
        rows.append(nav)
    rows.append(back("menu:home"))
    return markup(*rows)


def pack_kb(p: Pack) -> InlineKeyboardMarkup:
    link_kind = "addemoji" if p.kind == "emoji" else "addstickers"
    return markup(
        [btn("Открыть пак", url=f"https://t.me/{link_kind}/{p.short_name}", icon_name="link")],
        [btn("Добавить", cb=f"pk:add:{p.id}", icon_name="add", style="success"),
         btn("Удалить из пака", cb=f"pk:rm:{p.id}:0", icon_name="delete", style="danger")],
        [btn("Переименовать", cb=f"pk:ren:{p.id}", icon_name="edit", style="primary"),
         btn("Изменить ссылку", cb=f"pk:link:{p.id}", icon_name="link", style="primary")],
        *([post_btn(p.id)] if p.kind == "emoji" else []),
        [btn("Удалить пак", cb=f"pk:del:{p.id}", icon_name="delete", style="danger")],
        back(f"menu:packs:{p.kind}:0"),
    )


def cart_kb(has_items: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_items:
        rows.append([btn("Создать пак", cb="new:go", icon_name="done", style="success")])
        rows.append([btn("Название", cb="new:title", icon_name="edit", style="primary"),
                     btn("Ссылка", cb="new:link", icon_name="link", style="primary")])
        rows.append([btn("Очистить", cb="new:clear", icon_name="delete", style="danger")])
    rows.append(back("menu:home"))
    return markup(*rows)


def title_back_kb() -> InlineKeyboardMarkup:
    return markup(back("new:cart"))


def post_btn(pack_id: int) -> list:
    return [btn("Сгенерировать пост", cb=f"pk:post:{pack_id}", icon_name="forward", style="success")]


def created_kb(link: str, kind: str, pack_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [[btn("Открыть пак", url=link, icon_name="link")]]
    if pack_id is not None and kind == "emoji":
        rows.append(post_btn(pack_id))
    rows += [[btn("Ваши паки", cb=f"menu:packs:{kind}:0", icon_name="list", style="primary")], back("menu:home")]
    return markup(*rows)


def pack_back_kb(pack_id: int) -> InlineKeyboardMarkup:
    return markup(back(f"menu:pack:{pack_id}"))


def confirm_kb(yes_cb: str, no_cb: str, danger: bool = False) -> InlineKeyboardMarkup:
    return markup([btn("Подтвердить", cb=yes_cb, icon_name="done", style="danger" if danger else "success"),
                   btn("Отмена", cb=no_cb, icon_name="cancel")])


def after_delete_kb(kind: str) -> InlineKeyboardMarkup:
    return markup([btn("Ваши паки", cb=f"menu:packs:{kind}:0", icon_name="list", style="primary")],
                  back("menu:home"))


def remove_kb(pack_id: int, page: int, total: int, size: int) -> InlineKeyboardMarkup:
    rows = []
    nav = _nav(f"pk:rm:{pack_id}", page, total, size)
    if nav:
        rows.append(nav)
    rows.append(back(f"menu:pack:{pack_id}"))
    return markup(*rows)


def back_home() -> InlineKeyboardMarkup:
    return markup([btn("Главная", cb="menu:home", icon_name="home", style="primary")])


def panel_kb(pending: int) -> InlineKeyboardMarkup:
    return markup(
        [btn(f"Заявки ({pending})", cb="adm:req:0", icon_name="mail", style="primary")],
        [btn("Пользователи", cb="adm:users:0", icon_name="users", style="primary")],
        [btn("Статистика", cb="adm:stats", icon_name="stats", style="primary")],
        [btn("Аккаунт", cb="adm:acct", icon_name="gear", style="primary")],
        [btn("Чем создавать", cb="adm:backend", icon_name="bot", style="primary")],
        back("menu:home"),
    )


def requests_kb(items: list[PendingRequest], page: int, total: int, size: int) -> InlineKeyboardMarkup:
    rows = [[btn(f"Одобрить: {_short(r.name)}", cb=f"adm:ok:{r.user_id}:{page}", icon_name="done", style="success"),
             btn("Отклонить", cb=f"adm:no:{r.user_id}:{page}", icon_name="error", style="danger")] for r in items]
    nav = _nav("adm:req", page, total, size)
    if nav:
        rows.append(nav)
    rows.append(back("adm:panel"))
    return markup(*rows)


def request_kb(uid: int) -> InlineKeyboardMarkup:
    return markup([btn("Одобрить", cb=f"acc:ok:{uid}", icon_name="done", style="success"),
                   btn("Отклонить", cb=f"acc:no:{uid}", icon_name="error", style="danger")])


def stats_kb() -> InlineKeyboardMarkup:
    return markup([btn("Обновить", cb="adm:stats", icon_name="refresh", style="primary")], back("adm:panel"))


def users_kb(items: list[UserInfo], page: int, total: int, size: int) -> InlineKeyboardMarkup:
    rows = [[btn(_short(u.name, 24), cb=f"adm:user:{u.user_id}", icon_name="users", style="primary")] for u in items]
    nav = _nav("adm:users", page, total, size)
    if nav:
        rows.append(nav)
    rows.append([btn("Поиск", cb="adm:search", icon_name="target", style="primary")])
    rows.append(back("adm:panel"))
    return markup(*rows)


def user_card_kb(uid: int, blocked: bool, allowed: bool) -> InlineKeyboardMarkup:
    rows = []
    if allowed:
        rows.append([btn("Отозвать доступ", cb=f"adm:revoke:{uid}", icon_name="delete", style="danger")])
    if blocked:
        rows.append([btn("Разблокировать", cb=f"adm:unblock:{uid}", icon_name="done", style="success")])
    else:
        rows.append([btn("Заблокировать", cb=f"adm:block:{uid}", icon_name="block", style="danger")])
    rows.append(back("adm:users:0"))
    return markup(*rows)


def search_cancel_kb() -> InlineKeyboardMarkup:
    return markup([btn("Отмена", cb="adm:users:0", icon_name="cancel")])


def account_kb(session_id: int | None) -> InlineKeyboardMarkup:
    rows = []
    if session_id is not None:
        rows.append([btn("Проверить", cb="adm:acctchk", icon_name="refresh", style="primary")])
    rows.append([btn("Заменить сессию" if session_id is not None else "Добавить сессию", cb="w:tt",
                     icon_name="token", style="success")])
    if session_id is not None:
        rows.append([btn("Удалить сессию", cb=f"adm:acctdel:{session_id}", icon_name="delete", style="danger")])
    rows.append(back("adm:panel"))
    return markup(*rows)


def backend_kb(workers: list[Worker], choice: str, has_session: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_session:
        chosen = choice == "telethon"
        rows.append([btn("Общий аккаунт", cb="adm:bk:tt", icon_name="done" if chosen else "gear",
                         style="success" if chosen else "primary")])
    for w in workers:
        chosen = choice == f"bot:{w.id}"
        rows.append([btn(f"@{w.username}", cb=f"adm:bk:{w.id}", icon_name="done" if chosen else "bot",
                         style="success" if chosen else "primary"),
                     btn("Удалить", cb=f"w:del:{w.id}", icon_name="delete", style="danger")])
    rows.append([btn("Добавить бота", cb="w:add", icon_name="add", style="success")])
    rows.append(back("adm:panel"))
    return markup(*rows)


def add_choice_kb() -> InlineKeyboardMarkup:
    return markup([btn("Создать бота", cb="w:create", icon_name="add", style="success"),
                   btn("По токену", cb="w:token", icon_name="token", style="primary")],
                  back("adm:backend"))


def add_kb(pack_id: int, has_items: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_items:
        rows.append([btn("Подтвердить", cb=f"pk:addok:{pack_id}", icon_name="done", style="success"),
                     btn("Очистить", cb=f"pk:addclr:{pack_id}", icon_name="delete", style="danger")])
    rows.append(back(f"menu:pack:{pack_id}"))
    return markup(*rows)
