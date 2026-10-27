(() => {
  "use strict";

  const DEFAULTS = {
    avatar_x: 14, avatar_y: 50, name_x: 94, name_y: 38,
    title_y: 20, server_y: 60, count_y: 75,
    accent: "#7c3aed", background_start: "#040a16",
    background_end: "#194876", show_server: true, show_count: true,
    use_custom_background: false,
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
  const clamp = (v, min, max) => Math.max(min, Math.min(max, Number(v) || 0));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
  const uid = () => "layer-" + Math.random().toString(36).slice(2, 9);
  const findElement = id => current?.elements?.find(item => item.id === id);
  const labelFor = type => ({avatar:"صورة العضو",title:"عنوان الترحيب",name:"اسم العضو",server:"اسم السيرفر",count:"رقم العضو",text:"نص مخصص",shape:"شكل",line:"خط",frame:"إطار التصميم"}[type] || "عنصر");

  function normalize(config) {
    const base = { ...DEFAULTS, ...(config && typeof config === "object" ? config : {}) };
    if (Array.isArray(base.elements) && base.elements.length) {
      base.elements = base.elements.slice(0, 24).map((e, i) => ({
        id: String(e.id || uid()), type: ["avatar","title","name","server","count","text","shape","line","frame"].includes(e.type) ? e.type : "text",
        label: String(e.label || labelFor(e.type)), x: clamp(e.x, 0, 100), y: clamp(e.y, 0, 100),
        width: clamp(e.width ?? 30, 2, 100), height: clamp(e.height ?? 10, 2, 100),
        fontSize: clamp(e.fontSize ?? 24, 8, 160), color: /^#[0-9a-f]{6}$/i.test(e.color || "") ? e.color : base.accent,
        visible: e.visible !== false, locked: e.locked === true, opacity: clamp(e.opacity ?? 100, 0, 100),
        rotation: clamp(e.rotation ?? 0, -180, 180), align: ["left","center","right"].includes(e.align) ? e.align : "left",
        text: String(e.text ?? "").slice(0, 200),
      }));
    } else {
      base.elements = BASE_ELEMENTS.map(e => ({ ...e }));
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
    if (root) root.remove();
    document.removeEventListener("keydown", onEscape);
    root = null; current = null; onSaveCallback = null;
  }

  function open({ config = {}, onSave } = {}) {
    close();
    current = normalize(config); selectedId = current.elements.some(e => e.id === "name") ? "name" : current.elements[0]?.id;
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
              <span class="welcome-editor-upload-hint">PNG / JPG / WEBP — حتى 5MB</span>
              <div class="welcome-editor-upload-actions"><button type="button" class="btn" data-action="upload-background">رفع الخلفية</button><button type="button" class="btn" data-action="clear-background">إزالة الخلفية المخصصة</button></div>
            </div>
            <div class="welcome-editor-background-colors">
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
      current = normalize({ ...DEFAULTS, accent: current.accent, background_start: current.background_start, background_end: current.background_end });
      selectedId = "name"; renderAll(); setStatus("تمت استعادة العناصر الافتراضية. اضغط حفظ لتثبيت التغيير.");
    });
    modal.querySelectorAll("[data-config-color]").forEach(input => input.addEventListener("input", () => {
      current[input.dataset.configColor] = input.value;
      renderStage(); updateConfigColorFields();
    }));
    const fileInput = modal.querySelector("#welcome-editor-background-file");
    modal.querySelector('[data-action="upload-background"]').addEventListener("click", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return setStatus("اختر صورة أولاً.");
      if (!["image/png","image/jpeg","image/webp"].includes(file.type)) return setStatus("الصيغ المدعومة PNG وJPG وWEBP فقط.");
      if (file.size > 5 * 1024 * 1024) return setStatus("الحد الأقصى لحجم الخلفية 5 ميجابايت.");
      const button = modal.querySelector('[data-action="upload-background"]');
      button.disabled = true; setStatus("جارٍ رفع الخلفية والتحقق منها…");
      try {
        const form = new FormData(); form.append("file", file);
        const response = await fetch("api/guild/" + encodeURIComponent(window.PRIMEWelcomeEditor.guildId) + "/onboarding/welcome-background", {
          method:"POST", headers:{"X-CSRF-Token":window.PRIMEWelcomeEditor.csrfToken || ""}, body:form, credentials:"same-origin"
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) return setStatus(result.fields?.file || "تعذر رفع الخلفية والتحقق منها.");
        current.use_custom_background = true; renderStage(); setStatus("تم رفع الخلفية. اضغط حفظ التصميم لتطبيقها.");
      } catch (_) { setStatus("تعذر الاتصال بالخادم أثناء رفع الخلفية."); }
      finally { button.disabled = false; }
    });
    modal.querySelector('[data-action="clear-background"]').addEventListener("click", () => {
      current.use_custom_background = false; renderStage(); setStatus("تم إيقاف الخلفية المخصصة. احفظ التصميم لتطبيق التغيير.");
    });
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
    modal.querySelector(".welcome-editor-close").focus();
  }

  function onEscape(e) { if (e.key === "Escape") close(); }
  function setStatus(message) { if (root) root.querySelector(".welcome-editor-status").textContent = message; }
  function updateConfigColorFields() {
    if (!root) return;
    root.querySelectorAll("[data-config-color]").forEach(input => input.value = current[input.dataset.configColor] || "#7c3aed");
  }
  function syncLegacyFields() {
    const get = id => findElement(id);
    const avatar = get("avatar"), title = get("title"), name = get("name"), server = get("server"), count = get("count");
    if (avatar) { current.avatar_x = Number((avatar.x + avatar.width / 2).toFixed(1)); current.avatar_y = Number((avatar.y + avatar.height / 2).toFixed(1)); }
    if (title) current.title_y = Number(title.y.toFixed(1));
    if (name) { current.name_x = Number((name.x + name.width).toFixed(1)); current.name_y = Number((name.y + name.height / 2).toFixed(1)); }
    if (server) { current.server_y = Number((server.y + server.height / 2).toFixed(1)); current.show_server = server.visible; }
    if (count) { current.count_y = Number((count.y + count.height / 2).toFixed(1)); current.show_count = count.visible; }
  }
  function addElement(type) {
    if (current.elements.length >= 24) return setStatus("الحد الأقصى 24 طبقة في التصميم.");
    const id = uid(), el = {
      id, type, label: labelFor(type), x: type === "line" ? 35 : 38, y: 42 + (current.elements.length % 4) * 5,
      width: type === "shape" ? 18 : type === "line" ? 30 : 24, height: type === "shape" ? 12 : type === "line" ? 1.5 : 10,
      fontSize: 26, color: current.accent || "#a78bfa", visible: true, locked: false, opacity: 100, rotation: 0, align: "left",
      text: type === "text" ? "اكتب النص هنا" : "",
    };
    current.elements.push(el); selectedId = id; renderAll(); setStatus("أُضيف عنصر جديد.");
  }
  function renderAll() { renderStage(); renderLayers(); renderProperties(); updateConfigColorFields(); }
  function renderStage() {
    if (!root || !current) return;
    const stage = root.querySelector("#welcome-editor-preview");
    stage.style.setProperty("--welcome-accent", current.accent);
    if (current.use_custom_background) {
      stage.style.backgroundImage = "url(api/guild/" + encodeURIComponent(window.PRIMEWelcomeEditor.guildId) + "/onboarding/welcome-background?v=" + (window.PRIMEWelcomeEditor.cacheKey || Date.now()) + ")";
      stage.style.backgroundSize = "cover"; stage.style.backgroundPosition = "center";
    } else {
      stage.style.backgroundImage = "linear-gradient(115deg," + current.background_start + "," + current.background_end + ")";
    }
    stage.replaceChildren();
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
      node.style.justifyContent = el.align === "right" ? "flex-end" : (el.align === "center" ? "center" : "flex-start");
      node.hidden = !el.visible;
      if (el.type === "avatar") node.innerHTML = '<span class="welcome-editor-avatar-placeholder">P</span>';
      else if (el.type === "shape") node.innerHTML = '<span class="welcome-editor-shape-fill"></span>';
      else if (el.type === "line") node.innerHTML = '<span class="welcome-editor-line-fill"></span>';
      else if (el.type === "frame") node.setAttribute("aria-label", "إطار التصميم");
      else node.textContent = displayText(el);
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
    try { node.setPointerCapture(e.pointerId); } catch (_) {}
    const move = ev => {
      if (ev.pointerId !== e.pointerId) return;
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
      eye.addEventListener("click", () => { el.visible = !el.visible; selectedId = el.id; renderAll(); });
      const lock = document.createElement("button"); lock.type = "button"; lock.className = "welcome-editor-layer-icon"; lock.textContent = el.locked ? "🔒" : "⌁"; lock.title = el.locked ? "فك القفل" : "قفل العنصر"; lock.setAttribute("aria-label", lock.title);
      lock.addEventListener("click", () => { el.locked = !el.locked; selectedId = el.id; renderAll(); });
      const up = document.createElement("button"); up.type = "button"; up.className = "welcome-editor-layer-icon"; up.textContent = "↑"; up.title = "رفع الطبقة"; up.setAttribute("aria-label", up.title);
      up.addEventListener("click", () => reorder(el.id, 1));
      const down = document.createElement("button"); down.type = "button"; down.className = "welcome-editor-layer-icon"; down.textContent = "↓"; down.title = "خفض الطبقة"; down.setAttribute("aria-label", down.title);
      down.addEventListener("click", () => reorder(el.id, -1));
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "welcome-editor-layer-icon danger"; remove.textContent = "×"; remove.title = "حذف العنصر"; remove.setAttribute("aria-label", remove.title);
      remove.addEventListener("click", () => { current.elements = current.elements.filter(item => item.id !== el.id); selectedId = current.elements[0]?.id || null; renderAll(); });
      row.append(select, eye, lock, up, down, remove); host.append(row);
    });
  }
  function reorder(id, direction) {
    const index = current.elements.findIndex(e => e.id === id), next = index + direction;
    if (index < 0 || next < 0 || next >= current.elements.length) return;
    const [item] = current.elements.splice(index, 1); current.elements.splice(next, 0, item); selectedId = id; renderAll();
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
    if (el.type !== "avatar") host.append(propertyField("لون العنصر","color",el.color,"color"));
    host.append(propertyField("المحور الأفقي (%)","x",el.x,"number",{min:0,max:98,step:.5}), propertyField("المحور العمودي (%)","y",el.y,"number",{min:0,max:98,step:.5}));
    host.append(propertyField("العرض (%)","width",el.width,"number",{min:2,max:100,step:.5}), propertyField("الارتفاع (%)","height",el.height,"number",{min:1,max:100,step:.5}));
    if (["text","title","name","server","count"].includes(el.type)) host.append(propertyField("حجم الخط","fontSize",el.fontSize,"number",{min:8,max:160,step:1}), propertyField("محاذاة النص","align",el.align,"select",{options:[["right","يمين"],["center","وسط"],["left","يسار"]]}));
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