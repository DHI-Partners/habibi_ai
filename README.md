# habibi_ai

ИИ-модуль habibi: страница чата в desk ERPNext поверх движка Directus.

Ставится тенанту выборочно, как `habibi_ui`, через `saas_bridge` →
`/app/site-manager` → Site apps. Движок живёт отдельно
(`DHI-Partners/habibi_ai_engine`), один на инсталляцию.

## Как ходят запросы

    браузер тенанта -> сессия Frappe -> whitelisted-метод habibi_ai
                    -> http://ai-engine:8055 (внутренняя сеть)

Браузер в Directus не ходит. Токен движка лежит в `common_site_config.json`
на бенче — файле, до которого System Manager тенанта не дотягивается. Тот же
приём, которым `saas_bridge` хранит лимит мест.

## Изоляция тенантов

`tenant` берётся из `frappe.local.site` и никогда из параметров запроса.
Фильтр строится в одном месте — `engine.scoped_filter`. Чужой чат
неотличим от несуществующего: иначе перебором номеров можно было бы
выяснить, какие чаты есть у соседей.

## Настройка

```bash
bench --site <сайт> set-config -g habibi_ai_engine_url http://ai-engine:8055
bench --site <сайт> set-config -g habibi_ai_engine_token '<токен сервисной роли>'
```

## Тесты

`engine.py` не импортирует frappe, поэтому проверки изоляции гоняются без
поднятия сайта:

```bash
python -m unittest habibi_ai.tests.test_engine -v
```

## Грабли

**`bench execute` не вызывает методы сторонних приложений.** В Frappe 16
команда выполняет `eval(code, globals(), locals())` в namespace модуля
`frappe/commands/utils.py`, где импортирован только `frappe`. Поэтому
`bench execute frappe.utils.now` работает, а `bench execute
habibi_ai.api.list_bots` падает с `NameError: name 'habibi_ai' is not
defined`. Обходится импортом прямо в выражении:

```bash
bench --site <сайт> execute '__import__("habibi_ai.api", fromlist=["x"]).list_bots'
```

**Права Directus кешируются.** После выдачи разрешений сервисной политике
токен продолжает получать 403, пока движок не перезапустят:
`docker compose restart ai-engine`.

**После установки модуля нужен `bench --site <сайт> clear-cache`.** Frappe
держит разрешённые хуки в redis, и сайт, закешировавший их до установки,
работает так, будто модуля нет.
