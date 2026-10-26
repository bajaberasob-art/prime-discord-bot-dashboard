(()=>{'use strict';
const root=document.getElementById('app'), guildId=root.dataset.guildId, csrf=root.dataset.csrf;
const $=id=>document.getElementById(id); let items=[],settings={},categories=[],previewUrl='',layoutMetadata={},layoutDrag=null,layoutImageSource='',layoutNeedsDefault=false;
const endpoint=(suffix='')=>'/api/guild/'+encodeURIComponent(guildId)+'/profile-store'+suffix;
function assetUrl(id){return endpoint('/items/'+encodeURIComponent(id)+'/asset')}
function refreshBackgroundPreviewOptions(){const select=$('layoutBackgroundSource');if(!select)return;const prior=select.value;const choices=items.filter(item=>item.item_type==='background'&&item.has_asset);select.innerHTML='<option value="">بدون صورة تجريبية</option>'+choices.map(item=>'<option value="'+escape(item.item_id)+'">'+escape(item.name)+'</option>').join('');if(choices.some(item=>String(item.item_id)===prior))select.value=prior;updateBackgroundPreview()}
function notice(message,error=false){const n=$('notice');n.textContent=message;n.classList.toggle('error',error);n.style.display='block';window.setTimeout(()=>n.style.display='none',6000)}
async function api(path,opts={}){const headers=new Headers(opts.headers||{});headers.set('X-CSRF-Token',csrf);if(opts.body&&!(opts.body instanceof FormData))headers.set('Content-Type','application/json');const res=await fetch(endpoint(path),{...opts,headers,credentials:'same-origin'});let data={};try{data=await res.json()}catch{}if(!res.ok)throw new Error(data.message||data.error||('HTTP '+res.status));return data}
function parseItem(i){return {...i,style:i.style||{},metadata:i.metadata||{}}}
function switchAdminTab(name){
 const valid=new Set(['overview','products','design','categories','settings']);
 if(!valid.has(name))name='overview';
 document.querySelectorAll('[data-admin-pane]').forEach(p=>p.classList.toggle('hidden',p.dataset.adminPane!==name));
 document.querySelectorAll('[data-admin-tab]').forEach(b=>{const active=b.dataset.adminTab===name;b.classList.toggle('is-active',active);b.setAttribute('aria-selected',active?'true':'false');});
 if(name==='design')renderLayoutEditor();
}
function updateDesignTemplateOptions(){
 const select=$('designTemplateSelect');if(!select)return;
 const previous=select.value;
 const current=items.find(item=>String(item.item_id)===String($('itemId').value||'')&&item.item_type==='card_template');
 const templates=items.filter(item=>item.item_type==='card_template');
 select.innerHTML='<option value="">اختر قالبًا محفوظًا</option>'+templates.map(item=>'<option value="'+escape(item.item_id)+'">'+escape(item.name)+' · '+escape(item.slug)+'</option>').join('');
 if(templates.some(item=>String(item.item_id)===previous))select.value=previous;
 else if(current)select.value=String(current.item_id);
 else select.value='';
 $('designLoadTemplate').disabled=templates.length===0;
 $('designStatus').textContent=templates.length?'اختر القالب ثم افتحه في الاستوديو. القوالب غير المعدّلة تبقى كما هي حتى تحفظ تغييراتك.':'لا توجد قوالب بطاقات محفوظة بعد. أنشئ قالبًا من قسم المنتجات والقوالب أولًا.';
}
async function load(){try{const data=await api('');items=(data.items||[]).map(parseItem);updateDesignTemplateOptions();categories=(data.categories||[]).map(c=>({...c,category_id:Number(c.category_id),parent_id:c.parent_id==null?null:Number(c.parent_id),sort_order:Number(c.sort_order)||0,is_active:!!c.is_active}));settings=data.settings||{};refreshBackgroundPreviewOptions();const sales=data.sales||{};$('salesPurchases').textContent=fmtNumber(sales.purchases);$('salesBuyers').textContent=fmtNumber(sales.buyers);$('salesMoon').textContent=fmtNumber(sales.moon_revenue);$('salesGems').textContent=fmtNumber(sales.gem_revenue);$('storeEnabled').checked=!!Number(settings.enabled);$('customEnabled').checked=!!Number(settings.custom_upload_enabled);$('customPrice').value=settings.custom_upload_price||0;$('customCurrency').value=settings.custom_upload_currency||'moon';$('maxUpload').value=Math.max(0.001,Number(settings.max_custom_upload_bytes||5242880)/1048576).toFixed(3);$('sessionTtl').value=settings.session_ttl_seconds||900;render();}catch(e){notice('تعذر تحميل المتجر: '+e.message,true)}}
function fmtNumber(value){return Number(value||0).toLocaleString('ar')}
function localDateTimeValue(value){if(!value)return '';const d=new Date(value);if(Number.isNaN(d.getTime()))return '';return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16)}
function utcDateTimeValue(value){if(!value)return null;const d=new Date(value);if(Number.isNaN(d.getTime()))throw new Error('تأكد من صحة وقت بداية العرض ونهايته.');return d.toISOString()}
function effectivePrice(item){return Number(item&&item.is_on_sale?item.effective_price:(item&&item.price)||0)}
function escape(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function kind(t){return ({background:'خلفية',card_template:'قالب بطاقة',badge:'استكر / شارة',flag:'علم',shape:'إطار / شكل',club_logo:'شعارات / أيقونات',card_color:'ألوان'})[t]||t}
function isShopVisible(item){
 if(!item||!item.is_active)return false;
 const name=String(item.category||'').trim().toLocaleLowerCase('ar');
 const sub=String(item.subcategory||'').trim().toLocaleLowerCase('ar');
 const parent=categories.find(c=>c.parent_id===null&&String(c.name||'').trim().toLocaleLowerCase('ar')===name);
 if(parent&&!parent.is_active)return false;
 if(parent&&sub){const child=categories.find(c=>Number(c.parent_id)===Number(parent.category_id)&&String(c.name||'').trim().toLocaleLowerCase('ar')===sub);if(child&&!child.is_active)return false;}
 return true;
}
function render(){
 const allTop=categories.filter(c=>c.parent_id===null).sort((a,b)=>a.sort_order-b.sort_order||a.category_id-b.category_id); const activeTop=allTop.filter(c=>c.is_active);
 const itemCategories=Array.from(new Set(items.map(i=>String(i.category||'').trim()).filter(v=>v&&v!=='all'))).sort((a,b)=>a.localeCompare(b,'ar'));
 const priorCategory=$('filterCategory').value;
 $('filterCategory').innerHTML='<option value="">كل الأقسام</option>'+itemCategories.map(v=>'<option value="'+escape(v)+'">'+escape(v)+'</option>').join('');
 if(itemCategories.includes(priorCategory))$('filterCategory').value=priorCategory;
 const suggestions=Array.from(new Set(['الخلفيات','القوالب','البانرات','الاستكرات','الأعلام','الإطارات','الشعارات','ألوان البطاقات',...categories.map(v=>v.parent_id===null?v.name:null).filter(Boolean),...itemCategories]));
 $('categories').innerHTML=suggestions.map(v=>'<option value="'+escape(v)+'"></option>').join('');
 const categoryCounts=new Map();items.forEach(i=>{const name=String(i.category||'').trim();if(name&&name!=='all')categoryCounts.set(name,(categoryCounts.get(name)||0)+1)});
 $('sections').innerHTML=activeTop.length?activeTop.map(v=>'<button type="button" data-section="'+escape(v.name)+'">'+escape(v.name)+' <span class="pill">'+(categoryCounts.get(v.name)||0)+'</span></button>').join(''):'<span class="muted">لا توجد أقسام نشطة بعد. أضف قسمًا من إدارة الأقسام.</span>';
 const parentSelect=$('categoryParent'), oldParent=parentSelect.value;
 parentSelect.innerHTML='<option value="">قسم رئيسي</option>'+allTop.map(v=>'<option value="'+v.category_id+'">'+escape(v.name)+(v.is_active?'':' (مخفي)')+'</option>').join('');
 if(activeTop.some(v=>String(v.category_id)===oldParent))parentSelect.value=oldParent;
 const search=$('search').value.trim().toLowerCase(),type=$('filterType').value,category=$('filterCategory').value,status=$('filterStatus').value;
 const filtered=items.filter(i=>(!search||(i.name+' '+i.slug+' '+i.category+' '+(i.subcategory||'')).toLowerCase().includes(search))&&(!type||i.item_type===type)&&(!category||String(i.category||'')===category)&&(!status||(status==='active'?isShopVisible(i):status==='inactive'?!isShopVisible(i):status==='free'?effectivePrice(i)===0:effectivePrice(i)>0)));
 $('countAll').textContent=fmtNumber(items.length);$('countActive').textContent=fmtNumber(items.filter(isShopVisible).length);$('countFree').textContent=fmtNumber(items.filter(i=>effectivePrice(i)===0).length);$('countPaid').textContent=fmtNumber(items.filter(i=>effectivePrice(i)>0).length);$('countHidden').textContent=fmtNumber(items.filter(i=>!isShopVisible(i)).length);$('countMissing').textContent=fmtNumber(items.filter(i=>isShopVisible(i)&&!i.has_asset&&['background','card_template','flag','badge','shape','club_logo'].includes(i.item_type)).length);
 $('items').innerHTML=filtered.length?filtered.map(i=>{const effective=effectivePrice(i),base=Number(i.price)||0;const priceMarkup=i.is_on_sale?'<div class="price"><span class="sale-old">'+fmtNumber(base)+'</span><strong class="sale-current">'+(effective===0?'مجاني':fmtNumber(effective)+' '+(i.currency==='gem'?'بلورة':'عملة'))+'</strong><span class="pill">عرض نشط</span></div>':'<div class="price">'+(base===0?'مجاني':fmtNumber(base)+' '+(i.currency==='gem'?'بلورة':'عملة'))+'</div>';return '<article class="item"><div class="thumb">'+(i.has_asset?'<img loading="lazy" alt="" src="'+endpoint('/items/'+i.item_id+'/asset')+'">':'<span>لا توجد صورة بعد</span>')+'</div><div class="itembody"><div class="itemtitle"><strong>'+escape(i.name)+'</strong><span class="pill">'+kind(i.item_type)+'</span></div><div class="muted">'+escape(i.category||'عام')+(i.subcategory?' / '+escape(i.subcategory):'')+' · '+(isShopVisible(i)?'معروض':i.is_active?'القسم مخفي':'مخفي')+'</div>'+priceMarkup+'<div class="itemmetrics">ترتيب '+fmtNumber(i.sort_order)+' · مشتريات '+fmtNumber(i.purchase_count)+'<br>الإيرادات: '+fmtNumber(i.moon_revenue)+' عملة PRIME · '+fmtNumber(i.gem_revenue)+' بلورة</div><div class="muted">'+escape(i.slug)+'</div><div class="itembuttons"><button data-edit="'+i.item_id+'">تعديل</button><button data-toggle="'+i.item_id+'">'+(i.is_active?'إخفاء':'نشر')+'</button><span class="item-reorder"><button type="button" data-move-item="'+i.item_id+'" data-dir="-1" aria-label="ترتيب المنتج للأعلى">↑</button><button type="button" data-move-item="'+i.item_id+'" data-dir="1" aria-label="ترتيب المنتج للأسفل">↓</button></span></div></div></article>'}).join(''):'<p class="muted">لا توجد منتجات مطابقة.</p>';
 $('items').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(Number(b.dataset.edit)));$('items').querySelectorAll('[data-toggle]').forEach(b=>b.onclick=()=>toggle(Number(b.dataset.toggle)));$('items').querySelectorAll('[data-move-item]').forEach(b=>b.onclick=()=>moveItem(Number(b.dataset.moveItem),Number(b.dataset.dir)));
 const sorted=[...categories].sort((a,b)=>(a.parent_id??0)-(b.parent_id??0)||a.sort_order-b.sort_order||a.category_id-b.category_id);
 $('categoryList').innerHTML=sorted.length?sorted.map(cat=>{const parent=categories.find(p=>p.category_id===cat.parent_id);const count=items.filter(i=>String(i.category||'').toLowerCase()===String(parent?parent.name:cat.name).toLowerCase()&&(!parent||String(i.subcategory||'').toLowerCase()===cat.name.toLowerCase())).length;return '<article class="category-card"><strong>'+ (parent?'↳ ':'')+escape(cat.name)+'</strong><div class="muted"><small>'+(parent?'فرعي من '+escape(parent.name):'قسم رئيسي')+' · '+count+' منتج · '+(cat.is_active?'ظاهر':'مخفي')+'</small></div><div class="actions"><button type="button" data-edit-category="'+cat.category_id+'">تعديل</button><button type="button" data-toggle-category="'+cat.category_id+'">'+(cat.is_active?'إخفاء':'نشر')+'</button><button type="button" data-move-category="'+cat.category_id+'" data-dir="-1" aria-label="تحريك للأعلى">↑</button><button type="button" data-move-category="'+cat.category_id+'" data-dir="1" aria-label="تحريك للأسفل">↓</button></div></article>'}).join(''):'<p class="muted">لا توجد أقسام. أضف أول قسم من النموذج أعلاه.</p>';
 $('categoryList').querySelectorAll('[data-edit-category]').forEach(b=>b.onclick=()=>editCategory(Number(b.dataset.editCategory)));
 $('categoryList').querySelectorAll('[data-toggle-category]').forEach(b=>b.onclick=()=>toggleCategory(Number(b.dataset.toggleCategory)));
 $('categoryList').querySelectorAll('[data-move-category]').forEach(b=>b.onclick=()=>moveCategory(Number(b.dataset.moveCategory),Number(b.dataset.dir)));
}
function reset(){ clearAssetPreview();$('itemForm').reset();$('itemId').value='';layoutMetadata={};layoutNeedsDefault=true;layoutImageSource='';$('editorTitle').textContent='إضافة منتج جديد';$('deleteItem').classList.add('hidden');$('active').checked=true;$('price').value=0;$('sortOrder').value=items.reduce((max,item)=>Math.max(max,Number(item.sort_order)||0),-10)+10;$('salePrice').value='';$('saleStart').value='';$('saleEnd').value='';$('accent').value='#72DCFF';updateAssetField();updateSubcategorySuggestions();syncLayoutEditor(true)}
function edit(id){const i=items.find(x=>x.item_id===id);if(!i)return;$('itemId').value=id;$('name').value=i.name;$('slug').value=i.slug;$('type').value=i.item_type;$('category').value=i.category||'عام';$('subcategory').value=i.subcategory||'';updateSubcategorySuggestions();$('rarity').value=i.rarity||'common';$('price').value=i.price;$('currency').value=i.currency||'moon';$('sortOrder').value=Number(i.sort_order)||0;$('salePrice').value=i.sale_price==null?'':i.sale_price;$('saleStart').value=localDateTimeValue(i.sale_starts_at);$('saleEnd').value=localDateTimeValue(i.sale_ends_at);$('description').value=i.description||'';$('active').checked=!!i.is_active;$('featured').checked=!!i.is_featured;$('accent').value=i.style?.accent||'#72DCFF';clearAssetPreview();$('asset').value='';layoutMetadata=cloneLayoutMetadata(i.metadata||{});layoutNeedsDefault=!(Number(layoutMetadata.width)>0&&Number(layoutMetadata.height)>0);layoutImageSource='';updateAssetField();$('editorTitle').textContent='تعديل: '+i.name;$('deleteItem').classList.remove('hidden');syncLayoutEditor(true);window.scrollTo({top:0,behavior:'smooth'})}
function payload(){return {slug:$('slug').value.trim().toLowerCase(),name:$('name').value.trim(),description:$('description').value.trim(),item_type:$('type').value,category:$('category').value.trim()||'عام',subcategory:$('subcategory').value.trim(),rarity:$('rarity').value,price:Number($('price').value),currency:$('currency').value,sort_order:Number($('sortOrder').value)||0,sale_price:$('salePrice').value.trim()===''?null:Number($('salePrice').value),sale_starts_at:utcDateTimeValue($('saleStart').value),sale_ends_at:utcDateTimeValue($('saleEnd').value),style:{accent:$('accent').value.trim()||'#72DCFF'},metadata:cloneLayoutMetadata(layoutMetadata),is_active:$('active').checked,is_featured:$('featured').checked}}
async function upload(id){const file=$('asset').files[0];if(!file)return;const form=new FormData();form.append('file',file);const result=await api('/items/'+id+'/asset',{method:'POST',body:form});notice('تم رفع الصورة ('+result.width+'×'+result.height+')')}
$('itemForm').addEventListener('submit',async e=>{e.preventDefault();const btn=$('saveItem');btn.disabled=true;let savedId=$('itemId').value;try{const id=savedId;const desired=payload();const current=id?items.find(i=>String(i.item_id)===String(id)):null;const needsAsset=['background','card_template','badge','flag','shape','club_logo'].includes(desired.item_type);const hasFile=$('asset').files.length>0;const hasExistingAsset=!!(current&&current.has_asset);if(needsAsset&&!hasFile&&!hasExistingAsset&&desired.is_active)throw new Error('ارفع صورة لهذا المنتج قبل نشره في المتجر.');const stageInactive=needsAsset&&hasFile&&desired.is_active&&!hasExistingAsset;const saveBody={...desired,is_active:stageInactive?false:desired.is_active};const result=await api(id?'/items/'+id:'/items',{method:id?'PUT':'POST',body:JSON.stringify(saveBody)});const saved=result.item;savedId=String(saved.item_id);$('itemId').value=savedId;$('editorTitle').textContent='تعديل: '+saved.name;$('deleteItem').classList.remove('hidden');if(hasFile)await upload(saved.item_id);if(stageInactive){const latest=await api('');const item=(latest.items||[]).find(i=>String(i.item_id)===savedId);if(!item||!item.has_asset)throw new Error('تم حفظ المنتج، لكن لم يثبت وجود ملف الصورة بعد؛ بقي مخفياً حتى تكتمل الصورة.');const publishBody={slug:item.slug,name:item.name,description:item.description||'',item_type:item.item_type,category:item.category||'all',subcategory:item.subcategory||'',rarity:item.rarity||'common',price:Number(item.price)||0,currency:item.currency||'moon',sort_order:Number(item.sort_order)||0,sale_price:item.sale_price==null?null:Number(item.sale_price),sale_starts_at:item.sale_starts_at||null,sale_ends_at:item.sale_ends_at||null,style:item.style||{},metadata:item.metadata||{},is_active:true,is_featured:!!item.is_featured};await api('/items/'+savedId,{method:'PUT',body:JSON.stringify(publishBody)})}notice(id?'تم تحديث المنتج':'تم إنشاء المنتج');if(desired.item_type==='card_template'){await load();edit(Number(savedId));notice('تم حفظ المنتج. يمكنك الآن ضبط مواضع عناصر القالب من المحرر المرئي.')}else{reset();await load()}}catch(err){if(savedId){$('itemId').value=savedId;$('editorTitle').textContent='استكمال تعديل المنتج #'+savedId;$('deleteItem').classList.remove('hidden')}notice('تعذر إكمال الحفظ: '+err.message,true)}finally{btn.disabled=false}});
$('deleteItem').onclick=async()=>{const id=$('itemId').value;if(!id||!confirm('سيتم حذف المنتج من الكتالوج وإخفاؤه عن العملاء. متابعة؟'))return;try{await api('/items/'+id,{method:'DELETE'});notice('تم حذف المنتج من الكتالوج');reset();await load()}catch(e){notice('تعذر الحذف: '+e.message,true)}};
async function toggle(id){const i=items.find(x=>x.item_id===id);if(!i)return;try{await api('/items/'+id,{method:'PUT',body:JSON.stringify({slug:i.slug,name:i.name,description:i.description,item_type:i.item_type,category:i.category,subcategory:i.subcategory||'',rarity:i.rarity,price:i.price,currency:i.currency,sort_order:Number(i.sort_order)||0,sale_price:i.sale_price==null?null:Number(i.sale_price),sale_starts_at:i.sale_starts_at||null,sale_ends_at:i.sale_ends_at||null,style:i.style||{},metadata:i.metadata||{},is_active:!i.is_active,is_featured:!!i.is_featured})});notice(i.is_active?'تم إخفاء المنتج':'تم نشر المنتج');await load()}catch(e){notice('تعذر تغيير الحالة: '+e.message,true)}}
$('saveSettings').onclick=async()=>{if(!$('customPrice').reportValidity()||!$('maxUpload').reportValidity()||!$('sessionTtl').reportValidity())return;try{await api('/settings',{method:'POST',body:JSON.stringify({enabled:$('storeEnabled').checked,custom_upload_enabled:$('customEnabled').checked,custom_upload_price:Number($('customPrice').value),custom_upload_currency:$('customCurrency').value,max_custom_upload_bytes:Math.round(Number($('maxUpload').value)*1048576),session_ttl_seconds:Number($('sessionTtl').value)})});notice('تم حفظ إعدادات المتجر')}catch(e){notice('تعذر حفظ الإعدادات: '+e.message,true)}};
$('refreshSales').addEventListener('click',load);
async function moveItem(id,direction){const ordered=[...items].sort((a,b)=>(Number(a.sort_order)||0)-(Number(b.sort_order)||0)||a.item_id-b.item_id);const index=ordered.findIndex(item=>item.item_id===id),target=index+direction;if(index<0||target<0||target>=ordered.length)return;[ordered[index],ordered[target]]=[ordered[target],ordered[index]];try{await api('/items/reorder',{method:'PUT',body:JSON.stringify({item_ids:ordered.map(item=>Number(item.item_id))})});notice('تم تحديث ترتيب المنتجات');await load()}catch(e){notice('تعذر ترتيب المنتجات: '+e.message,true)}}
$('sections').addEventListener('click',event=>{const button=event.target.closest('[data-section]');if(!button)return;$('filterCategory').value=button.dataset.section;render();$('items').scrollIntoView({behavior:'smooth',block:'start'})});
$('categoryForm').addEventListener('submit',async e=>{e.preventDefault();const id=$('categoryId').value;const payload={name:$('categoryName').value.trim(),parent_id:$('categoryParent').value?Number($('categoryParent').value):null,sort_order:Number($('categoryOrder').value)||0,is_active:$('categoryActive').checked};const btn=$('saveCategory');btn.disabled=true;try{await api(id?'/categories/'+id:'/categories',{method:id?'PUT':'POST',body:JSON.stringify(payload)});notice(id?'تم تحديث القسم':'تم إنشاء القسم');resetCategory();await load()}catch(err){notice('تعذر حفظ القسم: '+err.message,true)}finally{btn.disabled=false}});
function updateSubcategorySuggestions(){const name=$('category').value.trim().toLowerCase();const parent=categories.find(c=>c.parent_id===null&&c.name.toLowerCase()===name);const children=parent?categories.filter(c=>c.parent_id===parent.category_id):[];$('subcategorySuggestions').innerHTML=children.map(c=>'<option value="'+escape(c.name)+'"></option>').join('')}
function resetCategory(){ $('categoryForm').reset();$('categoryId').value='';$('categoryOrder').value=categories.length;$('categoryActive').checked=true;$('saveCategory').textContent='حفظ القسم';}
function editCategory(id){const c=categories.find(x=>x.category_id===id);if(!c)return;$('categoryId').value=c.category_id;$('categoryName').value=c.name;$('categoryParent').value=c.parent_id==null?'':String(c.parent_id);$('categoryOrder').value=c.sort_order;$('categoryActive').checked=c.is_active;$('saveCategory').textContent='حفظ التعديلات';$('categoryName').focus();}
async function toggleCategory(id){const c=categories.find(x=>x.category_id===id);if(!c)return;try{await api('/categories/'+id,{method:'PUT',body:JSON.stringify({name:c.name,parent_id:c.parent_id,sort_order:c.sort_order,is_active:!c.is_active})});notice(c.is_active?'تم إخفاء القسم عن المتجر':'تم نشر القسم');await load()}catch(e){notice('تعذر تغيير القسم: '+e.message,true)}}
async function moveCategory(id,direction){const c=categories.find(x=>x.category_id===id);if(!c)return;const siblings=categories.filter(x=>x.parent_id===c.parent_id).sort((a,b)=>a.sort_order-b.sort_order||a.category_id-b.category_id);const index=siblings.findIndex(x=>x.category_id===id),target=index+direction;if(target<0||target>=siblings.length)return;[siblings[index],siblings[target]]=[siblings[target],siblings[index]];const ordered=[...categories.filter(x=>x.parent_id===null).sort((a,b)=>a.sort_order-b.sort_order||a.category_id-b.category_id)];for(const parent of ordered)ordered.push(...categories.filter(x=>x.parent_id===parent.category_id).sort((a,b)=>a.sort_order-b.sort_order||a.category_id-b.category_id));const siblingIds=new Set(siblings.map(x=>x.category_id));let cursor=0;const finalOrder=ordered.map(x=>siblingIds.has(x.category_id)?siblings[cursor++]:x);try{await api('/categories/reorder',{method:'PUT',body:JSON.stringify({category_ids:finalOrder.map(x=>x.category_id)})});await load()}catch(e){notice('تعذر ترتيب الأقسام: '+e.message,true)}}
$('category').addEventListener('input',updateSubcategorySuggestions);$('clearSectionFilter').onclick=()=>{$('filterCategory').value='';render()};$('refresh').onclick=load;$('resetForm').onclick=reset;$('resetCategory').onclick=resetCategory;$('search').oninput=render;$('filterType').onchange=render;$('filterCategory').onchange=render;$('filterStatus').onchange=render;
const cats={background:'الخلفيات',card_template:'القوالب',badge:'الاستكرات',flag:'الأعلام',shape:'الإطارات',club_logo:'الشعارات',card_color:'ألوان البطاقات'};function updateAssetField(){const canUpload=['background','card_template','badge','flag','shape','club_logo'].includes($('type').value);$('asset').disabled=!canUpload;if(!canUpload){$('asset').value='';clearAssetPreview();}$('assetNote').textContent=canUpload?'حد أقصى 5MB و16 مليون بكسل للصورة؛ GIF المتحرك حتى 60 إطارًا و32 مليون بكسل إجماليًا. للبنرات العريضة اختر نوع «خلفية» واكتب «البانرات» في خانة القسم.':'هذا النوع يعتمد على إعدادات الألوان ولا يحتاج إلى ملف صورة.'} $('type').onchange=()=>{if(!$('category').value||Object.values(cats).includes($('category').value))$('category').value=cats[$('type').value]||'عام';updateAssetField();syncLayoutEditor(true)};
function clearAssetPreview(){if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl='';$('assetPreview').replaceChildren();$('assetPreview').classList.add('hidden')}
$('asset').addEventListener('change',()=>{clearAssetPreview();const file=$('asset').files&&$('asset').files[0];if(!file)return;const allowed=['image/png','image/jpeg','image/webp','image/gif'];if(!allowed.includes(file.type)){notice('اختر PNG أو JPG أو WEBP أو GIF؛ سيجري التحقق الحقيقي من الملف وحدود الحركة على الخادم.',true);$('asset').value='';return}if(file.size>5*1024*1024){notice('حجم الصورة يتجاوز 5MB.',true);$('asset').value='';return}previewUrl=URL.createObjectURL(file);const img=document.createElement('img');img.alt='معاينة صورة المنتج قبل الرفع';img.src=previewUrl;const label=document.createElement('span');label.textContent=file.name+' · '+Math.ceil(file.size/1024)+' KB';$('assetPreview').append(img,label);$('assetPreview').classList.remove('hidden');if($('type').value==='card_template'){layoutMetadata={};layoutNeedsDefault=true}syncLayoutEditor(true)});

function cloneLayoutMetadata(value){try{return JSON.parse(JSON.stringify(value&&typeof value==='object'?value:{}))}catch{return {}}}
function defaultLayoutMetadata(width,height){
 width=Math.max(640,Number(width)||1280);height=Math.max(320,Number(height)||460);
 const scale=Math.min(1,2048/width,1600/height,Math.sqrt(2500000/(width*height)));const w=Math.round(width*scale),h=Math.round(height*scale);
 const portrait=h>w*1.08,layout=portrait?'portrait':(w<=h*1.08?'square':'wide');
 const backgroundArea=layout==='wide'?{x:Math.round(w*.05),y:Math.round(h*.06),width:Math.round(w*.90),height:Math.round(h*.85),fit:'cover'}:{x:Math.round(w*.07),y:Math.round(h*.045),width:Math.round(w*.86),height:Math.round(h*.90),fit:'cover'};
 const avatar=layout==='wide'?{x:Math.round(w*.785),y:Math.round(h*.075),size:Math.round(Math.min(w,h)*.185)}:{x:Math.round(w*.675),y:Math.round(h*.075),size:Math.round(Math.min(w,h)*.165)};
 const name=layout==='wide'?{x:Math.round(w*.755),y:Math.round(h*.335),max_width:Math.round(w*.405),anchor:'ra'}:{x:Math.round(w*.925),y:Math.round(h*.295),max_width:Math.round(w*.425),anchor:'ra'};
 const handle=layout==='wide'?{x:Math.round(w*.755),y:Math.round(h*.385),max_width:Math.round(w*.405),anchor:'ra'}:{x:Math.round(w*.925),y:Math.round(h*.340),max_width:Math.round(w*.425),anchor:'ra'};
 const level=layout==='wide'?{x:Math.round(w*.055),y:Math.round(h*.455),max_width:Math.round(w*.405),anchor:'la'}:{x:Math.round(w*.065),y:Math.round(h*.405),max_width:Math.round(w*.430),anchor:'la'};
 const xp=layout==='wide'?{x:Math.round(w*.055),y:Math.round(h*.520),width:Math.round(w*.685),height:Math.max(8,Math.round(h*.014)),enabled:false}:{x:Math.round(w*.065),y:Math.round(h*.470),width:Math.round(w*.865),height:Math.max(8,Math.round(h*.012)),enabled:false};
 const boxes=layout==='wide'?[[.025,.755,.220,.145],[.265,.755,.220,.145],[.505,.755,.220,.145],[.745,.755,.220,.145]]:[[.055,.655,.410,.115],[.535,.655,.410,.115],[.055,.805,.410,.115],[.535,.805,.410,.115]];
 const keys=[['balance','الرصيد'],['messages','الرسائل'],['rank','الترتيب'],['voice','الصوت']];
 return {layout,width:w,height:h,orientation:portrait?'portrait':(w===h?'square':'landscape'),background_area:backgroundArea,avatar,name,handle,level,xp_bar:xp,badges:{x:Math.round(w*.055),y:Math.round(h*.58),size:Math.max(24,Math.round(h*.03)),limit:5},flag:{x:Math.round(w*.045),y:Math.round(h*.035),width:Math.max(24,Math.round(w*.055)),height:Math.max(18,Math.round(h*.035))},stats:keys.map((v,i)=>({key:v[0],label:v[1],x:Math.round(w*boxes[i][0]),y:Math.round(h*boxes[i][1]),w:Math.round(w*boxes[i][2]),h:Math.round(h*boxes[i][3])}))};
}
function normalizeLayoutMetadata(){
 if(!layoutMetadata||typeof layoutMetadata!=='object'||Array.isArray(layoutMetadata))layoutMetadata={};
 if(!Number.isFinite(Number(layoutMetadata.width))||Number(layoutMetadata.width)<1)layoutMetadata.width=1280;
 if(!Number.isFinite(Number(layoutMetadata.height))||Number(layoutMetadata.height)<1)layoutMetadata.height=460;
 layoutMetadata.width=Math.max(640,Math.min(2048,Math.round(Number(layoutMetadata.width))));
 layoutMetadata.height=Math.max(320,Math.min(1600,Math.round(Number(layoutMetadata.height))));
 const width=layoutMetadata.width,height=layoutMetadata.height,wide=width>height*1.08;
 if(!layoutMetadata.background_area||typeof layoutMetadata.background_area!=='object'||Array.isArray(layoutMetadata.background_area)){
  layoutMetadata.background_area=wide?{x:Math.round(width*.05),y:Math.round(height*.06),width:Math.round(width*.90),height:Math.round(height*.85),fit:'cover'}:{x:Math.round(width*.07),y:Math.round(height*.045),width:Math.round(width*.86),height:Math.round(height*.90),fit:'cover'};
 }
 const area=layoutMetadata.background_area;
 area.x=Math.max(0,Math.min(width-1,Math.round(Number(area.x)||0)));
 area.y=Math.max(0,Math.min(height-1,Math.round(Number(area.y)||0)));
 area.width=Math.max(1,Math.min(width-area.x,Math.round(Number(area.width)||width-area.x)));
 area.height=Math.max(1,Math.min(height-area.y,Math.round(Number(area.height)||height-area.y)));
 area.fit=area.fit==='contain'?'contain':'cover';
 if(!Array.isArray(layoutMetadata.stats))layoutMetadata.stats=[];
 for(const key of ['avatar','name','handle','level','xp_bar','badges','flag'])if(!layoutMetadata[key]||typeof layoutMetadata[key]!=='object')layoutMetadata[key]={};
 return layoutMetadata;
}
const layoutLabels={background_area:'مساحة الخلفية',avatar:'صورة المستخدم',name:'الاسم',handle:'اسم الحساب',level:'المستوى',xp_bar:'شريط الخبرة XP',badges:'الشارات',flag:'العلم',balance:'الرصيد',messages:'الرسائل',rank:'الترتيب',voice:'وقت الصوت',streak:'الأيام'};
function ensureLayoutField(key){
 const meta=normalizeLayoutMetadata();
 if(key.startsWith('stat:')){
  const statKey=key.slice(5);let stat=meta.stats.find(item=>item&&item.key===statKey);
  if(!stat){stat={key:statKey,label:layoutLabels[statKey]||statKey,x:Math.round(meta.width*.5),y:Math.round(meta.height*.82),w:Math.round(meta.width*.22),h:Math.max(28,Math.round(meta.height*.1))};meta.stats.push(stat)}
  return stat;
 }
 if(!meta[key]||typeof meta[key]!=='object')meta[key]={};
 if(key==='avatar'&&!meta.avatar.size)meta.avatar.size=Math.round(Math.min(meta.width,meta.height)*.18);
 if(['name','handle','level'].includes(key)&&!meta[key].max_width)meta[key].max_width=Math.round(meta.width*.4);
 if(key==='badges'){if(!meta.badges.size)meta.badges.size=32;if(!meta.badges.limit)meta.badges.limit=5}
 if(key==='flag'){if(!meta.flag.width)meta.flag.width=60;if(!meta.flag.height)meta.flag.height=40}
 if(key==='xp_bar'){if(!meta.xp_bar.width)meta.xp_bar.width=Math.round(meta.width*.65);if(!meta.xp_bar.height)meta.xp_bar.height=8}
 return meta[key];
}
function layoutKind(key){if(key.startsWith('stat:'))return 'stat';return key==='background_area'?'background':['name','handle','level'].includes(key)?'text':key==='avatar'?'avatar':key==='badges'?'badges':key==='xp_bar'?'xp':key==='flag'?'flag':'other'}
function getLayoutBox(key){
 const meta=normalizeLayoutMetadata(),kind=layoutKind(key),box=ensureLayoutField(key);
 let width=Number(box.w||box.width||box.max_width||box.size||120),height=Number(box.h||box.height||box.size||40);
 if(kind==='avatar')width=height=Number(box.size)||96;
  if(kind==='background'){width=Number(box.width)||Math.round(meta.width*.86);height=Number(box.height)||Math.round(meta.height*.88)}
 if(kind==='badges'){width=Math.max(20,Number(box.size)||32)*Math.max(1,Number(box.limit)||5)+8*(Math.max(1,Number(box.limit)||5)-1);height=Number(box.size)||32}
 if(kind==='text'){width=Number(box.max_width)||Math.round(meta.width*.4);height=key==='name'?44:36}
 return {x:Number(box.x)||0,y:Number(box.y)||0,width,height,anchor:box.anchor||(['level'].includes(key)?'la':'ra'),kind};
}
function writeLayoutBox(key,x,y,width,height){
 const meta=normalizeLayoutMetadata(),kind=layoutKind(key),box=ensureLayoutField(key);
 x=Math.max(0,Math.min(meta.width-1,Math.round(Number(x)||0)));y=Math.max(0,Math.min(meta.height-1,Math.round(Number(y)||0)));
 width=Math.max(1,Math.min(meta.width-x,Math.round(Number(width)||1)));height=Math.max(1,Math.min(meta.height-y,Math.round(Number(height)||1)));
 box.x=x;box.y=y;
 if(kind==='background'){box.width=width;box.height=height;box.fit=box.fit==='contain'?'contain':'cover'}
  else if(kind==='avatar')box.size=Math.max(72,Math.min(Math.floor(Math.min(meta.width,meta.height)/2),Math.round((width+height)/2)));
 else if(kind==='badges')box.size=Math.max(20,Math.min(72,Math.round(height)));
 else if(kind==='text')box.max_width=Math.max(100,Math.min(meta.width,Math.round(width)));
 else if(kind==='xp'){box.width=width;box.height=Math.max(4,Math.min(30,height))}
 else if(kind==='flag'){box.width=width;box.height=height}
 else if(kind==='stat'){box.w=width;box.h=height}
}
function layoutElements(){
 const meta=normalizeLayoutMetadata(),elements=[];
 for(const key of ['background_area','avatar','name','handle','level','xp_bar','badges','flag'])elements.push({key,label:layoutLabels[key],box:getLayoutBox(key),kind:layoutKind(key)});
 for(const stat of meta.stats){if(stat&&stat.key)elements.push({key:'stat:'+stat.key,label:stat.label||layoutLabels[stat.key]||stat.key,box:getLayoutBox('stat:'+stat.key),kind:'stat'})}
 return elements;
}
function displayLayoutLeft(box){return box.kind==='text'?(box.anchor==='ra'?box.x-box.width:box.anchor==='ma'?box.x-box.width/2:box.x):box.x}
function updateLayoutOverlayStyle(element,key){
 const box=getLayoutBox(key),meta=normalizeLayoutMetadata();
 element.style.left=(displayLayoutLeft(box)/meta.width*100)+'%';element.style.top=(box.y/meta.height*100)+'%';element.style.width=(box.width/meta.width*100)+'%';element.style.height=(box.height/meta.height*100)+'%';
}
function renderLayoutEditor(){
 const panel=$('layoutEditorPanel');if(!panel)return;const active=$('type').value==='card_template';panel.classList.toggle('hidden',!active);if(!active)return;
 const meta=normalizeLayoutMetadata(),stage=$('layoutStage'),img=$('layoutStageImage'),empty=$('layoutStageEmpty');
 updateBackgroundPreview();
 const stageMaxHeight=window.innerWidth<=850?380:460;stage.style.maxHeight=stageMaxHeight+'px';stage.style.width='min(100%, '+Math.round(stageMaxHeight*meta.width/meta.height)+'px)';stage.style.aspectRatio=meta.width+' / '+meta.height;
 const item=items.find(x=>String(x.item_id)===String($('itemId').value));
 const selected=$('layoutField').value||'avatar';ensureLayoutField(selected);
 const file=$('asset').files&&$('asset').files[0];const source=file&&previewUrl?previewUrl:(item&&item.has_asset?endpoint('/items/'+item.item_id+'/asset'):'');
 if(source&&img.dataset.layoutSource!==source){img.dataset.layoutSource=source;img.src=source}
 if(!source){img.removeAttribute('src');img.dataset.layoutSource='';img.classList.add('hidden');empty.classList.remove('hidden')}
 else {img.classList.remove('hidden');empty.classList.add('hidden')}
 $('layoutOverlayLayer').innerHTML=layoutElements().map(el=>'<div class="layout-element '+(el.key===selected?'selected':'')+'" data-layout-key="'+escape(el.key)+'" data-kind="'+el.kind+'" data-anchor="'+escape(el.box.anchor||"")+'" role="button" tabindex="0" aria-label="تحريك '+escape(el.label)+'"><span>'+escape(el.label)+'</span>'+(el.key==='background_area'?'<i class="layout-resize nw" data-corner="nw" aria-hidden="true"></i><i class="layout-resize ne" data-corner="ne" aria-hidden="true"></i><i class="layout-resize sw" data-corner="sw" aria-hidden="true"></i><i class="layout-resize se" data-corner="se" aria-hidden="true"></i>':'<i class="layout-resize se" data-corner="se" aria-hidden="true"></i>')+'</div>').join('');
 $('layoutOverlayLayer').querySelectorAll('[data-layout-key]').forEach(el=>updateLayoutOverlayStyle(el,el.dataset.layoutKey));
 updateLayoutControls();
}
function updateBackgroundPreview(){const preview=$('layoutBackgroundPreview'),select=$('layoutBackgroundSource');if(!preview||!select)return;const id=select.value,item=items.find(value=>String(value.item_id)===String(id)&&value.item_type==='background'&&value.has_asset),meta=normalizeLayoutMetadata(),area=meta.background_area;if(!item){preview.removeAttribute('src');preview.classList.add('hidden');preview.dataset.source='';return}const source=assetUrl(item.item_id);if(preview.dataset.source!==source){preview.dataset.source=source;preview.src=source}preview.classList.remove('hidden');preview.style.left=(area.x/meta.width*100)+'%';preview.style.top=(area.y/meta.height*100)+'%';preview.style.width=(area.width/meta.width*100)+'%';preview.style.height=(area.height/meta.height*100)+'%';preview.style.objectFit=area.fit==='contain'?'contain':'cover';preview.style.background='rgba(8,11,20,.5)'}
function syncLayoutEditor(force){if(force)layoutImageSource='';renderLayoutEditor()}
function updateLayoutControls(){
 const key=$('layoutField').value||'avatar';ensureLayoutField(key);const box=getLayoutBox(key),meta=normalizeLayoutMetadata();
 $('layoutX').max=meta.width;$('layoutY').max=meta.height;$('layoutWidth').max=meta.width;$('layoutHeight').max=meta.height;
 $('layoutX').value=Math.round(box.x);$('layoutY').value=Math.round(box.y);$('layoutWidth').value=Math.round(box.width);$('layoutHeight').value=Math.round(box.height);
 $('layoutAnchor').disabled=box.kind!=='text';$('layoutAnchor').value=box.anchor==='la'?'la':'ra';
 $('backgroundFit').disabled=key!=='background_area';$('backgroundFit').value=meta.background_area.fit||'cover';
 $('layoutXpEnabled').disabled=key!=='xp_bar';$('layoutXpEnabled').checked=!!(meta.xp_bar&&meta.xp_bar.enabled);
 $('layoutHelp').textContent=key==='background_area'?'حدد مساحة ظهور صورة الخلفية داخل القالب بالسحب أو بتعديل X/Y والعرض والارتفاع. تُركّب صورة الخلفية داخل هذا المستطيل فقط، ويبقى إطار القالب فوقها. اختر ملء المساحة للقص المتناسب أو إظهار الصورة كاملة دون قص.':key==='xp_bar'?'يمكن تحديد موضع شريط الخبرة وحجمه. تفعيل الرسم يرسم نسبة XP الحقيقية؛ اتركه متوقفاً إذا كان الشريط مرسوماً داخل صورة القالب.':box.kind==='text'?'يتحكم العرض في المساحة القصوى للنص، وتحدد المحاذاة اتجاه تثبيت نقطة الإحداثيات.':'اسحب العنصر فوق الصورة، أو أدخل قيم X/Y والعرض والارتفاع بدقة.';
}
function applyLayoutInputs(){
 const key=$('layoutField').value||'avatar',box=getLayoutBox(key);let x=Number($('layoutX').value),y=Number($('layoutY').value),w=Number($('layoutWidth').value),h=Number($('layoutHeight').value);
 if(![x,y,w,h].every(Number.isFinite)||w<=0||h<=0){notice('أدخل مواضع وأبعاد صحيحة للقالب.',true);return}
 writeLayoutBox(key,x,y,w,h);const b=ensureLayoutField(key);if(box.kind==='text')b.anchor=$('layoutAnchor').value==='la'?'la':'ra';
 if(key==='background_area')b.fit=$('backgroundFit').value==='contain'?'contain':'cover';
 if(key==='xp_bar')b.enabled=$('layoutXpEnabled').checked;
 renderLayoutEditor();
}
function startLayoutPointer(event){
 const el=event.target.closest('[data-layout-key]');if(!el)return;const key=el.dataset.layoutKey;if(!key)return;
 $('layoutField').value=key;ensureLayoutField(key);const box=getLayoutBox(key),stage=$('layoutStage').getBoundingClientRect();
 layoutDrag={key,kind:box.kind,anchor:box.anchor,mode:event.target.closest('.layout-resize')?'resize':'move',corner:(event.target.closest('.layout-resize')&&event.target.closest('.layout-resize').dataset.corner)||'se',pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,x:box.x,y:box.y,width:box.width,height:box.height,stageWidth:Math.max(1,stage.width),stageHeight:Math.max(1,stage.height),target:el};
 if(el.setPointerCapture)try{el.setPointerCapture(event.pointerId)}catch{}
 $('layoutOverlayLayer').querySelectorAll('[data-layout-key]').forEach(item=>item.classList.toggle('selected',item===el));updateLayoutControls();event.preventDefault();
}
function moveLayoutPointer(event){
 if(!layoutDrag||event.pointerId!==layoutDrag.pointerId)return;const meta=normalizeLayoutMetadata(),dx=(event.clientX-layoutDrag.startX)*meta.width/layoutDrag.stageWidth,dy=(event.clientY-layoutDrag.startY)*meta.height/layoutDrag.stageHeight;
 let x=layoutDrag.x,y=layoutDrag.y,w=layoutDrag.width,h=layoutDrag.height;
 if(layoutDrag.mode==='move'){x+=dx;y+=dy}else if(layoutDrag.key==='background_area'){const c=layoutDrag.corner;if(c.includes('w')){x+=dx;w-=dx}else{w+=dx}if(c.includes('n')){y+=dy;h-=dy}else{h+=dy}}else{w+=(layoutDrag.kind==='text'&&layoutDrag.anchor==='ra'?-dx:dx);h+=dy}
 writeLayoutBox(layoutDrag.key,x,y,w,h);
 if(layoutDrag.key==='background_area')updateBackgroundPreview();
 const el=layoutDrag.target;if(el&&el.isConnected)updateLayoutOverlayStyle(el,layoutDrag.key);
 const box=getLayoutBox(layoutDrag.key);$('layoutX').value=Math.round(box.x);$('layoutY').value=Math.round(box.y);$('layoutWidth').value=Math.round(box.width);$('layoutHeight').value=Math.round(box.height);
}
function endLayoutPointer(event){if(!layoutDrag||event.pointerId!==layoutDrag.pointerId)return;layoutDrag=null;renderLayoutEditor()}
$('layoutBackgroundSource').addEventListener('change',updateBackgroundPreview);
$('layoutField').addEventListener('change',()=>{ensureLayoutField($('layoutField').value);renderLayoutEditor()});
$('applyLayout').addEventListener('click',applyLayoutInputs);
$('saveLayout').addEventListener('click',async()=>{const id=String($('itemId').value||'');if(!id){notice('احفظ المنتج أولاً، ثم احفظ تخطيط القالب.',true);return}const item=items.find(value=>String(value.item_id)===id);if(!item||item.item_type!=='card_template'){notice('اختر قالباً محفوظاً أولاً.',true);return}const button=$('saveLayout');button.disabled=true;try{const desired=payload();const result=await api('/items/'+id,{method:'PUT',body:JSON.stringify({...desired,is_active:!!item.is_active,is_featured:!!item.is_featured})});const saved=result.item;if(!saved||JSON.stringify(saved.metadata||{})!==JSON.stringify(desired.metadata)){await load();const fresh=items.find(value=>String(value.item_id)===id);if(!fresh||JSON.stringify(fresh.metadata||{})!==JSON.stringify(desired.metadata))throw new Error('لم تتطابق الإعدادات بعد إعادة قراءتها من قاعدة البيانات.')}await load();edit(Number(id));notice('تم حفظ التخطيط والتحقق منه بعد إعادة تحميل بيانات المتجر.')}catch(error){notice('تعذر حفظ التخطيط: '+error.message,true)}finally{button.disabled=false}});
$('backgroundFit').addEventListener('change',()=>{ensureLayoutField('background_area').fit=$('backgroundFit').value==='contain'?'contain':'cover';renderLayoutEditor()});
$('layoutXpEnabled').addEventListener('change',()=>{const xp=ensureLayoutField('xp_bar');xp.enabled=$('layoutXpEnabled').checked;renderLayoutEditor()});
$('resetLayout').addEventListener('click',()=>{
 const img=$('layoutStageImage');
 if(!img.naturalWidth||!img.naturalHeight){notice('اختر صورة القالب أولاً حتى يمكن إنشاء تخطيط افتراضي.',true);return}
 layoutMetadata=defaultLayoutMetadata(img.naturalWidth,img.naturalHeight);layoutNeedsDefault=false;renderLayoutEditor();notice('أُعيد التخطيط إلى المواضع الافتراضية. احفظ المنتج لتثبيته.')
});
$('layoutOverlayLayer').addEventListener('pointerdown',startLayoutPointer);
document.addEventListener('pointermove',moveLayoutPointer);
document.addEventListener('pointerup',endLayoutPointer);
window.addEventListener('resize',()=>renderLayoutEditor());
$('layoutStageImage').addEventListener('load',()=>{
 const img=$('layoutStageImage');
 if(img.naturalWidth&&img.naturalHeight&&layoutNeedsDefault){layoutMetadata=defaultLayoutMetadata(img.naturalWidth,img.naturalHeight);layoutNeedsDefault=false;renderLayoutEditor()}
});

document.querySelectorAll('[data-admin-tab]').forEach(button=>button.addEventListener('click',()=>switchAdminTab(button.dataset.adminTab)));
$('designLoadTemplate').addEventListener('click',()=>{
 const selected=items.find(item=>String(item.item_id)===String($('designTemplateSelect').value)&&item.item_type==='card_template');
 if(!selected){notice('اختر قالب بطاقة محفوظًا أولًا.',true);return}
 edit(Number(selected.item_id));
 switchAdminTab('design');
 $('designStatus').textContent='القالب «'+selected.name+'» مفتوح للتحرير. حرّك العناصر أو اضبط قيمها ثم اضغط حفظ التخطيط.';
});
$('designTemplateSelect').addEventListener('change',()=>{
 const selected=items.find(item=>String(item.item_id)===String($('designTemplateSelect').value)&&item.item_type==='card_template');
 $('designStatus').textContent=selected?'سيتم فتح «'+selected.name+'» في الاستوديو عند الضغط على زر الفتح.':'اختر قالبًا محفوظًا لبدء التحرير.';
});
$('designManageProducts').addEventListener('click',()=>switchAdminTab('products'));
switchAdminTab('overview');
updateAssetField();resetCategory();
load();
})();
