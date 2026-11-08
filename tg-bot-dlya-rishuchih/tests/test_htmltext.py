import pytest

from bot.htmltext import esc, fill, has_tags, placeholders, prepare_html, strip_tags


def test_supported_markup_is_kept():
    text = '<b>Жирний</b> <i>курсив</i> <a href="https://x.com/a?b=1">посилання</a> <tg-spoiler>s</tg-spoiler>'
    checked = prepare_html(text)
    assert checked.errors == []
    assert checked.text == text


def test_stray_characters_are_escaped():
    checked = prepare_html("Ціна <5000 грн> & бонус &amp; <бонус> -> далі")
    assert checked.errors == []
    assert checked.text == "Ціна &lt;5000 грн&gt; &amp; бонус &amp; &lt;бонус&gt; -&gt; далі"


def test_ampersand_inside_link_is_escaped():
    checked = prepare_html('<a href="https://pay.ua/?a=1&b=2">оплатити</a>')
    assert checked.errors == []
    assert checked.text == '<a href="https://pay.ua/?a=1&amp;b=2">оплатити</a>'


def test_numeric_entities_are_kept():
    assert prepare_html("&#128293; &#x1F525;").text == "&#128293; &#x1F525;"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("рядок<br>рядок", "<br> не підтримується"),
        ("<p>абзац</p>", "<p> не підтримується"),
        ("<b>жирний", "не закрито"),
        ("жирний</b>", "зайвий закривальний"),
        ("<b><i>текст</b></i>", "тег <i> не закрито"),
        ("<a>без посилання</a>", "потрібне посилання"),
        ("<span>x</span>", "tg-spoiler"),
        ("<b/>", "порожнім"),
        ("<Bonus>", "<bonus> не підтримується"),
    ],
)
def test_markup_errors(text, fragment):
    errors = prepare_html(text).errors
    assert errors, text
    assert any(fragment in e for e in errors), errors


def test_placeholders_and_fill():
    template = "Привіт, {first_name}! Залишилось {time_left}. {невідома} {unknown} {{дужки}}"
    assert placeholders(template) == {"first_name", "time_left", "unknown"}
    result = fill(template, {"first_name": "Оля", "time_left": "3 год"})
    assert result == "Привіт, Оля! Залишилось 3 год. {невідома} {unknown} {{дужки}}"


def test_fill_does_not_expand_values_twice():
    assert fill("{a} {b}", {"a": "{b}", "b": "x"}) == "{b} x"


def test_esc_strip_and_has_tags():
    assert esc('<Іван & "Ко">') == "&lt;Іван &amp; &quot;Ко&quot;&gt;"
    assert strip_tags("<b>Ціна</b> &lt;5000&gt;") == "Ціна <5000>"
    assert has_tags("<b>VIP</b>")
    assert not has_tags("VIP <3")
    assert not has_tags("Старт")
