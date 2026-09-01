// Страница чата. Ходит только в whitelisted-методы habibi_ai: адрес движка и
// токен живут на бенче, в браузер не попадают, tenant подставляет сервер.

frappe.pages["ai-chat"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("ИИ-чат"),
		single_column: true,
	});

	const chat = new AiChat(page);
	chat.load_bots();
};

class AiChat {
	constructor(page) {
		this.page = page;
		this.chat_id = null;
		this.render_shell();
	}

	render_shell() {
		this.page.main.html(`
			<div class="ai-chat" style="display:flex;flex-direction:column;height:70vh;max-width:820px">
				<div class="mb-3">
					<select class="form-control ai-chat-bot" style="max-width:320px"></select>
				</div>
				<div class="ai-chat-log flex-grow-1"
					style="overflow-y:auto;border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:12px;background:var(--card-bg)"></div>
				<div class="mt-3 d-flex" style="gap:8px">
					<input type="text" class="form-control ai-chat-input" placeholder="${__("Сообщение")}">
					<button class="btn btn-primary ai-chat-send">${__("Отправить")}</button>
				</div>
			</div>
		`);

		this.$log = this.page.main.find(".ai-chat-log");
		this.$input = this.page.main.find(".ai-chat-input");
		this.$bot = this.page.main.find(".ai-chat-bot");

		this.page.main.find(".ai-chat-send").on("click", () => this.send());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter") this.send();
		});
	}

	load_bots() {
		frappe.call({ method: "habibi_ai.api.list_bots" }).then((r) => {
			const bots = r.message || [];
			if (!bots.length) {
				this.$bot.append(`<option value="">${__("Ботов пока нет")}</option>`);
				return;
			}
			bots.forEach((b) => {
				this.$bot.append(`<option value="${b.id}">${frappe.utils.escape_html(b.name || b.id)}</option>`);
			});
		});
	}

	append(role, text) {
		const mine = role === "user";
		this.$log.append(`
			<div class="mb-2" style="text-align:${mine ? "right" : "left"}">
				<span style="display:inline-block;max-width:80%;padding:8px 12px;border-radius:12px;
					background:${mine ? "var(--bg-blue)" : "var(--bg-light-gray)"};white-space:pre-wrap">
					${frappe.utils.escape_html(text)}
				</span>
			</div>
		`);
		this.$log.scrollTop(this.$log[0].scrollHeight);
	}

	send() {
		const text = (this.$input.val() || "").trim();
		if (!text) return;

		const bot_id = this.$bot.val();
		if (!bot_id) {
			frappe.msgprint(__("Сначала заведите бота в админке движка"));
			return;
		}

		this.append("user", text);
		this.$input.val("");

		// Чат заводится через прокси, а не движком: tenant в customer_chats
		// обязателен, и подставить его может только серверная сторона.
		this.ensure_chat(bot_id)
			.then(() =>
				frappe.call({
					method: "habibi_ai.api.send_message",
					args: { chat_id: this.chat_id, message: text, bot_id: bot_id },
					freeze: true,
					freeze_message: __("Думает..."),
				})
			)
			.then((r) => {
				// Движок отдаёт текст в поле response (рядом с scenario_key и
				// scenario_stack), а не в reply или content.
				const answer = (r.message && r.message.response) || __("Пустой ответ");
				this.append("assistant", answer);
			});
	}

	ensure_chat(bot_id) {
		if (this.chat_id) return Promise.resolve();
		return frappe
			.call({ method: "habibi_ai.api.create_chat", args: { bot_id: bot_id } })
			.then((r) => {
				this.chat_id = r.message && r.message.id;
			});
	}
}
