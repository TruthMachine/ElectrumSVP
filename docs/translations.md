# Interface translation update

This is a catalogue-only companion to the appearance, hardware and coinbase changes.
`data_in/locale/interface.pot` lists the 183 singular messages in scope. All 37
non-English catalogues include them; compiled files are checked for message coverage
and unchanged format placeholders. Unused entries are harmless before their feature lands.

Chinese additions were authored/reviewed during development. Other new translations
are machine/model drafts and still require native-language review, especially hardware
signing warnings. Automated checks establish coverage and formatting, not linguistic accuracy.
Existing translations outside this scope are retained, except a Korean `%2d minutes`
placeholder repair required by gettext format validation. The Norwegian locale alias and
Traditional Chinese language selector are corrected here, rather than copied across PRs.

Compile locally with `python contrib/compile_locales.py` (GNU gettext `msgfmt` required).
No translation service is contacted by the compiler. Tests: `test_interface_i18n.py`.
