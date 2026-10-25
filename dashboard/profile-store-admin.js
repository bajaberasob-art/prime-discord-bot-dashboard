(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const guildId = Number($('meta[name="prime-store-guild-id"]')?.content || 0);
  const csrf = $('meta[name="prime-store-csrf"]')?.content || "";
  const base = `/api/guild/${guildId}/profile-store`;
  const state = {
    items: [], categories: [], settings: {}, selectedItemId: null, selectedCategoryId: null,
    itemFilter: "all", search: "", view: "products", busy: false, selectedFile: null, clearAsset: false
  };
  const DEFAULT_CATEGORIES = new Set(["backgrounds","templates","banners","stickers","flags","colors","shapes","club-logos"]);
  const TYPE_LABELS = {
    background:"خلفية أو بانر", card_template:"قالب بروفايل", badge:"استكر أو شارة",
    flag:"علم", card_color:"لون بطاقة", shape:"شكل أو إطار", club_logo:"شعار نادٍ"
  };
  const RARITY_LABELS = {common:"عادي",silver:"فضي",gold:"ذهبي",legendary:"أسطوري"};
  const $error = (id, message = "") => { const el = $("#"+id); el.textContent = message; el.hidden = !message; };
  function toast(message, type = "success") {
    const node = document.createElement("div");
    node.className = "toast" + (type === "error" ? " error" : "");
    node.textContent = message;
    $("#toastRegion").append(node);
    window.setTimeout(() => node.remove(), 4500);
  }
  function notice(message, type = "info") {
    const node = $("#notice"); node.textContent = message || ""; node.hidden = !message;
    node.classList.toggle("error", type === "error");
  }
  function describeError(data, status) {
    const code = data?.error || "request_failed";
    const messages = {
      unauthorized:"انتهت جلسة الداشبورد؛ سجّل الدخول مجددًا.",
      forbidden:"لا تملك صلاحية إدارة متجر PRIME في هذا الحساب.",
      profile_store_owner_required:"إدارة الكتالوج متاحة لمالك البوت أو المسؤولين المحددين فقط.",
      csrf:"انتهت صلاحية الحماية؛ حدّث الصفحة وحاول مجددًا.",
      duplicate_slug:"المعرّف التقني مستخدم بالفعل. اختر معرّفًا آخر.",
      duplicate_category_slug:"معرّف القسم مستخدم بالفعل.",
      category_not_found:"القسم غير موجود أو تم حذفه.",
      item_not_found:"المنتج غير موجود.",
      item_type_does_not_accept_image:"هذا النوع لا يقبل صورة مرفوعة.",
      item_type_does_not_accept_image:"هذا النوع لا يقبل صورة مرفوعة.",
      image_too_large:"حجم الصورة أكبر من الحد المسموح (5 ميجابايت).",
      invalid_image_file:"تعذر قراءة الصورة. تأكد أنها PNG أو JPG أو WebP.",
      invalid_image_format:"صيغة الصورة غير مدعومة.",
      animated_images_not_allowed:"ارفع صورة ثابتة؛ الصور المتحركة غير مدعومة حاليًا.",
      image_pixel_limit:"أبعاد الصورة أكبر من الحد الآمن.",
      asset_upload_failed:"تعذر رفع الصورة؛ لم يتم تأكيد الرفع.",
      asset_storage_failed:"تعذر تخزين الصورة على الخادم.",
      default_category_cannot_be_deleted:"القسم الأساسي لا يُحذف؛ يمكنك إخفاؤه.",
      duplicate_slug:"المعرّف التقني موجود بالفعل."
    };
    return messages[code] || data?.message || `تعذر إتمام العملية (HTTP ${status || "?"}, ${code}).`;
  }
  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    const init = { method, headers, credentials: "same-origin", cache: "no-store" };
    if (method !== "GET" && method !== "HEAD") {
      headers["X-CSRF-Token"] = csrf;
      if (options.body instanceof FormData) init.body = options.body;
      else { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(options.body || {}); }
    }
    let response;
    try { response = await fetch(path, init); }
    catch (_) { throw new Error("تعذر الاتصال بالخادم؛ تحقق من الإنترنت وحاول مجددًا."); }
    const data = (headers.Accept?.includes("application/json")) ? await response.json().catch(() => ({})) : {};
    if (!response.ok) throw new Error(describeError(data, response.status));
    return data;
  }
  function slugify(value) {
    return String(value || "").toLowerCase().trim()
      .replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
  }
  function typeCategory(type) {
    return ({background:"backgrounds",card_template:"templates",badge:"stickers",flag:"flags",
      card_color:"colors",shape:"shapes",club_logo:"club-logos"})[type] || "backgrounds";
  }
  function currency(value) { return value === "gem" ? "بلورات PRIME" : "عملات PRIME"; }
  function fmt(value) { return Number(value || 0).toLocaleString("en-US"); }
  function setBusy(busy) {
    state.busy = busy;
    ["#saveItemButton","#saveCategoryButton","#saveSettingsButton","#newItemButton","#newCategoryButton"].forEach(sel => {
      const b = $(sel); if (b) b.disabled = busy;
    });
  }
  function renderStats() {
    $("#totalItems").textContent = fmt(state.items.length);
    $("#totalCategories").textContent = fmt(state.categories.filter(item => Number(item.is_active)).length);
    $("#publishedItems").textContent = fmt(state.items.filter(item => Number(item.is_active) && !item.deleted_at).length);
    $("#storeStatus").textContent = Number(state.settings.enabled) ? "يعمل" : "متوقف";
    $("#storeStatus").style.color = Number(state.settings.enabled) ? "var(--green)" : "var(--danger)";
    $("#storeStatusNote").textContent = Number(state.settings.enabled) ? "الشراء والتجهيز متاحان" : "الشراء والتجهيز متوقفان";
    $("#tabItemsCount").textContent = fmt(state.items.length);
    $("#tabCategoriesCount").textContent = fmt(state.categories.length);
    $("#browserCount").textContent = fmt(visibleItems().length);
    $("#categoryBrowserCount").textContent = fmt(state.categories.length);
  }
  function renderCategorySelect(selected = "") {
    const select = $("#itemCategory"); select.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "all"; allOption.textContent = "كل الأقسام"; select.append(allOption);
    const available = state.categories.filter(category => Number(category.is_active) || category.slug === selected);
    available.forEach(category => {
      const option = document.createElement("option");
      option.value = category.slug;
      option.textContent = category.name + (Number(category.is_active) ? "" : " · مخفي");
      select.append(option);
    });
    if (selected && ![...select.options].some(option => option.value === selected)) {
      const missing = document.createElement("option"); missing.value = selected;
      missing.textContent = selected + " · قسم غير معروف"; select.append(missing);
    }
    if (selected && [...select.options].some(option => option.value === selected)) select.value = selected;
    else select.value = available[0]?.slug || "all";
  }
  function assetUrl(item) { return item?.item_id && item?.has_asset ? `/api/guild/${guildId}/profile-store/items/${item.item_id}/asset` : ""; }
  function renderItemThumb(item) {
    const thumb = document.createElement("div"); thumb.className = "item-thumb";
    const src = assetUrl(item);
    if (src) {
      const image = document.createElement("img"); image.src = src; image.alt = ""; image.loading = "lazy";
      image.onerror = () => { image.remove(); thumb.textContent = item.item_type === "card_template" ? "P" : "▧"; };
      thumb.append(image);
    } else thumb.textContent = item.item_type === "card_template" ? "P" : "▧";
    return thumb;
  }
  function visibleItems() {
    const query = state.search.trim().toLocaleLowerCase("ar");
    return state.items.filter(item => {
      if (state.itemFilter === "active" && !Number(item.is_active)) return false;
      if (state.itemFilter === "inactive" && Number(item.is_active)) return false;
      if (query && ![item.name,item.slug,item.description,item.category,item.subcategory].join(" ").toLocaleLowerCase("ar").includes(query)) return false;
      return true;
    }).sort((a,b) => Number(b.is_active)-Number(a.is_active) || Number(b.is_featured)-Number(a.is_featured) || Number(b.item_id)-Number(a.item_id));
  }
  function renderItems() {
    const root = $("#itemList"); root.replaceChildren();
    const items = visibleItems();
    if (!items.length) {
      const empty = document.createElement("div"); empty.className = "empty-inline";
      empty.textContent = state.items.length ? "لا توجد نتائج تطابق البحث." : "لا توجد منتجات بعد. أضف أول منتج من الزر أعلاه.";
      root.append(empty);
    }
    items.forEach(item => {
      const button = document.createElement("button"); button.type = "button";
      button.className = "item-row" + (Number(item.item_id) === state.selectedItemId ? " is-selected" : "");
      button.addEventListener("click", () => editItem(item.item_id));
      button.append(renderItemThumb(item));
      const copy = document.createElement("span"); copy.className = "item-copy";
      const name = document.createElement("strong"); name.textContent = item.name;
      const category = state.categories.find(cat => cat.slug === item.category);
      const small = document.createElement("small"); small.textContent = (category?.name || item.category || "بدون قسم") + " · " + (TYPE_LABELS[item.item_type] || item.item_type);
      copy.append(name, small);
      const meta = document.createElement("span"); meta.className = "item-meta";
      const status = document.createElement("span"); status.className = "tiny-status" + (Number(item.is_active) ? "" : " off"); status.textContent = Number(item.is_active) ? "منشور" : "مخفي";
      const price = document.createElement("span"); price.textContent = Number(item.price) === 0 ? "مجاني" : fmt(item.price) + " " + (item.currency === "gem" ? "◆" : "P");
      meta.append(status, price); button.append(copy, meta); root.append(button);
    });
    $("#browserCount").textContent = fmt(items.length);
  }
  function showPlaceholder() {
    $("#assetPreview").replaceChildren();
    const placeholder = document.createElement("div"); placeholder.className = "asset-placeholder";
    const glyph = document.createElement("span"); glyph.textContent = "＋";
    const title = document.createElement("b"); title.textContent = "معاينة الصورة";
    const text = document.createElement("small"); text.textContent = "اختر ملفًا أو احفظ المنتج مع صورة";
    placeholder.append(glyph,title,text); $("#assetPreview").append(placeholder);
  }
  function setAssetPreview(src) {
    $("#assetPreview").replaceChildren();
    if (!src) { showPlaceholder(); return; }
    const image = document.createElement("img"); image.src = src; image.alt = "معاينة صورة المنتج";
    image.onerror = () => showPlaceholder();
    $("#assetPreview").append(image);
  }
  function clearItemForm() {
    state.selectedItemId = null; state.selectedFile = null; state.clearAsset = false;
    $("#itemForm").reset(); $("#itemId").value = ""; $("#itemPrice").value = "100";
    $("#itemSlug").dataset.touched = "";
    $("#itemActive").checked = true; $("#itemFeatured").checked = false;
    $("#itemAccent").value = "#6CDFFF"; $("#itemAccent2").value = "#D9F8FF";
    $("#itemDivider").value = "#D9F8FF"; $("#itemXp").value = "#6CDFFF";
    $("#itemDeep").value = "#071725"; $("#itemGlow").value = "#8AEAFF";
    $("#itemStyleJson").value = "{}"; $("#itemMetadataJson").value = "{}";
    $("#itemFormTitle").textContent = "إضافة منتج جديد"; $("#itemIdLabel").textContent = "منتج جديد";
    $("#deleteItemButton").hidden = true; $("#itemFile").value = ""; $("#assetInfo").textContent = "لم يتم إرفاق صورة بعد.";
    renderCategorySelect(typeCategory($("#itemType").value));
    $("#itemType").value = "background"; $("#itemRarity").value = "common";
    $("#priceMode").value = "paid"; $("#itemPrice").disabled = false; $("#itemPrice").min = "1";
    $error("itemFormError"); showPlaceholder(); renderItems();
  }
  function fillItem(item) {
    state.selectedItemId = Number(item.item_id); state.selectedFile = null; state.clearAsset = false;
    $("#itemId").value = String(item.item_id); $("#itemName").value = item.name || "";
    $("#itemSlug").value = item.slug || ""; $("#itemDescription").value = item.description || "";
    $("#itemType").value = item.item_type || "background";
    renderCategorySelect(item.category || typeCategory(item.item_type));
    $("#itemSubcategory").value = item.subcategory || "";
    $("#itemRarity").value = item.rarity || "common";
    const style = item.style && typeof item.style === "object" ? item.style : {};
    $("#itemAccent").value = /^#[0-9a-f]{6}$/i.test(style.accent || "") ? style.accent : "#6CDFFF";
    $("#itemAccent2").value = /^#[0-9a-f]{6}$/i.test(style.accent2 || "") ? style.accent2 : "#D9F8FF";
    $("#itemDivider").value = /^#[0-9a-f]{6}$/i.test(style.divider || "") ? style.divider : $("#itemAccent2").value;
    $("#itemXp").value = /^#[0-9a-f]{6}$/i.test(style.xp || "") ? style.xp : $("#itemAccent").value;
    $("#itemDeep").value = /^#[0-9a-f]{6}$/i.test(style.deep || "") ? style.deep : "#071725";
    $("#itemGlow").value = /^#[0-9a-f]{6}$/i.test(style.glow || "") ? style.glow : $("#itemAccent2").value;
    $("#itemStyleJson").value = JSON.stringify(style, null, 2);
    $("#itemMetadataJson").value = JSON.stringify(item.metadata && typeof item.metadata === "object" ? item.metadata : {}, null, 2);
    const price = Number(item.price || 0);
    $("#priceMode").value = price === 0 ? "free" : "paid";
    $("#itemPrice").value = price === 0 ? "100" : String(price);
    $("#itemPrice").disabled = price === 0; $("#itemPrice").min = "1";
    $("#itemCurrency").value = item.currency || "moon";
    $("#itemActive").checked = Boolean(Number(item.is_active));
    $("#itemFeatured").checked = Boolean(Number(item.is_featured));
    $("#itemFormTitle").textContent = "تعديل المنتج";
    $("#itemIdLabel").textContent = "ID / " + item.item_id;
    $("#deleteItemButton").hidden = false; $("#itemFile").value = "";
    $("#assetInfo").textContent = item.has_asset ? "الصورة الحالية: " + (item.asset_name || "مرفقة") + " · يمكنك اختيار صورة جديدة لاستبدالها." : "لا توجد صورة مرفقة؛ يمكنك رفع صورة الآن.";
    setAssetPreview(assetUrl(item)); $error("itemFormError"); renderItems();
  }
  function editItem(id) {
    const item = state.items.find(value => Number(value.item_id) === Number(id));
    if (item) fillItem(item);
  }
  function itemPayload() {
    const priceMode = $("#priceMode").value;
    const price = priceMode === "free" ? 0 : Number($("#itemPrice").value);
    if (priceMode === "paid" && (!Number.isSafeInteger(price) || price < 1 || price > 1000000000)) {
      throw new Error("أدخل سعرًا صحيحًا أكبر من صفر أو اختر «مجاني».");
    }
    let style, metadata;
    try {
      style = JSON.parse($("#itemStyleJson").value || "{}");
      metadata = JSON.parse($("#itemMetadataJson").value || "{}");
    } catch (_) {
      throw new Error("صيغة JSON غير صحيحة في إعدادات الستايل أو تخطيط القالب.");
    }
    if (!style || typeof style !== "object" || Array.isArray(style)) throw new Error("بيانات الستايل يجب أن تكون JSON من نوع كائن.");
    if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) throw new Error("بيانات التخطيط يجب أن تكون JSON من نوع كائن.");
    style = { ...style };
    Object.assign(style, {
      accent: $("#itemAccent").value, accent2: $("#itemAccent2").value,
      divider: $("#itemDivider").value, xp: $("#itemXp").value,
      deep: $("#itemDeep").value, glow: $("#itemGlow").value
    });
    const payload = {
      slug: $("#itemSlug").value.trim().toLowerCase(),
      name: $("#itemName").value.trim(),
      description: $("#itemDescription").value.trim(),
      item_type: $("#itemType").value,
      category: $("#itemCategory").value || "all",
      subcategory: $("#itemSubcategory").value.trim(),
      rarity: $("#itemRarity").value,
      price,
      currency: $("#itemCurrency").value,
      style, metadata,
      is_active: $("#itemActive").checked,
      is_featured: $("#itemFeatured").checked
    };
    if (!payload.slug || !payload.name) throw new Error("اسم المنتج والمعرّف التقني مطلوبان.");
    return payload;
  }
  async function uploadSelectedImage(itemId) {
    if (!state.selectedFile) return;
    const form = new FormData(); form.append("file", state.selectedFile);
    const result = await api(`${base}/items/${itemId}/asset`, {method:"POST",body:form});
    return result;
  }
  async function saveItem(event) {
    event.preventDefault(); if (state.busy) return;
    $error("itemFormError");
    let payload;
    try { payload = itemPayload(); }
    catch(error) { $error("itemFormError", error.message); return; }
    setBusy(true);
    const wasNew = !state.selectedItemId;
    try {
      let result;
      if (wasNew) {
        result = await api(base + "/items", {method:"POST",body:payload});
        state.selectedItemId = Number(result.item.item_id);
      } else {
        result = await api(base + "/items/" + state.selectedItemId, {method:"PUT",body:payload});
      }
      if (state.selectedFile) {
        try { await uploadSelectedImage(state.selectedItemId); }
        catch(error) {
          await loadData(false);
          fillItem(state.items.find(item => Number(item.item_id) === state.selectedItemId) || result.item);
          $error("itemFormError", "تم حفظ بيانات المنتج، لكن فشل رفع الصورة: " + error.message + " اختر الملف وحاول حفظه مرة أخرى.");
          return;
        }
      }
      await loadData(false);
      const saved = state.items.find(item => Number(item.item_id) === state.selectedItemId);
      if (saved) fillItem(saved);
      toast(wasNew ? "تم إنشاء المنتج." : "تم حفظ تعديلات المنتج.");
      notice("تم حفظ المنتج. إذا كان منشورًا فسيظهر في المتجر ضمن القسم المحدد.");
    } catch(error) { $error("itemFormError", error.message); }
    finally { setBusy(false); }
  }
  async function deleteItem() {
    if (!state.selectedItemId || state.busy) return;
    const item = state.items.find(value => Number(value.item_id) === state.selectedItemId);
    if (!item || !window.confirm(`تأكيد حذف «${item.name}»؟ سيُخفى المنتج من المتجر ولن يُحذف من سجلات المشتريات القديمة.`)) return;
    setBusy(true);
    try {
      await api(base + "/items/" + state.selectedItemId, {method:"DELETE"});
      clearItemForm(); await loadData(false); toast("تم إخفاء المنتج من المتجر.");
    } catch(error) { $error("itemFormError", error.message); }
    finally { setBusy(false); }
  }
  function renderCategories() {
    const root = $("#categoryList"); root.replaceChildren();
    if (!state.categories.length) {
      const empty = document.createElement("div"); empty.className = "empty-inline"; empty.textContent = "لا توجد أقسام بعد."; root.append(empty);
    }
    state.categories.forEach(category => {
      const row = document.createElement("div"); row.className = "category-row";
      const icon = document.createElement("div"); icon.className = "category-icon"; icon.textContent = category.icon || "▦";
      const copy = document.createElement("div"); copy.className = "category-copy";
      const name = document.createElement("strong"); name.textContent = category.name;
      const details = document.createElement("small"); details.textContent = category.slug + (category.description ? " · " + category.description : "");
      const status = document.createElement("small"); status.textContent = Number(category.is_active) ? "فعال" : "مخفي";
      copy.append(name,details,status);
      const actions = document.createElement("div"); actions.className = "category-row-actions";
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "mini-button"; edit.textContent = "تعديل";
      edit.addEventListener("click", () => fillCategory(category));
      const del = document.createElement("button"); del.type = "button"; del.className = "mini-button"; del.textContent = "حذف";
      del.disabled = DEFAULT_CATEGORIES.has(category.slug);
      del.title = del.disabled ? "القسم الأساسي يمكن إخفاؤه بدل حذفه" : "حذف القسم ونقل منتجاته إلى كل الأقسام";
      del.addEventListener("click", () => deleteCategory(category));
      actions.append(edit,del); row.append(icon,copy,actions); root.append(row);
    });
  }
  function clearCategoryForm() {
    state.selectedCategoryId = null; $("#categoryForm").reset(); $("#categoryId").value = "";
    $("#categoryFormTitle").textContent = "إضافة قسم"; $("#categoryIcon").value = "▦";
    $("#categoryOrder").value = "100"; $("#categoryActive").checked = true;
    $("#deleteCategoryButton").hidden = true; $("#saveCategoryButton").textContent = "حفظ القسم";
    $error("categoryFormError");
  }
  function fillCategory(category) {
    state.selectedCategoryId = Number(category.category_id);
    $("#categoryId").value = String(category.category_id); $("#categoryName").value = category.name || "";
    $("#categorySlug").value = category.slug || ""; $("#categoryIcon").value = category.icon || "▦";
    $("#categoryDescription").value = category.description || ""; $("#categoryOrder").value = String(category.sort_order ?? 100);
    $("#categoryActive").checked = Boolean(Number(category.is_active));
    $("#categoryFormTitle").textContent = "تعديل القسم"; $("#deleteCategoryButton").hidden = DEFAULT_CATEGORIES.has(category.slug);
    $("#saveCategoryButton").textContent = "حفظ التغييرات"; $error("categoryFormError");
  }
  function categoryPayload() {
    const sort_order = Number($("#categoryOrder").value || 0);
    const payload = {slug:$("#categorySlug").value.trim().toLowerCase(),name:$("#categoryName").value.trim(),
      description:$("#categoryDescription").value.trim(),icon:$("#categoryIcon").value.trim() || "▦",
      sort_order,is_active:$("#categoryActive").checked};
    if (!payload.slug || !payload.name) throw new Error("اسم القسم والمعرّف التقني مطلوبان.");
    if (!Number.isInteger(sort_order) || sort_order < 0 || sort_order > 10000) throw new Error("ترتيب القسم يجب أن يكون رقمًا من 0 إلى 10000.");
    return payload;
  }
  async function saveCategory(event) {
    event.preventDefault(); if (state.busy) return;
    $error("categoryFormError"); let payload;
    try { payload = categoryPayload(); } catch(error) { $error("categoryFormError",error.message); return; }
    setBusy(true);
    try {
      if (state.selectedCategoryId) await api(base + "/categories/" + state.selectedCategoryId,{method:"PUT",body:payload});
      else await api(base + "/categories",{method:"POST",body:payload});
      await loadData(false); clearCategoryForm(); toast("تم حفظ القسم."); notice("ستظهر الأقسام الفعالة تلقائيًا في صفحة شراء PRIME.");
    } catch(error) { $error("categoryFormError",error.message); }
    finally { setBusy(false); }
  }
  async function deleteCategory(category) {
    if (state.busy) return;
    if (!window.confirm(`حذف قسم «${category.name}»؟ ستُنقل المنتجات التابعة له إلى «كل الأقسام» ولن تُحذف مشترياتها.`)) return;
    setBusy(true);
    try { await api(base + "/categories/" + category.category_id,{method:"DELETE"}); await loadData(false); clearCategoryForm(); toast("تم حذف القسم."); }
    catch(error) { toast(error.message,"error"); }
    finally { setBusy(false); }
  }
  async function loadSettings() {
    $("#settingEnabled").checked = Boolean(Number(state.settings.enabled ?? 1));
    $("#settingMaxBytes").value = String(state.settings.max_custom_upload_bytes ?? 5242880);
    $("#settingTtl").value = String(state.settings.session_ttl_seconds ?? 900);
  }
  async function saveSettings(event) {
    event.preventDefault(); if (state.busy) return;
    const maxBytes = Number($("#settingMaxBytes").value);
    const ttl = Number($("#settingTtl").value);
    if (!Number.isInteger(maxBytes) || maxBytes < 1024 || maxBytes > 5242880) { toast("حجم الملف يجب أن يكون بين 1024 و5242880 بايت.","error"); return; }
    if (!Number.isInteger(ttl) || ttl < 60 || ttl > 900) { toast("مدة الجلسة يجب أن تكون بين 60 و900 ثانية.","error"); return; }
    setBusy(true);
    try {
      await api(base + "/settings",{method:"POST",body:{
        enabled:$("#settingEnabled").checked,
        max_custom_upload_bytes:maxBytes,session_ttl_seconds:ttl
      }});
      await loadData(false); toast("تم حفظ إعدادات المتجر.");
    } catch(error) { toast(error.message,"error"); }
    finally { setBusy(false); }
  }
  function switchView(view) {
    state.view = view;
    $$(".admin-tab").forEach(button => button.classList.toggle("is-active",button.dataset.view===view));
    $$(".admin-view").forEach(section => section.classList.toggle("is-active",section.id===view+"View"));
  }
  async function loadData(showNotice = true) {
    try {
      const data = await api(base);
      state.items = Array.isArray(data.items) ? data.items : [];
      state.categories = Array.isArray(data.categories) ? data.categories : [];
      state.settings = data.settings || {};
      renderStats(); renderItems(); renderCategories(); renderCategorySelect($("#itemCategory").value); loadSettings();
      if (showNotice) notice("تم تحميل بيانات متجر PRIME.");
      return true;
    } catch(error) {
      notice(error.message, "error");
      $("#itemList").replaceChildren();
      const empty = document.createElement("div"); empty.className = "empty-inline"; empty.textContent = error.message; $("#itemList").append(empty);
      return false;
    }
  }
  function selectView(event) {
    const button = event.target.closest("[data-view]"); if (button) switchView(button.dataset.view);
  }
  function init() {
    if (!guildId || !csrf) { notice("تعذر قراءة سياق الداشبورد الآمن. ارجع إلى الداشبورد وافتح إدارة المتجر من السيرفر المحدد.","error"); return; }
    $$(".admin-tab").forEach(button => button.addEventListener("click",selectView));
    $("#refreshButton").addEventListener("click",() => loadData(true));
    $("#newItemButton").addEventListener("click",() => { clearItemForm(); switchView("products"); });
    $("#newCategoryButton").addEventListener("click",() => { clearCategoryForm(); switchView("categories"); });
    $("#resetItemButton").addEventListener("click",clearItemForm);
    $("#resetCategoryButton").addEventListener("click",clearCategoryForm);
    $("#deleteItemButton").addEventListener("click",deleteItem);
    $("#itemForm").addEventListener("submit",saveItem);
    $("#categoryForm").addEventListener("submit",saveCategory);
    $("#settingsForm").addEventListener("submit",saveSettings);
    $("#itemType").addEventListener("change",() => { if (!state.selectedItemId) renderCategorySelect(typeCategory($("#itemType").value)); });
    $("#priceMode").addEventListener("change",() => {
      const free = $("#priceMode").value === "free";
      $("#itemPrice").disabled = free; $("#itemPrice").min = free ? "0" : "1";
      if (free) $("#itemPrice").value = "0"; else if (Number($("#itemPrice").value) <= 0) $("#itemPrice").value = "100";
    });
    $("#itemName").addEventListener("input",() => { if (!state.selectedItemId && !$("#itemSlug").dataset.touched) $("#itemSlug").value = slugify($("#itemName").value); });
    $("#itemSlug").addEventListener("input",() => { $("#itemSlug").dataset.touched = "true"; });
    $("#itemSearch").addEventListener("input",event => { state.search=event.target.value || ""; renderItems(); });
    $$(".filter-pill").forEach(button => button.addEventListener("click",() => {
      state.itemFilter = button.dataset.itemFilter;
      $$(".filter-pill").forEach(item => item.classList.toggle("is-active",item===button));
      renderItems();
    }));
    $("#itemFile").addEventListener("change",event => {
      const file = event.target.files?.[0] || null;
      state.selectedFile = file; state.clearAsset = false;
      if (!file) { $("#assetInfo").textContent = "لم يتم اختيار ملف جديد."; return; }
      $("#assetInfo").textContent = file.name + " · " + (file.size/1024/1024).toFixed(2) + " MB";
      const extension = String(file.name || "").toLowerCase().split(".").pop();
      const acceptedMime = ["image/png","image/jpeg","image/webp"].includes(file.type);
      const acceptedExtension = ["png","jpg","jpeg","webp"].includes(extension);
      if ((!acceptedMime && !acceptedExtension) || file.size > 5*1024*1024) {
        state.selectedFile = null; $error("itemFormError","اختر PNG أو JPG أو WebP ثابتة بحجم أقل من 5 ميجابايت."); return;
      }
      const url = URL.createObjectURL(file); setAssetPreview(url);
      const image = $("#assetPreview img"); if (image) image.addEventListener("load",()=>URL.revokeObjectURL(url),{once:true});
    });
    $("#clearImageButton").addEventListener("click",() => {
      state.selectedFile = null; $("#itemFile").value = ""; $("#assetInfo").textContent = "لن يتم تغيير الصورة الحالية. إذا لم تكن هناك صورة فسيبقى المنتج بلا صورة.";
      const item=state.items.find(value=>Number(value.item_id)===state.selectedItemId); setAssetPreview(assetUrl(item)); $error("itemFormError");
    });
    clearItemForm(); clearCategoryForm(); loadData(false);
  }
  init();
})();
