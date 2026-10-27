(() => {
  "use strict";

  const DEFAULTS = {
    avatar_x: 14, avatar_y: 50, name_x: 94, name_y: 38,
    title_y: 20, server_y: 60, count_y: 75,
    accent: "#7c3aed", background_start: "#040a16",
    background_end: "#194876", show_server: true, show_count: true,
    use_custom_background: false, canvas_format: "auto", background_slot: 1,
  };
  const BASE_ELEMENTS = [
    { id:"frame", type:"frame", label:"إطار التصميم", x:1.8, y:5.2, width:96.4, height:90, fontSize:20, color:"#7c3aed", visible:true, locked:false, opacity:62, rotation:0, align:"center", text:"" },
    { id:"avatar", type:"avatar", label:"صورة العضو", x:4.8, y:23.8, width:18.3, height:52.4, fontSize:30, color:"#ffffff", visible:true, locked:false, opacity:100, rotation:0, align:"center", text:"" },
    { id:"title", type:"title", label:"عنوان الترحيب", x:44, y:15, width:50, height:10, fontSize:27, color:"#7c3aed", visible:true, locked:false, opacity:100, rotation:0, align:"right", text:"أهلاً بك في PRIME" },
    { id:"name", type:"name", label:"اسم العضو", x:49, y:27, width:45, height:15, fontSize:48, color:"#f8fafc", visible:true, locked:false, opacity:100, rotation:0, align:"right", text:"اسم العضو" },
    { id:"server", type:"server", label:"اسم السيرفر", x:49, y:52, width:45, height:9, fontSize:23, color:"#c0cfe2", visible:true, locked:false, opacity:100, rotation:0, align:"right", text:"اسم السيرفر" },
    { id:"count", type:"count", label:"رقم العضو", x:49, y:68, width:45, height:9, fontSize:23, color:"#7c3aed", visible:true, locked:false, opacity:100, rotation:0, align:"right", text:"العضو رقم 128" },
    { id:"watermark", type:"text", label:"شعار PRIME", x:4, y:83, width:30, height:9, fontSize:22, color:"#7c3aed", visible:true, locked:false, opacity:16, rotation:0, align:"left", text:"PRIME" },
  ];
  let root = null, current = null, selectedId = "name", onSaveCallback = null;
  let history = [], future = [];
  const clamp = (v, min, max) => Math.max(min, Math.min(max, Number(v) || 0));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
  const uid = () => "layer-" + Math.random().toString(36).slice(2, 9);
  const findElement = id => current?.elements?.find(item => item.id === id);
  const labelFor = type => ({avatar:"صورة العضو",title:"عنوان الترحيب",name:"اسم العضو",server:"اسم السيرفر",count:"رقم العضو",text:"نص مخصص",shape:"شكل",line:"خط",frame:"إطار التصميم"}[type] || "عنصر");

  function normalize(config) {
    const base = { ...DEFAULTS, ...(config && typeof config === "object" ? config : {}) };
    if (!["auto", "banner", "landscape", "square", "portrait"].includes(base.canvas_format)) base.canvas_format = "auto";
    base.background_slot = Math.round(clamp(base.background_slot ?? 1, 1, 4));
    if (Array.isArray(base.elements)) {
      base.elements = base.elements.slice(0, 24).map((e, i) => ({
        id: String(e.id || uid()), type: ["avatar","title","name","server","count","text","shape","line","frame"].includes(e.type) ? e.type : "text",
        label: String(e.label || labelFor(e.type)), x: clamp(e.x, 0, 100), y: clamp(e.y, 0, 100),
        width: clamp(e.width ?? 30, 2, 100), height: clamp(e.height ?? 10, 2, 100),
        fontSize: clamp(e.fontSize ?? 24, 8, 160), color: /^#[0-9a-f]{6}$/i.test(e.color || "") ? e.color : base.accent,
        fontFamily: ["sans","serif","mono","condensed"].includes(e.fontFamily) ? e.fontFamily : "sans",
        fontWeight: e.fontWeight === "bold" ? "bold" : "normal",
        fontStyle: e.fontStyle === "italic" ? "italic" : "normal",
        textTransform: ["none","uppercase","lowercase","capitalize"].includes(e.textTransform) ? e.textTransform : "none",
        visible: e.visible !== false, locked: e.locked === true, opacity: clamp(e.opacity ?? 100, 0, 100),
        rotation: clamp(e.rotation ?? 0, -180, 180), align: ["left","center","right"].includes(e.align) ? e.align : "left",
        text: String(e.text ?? "").slice(0, 200),
      }));
    } else {
      base.elements = BASE_ELEMENTS.map(e => ({
        ...e, fontFamily: e.fontFamily || "sans", fontWeight: e.fontWeight || "normal",
        fontStyle: e.fontStyle || "normal", textTransform: e.textTransform || "none",
      }));
      const update = (id, patch) => Object.assign(base.elements.find(e => e.id === id), patch);
      update("avatar", { x: clamp((base.avatar_x || 14) - 9.15, 0, 81.7), y: clamp((base.avatar_y || 50) - 26.2, 0, 47.6) });
      update("title", { y: clamp(base.title_y ?? 20, 0, 90), color: base.accent });
      update("name", { y: clamp((base.name_y ?? 38) - 7.5, 0, 85), x: clamp((base.name_x ?? 94) - 45, 0, 55) });
      update("server", { y: clamp((base.server_y ?? 60) - 4.5, 0, 91), x: clamp((base.name_x ?? 94) - 45, 0, 55), visible: base.show_server !== false, color: "#c0cfe2" });
      update("count", { y: clamp((base.count_y ?? 75) - 4.5, 0, 91), x: clamp((base.name_x ?? 94) - 45, 0, 55), visible: base.show_count !== false, color: base.accent });
    }
    return base;
  }

  function close() {
    if (root) {
      root.querySelectorAll(".welcome-editor-bg-thumb img[data-object-url]").forEach(image => {
        try { URL.revokeObjectURL(image.dataset.objectUrl); } catch (_) {}
      });
      root.remove();
    }
    document.removeEventListener("keydown", onEscape);
    root = null; current = null; onSaveCallback = null;
  }

  function open({ config = {}, onSave } = {}) {
    close();
    current = normalize(config); history = []; future = []; selectedId = current.elements.some(e => e.id === "name") ? "name" : current.elements[0]?.id;
    window.PRIMEWelcomeEditor.cacheKey = Date.now();
    onSaveCallback = onSave;
    const modal = document.createElement("div");
    modal.className = "welcome-editor-backdrop";
    modal.innerHTML = `
      <section class="welcome-editor-modal" role="dialog" aria-modal="true" aria-labelledby="welcome-editor-heading">
        <header class="welcome-editor-header">
          <div><span class="welcome-editor-kicker">PRIME • WELCOME STUDIO</span><h2 id="welcome-editor-heading">محرر الترحيب الاحترافي</h2><p>اسحب العناصر داخل التصميم، وأظهر أو أخفِ أي طبقة، وعدّل خصائصها مباشرة.</p></div>
          <button type="button" class="welcome-editor-close" aria-label="إغلاق">×</button>
        </header>
        <div class="welcome-editor-toolbar">
          <button type="button" class="btn" data-add="text">＋ نص</button>
          <button type="button" class="btn" data-add="shape">＋ شكل</button>
          <button type="button" class="btn" data-add="line">＋ خط</button>
          <button type="button" class="btn" data-action="undo" disabled>↶ تراجع</button>
          <button type="button" class="btn" data-action="redo" disabled>↷ إعادة</button>
          <button type="button" class="btn" data-action="reset">استعادة الافتراضي</button>
          <span class="welcome-editor-toolbar-hint">اضغط على العنصر لتحديده واسحبه لتحريكه</span>
        </div>
        <div class="welcome-editor-workspace">
          <div class="welcome-editor-preview-wrap">
            <div class="welcome-editor-stage" id="welcome-editor-preview" aria-label="مساحة تصميم صورة الترحيب"></div>
            <p class="welcome-editor-note">المعاينة تتبع مواضع العناصر وخصائصها. الصورة النهائية تُنشأ بواسطة البوت عند وصول العضو.</p>
            <div class="welcome-editor-upload">
              <label class="welcome-editor-file-label">خلفية التصميم
                <input type="file" accept="image/png,image/jpeg,image/webp" id="welcome-editor-background-file">
              </label>
              <span class="welcome-editor-upload-hint">PNG / JPG / WEBP — حتى 15MB، بأي أبعاد. صور الخانات تُحفظ بشكل مستقل.</span>
              <div class="welcome-editor-bg-slots" role="group" aria-label="الخلفيات المحفوظة">
                <span>الخانة الحالية</span>
                <button type="button" class="btn welcome-editor-bg-slot" data-background-slot="1" aria-pressed="false"><span class="welcome-editor-bg-thumb" data-slot-thumb="1" aria-hidden="true"><span class="welcome-editor-bg-placeholder">1</span></span><span class="welcome-editor-bg-label">صورة 1</span><span class="welcome-editor-bg-state" data-slot-state="1">جارٍ الفحص</span></button>
                <button type="button" class="btn welcome-editor-bg-slot" data-background-slot="2" aria-pressed="false"><span class="welcome-editor-bg-thumb" data-slot-thumb="2" aria-hidden="true"><span class="welcome-editor-bg-placeholder">2</span></span><span class="welcome-editor-bg-label">صورة 2</span><span class="welcome-editor-bg-state" data-slot-state="2">جارٍ الفحص</span></button>
                <button type="button" class="btn welcome-editor-bg-slot" data-background-slot="3" aria-pressed="false"><span class="welcome-editor-bg-thumb" data-slot-thumb="3" aria-hidden="true"><span class="welcome-editor-bg-placeholder">3</span></span><span class="welcome-editor-bg-label">صورة 3</span><span class="welcome-editor-bg-state" data-slot-state="3">جارٍ الفحص</span></button>
                <button type="button" class="btn welcome-editor-bg-slot" data-background-slot="4" aria-pressed="false"><span class="welcome-editor-bg-thumb" data-slot-thumb="4" aria-hidden="true"><span class="welcome-editor-bg-placeholder">4</span></span><span class="welcome-editor-bg-label">صورة 4</span><span class="welcome-editor-bg-state" data-slot-state="4">جارٍ الفحص</span></button>
              </div>
              <div class="welcome-editor-upload-actions">
                <button type="button" class="btn" data-action="upload-background">رفع وحفظ في الخانة</button>
                <button type="button" class="btn" data-action="clear-background">إيقاف الخلفية المخصصة</button>
                <button type="button" class="btn danger" data-action="delete-background">حذف صورة الخانة</button>
              </div>
            </div>
            <div class="welcome-editor-background-colors">
              <label class="welcome-editor-canvas-format">مقاس لوحة الترحيب
                <select data-config-select="canvas_format">
                  <option value="auto">تلقائي حسب نسبة الصورة</option>
                  <option value="banner">بانر عريض 1200×420</option>
                  <option value="landscape">عريض 16:9</option>
                  <option value="square">مربع 1:1</option>
                  <option value="portrait">عمودي 2:3</option>
                </select>
              </label>
              <label>لون التمييز <input type="color" data-config-color="accent"></label>
              <label>بداية التدرج <input type="color" data-config-color="background_start"></label>
              <label>نهاية التدرج <input type="color" data-config-color="background_end"></label>
            </div>
          </div>
          <aside class="welcome-editor-side">
            <section class="welcome-editor-panel">
              <div class="welcome-editor-panel-title"><h3>الطبقات</h3><span data-layer-count></span></div>
              <p class="welcome-editor-panel-hint">العين للإظهار، والقفل لمنع التحريك. اسحب العنصر في لوحة التصميم لتغيير مكانه.</p>
              <div class="welcome-editor-layers" data-layers></div>
            </section>
            <section class="welcome-editor-panel">
              <div class="welcome-editor-panel-title"><h3>خصائص العنصر</h3><span data-selected-name>—</span></div>
              <div class="welcome-editor-properties" data-properties></div>
            </section>
          </aside>
        </div>
        <footer class="welcome-editor-footer">
          <p class="welcome-editor-status" role="status" aria-live="polite"></p>
          <button type="button" class="btn primary" data-action="save">حفظ التصميم</button>
        </footer>
      </section>`;
    root = modal; document.body.append(modal);
    modal.querySelector(".welcome-editor-close").addEventListener("click", close);
    modal.addEventListener("click", e => { if (e.target === modal) close(); });
    document.addEventListener("keydown", onEscape);
    modal.querySelectorAll("[data-add]").forEach(button => button.addEventListener("click", () => addElement(button.dataset.add)));
    modal.querySelector('[data-action="reset"]').addEventListener("click", () => {
      checkpoint();
      current = normalize({ ...DEFAULTS, accent: current.accent, background_start: current.background_start, background_end: current.background_end });
      selectedId = "name"; renderAll(); setStatus("تمت استعادة العناصر الافتراضية. اضغط حفظ لتثبيت التغيير.");
    });
    modal.querySelectorAll("[data-config-select]").forEach(input => input.addEventListener("change", () => {
      checkpoint();
      current[input.dataset.configSelect] = input.value;
      renderAll();
      setStatus("تم تغيير مقاس اللوحة. احفظ التصميم لتثبيت المقاس الجديد.");
    }));
    modal.querySelectorAll("[data-config-color]").forEach(input => input.addEventListener("input", () => {
      checkpoint();
      const key = input.dataset.configColor;
      const previous = current[key];
      current[key] = input.value;
      if (key === "accent") {
        current.elements.forEach(element => {
          if (element.color === previous) element.color = input.value;
        });
      }
      renderStage(); renderLayers(); renderProperties(); updateConfigColorFields();
    }));
    modal.querySelectorAll("[data-background-slot]").forEach(button => button.addEventListener("click", () => {
      checkpoint();
      current.background_slot = Number(button.dataset.backgroundSlot);
      current.use_custom_background = true;
      window.PRIMEWelcomeEditor.cacheKey = Date.now();
      renderAll();
      setStatus("تم تحديد خانة الصورة " + current.background_slot + ". ارفع صورة لحفظها بهذه الخانة أو احفظ التصميم لاستخدامها.");
    }));
    const fileInput = modal.querySelector("#welcome-editor-background-file");
    modal.querySelector('[data-action="upload-background"]').addEventListener("click", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return setStatus("اختر صورة أولاً.");
      if (!["image/png","image/jpeg","image/webp"].includes(file.type)) return setStatus("الصيغ المدعومة PNG وJPG وWEBP فقط.");
      if (file.size > 15 * 1024 * 1024) return setStatus("الحد الأقصى لحجم الخلفية 15 ميجابايت.");
      const button = modal.querySelector('[data-action="upload-background"]');
      const slot = Number(current.background_slot || 1);
      button.disabled = true; setStatus("جارٍ رفع الصورة وحفظها في الخانة " + slot + "…");
      try {
        let uploaded = false;
        for (let attempt = 0; attempt < 2 && !uploaded; attempt++) {
          try {
            const form = new FormData(); form.append("file", file);
            const response = await fetch("api/guild/" + encodeURIComponent(window.PRIMEWelcomeEditor.guildId) + "/onboarding/welcome-background?slot=" + encodeURIComponent(slot), {
              method:"POST", headers:{"X-CSRF-Token":window.PRIMEWelcomeEditor.csrfToken || ""}, body:form, credentials:"same-origin"
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.ok) {
              if (response.status === 499 && attempt === 0) {
                setStatus("انقطع الاتصال أثناء الرفع؛ جارٍ إعادة المحاولة…");
                await new Promise(resolve => setTimeout(resolve, 700));
                continue;
              }
              return setStatus(result.fields?.file || "تعذر رفع الخلفية والتحقق منها.");
            }
            checkpoint(); current.use_custom_background = true; window.PRIMEWelcomeEditor.cacheKey = Date.now(); renderAll(); loadBackgroundSlotPreviews(); setStatus("تم حفظ صورة الخانة " + slot + " في قاعدة البيانات. اضغط حفظ التصميم لتفعيلها للترحيب والتجربة.");
            uploaded = true;
          } catch (_) {
            if (attempt === 0) {
              setStatus("انقطع الاتصال أثناء الرفع؛ جارٍ إعادة المحاولة…");
              await new Promise(resolve => setTimeout(resolve, 700));
            } else {
              setStatus("تعذر الاتصال بالخادم بعد محاولتين. تحقق من الشبكة ثم أعد رفع الصورة.");
            }
          }
        }
      } finally { button.disabled = false; }
    });
    modal.querySelector('[data-action="clear-background"]').addEventListener("click", () => {
      checkpoint();
      current.use_custom_background = false; renderAll(); setStatus("تم إيقاف الخلفية المخصصة فقط؛ الصور المحفوظة في الخانات الأربع لم تُحذف. احفظ التصميم لتطبيق التغيير.");
    });
    modal.querySelector('[data-action="delete-background"]').addEventListener("click", async () => {
      const slot = Number(current.background_slot || 1);
      const button = modal.querySelector('[data-action="delete-background"]');
      button.disabled = true;
      setStatus("جارٍ حذف صورة الخانة " + slot + " من قاعدة البيانات…");
      try {
        const url = "api/guild/" + encodeURIComponent(window.PRIMEWelcomeEditor.guildId) + "/onboarding/welcome-background?slot=" + encodeURIComponent(slot);
        const response = await fetch(url, {
          method: "DELETE",
          headers: { "X-CSRF-Token": window.PRIMEWelcomeEditor.csrfToken || "" },
          credentials: "same-origin",
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) {
          return setStatus(result.fields?.slot || "تعذر حذف الصورة المحفوظة. لم نعتبر الحذف ناجحًا.");
        }
        if (Number(current.background_slot || 1) === slot) current.use_custom_background = false;
        window.PRIMEWelcomeEditor.cacheKey = Date.now();
        renderAll();
        loadBackgroundSlotPreviews();
        setStatus("تم حذف صورة الخانة " + slot + " فقط. بقية الصور لم تتأثر؛ احفظ التصميم لتثبيت إيقاف الخلفية.");
      } catch (_) {
        setStatus("تعذر الاتصال لحذف الصورة. لم نؤكد نجاح الحذف.");
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    });
    modal.querySelector('[data-action="undo"]').addEventListener("click", undo);
    modal.querySelector('[data-action="redo"]').addEventListener("click", redo);
    modal.querySelector('[data-action="save"]').addEventListener("click", async () => {
      const button = modal.querySelector('[data-action="save"]');
      button.disabled = true; setStatus("جارٍ حفظ التصميم…");
      try {
        syncLegacyFields();
        const ok = await onSaveCallback({ ...current, elements: current.elements.map(e => ({ ...e })) });
        if (ok) setStatus("تم حفظ التصميم بنجاح."); else setStatus("تعذر الحفظ. راجع اتصال اللوحة ثم حاول مجددًا.");
      } catch (_) { setStatus("تعذر الحفظ. حاول مرة أخرى."); }
      finally { if (button.isConnected) button.disabled = false; }
    });
    renderAll();
    loadBackgroundSlotPreviews();
    modal.querySelector(".welcome-editor-close").focus();
  }

  function onEscape(e) { if (e.key === "Escape") close(); }
  function setStatus(message) { if (root) root.querySelector(".welcome-editor-status").textContent = message; }
  function updateConfigColorFields() {
    if (!root) return;
    root.querySelectorAll("[data-config-color]").forEach(input => input.value = current[input.dataset.configColor] || "#7c3aed");
    root.querySelectorAll("[data-config-select]").forEach(input => input.value = current[input.dataset.configSelect] || "auto");
    root.querySelectorAll("[data-background-slot]").forEach(button => {
      const active = Number(button.dataset.backgroundSlot) === Number(current.background_slot || 1);
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }
  async function loadBackgroundSlotPreviews() {
    if (!root || !window.PRIMEWelcomeEditor.guildId) return;
    const guildId = String(window.PRIMEWelcomeEditor.guildId);
    const buttons = [...root.querySelectorAll("[data-background-slot]")];
    await Promise.all(buttons.map(async button => {
      const slot = Number(button.dataset.backgroundSlot);
      const thumb = button.querySelector("[data-slot-thumb]");
      const state = button.querySelector("[data-slot-state]");
      if (!thumb || !state) return;
      state.textContent = "جارٍ الفحص";
      try {
        const url = "api/guild/" + encodeURIComponent(guildId) + "/onboarding/welcome-background?slot=" + slot + "&v=" + (window.PRIMEWelcomeEditor.cacheKey || Date.now());
        const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
        if (!root || String(window.PRIMEWelcomeEditor.guildId) !== guildId) return;
        if (response.status === 404) {
          thumb.replaceChildren(Object.assign(document.createElement("span"), { className: "welcome-editor-bg-placeholder", textContent: "+" }));
          button.classList.remove("has-image");
          state.textContent = "فارغة";
          return;
        }
        if (!response.ok) throw new Error("background_load_failed");
        const blob = await response.blob();
        if (!blob.type.startsWith("image/") || !blob.size) throw new Error("invalid_background_response");
        const previousImage = thumb.querySelector("img[data-object-url]");
        if (previousImage) {
          try { URL.revokeObjectURL(previousImage.dataset.objectUrl); } catch (_) {}
        }
        const objectUrl = URL.createObjectURL(blob);
        const image = document.createElement("img");
        image.src = objectUrl;
        image.alt = "";
        image.loading = "lazy";
        image.decoding = "async";
        image.dataset.objectUrl = objectUrl;
        thumb.replaceChildren(image);
        button.classList.add("has-image");
        state.textContent = "محفوظة";
      } catch (_) {
        button.classList.remove("has-image");
        state.textContent = "تعذر التحميل";
      }
    }));
  }

  function syncLegacyFields() {
    // The freeform elements are the source of truth; legacy coordinate limits
    // must never constrain or reject a user-designed canvas.
    const server = findElement("server"), count = findElement("count");
    if (server) current.show_server = server.visible;
    if (count) current.show_count = count.visible;
  }
  function addElement(type) {
    if (current.elements.length >= 24) return setStatus("الحد الأقصى 24 طبقة في التصميم.");
    const id = uid(), el = {
      id, type, label: labelFor(type), x: type === "line" ? 35 : 38, y: 42 + (current.elements.length % 4) * 5,
      width: type === "shape" ? 18 : type === "line" ? 30 : 24, height: type === "shape" ? 12 : type === "line" ? 1.5 : 10,
      fontSize: 26, color: current.accent || "#a78bfa", visible: true, locked: false, opacity: 100, rotation: 0, align: "left",
      fontFamily: "sans", fontWeight: "normal", fontStyle: "normal", textTransform: "none",
      text: type === "text" ? "اكتب النص هنا" : "",
    };
    checkpoint(); current.elements.push(el); selectedId = id; renderAll(); setStatus("أُضيف عنصر جديد.");
  }
  function renderAll() { renderStage(); renderLayers(); renderProperties(); updateConfigColorFields(); updateHistoryButtons(); }
  function checkpoint() {
    if (!current) return;
    const snapshot = JSON.stringify(current);
    if (history[history.length - 1] !== snapshot) history.push(snapshot);
    if (history.length > 60) history.shift();
    future = [];
    updateHistoryButtons();
  }
  function updateHistoryButtons() {
    if (!root) return;
    const undoButton = root.querySelector('[data-action="undo"]');
    const redoButton = root.querySelector('[data-action="redo"]');
    if (undoButton) undoButton.disabled = history.length === 0;
    if (redoButton) redoButton.disabled = future.length === 0;
  }
  function restoreSnapshot(snapshot) {
    current = normalize(JSON.parse(snapshot));
    if (!current.elements.some(item => item.id === selectedId)) selectedId = current.elements[0]?.id || null;
    renderAll();
  }
  function undo() {
    if (!history.length) return;
    future.push(JSON.stringify(current));
    restoreSnapshot(history.pop());
    setStatus("تم التراجع عن آخر تعديل.");
  }
  function redo() {
    if (!future.length) return;
    history.push(JSON.stringify(current));
    restoreSnapshot(future.pop());
    setStatus("تمت إعادة التعديل.");
  }
  function renderStage() {
    if (!root || !current) return;
    const stage = root.querySelector("#welcome-editor-preview");
    stage.style.setProperty("--welcome-accent", current.accent);
    const fixedRatios = { banner: "1200 / 420", landscape: "16 / 9", square: "1 / 1", portrait: "2 / 3" };
    stage.style.aspectRatio = fixedRatios[current.canvas_format] || "1200 / 420";
    let fittedBackgroundUrl = null;
    stage.style.backgroundImage = "linear-gradient(115deg," + current.background_start + "," + current.background_end + ")";
    stage.style.backgroundSize = "cover";
    stage.style.backgroundRepeat = "no-repeat";
    stage.style.backgroundColor = "#080c16";
    if (current.use_custom_background) {
      fittedBackgroundUrl = "api/guild/" + encodeURIComponent(window.PRIMEWelcomeEditor.guildId) + "/onboarding/welcome-background?slot=" + encodeURIComponent(current.background_slot || 1) + "&v=" + (window.PRIMEWelcomeEditor.cacheKey || Date.now());
      if (current.canvas_format === "auto") {
        const preload = new Image();
        preload.onload = () => {
          if (root && current?.use_custom_background && current.canvas_format === "auto") {
            stage.style.aspectRatio = preload.naturalWidth + " / " + preload.naturalHeight;
          }
        };
        preload.src = fittedBackgroundUrl;
      }
    }
    stage.replaceChildren();
    if (fittedBackgroundUrl) {
      const addBackgroundLayer = (fit, blur) => {
        const image = document.createElement("img");
        image.src = fittedBackgroundUrl;
        image.alt = "";
        image.onerror = () => image.remove();
        image.draggable = false;
        image.setAttribute("aria-hidden", "true");
        Object.assign(image.style, {
          position: "absolute", inset: "0", width: "100%", height: "100%",
          objectFit: fit, pointerEvents: "none", zIndex: "0",
        });
        if (blur) {
          image.style.filter = "blur(16px)";
          image.style.transform = "scale(1.08)";
        }
        stage.append(image);
      };
      addBackgroundLayer("cover", true);
      addBackgroundLayer("contain", false);
    }
    current.elements.forEach(el => {
      const node = document.createElement("div");
      node.className = "welcome-editor-object type-" + el.type + (el.id === selectedId ? " is-selected" : "") + (!el.visible ? " is-hidden" : "") + (el.locked ? " is-locked" : "");
      node.dataset.elementId = el.id; node.tabIndex = 0; node.setAttribute("role","button");
      node.setAttribute("aria-label", el.label + (el.visible ? "" : " (مخفي)"));
      node.style.left = el.x + "%"; node.style.top = el.y + "%";
      node.style.width = el.width + "%"; node.style.height = el.height + "%";
      node.style.setProperty("--element-color", el.color || current.accent);
      node.style.setProperty("--element-opacity", String(el.opacity / 100));
      node.style.setProperty("--element-rotation", el.rotation + "deg");
      node.style.setProperty("--element-font", "clamp(6px," + Math.max(.5, el.fontSize / 12) + "cqw,80px)");
      node.style.textAlign = el.align || "left";
      const fontStacks = {
        sans: 'Tahoma, "Noto Sans Arabic", "DejaVu Sans", Arial, sans-serif',
        serif: '"Noto Naskh Arabic", "Traditional Arabic", "DejaVu Serif", Georgia, serif',
        mono: '"DejaVu Sans Mono", "Noto Sans Arabic", monospace',
        condensed: '"Arial Narrow", Tahoma, "DejaVu Sans", sans-serif',
      };
      node.style.fontFamily = fontStacks[el.fontFamily || "sans"] || fontStacks.sans;
      node.style.fontWeight = el.fontWeight === "bold" ? "700" : "400";
      node.style.fontStyle = el.fontStyle === "italic" ? "italic" : "normal";
      node.style.textTransform = el.textTransform || "none";
      node.dir = "auto";
      node.style.unicodeBidi = "plaintext";
      node.style.justifyContent = el.align === "right" ? "flex-end" : (el.align === "center" ? "center" : "flex-start");
      node.hidden = !el.visible;
      if (el.type === "avatar") node.innerHTML = '<span class="welcome-editor-avatar-placeholder">P</span>';
      else if (el.type === "shape") node.innerHTML = '<span class="welcome-editor-shape-fill"></span>';
      else if (el.type === "line") node.innerHTML = '<span class="welcome-editor-line-fill"></span>';
      else if (el.type === "frame") node.setAttribute("aria-label", "إطار التصميم");
      else node.textContent = displayText(el);
      if (el.id === selectedId && el.visible) {
        ["nw","ne","sw","se"].forEach(corner => {
          const handle = document.createElement("span");
          handle.className = "welcome-editor-resize-handle handle-" + corner;
          handle.setAttribute("aria-label", "تغيير حجم العنصر");
          handle.addEventListener("pointerdown", event => startResize(event, el, node, handle, corner));
          node.append(handle);
        });
      }
      node.addEventListener("pointerdown", e => startDrag(e, el, node));
      node.addEventListener("click", e => { e.stopPropagation(); if (selectedId !== el.id) { selectedId = el.id; renderAll(); } });
      node.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectedId = el.id; renderAll(); }
        if (selectedId === el.id && !el.locked && ["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(e.key)) {
          e.preventDefault(); const amount = e.shiftKey ? 2 : .5;
          if (e.key === "ArrowLeft") el.x -= amount; if (e.key === "ArrowRight") el.x += amount;
          if (e.key === "ArrowUp") el.y -= amount; if (e.key === "ArrowDown") el.y += amount;
          clampElement(el); renderAll();
        }
      });
      stage.append(node);
    });
    stage.onclick = () => { selectedId = null; renderAll(); };
  }
  function displayText(el) {
    const sample = { title:"أهلاً بك في PRIME", name:"اسم العضو", server:"اسم السيرفر", count:"العضو رقم 128" };
    return el.text || sample[el.type] || "";
  }
  function clampElement(el) {
    el.width = clamp(el.width, 2, 100); el.height = clamp(el.height, 1, 100);
    el.x = Number(clamp(el.x, 0, 100 - el.width).toFixed(1));
    el.y = Number(clamp(el.y, 0, 100 - el.height).toFixed(1));
    el.fontSize = clamp(el.fontSize, 8, 160); el.opacity = clamp(el.opacity, 0, 100);
    el.rotation = clamp(el.rotation, -180, 180);
  }
  function startDrag(e, el, node) {
    if (e.button !== undefined && e.button !== 0) return;
    e.stopPropagation(); selectedId = el.id;
    if (el.locked) { renderLayers(); renderProperties(); return; }
    const stage = root.querySelector("#welcome-editor-preview"), rect = stage.getBoundingClientRect();
    const startX = e.clientX, startY = e.clientY, origX = el.x, origY = el.y;
    let didMove = false;
    try { node.setPointerCapture(e.pointerId); } catch (_) {}
    const move = ev => {
      if (ev.pointerId !== e.pointerId) return;
      if (!didMove) { checkpoint(); didMove = true; }
      el.x = origX + (ev.clientX - startX) / rect.width * 100;
      el.y = origY + (ev.clientY - startY) / rect.height * 100;
      clampElement(el); node.style.left = el.x + "%"; node.style.top = el.y + "%"; node.classList.add("is-dragging");
      syncPropertyInputs(el);
    };
    const up = ev => {
      if (ev.pointerId !== e.pointerId) return;
      node.removeEventListener("pointermove", move); node.removeEventListener("pointerup", up); node.removeEventListener("pointercancel", up);
      node.classList.remove("is-dragging"); selectedId = el.id; renderAll();
    };
    node.addEventListener("pointermove", move); node.addEventListener("pointerup", up); node.addEventListener("pointercancel", up);
    renderLayers(); renderProperties();
  }
  function startResize(event, el, node, handle, corner) {
    event.stopPropagation(); event.preventDefault();
    if (el.locked || (event.button !== undefined && event.button !== 0)) return;
    const stageRect = root.querySelector("#welcome-editor-preview").getBoundingClientRect();
    const startX = event.clientX, startY = event.clientY;
    const original = { x:el.x, y:el.y, width:el.width, height:el.height };
    let changed = false;
    try { handle.setPointerCapture(event.pointerId); } catch (_) {}
    const move = e => {
      if (e.pointerId !== event.pointerId) return;
      const dx = (e.clientX - startX) / stageRect.width * 100;
      const dy = (e.clientY - startY) / stageRect.height * 100;
      if (!changed) { checkpoint(); changed = true; }
      let x = original.x, y = original.y, width = original.width, height = original.height;
      if (corner.includes("e")) width = original.width + dx;
      if (corner.includes("s")) height = original.height + dy;
      if (corner.includes("w")) { x = original.x + dx; width = original.width - dx; }
      if (corner.includes("n")) { y = original.y + dy; height = original.height - dy; }
      el.x = x; el.y = y; el.width = width; el.height = height; clampElement(el);
      node.style.left = el.x + "%"; node.style.top = el.y + "%";
      node.style.width = el.width + "%"; node.style.height = el.height + "%";
      syncPropertyInputs(el);
    };
    const up = e => {
      if (e.pointerId !== event.pointerId) return;
      handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); handle.removeEventListener("pointercancel", up);
      renderAll();
    };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up); handle.addEventListener("pointercancel", up);
  }
  function renderLayers() {
    if (!root) return;
    const host = root.querySelector("[data-layers]");
    root.querySelector("[data-layer-count]").textContent = current.elements.length + " طبقة";
    host.replaceChildren();
    [...current.elements].reverse().forEach(el => {
      const row = document.createElement("div"); row.className = "welcome-editor-layer" + (el.id === selectedId ? " is-active" : "");
      const select = document.createElement("button"); select.type = "button"; select.className = "welcome-editor-layer-select"; select.textContent = el.label || labelFor(el.type);
      select.addEventListener("click", () => { selectedId = el.id; renderAll(); });
      const eye = document.createElement("button"); eye.type = "button"; eye.className = "welcome-editor-layer-icon"; eye.textContent = el.visible ? "◉" : "○"; eye.title = el.visible ? "إخفاء العنصر" : "إظهار العنصر"; eye.setAttribute("aria-label", eye.title);
      eye.addEventListener("click", () => { checkpoint(); el.visible = !el.visible; selectedId = el.id; renderAll(); });
      const lock = document.createElement("button"); lock.type = "button"; lock.className = "welcome-editor-layer-icon"; lock.textContent = el.locked ? "🔒" : "⌁"; lock.title = el.locked ? "فك القفل" : "قفل العنصر"; lock.setAttribute("aria-label", lock.title);
      lock.addEventListener("click", () => { checkpoint(); el.locked = !el.locked; selectedId = el.id; renderAll(); });
      const up = document.createElement("button"); up.type = "button"; up.className = "welcome-editor-layer-icon"; up.textContent = "↑"; up.title = "رفع الطبقة"; up.setAttribute("aria-label", up.title);
      up.addEventListener("click", () => reorder(el.id, 1));
      const down = document.createElement("button"); down.type = "button"; down.className = "welcome-editor-layer-icon"; down.textContent = "↓"; down.title = "خفض الطبقة"; down.setAttribute("aria-label", down.title);
      down.addEventListener("click", () => reorder(el.id, -1));
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "welcome-editor-layer-icon danger"; remove.textContent = "×"; remove.title = "حذف العنصر"; remove.setAttribute("aria-label", remove.title);
      remove.addEventListener("click", () => { checkpoint(); current.elements = current.elements.filter(item => item.id !== el.id); selectedId = current.elements[0]?.id || null; renderAll(); });
      row.append(select, eye, lock, up, down, remove); host.append(row);
    });
  }
  function reorder(id, direction) {
    const index = current.elements.findIndex(e => e.id === id), next = index + direction;
    if (index < 0 || next < 0 || next >= current.elements.length) return;
    checkpoint(); const [item] = current.elements.splice(index, 1); current.elements.splice(next, 0, item); selectedId = id; renderAll();
  }
  function propertyField(label, key, value, type = "number", attrs = {}) {
    const wrap = document.createElement("label"); wrap.className = "welcome-editor-property";
    const caption = document.createElement("span"); caption.textContent = label;
    let input;
    if (type === "textarea") { input = document.createElement("textarea"); input.rows = 2; }
    else if (type === "select") { input = document.createElement("select"); (attrs.options || []).forEach(([v,t]) => { const op = document.createElement("option"); op.value = v; op.textContent = t; input.append(op); }); }
    else { input = document.createElement("input"); input.type = type; }
    input.dataset.property = key; if (type === "checkbox") input.checked = Boolean(value); else if (value !== undefined && value !== null) input.value = value;
    for (const [name,v] of Object.entries(attrs)) if (name !== "options") input.setAttribute(name, String(v));
    input.addEventListener(type === "checkbox" ? "change" : "input", () => {
      const el = findElement(selectedId); if (!el) return;
      let next = type === "checkbox" ? input.checked : input.value;
      checkpoint();
      if (["x","y","width","height","fontSize","opacity","rotation"].includes(key)) next = Number(next);
      if (key === "label") next = String(next).slice(0,40);
      if (key === "text") next = String(next).slice(0,200);
      el[key] = next; clampElement(el);
      const output = root.querySelector('[data-element-id="' + CSS.escape(el.id) + '"]');
      if (output && ["x","y","width","height","fontSize","opacity","rotation","color","align","text","visible"].includes(key)) renderStage();
      else renderStage();
      renderLayers(); root.querySelector("[data-selected-name]").textContent = el.label;
    });
    wrap.append(caption,input); return wrap;
  }
  function renderProperties() {
    if (!root) return;
    const host = root.querySelector("[data-properties]"); host.replaceChildren();
    const el = findElement(selectedId);
    root.querySelector("[data-selected-name]").textContent = el?.label || "لم يتم تحديد عنصر";
    if (!el) { host.textContent = "حدد طبقة من القائمة أو اضغط على عنصر في مساحة التصميم."; return; }
    host.append(propertyField("اسم الطبقة","label",el.label,"text",{maxlength:40}));
    if (["text","title","name","server","count"].includes(el.type)) host.append(propertyField("النص","text",el.text || "","textarea",{maxlength:200}));
    host.append(propertyField("لون العنصر","color",el.color,"color"));
    host.append(propertyField("المحور الأفقي (%)","x",el.x,"number",{min:0,max:98,step:.5}), propertyField("المحور العمودي (%)","y",el.y,"number",{min:0,max:98,step:.5}));
    host.append(propertyField("العرض (%)","width",el.width,"number",{min:2,max:100,step:.5}), propertyField("الارتفاع (%)","height",el.height,"number",{min:1,max:100,step:.5}));
    if (["text","title","name","server","count"].includes(el.type)) {
      host.append(
        propertyField("حجم الخط","fontSize",el.fontSize,"number",{min:8,max:160,step:1}),
        propertyField("نوع الخط","fontFamily",el.fontFamily || "sans","select",{options:[
          ["sans","عربي واضح (Sans)"],["serif","كلاسيكي (Serif)"],["mono","تقني (Mono)"],["condensed","مضغوط (Condensed)"]
        ]}),
        propertyField("سماكة الخط","fontWeight",el.fontWeight || "normal","select",{options:[["normal","عادي"],["bold","عريض / Bold"]]}),
        propertyField("ميل الخط","fontStyle",el.fontStyle || "normal","select",{options:[["normal","مستقيم"],["italic","مائل"]]}),
        propertyField("تنسيق الإنجليزية","textTransform",el.textTransform || "none","select",{options:[
          ["none","كما كُتب"],["uppercase","أحرف كبيرة"],["lowercase","أحرف صغيرة"],["capitalize","أول حرف كبير"]
        ]}),
        propertyField("محاذاة النص","align",el.align,"select",{options:[["right","يمين"],["center","وسط"],["left","يسار"]]})
      );
    }
    host.append(propertyField("الشفافية (%)","opacity",el.opacity,"number",{min:0,max:100,step:1}), propertyField("الدوران (درجة)","rotation",el.rotation,"number",{min:-180,max:180,step:1}));
    const vis = propertyField("إظهار العنصر","visible",el.visible,"checkbox");
    host.append(vis);
    host.append(propertyField("قفل التحريك","locked",el.locked,"checkbox"));
    root.querySelector("[data-selected-name]").textContent = el.label;
  }
  function syncPropertyInputs(el) {
    if (!root) return;
    root.querySelectorAll("[data-property]").forEach(input => {
      const key = input.dataset.property;
      if (Object.prototype.hasOwnProperty.call(el,key)) { if (input.type === "checkbox") input.checked = Boolean(el[key]); else input.value = el[key]; }
    });
  }
  window.PRIMEWelcomeEditor = { open, close, guildId:null, csrfToken:"", cacheKey:5 };
})();