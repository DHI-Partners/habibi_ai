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
