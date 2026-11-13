/* Local content studio. All persistent writes go through the loopback service. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const clone = value => JSON.parse(JSON.stringify(value));
  const list = value => String(value || '').split(/[,，;；\n]+/).map(x => x.trim()).filter(Boolean);
  const joined = value => (value || []).join('，');
  const number = (value, fallback = 1) => value === '' || !Number.isFinite(Number(value)) ? fallback : Number(value);
  const kindNames = {'character-art':'角色立绘','character-sheet':'角色设定图','key-visual':'作品主视觉','illustration':'插画','wallpaper':'壁纸','screenshot':'截图','photo':'照片','logo':'标志','other':'其他'};
  const useNames = {hero:'首页轮播',gallery:'画廊展示',avatar:'头像候选',card:'卡片配图',reference:'设定资料'};
  const effectNames = {auto:'随图片自动选择',petals:'花瓣',snow:'雪与冰晶',sparks:'火花',stars:'星光',motes:'浮尘',none:'不显示粒子'};
  const viewNames = {assets:'素材库',works:'我的作品',ips:'IP 档案',characters:'角色名册',settings:'轮播设置',delivery:'发布与历史'};
  let state = null, view = 'assets', selectedUid = '', editingNew = false, importing = false;
  let dirty = false, busy = false, importMode = 'files', chosenFiles = [], toastTimer;
  const bootParams = new URLSearchParams(location.hash.slice(1));
  let token = bootParams.get('token') || '';
  try { if (token) sessionStorage.setItem('sora-studio-token', token); else token = sessionStorage.getItem('sora-studio-token') || ''; } catch (_) {}
  if (bootParams.has('token')) history.replaceState(null, '', location.pathname + location.search);
  const group = () => view === 'works' ? 'assets' : view;
  const lib = () => state?.library || {ips:[],characters:[],assets:[],settings:{}};
  const byId = (type, id) => lib()[type]?.find(x => x.id === id);
  const current = () => lib()[group()]?.find(x => (x.uid || x.id) === selectedUid);
  const ipName = id => byId('ips', id)?.name || '未归属';
  const artworkIp = () => lib().ips.find(x => x.systemRole === 'owner-works')?.id || lib().ips.find(x => x.name === '我的作品')?.id || '';
  const thumb = asset => `media/derived/${encodeURIComponent(asset.id)}.thumb.webp`;
  const safeLink = value => { try { const u = new URL(value, location.href); return /^(https?:)$/.test(u.protocol) ? u.href : ''; } catch (_) { return ''; } };

  function notice(message, type = '') { $('#status').textContent = message; $('#status').className = `status ${type}`; }
  function toast(message, error = false) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').className = `toast${error ? ' error' : ''}`; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 7500 : 4200); }
  function markDirty(value) { dirty = value; const el = $('#edit-state'); el.textContent = value ? '有修改，待保存' : '已保存'; el.className = `pill${value ? ' pending' : ''}`; }
  function canLeave() { return !dirty || window.confirm('当前填写的内容还没有保存。离开后会丢弃这些修改，继续吗？'); }
  function setBusy(value) {
    busy = value;
    $('#record-form').setAttribute('aria-busy', String(value));
    $$('#record-form input, #record-form select, #record-form textarea, #record-form button, #asset-preview input, #asset-preview button').forEach(el => {
      if (value) { el.dataset.preBusyDisabled = el.disabled ? '1' : '0'; el.disabled = true; }
      else if ('preBusyDisabled' in el.dataset) { el.disabled = el.dataset.preBusyDisabled === '1'; delete el.dataset.preBusyDisabled; }
    });
    $('#build-preview').disabled = value || !state;
    $('#publish-site').disabled = value || !state;
    $('#new-record').disabled = value || !state;
    $('#refresh-state').disabled = value || !state;
    $$('.sidebar button').forEach(el => { el.disabled = value; });
    $$('.record-item').forEach(el => { el.disabled = value; });
  }
  async function api(path, data, raw) {
    const headers = {'X-Studio-Token':token, 'Accept':'application/json'};
    const options = {headers, cache:'no-store'};
    if (data !== undefined || raw !== undefined) { options.method = 'POST'; if (raw !== undefined) { options.body = raw; headers['Content-Type'] = 'application/octet-stream'; } else { options.body = JSON.stringify(data); headers['Content-Type'] = 'application/json'; } }
    let response;
    try { response = await fetch(path, options); } catch (_) { throw new Error('本地工作台暂时没有响应。请检查启动窗口是否仍在运行，再重试。'); }
    let payload;
    try { payload = await response.json(); } catch (_) { throw new Error('工作台返回了无法读取的结果，请重新打开启动脚本后重试。'); }
    if (!response.ok) {
      const error = new Error(payload.error || `操作未完成（${response.status}）`);
      error.code = response.status;
      if (response.status === 409) error.message += ' 这页内容已保留；请先记下未保存的修改，再点列表上方“刷新”载入最新版本。';
      if (response.status === 401 || response.status === 403) error.message += ' 请通过项目里的“打开素材工作台”启动入口重新进入。';
      throw error;
    }
    return payload;
  }
  function failed(error) { notice(error.message || String(error), 'error'); toast(error.message || String(error), true); }
  function savedNotice(payload, fallback) { const warnings = payload.warnings || payload.meta?.warnings || payload.state?.meta?.warnings || []; notice((payload.message || fallback) + (warnings.length ? ' 提示：' + warnings.join('；') : ''),warnings.length ? 'notice' : ''); }
  function acceptState(payload) { const next = payload?.library ? payload : payload?.state; if (!next?.library) return false; state = next; return true; }
  async function refresh(showMessage = true) {
    const payload = await api('/api/state');
    if (!acceptState(payload)) throw new Error('素材库数据不完整，未覆盖当前页面。');
    renderSummary(); renderFilters(); renderList(); renderEditor(); renderConnection();
    if (showMessage) notice('已读取项目中的素材库。保存后会自动整理文件和关联，再生成预览查看效果。');
  }
  function renderConnection() {
    const git = state?.git || {};
    $('#connection-note').textContent = git.available && git.branch ? `本地工作台 · 当前分支 ${git.branch}` : '本地工作台只在这台电脑运行。';
  }
  function renderSummary() {
    const library = lib();
    const values = [['assets',library.assets.length,'张素材'],['ips',library.ips.length,'个世界'],['characters',library.characters.length,'位角色'],['works',library.assets.filter(x => x.ownerWork).length,'张本人作品']];
    $('#library-summary').innerHTML = values.slice(0,3).map(([,count,label]) => `<div><b>${count}</b><span>${label}</span></div>`).join('');
    values.forEach(([name,count]) => { const el = $(`[data-count="${name}"]`); if (el) el.textContent = count; });
  }
  function options(records, selected, prompt = null) { return (prompt !== null ? `<option value="">${esc(prompt)}</option>` : '') + records.map(x => `<option value="${esc(x.id)}"${x.id === selected ? ' selected' : ''}>${esc(x.name || x.label)}</option>`).join(''); }
  function renderFilters() {
    const select = $('#ip-filter'), before = select.value;
    select.innerHTML = options(lib().ips, before, '所有 IP');
    select.disabled = view === 'ips';
  }
  function filteredRecords() {
    let records = lib()[group()] || [];
    if (view === 'works') records = records.filter(x => x.ownerWork);
    const search = $('#search').value.trim().toLocaleLowerCase(), ip = $('#ip-filter').value, status = $('#state-filter').value;
    return records.filter(record => {
      if (ip && view !== 'ips' && record.ipId !== ip) return false;
      if (status === 'enabled' && record.enabled === false || status === 'disabled' && record.enabled !== false) return false;
      if (!search) return true;
      const text = [record.name,record.label,record.caption,record.description,record.notes,record.favorite,ipName(record.ipId),...(record.tags || []),...(record.aliases || []),...(record.characterIds || []).map(id => byId('characters',id)?.name || '')].join(' ').toLocaleLowerCase();
      return text.includes(search);
    });
  }
  function imageFallbacks(container) { container.querySelectorAll('img[data-original]').forEach(img => { img.addEventListener('error', () => { if (!img.dataset.retried) { img.dataset.retried = '1'; img.src = img.dataset.original; } else { img.style.opacity = '.25'; img.alt = '图片暂未生成'; } }); }); }
  function renderList() {
    $('#collection-title').textContent = viewNames[view];
    $('#new-record').textContent = group() === 'assets' ? '＋ 导入' : '＋ 新建';
    $('#new-record').title = group() === 'assets' ? '导入本地图片或下载图片网址' : `新建${view === 'ips' ? 'IP' : '角色'}`;
    const records = filteredRecords();
    $('#list-count').textContent = `${records.length} 条记录`;
    $('#record-list').innerHTML = records.length ? records.map(record => {
      const id = record.uid || record.id, selected = !importing && !editingNew && id === selectedUid;
      let description, mark;
      if (group() === 'assets') { description = `${ipName(record.ipId)} · ${kindNames[record.kind] || '素材'}`; mark = `<img src="${esc(thumb(record))}" data-original="${esc(record.file)}" alt="" loading="lazy">`; }
      else if (view === 'characters') { const count = lib().assets.filter(x => x.characterIds?.includes(record.id)).length; description = `${ipName(record.ipId)} · ${count} 张素材`; mark = '<span class="record-icon">♧</span>'; }
      else { const count = lib().assets.filter(x => x.ipId === record.id).length; description = `${count} 张素材 · ${record.rotation?.enabled ? '轮播已开启' : '收在档案里'}`; mark = '<span class="record-icon">◈</span>'; }
      return `<button type="button" class="record-item${selected ? ' selected' : ''}" data-uid="${esc(id)}" aria-pressed="${selected}">${mark}<span class="record-copy"><strong>${esc(record.name || record.label)}</strong><small>${esc(description)}</small></span>${record.enabled === false ? '<span class="record-off">停用</span>' : '<span class="record-dot" aria-label="已启用"></span>'}</button>`;
    }).join('') : `<div class="empty-list">${view === 'works' ? '还没有导入本人作品。<br>点“导入”，选择你自己的原图。' : '这里还没有匹配的记录。<br>试试更换关键词，或新增一条。'}</div>`;
    imageFallbacks($('#record-list'));
    $$('.record-item').forEach(button => button.addEventListener('click', () => {
      if (busy || !canLeave()) return;
      selectedUid = button.dataset.uid; importing = false; editingNew = false; clearFiles(); markDirty(false); renderList(); renderEditor();
    }));
  }
  function field(label, control, hint = '', wide = false) { return `<label class="field${wide ? ' wide' : ''}"><span>${label}</span>${control}${hint ? `<small>${hint}</small>` : ''}</label>`; }
  function input(name, value = '', type = 'text', extra = '') { return `<input name="${name}" type="${type}" value="${esc(value)}" ${extra}>`; }
  function area(name, value = '', extra = '') { return `<textarea name="${name}" ${extra}>${esc(value)}</textarea>`; }
  function check(name, checked, label, extra = '') { return `<label class="check-row"><input type="checkbox" name="${name}"${checked ? ' checked' : ''} ${extra}>${label}</label>`; }
  function weight(value) { return field('出现偏好',input('weight',value ?? 1,'number','min="0" max="100" step="0.1" required'),'默认 1；越大越常出现。填 0 则暂不随机出现。'); }
  function usesCheckboxes(values = []) { return `<div class="checks">${Object.entries(useNames).map(([key,label]) => check('uses',values.includes(key),label,`value="${key}"`)).join('')}</div>`; }
  function characterChoices(ipId, values = []) {
    const records = lib().characters.filter(x => x.ipId === ipId);
    return records.length ? records.map(record => `<label><input type="checkbox" name="characterIds" value="${esc(record.id)}"${values.includes(record.id) ? ' checked' : ''}>${esc(record.name)}</label>`).join('') : '<span class="field-help">这个 IP 还没有角色。可以先导入场景图，之后再到“角色名册”补上。</span>';
  }
  function baseAssetFields(record, isImport = false) {
    const source = record.source || {}, uses = record.uses || ['hero','gallery'];
    return `<fieldset><legend>${isImport ? '这批图片放在哪里？' : '名称与归属'}</legend><div class="fields">${field(record.ownerWork ? '作品标题' : isImport ? '图片标题' : '素材标题',input('label',record.label || '','text',isImport ? 'placeholder="留空则使用原文件名或所属角色" maxlength="120"' : 'required maxlength="120"'),isImport ? '多张图片会自动区分；你可以在导入后逐张补充标题。' : '更名会同步整理文件名与引用，不需要自己改路径。',true)}${field('所属 IP',`<select name="ipId" required>${options(lib().ips,record.ipId,'请选择一个 IP')}</select>`,'找不到时，先到“IP 档案”新建。')}${field('图片类型',`<select name="kind">${Object.entries(kindNames).map(([key,label]) => `<option value="${key}"${record.kind === key ? ' selected' : ''}>${label}</option>`).join('')}</select>`)}<div class="field wide"><span>出场角色 <small>可多选；场景、作品主视觉也可以不选角色。</small></span><div class="character-picks">${characterChoices(record.ipId,record.characterIds)}</div></div></div></fieldset>
      <fieldset><legend>让它在哪里出现？</legend><div class="fields"><div class="field wide">${usesCheckboxes(uses)}<small>首页轮播还需要开启所属 IP 的轮播。设定资料不会自动进入首页。</small></div>${weight(record.weight)}<div class="field">${check('enabled',record.enabled !== false,'启用这张素材')}${check('ownerWork',!!record.ownerWork,'这是我的作品')}<small>本人作品会进入个人作品画廊，请只勾选自己创作的内容。</small></div>${field('标签',input('tags',joined(record.tags),'text','placeholder="例如：生日、冬日、收藏"'),'用逗号分隔，方便搜索和整理。',true)}</div></fieldset>
      <fieldset data-owner-description${record.ownerWork ? '' : ' hidden'}><legend>作品介绍</legend><div class="fields">${field('作品简介',area('description',record.description || '','rows="5" maxlength="50000" placeholder="粘贴作品页的原始简介，或写下你想展示给访客的话…"'),'与整理备注分开保存；按纯文字展示，保留换行，不执行 HTML。',true)}</div></fieldset><fieldset><legend>出处与备注</legend><div class="fields">${field('来源作品页',input('sourcePage',source.page || '','url','placeholder="https://…"'),'Pixiv 作品链接也填在这里；它用于标注出处，不作为图片下载地址。',true)}${field('图片原地址（可选）',input('sourceImage',source.image || '','url','placeholder="https://…"'),'',true)}${field('作者 / 权利说明',input('rights',source.rights || '','text','placeholder="例如：本人作品 / 原作者名"'),'',true)}${field('整理备注',area('notes',record.notes || '','placeholder="记下来源、版本、偏好，或者以后想替换的高清图…"'),'',true)}</div></fieldset>${appearanceFields(record.appearance || {})}`;
  }
  function appearanceFields(appearance) {
    const focal = appearance.focalPoint || [78,40];
    return `<details class="appearance-options"><summary>画面适配 · 默认自动分析颜色、特效与动效，可按需微调</summary><div class="fields">${field('图片放置方式',`<select name="fit"><option value="auto"${!appearance.fit || appearance.fit === 'auto' ? ' selected' : ''}>自动判断画幅</option><option value="cover"${appearance.fit === 'cover' ? ' selected' : ''}>铺满画面</option><option value="contain"${appearance.fit === 'contain' ? ' selected' : ''}>完整保留图片</option></select>`)}${field('粒子效果',`<select name="effect">${Object.entries(effectNames).map(([key,label]) => `<option value="${key}"${(appearance.effect || 'auto') === key ? ' selected' : ''}>${label}</option>`).join('')}</select>`)}<div class="field"><span>主题颜色</span><div class="auto-row">${check('autoAccent',!appearance.accent,'自动提取')}<input type="color" name="accent" aria-label="手动主题颜色" value="${esc(appearance.accent || '#557b68')}"${!appearance.accent ? ' disabled' : ''}></div><small>自动模式会从原图取色，并照顾文字可读性。</small></div><div class="field"><span>动效强度</span>${check('autoMotion',appearance.motion == null,'自动判断')}<div class="range-line"><input type="range" name="motion" min="0" max="1" step="0.05" value="${appearance.motion ?? .5}"${appearance.motion == null ? ' disabled' : ''} aria-label="动效强度"><output>${Math.round((appearance.motion ?? .5)*100)}%</output></div></div>${field('主角位置 · 水平',`<div class="range-line"><input type="range" name="focalX" min="0" max="100" value="${focal[0]}" aria-label="主角水平位置"><output>${focal[0]}%</output></div>`,'左侧 0% → 右侧 100%')}${field('主角位置 · 垂直',`<div class="range-line"><input type="range" name="focalY" min="0" max="100" value="${focal[1]}" aria-label="主角垂直位置"><output>${focal[1]}%</output></div>`,'顶部 0% → 底部 100%')}</div></details>`;
  }
  function renderEditor() {
    const special = view === 'settings' || view === 'delivery';
    $('.workspace').classList.toggle('single-panel',special); $('#collection-panel').hidden = special;
    $('#asset-preview').hidden = true; $('#asset-preview').innerHTML = ''; $('#probabilities').innerHTML = '';
    $('#record-form').innerHTML = ''; $('#edit-state').hidden = view === 'delivery';
    if (view === 'settings') return renderSettings();
    if (view === 'delivery') return renderDelivery();
    if (importing) return renderImport();
    let record = current();
    if (editingNew) record = view === 'ips' ? {name:'',enabled:true,rotation:{enabled:false,weight:1},tags:[],weight:1} : {name:'',ipId:$('#ip-filter').value || '',enabled:true,weight:1,tags:[],aliases:[]};
    if (!record) {
      $('#editor-title').textContent = view === 'works' ? '你的作品，值得一个位置。' : '选一条记录，慢慢整理。';
      $('#editor-kicker').textContent = 'A PLACE FOR YOUR FAVORITES';
      $('#record-form').innerHTML = `<div class="empty-state"><span class="empty-symbol">✧</span><h3>${view === 'works' ? '把自己的画挂上来' : '今天想收藏什么？'}</h3><p>${view === 'works' ? '选择本地原图，或填写可公开下载的图片直链。可以附上 Pixiv 作品链接，主页会把它们展示在作品画廊。' : '从左边选择已有内容，或者新建一条。ID、文件名和保存位置由工作台自动处理。'}</p><button type="button" class="primary" id="empty-add">${group() === 'assets' ? '＋ 导入图片' : '＋ 新建记录'}</button></div>`;
      $('#empty-add').addEventListener('click',newRecord); markDirty(false); return;
    }
    $('#editor-title').textContent = editingNew ? `新建${view === 'ips' ? 'IP' : '角色'}` : record.name || record.label;
    $('#editor-kicker').textContent = {ips:'WORLDS YOU LOVE',characters:'A FAMILIAR FACE',assets:'THE PICTURE NOTEBOOK',works:'YOUR OWN GALLERY'}[view];
    let body = '';
    if (view === 'ips') {
      body = `<fieldset><legend>记录这个世界</legend><div class="fields">${field('IP 名称',input('name',record.name,'text','required maxlength="100" placeholder="例如：原神"'),'所有 IP 平等管理。名字改动后，关联的角色与素材会一起更新。',true)}${field('系列 / 子作品',input('series',joined(record.series),'text','placeholder="例如：Fate/stay night，Fate/Zero"'),'用逗号分隔；不填也可以。',true)}${field('偏爱角色 / 一句话偏好',input('favorite',record.favorite || ''),'',true)}${field('主题小记',area('caption',record.caption || '','placeholder="想写在这个世界旁边的一句话…"'),'',true)}${field('标签',input('tags',joined(record.tags),'text','placeholder="例如：游戏，动画，重点偏爱"'),'用逗号分隔。',true)}${field('整理备注',area('notes',record.notes || ''),'',true)}</div></fieldset><fieldset><legend>出场安排</legend><div class="fields"><div>${check('enabled',record.enabled !== false,'启用这个 IP')}${check('rotationEnabled',!!record.rotation?.enabled,'加入首页主题轮播')}<small class="field-help">只要有启用的首页轮播图片，就可以开始登场。</small></div>${field('首页轮播偏好',input('rotationWeight',record.rotation?.weight ?? 1,'number','min="0" max="100" step="0.1" required'),'默认 1；越大越常切换到这个 IP。')}${weight(record.weight)}</div></fieldset>`;
    } else if (view === 'characters') {
      body = `<fieldset><legend>认识这位角色</legend><div class="fields">${field('角色名字',input('name',record.name,'text','required maxlength="100" placeholder="例如：刻晴"'),'更名会同步整理关联素材，不会丢失关系。',true)}${field('所属 IP',`<select name="ipId" required>${options(lib().ips,record.ipId,'请选择一个 IP')}</select>`,'角色已有素材时，跨 IP 调整会校验关联。',true)}${field('别名 / 常用称呼',input('aliases',joined(record.aliases),'text','placeholder="例如：拉芙希妮，焰影苇草"'),'用逗号分隔。',true)}${field('标签',input('tags',joined(record.tags)),'',true)}${field('角色备注',area('notes',record.notes || '','placeholder="记下设定、称呼、偏好或资料线索…"'),'',true)}${weight(record.weight)}<div>${check('enabled',record.enabled !== false,'启用这位角色')}<small class="field-help">停用后，包含该角色的素材会停止随机出现。</small></div></div></fieldset>`;
    } else {
      renderAssetPreview(record);
      body = baseAssetFields(record);
    }
    $('#record-form').innerHTML = body + '<div class="form-actions"><button type="submit" class="primary">保存到项目</button><p>自动校验、命名并更新关联。保存完成后，点上方“生成预览”更新页面。</p></div>';
    bindForm(); renderProbabilities(record); markDirty(false);
  }
  function renderAssetPreview(record) {
    const preview = $('#asset-preview'); preview.hidden = false;
    const names = (record.characterIds || []).map(id => byId('characters',id)?.name).filter(Boolean);
    const workSource = record.source?.page ? safeLink(record.source.page) : '';
    preview.innerHTML = `<img src="${esc(thumb(record))}" data-original="${esc(record.file)}" alt="${esc(record.label)}"><div class="preview-copy"><p><b>${esc(ipName(record.ipId))}</b>${names.length ? ` / ${esc(names.join('、'))}` : ''}</p><p>${esc(kindNames[record.kind] || '素材')} · ${(record.uses || []).map(x => esc(useNames[x])).join(' / ') || '尚未设置展示用途'}</p>${record.ownerWork ? `<p>${workSource ? `<a href="${esc(workSource)}" target="_blank" rel="noopener noreferrer">打开原作品页面 ↗</a>` : '尚未填写作品出处，可在下方补上原作品链接。'}</p>` : ''}<p>原图已复制在项目中。替换时保留所有配置，重新分析配色与动效。</p><div class="button-row"><a href="${esc(record.file)}" target="_blank" rel="noopener">查看原图 ↗</a><label class="button small file-trigger">替换原图<input id="replace-file" type="file" accept="image/png,image/jpeg,image/webp,image/avif,image/gif" aria-label="选择替换原图"></label></div></div>`;
    imageFallbacks(preview);
    $('#replace-file').addEventListener('change',async event => {
      const file = event.target.files[0]; if (!file) return;
      if (dirty) { notice('请先保存当前表单，再替换原图。这样可以保留刚填写的内容。','notice'); event.target.value = ''; return; }
      setBusy(true);
      try {
        notice('正在检查并复制替换图片…');
        const uploaded = await uploadFile(file);
        const result = await api('/api/replace',{id:record.id,uploadId:uploaded.uploadId,revision:state.revision});
        acceptState(result); if (!result.library && !result.state) await refresh(false);
        markDirty(false); renderSummary(); renderList(); renderEditor(); savedNotice(result,'原图已替换。点击“生成预览”，更新图片分析和主页。'); toast('原图已替换，配置已保留。');
      } catch (error) { failed(error); } finally { setBusy(false); }
    });
  }
  function bindForm() {
    const form = $('#record-form');
    form.oninput = () => markDirty(true); form.onchange = () => markDirty(true);
    const ipSelect = form.elements.namedItem('ipId');
    if (ipSelect && $('.character-picks')) ipSelect.addEventListener('change',() => { $('.character-picks').innerHTML = characterChoices(ipSelect.value); });
    form.querySelectorAll('input[type=range]').forEach(range => range.addEventListener('input',() => { const output = range.parentElement.querySelector('output'); if (output) output.textContent = `${range.name === 'motion' ? Math.round(range.value*100) : range.value}%`; }));
    const ownerWork = form.elements.namedItem('ownerWork'), descriptionSection = form.querySelector('[data-owner-description]');
    if (ownerWork && descriptionSection) ownerWork.addEventListener('change',() => { descriptionSection.hidden = !ownerWork.checked; });
    const autoAccent = form.elements.namedItem('autoAccent'), autoMotion = form.elements.namedItem('autoMotion');
    if (autoAccent) autoAccent.addEventListener('change',() => { form.elements.namedItem('accent').disabled = autoAccent.checked; });
    if (autoMotion) autoMotion.addEventListener('change',() => { form.elements.namedItem('motion').disabled = autoMotion.checked; });
    form.onsubmit = saveRecord;
  }
  function readAsset(form) {
    const data = new FormData(form), appearance = {fit:data.get('fit') || 'auto',effect:data.get('effect') || 'auto',focalPoint:[number(data.get('focalX'),78),number(data.get('focalY'),40)]};
    if (!data.has('autoAccent')) appearance.accent = data.get('accent') || '#557b68';
    if (!data.has('autoMotion')) appearance.motion = number(data.get('motion'),.5);
    return {label:String(data.get('label') || '').trim(),ipId:data.get('ipId'),characterIds:data.getAll('characterIds'),kind:data.get('kind'),uses:data.getAll('uses'),weight:number(data.get('weight')),enabled:data.has('enabled'),ownerWork:data.has('ownerWork'),tags:list(data.get('tags')),description:data.get('description') || '',notes:data.get('notes') || '',source:{page:data.get('sourcePage') || '',image:data.get('sourceImage') || '',rights:data.get('rights') || ''},appearance};
  }
  async function saveRecord(event) {
    event.preventDefault(); if (busy) return;
    const form = event.currentTarget; if (!form.reportValidity()) return;
    if (importing) return importAssets(form);
    const formData = new FormData(form), record = current();
    let fields;
    if (view === 'settings') fields = {sceneSeconds:number(formData.get('sceneSeconds'),12),recentWindow:number(formData.get('recentWindow'),6),noveltyBoost:number(formData.get('noveltyBoost'),.6),homeGallerySize:number(formData.get('homeGallerySize'),12),defaultIp:formData.get('defaultIp'),defaultAsset:formData.get('defaultAsset'),avatarAsset:formData.get('avatarAsset')};
    else if (view === 'ips') fields = {name:String(formData.get('name')).trim(),notes:formData.get('notes') || '',caption:formData.get('caption') || '',favorite:formData.get('favorite') || '',series:list(formData.get('series')),tags:list(formData.get('tags')),enabled:formData.has('enabled'),weight:number(formData.get('weight')),rotation:{enabled:formData.has('rotationEnabled'),weight:number(formData.get('rotationWeight'))}};
    else if (view === 'characters') fields = {name:String(formData.get('name')).trim(),notes:formData.get('notes') || '',ipId:formData.get('ipId'),aliases:list(formData.get('aliases')),tags:list(formData.get('tags')),enabled:formData.has('enabled'),weight:number(formData.get('weight'))};
    else fields = readAsset(form);
    setBusy(true);
    try {
      notice('正在校验配置，统一更新名称、文件与关联…');
      const payload = {group:group(),record:fields,revision:state.revision}; if (record && !editingNew) payload.id = record.id;
      const result = await api('/api/record',payload);
      acceptState(result); if (!result.library && !result.state) await refresh(false);
      selectedUid = result.savedUid || record?.uid || selectedUid; editingNew = false;
      markDirty(false); renderSummary(); renderFilters(); renderList(); renderEditor();
      savedNotice(result,'已保存到项目。关联、名称和文件位置已统一处理；生成预览后即可看到变化。'); toast('已保存到项目。');
    } catch (error) { failed(error); } finally { setBusy(false); }
  }
  function clearFiles() { chosenFiles.forEach(x => URL.revokeObjectURL(x.preview)); chosenFiles = []; }
  function newRecord() {
    if (busy || !canLeave()) return;
    clearFiles(); selectedUid = ''; editingNew = group() !== 'assets'; importing = group() === 'assets'; importMode = 'files';
    markDirty(false); renderList(); renderEditor();
  }
  function renderImport() {
    $('#editor-kicker').textContent = 'BRING YOUR FAVORITES HOME';
    $('#editor-title').textContent = view === 'works' ? '导入我的作品' : '导入新素材';
    const record = {ipId:view === 'works' ? artworkIp() : $('#ip-filter').value || '',characterIds:[],kind:'illustration',uses:view === 'works' ? ['gallery'] : ['hero','gallery'],enabled:true,weight:1,ownerWork:view === 'works',source:{},appearance:{fit:'auto'}};
    $('#record-form').innerHTML = `<p class="import-intro">原图会复制进项目。你只需选择图片、填写归属与用途，不需要处理路径、文件名或唯一编号。</p><div class="import-tabs" role="tablist" aria-label="图片导入方式"><button type="button" role="tab" data-import-mode="files" aria-selected="${importMode === 'files'}">从电脑选择</button><button type="button" role="tab" data-import-mode="urls" aria-selected="${importMode === 'urls'}">下载图片网址</button></div><div class="import-source" data-source-panel="files"${importMode !== 'files' ? ' hidden' : ''}><label class="upload-drop"><span>＋</span><b>打开资源管理器，选择图片</b><small>支持多选或拖入 · JPG / PNG / WebP / AVIF / GIF</small><input id="import-files" type="file" multiple accept="image/png,image/jpeg,image/webp,image/avif,image/gif" aria-label="从电脑选择要导入的图片"></label><div id="upload-files" class="upload-files"></div></div><div class="import-source" data-source-panel="urls"${importMode !== 'urls' ? ' hidden' : ''}>${field('可公开下载的图片直链',area('downloadUrls','','rows="4" placeholder="https://example.com/image.jpg&#10;每行一个图片网址"'),'这里只接受直接返回图片的网址；作品介绍页请填写在下方“来源作品页”。遇到登录、403 或防盗链时，请改用本地原图。',true)}</div><p class="inline-note"><strong>不需要为图片命名：</strong>工作台会生成唯一名称并检查重复。以后更名、换角色或换图，也会同步修复所有引用。</p>${baseAssetFields(record,true)}<div class="form-actions"><button type="submit" class="primary">导入并保存</button><p>先检查图片和整批配置，再写入项目。图片原尺寸保留，展示版本由构建自动生成。</p></div>`;
    bindForm(); renderChosenFiles();
    $$('[data-import-mode]').forEach(button => button.addEventListener('click',() => {
      if (busy) return; importMode = button.dataset.importMode;
      $$('[data-import-mode]').forEach(x => x.setAttribute('aria-selected',String(x.dataset.importMode === importMode)));
      $$('[data-source-panel]').forEach(x => { x.hidden = x.dataset.sourcePanel !== importMode; });
    }));
    $('#import-files').addEventListener('change',event => { addFiles(event.target.files); event.target.value = ''; });
    const drop = $('.upload-drop');
    ['dragenter','dragover'].forEach(type => drop.addEventListener(type,event => { event.preventDefault(); if (!busy) drop.classList.add('dragging'); }));
    ['dragleave','drop'].forEach(type => drop.addEventListener(type,event => { event.preventDefault(); drop.classList.remove('dragging'); }));
    drop.addEventListener('drop',event => { if (!busy) addFiles(event.dataTransfer.files); });
    markDirty(chosenFiles.length > 0);
  }
  function addFiles(files) {
    const rejected = [];
    for (const file of files) {
      if (chosenFiles.length >= 100) { rejected.push('一次最多导入 100 张，请分批选择'); break; }
      if (!/\.(png|jpe?g|webp|avif|gif)$/i.test(file.name)) { rejected.push(`${file.name}：暂不支持的格式`); continue; }
      if (file.size > 80*1024*1024) { rejected.push(`${file.name}：超过单张 80 MB`); continue; }
      if (chosenFiles.some(x => x.file.name === file.name && x.file.size === file.size && x.file.lastModified === file.lastModified)) continue;
      chosenFiles.push({file,preview:URL.createObjectURL(file)});
    }
    renderChosenFiles(); if (chosenFiles.length) markDirty(true);
    if (rejected.length) notice(rejected.join('；'),'notice');
  }
  function renderChosenFiles() {
    const holder = $('#upload-files'); if (!holder) return;
    holder.innerHTML = chosenFiles.map((item,index) => `<div class="upload-file"><img src="${esc(item.preview)}" alt="${esc(item.file.name)}"><span title="${esc(item.file.name)}">${esc(item.file.name)}</span><span>${(item.file.size/1024/1024).toFixed(2)} MB</span><button type="button" data-remove="${index}" aria-label="移除 ${esc(item.file.name)}">×</button></div>`).join('');
    holder.querySelectorAll('[data-remove]').forEach(button => button.addEventListener('click',() => { if (busy) return; const [removed] = chosenFiles.splice(Number(button.dataset.remove),1); URL.revokeObjectURL(removed.preview); renderChosenFiles(); markDirty(true); }));
  }
  async function uploadFile(file) {
    if (file.size > 80*1024*1024) throw new Error('这张图片超过单张 80 MB，请先选择合适的原图版本。');
    return api(`/api/upload?filename=${encodeURIComponent(file.name)}`,undefined,file);
  }
  async function importAssets(form) {
    const common = readAsset(form), urls = importMode === 'urls' ? String(new FormData(form).get('downloadUrls') || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean) : [];
    if (importMode === 'files' && !chosenFiles.length || importMode === 'urls' && !urls.length) { notice('先选择至少一张图片，或填写一个可下载的图片直链。','notice'); return; }
    if (urls.length > 25) { notice('图片网址一次最多 25 个，请分批导入。','notice'); return; }
    if (urls.some(url => !safeLink(url) || !/^https?:\/\//i.test(url))) { notice('图片网址必须以 http:// 或 https:// 开头。','notice'); return; }
    setBusy(true);
    try {
      const uploadIds = [];
      if (importMode === 'files') for (let i=0;i<chosenFiles.length;i++) { notice(`正在检查并暂存第 ${i+1} / ${chosenFiles.length} 张图片：${chosenFiles[i].file.name}`); const result = await uploadFile(chosenFiles[i].file); uploadIds.push(result.uploadId); }
      notice(importMode === 'urls' ? '正在下载图片并检查这批配置，原图较大时请稍候…' : '图片已检查，正在生成名称并保存到项目…');
      const result = await api('/api/import',{revision:state.revision,common,uploadIds,urls});
      acceptState(result); if (!result.library && !result.state) await refresh(false);
      clearFiles(); importing = false; editingNew = false; selectedUid = result.savedUid || result.importedUids?.[0] || '';
      markDirty(false); renderSummary(); renderFilters(); renderList(); renderEditor();
      savedNotice(result,'图片已导入并保存到项目。点击“生成预览”，让新的画面登场。'); toast(result.message || '导入完成，原图已经在项目里。');
    } catch (error) { failed(error); } finally { setBusy(false); }
  }
  function renderProbabilities(record) {
    const target = $('#probabilities'); if (!window.SoraRotation) return;
    try {
      const metrics = state.meta?.assetMetrics || {};
      const assets = lib().assets.map(asset => ({...asset,...(metrics[asset.id] || {})}));
      const hasMetrics = assets.every(asset => !asset.uses?.includes('hero') || Array.isArray(asset.dimensions));
      const engine = window.SoraRotation.createLibrary({...lib(),assets},lib().settings);
      const distribution = engine.distribution({use:'hero',viewport:'desktop'});
      let entries;
      if (view === 'ips') entries = lib().ips.map(ip => ({name:ip.name,value:distribution.filter(x => x.ipId === ip.id).reduce((s,x) => s+x.probability,0)})).filter(x => x.value>0);
      else {
        const ip = record.ipId;
        const local = engine.distribution({ipId:ip,use:'hero',viewport:'desktop'});
        entries = local.map(x => ({name:byId('assets',x.id)?.label || '图片',value:x.probability}));
      }
      entries.sort((a,b) => b.value-a.value);
      target.innerHTML = `<h3>${view === 'ips' ? '各世界的初始出场机会' : '这个 IP 内的图片出场机会'}</h3><p>使用主页同一套选择逻辑。播放时会避开刚看过的内容，并照顾较少出现的角色；下面显示尚无播放历史时的分布。${hasMetrics ? '' : '部分图片还未生成分析，当前为未计清晰度和画幅修正的估算。'}</p>${entries.length ? `<div class="probability-list">${entries.map(x => `<div class="probability-row"><span>${esc(x.name)}</span><span class="probability-track"><i style="width:${Math.min(100,x.value*100)}%"></i></span><b>${(x.value*100).toFixed(1)}%</b></div>`).join('')}</div>` : '<p>目前没有符合轮播条件的图片。请检查 IP 轮播、角色启用状态和素材的“首页轮播”用途。</p>'}`;
    } catch (_) { target.innerHTML = '<p>保存后即可重新计算轮播分布。</p>'; }
  }
  function renderSettings() {
    const settings = lib().settings, heroAssets = lib().assets.filter(x => x.uses?.includes('hero')), avatars = lib().assets.filter(x => x.uses?.includes('avatar'));
    $('#editor-kicker').textContent = 'LET THE WORLDS TAKE TURNS'; $('#editor-title').textContent = '让每个喜欢，都有机会登场。';
    $('#record-form').innerHTML = `<p class="import-intro">先挑世界，再挑角色，最后挑画面。新增的 IP、角色和素材会自动加入计算，不需要修改程序。</p><fieldset><legend>打开主页时的第一幕</legend><div class="fields">${field('起始 IP',`<select name="defaultIp">${options(lib().ips,settings.defaultIp,'自动选择')}</select>`)}${field('起始画面',`<select name="defaultAsset">${options(heroAssets.filter(x => !settings.defaultIp || x.ipId === settings.defaultIp),settings.defaultAsset,'自动选择')}</select>`)}${field('站长头像',`<select name="avatarAsset">${options(avatars,settings.avatarAsset,'使用文字头像')}</select>`,'先在素材用途勾选“头像候选”，这里就会出现；适合小尺寸的画面效果更好。',true)}</div></fieldset><fieldset><legend>轮播节奏与公平感</legend><div class="fields">${field('每幕停留时间（秒）',input('sceneSeconds',settings.sceneSeconds ?? 12,'number','min="4" max="120" step="1" required'),'首页仍可随时暂停或继续。')}${field('近期记忆长度',input('recentWindow',settings.recentWindow ?? 6,'number','min="0" max="128" step="1" required'),'避免反复展示刚看过的图片。')}${field('较少露面的内容加成',input('noveltyBoost',settings.noveltyBoost ?? .6,'number','min="0" max="5" step="0.1" required'),'默认 0.6。越大越照顾最近较少出现的内容。')}${field('首页画廊图片数量',input('homeGallerySize',settings.homeGallerySize ?? 12,'number','min="1" max="48" step="1" required'),'已导入本人作品时，个人画廊优先展示本人作品。')}</div></fieldset><p class="inline-note">每张图的颜色、文字对比度、粒子效果、过渡时长与运动强度都会在生成预览时分析。单图有特别想法，可以在素材编辑里的“画面适配”中微调。</p><div class="form-actions"><button type="submit" class="primary">保存轮播设置</button><p>保存后生成预览，再在主页实际播放看看。</p></div>`;
    bindForm();
    $('#record-form').elements.namedItem('defaultIp').addEventListener('change',event => { $('#record-form').elements.namedItem('defaultAsset').innerHTML = options(heroAssets.filter(x => !event.target.value || x.ipId === event.target.value),'','自动选择'); });
    markDirty(false);
  }
  function renderDelivery() {
    $('#editor-kicker').textContent = 'FROM YOUR DESK TO THE WORLD'; $('#editor-title').textContent = '整理在本地，展示在 GitHub。';
    const git = state?.git || {}, meta = state?.meta || {};
    const actionsUrl = safeLink(git.actionsUrl || 'https://github.com/kaminosora/kaminosora.github.io/actions');
    const siteUrl = safeLink(git.siteUrl || 'https://kaminosora.github.io/');
    const commands = meta.commands || meta.guidance || [];
    const commandText = typeof commands === 'string' ? commands : Array.isArray(commands) ? commands.map(x => typeof x === 'string' ? x : `${x.label || x.title || ''}\n${x.command || x.text || ''}`).join('\n\n') : Object.entries(commands).map(([key,value]) => `${key}\n${typeof value === 'string' ? value : JSON.stringify(value)}`).join('\n\n');
    $('#record-form').innerHTML = `<p class="import-intro">这个工作台只在你的电脑上运行。生成后的主页是静态网页，GitHub Pages 可以直接展示；访客不会接触本地编辑接口。</p><div class="git-status">${git.available ? `已连接 Git 仓库${git.branch ? ` · 分支 ${esc(git.branch)}` : ''}。${git.dirty ? '本地有待保存到 Git 的变更。' : 'Git 工作区当前没有待提交变更。'}` : '当前目录尚未连接 Git 仓库；仍然可以整理素材、生成预览和导出静态网页。'}</div><div class="delivery-steps"><div><span class="step">1</span><b>选择与保存</b><p>原图复制进项目，IP、角色、用途和标签在表单里维护。</p></div><div><span class="step">2</span><b>生成与预览</b><p>工作台校验引用、提取图片主题，生成可独立运行的静态网页。</p></div><div><span class="step">3</span><b>发布与留档</b><p>提交并推送当前版本，GitHub 构建后更新网站。每次发布保留历史。</p></div></div><div class="delivery-cards"><div class="delivery-card"><h3>先接上远端的新内容</h3><p>换电脑或别人更新过仓库时，可以先同步。只在本地源码没有未提交修改时快进更新，不会覆盖本地内容。</p><button type="button" id="sync-site">同步远端版本</button><p>有未提交修改或历史分叉时会停止；需要先保存本地版本，或通过 Git 工具解决冲突。</p></div><div class="delivery-card"><h3>更新正式主页</h3><p>发布会提交项目中的内容变更并推送 GitHub，再由 GitHub Actions 构建。实际网页更新需要等待云端任务完成。</p><button type="button" id="delivery-publish" class="primary">发布到 GitHub</button><p><a href="${esc(actionsUrl)}" target="_blank" rel="noopener">查看构建与版本记录 ↗</a> · <a href="${esc(siteUrl)}" target="_blank" rel="noopener">打开正式主页 ↗</a></p></div><div class="delivery-card"><h3>只生成静态网页</h3><p>打包适合 GitHub Pages 的展示文件，用于本地验收或自行部署。素材工作台和本地暂存文件不进入公开网页包。</p><button type="button" id="package-site">生成静态发布包</button><p>这一步不会推送到 GitHub。</p></div></div><details class="delivery-detail"><summary>仓库状态与操作提示</summary><p>日常从项目的“打开素材工作台”入口启动即可。首次克隆或换电脑时，按启动器提示准备环境。</p>${git.remote ? `<p>远端：${esc(git.remote)}</p>` : ''}${git.status ? `<pre>${esc(typeof git.status === 'string' ? git.status : JSON.stringify(git.status,null,2))}</pre>` : ''}${commandText ? `<p>当前项目对应的操作提示：</p><pre>${esc(commandText)}</pre>` : '<p>环境缺失、网络失败或推送冲突时，任务日志会给出下一步提示。失败不会被显示成已发布。</p>'}</details>`;
    $('#delivery-publish').addEventListener('click',() => startJob('publish'));
    $('#package-site').addEventListener('click',() => startJob('package'));
    $('#sync-site').addEventListener('click',() => startJob('sync'));
  }
  function jobReports(job) {
    const lines = (Array.isArray(job.log) ? job.log.join('\n') : job.log || '').split(/\r?\n/);
    return lines.map(line => { try { const report = JSON.parse(line); return report && typeof report === 'object' && !Array.isArray(report) ? report : null; } catch (_) { return null; } }).filter(Boolean);
  }
  function friendlyJobSummary(type, job) {
    const reports = jobReports(job), build = [...reports].reverse().find(x => Number.isFinite(x.assets) && Number.isFinite(x.themes));
    const summary = build ? `已整理 ${build.assets} 张素材、${build.ips} 个 IP 和 ${build.characters} 位角色；${build.themes} 个主题、${build.heroAssets} 张画面可参与首页轮播。` : '';
    if (job.status === 'success') {
      const last = reports[reports.length-1] || {};
      if (type === 'build') return summary || '图片检查、自动配色与动效分析已完成，主页预览已经更新。';
      if (type === 'package') return `${summary}${Number.isFinite(last.files) ? ` 已打包 ${last.files} 个网页资源${Number.isFinite(last.bytes) ? `，共 ${(last.bytes/1024/1024).toFixed(1)} MB` : ''}。` : '静态网页打包完成。'}`;
      if (type === 'publish') return '源码版本已保存并推送，GitHub 正在接手构建。';
      if (type === 'sync') return last.message || '已同步远端版本，并重新读取本地素材库。';
    }
    if (job.status === 'error') return '任务未完成。请按提示处理后重试，详细过程可展开查看。';
    const stages = {build:'正在检查图片并分析配色与动效，原图较大时请稍候。',package:'正在生成适合 GitHub Pages 的静态网页。',publish:'正在检查远端、保存版本并推送到 GitHub。',sync:'正在检查并获取远端版本，本地改动会受到保护。'};
    return summary || stages[type] || '正在处理，请稍候。';
  }
  async function startJob(type) {
    if (busy || !state) return;
    if (dirty) { notice('先保存正在编辑的内容，再生成或发布。这样本次修改才能一起生效。','notice'); toast('还有内容未保存。'); return; }
    const names = {build:'生成主页预览',package:'生成静态发布包',publish:'提交并推送到 GitHub',sync:'同步远端版本'};
    setBusy(true); $('#job-panel').hidden = false; $('#job-title').textContent = names[type]; $('#job-state').textContent = '进行中'; $('#job-state').className = 'pill'; $('#job-log').textContent = '任务准备中…'; $('#job-summary').textContent = friendlyJobSummary(type,{status:'running'}); $('#job-details').open = false; $('#job-result').textContent = ''; $('#close-job').hidden = true;
    try {
      const started = await api(`/api/${type}`,type === 'publish' ? {message:'从素材工作台更新主页内容'} : {});
      if (!started.jobId) throw new Error('工作台未返回任务编号，请查看服务日志后重试。');
      notice(`${names[type]}中，进度会显示在下方。`);
      let job;
      do {
        job = await api(`/api/job/${encodeURIComponent(started.jobId)}`);
        $('#job-log').textContent = Array.isArray(job.log) ? job.log.join('\n') : job.log || '正在处理…';
        $('#job-summary').textContent = friendlyJobSummary(type,job);
        $('#job-log').scrollTop = $('#job-log').scrollHeight;
        if (job.status === 'running' || job.status === 'pending' || job.status === 'queued') await new Promise(resolve => setTimeout(resolve,1200));
      } while (job.status === 'running' || job.status === 'pending' || job.status === 'queued');
      if (job.status !== 'success') throw new Error(job.error || job.result?.error || '任务未完成，请查看上方日志。');
      $('#job-state').textContent = '已完成';
      if (type === 'publish') {
        $('#job-result').innerHTML = `已完成本地发布步骤。网页是否更新，请以 <a href="${esc(safeLink(state.git?.actionsUrl || 'https://github.com/kaminosora/kaminosora.github.io/actions'))}" target="_blank" rel="noopener">GitHub 构建结果 ↗</a> 为准。<a href="${esc(safeLink(state.git?.siteUrl || 'https://kaminosora.github.io/'))}" target="_blank" rel="noopener">打开正式主页 ↗</a>`;
        notice('发布任务已完成；GitHub 构建与网页更新仍需以云端结果为准。');
      } else if (type === 'build') { $('#job-result').innerHTML = '预览已更新。<a href="index.html" target="_blank" rel="noopener">打开主页预览 ↗</a>'; notice('预览已生成，颜色与动效分析已更新。'); }
      else if (type === 'sync') { $('#job-result').textContent = '远端同步完成，素材库已重新读取。生成预览即可查看同步后的内容。'; notice('已同步远端版本。'); }
      else { $('#job-result').textContent = typeof job.result === 'string' ? job.result : job.result?.message || '静态发布包已生成，输出位置见任务日志。'; notice('静态发布包已生成。'); }
      const fresh = await api('/api/state'); if (acceptState(fresh)) { renderSummary(); renderFilters(); renderList(); renderEditor(); renderConnection(); }
      toast(`${names[type]}完成。`);
    } catch (error) { $('#job-state').textContent = '未完成'; $('#job-state').className = 'pill error'; $('#job-summary').textContent = '这次操作没有完成。请根据提示处理后重试，详细过程可展开查看。'; failed(error); }
    finally { $('#close-job').hidden = false; setBusy(false); }
  }
  $$('.sidebar [data-view]').forEach(button => button.addEventListener('click',() => {
    if (busy || button.dataset.view === view || !canLeave()) return;
    clearFiles(); view = button.dataset.view; selectedUid = ''; editingNew = false; importing = false; $('#search').value = ''; $('#ip-filter').value = ''; $('#state-filter').value = '';
    $$('.sidebar [data-view]').forEach(x => x.dataset.view === view ? x.setAttribute('aria-current','page') : x.removeAttribute('aria-current'));
    markDirty(false); renderFilters(); renderList(); renderEditor();
  }));
  $('#new-record').addEventListener('click',newRecord);
  $('#search').addEventListener('input',renderList); $('#ip-filter').addEventListener('change',renderList); $('#state-filter').addEventListener('change',renderList);
  $('#refresh-state').addEventListener('click',async () => { if (busy || !canLeave()) return; setBusy(true); try { markDirty(false); await refresh(); } catch (error) { failed(error); } finally { setBusy(false); } });
  $('#build-preview').addEventListener('click',() => startJob('build'));
  $('#publish-site').addEventListener('click',() => startJob('publish'));
  $('#close-job').addEventListener('click',() => { $('#job-panel').hidden = true; });
  window.addEventListener('beforeunload',event => { if (dirty || busy) { event.preventDefault(); event.returnValue = ''; } });
  setBusy(true);
  if (!token) {
    notice('请从项目里的“打开素材工作台”入口启动。它会自动打开带本次会话凭据的本地页面；直接打开这个 HTML 文件无法保存。','notice');
    $('#editor-title').textContent = '从启动入口进入工作台';
    $('#record-form').innerHTML = '<div class="empty-state"><span class="empty-symbol">✧</span><h3>双击启动，之后都在这里操作。</h3><p>工作台需要本地服务来复制图片、生成预览和发布。请关闭这个页面，从项目里的启动入口重新进入。</p></div>';
  } else refresh().catch(failed).finally(() => setBusy(false));
})();
