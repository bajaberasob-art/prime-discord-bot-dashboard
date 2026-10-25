(() => {
  "use strict";
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const state = {
    items: [], categories: [], wallet: { coins: 0, gems: 0, bank: 0 }, user: {}, guild: {}, equipped: {},
    category: "all", subcategory: "all", filter: "all", sort: "recommended", search: "",
    selectedId: null, busy: false, loaded: false, previewBusy: false, previewObjectUrl: null
  };
  const CATEGORIES = {
    all: { label: "كل الأقسام", icon: "▦" },
    background: { label: "الخلفيات", icon: "▧" },
    card_color: { label: "ألوان البطاقة", icon: "◉" },
    card_template: { label: "قوالب البروفايل", icon: "▣" },
    flag: { label: "الأعلام", icon: "⚑" },
    badge: { label: "الشارات", icon: "✦" },
    shape: { label: "الأشكال", icon: "⬡" },
    club_logo: { label: "شعارات الأندية", icon: "★" }
  };
  const RARITY = {
    common: { label: "عادي", className: "rarity-common" },
    silver: { label: "فضي", className: "rarity-silver" },
    gold: { label: "ذهبي", className: "rarity-gold" },
    legendary: { label: "أسطوري", className: "rarity-legendary" }
  };
  const GLYPHS = { background: "▧", card_color: "◉", card_template: "P", flag: "⚑", badge: "✦", shape: "⬡", club_logo: "★" };
  const ERRORS = {
    store_session_expired: "انتهت جلسة المتجر. افتحه مرة ثانية من زر «تعديل بطاقة» داخل Discord.",
    csrf: "تعذر تأكيد الطلب الآمن. حدّث الصفحة وحاول مرة ثانية.",
    store_disabled: "متجر PRIME متوقف مؤقتاً.",
    item_not_found: "العنصر لم يعد متاحاً في المتجر.",
    already_owned: "العنصر موجود في مقتنياتك بالفعل.",
    insufficient_coins: "رصيد عملات PRIME ما يكفي لهذا العنصر.",
    insufficient_gems: "رصيد بلورات PRIME ما يكفي لهذا العنصر.",
    item_not_owned: "اشترِ العنصر أولاً قبل تجهيزه.",
    equip_conflict: "تعذر تجهيز العنصر بسبب تعارض مع تجهيز موجود.",
    unsupported_item_type: "هذا العنصر لا يدعم التجهيز حالياً.",
    wallet_changed: "تغيّر رصيدك أثناء الشراء. حدّث الصفحة وحاول مجدداً.",
    purchase_failed: "ما اكتمل الشراء. لم يتم تسجيل نجاح غير مؤكد.",
    equip_failed: "تعذر تجهيز العنصر. جرّب مرة ثانية.",
    unequip_failed: "تعذر إزالة التجهيز. جرّب مرة ثانية.",
    preview_failed: "تعذر إنشاء المعاينة الحقيقية. لم يتم حفظ أي تغيير.",
    preview_rate_limited: "انتظر لحظة قصيرة قبل إنشاء معاينة أخرى.",
    bot_unavailable: "البوت غير متصل حالياً؛ جرّب المعاينة لاحقاً.",
    guild_unavailable: "تعذر الوصول إلى السيرفر من البوت. تأكد أن البوت متصل.",
    member_unavailable: "تعذر العثور على حسابك داخل السيرفر.",
    badge_slots_full: "خانات الشارات الخمس ممتلئة. أزل شارة موجودة ثم جهّز الجديدة."
  };

  function safeColor(value, fallback) {
    return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value) ? value : (fallback || "#43d9ff");
  }
  function amount(value) {
    const n = Number(value);
    return Number.isFinite(n) && n >= 0 ? Math.trunc(n) : 0;
  }
  function fmt(value) { return amount(value).toLocaleString("en-US"); }
  function cookie(name) {
    const prefix = encodeURIComponent(name) + "=";
    const part = document.cookie.split("; ").find(row => row.startsWith(prefix));
    if (!part) return "";
    try { return decodeURIComponent(part.slice(prefix.length)); } catch (_) { return ""; }
  }
  function categoryName(type) { return (CATEGORIES[type] || {}).label || "عنصر PRIME"; }
  function currencyName(currency) { return currency === "gem" ? "بلورات PRIME" : "عملات PRIME"; }
  function currencyGlyph(currency) { return currency === "gem" ? "◆" : "P"; }
  function rarity(item) { return RARITY[item && item.rarity] || RARITY.common; }
  function isOwned(item) { return Boolean(item && item.owned); }
  function isEquipped(item) { return Boolean(item && item.equipped); }
  function isEquippable(item) { return Boolean(item && Object.prototype.hasOwnProperty.call(CATEGORIES, item.item_type) && item.item_type !== "all"); }
  function priceOf(item) { return amount(item && item.price); }
  function canAfford(item) {
    if (!item) return false;
    return isOwned(item) || priceOf(item) <= amount(item.currency === "gem" ? state.wallet.gems : state.wallet.coins);
  }
  function priceNode(item, detailed) {
    const price = priceOf(item);
    const node = document.createElement("span");
    node.className = "product-price" + (detailed ? " detail-price-block" : "") +
      (item.currency === "gem" && price > 0 ? " price-gem" : "") + (price === 0 ? " price-free" : "");
    if (!price) {
      node.textContent = "مجاني";
      return node;
    }
    const glyph = document.createElement("span");
    glyph.className = item.currency === "gem" ? "price-gem" : "price-prime";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = currencyGlyph(item.currency);
    const value = document.createElement("strong");
    value.textContent = fmt(price);
    node.append(glyph, value);
    if (detailed) {
      const label = document.createElement("span");
      label.className = "price-label";
      label.textContent = currencyName(item.currency);
      node.append(label);
    }
    return node;
  }
  function toast(message, type) {
    const region = $("#toastRegion");
    const element = document.createElement("div");
    element.className = "toast " + (type || "success");
    element.setAttribute("role", type === "error" ? "alert" : "status");
    element.textContent = message;
    region.append(element);
    window.setTimeout(() => element.remove(), 4200);
  }
  function setNotice(message, visible) {
    const element = $("#storeNotice");
    element.textContent = message || "";
    element.hidden = visible === false || !message;
  }
  async function api(path, options) {
    const opts = options || {};
    const method = opts.method || "GET";
    const headers = { Accept: "application/json" };
    const init = { method, credentials: "same-origin", headers, cache: "no-store" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = cookie("prime_profile_csrf");
      init.body = JSON.stringify(opts.body || {});
    }
    let response;
    try { response = await fetch(path, init); }
    catch (error) { throw Object.assign(new Error("network_error"), { cause: error }); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = typeof data.error === "string" ? data.error : "http_" + response.status;
      throw Object.assign(new Error(code), { code, status: response.status, message: typeof data.message === "string" ? data.message : "" });
    }
    return data;
  }
  function errorText(error) {
    if (error && error.code && ERRORS[error.code]) return ERRORS[error.code];
    if (error && (error.code === "unauthorized" || error.status === 401)) return ERRORS.store_session_expired;
    if (error && error.message && error.message !== "network_error" && !/fetch failed/i.test(error.message)) return error.message;
    return "تعذّر الاتصال بالمتجر. تأكد من الإنترنت ثم جرّب مرة ثانية.";
  }
  function handleSessionNotice() {
    const query = new URLSearchParams(window.location.search);
    if (query.get("link") === "invalid") setNotice("الرابط منتهي أو سبق استخدامه. ارجع إلى Discord وافتح المتجر مجدداً من أمر /profile.");
    else if (query.get("link") === "disabled") setNotice("متجر PRIME متوقف مؤقتاً من الإدارة. حاول لاحقاً.");
    else setNotice("جلسة المتجر غير صالحة أو انتهت. افتحه من زر «تعديل بطاقة» داخل Discord لربط حسابك.");
  }
  function renderWallet() {
    $("#coinBalance").textContent = fmt(state.wallet.coins);
    $("#gemBalance").textContent = fmt(state.wallet.gems);
    $("#userName").textContent = state.user.name || "عضو PRIME";
    $("#guildName").textContent = state.guild.name || "مساحة التخصيص";
    $("#userAvatar").src = state.user.avatar_url || "https://cdn.discordapp.com/embed/avatars/0.png";
    $("#userAvatar").alt = "صورة " + (state.user.name || "عضو PRIME");
    $("#userAvatar").referrerPolicy = "no-referrer";
    $("#catalogTotal").textContent = fmt(state.items.length);
  }
  function subcategory(item) { return String(item.subcategory || "").trim(); }
  function renderCategories() {
    const root = $("#categoryTabs");
    root.replaceChildren();
    const categories = Array.isArray(state.categories) ? state.categories : [];
    const counts = { all: state.items.length };
    state.items.forEach(item => {
      const slug = String(item.category || "all");
      counts[slug] = (counts[slug] || 0) + 1;
    });
    const entries = [{ slug: "all", name: "كل الأقسام", icon: "▦" },
      ...categories.filter(category => category && category.slug && category.name)];
    entries.forEach(category => {
      const key = category.slug;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "category-tab" + (state.category === key ? " is-active" : "");
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(state.category === key));
      button.dataset.category = key;
      const icon = document.createElement("span");
      icon.className = "tab-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = String(category.icon || "▦").slice(0, 12);
      const label = document.createElement("span");
      label.textContent = category.name;
      const count = document.createElement("span");
      count.className = "tab-count";
      count.textContent = fmt(counts[key] || 0);
      button.append(icon, label, count);
      root.append(button);
    });
  }
  function renderSubcategories(items) {
    const root = $("#subcategoryTabs");
    root.replaceChildren();
    const values = Array.from(new Set(items.map(subcategory).filter(Boolean))).sort((a, b) => a.localeCompare(b, "ar"));
    if (values.length < 2) {
      root.hidden = true;
      state.subcategory = "all";
      return;
    }
    root.hidden = false;
    const add = (key, label) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "subcategory-chip" + (state.subcategory === key ? " is-active" : "");
      button.dataset.subcategory = key;
      button.textContent = label;
      root.append(button);
    };
    add("all", "كل الأنواع");
    values.forEach(value => add(value, value));
  }
  function visibleItems() {
    let items = state.items.slice();
    if (state.category !== "all") items = items.filter(item => String(item.category || "all") === state.category);
    if (state.subcategory !== "all") items = items.filter(item => subcategory(item) === state.subcategory);
    if (state.filter === "owned") items = items.filter(isOwned);
    if (state.filter === "affordable") items = items.filter(canAfford);
    const query = state.search.trim().toLocaleLowerCase("ar");
    if (query) items = items.filter(item => [item.name, item.description, item.category, item.subcategory].join(" ").toLocaleLowerCase("ar").includes(query));
    if (state.sort === "price-asc") items.sort((a, b) => priceOf(a) - priceOf(b) || String(a.name).localeCompare(String(b.name), "ar"));
    else if (state.sort === "price-desc") items.sort((a, b) => priceOf(b) - priceOf(a) || String(a.name).localeCompare(String(b.name), "ar"));
    else if (state.sort === "name") items.sort((a, b) => String(a.name).localeCompare(String(b.name), "ar"));
    return items;
  }
  function drawGlyph(art, item, detail) {
    const frame = document.createElement("span");
    frame.className = "art-preview-frame";
    frame.setAttribute("aria-hidden", "true");
    const glyph = document.createElement("span");
    glyph.className = "art-glyph";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = GLYPHS[item.item_type] || "P";
    const caption = document.createElement("span");
    caption.className = "art-caption";
    caption.textContent = "PRIME / COSMETICS";
    art.append(frame, glyph);
    if (!detail) art.append(caption);
  }
  function productArt(item, detail) {
    const art = document.createElement("div");
    art.className = (detail ? "detail-art" : "product-art");
    art.style.setProperty("--item-accent", safeColor(item.style && item.style.accent));
    const asset = typeof item.asset === "string" ? item.asset : "";
    let allowedRemote = false;
    if (/^https:\/\//i.test(asset)) {
      try { allowedRemote = ["cdn.discordapp.com", "media.discordapp.net"].includes(new URL(asset).hostname); } catch (_) {}
    }
    if (/^\/api\/profile-store\/assets\/[1-9]\d*$/.test(asset) || allowedRemote) {
      const image = document.createElement("img");
      image.src = asset;
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => {
        image.remove();
        art.classList.remove("has-image");
        drawGlyph(art, item, Boolean(detail));
      }, { once: true });
      art.classList.add("has-image");
      art.append(image);
    } else drawGlyph(art, item, Boolean(detail));
    return art;
  }
  function rarityPill(item, extraClass) {
    const meta = rarity(item);
    const pill = document.createElement("span");
    pill.className = "rarity-pill " + meta.className + (extraClass ? " " + extraClass : "");
    pill.textContent = meta.label;
    return pill;
  }
  function renderCard(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "product-card" + (state.selectedId === item.item_id ? " is-selected" : "");
    button.dataset.itemId = String(item.item_id);
    button.setAttribute("aria-label", (item.name || "عنصر") + "، " + (isOwned(item) ? "ضمن مقتنياتك" : priceOf(item) === 0 ? "مجاني" : fmt(priceOf(item)) + " " + currencyName(item.currency)));
    const art = productArt(item, false);
    art.append(rarityPill(item, "card-rarity"));
    if (isEquipped(item) || isOwned(item)) {
      const badge = document.createElement("span");
      badge.className = isEquipped(item) ? "equipped-pill" : "owned-pill";
      badge.textContent = isEquipped(item) ? "مجهز" : "مقتنى";
      art.append(badge);
    }
    const info = document.createElement("span");
    info.className = "product-info";
    const title = document.createElement("span");
    title.className = "product-name";
    title.textContent = item.name || "عنصر PRIME";
    const description = document.createElement("span");
    description.className = "product-description";
    description.textContent = item.description || categoryName(item.item_type);
    const meta = document.createElement("span");
    meta.className = "product-meta";
    meta.append(priceNode(item, false));
    const kind = document.createElement("span");
    kind.className = "product-kind";
    kind.textContent = categoryName(item.item_type);
    meta.append(kind);
    info.append(title, description, meta);
    button.append(art, info);
    return button;
  }
  function renderGrid() {
    const items = visibleItems();
    const grid = $("#productGrid");
    grid.replaceChildren();
    const fragment = document.createDocumentFragment();
    items.forEach(item => fragment.append(renderCard(item)));
    grid.append(fragment);
    $("#resultCount").textContent = fmt(items.length) + " عنصر";
    $("#emptyState").hidden = items.length > 0;
    grid.hidden = items.length === 0;
    renderCategories();
    renderSubcategories(state.items.filter(item => state.category === "all" || String(item.category || "all") === state.category));
    $("#ownershipFilters").querySelectorAll("[data-filter]").forEach(button => {
      const active = button.dataset.filter === state.filter;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const selectedCategory = (state.categories || []).find(category => category.slug === state.category);
    $("#catalogHeading").textContent = state.category === "all" ? "اكتشف العناصر" : (selectedCategory ? selectedCategory.name : "اكتشف العناصر");
  }
  function findItem(itemId) {
    return state.items.find(item => Number(item.item_id) === Number(itemId === undefined ? state.selectedId : itemId)) || null;
  }
  function updateLivePreview(item) {
    const card = $("#liveProfileCard");
    const accent = safeColor(item.style && (item.style.accent || item.style.primary), "#43d9ff");
    const secondary = safeColor(item.style && (item.style.secondary || item.style.glow), "#8b72ff");
    const layout = String(item.metadata && (item.metadata.layout || item.metadata.orientation) || "wide").toLowerCase();
    card.dataset.layout = ["portrait", "square"].includes(layout) ? layout : "wide";
    const templateArt = $("#previewTemplateArt");
    if (item.item_type === "card_template" && typeof item.asset === "string" && /^\/api\/profile-store\/assets\/[1-9]\d*$/.test(item.asset)) {
      templateArt.src = item.asset;
      templateArt.hidden = false;
      card.dataset.hasTemplate = "true";
    } else {
      templateArt.removeAttribute("src");
      templateArt.hidden = true;
      card.dataset.hasTemplate = "false";
    }
    card.style.setProperty("--preview-accent", accent);
    card.style.setProperty("--preview-secondary", secondary);
    $("#previewName").textContent = state.user.name || "عضو PRIME";
    $("#previewHandle").textContent = "@" + String(state.user.username || "prime_member").replace(/^@/, "");
    $("#previewAvatar").src = state.user.avatar_url || "https://cdn.discordapp.com/embed/avatars/0.png";
    $("#previewCoins").textContent = fmt(state.wallet.coins);
    $("#previewGems").textContent = fmt(state.wallet.gems);
    $("#previewLevel").textContent = "PRIME";
    $("#previewBadge").textContent = String(item.name || "PRIME MEMBER").toLocaleUpperCase("en-US").slice(0, 22);
    $("#previewXp").textContent = item.item_type === "card_template" ? "TEMPLATE PREVIEW" : categoryName(item.item_type).toLocaleUpperCase("en-US");
    $("#previewNote").textContent = isOwned(item)
      ? (isEquipped(item) ? "هذا العنصر مجهّز حالياً. معاينة المظهر تستخدم إعداداته الحالية." : "هذا العنصر ضمن مقتنياتك. اضغط «تجهيز العنصر» لتطبيقه على بطاقة PRIME.")
      : "معاينة تقريبية بألوان العنصر. جرّبه هنا قبل الشراء؛ لن يتغير بروفايلك حتى تقتنيه وتجهّزه.";
  }

  function openSheet(itemId) {
    const item = findItem(itemId);
    if (!item) return;
    const nextId = Number(item.item_id);
    if (state.selectedId !== nextId) {
      const previousImage = $("#serverPreviewImage");
      if (state.previewObjectUrl) URL.revokeObjectURL(state.previewObjectUrl);
      state.previewObjectUrl = null;
      previousImage.removeAttribute("src");
      previousImage.hidden = true;
      $("#serverPreviewResult").hidden = true;
      $("#serverPreviewStatus").textContent = "";
    }
    state.selectedId = nextId;
    const art = productArt(item, true);
    $("#detailArt").replaceChildren(...Array.from(art.childNodes));
    $("#detailArt").style.setProperty("--item-accent", safeColor(item.style && item.style.accent));
    $("#detailRarity").className = "rarity-pill " + rarity(item).className;
    $("#detailRarity").textContent = rarity(item).label;
    $("#detailType").textContent = categoryName(item.item_type);
    $("#detailTitle").textContent = item.name || "عنصر PRIME";
    $("#detailDescription").textContent = item.description || "أضف هذا العنصر لهويتك داخل PRIME.";
    updateLivePreview(item);
    const priceRoot = $("#detailPriceRow");
    priceRoot.replaceChildren(priceNode(item, true));
    if (priceOf(item) > 0) {
      const info = document.createElement("span");
      info.className = "detail-type";
      info.textContent = currencyName(item.currency);
      priceRoot.append(info);
    }
    const primary = $("#primaryAction");
    const secondary = $("#secondaryAction");
    primary.disabled = state.busy;
    primary.onclick = null;
    secondary.onclick = null;
    secondary.hidden = true;
    if (isEquipped(item)) {
      primary.textContent = "مجهز حالياً ✓";
      primary.disabled = true;
      secondary.hidden = false;
      secondary.textContent = "إزالة التجهيز";
      secondary.disabled = state.busy;
      secondary.onclick = () => runAction("unequip", item);
    } else if (isOwned(item)) {
      primary.textContent = "تجهيز العنصر";
      primary.disabled = state.busy || !isEquippable(item);
      primary.onclick = () => runAction("equip", item);
    } else {
      primary.textContent = priceOf(item) === 0 ? "احصل عليه مجاناً" : "شراء الآن";
      primary.disabled = state.busy || !canAfford(item);
      primary.onclick = () => runAction("purchase", item);
      if (!canAfford(item)) {
        const note = document.createElement("span");
        note.className = "detail-type";
        note.textContent = "رصيدك غير كافٍ";
        priceRoot.append(note);
      }
    }
    $(".detail-actions").classList.toggle("has-secondary", !secondary.hidden);
    $("#detailSheet").classList.add("is-open");
    $("#detailSheet").setAttribute("aria-hidden", "false");
    $("#sheetScrim").hidden = false;
    renderGrid();
  }
  function closeSheet() {
    $("#detailSheet").classList.remove("is-open");
    $("#detailSheet").setAttribute("aria-hidden", "true");
    $("#sheetScrim").hidden = true;
    state.selectedId = null;
    renderGrid();
  }
  async function renderFullPreview() {
    const item = findItem();
    if (!item || state.previewBusy) return;
    const requestedItemId = Number(item.item_id);
    const button = $("#renderFullPreview");
    const result = $("#serverPreviewResult");
    const status = $("#serverPreviewStatus");
    const image = $("#serverPreviewImage");
    state.previewBusy = true;
    button.disabled = true;
    button.textContent = "جاري إنشاء البطاقة...";
    result.hidden = false;
    status.textContent = "يتم الآن تشغيل مولّد بطاقات PRIME على بياناتك الحقيقية. لا يتم خصم رصيد أو تغيير تجهيزاتك.";
    image.hidden = true;
    try {
      const response = await fetch("/api/profile-store/preview/" + encodeURIComponent(String(item.item_id)), {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "image/png, application/json" }
      });
      if (!response.ok) {
        let data = {};
        try { data = await response.json(); } catch (_) {}
        const code = typeof data.error === "string" ? data.error : "preview_failed";
        throw Object.assign(new Error(code), {
          code,
          status: response.status,
          message: typeof data.message === "string" ? data.message : ""
        });
      }
      const blob = await response.blob();
      if (!blob.type.toLowerCase().includes("image/png") || !blob.size) {
        throw Object.assign(new Error("preview_invalid_image"), { code: "preview_failed" });
      }
      if (state.selectedId !== requestedItemId) return;
      if (state.previewObjectUrl) URL.revokeObjectURL(state.previewObjectUrl);
      state.previewObjectUrl = URL.createObjectURL(blob);
      image.src = state.previewObjectUrl;
      image.hidden = false;
      status.textContent = "هذه معاينة ناتجة من مولّد بطاقة PRIME الفعلي. لم يتم حفظ أي تغيير.";
    } catch (error) {
      status.textContent = errorText(error);
      toast(errorText(error), "error");
    } finally {
      state.previewBusy = false;
      button.disabled = false;
      button.textContent = "تحديث المعاينة الفعلية";
    }
  }

  async function runAction(action, item) {
    if (state.busy || !item) return;
    state.busy = true;
    ["primaryAction", "secondaryAction"].forEach(id => { const button = $("#" + id); if (button) button.disabled = true; });
    const button = $("#primaryAction");
    const originalLabel = button.textContent;
    if (action !== "unequip") button.textContent = action === "purchase" ? "جاري الشراء..." : "جاري التجهيز...";
    else $("#secondaryAction").textContent = "جاري الإزالة...";
    try {
      const endpoint = "/api/profile-store/" + action;
      const data = await api(endpoint, { method: "POST", body: { item_id: Number(item.item_id) } });
      const stateRefreshed = await loadState(false);
      if (!stateRefreshed) {
        const localItem = findItem(item.item_id);
        if (action === "purchase" && localItem) {
          localItem.owned = true;
          if (item.currency === "gem") state.wallet.gems = amount(data.purchase && data.purchase.balance);
          else state.wallet.coins = amount(data.purchase && data.purchase.balance);
        } else if (action === "equip" && localItem) {
          localItem.equipped = true;
        } else if (action === "unequip" && localItem) {
          localItem.equipped = false;
        }
        renderWallet();
        renderGrid();
        setNotice("تم تأكيد العملية من الخادم، لكن تعذر تحديث الكتالوج. حدّث الصفحة لتأكيد الحالة الحالية.", true);
      }
      const refreshed = findItem(item.item_id);
      if (action === "purchase") toast("تم اقتناء «" + (data.purchase && data.purchase.name || item.name) + "» بنجاح.");
      else if (action === "equip") toast("تم حفظ تجهيز «" + item.name + "». استخدم /profile لعرض البطاقة الجديدة داخل Discord.");
      else toast("تمت إزالة «" + item.name + "» من التجهيز.");
      if (refreshed) openSheet(refreshed.item_id);
      else closeSheet();
    } catch (error) {
      button.textContent = originalLabel;
      toast(errorText(error), "error");
      if (error && error.status === 401) setNotice(ERRORS.store_session_expired);
      openSheet(item.item_id);
    } finally {
      state.busy = false;
      if (state.selectedId) openSheet(state.selectedId);
    }
  }
  async function loadState(showMessage) {
    try {
      const data = await api("/api/profile-store/state");
      state.items = Array.isArray(data.items) ? data.items : [];
      state.categories = Array.isArray(data.categories) ? data.categories : [];
      if (state.category !== "all" && !state.categories.some(category => category.slug === state.category)) state.category = "all";
      state.wallet = data.wallet || { coins: 0, gems: 0, bank: 0 };
      state.user = data.user || {};
      state.guild = data.guild || {};
      state.equipped = data.equipped || {};
      state.loaded = true;
      renderWallet();
      renderGrid();
      setNotice("", false);
      if (showMessage) toast("تم تحديث متجر PRIME.");
      if (state.selectedId && findItem(state.selectedId)) openSheet(state.selectedId);
      return true;
    } catch (error) {
      if (error && (error.status === 401 || error.code === "store_session_expired")) {
        handleSessionNotice();
      } else {
        setNotice(errorText(error), true);
      }
      if (!state.loaded) {
        $("#productGrid").hidden = true;
        $("#emptyState").hidden = true;
      }
      if (showMessage) toast(errorText(error), "error");
      return false;
    }
  }

  $("#categoryTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-category]");
    if (!button) return;
    state.category = button.dataset.category;
    state.subcategory = "all";
    renderGrid();
  });
  $("#subcategoryTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-subcategory]");
    if (!button) return;
    state.subcategory = button.dataset.subcategory;
    renderGrid();
  });
  $("#ownershipFilters").addEventListener("click", event => {
    const button = event.target.closest("[data-filter]");
    if (!button) return;
    state.filter = button.dataset.filter;
    renderGrid();
  });
  $("#storeSearch").addEventListener("input", event => {
    state.search = event.target.value || "";
    renderGrid();
  });
  $("#storeSort").addEventListener("change", event => {
    state.sort = event.target.value;
    renderGrid();
  });
  $("#productGrid").addEventListener("click", event => {
    const button = event.target.closest("[data-item-id]");
    if (button) openSheet(button.dataset.itemId);
  });
  $("#renderFullPreview").addEventListener("click", renderFullPreview);
  $("#closeSheet").addEventListener("click", closeSheet);
  $("#sheetScrim").addEventListener("click", closeSheet);
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeSheet(); });
  $("#clearFilters").addEventListener("click", () => {
    state.category = "all";
    state.subcategory = "all";
    state.filter = "all";
    state.sort = "recommended";
    state.search = "";
    $("#storeSearch").value = "";
    $("#storeSort").value = "recommended";
    renderGrid();
  });
  window.addEventListener("pagehide", () => {
    if (state.previewObjectUrl) URL.revokeObjectURL(state.previewObjectUrl);
    state.previewObjectUrl = null;
  });

  $("#storeLogout").addEventListener("click", async () => {
    try {
      await api("/api/profile-store/logout", { method: "POST", body: {} });
      toast("انتهت جلسة المتجر بأمان. افتحه مجدداً من Discord.");
      state.items = [];
      state.wallet = { coins: 0, gems: 0 };
      renderWallet();
      renderGrid();
      setNotice("انتهت الجلسة. ارجع إلى Discord وافتح المتجر من زر «تعديل بطاقة».");
    } catch (error) {
      toast(errorText(error), "error");
    }
  });

  loadState(false);
})();