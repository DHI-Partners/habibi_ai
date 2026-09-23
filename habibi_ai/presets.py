"""Применение пресета вертикали к сайту: разделы, правила, флаги, шаблоны, права.

Идемпотентно — см. preset_rules. Поля пресета, которых нет на сайте,
вычищаются здесь, а не в кабинете: иначе раздел целиком выпадал бы из-за
одного своего поля, заведённого руками только на проде.
"""

import json
import os

import frappe
from frappe.model import default_fields
from frappe.permissions import add_permission, update_permission_property
from habibi_ui.cabinet.fields import parse_fields

from habibi_ai import preset_rules

# "doctype" в default_fields — не пользовательское поле раздела, его в
# list_fields/base_filters быть не должно; остальные (name, docstatus и т.д.)
# в мете DocField не перечислены, но существуют всегда — как в _describe
# кабинета (habibi_ui.api.v1.cabinet).
_KNOWN_FIELDS = set(default_fields) - {"doctype"}


def _load(name):
	path = os.path.join(frappe.get_app_path("habibi_ai"), "presets", f"{name}.json")
	with open(path) as f:
		return json.load(f)


def _keep_existing(lines, meta):
	"""Оставить только поля, которые правда есть в мете DocType.

	`docstatus` из раздела «Заказы» иначе пропал бы на сайте без своего
	воркфлоу: у него нет DocField в мете, но поле существует у любого
	документа.
	"""
	return "\n".join(
		("@" if s.adapter else "") + s.fieldname + (f":{s.label}" if s.label else "")
		for s in parse_fields(lines)
		if s.adapter or s.fieldname in _KNOWN_FIELDS or meta.get_field(s.fieldname)
	)


def _fit(section, skipped):
	if section["kind"] != "generic":
		return section
	if not frappe.db.exists("DocType", section["ref_doctype"]):
		skipped.append(section["key"])
		return None
	meta = frappe.get_meta(section["ref_doctype"])
	fitted = {
		**section,
		"list_fields": _keep_existing(section.get("list_fields"), meta),
		"form_fields": _keep_existing(section.get("form_fields"), meta),
	}
	if section.get("base_filters"):
		base = json.loads(section["base_filters"])
		fitted["base_filters"] = (
			json.dumps({k: v for k, v in base.items() if k in _KNOWN_FIELDS or meta.get_field(k)})
			if isinstance(base, dict)
			else section["base_filters"]
		)
	if not fitted["list_fields"]:
		skipped.append(section["key"])
		return None
	return fitted


def apply(name):
	preset = _load(name)
	skipped = []

	cabinet = frappe.get_single("Cabinet Settings")
	current = [r.as_dict(no_default_fields=True) for r in cabinet.sections]
	fitted = [s for s in (_fit(s, skipped) for s in preset["sections"]) if s]
	cabinet.sections = []
	for s in preset_rules.merge_sections(current, fitted):
		cabinet.append("sections", s)
	cabinet.save()

	profile = frappe.get_single("Business Profile")
	rules = preset_rules.merge_rules([r.as_dict(no_default_fields=True) for r in profile.rules], preset["rules"])
	profile.rules = []
	for r in rules:
		profile.append("rules", r)
	profile.save()

	settings = frappe.get_single("Habibi AI Settings")
	settings.update({**preset["features"], **preset["workflow"]})
	settings.save()

	created = 0
	for key, text in preset["templates"].items():
		if not frappe.db.exists("Telegram Message Template", key):
			frappe.get_doc({"doctype": "Telegram Message Template", "template_name": key, "default_template": text}).insert()
			created += 1

	for role, doctypes in preset["permissions"].items():
		for doctype, rights in doctypes.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			add_permission(doctype, role, 0)
			for right in rights:
				update_permission_property(doctype, role, 0, right, 1)

	return {"sections": len(fitted), "rules": len(rules), "templates_created": created, "skipped": skipped}
