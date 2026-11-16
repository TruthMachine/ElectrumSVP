"""Every shipped locale must render the new hardware prompts without format errors."""
import ast
from collections import Counter
import gettext
from pathlib import Path
import string

import pytest


ROOT = Path(__file__).resolve().parents[2]
CATALOGUES = sorted((ROOT / 'electrumsv/data/locale').glob('*/LC_MESSAGES/electrumsv.mo'))


def coinbase_messages():
    # The scoped template has singular msgids only. Avoid adding a PO parser dependency
    # to the wallet or to the normal test environment.
    messages, current = [], None
    for line in (ROOT / 'data_in/locale/coinbase.pot').read_text().splitlines():
        if line.startswith('msgid '):
            current = ast.literal_eval(line[6:])
        elif line.startswith('"') and current is not None:
            current += ast.literal_eval(line)
        elif line.startswith('msgstr '):
            if current:
                messages.append(current)
            current = None
    assert messages
    return messages


def format_fields(text):
    fields, positional = [], 0
    for _, name, spec, conversion in string.Formatter().parse(text):
        if name is not None:
            if name == '':
                name = str(positional)
                positional += 1
            fields.append((name, spec, conversion))
    return Counter(fields)


@pytest.mark.parametrize('path', CATALOGUES, ids=lambda p: p.parts[-3])
def test_coinbase_messages_are_compiled_and_preserve_format_fields(path):
    with path.open('rb') as stream:
        translation = gettext.GNUTranslations(stream)
    for message in coinbase_messages():
        # A valid translation can equal English, so check catalogue membership too.
        assert message in translation._catalog, (path, message)
        translated = translation.gettext(message)
        assert translated.strip(), (path, message)
        assert format_fields(translated) == format_fields(message), (path, message)


def test_language_selector_resolves_all_non_english_catalogues():
    from electrumsv import i18n
    original = i18n.language
    try:
        for locale in i18n.languages:
            if locale in ('', 'en_UK'):
                continue
            i18n.set_language(locale)
            assert isinstance(i18n.language, gettext.GNUTranslations), locale
            assert 'Mining reward is not verified on the selected chain' in i18n.language._catalog, locale
    finally:
        i18n.language = original
