import pytest

from bot import check_content
from bot.content import ContentError, Package, Reminder, load_content, parse_content
from bot.paths import DEFAULT_CONTENT_PATH
from tests.harness import REFERENCE_CONTENT_PATH


def errors_of(raw) -> list[str]:
    with pytest.raises(ContentError) as info:
        parse_content(raw)
    return info.value.errors


def assert_error(raw, *fragments):
    errors = errors_of(raw)
    joined = "\n".join(errors)
    for fragment in fragments:
        assert fragment in joined, joined


# ----------------------------------------------------------------------------- файли контенту


def test_project_content_yaml_is_valid():
    """Справжній content.yaml (тексти замовника) завантажується без помилок — як під час збірки на хостингу."""
    content = load_content(DEFAULT_CONTENT_PATH)
    assert content.packages
    assert "{timer}" in content.messages["offer"]


def test_reference_content_file():
    content = load_content(REFERENCE_CONTENT_PATH)
    s = content.settings
    assert s.offer_duration == 24 * 3600
    assert s.timezone.key == "Europe/Kyiv"
    assert s.timer_update_every == 60
    assert s.packages_after_expiry is False
    assert s.silent_hours is not None and s.silent_hours.describe() == "22:00–08:00"
    assert [p.id for p in content.packages] == ["start", "optimal", "vip"]
    assert [r.id for r in content.reminders] == ["r1", "r2", "r3", "r4"]
    assert content.keyboards["offer"] == (("choose_package",),)
    assert content.keyboards["package"] == (("get_payment_link",), ("other_packages",))
    assert content.keyboards["payment"] == (("pay_url",), ("other_packages",))
    assert content.keyboards["expired"] == ()
    assert content.buttons["choose_package"] == "Обрати пакет"
    assert content.buttons["get_payment_link"] == "Отримати посилання на оплату"
    assert content.buttons["other_packages"] == "Подивитися інші пакети"
    assert "Пропозиція для рішучих. Діє 24 години" in content.messages["offer"]
    assert "{timer}" in content.messages["offer"]
    assert content.package("vip").name == "VIP"
    assert content.package("nope") is None


def test_reference_content_warns_about_placeholder_links():
    content = load_content(REFERENCE_CONTENT_PATH)
    stub_warnings = [w for w in content.warnings if "example.com" in w]
    assert len(stub_warnings) == 3
    # інших попереджень у файлі за замовчуванням бути не повинно
    assert len(content.warnings) == 3, content.warnings


def test_reminders_sorted_by_time(raw_content):
    raw_content["reminders"].reverse()
    content = parse_content(raw_content)
    assert [r.id for r in content.reminders] == ["r1", "r2", "r3", "r4"]


def test_reminder_due_at_and_description():
    after = Reminder(id="a", text="t", after=7200)
    before = Reminder(id="b", text="t", before_end=3600)
    assert after.due_at(1000, 1000 + 86400) == 1000 + 7200
    assert before.due_at(1000, 1000 + 86400) == 1000 + 86400 - 3600
    assert after.describe() == "через 2 год після старту"
    assert before.describe() == "за 1 год до кінця"


def test_package_prices_after_expiry():
    plain = Package(id="a", name="A", price="100", description="d", payment_url="https://p/a")
    special = Package(id="b", name="B", price="100", description="d", payment_url="https://p/b",
                      price_after_expiry="200", payment_url_after_expiry="https://p/b2")
    assert (plain.price_for(True), plain.url_for(True)) == ("100", "https://p/a")
    assert (special.price_for(False), special.url_for(False)) == ("100", "https://p/b")
    assert (special.price_for(True), special.url_for(True)) == ("200", "https://p/b2")


# ----------------------------------------------------------------------------- значення за замовчуванням


def test_settings_defaults_when_missing(raw_content):
    del raw_content["settings"]
    content = parse_content(raw_content)
    assert content.settings.offer_duration == 86400
    assert content.settings.silent_hours is not None  # за замовчуванням 22:00–08:00


def test_empty_silent_hours_disables_them(raw_content):
    raw_content["settings"]["silent_hours"] = ""
    assert parse_content(raw_content).settings.silent_hours is None


def test_optional_texts_have_defaults(raw_content):
    for key in ("package_line", "expired_notice", "fallback", "manager_payment_request", "package_unavailable"):
        del raw_content["messages"][key]
    del raw_content["buttons"]["package"]
    content = parse_content(raw_content)
    assert content.messages["fallback"] == ""
    assert content.messages["package_line"].startswith("{number}")
    assert content.buttons["package"] == "{name} — {price}"


def test_missing_keyboard_uses_default_and_empty_list_removes_buttons(raw_content):
    del raw_content["keyboards"]["payment"]
    raw_content["keyboards"]["reminder"] = []
    content = parse_content(raw_content)
    assert content.keyboards["payment"] == (("pay_url",), ("other_packages",))
    assert content.keyboards["reminder"] == ()


def test_keyboard_layout_and_order_from_config(raw_content):
    raw_content["keyboards"]["package"] = [["other_packages", "get_payment_link"]]
    raw_content["keyboards"]["offer"] = ["choose_package"]  # ряд без дужок теж підходить
    content = parse_content(raw_content)
    assert content.keyboards["package"] == (("other_packages", "get_payment_link"),)
    assert content.keyboards["offer"] == (("choose_package",),)


def test_numbers_become_text(raw_content):
    raw_content["packages"][0]["price"] = 1990
    assert parse_content(raw_content).packages[0].price == "1990"


# ----------------------------------------------------------------------------- помилки


def test_not_a_mapping():
    assert_error(["список"], "розділи")


def test_missing_required_message(raw_content):
    del raw_content["messages"]["offer"]
    assert_error(raw_content, "messages.offer: поле відсутнє")


def test_boolean_instead_of_text(raw_content):
    raw_content["buttons"]["choose_package"] = True
    assert_error(raw_content, "buttons.choose_package", "лапки")


def test_unknown_button_in_keyboard(raw_content):
    raw_content["keyboards"]["offer"] = [["choose_pakage"]]
    assert_error(raw_content, "keyboards.offer[1]", "невідома кнопка «choose_pakage»")


def test_button_not_allowed_in_keyboard(raw_content):
    raw_content["keyboards"]["offer"] = [["get_payment_link"]]
    assert_error(raw_content, "keyboards.offer[1]", "тут використати не можна")


def test_package_errors(raw_content):
    packages = raw_content["packages"]
    packages[1]["id"] = "start"  # дубль
    packages[2]["id"] = "VIP пакет"  # недопустимі символи
    packages[0]["payment_url"] = "оплата тут"
    packages[0]["name"] = "<b>Старт</b>"
    del packages[1]["description"]
    errors = "\n".join(errors_of(raw_content))
    assert "packages[2]: id «start» уже використано" in errors
    assert "packages[3]: id: лише малі латинські літери" in errors
    assert "packages[1] (start).payment_url: «оплата тут» не схоже на посилання" in errors
    assert "packages[1] (start).name: HTML-теги тут не підтримуються" in errors
    assert "packages[2].description: поле відсутнє" in errors


@pytest.mark.parametrize(
    "url", ["https://…ваше-посилання-на-оплату-пакета-Старт…", "https://pay/start", "https://pay.ua/✏️-вставте"]
)
def test_template_links_are_rejected(raw_content, url):
    raw_content["packages"][0]["payment_url"] = url
    assert_error(raw_content, "packages[1] (start).payment_url:", "схоже на заготовку")


@pytest.mark.parametrize("url", ["https://secure.wayforpay.com/payment/s1a2b3", "https://оплата.укр/старт?sum=2490"])
def test_real_links_are_accepted(raw_content, url):
    raw_content["packages"][0]["payment_url"] = url
    assert parse_content(raw_content).packages[0].payment_url == url


def test_no_packages(raw_content):
    raw_content["packages"] = []
    assert_error(raw_content, "packages: потрібен хоча б один пакет")


def test_reminder_errors(raw_content):
    reminders = raw_content["reminders"]
    reminders[0]["before_end"] = "1 год"  # і after, і before_end
    reminders[1]["after"] = "25 год"  # довше за пропозицію
    reminders[2]["before_end"] = "три години"
    del reminders[3]["text"]
    errors = "\n".join(errors_of(raw_content))
    assert "reminders[1] (r1): вкажіть рівно одне з двох" in errors
    assert "reminders[2] (r2).after: має бути більше 0 і менше тривалості пропозиції (24 год)" in errors
    assert "reminders[3] (r3).before_end: не вдалося розібрати «три години»" in errors
    assert "reminders[4] (r4).text: поле відсутнє" in errors


def test_reminders_shorter_offer(raw_content):
    raw_content["settings"]["offer_duration"] = "2 год"
    assert_error(raw_content, "reminders[2] (r2).after: має бути більше 0 і менше тривалості пропозиції (2 год)")


def test_settings_errors(raw_content):
    s = raw_content["settings"]
    s["offer_duration"] = 24
    s["timezone"] = "Kyiv"
    s["timer_update_every"] = "30 с"
    s["packages_after_expiry"] = "ні"
    s["silent_hours"] = "вночі"
    s["packages_per_row"] = 0
    s["time_left_format"] = "скільки лишилось"
    errors = "\n".join(errors_of(raw_content))
    assert "settings.offer_duration: вкажіть одиниці виміру" in errors
    assert "settings.timezone: невідомий часовий пояс «Kyiv»" in errors
    assert "settings.timer_update_every: щонайменше 1 хв" in errors
    assert "settings.packages_after_expiry: очікується true" in errors
    assert "settings.silent_hours: очікується інтервал" in errors
    assert "settings.packages_per_row: ціле число від 1 до 8" in errors
    assert "settings.time_left_format: вкажіть auto або шаблон" in errors


def test_html_errors_are_reported_with_path(raw_content):
    raw_content["messages"]["payment"] = "Оплата<br>{payment_url}"
    raw_content["reminders"][0]["text"] = "<b>Залишилось {time_left}"
    errors = "\n".join(errors_of(raw_content))
    assert "messages.payment: тег <br> не підтримується" in errors
    assert "reminders[1] (r1).text: не закрито тег(и): <b>" in errors


def test_too_long_text(raw_content):
    raw_content["packages"][0]["description"] = "а" * 4000
    assert_error(raw_content, "packages[1] (start).description: занадто довгий текст: 4000 символів")


def test_all_errors_are_collected_at_once(raw_content):
    del raw_content["messages"]["offer"]
    raw_content["buttons"]["pay_url"] = ""
    raw_content["packages"][0]["payment_url"] = "ftp://x"
    assert len(errors_of(raw_content)) >= 3


# ----------------------------------------------------------------------------- попередження


def test_unknown_placeholder_is_a_warning(raw_content):
    raw_content["messages"]["expired"] = "Завершено {deadlnie}"
    content = parse_content(raw_content)
    assert any("messages.expired: невідома підстановка {deadlnie}" in w for w in content.warnings)


def test_unknown_fields_are_warnings(raw_content):
    raw_content["settings"]["ofer_duration"] = "2 год"
    raw_content["packages"][0]["prise"] = "1"
    content = parse_content(raw_content)
    assert any(w.startswith("settings.ofer_duration: невідоме поле") for w in content.warnings)
    assert any(w.startswith("packages[1] (start).prise: невідоме поле") for w in content.warnings)


def test_offer_without_timer_is_a_warning(raw_content):
    raw_content["messages"]["offer"] = "Офер без таймера"
    content = parse_content(raw_content)
    assert any("немає підстановки {timer}" in w for w in content.warnings)


def test_package_buttons_after_expiry_warning(raw_content):
    raw_content["keyboards"]["expired"] = [["choose_package"]]
    content = parse_content(raw_content)
    assert any(w.startswith("keyboards.expired:") for w in content.warnings)
    raw_content["settings"]["packages_after_expiry"] = True
    assert not any(w.startswith("keyboards.expired:") for w in parse_content(raw_content).warnings)


def test_open_packages_after_expiry_need_a_button(raw_content):
    raw_content["settings"]["packages_after_expiry"] = True  # а keyboards.expired лишилась порожньою
    content = parse_content(raw_content)
    assert any(w.startswith("keyboards.expired:") and "[choose_package]" in w for w in content.warnings)


# ----------------------------------------------------------------------------- файл


def test_yaml_syntax_error_has_line_number(tmp_path):
    path = tmp_path / "content.yaml"
    path.write_text('settings:\n  timezone: "Europe/Kyiv\nmessages:\n  offer: x\n', encoding="utf-8")
    with pytest.raises(ContentError) as info:
        load_content(path)
    assert "синтаксична помилка YAML (рядок" in info.value.errors[0]


def test_tab_indentation_is_explained(tmp_path):
    path = tmp_path / "content.yaml"
    path.write_text("settings:\n\ttimezone: Europe/Kyiv\n", encoding="utf-8")
    with pytest.raises(ContentError, match="Tab"):
        load_content(path)


def test_missing_file(tmp_path):
    with pytest.raises(ContentError, match="не знайдено"):
        load_content(tmp_path / "nope.yaml")


def test_file_with_bom_from_windows_notepad(tmp_path):
    path = tmp_path / "content.yaml"
    path.write_bytes(b"\xef\xbb\xbf" + REFERENCE_CONTENT_PATH.read_bytes())
    assert load_content(path).packages


def test_check_content_cli(tmp_path, capsys):
    assert check_content.main([str(REFERENCE_CONTENT_PATH)]) == 0
    out = capsys.readouterr().out
    assert "OK:" in out and "Пакети (3)" in out and "r4: за 1 год до кінця" in out

    broken = tmp_path / "content.yaml"
    broken.write_text("settings: {}\n", encoding="utf-8")
    assert check_content.main([str(broken)]) == 1
    assert "ПОМИЛКА" in capsys.readouterr().out


# ----------------------------------------------------------------------------- інформаційні пакети (без оплати)


def test_packages_without_payment_links_are_informational(raw_content, tmp_path, capsys):
    for package in raw_content["packages"]:
        del package["payment_url"]
    content = parse_content(raw_content)
    assert all(p.payment_url is None and p.url_for(False) is None for p in content.packages)
    assert not [w for w in content.warnings if "payment_url" in w]  # усі без посилань — так задумано, без попереджень

    path = tmp_path / "content.yaml"
    import yaml

    path.write_text(yaml.safe_dump(raw_content, allow_unicode=True), encoding="utf-8")
    assert check_content.main([str(path)]) == 0
    assert "Режим: інформаційний" in capsys.readouterr().out


def test_mixed_payment_links_warn_about_missing_ones(raw_content):
    del raw_content["packages"][1]["payment_url"]
    content = parse_content(raw_content)
    assert content.package("optimal").payment_url is None
    assert any(w.startswith("packages (optimal).payment_url: посилання на оплату не вказано") for w in content.warnings)
