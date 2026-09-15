# Locales

Words the application sends or shows, one folder per language, named by its code
(`src/commons/enums.py::BoardLanguage`).

- `notifications.toml` (Issue 66): what each patient message says, per channel. Each entry has a `version`,
  and the notification service stores every version it has ever used, so a message sent months ago can be
  reproduced exactly. **Changing the words of an entry means raising its `version`.**
  `tests/unit/notifications/test_template_registry.py` fails if a file's words change and its version does
  not.

Only English (`en`) is written so far. isiZulu (`zu`), isiXhosa (`xh`), Afrikaans (`af`) and Sesotho (`st`)
arrive with the translation framework (Issue 77), checked by a fluent speaker who is credited in the file.
A message in a language without an entry is sent in English.
