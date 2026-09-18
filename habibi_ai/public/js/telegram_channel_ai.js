// Выбор ИИ-бота на канале Telegram. Боты живут в движке (Directus), а не во
// Frappe, поэтому поле — Autocomplete со списком, который отдаёт habibi_ai:
// свои боты тенанта плюс общие.
["Telegram Bot", "Telegram Account"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			load_ai_bots(frm);
		},
		ai_enabled(frm) {
			load_ai_bots(frm);
		},
	});
});

function load_ai_bots(frm) {
	if (!frm.doc.ai_enabled || frm.__ai_bots_loaded) return;
	frappe.call("habibi_ai.api.list_bots").then((r) => {
		const options = (r.message || []).map((bot) => ({
			value: String(bot.id),
			label: `${bot.name} (#${bot.id})`,
		}));
		frm.set_df_property("ai_bot", "options", options);
		frm.__ai_bots_loaded = true;
	});
}
