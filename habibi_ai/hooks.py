app_name = "habibi_ai"
app_title = "Habibi AI"
app_publisher = "Habeebe"
app_description = "ИИ-модуль: чат в desk ERPNext поверх движка Directus"
app_email = "dosnet2200@gmail.com"
app_license = "mit"

# Плитка модуля на рабочем столе создаётся кодом: из фикстур приложения
# Frappe Desktop Icon не создаёт. Подробности — в habibi_ai/setup.py.
after_install = "habibi_ai.setup.after_install"
after_migrate = "habibi_ai.setup.after_migrate"
