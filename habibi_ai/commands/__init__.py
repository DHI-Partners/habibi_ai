"""Команды bench: `bench --site <site> habibi-ai <команда>`."""

import click
import frappe
from frappe.commands import get_site, pass_context


@click.group("habibi-ai")
def habibi_ai():
	"""Habibi AI"""


@click.command("apply-preset")
@click.argument("name")
@pass_context
def apply_preset(context, name):
	"""Применить пресет вертикали (например, food)"""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()
	try:
		from habibi_ai.presets import apply

		summary = apply(name)
		frappe.db.commit()
		click.echo(f"Пресет {name}: {summary}")
	finally:
		frappe.destroy()


habibi_ai.add_command(apply_preset)
commands = [habibi_ai]
