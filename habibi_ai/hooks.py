app_name = "habibi_ai"
app_title = "Habibi AI"
app_publisher = "Habeebe"
app_description = "ИИ-модуль: раздел ИИ в интерфейсе habibi_ui поверх движка Directus"
app_email = "dosnet2200@gmail.com"
app_license = "mit"

# Интерфейс модуля — раздел в habibi_ui (/ui/ai). Своей страницы в Desk у него
# больше нет, поэтому без оболочки ставить его некуда.
required_apps = ["habibi_ui"]

# Плитка модуля на рабочем столе создаётся кодом: из фикстур приложения
# Frappe Desktop Icon не создаёт. Тем же хуком снимается пустой Workspace
# "Habibi AI" на сайтах, где он остался от старой версии модуля. Подробности —
# в habibi_ai/setup.py.
after_install = "habibi_ai.setup.after_install"
after_migrate = "habibi_ai.setup.after_migrate"

# Роль-переключатель трассировки приезжает фикстурой: без неё в api.py
# ссылка на несуществующую роль, и выдать право некому.
fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["Habibi AI Debug"]]]},
]
