(() => {
  "use strict";
  const DEFAULTS = {
    avatar_x: 14, avatar_y: 50, name_x: 94, name_y: 38, title_y: 20,
    server_y: 60, count_y: 75, accent: "#7c3aed",
    background_start: "#040a16", background_end: "#194876",
    show_server: true, show_count: true,
  };
  let root = null, current = null;
  const clamp = (v, min, max) => Math.max(min, Math.min(max, Number(v) || min));
  const esc = (s) => String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
  function close() { if (root) root.remove(); root = null; current = null; }
  function open({ config = {}, onSave } = {}) {
    close();
    current = { ...DEFAULTS, ...(config && typeof config === "object" ? config : {}) };
    const modal = document.createElement("div");
    modal.className = "welcome-editor-backdrop";
    modal.innerHTML = `
      <section class="welcome-editor-modal" role="dialog" aria-modal="true" aria-labelledby="welcome-editor-heading">
        <header class="welcome-editor-header">
          <div><span class="welcome-editor-kicker">PRIME • WELCOME STUDIO</span><h2 id="welcome-editor-heading">محرر صورة الترحيب</h2><p>عدّل التوزيع والألوان وشاهد النتيجة قبل الحفظ.</p></div>
          <button type="button" class="welcome-editor-close" aria-label="إغلاق">×</button>
        </header>
        <div class="welcome-editor-workspace">
          <div class="welcome-editor-preview-wrap">
            <div class="welcome-editor-preview" id="welcome-editor-preview">
              <div class="welcome-editor-avatar">P</div>
              <div class="welcome-editor-copy">
                <div class="welcome-editor-title">أهلاً بك في PRIME</div>
                <div class="welcome-editor-name">اسم العضو</div>
                <div class="welcome-editor-server">اسم السيرفر</div>
                <div class="welcome-editor-count">العضو رقم 128</div>
              </div>
              <span class="welcome-editor-watermark">PRIME</span>
            </div>
            <p class="welcome-editor-note">المعاينة تقريبية؛ الصورة النهائية تُنشأ بواسطة البوت عند وصول العضو.</p>
          </div>
          <div class="welcome-editor-controls">
            <div class="welcome-editor-control-grid">
              <label>لون الإطار والنص <input type="color" data-color="accent"></label>
              <label>بداية الخلفية <input type="color" data-color="background_start"></label>
              <label>نهاية الخلفية <input type="color" data-color="background_end"></label>
            </div>
            <div class="welcome-editor-sliders">
              <label>موقع الصورة أفقيًا <output data-value="avatar_x"></output><input type="range" min="12" max="35" step="1" data-range="avatar_x"></label>
              <label>موقع الصورة عموديًا <output data-value="avatar_y"></output><input type="range" min="25" max="75" step="1" data-range="avatar_y"></label>
              <label>موقع النص أفقيًا <output data-value="name_x"></output><input type="range" min="45" max="96" step="1" data-range="name_x"></label>
              <label>ارتفاع اسم العضو <output data-value="name_y"></output><input type="range" min="25" max="60" step="1" data-range="name_y"></label>
              <label>ارتفاع العنوان <output data-value="title_y"></output><input type="range" min="8" max="30" step="1" data-range="title_y"></label>
              <label>ارتفاع اسم السيرفر <output data-value="server_y"></output><input type="range" min="45" max="78" step="1" data-range="server_y"></label>
              <label>ارتفاع رقم العضو <output data-value="count_y"></output><input type="range" min="60" max="90" step="1" data-range="count_y"></label>
            </div>
            <div class="welcome-editor-toggles">
              <label><input type="checkbox" data-toggle="show_server"> إظهار اسم السيرفر</label>
              <label><input type="checkbox" data-toggle="show_count"> إظهار رقم العضو</label>
            </div>
            <div class="welcome-editor-actions">
              <button type="button" class="btn" data-action="reset">استعادة الافتراضي</button>
              <button type="button" class="btn primary" data-action="save">حفظ التصميم</button>
            </div>
            <p class="welcome-editor-status" role="status" aria-live="polite"></p>
          </div>
        </div>
      </section>`;
    root = modal;
    document.body.append(modal);
    modal.querySelector(".welcome-editor-close").addEventListener("click", close);
    modal.addEventListener("click", e => { if (e.target === modal) close(); });
    document.addEventListener("keydown", onEscape);
    modal.querySelectorAll("[data-range]").forEach(input => input.addEventListener("input", () => {
      current[input.dataset.range] = clamp(input.value, Number(input.min), Number(input.max));
      updatePreview();
    }));
    modal.querySelectorAll("[data-color]").forEach(input => input.addEventListener("input", () => {
      current[input.dataset.color] = input.value;
      updatePreview();
    }));
    modal.querySelectorAll("[data-toggle]").forEach(input => input.addEventListener("change", () => {
      current[input.dataset.toggle] = input.checked;
      updatePreview();
    }));
    modal.querySelector('[data-action="reset"]').addEventListener("click", () => {
      current = { ...DEFAULTS }; syncControls(); updatePreview();
    });
    modal.querySelector('[data-action="save"]').addEventListener("click", async () => {
      const button = modal.querySelector('[data-action="save"]');
      const status = modal.querySelector(".welcome-editor-status");
      button.disabled = true; status.textContent = "جارٍ حفظ التصميم…";
      try {
        const ok = await onSave({ ...current });
        if (ok) status.textContent = "تم حفظ التصميم.";
        else status.textContent = "تعذر الحفظ. راجع اتصال اللوحة ثم حاول مجددًا.";
      } catch (err) {
        status.textContent = "تعذر الحفظ. حاول مرة أخرى.";
      } finally { if (button.isConnected) button.disabled = false; }
    });
    syncControls(); updatePreview();
    modal.querySelector(".welcome-editor-close").focus();
  }
  function onEscape(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", onEscape); } }
  function syncControls() {
    if (!root || !current) return;
    root.querySelectorAll("[data-range]").forEach(input => input.value = current[input.dataset.range]);
    root.querySelectorAll("[data-color]").forEach(input => input.value = current[input.dataset.color]);
    root.querySelectorAll("[data-toggle]").forEach(input => input.checked = Boolean(current[input.dataset.toggle]));
  }
  function updatePreview() {
    if (!root || !current) return;
    const preview = root.querySelector("#welcome-editor-preview");
    preview.style.background = `linear-gradient(115deg, ${current.background_start}, ${current.background_end})`;
    preview.style.setProperty("--welcome-accent", current.accent);
    const avatar = preview.querySelector(".welcome-editor-avatar");
    avatar.style.left = current.avatar_x + "%"; avatar.style.top = current.avatar_y + "%";
    const copy = preview.querySelector(".welcome-editor-copy");
    copy.style.left = "0";
    copy.querySelectorAll(".welcome-editor-title,.welcome-editor-name,.welcome-editor-server,.welcome-editor-count").forEach(node => node.style.right = (100 - current.name_x) + "%");
    const title = preview.querySelector(".welcome-editor-title"); title.style.top = current.title_y + "%";
    const name = preview.querySelector(".welcome-editor-name"); name.style.top = current.name_y + "%";
    const server = preview.querySelector(".welcome-editor-server"); server.style.top = current.server_y + "%"; server.hidden = !current.show_server;
    const count = preview.querySelector(".welcome-editor-count"); count.style.top = current.count_y + "%"; count.hidden = !current.show_count;
    root.querySelectorAll("[data-value]").forEach(out => out.textContent = current[out.dataset.value] + "%");
  }
  window.PRIMEWelcomeEditor = { open, close };
})();