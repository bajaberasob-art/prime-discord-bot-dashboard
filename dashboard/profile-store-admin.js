(() => {
  "use strict";
  const $ = (selector, parent=document) => parent.querySelector(selector);
  const TYPE_LABELS = {
    background:"خلفية / بانر", card_template:"قالب بروفايل", badge:"استكر / شارة",
    flag:"علم", shape:"شكل", club_logo:"شعار نادٍ", card_color:"لون بطاقة"
  };
  const RARITY_LABELS = {common:"عادي", silver:"فضي", gold:"ذهبي", legendary:"أسطوري"};
  const state = {session:null,guilds:[],guildId:"",items:[],settings:{},editingId:null,busy:false,categoryFilter:"all",previewUrl:null};
  const endpoint = (suffix="") => "/api/guild/"+encodeURIComponent(state.guildId)+"/profile-store"+suffix;

  async function fetchJson(url, options={}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept","application/json");
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type","application/json");
    if (!["GET","HEAD"].includes((options.method||"GET").toUpperCase())) {
      if (!state.session?.csrf) throw new Error("csrf_missing");
      headers.set("X-CSRF-Token", state.session.csrf);
    }
    const response = await fetch(url,{...options,headers,credentials:"same-origin",cache:"no-store"});
    if (response.status===401) { location.href="/login"; throw new Error("unauthorized"); }
    let data={};
    try { data=await response.json(); } catch (_) {}
    if (!response.ok) {
      const error=new Error(data.message || data.error || "request_failed");
      error.status=response.status; error.code=data.error || "request_failed";
      throw error;
    }
    return data;
  }
  function notify(message, type="success") {
    const toast=$("#toast"); toast.textContent=message; toast.className="toast "+type; toast.hidden=false;
    window.clearTimeout(notify.timer); notify.timer=window.setTimeout(()=>{toast.hidden=true;},4500);
  }
  function showStatus(message, error=false) {
    const root=$("#catalogStatus");
    root.textContent=message; root.classList.toggle("notice-error",error); root.hidden=!message;
  }
  function errorMessage(error) {
    const dict={
      unauthorized:"انتهت جلسة الداشبورد. سجّل الدخول مرة أخرى.",
      forbidden:"ليس لديك صلاحية إدارة متجر PRIME في السيرفر المحدد.",
      management_role_denied:"تحتاج صلاحية إدارة الداشبورد على السيرفر المحدد.",
      profile_store_owner_required:"إدارة متجر PRIME متاحة لمالك البوت أو الإدارة العامة المعتمدة فقط.",
      duplicate_slug:"هذا المعرّف البرمجي مستخدم بالفعل. اختر معرّفًا آخر.",
      invalid_image_file:"الملف ليس صورة صالحة.",
      image_too_large:"حجم الصورة أكبر من الحد المسموح (5MB).",
      image_pixel_limit:"أبعاد الصورة أكبر من الحد المسموح.",
      animated_images_not_allowed:"ارفع صورة ثابتة؛ الصور المتحركة غير مدعومة حاليًا.",
      item_type_does_not_accept_image:"هذا النوع لا يقبل صورة مرفقة.",
      invalid_slug:"استخدم حروفًا إنجليزية صغيرة وأرقامًا وشرطة فقط في المعرّف.",
      csrf:"تعذر تأكيد أمان الطلب. حدّث الصفحة ثم حاول مجددًا."
    };
    return dict[error?.code] || error?.message || "تعذر تنفيذ العملية.";
  }
  async function bootstrap() {
    try {
      const meResponse=await fetch("/api/me",{credentials:"same-origin",cache:"no-store"});
      if(meResponse.status===401){location.href="/login";return;}
      const me=await meResponse.json();
      if(!me.auth || !me.session){location.href="/login";return;}
      state.session=me.session;
      state.guilds=Array.isArray(me.session.guilds)?me.session.guilds:[];
      const select=$("#guildSelect"); select.replaceChildren();
      state.guilds.forEach(guild=>{
        const option=document.createElement("option");
        option.value=String(guild.id); option.textContent=guild.name || ("Server "+guild.id);
        select.append(option);
      });
      const requested=new URLSearchParams(location.search).get("guild_id");
      const selected=state.guilds.find(g=>String(g.id)===requested) || state.guilds[0];
      if(!selected){$("#accessStatus").textContent="ما فيه سيرفرات متاحة في جلسة الداشبورد.";$("#accessStatus").dataset.state="error";select.disabled=true;return;}
      state.guildId=String(selected.id);select.value=state.guildId;select.disabled=false;
      await loadCatalog();
    } catch(error) {
      $("#accessStatus").textContent=errorMessage(error);$("#accessStatus").dataset.state="error";
      showStatus(errorMessage(error),true);
    }
  }
  async function loadCatalog() {
    showStatus("جاري تحميل الكتالوج وصلاحيات الإدارة...");
    $("#accessStatus").textContent="التحقق من صلاحيات الإدارة...";
    const data=await fetchJson(endpoint());
    state.items=Array.isArray(data.items)?data.items:[];
    state.settings=data.settings || {};
    $("#accessStatus").textContent="تم التحقق من الوصول إلى "+(state.guilds.find(g=>String(g.id)===state.guildId)?.name || "السيرفر")+" — إدارة مسموحة";
    $("#accessStatus").dataset.state="ok";
    $("#storeEnabled").checked=Boolean(Number(state.settings.enabled ?? 1));
    $("#uploadEnabled").checked=Boolean(Number(state.settings.custom_upload_enabled ?? 0));
    $("#uploadPrice").value=Number(state.settings.custom_upload_price ?? 0);
    $("#uploadCurrency").value=state.settings.custom_upload_currency || "moon";
    $("#uploadLimit").value=Math.max(1,Math.min(5,Math.ceil(Number(state.settings.max_custom_upload_bytes || 5242880)/1048576)));
    $("#sessionTtl").value=Math.max(60,Math.min(900,Number(state.settings.session_ttl_seconds || 900)));
    renderCatalog(); showStatus("");
  }
  function effectiveItems(){return state.items.filter(item=>state.categoryFilter==="all" || String(item.category||"all")===state.categoryFilter);}
  function renderMetrics(){
    const visible=state.items.filter(item=>!item.deleted_at);
    $("#metricTotal").textContent=fmt(visible.length);
    $("#metricActive").textContent=fmt(visible.filter(item=>Boolean(Number(item.is_active)) && !item.deleted_at).length);
    $("#metricFree").textContent=fmt(visible.filter(item=>Number(item.price)===0).length);
    $("#metricPaid").textContent=fmt(visible.filter(item=>Number(item.price)>0).length);
  }
  function renderCategoryManager(){
    const root=$("#categoryChips");root.replaceChildren();
    const counts=new Map();
    state.items.filter(item=>!item.deleted_at).forEach(item=>{
      const category=String(item.category||"all").trim();
      if(!category || category==="all")return;
      counts.set(category,(counts.get(category)||0)+1);
    });
    $("#categoryTotal").textContent=fmt(counts.size)+" تصنيف";
    const all=document.createElement("button");all.type="button";all.className="category-chip"+(state.categoryFilter==="all"?" is-active":"");all.textContent="كل التصنيفات";all.addEventListener("click",()=>{state.categoryFilter="all";renderCatalog();});root.append(all);
    [...counts.entries()].sort((a,b)=>a[0].localeCompare(b[0],"ar")).forEach(([name,count])=>{
      const button=document.createElement("button");button.type="button";button.className="category-chip"+(state.categoryFilter===name?" is-active":"");
      button.textContent=name+" ";const strong=document.createElement("strong");strong.textContent=fmt(count);button.append(strong);
      button.addEventListener("click",()=>{state.categoryFilter=name;renderCatalog();});root.append(button);
    });
    const categories=[...new Set(state.items.map(i=>String(i.category||"").trim()).filter(v=>v&&v!=="all"))].sort((a,b)=>a.localeCompare(b,"ar"));
    const subcategories=[...new Set(state.items.map(i=>String(i.subcategory||"").trim()).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"ar"));
    $("#categoryOptions").replaceChildren(...categories.map(value=>{const o=document.createElement("option");o.value=value;return o;}));
    $("#subcategoryOptions").replaceChildren(...subcategories.map(value=>{const o=document.createElement("option");o.value=value;return o;}));
    if(!counts.size){const empty=document.createElement("span");empty.className="empty-inline";empty.textContent="ما تم إنشاء تصنيفات مخصصة بعد. اكتب اسم التصنيف في نموذج المنتج.";root.append(empty);}
  }
  function createTag(text){const tag=document.createElement("span");tag.className="tag";tag.textContent=text;return tag;}
  function createCard(item){
    const card=document.createElement("article");card.className="product-card";
    const art=document.createElement("div");art.className="product-art";
    const status=document.createElement("span");status.className="status-pill"+(item.deleted_at?" deleted":!Number(item.is_active)?" inactive":"");status.textContent=item.deleted_at?"محذوف":Number(item.is_active)?"منشور":"مخفي";art.append(status);
    if(item.has_asset){
      const image=document.createElement("img");image.alt="";image.loading="lazy";image.src=endpoint("/items/"+item.item_id+"/asset");
      image.onerror=()=>{image.remove();const text=document.createElement("span");text.className="no-art";text.textContent="P";art.append(text);};art.append(image);
    }else{const text=document.createElement("span");text.className="no-art";text.textContent=({background:"▧",card_template:"P",badge:"✦",flag:"⚑",shape:"⬡",club_logo:"★",card_color:"◉"})[item.item_type]||"P";art.append(text);}
    const content=document.createElement("div");content.className="product-content";
    const row=document.createElement("div");row.className="product-title-row";const title=document.createElement("h3");title.textContent=item.name;
    const price=document.createElement("span");price.className="product-price";price.textContent=Number(item.price)===0?"مجاني":fmt(item.price)+" "+(item.currency==="gem"?"بلورة":"عملة");row.append(title,price);content.append(row);
    const meta=document.createElement("div");meta.className="product-meta";meta.append(createTag(TYPE_LABELS[item.item_type]||item.item_type),createTag(item.category||"بدون تصنيف"),createTag(RARITY_LABELS[item.rarity]||item.rarity));if(item.subcategory)meta.append(createTag(item.subcategory));if(item.is_featured)meta.append(createTag("مميز"));content.append(meta);
    if(item.description){const desc=document.createElement("p");desc.className="product-description";desc.textContent=item.description;content.append(desc);}
    const bottom=document.createElement("div");bottom.className="product-bottom";const id=document.createElement("span");id.className="product-id";id.textContent="#"+item.item_id+" · "+item.slug;bottom.append(id);
    const actions=document.createElement("div");actions.className="product-actions";
    if(!item.deleted_at){const edit=document.createElement("button");edit.type="button";edit.className="small-button";edit.textContent="تعديل";edit.addEventListener("click",()=>openEditor(item));actions.append(edit);
      const del=document.createElement("button");del.type="button";del.className="small-button danger";del.textContent="حذف";del.addEventListener("click",()=>deleteItem(item));actions.append(del);}
    bottom.append(actions);content.append(bottom);card.append(art,content);return card;
  }
  function renderCatalog(){
    renderMetrics();renderCategoryManager();
    const query=$("#productSearch").value.trim().toLocaleLowerCase("ar");
    const type=$("#typeFilter").value,status=$("#statusFilter").value;
    let items=effectiveItems().filter(item=>{
      if(type!=="all"&&item.item_type!==type)return false;
      if(status==="active"&&(Number(!item.is_active)||item.deleted_at))return false;
      if(status==="inactive"&&(Number(item.is_active)||item.deleted_at))return false;
      if(status==="deleted"&&!item.deleted_at)return false;
      if(query && ![item.name,item.slug,item.description,item.category,item.subcategory,String(item.item_id)].join(" ").toLocaleLowerCase("ar").includes(query))return false;
      return true;
    }).sort((a,b)=>Number(Boolean(a.deleted_at))-Number(Boolean(b.deleted_at)) || Number(Boolean(b.is_active))-Number(Boolean(a.is_active)) || Number(b.item_id)-Number(a.item_id));
    const root=$("#productGrid");root.replaceChildren();items.forEach(item=>root.append(createCard(item)));
    $("#emptyState").hidden=items.length>0;root.hidden=items.length===0;
  }
  function fmt(value){return new Intl.NumberFormat("en-US").format(Number(value)||0);}
  function openEditor(item=null){
    state.editingId=item?Number(item.item_id):null;
    $("#dialogTitle").textContent=item?"تعديل المنتج":"إضافة منتج جديد";
    $("#productForm").reset();
    $("#itemPrice").value=0;$("#itemFree").checked=true;$("#itemActive").checked=true;$("#itemFeatured").checked=false;
    $("#itemType").value="background";$("#itemCategory").value="بانرات";$("#itemCurrency").value="moon";
    $("#itemRarity").value="common";$("#itemStyle").value="{}";$("#itemMetadata").value="{}";$("#templateLayout").value="wide";
    $("#itemAsset").value="";$("#assetPreview").hidden=true;$("#assetPreview").removeAttribute("src");$("#currentAsset").textContent="لم يتم تحديد صورة لهذا المنتج.";
    $("#formError").hidden=true;$("#templateMetadata").hidden=true;
    if(item){
      $("#itemName").value=item.name||"";$("#itemSlug").value=item.slug||"";$("#itemDescription").value=item.description||"";
      $("#itemType").value=item.item_type||"background";$("#itemCategory").value=item.category||"all";$("#itemSubcategory").value=item.subcategory||"";
      $("#itemRarity").value=item.rarity||"common";$("#itemPrice").value=Number(item.price)||0;$("#itemCurrency").value=item.currency||"moon";
      $("#itemFree").checked=Number(item.price)===0;$("#itemActive").checked=Boolean(Number(item.is_active));$("#itemFeatured").checked=Boolean(Number(item.is_featured));
      $("#itemStyle").value=JSON.stringify(item.style||{},null,2);$("#itemMetadata").value=JSON.stringify(item.metadata||{},null,2);
      $("#templateLayout").value=String(item.metadata?.layout||"wide");
      $("#currentAsset").textContent=item.asset_name?"الصورة الحالية: "+item.asset_name:(item.has_asset?"يوجد ملف صورة مرتبط":"لم يتم رفع صورة بعد");
      if(item.has_asset){$("#assetPreview").src=endpoint("/items/"+item.item_id+"/asset");$("#assetPreview").hidden=false;}
    }
    updateTypeFields();
    $("#productDialog").showModal();
  }
  function updateTypeFields(){
    const isTemplate=$("#itemType").value==="card_template";
    $("#templateMetadata").hidden=!isTemplate;
    if(!$("#itemCategory").value || $("#itemCategory").value==="all"){
      $("#itemCategory").value=({background:"بانرات",card_template:"قوالب",badge:"استكرات",flag:"أعلام",shape:"أشكال",club_logo:"شعارات الأندية",card_color:"ألوان"})[$("#itemType").value]||"عام";
    }
  }
  function parseObject(value,label){
    let parsed;try{parsed=JSON.parse(value||"{}");}catch(_){throw new Error(label+" يجب أن يكون JSON صالحًا.");}
    if(!parsed||Array.isArray(parsed)||typeof parsed!=="object")throw new Error(label+" يجب أن يكون كائن JSON.");
    return parsed;
  }
  async function submitProduct(event){
    event.preventDefault();if(state.busy)return;
    const errorBox=$("#formError");errorBox.hidden=true;
    let payload;
    try{
      const name=$("#itemName").value.trim(),slug=$("#itemSlug").value.trim().toLowerCase(),category=$("#itemCategory").value.trim();
      const free=$("#itemFree").checked;const price=free?0:Number($("#itemPrice").value);
      if(!name||!slug||!category)throw new Error("املأ الاسم والمعرّف والتصنيف.");
      if(!/^[a-z0-9](?:[a-z0-9_-]{0,78}[a-z0-9])?$/.test(slug))throw new Error("المعرّف يجب أن يحتوي حروفًا إنجليزية صغيرة وأرقامًا وشرطة فقط.");
      if(!Number.isSafeInteger(price)||price<0||price>1000000000)throw new Error("السعر غير صالح.");
      const metadata=parseObject($("#itemMetadata").value,"بيانات القالب");
      if($("#itemType").value==="card_template")metadata.layout=$("#templateLayout").value;
      payload={slug,name,description:$("#itemDescription").value.trim(),item_type:$("#itemType").value,category,
        subcategory:$("#itemSubcategory").value.trim(),rarity:$("#itemRarity").value,price,currency:$("#itemCurrency").value,
        is_active:$("#itemActive").checked,is_featured:$("#itemFeatured").checked,
        style:parseObject($("#itemStyle").value,"إعدادات الألوان"),metadata};
    }catch(error){errorBox.textContent=error.message;errorBox.hidden=false;return;}
    state.busy=true;$("#saveProduct").disabled=true;$("#saveProduct").textContent="جاري الحفظ...";
    try{
      let result;
      if(state.editingId){
        result=await fetchJson(endpoint("/items/"+state.editingId),{method:"PUT",body:JSON.stringify(payload)});
      }else{
        result=await fetchJson(endpoint("/items"),{method:"POST",body:JSON.stringify(payload)});
      }
      let item=result.item;const file=$("#itemAsset").files[0];
      if(file){
        const form=new FormData();form.append("file",file);
        const upload=await fetchJson(endpoint("/items/"+item.item_id+"/asset"),{method:"POST",body:form});
        if(item.item_type==="card_template"){
          const meta={...(payload.metadata||{}),layout:$("#templateLayout").value,width:upload.width,height:upload.height};
          payload.metadata=meta;
          await fetchJson(endpoint("/items/"+item.item_id),{method:"PUT",body:JSON.stringify(payload)});
        }
      }
      $("#productDialog").close();notify(state.editingId?"تم تحديث المنتج والصورة بنجاح.":"تم إنشاء المنتج بنجاح.","success");await loadCatalog();
    }catch(error){errorBox.textContent=errorMessage(error);errorBox.hidden=false;notify(errorMessage(error),"error");}
    finally{state.busy=false;$("#saveProduct").disabled=false;$("#saveProduct").textContent="حفظ المنتج";}
  }
  async function deleteItem(item){
    if(!confirm("سيتم حذف «"+item.name+"» من المتجر وإخفاؤه عن العملاء. ستبقى سجلات المشتريات محفوظة. متابعة؟"))return;
    try{await fetchJson(endpoint("/items/"+item.item_id),{method:"DELETE"});notify("تم إخفاء المنتج وحذفه من الكتالوج النشط.");await loadCatalog();}
    catch(error){notify(errorMessage(error),"error");}
  }
  async function saveSettings(){
    const button=$("#saveSettings");button.disabled=true;button.textContent="جاري الحفظ...";
    try{
      const payload={
        enabled:$("#storeEnabled").checked,custom_upload_enabled:$("#uploadEnabled").checked,
        custom_upload_price:Number($("#uploadPrice").value),custom_upload_currency:$("#uploadCurrency").value,
        max_custom_upload_bytes:Number($("#uploadLimit").value)*1024*1024,
        session_ttl_seconds:Number($("#sessionTtl").value)
      };
      if(!Number.isSafeInteger(payload.custom_upload_price)||payload.custom_upload_price<0)throw new Error("سعر الرفع غير صالح.");
      if(!Number.isInteger(payload.max_custom_upload_bytes)||payload.max_custom_upload_bytes<1024||payload.max_custom_upload_bytes>5*1024*1024)throw new Error("حد الرفع يجب أن يكون بين 1 و5 ميغابايت.");
      if(!Number.isInteger(payload.session_ttl_seconds)||payload.session_ttl_seconds<60||payload.session_ttl_seconds>900)throw new Error("مدة الجلسة يجب أن تكون بين 60 و900 ثانية.");
      await fetchJson(endpoint("/settings"),{method:"POST",body:JSON.stringify(payload)});
      notify("تم حفظ إعدادات متجر PRIME.");await loadCatalog();
    }catch(error){notify(errorMessage(error),"error");}
    finally{button.disabled=false;button.textContent="حفظ الإعدادات";}
  }

  $("#guildSelect").addEventListener("change",async event=>{
    state.guildId=event.target.value;state.categoryFilter="all";
    const url=new URL(location.href);url.searchParams.set("guild_id",state.guildId);history.replaceState(null,"",url);
    try{await loadCatalog();}catch(error){showStatus(errorMessage(error),true);$("#accessStatus").textContent=errorMessage(error);$("#accessStatus").dataset.state="error";}
  });
  $("#addProduct").addEventListener("click",()=>openEditor());
  $("#closeDialog").addEventListener("click",()=>$("#productDialog").close());
  $("#cancelProduct").addEventListener("click",()=>$("#productDialog").close());
  $("#productForm").addEventListener("submit",submitProduct);
  $("#itemType").addEventListener("change",updateTypeFields);
  $("#itemFree").addEventListener("change",()=>{if($("#itemFree").checked)$("#itemPrice").value=0;else if(Number($("#itemPrice").value)===0)$("#itemPrice").value=1;});
  $("#itemPrice").addEventListener("input",()=>{if(Number($("#itemPrice").value)>0)$("#itemFree").checked=false;else $("#itemFree").checked=true;});
  $("#itemAsset").addEventListener("change",()=>{
    const file=$("#itemAsset").files[0];if(!file)return;
    if(state.previewUrl)URL.revokeObjectURL(state.previewUrl);
    state.previewUrl=URL.createObjectURL(file);$("#assetPreview").src=state.previewUrl;$("#assetPreview").hidden=false;$("#currentAsset").textContent=file.name+" · "+fmt(file.size)+" bytes";
  });
  $("#saveSettings").addEventListener("click",saveSettings);
  $("#productSearch").addEventListener("input",renderCatalog);
  $("#typeFilter").addEventListener("change",renderCatalog);
  $("#statusFilter").addEventListener("change",renderCatalog);
  window.addEventListener("pagehide",()=>{if(state.previewUrl)URL.revokeObjectURL(state.previewUrl);});
  bootstrap();
})();