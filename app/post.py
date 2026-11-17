import asyncio
import gzip
import io
import logging
from dataclasses import dataclass
from html import escape

from PIL import Image

log = logging.getLogger(__name__)

COLS, ROWS = 9, 6
CELL, ICON, PAD = 280, 216, 48
BACKGROUND = (29, 28, 30, 255)
ROW_EMOJI, TEXT_ROWS = 11, 2
TEXT_EMOJI_SKIP = 1
BOOST_URL = "https://t.me/boost/emojis_prem"
ROSE_ID = "5258104280379856862"
DOWNLOADS = 8


@dataclass(frozen=True)
class PostSticker:
    file_id: str
    custom_emoji_id: str | None
    emoji: str
    fmt: str


def _fit(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    scale = ICON / max(img.size)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def _opaque(img: Image.Image) -> int:
    return sum(img.getchannel("A").point(lambda a: 255 if a > 16 else 0).histogram()[255:])


def _best(frames: list[Image.Image]) -> Image.Image | None:
    return max(frames, key=_opaque) if frames else None


def _tgs(data: bytes) -> Image.Image | None:
    from rlottie_python import LottieAnimation

    anim = LottieAnimation.from_data(gzip.decompress(data).decode("utf-8"))
    total = max(1, anim.lottie_animation_get_totalframe())
    picks = sorted({0, total // 4, total // 2, 3 * total // 4})
    return _best([anim.render_pillow_frame(frame_num=f, width=512, height=512) for f in picks])


def _webm(data: bytes) -> Image.Image | None:
    import av

    with av.open(io.BytesIO(data)) as box:
        stream = box.streams.video[0]
        codec = "libvpx-vp9" if stream.codec_context.name == "vp9" else "libvpx"
        ctx = av.CodecContext.create(codec, "r")
        frames = []
        for packet in box.demux(stream):
            for frame in ctx.decode(packet):
                rgba = frame.reformat(format="rgba")
                plane = rgba.planes[0]
                frames.append(Image.frombuffer("RGBA", (rgba.width, rgba.height), bytes(plane),
                                               "raw", "RGBA", plane.line_size, 1).copy())
            if len(frames) >= 30:
                break
    return _best(frames[::6])


def decode(data: bytes, fmt: str) -> Image.Image | None:
    try:
        if fmt == "tgs":
            img = _tgs(data)
        elif fmt == "webm":
            img = _webm(data)
        else:
            img = Image.open(io.BytesIO(data))
            img.load()
        return _fit(img) if img is not None else None
    except Exception as e:
        log.warning("не удалось нарисовать эмодзи: %s", type(e).__name__)
        return None


def render_grid(images: list[Image.Image | None]) -> bytes:
    width, height = COLS * CELL + 2 * PAD, ROWS * CELL + 2 * PAD
    canvas = Image.new("RGBA", (width, height), BACKGROUND)
    for i, img in enumerate(images[:COLS * ROWS]):
        if img is None:
            continue
        row, col = divmod(i, COLS)
        x = PAD + col * CELL + (CELL - img.width) // 2
        y = PAD + row * CELL + (CELL - img.height) // 2
        canvas.alpha_composite(img, (x, y))
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def _emoji(s: PostSticker) -> str:
    alt = escape(s.emoji or "⭐")
    return f'<tg-emoji emoji-id="{s.custom_emoji_id}">{alt}</tg-emoji>' if s.custom_emoji_id else alt


def post_text(link: str, stickers: list[PostSticker]) -> str:
    head = _emoji(stickers[0]) if stickers else ""
    body = stickers[TEXT_EMOJI_SKIP:TEXT_EMOJI_SKIP + ROW_EMOJI * TEXT_ROWS] or stickers
    rows = ["".join(_emoji(s) for s in body[i:i + ROW_EMOJI]) for i in range(0, len(body), ROW_EMOJI)]
    rose = f'<tg-emoji emoji-id="{ROSE_ID}">🌹</tg-emoji>'
    tail = (f'<i>🔗 </i><a href="{escape(link)}"><i>ссылка</i></a><i> #тема </i><i>{rose}</i><i> </i>'
            f'<a href="{BOOST_URL}"><i>boost</i></a>')
    return f"<i>{head} - </i>\n\n" + "\n".join(rows) + "\n\n" + tail


def sticker_fmt(s) -> str:
    if getattr(s, "is_animated", False):
        return "tgs"
    if getattr(s, "is_video", False):
        return "webm"
    return "static"


async def load_stickers(bot, short_name: str) -> list[PostSticker]:
    st = await bot.get_sticker_set(name=short_name)
    return [PostSticker(s.file_id, getattr(s, "custom_emoji_id", None), s.emoji or "", sticker_fmt(s))
            for s in st.stickers]


async def build_image(bot, stickers: list[PostSticker]) -> bytes:
    sem = asyncio.Semaphore(DOWNLOADS)

    async def one(s: PostSticker) -> Image.Image | None:
        async with sem:
            try:
                buf = await bot.download(s.file_id)
            except Exception as e:
                log.warning("не удалось скачать эмодзи: %s", type(e).__name__)
                return None
        return await asyncio.to_thread(decode, buf.read(), s.fmt)

    images = await asyncio.gather(*(one(s) for s in stickers[:COLS * ROWS]))
    return await asyncio.to_thread(render_grid, list(images))
