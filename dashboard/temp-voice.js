/* Existing PRIME dashboard — persistent, authenticated temporary-voice editor. */
(function () {
  "use strict";
  var BUTTON_LABELS = {
    rename: "تغيير الاسم", limit: "حد الأعضاء", privacy: "إخفاء/إظهار", waiting: "غرفة الانتظار",
    kick: "طرد", invite: "دعوة", trust: "ثقة", untrust: "سحب الثقة", status: "حالة الروم",
    lock: "قفل", unlock: "فتح", ban: "حظر", unban: "فك الحظر", region: "المنطقة",
    mute: "كتم عضو", deafen: "إصمام عضو", color: "لون الحاوية", meeting: "وضع الاجتماع",
    claim: "أخذ الملكية", transfer: "نقل الملكية", delete: "حذف الروم", pin: "تثبيت الروم",
    activity: "نشاط", quick_lock: "قفل سريع", quick_unlock: "فتح سريع", age: "تقييد عمري",
    emergency: "قفل الطوارئ", slowmode: "الوضع البطيء", report: "إبلاغ الإدارة",
  };
  var BUTTON_EMOJIS = {
    rename: "📝", limit: "👥", privacy: "👁️", waiting: "⌛", kick: "🚪",
    invite: "📨", trust: "🤝", untrust: "🚫", status: "ℹ️", lock: "🔒",
    unlock: "🔓", ban: "⛔", unban: "✅", region: "🌐", mute: "🔇",
    deafen: "🎧", color: "🎨", meeting: "🎙️", claim: "👑", transfer: "🔄",
    delete: "🗑️", pin: "📌", activity: "🎮", quick_lock: "🛡️",
    quick_unlock: "🔑", age: "🔞", emergency: "🚨", slowmode: "🐢", report: "🚩",
  };
  var FLAGS = [
    ["permanent_memory", "الذاكرة الدائمة", "حفظ اسم المالك وحد الأعضاء والموثوقين والمحظورين لاستعادتها."],
    ["in_room_interface", "الواجهة داخل الروم", "إرسال أدوات التحكم داخل قناة الروم بعد إنشائه."],
    ["ownership_claim", "أخذ الملكية", "السماح بطلب الملكية عند مغادرة المالك."],
    ["owner_embed_color", "لون الحاوية للمالك", "السماح لمالك الروم بتغيير لون لوحة الروم."],
    ["waiting_room", "غرفة الانتظار", "إنشاء قناة انتظار مرتبطة بالروم وحظر الدخول المباشر."],
    ["meeting_mode", "وضع الاجتماع", "حجب صوت الجميع عدا مالك الروم."],
    ["owner_manage_channel", "إدارة القناة للمالك", "منح المالك صلاحية إدارة قناته المؤقتة فقط."],
    ["voice_analytics", "الإحصائيات الصوتية", "احتساب دقائق الروم عند وجود أعضاء حقيقيين."],
    ["admin_protection", "حماية الأدمن", "منع مالك الروم من طرد أو كتم أعضاء الأدمن."],
  ];
  var COLORS = ["#8b5cf6", "#a855f7", "#6366f1", "#06b6d4", "#ec4899", "#f59e0b", "#10b981"];
  var MAX_BANNER_BYTES = 2 * 1024 * 1024;
  var MAX_BANNER_DIMENSION = 4096;
  var BANNER_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
  var sequence = 0;
  function node(tag, attrs) {
    var item = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value == null || value === false) return;
      if (key === "text") item.textContent = value;
      else if (key.slice(0, 2) === "on") item.addEventListener(key.slice(2), value);
      else if (key === "checked" || key === "disabled" || key === "selected") item[key] = !!value;
      else item.setAttribute(key, value === true ? "" : String(value));
    });
    for (var i = 2; i < arguments.length; i++) {
      if (arguments[i] != null) item.append(arguments[i]);
    }
    return item;
  }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function decodeImageFile(file) {
    if (window.createImageBitmap) {
      return window.createImageBitmap(file).then(function (bitmap) {
        return { image: bitmap, width: bitmap.width, height: bitmap.height,
          release: function () { bitmap.close(); } };
      }).catch(function () { return decodeImageElement(file); });
    }
    return decodeImageElement(file);
  }
  function decodeImageElement(file) {
    return new Promise(function (resolve, reject) {
      var objectUrl = URL.createObjectURL(file), image = new Image();
      image.onload = function () {
        resolve({ image: image, width: image.naturalWidth, height: image.naturalHeight,
          release: function () { URL.revokeObjectURL(objectUrl); } });
      };
      image.onerror = function () {
        URL.revokeObjectURL(objectUrl);
        reject(Error("تعذر قراءة الصورة. جرّب PNG أو JPG أو WebP."));
      };
      image.src = objectUrl;
    });
  }
  function canvasBlob(canvas, type, quality) {
    return new Promise(function (resolve) { canvas.toBlob(resolve, type, quality); });
  }
  async function prepareBanner(file) {
    if (file.type && !file.type.startsWith("image/")) {
      throw Error("اختر ملف صورة.");
    }
    if (file.size > 30 * 1024 * 1024) {
      throw Error("الصورة أكبر من 30 ميغابايت؛ اختر نسخة أصغر.");
    }
    var decoded;
    try {
      decoded = await decodeImageFile(file);
    } catch (_) {
      throw Error("تعذر قراءة الصورة على هذا الجهاز. جرّب حفظها بصيغة PNG أو JPG.");
    }
    try {
      var width = decoded.width, height = decoded.height;
      if (!width || !height) throw Error("أبعاد الصورة غير صالحة.");
      if (BANNER_TYPES.includes(file.type) && file.size <= MAX_BANNER_BYTES
          && width <= MAX_BANNER_DIMENSION && height <= MAX_BANNER_DIMENSION) {
        return file;
      }

      var scale = Math.min(1, MAX_BANNER_DIMENSION / width, MAX_BANNER_DIMENSION / height);
      var canvas = document.createElement("canvas");
      var context = canvas.getContext("2d");
      if (!context) throw Error("تعذر تجهيز الصورة في هذا المتصفح.");
      var targetType = file.type === "image/jpeg" ? "image/jpeg" : "image/webp";
      var qualities = [0.9, 0.82, 0.74, 0.66];
      for (var attempt = 0; attempt < 7; attempt++) {
        canvas.width = Math.max(1, Math.floor(width * scale));
        canvas.height = Math.max(1, Math.floor(height * scale));
        context.drawImage(decoded.image, 0, 0, canvas.width, canvas.height);
        for (var i = 0; i < qualities.length; i++) {
          var blob = await canvasBlob(canvas, targetType, qualities[i]);
          if (!blob || !BANNER_TYPES.includes(blob.type) || blob.size > MAX_BANNER_BYTES) continue;
          var ext = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp" }[blob.type];
          var baseName = (file.name || "prime-voice-banner").replace(/\.[^.]+$/, "");
          return new File([blob], baseName + "." + ext, { type: blob.type, lastModified: file.lastModified });
        }
        scale *= 0.85;
      }
      throw Error("تعذر ضغط الصورة إلى الحجم المطلوب. اختر صورة أصغر أو أقل دقة.");
    } finally {
      if (decoded.release) decoded.release();
    }
  }
  function mount(host, options) {
    var guildId = options.guildId, url = "api/temp-voice/" + encodeURIComponent(guildId);
    var root = node("section", { class: "temp-voice", dir: "rtl", "aria-label": "إعدادات الرومات المؤقتة" });
    var liveTimer = null, disposed = false, loading = true, writing = false, uploading = false;
    var snap = null, cfg = null, baseRevision = 0, dirty = false, issue = "", notice = "", conflict = false;
    var conflictConfig = null, remoteRevision = null;
    var draftBanner = null, localUrl = "", modal = null;
    host.replaceChildren(root);
    function freshConfig(source) {
      var value = clone(source || {});
      delete value.panel_message_id;
      delete value.panel_layout_version;
      return value;
    }
    function channelLists() { return (snap && snap.channels) || { categories: [], text: [], voice: [] }; }
    function markDirty(message) {
      dirty = true;
      issue = "";
      notice = "";
      updateButtons();
      var badge = root.querySelector("[data-dirty]");
      if (badge) badge.textContent = dirty ? "مسودة غير محفوظة" : "الإعدادات محفوظة";
      if (message) status.textContent = message;
    }
    var status = node("div", { class: "tv-alert", role: "status", "aria-live": "polite" });
    function updateButtons() {
      var save = root.querySelector("[data-save]");
      var setup = root.querySelector("[data-setup]");
      var uploads = root.querySelectorAll("[data-upload]");
      if (save) save.disabled = loading || writing || uploading || !snap || !dirty;
      if (setup) setup.disabled = loading || writing || uploading || !snap || dirty;
      uploads.forEach(function (upload) { upload.disabled = writing || uploading || loading; });
      root.querySelectorAll("[data-delete-room]").forEach(function (button) {
        button.disabled = writing;
      });
    }
    async function request(path, init, retry) {
      var response = await options.api(path, Object.assign({ cache: "no-store" }, init || {}));
      if (response.status === 403 && retry !== false) {
        var body;
        try { body = await response.clone().json(); } catch (_) {}
        if ((!body || body.error === "csrf") && await options.refreshSession()) {
          init = init || {};
          init.headers = Object.assign({}, init.headers || {}, { "X-CSRF-Token": options.getCsrf() });
          response = await options.api(path, init);
        }
      }
      return response;
    }
    async function readResponse(response) {
      var data;
      try { data = await response.json(); } catch (_) { data = {}; }
      if (response.status === 401) throw Error("انتهت الجلسة. أعد تحميل لوحة التحكم.");
      if (!response.ok) throw Error(data.message || (response.status === 409 ? "تغيّرت الإعدادات في جلسة أخرى." : "تعذر تنفيذ الطلب."));
      return data;
    }
    function accept(data, resetDraft) {
      var changed = snap && snap.revision !== data.revision;
      snap = data;
      if (resetDraft) {
        cfg = freshConfig(data.config);
        baseRevision = data.revision;
        dirty = false;
        conflict = false;
        conflictConfig = null;
        remoteRevision = null;
        draftBanner = data.banner || null;
      } else if (changed && dirty) {
        conflict = true;
        conflictConfig = data.config;
        remoteRevision = data.revision;
      }
    }
    function loadServerConfig() {
      cfg = freshConfig(conflictConfig || snap.config);
      baseRevision = remoteRevision == null ? snap.revision : remoteRevision;
      dirty = false;
      conflict = false;
      conflictConfig = null;
      remoteRevision = null;
      releaseUrl();
      draftBanner = snap.banner || null;
      issue = "";
      notice = "تم تحميل إعدادات الخادم. أعد تطبيق تغييراتك إن لزم.";
      render();
    }
    function overwriteServerConfig() {
      if (!dirty || !conflict || !Number.isInteger(remoteRevision) || !conflictConfig) return;
      if (!window.confirm("سيتم استبدال إعدادات الخادم بالمسودة الحالية. هل تريد المتابعة؟")) return;
      baseRevision = remoteRevision;
      conflict = false;
      conflictConfig = null;
      remoteRevision = null;
      status.className = "tv-alert";
      status.textContent = "جارٍ حفظ المسودة فوق أحدث إعدادات الخادم…";
      save();
    }
    function showConflict() {
      if (!conflict) return;
      status.className = "tv-alert is-warn";
      status.replaceChildren(document.createTextNode(
        "تغيّرت الإعدادات من مكان آخر. بقيت مسودتك محفوظة؛ اختر تحميل إعدادات الخادم أو تأكيد استبدالها."
      ));
      var actions = node("div", { class: "tv-actions", "data-conflict-actions": "" },
        node("button", { type: "button", class: "tv-action", text: "تحميل إعدادات الخادم",
          onclick: loadServerConfig }));
      if (dirty && Number.isInteger(remoteRevision) && conflictConfig) {
        actions.append(node("button", { type: "button", class: "tv-action is-primary",
          text: "تأكيد الكتابة فوق إعدادات الخادم", onclick: overwriteServerConfig }));
      }
      status.append(actions);
    }
    async function fetchState(initial) {
      if (disposed || writing || uploading) return;
      try {
        var result = await readResponse(await request(url, {}, false));
        var externalRevision = snap && snap.revision !== result.revision;
        accept(result, initial || (!dirty && externalRevision));
        issue = "";
        live();
        if (conflict) {
          showConflict();
        }
      } catch (error) {
        if (!snap) issue = error.message;
        else status.textContent = "تعذر تحديث النشاط الحي: " + error.message;
      }
      loading = false;
      if (!snap) render();
      else updateButtons();
    }
    async function save() {
      if (!dirty || writing || uploading || !snap) return;
      writing = true; issue = ""; updateButtons();
      var body = clone(cfg);
      body.revision = baseRevision;
      delete body.panel_message_id;
      delete body.panel_layout_version;
      try {
        var response = await request(url, {
          method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRF-Token": options.getCsrf() },
          body: JSON.stringify(body),
        });
        if (response.status === 409) {
          var current = await response.json().catch(function () { return {}; });
          conflict = true;
          conflictConfig = current.config || null;
          remoteRevision = current.revision;
          showConflict();
        } else {
          var result = await readResponse(response);
          accept(result, true);
          notice = "تم حفظ الإعدادات. اطلب تجهيز النظام أو إعادة نشر البانل عند تغيير القنوات.";
          status.className = "tv-alert is-ok";
          status.textContent = notice;
          options.toast("تم حفظ إعدادات الرومات المؤقتة", "success");
          render();
        }
      } catch (error) {
        issue = error.message;
        status.className = "tv-alert is-warn";
        status.textContent = issue;
      }
      writing = false; updateButtons();
    }
    async function setup(republish) {
      if (dirty) { status.textContent = "احفظ المسودة أولاً ثم جهّز النظام أو أعد نشر البانل."; return; }
      writing = true; updateButtons();
      try {
        var response = await request(url + "/setup", {
          method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": options.getCsrf() },
          body: JSON.stringify({ revision: baseRevision, republish: !!republish }),
        });
        var result = await readResponse(response);
        accept(result, true);
        status.className = "tv-alert is-ok";
        status.textContent = republish ? "أُعيد نشر لوحة التحكم." : "تم إنشاء النظام ونشر لوحة التحكم.";
        render();
      } catch (error) { status.className = "tv-alert is-warn"; status.textContent = error.message; }
      writing = false; updateButtons();
    }
    function set(key, value) { cfg[key] = value; markDirty(); }
    function section(title, note) {
      var panel = node("section", { class: "tv-card" }, node("h2", { text: title }));
      if (note) panel.append(node("p", { class: "tv-help", text: note }));
      return panel;
    }
    function field(label, value, type, key, attrs) {
      var settings = Object.assign({ type: type || "text", value: value == null ? "" : value,
        oninput: function (event) {
          var val = type === "number" ? Number(event.target.value) : event.target.value;
          set(key, val);
          var preview = root.querySelector("[data-preview]");
          if (preview && key === "panel_title") {
            var target = preview.querySelector("strong");
            if (target) target.textContent = event.target.value;
          }
        } }, attrs || {});
      var input = node(settings.multiline ? "textarea" : "input", settings);
      if (settings.multiline) input.value = value == null ? "" : value;
      return node("label", { class: "tv-field" }, node("span", { text: label }), input);
    }
    function selectField(label, optionsList, current, onpick, placeholder) {
      var select = node("select", { oninput: function (event) { onpick(event.target.value); } });
      select.append(node("option", { value: "", text: placeholder || "— اختر —", selected: !current }));
      optionsList.forEach(function (option) {
        var item = node("option", { value: option.id, text: option.name, selected: String(option.id) === String(current) });
        select.append(item);
      });
      return node("label", { class: "tv-field" }, node("span", { text: label }), select);
    }
    function flagField(parent, key, label, description) {
      var input = node("input", { type: "checkbox", checked: !!cfg[key], oninput: function (event) {
        set(key, event.target.checked);
      } });
      parent.append(node("label", { class: "tv-flag" }, input,
        node("span", {}, node("b", { text: label }), node("small", { text: description }))));
    }
    function rolePicker(parent, key, label, hint) {
      var wrap = node("div", { class: "tv-field" }, node("span", { text: label }),
        node("p", { class: "tv-help", text: hint }));
      var roles = node("div", { class: "tv-role-list" });
      (snap.roles || []).forEach(function (role) {
        var checked = (cfg[key] || []).indexOf(role.id) >= 0;
        var input = node("input", { type: "checkbox", checked: checked, oninput: function (event) {
          var result = (cfg[key] || []).filter(function (id) { return id !== role.id; });
          if (event.target.checked) result.push(role.id);
          set(key, result);
        } });
        roles.append(node("label", { class: "tv-role" }, input, node("span", { text: role.name })));
      });
      wrap.append(roles);
      parent.append(wrap);
    }
    function buttonLayout() {
      function emojiNode(option, id) {
        if (option && option.emoji_url) {
          return node("img", { class: "tv-chip-emoji", src: option.emoji_url,
            alt: "", "aria-hidden": "true", loading: "lazy" });
        }
        return node("span", { class: "tv-chip-emoji", text:
          (option && option.emoji) || BUTTON_EMOJIS[id] || "•", "aria-hidden": "true" });
      }
      var choices = snap.buttons || Object.keys(BUTTON_LABELS).map(function (id) {
        return { id: id, label: BUTTON_LABELS[id], emoji: BUTTON_EMOJIS[id],
          gear: ["report", "activity", "slowmode", "emergency"].includes(id) };
      });
      var active = section("أزرار البانل", "تظهر الأزرار بأيقونات فقط في Discord: أربعة أزرار في الصف وخمسة صفوف كحد أقصى. اسحب لإعادة الترتيب أو استخدم الأسهم.");
      var activeList = node("div", { class: "tv-button-grid", "aria-label": "الأزرار المعروضة" });
      (cfg.buttons || []).forEach(function (id, index) {
        var option = choices.find(function (x) { return x.id === id; }) ||
          { id: id, label: BUTTON_LABELS[id] || id, emoji: BUTTON_EMOJIS[id] };
        var chip = node("div", { class: "tv-chip", draggable: true, "data-drag-index": index },
          emojiNode(option, id),
          node("span", { text: option.label }));
        if (option.gear) chip.append(node("button", { type: "button", class: "tv-iconbtn", title: "إعدادات إضافية",
          text: "⚙", onclick: function () { buttonSettings(option.id); } }));
        chip.append(node("button", { type: "button", class: "tv-iconbtn", title: "تحريك لأعلى", "aria-label": "تحريك " + option.label + " لأعلى",
          disabled: index === 0, text: "↑", onclick: function () { move(index, -1); } }),
          node("button", { type: "button", class: "tv-iconbtn", title: "تحريك لأسفل", "aria-label": "تحريك " + option.label + " لأسفل",
            disabled: index === cfg.buttons.length - 1, text: "↓", onclick: function () { move(index, 1); } }),
          node("button", { type: "button", class: "tv-iconbtn is-remove", title: "إزالة الزر", "aria-label": "إزالة " + option.label,
            text: "×", onclick: function () { set("buttons", cfg.buttons.filter(function (x) { return x !== id; })); render(); } }));
        chip.addEventListener("dragstart", function (event) { event.dataTransfer.setData("text/plain", String(index)); });
        chip.addEventListener("dragover", function (event) { event.preventDefault(); });
        chip.addEventListener("drop", function (event) {
          event.preventDefault();
          var from = Number(event.dataTransfer.getData("text/plain"));
          if (!Number.isInteger(from) || from === index) return;
          var next = cfg.buttons.slice(), moved = next.splice(from, 1)[0];
          next.splice(index, 0, moved); set("buttons", next); render();
        });
        activeList.append(chip);
      });
      active.append(activeList, node("span", { class: "tv-count", text: (cfg.buttons || []).length + " / 20" }));
      var bank = node("div", { class: "tv-button-grid tv-bank", "aria-label": "الأزرار المتاحة" });
      choices.filter(function (x) { return !(cfg.buttons || []).includes(x.id); }).forEach(function (option) {
        bank.append(node("button", { type: "button", class: "tv-bank-button", disabled: cfg.buttons.length >= 20,
          onclick: function () { set("buttons", cfg.buttons.concat(option.id)); render(); } },
          emojiNode(option, option.id), node("span", { text: "+ " + option.label })));
      });
      active.append(node("h3", { text: "الأزرار المتوفرة" }), bank);
      return active;
    }
    function move(index, direction) {
      var target = index + direction;
      if (target < 0 || target >= cfg.buttons.length) return;
      var next = cfg.buttons.slice(), temp = next[index]; next[index] = next[target]; next[target] = temp;
      set("buttons", next); render();
    }
    function buttonSettings(key) {
      var existing = cfg.button_settings[key] || {};
      var dialog = node("dialog", { class: "tv-modal" });
      var form = node("form", { method: "dialog", class: "tv-modal-inner" });
      form.append(node("h2", { text: "إعدادات: " + BUTTON_LABELS[key] }),
        node("p", { class: "tv-help", text: "اختياري. سجلات الإدارة تُرسل إلى قناة نصية واحدة؛ التنبيهات لا تذكر @everyone." }));
      var channels = channelLists().text;
      var select = selectField("قناة سجل الإدارة", channels, existing.log_channel_id || "",
        function (value) { existing.log_channel_id = value || null; }, "— بلا سجل —");
      form.append(select);
      if (key === "activity") form.append(field("معرّف تطبيق النشاط", existing.application_id || "", "text", null, {
        oninput: function (event) { existing.application_id = event.target.value || null; },
        placeholder: "اختياري: Activity Application ID",
      }));
      if (key === "slowmode") form.append(field("مدة الوضع البطيء (ثانية)", existing.seconds == null ? 10 : existing.seconds, "number", null, {
        min: 0, max: 21600, oninput: function (event) { existing.seconds = Number(event.target.value); },
      }));
      var alertWrap = node("div", { class: "tv-field" }, node("span", { text: "رولات التنبيه" }));
      var alertRoles = node("div", { class: "tv-role-list" });
      (snap.roles || []).forEach(function (role) {
        var choice = node("input", { type: "checkbox", checked: (existing.alert_role_ids || []).includes(role.id),
          onchange: function (event) {
            var set = (existing.alert_role_ids || []).filter(function (id) { return id !== role.id; });
            if (event.target.checked) set.push(role.id);
            existing.alert_role_ids = set;
          } });
        alertRoles.append(node("label", { class: "tv-role" }, choice, node("span", { text: role.name })));
      });
      alertWrap.append(alertRoles); form.append(alertWrap);
      var close = node("button", { type: "button", class: "tv-action", text: "إلغاء", onclick: function () { dialog.close(); } });
      var apply = node("button", { type: "button", class: "tv-action is-primary", text: "حفظ إعداد الزر", onclick: function () {
        cfg.button_settings[key] = existing; markDirty(); dialog.close();
      } });
      form.append(node("div", { class: "tv-actions" }, close, apply));
      dialog.append(form); document.body.append(dialog);
      dialog.addEventListener("close", function () { dialog.remove(); });
      dialog.showModal();
    }
    function render() {
      if (disposed || !snap) return;
      var y = window.scrollY, focusId = root.contains(document.activeElement) ? document.activeElement.id : "";
      var n = snap.stats, chans = channelLists();
      root.replaceChildren();
      var hero = node("header", { class: "tv-header" }, node("div", {},
        node("small", { text: "PRIME • VOICE MANAGEMENT" }),
        node("h1", { text: "الرومات المؤقتة" }),
        node("p", { class: "tv-muted", text: "إعداد نظام إنشاء الرومات وإدارة الأزرار والإحصائيات المباشرة." })));
      var master = node("label", { class: "tv-master" }, node("span", {},
        node("b", { text: "لونا فويس" }), node("small", { text: snap.status.message || (snap.status.ready ? "المحرك متصل ويطبّق القواعد المحفوظة." : "محرك الرومات قيد الاتصال.") })),
        node("input", { type: "checkbox", role: "switch", checked: cfg.enabled,
          onchange: function (event) { set("enabled", event.target.checked); } }));
      hero.append(master); root.append(hero);
      if (snap.status.message) {
        var fault = node("div", { class: "tv-alert is-warn", text: snap.status.message });
        root.append(fault);
      }
      var cards = node("div", { class: "tv-stats" });
        [["دقيقة صوتية إجمالاً", n.total_minutes, "◷", "total_minutes"], ["رومات من البداية", n.rooms_created, "◧", "rooms_created"],
        ["بروفايلات محفوظة", n.saved_profiles, "▣", "saved_profiles"], ["رومات نشطة الآن", n.active_rooms, "◉", "active_rooms"]]
        .forEach(function (x) { cards.append(node("article", { class: "tv-stat" },
          node("span", { class: "tv-stat-icon", text: x[2] }), node("b", { text: Number(x[1] || 0).toLocaleString("en"), "data-live-stat": x[3] }),
          node("small", { text: x[0] }))); });
      root.append(cards);

      var setupCard = section("تجهيز النظام", "إعداد القنوات لا ينشر لوحة ولا ينشئ رومات عامة حتى تطلب ذلك صراحةً.");
      var fields = node("div", { class: "tv-grid" });
      fields.append(selectField("الكاتيجوري", chans.categories, cfg.category_id, function (v) { set("category_id", v || null); }),
        selectField("قناة لوحة التحكم", chans.text, cfg.panel_channel_id, function (v) { set("panel_channel_id", v || null); }),
        selectField("قناة الإنشاء الصوتية", chans.voice, cfg.creation_channel_id, function (v) {
          set("creation_channel_id", v || null);
          var selected = chans.voice.find(function (x) { return x.id === v; });
          if (selected) {
            var actual = snap.channels.categories.find(function (x) { return x.name === selected.category_name; });
            if (selected.category_id) set("category_id", selected.category_id);
          }
        }));
      setupCard.append(fields, node("p", { class: "tv-help", "data-dirty": "", text: dirty ? "مسودة غير محفوظة" : "الإعدادات محفوظة" }));
      var setupActions = node("div", { class: "tv-actions" },
        node("button", { type: "button", class: "tv-action is-primary", "data-setup": "", text: "✦ تجهيز الآن",
          onclick: function () { setup(false); } }),
        node("button", { type: "button", class: "tv-action", text: "إعادة نشر البانل",
          onclick: function () { setup(true); } }));
      setupCard.append(setupActions);
      root.append(setupCard);

      var defaults = section("إعدادات الرومات", "تطبّق القيم الافتراضية على الرومات الجديدة. المتغيرات: {OWNER_NAME} و{OWNER_MENTION} و{COUNT}.");
      var fields2 = node("div", { class: "tv-grid" });
      fields2.append(field("قالب الاسم", cfg.name_template, "text", "name_template"),
        field("حد الأعضاء (0 = بلا حد)", cfg.user_limit, "number", "user_limit", { min: 0, max: 99 }),
        field("جودة الصوت (kbps)", cfg.bitrate, "number", "bitrate", { min: 8, max: snap.limits.bitrate_max, step: 8 }),
        field("فترة الانتظار (ثانية)", cfg.cooldown, "number", "cooldown", { min: 0, max: 3600 }),
        selectField("الخصوصية الافتراضية", [{ id: "public", name: "عام — الكل يدخل" }, { id: "private", name: "خاص — المالك والموثوقون" }],
          cfg.privacy, function (value) { set("privacy", value); }));
      defaults.append(fields2, field("رسالة الترحيب", cfg.welcome_template, "text", "welcome_template", { maxLength: 1500, multiline: true, rows: 3 }));
      root.append(defaults);

      var style = section("الواجهة والألوان والبانر", "ارفع صورة واحدة لتظهر فوق أزرار التحكم، أو استخدم رابط HTTPS لصورة عامة.");
      var styleGrid = node("div", { class: "tv-grid" },
        field("عنوان البانل", cfg.panel_title, "text", "panel_title", { maxLength: 256 }),
        node("label", { class: "tv-field" }, node("span", { text: "لون شريط الحاوية" }),
          node("span", { class: "tv-color" },
            node("input", { type: "color", value: cfg.embed_color,
              oninput: function (event) { set("embed_color", event.target.value); root.style.setProperty("--tv-tint", event.target.value); } }),
            field("", cfg.embed_color, "text", "embed_color", { maxLength: 7, pattern: "#[0-9A-Fa-f]{6}",
              oninput: function (event) { cfg.embed_color = event.target.value; dirty = true; root.style.setProperty("--tv-tint", event.target.value); updateButtons(); } }))));
      style.append(styleGrid);
      var preview = node("article", { class: "tv-preview", "data-preview": "" },
        node("small", { text: "معاينة البانل" }), node("strong", { text: cfg.panel_title }),
        node("div", { class: "tv-preview-bar" }));
      if (draftBanner && draftBanner.url) {
        preview.append(node("img", { src: localUrl || draftBanner.url, alt: "معاينة بانر الرومات", class: "tv-banner-preview" }));
      } else if (cfg.banner_url && /^https:\/\//i.test(cfg.banner_url)) {
        preview.append(node("img", { src: cfg.banner_url, alt: "معاينة بانر الرومات", class: "tv-banner-preview" }));
      }
      style.append(preview);
      var palette = node("div", { class: "tv-palette" });
      COLORS.forEach(function (color) { palette.append(node("button", { type: "button", class: "tv-swatch", title: color,
        style: "--swatch:" + color, onclick: function () { set("theme_color", color); set("embed_color", color); root.style.setProperty("--tv-tint", color); render(); } })); });
      palette.append(node("button", { type: "button", class: "tv-action is-primary", text: "توليد الثيم",
        onclick: function () { var color = COLORS[Math.floor(Math.random() * COLORS.length)]; set("theme_color", color); set("embed_color", color); root.style.setProperty("--tv-tint", color); render(); } }),
        node("button", { type: "button", class: "tv-action", text: "إرجاع الافتراضي",
          onclick: function () { set("theme_color", "#8b5cf6"); set("embed_color", "#8b5cf6"); root.style.setProperty("--tv-tint", "#8b5cf6"); render(); } }));
      style.append(node("label", { class: "tv-field" }, node("span", { text: "ثيم البانل" }), palette));
      var urlField = field("رابط صورة HTTPS اختياري", cfg.banner_url, "url", null, { placeholder: "https://example.com/banner.png",
        oninput: function (event) {
          cfg.banner_url = event.target.value;
          cfg.banner_image_id = null;
          draftBanner = null;
          releaseUrl();
          dirty = true;
          updateButtons();
        } });
      var urlControl = urlField.querySelector("input");
      urlControl.addEventListener("input", function () {
        var preview = root.querySelector("[data-preview]");
        var image = preview && preview.querySelector(".tv-banner-preview");
        if (/^https:\/\//i.test(urlControl.value)) {
          if (!image && preview) {
            image = node("img", { class: "tv-banner-preview", alt: "معاينة بانر الرومات" });
            preview.append(image);
          }
          if (image) image.src = urlControl.value;
        } else if (image) image.remove();
      });
      var file = node("input", { type: "file", class: "tv-file-input", "data-upload": "",
        accept: "image/*", disabled: writing || uploading,
        "aria-label": "اختر صورة بانر من الجهاز",
        onchange: function (event) { var picked = event.target.files && event.target.files[0]; event.target.value = ""; uploadImage(picked); } });
      style.append(urlField,
        node("label", { class: "tv-field" },
          node("span", { text: uploading ? "جارٍ تجهيز ورفع الصورة…" : "اختر صورة من الجهاز؛ تُصغّر تلقائيًا عند الحاجة" }), file),
        node("div", { class: "tv-actions" },
        node("button", { type: "button", class: "tv-action", disabled: !draftBanner && !cfg.banner_url,
          text: "إزالة البانر", onclick: function () { cfg.banner_image_id = null; cfg.banner_url = ""; draftBanner = null; releaseUrl(); markDirty(); render(); } })));
      root.append(style);

      root.append(buttonLayout());
      var toggles = section("المميزات والصلاحيات", "تُحدّث صلاحيات Discord للقنوات المؤقتة القائمة والجديدة مع الحفاظ على شروط صلاحيات البوت.");
      FLAGS.forEach(function (row) { flagField(toggles, row[0], row[1], row[2]); });
      rolePicker(toggles, "blacklisted_role_ids", "رولات ممنوعة من الإنشاء", "حاملو أي رول هنا لا ينشئون رومًا.");
      rolePicker(toggles, "whitelisted_role_ids", "رولات مسموحة فقط", "إذا كانت فارغة، يستطيع كل عضو غير محظور إنشاء روم.");
      rolePicker(toggles, "admin_role_ids", "أدمن الرومات المؤقتة", "صلاحية إدارة الرومات المؤقتة فقط؛ لا تمنح صلاحيات Discord الأخرى.");
      root.append(toggles);

      var leaderboard = section("أعلى المالكين", "الدقائق منذ بدء التتبع؛ يبقى ترتيب إنشاء الرومات محفوظًا.");
      (snap.leaderboard || []).forEach(function (user) {
        var line = node("div", { class: "tv-leader" });
        if (user.avatar_url) line.append(node("img", { src: user.avatar_url, alt: "", loading: "lazy" }));
        line.append(node("b", { text: user.name }), node("span", { text: user.minutes + " دقيقة · " + user.rooms_created + " روم" }));
        leaderboard.append(line);
      });
      if (!(snap.leaderboard || []).length) leaderboard.append(node("p", { class: "tv-help", text: "لا توجد بيانات بعد." }));
      var live = node("div", { class: "tv-room-list tv-live-room-list", "data-live-rooms": "" });
      (snap.rooms || []).forEach(function (room) {
        live.append(roomItem(room));
      });
      if (!snap.rooms.length) live.append(node("p", { class: "tv-help", text: "لا توجد رومات نشطة." }));
      root.append(leaderboard, section("الرومات النشطة الآن", "يتحدّث النشاط تلقائيًا؛ الحذف الإجباري متاح للإداريين فقط.").appendChild(live).parentElement);

      status.className = "tv-alert" + (issue ? " is-warn" : notice ? " is-ok" : "");
      if (issue) status.textContent = issue;
      else if (notice) status.textContent = notice;
      var actions = node("footer", { class: "tv-sticky-actions" },
        node("span", { class: "tv-count", text: dirty ? "تغييرات غير محفوظة" : "الإعدادات محفوظة" }),
        node("button", { type: "button", class: "tv-action", text: "تراجع عن التغييرات",
          disabled: writing || uploading || !dirty, onclick: function () {
            releaseUrl();
            cfg = freshConfig(snap.config);
            draftBanner = snap.banner;
            dirty = false; conflict = false; issue = ""; render();
          } }),
        node("button", { type: "button", class: "tv-action is-primary", "data-save": "",
          disabled: writing || uploading || !dirty, text: writing ? "جارٍ الحفظ…" : "حفظ التغييرات", onclick: save }));
      root.append(status, actions);
      if (conflict) showConflict();
      window.scrollTo({ top: y, behavior: "instant" });
      if (focusId) requestAnimationFrame(function () { var control = document.getElementById(focusId); if (control && root.contains(control)) control.focus({ preventScroll: true }); });
      updateButtons();
    }
    function releaseUrl() {
      if (localUrl) URL.revokeObjectURL(localUrl);
      localUrl = "";
    }
    function roomItem(room) {
      return node("article", { class: "tv-live-room" },
        node("div", {}, node("b", { text: room.name }),
          node("p", { text: room.owner_name + " · " + room.members + " عضو" + (room.pinned ? " · مثبّت" : "") })),
        node("button", { type: "button", class: "tv-action is-danger", "data-delete-room": "", disabled: writing,
          text: "حذف إجباري", onclick: function () { deleteRoom(room); } }));
    }
    async function deleteRoom(room) {
      if (writing || !window.confirm("حذف الروم " + room.name + " وفصل أعضائه وإزالة غرفة الانتظار؟")) return;
      writing = true; updateButtons();
      try {
        var response = await request(url + "/rooms/" + encodeURIComponent(room.channel_id) + "/delete", {
          method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": options.getCsrf() },
          body: "{}",
        });
        accept(await readResponse(response), false);
        notice = "حُذف الروم المؤقت.";
        status.className = "tv-alert is-ok";
        status.textContent = notice;
        render();
      } catch (error) {
        status.className = "tv-alert is-warn";
        status.textContent = error.message;
      } finally {
        writing = false;
        updateButtons();
      }
    }
    async function uploadImage(file) {
      if (!file || writing || uploading) return;
      uploading = true; updateButtons();
      try {
        var uploadFile = await prepareBanner(file);
        var response = await request(url + "/banner", {
          method: "POST", headers: { "Content-Type": uploadFile.type, "X-CSRF-Token": options.getCsrf() }, body: uploadFile,
        });
        var data = await readResponse(response);
        releaseUrl();
        localUrl = URL.createObjectURL(uploadFile);
        draftBanner = data.image;
        cfg.banner_image_id = data.image.id;
        cfg.banner_url = "";
        markDirty(); issue = ""; uploading = false; render();
      } catch (error) {
        issue = error.message;
        status.className = "tv-alert is-warn";
        status.textContent = issue;
      }
      uploading = false; updateButtons();
    }
    function live() {
      root.querySelectorAll("[data-live-stat]").forEach(function (item) {
        var value = snap.stats[item.dataset.liveStat];
        if (value !== undefined) item.textContent = Number(value).toLocaleString("en");
      });
    }
    // The first server response builds the protected admin view; refresh only live
    // metrics and room data during normal polling, without replacing a dirty form.
    loading = true;
    fetchState(true).then(function () {
      if (disposed) return;
      render();
      liveTimer = setInterval(function () {
        if (!disposed && !document.hidden) fetchState(false).then(function () { if (!disposed) { updateLiveRooms(); live(); } });
      }, 8000);
    });
    function updateLiveRooms() {
      var existing = root.querySelector(".tv-live-room-list");
      if (!existing) return;
      var fresh = node("div", { class: "tv-room-list tv-live-room-list" });
      (snap.rooms || []).forEach(function (room) {
        fresh.append(roomItem(room));
      });
      if (!fresh.children.length) fresh.append(node("p", { class: "tv-help", text: "لا توجد رومات نشطة." }));
      existing.replaceChildren.apply(existing, Array.from(fresh.childNodes));
    }
    return function cleanup() {
      disposed = true;
      clearInterval(liveTimer);
      releaseUrl();
      if (modal && modal.isConnected) modal.close();
      root.replaceChildren();
    };
  }
  window.PrimeTempVoice = { mount: mount };
}());
