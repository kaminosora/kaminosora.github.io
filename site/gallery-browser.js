/* Shared personal-work and fandom-material browser. No network or persistence. */
(function (host, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (host) host.SoraGallery = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const list = value => Array.isArray(value) ? value : [];
  const text = value => typeof value === 'string' ? value : '';
  const unique = values => [...new Set(values.filter(Boolean))];
  const kinds = {'character-art':'角色立绘','character-sheet':'角色设定图','key-visual':'作品主视觉','illustration':'插画','wallpaper':'壁纸','screenshot':'截图','photo':'照片','logo':'标志','other':'其他'};
  const UNTAGGED = '__untagged__';
  const uses = {hero:'首页轮播',gallery:'画廊展示',avatar:'头像候选',card:'卡片配图',reference:'设定资料'};
  const emptyState = () => ({ipId:'',characterId:'',kind:'',use:'',tag:'',year:'',series:'',search:'',itemId:'',imagePage:0});
  function sourceUrl(asset) { return typeof asset.source === 'string' ? asset.source : asset.source?.page || ''; }
  function workKey(asset) {
    if (asset.workId) return String(asset.workId);
    const match = sourceUrl(asset).match(/^https?:\/\/(?:www\.)?pixiv\.net\/(?:[a-z]{2}\/)?artworks\/(\d+)(?:[/?#]|$)/i);
    return match ? 'pixiv-' + match[1] : asset.id;
  }
  function pageNumber(asset) {
    const value = asset.pageIndex ?? asset.pixiv?.pageIndex ?? 0;
    return Number.isInteger(value) && value >= 0 ? value : 0;
  }
  function assetYear(asset) {
    const value = asset.publishedAt || asset.createdAt || asset.createDate || asset.date || asset.pixiv?.createDate || '';
    return /^\d{4}/.test(value) ? value.slice(0,4) : '';
  }
  function groupWorks(assets) {
    const groups = new Map();
    for (const asset of list(assets)) {
      if (!asset?.id || !asset.ownerWork || asset.enabled === false || !list(asset.uses).includes('gallery')) continue;
      const id = workKey(asset);
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(asset);
    }
    return [...groups.entries()].map(([id, pages]) => {
      pages = [...pages].sort((a,b) => pageNumber(a) - pageNumber(b) || String(a.id).localeCompare(String(b.id)));
      const cover = pages[0];
      return {id, cover, pages, title:cover.workTitle || cover.label || '未命名作品',
        description:pages.find(a=>a.description)?.description || '',
        tags:unique(pages.flatMap(a=>list(a.tags))), year:assetYear(cover), ownerWork:true,
        metadataPending:pages.some(a=>a.metadataStatus==='pending')};
    });
  }
  function materialItems(assets) {
    return list(assets).filter(a=>a?.id && !a.ownerWork && a.enabled !== false)
      .map(a=>({id:a.id,cover:a,pages:[a],title:a.label||'未命名素材',description:a.description||'',tags:list(a.tags),year:assetYear(a),ownerWork:false}));
  }
  function filterItems(items, filter={}, library={}) {
    const characters = new Map(list(library.characters).map(c=>[c.id,c]));
    const ips = new Map(list(library.ips).map(ip=>[ip.id,ip]));
    const needle = text(filter.search).trim().toLocaleLowerCase();
    const series = text(filter.series).toLocaleLowerCase();
    return list(items).filter(item => {
      if (filter.year && item.year !== filter.year) return false;
      if (filter.tag === UNTAGGED ? item.tags.length > 0 : filter.tag && !item.tags.includes(filter.tag)) return false;
      return item.pages.some(asset => {
        if (filter.ipId && asset.ipId !== filter.ipId) return false;
        if (filter.characterId && !list(asset.characterIds).includes(filter.characterId)) return false;
        if (filter.kind && asset.kind !== filter.kind) return false;
        if (filter.use && !list(asset.uses).includes(filter.use)) return false;
        const people = list(asset.characterIds).flatMap(id=>{const c=characters.get(id);return c?[c.name,...list(c.aliases)]:[];});
        const searchable = [item.title,item.description,...item.tags,asset.label,...people,ips.get(asset.ipId)?.name].filter(Boolean).join(' ').toLocaleLowerCase();
        if (series && !searchable.includes(series)) return false;
        return !needle || searchable.includes(needle);
      });
    });
  }
  function materialTree(library, assets) {
    const materials=materialItems(assets);
    return list(library.ips).filter(ip=>ip.systemRole!=='owner-works').map(ip=>({
      ...ip,count:materials.filter(item=>item.cover.ipId===ip.id).length,
      children:list(library.characters).filter(c=>c.ipId===ip.id).map(c=>({...c,count:materials.filter(item=>item.cover.ipId===ip.id&&list(item.cover.characterIds).includes(c.id)).length}))
    }));
  }
  function createBrowser({root,mode,assets,library,onChange}) {
    const owned=mode==='works';
    const items=owned?groupWorks(assets):materialItems(assets);
    const tree=materialTree(library,assets);
    const byId=new Map(items.map(item=>[item.id,item]));
    const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const safeLink=value=>/^https?:\/\//i.test(value||'')||/^media\//.test(value||'')?value:'';
    let state=emptyState();
    const q=selector=>root.querySelector(selector);
    const tagOptions=unique(items.flatMap(i=>i.tags)).sort((a,b)=>a.localeCompare(b,'zh-CN'));
    const years=unique(items.map(i=>i.year)).sort().reverse();
    root.classList.add('image-browser');
    root.innerHTML=`<details class="browser-sidebar" open><summary>${owned?'作品导航':'偏爱档案导航'}</summary><nav class="browser-sidebar-content" aria-label="${owned?'个人作品分类':'IP 与角色分类'}"></nav></details><div class="browser-main"><div class="browser-toolbar"><label class="browser-filter-field browser-search-field"><span>搜索</span><input class="browser-search" type="search" placeholder="${owned?'搜索作品、简介或标签…':'搜索角色、名称或标签…'}" aria-label="${owned?'搜索个人作品':'搜索偏爱素材'}"></label>${owned?'':`<label class="browser-filter-field"><span>图片类型</span><select data-filter="kind" aria-label="筛选素材类型"><option value="">全部类型</option>${Object.entries(kinds).map(([key,value])=>`<option value="${key}">${value}</option>`).join('')}</select></label><label class="browser-filter-field"><span>用途</span><select data-filter="use" aria-label="筛选素材用途"><option value="">全部用途</option>${Object.entries(uses).map(([key,value])=>`<option value="${key}">${value}</option>`).join('')}</select></label>`}<label class="browser-filter-field"><span>标签</span><select data-filter="tag" aria-label="${owned?'筛选作品标签':'筛选素材标签'}"><option value="">全部标签</option>${items.some(item=>!item.tags.length)?`<option value="${UNTAGGED}">未分类</option>`:''}${tagOptions.map(tag=>`<option value="${escape(tag)}">${escape(tag)}</option>`).join('')}</select></label>${owned&&years.length?`<label class="browser-filter-field"><span>年份</span><select data-filter="year" aria-label="筛选作品年份"><option value="">全部年份</option>${years.map(year=>`<option>${year}</option>`).join('')}</select></label>`:''}<button class="quiet-button browser-reset" type="button">清除筛选</button></div><div class="browser-heading"><h2></h2><p class="browser-count" aria-live="polite"></p></div><section class="browser-viewer" hidden aria-label="${owned?'个人作品大图':'偏爱素材大图'}" tabindex="-1"></section><div class="browser-masonry" aria-label="${owned?'个人作品瀑布流':'偏爱素材瀑布流'}"></div><div class="browser-empty" hidden></div></div>`;
    q('.browser-sidebar').open=!matchMedia('(max-width: 800px)').matches;
    const navButton=(label,count,attributes,active=false)=>`<button type="button" class="browser-nav-button" ${attributes} aria-pressed="${active}"><span>${escape(label)}</span>${Number.isFinite(count)?`<small>${count}</small>`:''}</button>`;
    function emit(history) { if(onChange)onChange({...state},{history}); }
    function update(next,{scroll=false,notify=true,history='push'}={}) {
      state={...state,...next};
      if(!Number.isInteger(state.imagePage)||state.imagePage<0)state.imagePage=0;
      render();
      if(notify)emit(history);
      if(scroll&&!q('.browser-viewer').hidden)q('.browser-viewer').scrollIntoView({behavior:root.closest('[data-reduced="true"]')?'instant':'smooth',block:'start'});
    }
    function sidebar() {
      const nav=q('.browser-sidebar-content'), oldScroll=nav.scrollTop;
      if(owned){
        nav.innerHTML=`<p class="browser-nav-label">我的画廊</p>${navButton('全部作品',items.length,'data-browser-all',!state.year&&!state.tag&&!state.itemId)}${years.length?`<p class="browser-nav-label">按年份</p>${years.map(year=>navButton(year,items.filter(i=>i.year===year).length,`data-browser-year="${year}"`,state.year===year)).join('')}`:''}<p class="browser-nav-label">作品目录</p>${items.map(item=>navButton(item.title,item.pages.length,`data-browser-item="${escape(item.id)}"`,state.itemId===item.id)).join('')}<p class="browser-nav-label">收藏的另一些世界</p><button type="button" class="browser-crosslink" data-browser-materials>浏览偏爱素材 →</button>`;
      }else{
        nav.innerHTML=`<p class="browser-nav-label">偏爱档案</p>${navButton('全部素材',items.length,'data-browser-all',!state.ipId)}${tree.map(ip=>`<div class="browser-ip-group ${state.ipId===ip.id?'is-current':''}">${navButton(ip.name,ip.count,`data-browser-ip="${escape(ip.id)}"`,state.ipId===ip.id&&!state.characterId&&!state.series)}${state.ipId===ip.id?`<div class="browser-children">${ip.children.map(c=>navButton(c.name,c.count,`data-browser-character="${escape(c.id)}" data-parent-ip="${escape(ip.id)}"`,state.characterId===c.id)).join('')}${list(ip.series).map(series=>navButton(series,null,`data-browser-series="${escape(series)}" data-parent-ip="${escape(ip.id)}"`,state.series===series)).join('')}</div>`:''}</div>`).join('')}<p class="browser-nav-label">站长的作品</p><button type="button" class="browser-crosslink" data-browser-works>走进个人画廊 →</button>`;
      }
      nav.scrollTop=oldScroll;
      nav.querySelectorAll('[data-browser-all]').forEach(b=>b.addEventListener('click',()=>update({...emptyState()})));
      nav.querySelectorAll('[data-browser-ip]').forEach(b=>b.addEventListener('click',()=>update({ipId:b.dataset.browserIp,characterId:'',series:'',search:'',itemId:'',imagePage:0})));
      nav.querySelectorAll('[data-browser-character]').forEach(b=>b.addEventListener('click',()=>update({ipId:b.dataset.parentIp,characterId:b.dataset.browserCharacter,series:'',search:'',itemId:'',imagePage:0})));
      nav.querySelectorAll('[data-browser-series]').forEach(b=>b.addEventListener('click',()=>update({ipId:b.dataset.parentIp,characterId:'',series:b.dataset.browserSeries,search:'',itemId:'',imagePage:0})));
      nav.querySelectorAll('[data-browser-year]').forEach(b=>b.addEventListener('click',()=>update({year:b.dataset.browserYear,itemId:'',imagePage:0})));
      nav.querySelectorAll('[data-browser-item]').forEach(b=>b.addEventListener('click',()=>update({itemId:b.dataset.browserItem,imagePage:0},{scroll:true})));
      nav.querySelectorAll('[data-browser-materials],[data-browser-works]').forEach(b=>b.addEventListener('click',()=>root.dispatchEvent(new CustomEvent('sora:browse',{bubbles:true,detail:{page:b.hasAttribute('data-browser-materials')?'materials':'gallery'}}))));
    }
    function viewer() {
      const holder=q('.browser-viewer'), item=byId.get(state.itemId);
      if(!item){holder.hidden=true;holder.innerHTML='';return;}
      state.imagePage=Math.min(state.imagePage,item.pages.length-1);
      const art=item.pages[state.imagePage];
      const original=safeLink(art.original||art.source_image||sourceUrl(art));
      const source=safeLink(sourceUrl(art));
      const people=list(art.characterIds).map(id=>list(library.characters).find(c=>c.id===id)?.name).filter(Boolean);
      const ip=list(library.ips).find(x=>x.id===art.ipId);
      holder.hidden=false;
      holder.innerHTML=`<div class="browser-viewer-top"><div><span class="small-caps">${owned?'MY ARTWORK':'FANDOM MATERIAL'}</span><h3>${escape(item.title)}</h3></div><button type="button" class="quiet-button browser-viewer-close">收起大图 ×</button></div><div class="browser-viewer-image"><img src="${escape(art.background||art.image)}" alt="${escape(item.title)}${item.pages.length>1?' · 第 '+(state.imagePage+1)+' 张':''}"></div>${item.pages.length>1?`<div class="browser-viewer-pages"><div class="browser-page-controls"><button type="button" class="round-button" data-image-step="-1" aria-label="上一张作品图片" ${state.imagePage===0?'disabled':''}>‹</button><span>${state.imagePage+1} / ${item.pages.length}</span><button type="button" class="round-button" data-image-step="1" aria-label="下一张作品图片" ${state.imagePage===item.pages.length-1?'disabled':''}>›</button></div><div class="browser-page-thumbnails">${item.pages.map((page,index)=>`<button type="button" data-image-page="${index}" aria-label="查看第 ${index+1} 张作品图片" aria-pressed="${index===state.imagePage}"><img src="${escape(page.image)}" alt=""><span>${index+1}</span></button>`).join('')}</div></div>`:''}<div class="browser-viewer-info"><p class="browser-image-meta">${escape(owned?'神の空 · 本人作品':[ip?.name,...people].filter(Boolean).join(' / '))}${list(art.dimensions).length?' · '+art.dimensions.join(' × '):''}${!owned?' · '+escape(kinds[art.kind]||art.kind):''}</p>${item.metadataPending?'<p class="art-description browser-metadata-pending">原标题、简介与标签待补；目前以 Pixiv 作品编号整理。</p>':''}${item.description?`<p class="art-description">${escape(item.description)}</p>`:''}${item.tags.length?`<div class="art-tags">${item.tags.map(tag=>`<button type="button" data-viewer-tag="${escape(tag)}">#${escape(tag)}</button>`).join('')}</div>`:''}<div class="browser-source-links">${original?`<a href="${escape(original)}" target="_blank" rel="noopener noreferrer">打开原始图片 ↗</a>`:''}${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${owned?'查看原作品页':'查看素材来源'} ↗</a>`:''}</div></div>`;
      holder.querySelector('.browser-viewer-close').addEventListener('click',()=>update({itemId:'',imagePage:0}));
      holder.querySelectorAll('[data-image-step]').forEach(b=>b.addEventListener('click',()=>update({imagePage:state.imagePage+Number(b.dataset.imageStep)})));
      holder.querySelectorAll('[data-image-page]').forEach(b=>b.addEventListener('click',()=>update({imagePage:Number(b.dataset.imagePage)})));
      holder.querySelectorAll('[data-viewer-tag]').forEach(b=>b.addEventListener('click',()=>update({tag:b.dataset.viewerTag,itemId:'',imagePage:0})));
    }
    function render() {
      sidebar();
      q('.browser-search').value=state.search;
      root.querySelectorAll('[data-filter]').forEach(select=>select.value=state[select.dataset.filter]||'');
      const visible=filterItems(items,state,library);
      const ip=tree.find(x=>x.id===state.ipId), person=list(library.characters).find(c=>c.id===state.characterId);
      q('.browser-heading h2').textContent=owned?(state.year?state.year+' 年的作品':'我的作品'):[ip?.name||'所有喜欢的世界',person?.name||state.series].filter(Boolean).join(' / ');
      q('.browser-count').textContent=owned?`${visible.length} 个作品 · ${visible.reduce((sum,i)=>sum+i.pages.length,0)} 张图片`:`${visible.length} 张素材${state.kind?' · '+(kinds[state.kind]||state.kind):''}${state.use?' · '+(uses[state.use]||state.use):''}`;
      q('.browser-masonry').innerHTML=visible.map(item=>`<button type="button" class="browser-card cursor-interaction" data-open-item="${escape(item.id)}" aria-label="查看${escape(item.title)}"><div class="browser-card-image"><img src="${escape(item.cover.image)}" alt="${escape(item.title)}" loading="lazy" ${list(item.cover.dimensions).length?`width="${item.cover.dimensions[0]}" height="${item.cover.dimensions[1]}"`:''}>${item.pages.length>1?`<span class="browser-page-badge">${item.pages.length} 张</span>`:''}</div><div class="browser-card-copy"><h3>${escape(item.title)}</h3><p>${escape(owned?(item.tags.slice(0,3).map(tag=>'#'+tag).join(' · ')||'未分类'):(list(library.ips).find(ip=>ip.id===item.cover.ipId)?.name||''))}</p><small>${owned?(item.metadataPending?'原标题、简介与标签待补':item.year||'本人作品'):escape(kinds[item.cover.kind]||item.cover.kind)}</small></div></button>`).join('');
      q('.browser-empty').hidden=visible.length>0;
      q('.browser-empty').innerHTML=owned?'<span>✧</span><h3>这一页还没有作品。</h3><p>可以换个标签，或清除筛选，再看看其他画面。</p>':`<span>✧</span><h3>${ip||person||state.series?'这个位置，留给下一张喜欢。':'没有找到符合筛选的素材。'}</h3><p>${ip||person||state.series?'偏爱已经收在档案里；合适的图片还会慢慢补上。':'试着更换类型、用途或关键词。'}</p>`;
      q('.browser-masonry').querySelectorAll('[data-open-item]').forEach(b=>b.addEventListener('click',()=>update({itemId:b.dataset.openItem,imagePage:0},{scroll:true})));
      viewer();
    }
    q('.browser-search').addEventListener('input',event=>update({search:event.target.value,itemId:'',imagePage:0},{history:'replace'}));
    root.querySelectorAll('[data-filter]').forEach(select=>select.addEventListener('change',()=>update({[select.dataset.filter]:select.value,itemId:'',imagePage:0})));
    q('.browser-reset').addEventListener('click',()=>update(emptyState()));
    render();
    return {getState:()=>({...state}),setState(next={},options={}){state={...emptyState(),...next};update({},options);},open(id,page=0){update({itemId:id,imagePage:page},{scroll:true});},items};
  }
  return {groupWorks,materialItems,filterItems,materialTree,workKey,createBrowser,emptyState,kinds,uses,UNTAGGED};
});
