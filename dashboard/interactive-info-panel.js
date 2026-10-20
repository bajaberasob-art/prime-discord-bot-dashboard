(() => {
  "use strict";
  const h = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
    }
    children.flat().filter(x => x != null).forEach(x => node.append(x instanceof Node ? x : document.createTextNode(String(x))));
    return node;
  };
  const defaults = () => ({
    enabled: false, channel_id: "", message_id: null, pin_message: false,
    title: "دليل السيرفر", description: "اختر أحد الأزرار بالأسفل لعرض المعلومات المطلوبة.",
    color: "#6366F1", banner_url: "", auto_delete_seconds: 300,
    buttons: [
      { id: crypto.randomUUID().replaceAll("-", "").slice(0, 12), label: "القوانين", emoji: "📜", style: "primary", response: "يرجى الالتزام بقوانين السيرفر واحترام جميع الأعضاء." },
      { id: crypto.randomUUID().replaceAll("-", "").slice(0, 12), label: "من نحن؟", emoji: "✦", style: "secondary", response: "أهلًا بك في مجتمعنا!" },
    ],
  });
  function mount(host, ctx) {
    let config = defaults(), channels = [], dirty = false, busy = false;
    const cleanup = [];
    const listen = (node, event, fn) => { node.addEventListener(event, fn); cleanup.push(() => node.removeEventListener(event, fn)); };
    const input = (label, key, value, options = {}) => {
      const control = options.textarea
        ? h("textarea", { class: "iip-input", rows: options.rows || 3, maxlength: options.maxlength || 4096, placeholder: options.placeholder || "" }, value || "")
        : h("input", { class: "iip-input", type: options.type || "text", value: value || "", maxlength: options.maxlength || 4096, placeholder: options.placeholder || "" });
      listen(control, "input", () => { config[key] = options.type === "checkbox" ? control.checked : control.value; dirty = true; });
      return h("label", { class: "iip-field" }, h("span", { text: label }), control);
    };
    const select = (label, key, value, opts) => {
      const control = h("select", { class: "iip-input" }, ...opts.map(o => h("option", { value: o.value, selected: String(o.value) === String(value), text: o.label })));
      listen(control, "change", () => { config[key] = control.value; dirty = true; });
      return h("label", { class: "iip-field" }, h("span", { text: label }), control);
    };
    const checkbox = (label, key, value) => {
      const control = h("input", { type: "checkbox", checked: !!value });
      listen(control, "change", () => { config[key] = control.checked; dirty = true; });
      return h("label", { class: "iip-check" }, control, h("span", { text: label }));
    };
    function render() {
      host.replaceChildren();
      const header = h("header", { class: "iip-header" },
        h("div", {}, h("span", { class: "iip-kicker", text: "PRIME / INTERACTIVE INFO" }),
          h("h1", { text: "لوحة الأزرار والردود المخفية" }),
          h("p", { text: "أنشئ رسالة ثابتة بأزرار مخصصة، واجعل رد كل زر خاصًا بصاحبه ويُحذف تلقائيًا." })),
        h("span", { class: "iip-status " + (config.enabled ? "is-on" : "is-off"), text: config.enabled ? "مفعّلة" : "متوقفة" }));
      const main = h("div", { class: "iip-layout" });
      const settings = h("section", { class: "iip-card" },
        h("div", { class: "iip-card-head" }, h("h2", { text: "إعدادات اللوحة" }), h("p", { text: "حدد القناة والمحتوى وطريقة النشر." })),
        checkbox("تفعيل اللوحة", "enabled", config.enabled),
        select("قناة النشر", "channel_id", config.channel_id, [{ value: "", label: "اختر قناة نصية" }, ...channels.map(c => ({ value: c.id, label: "#" + c.name }))]),
        checkbox("تثبيت الرسالة بعد النشر", "pin_message", config.pin_message),
        input("عنوان الرسالة", "title", config.title, { maxlength: 256 }),
        input("وصف الرسالة", "description", config.description, { textarea: true, rows: 4, maxlength: 4096 }),
        h("div", { class: "iip-two-col" }, input("لون الإيمبد", "color", config.color, { type: "color" }), input("رابط صورة البانر", "banner_url", config.banner_url, { placeholder: "https://..." })),
        h("label", { class: "iip-field" }, h("span", { text: "أو ارفع صورة من جهازك (PNG / JPG / WEBP / GIF، حتى 8MB)" }),
          (() => {
            const control = h("input", { class: "iip-input", type: "file", accept: "image/png,image/jpeg,image/webp,image/gif" });
            listen(control, "change", () => uploadBanner(control));
            return control;
          })()),
        config.banner_file ? h("p", { class: "iip-help", text: "تم اختيار صورة مرفوعة؛ ستُرفق برسالة Discord عند النشر." }) : null,
        select("مدة الرد الخاص قبل الحذف", "auto_delete_seconds", config.auto_delete_seconds, [
          ...Array.from({ length: 14 }, (_, i) => ({
            value: (i + 1) * 60,
            label: i === 0 ? "دقيقة واحدة" : (i + 1) + " دقائق" + (i === 4 ? " · الافتراضي" : ""),
          })),
        ]),
        h("p", { class: "iip-help", text: "مدة Discord محدودة بنافذة صلاحية التفاعل؛ نقيّدها من 5 ثوانٍ إلى 14 دقيقة في الخادم." }));
      const builder = h("section", { class: "iip-card iip-builder" },
        h("div", { class: "iip-card-head iip-row" }, h("div", {}, h("h2", { text: "باني الأزرار" }), h("p", { text: "حتى 25 زرًا، موزعة تلقائيًا على صفوف من خمسة." })),
          h("button", { type: "button", class: "iip-button secondary", text: "+ إضافة زر", disabled: config.buttons.length >= 25, onClick: () => {
            config.buttons.push({ id: crypto.randomUUID().replaceAll("-", "").slice(0, 12), label: "زر جديد", emoji: "", style: "primary", response: "" }); dirty = true; render();
          } })),
        ...config.buttons.map((button, index) => {
          const card = h("div", { class: "iip-button-editor" });
          const field = (label, key, opts = {}) => {
            const control = opts.textarea
              ? h("textarea", { class: "iip-input", rows: 4, maxlength: 2000, placeholder: "النص الذي سيظهر لصاحب الضغط فقط..." }, button[key] || "")
              : h("input", { class: "iip-input", type: "text", maxlength: opts.maxlength || 100, value: button[key] || "" });
            listen(control, "input", () => { button[key] = control.value; dirty = true; });
            return h("label", { class: "iip-field" }, h("span", { text: label }), control);
          };
          const style = h("select", { class: "iip-input" },
            ...[["primary","Primary · أزرق"],["secondary","Secondary · رمادي"],["success","Success · أخضر"],["danger","Danger · أحمر"]].map(([value,label]) => h("option", { value, selected: button.style === value, text: label })));
          listen(style, "change", () => { button.style = style.value; dirty = true; });
          const move = (delta) => { const next = index + delta; if (next < 0 || next >= config.buttons.length) return; [config.buttons[index], config.buttons[next]] = [config.buttons[next], config.buttons[index]]; dirty = true; render(); };
          card.append(h("div", { class: "iip-row" }, h("strong", { text: "زر " + (index + 1) }), h("div", { class: "iip-actions" },
            h("button", { type: "button", class: "iip-icon-btn", text: "↑", title: "تحريك للأعلى", disabled: index === 0, onClick: () => move(-1) }),
            h("button", { type: "button", class: "iip-icon-btn", text: "↓", title: "تحريك للأسفل", disabled: index === config.buttons.length - 1, onClick: () => move(1) }),
            h("button", { type: "button", class: "iip-icon-btn danger", text: "حذف", onClick: () => { config.buttons.splice(index, 1); dirty = true; render(); } }))),
            h("div", { class: "iip-two-col" }, field("اسم الزر", "label", { maxlength: 80 }), field("Emoji (اختياري)", "emoji")),
            h("label", { class: "iip-field" }, h("span", { text: "لون الزر" }), style),
            field("الرد الخاص (يدعم Markdown ومنشن القنوات والرتب)", "response", { textarea: true }));
          return card;
        }));
      const preview = h("section", { class: "iip-card iip-preview" },
        h("div", { class: "iip-card-head" }, h("h2", { text: "معاينة الرسالة" }), h("p", { text: "معاينة تقريبية قبل النشر." })),
        h("div", { class: "iip-discord-embed", style: "border-right:4px solid " + (config.color || "#6366F1") },
          h("strong", { text: config.title || "عنوان الرسالة" }),
          h("p", { text: config.description || "وصف الرسالة" }),
          config.banner_url ? h("img", { class: "iip-banner", src: config.banner_url, alt: "Banner preview", onError: e => { e.currentTarget.hidden = true; } }) : null,
          h("div", { class: "iip-preview-buttons" }, ...config.buttons.map(b => h("span", { class: "iip-preview-button " + (b.style || "primary"), text: (b.emoji ? b.emoji + " " : "") + (b.label || "زر") })))));
      const actions = h("div", { class: "iip-save-row" },
        h("span", { class: "iip-help", text: config.message_id ? "معرّف الرسالة: " + config.message_id : "لم تُنشر رسالة بعد." }),
        h("div", { class: "iip-actions" },
          h("button", { type: "button", class: "iip-button secondary", text: "حفظ المسودة", disabled: busy, onClick: () => save(false) }),
          h("button", { type: "button", class: "iip-button primary", text: config.message_id ? "حفظ وتحديث الرسالة" : "حفظ ونشر في الروم", disabled: busy, onClick: () => save(true) })));
      main.append(settings, h("div", { class: "iip-right-column" }, builder, preview));
      host.append(header, main, actions);
    }
    async function uploadBanner(control) {
      const file = control.files && control.files[0];
      if (!file) return;
      if (file.size > 8 * 1024 * 1024) { control.value = ""; return ctx.toast("حجم الصورة يجب ألا يتجاوز 8 ميغابايت", "warn"); }
      const allowed = ["image/png", "image/jpeg", "image/webp", "image/gif"];
      if (!allowed.includes(file.type)) { control.value = ""; return ctx.toast("الصيغ المدعومة PNG وJPG وWEBP وGIF فقط", "warn"); }
      busy = true;
      try {
        let response;
        const send = async () => {
          const form = new FormData();
          form.append("file", file, file.name);
          return ctx.api("api/guild/" + encodeURIComponent(ctx.guildId) + "/interactive-info-panel/banner", {
            method: "POST", headers: { "X-CSRF-Token": ctx.getCsrf ? ctx.getCsrf() : "" }, body: form,
          });
        };
        response = await send();
        if (response.status === 403) {
          const errorData = await ctx.readJson(response.clone(), {});
          if (errorData.error === "csrf" && ctx.refreshSession && await ctx.refreshSession()) response = await send();
        }
        const data = await ctx.readJson(response, {});
        if (!response.ok) return ctx.toast(data.message || "تعذر رفع الصورة", "warn");
        config.banner_file = data.banner_file;
        config.banner_url = data.banner_url;
        dirty = true;
        ctx.toast("تم رفع الصورة؛ احفظ وانشر اللوحة لتظهر في Discord", "success");
        render();
      } catch (error) {
        if (error.message !== "unauth") ctx.toast("تعذر رفع الصورة", "warn");
      } finally { busy = false; control.value = ""; }
    }
    async function save(publish) {
      if (busy) return;
      if (config.enabled && !config.channel_id) return ctx.toast("اختر قناة النشر أولًا", "warn");
      if (!config.buttons.length) return ctx.toast("أضف زرًا واحدًا على الأقل", "warn");
      busy = true;
      const buttons = config.buttons.map(b => ({ ...b }));
      try {
        const response = await ctx.writeApi("api/guild/" + encodeURIComponent(ctx.guildId) + "/interactive-info-panel", { ...config, buttons, publish });
        const data = await ctx.readJson(response, {});
        if (!response.ok) return ctx.toast(data.message || "تعذر حفظ اللوحة", "warn");
        config = { ...config, ...(data.config || {}) }; dirty = false;
        ctx.toast(publish ? "تم نشر/تحديث اللوحة في Discord" : "تم حفظ مسودة اللوحة", "success");
        render();
      } catch (error) {
        if (error.message !== "unauth") ctx.toast("تعذر الاتصال لحفظ اللوحة", "warn");
      } finally { busy = false; }
    }
    async function load() {
      host.replaceChildren(h("div", { class: "iip-card", text: "جارٍ تحميل إعدادات اللوحة..." }));
      try {
        const response = await ctx.api("api/guild/" + encodeURIComponent(ctx.guildId) + "/interactive-info-panel");
        const data = await ctx.readJson(response, {});
        if (!response.ok) throw Error(data.message || "تعذر تحميل الإعدادات");
        channels = data.channels || [];
        config = { ...defaults(), ...(data.config || {}) };
        if (!Array.isArray(config.buttons)) config.buttons = [];
        render();
      } catch (error) {
        host.replaceChildren(h("div", { class: "iip-card", text: error.message === "unauth" ? "انتهت الجلسة." : "تعذر تحميل اللوحة. تأكد من صلاحيات الإدارة واتصال البوت." }));
      }
    }
    load();
    return () => cleanup.forEach(fn => fn());
  }
  window.PrimeInteractiveInfoPanel = { mount };
})();
