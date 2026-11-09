"""Картинки пакетів: фото з описом у підписі, повторне використання file_id, запасні варіанти."""

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import FSInputFile

from bot.content import ContentError, parse_content
from tests.fake_telegram import keyboard_of

PACKAGE_KEYBOARD = [[("Отримати посилання на оплату", "pay:vip")], [("Подивитися інші пакети", "packages")]]


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "vip.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 100)
    return path


def with_image(path, description=None):
    def change(raw):
        raw["packages"][2]["image"] = str(path)  # пакет vip
        if description is not None:
            raw["packages"][2]["description"] = description

    return change


async def opened_vip(harness):
    await harness.send("/start")
    harness.telegram.clear()
    await harness.press("pkg:vip")


async def test_package_with_image_is_one_photo_with_caption(make, picture):
    harness = await make(with_image(picture))
    await opened_vip(harness)
    [photo] = harness.telegram.photos(1)
    assert isinstance(photo.photo, FSInputFile) and str(photo.photo.path) == str(picture)
    assert photo.caption.startswith("<b>Пакет «VIP» — 7 990 грн</b>")
    assert photo.parse_mode == "HTML"
    assert keyboard_of(photo.reply_markup) == PACKAGE_KEYBOARD
    assert harness.telegram.sent() == []  # окремого текстового повідомлення немає


async def test_photo_is_uploaded_once_then_reused_by_id(make, picture):
    harness = await make(with_image(picture))
    await opened_vip(harness)
    [first] = harness.telegram.photos(1)
    assert isinstance(first.photo, FSInputFile)  # перший раз — завантаження файлу
    remembered = harness.funnel.photo_ids[str(picture)]
    harness.telegram.clear()
    await harness.press("pkg:vip")
    [again] = harness.telegram.photos(1)
    assert again.photo == remembered  # далі — збережений file_id від Telegram, без повторного завантаження
    assert isinstance(again.photo, str) and again.photo.startswith("photo-")


async def test_long_description_follows_the_photo(make, picture):
    harness = await make(with_image(picture, "<b>{name} — {price}</b>\n" + "Дуже довгий опис. " * 70))
    await opened_vip(harness)
    [photo] = harness.telegram.photos(1)
    [text] = harness.telegram.sent(1)
    assert photo.caption is None and photo.reply_markup is None
    assert text.text.startswith("<b>VIP — 7 990 грн</b>")
    assert keyboard_of(text.reply_markup) == PACKAGE_KEYBOARD


async def test_stale_photo_id_is_replaced_by_the_file(make, picture):
    harness = await make(with_image(picture))
    await opened_vip(harness)
    harness.telegram.clear()
    harness.telegram.fail_next(SendPhoto, lambda m: TelegramBadRequest(method=m, message="Bad Request: wrong file identifier"))
    await harness.press("pkg:vip")
    stale, fresh = harness.telegram.photos(1)
    assert isinstance(stale.photo, str) and isinstance(fresh.photo, FSInputFile)
    assert harness.telegram.sent() == []


async def test_description_still_arrives_when_photo_fails(make, picture):
    harness = await make(with_image(picture))
    harness.telegram.fail_next(SendPhoto, lambda m: TelegramBadRequest(method=m, message="Bad Request: IMAGE_PROCESS_FAILED"))
    await opened_vip(harness)
    [text] = harness.telegram.sent(1)
    assert text.text.startswith("<b>Пакет «VIP» — 7 990 грн</b>")
    assert keyboard_of(text.reply_markup) == PACKAGE_KEYBOARD


async def test_blocked_user_gets_nothing_more(make, picture):
    harness = await make(with_image(picture))
    await harness.send("/start")
    harness.telegram.clear()
    harness.telegram.blocked.add(1)
    await harness.press("pkg:vip")
    assert len(harness.telegram.photos(1)) == 1 and harness.telegram.sent() == []
    assert (await harness.storage.get_user(1)).is_blocked


def errors_of(raw):
    with pytest.raises(ContentError) as info:
        parse_content(raw)
    return "\n".join(info.value.errors)


def test_image_validation(raw_content, tmp_path):
    raw_content["packages"][0]["image"] = "images/немає.jpg"
    assert "packages[1] (start).image: файл картинки «images/немає.jpg» не знайдено" in errors_of(raw_content)

    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a")
    raw_content["packages"][0]["image"] = str(gif)
    assert "картинка має бути у форматі .jpg, .png або .webp" in errors_of(raw_content)

    huge = tmp_path / "huge.png"
    with huge.open("wb") as file:
        file.truncate(11 * 1024 * 1024)
    raw_content["packages"][0]["image"] = str(huge)
    assert "картинка більша за 10 МБ" in errors_of(raw_content)

    raw_content["packages"][0]["image"] = "https://cdn.ai-mastery.xyz/start.jpg"
    assert parse_content(raw_content).package("start").image == "https://cdn.ai-mastery.xyz/start.jpg"


def test_long_description_with_image_is_a_warning(raw_content, picture):
    with_image(picture, "<b>{name}</b>\n" + "а" * 1000)(raw_content)
    warnings = parse_content(raw_content).warnings
    assert any("картинку буде надіслано окремим повідомленням" in w for w in warnings)
