(() => {
  const root = document.getElementById('sora-blog');
  const { themes, projects, interests, artworks, library } = SORA_PREVIEW_DATA;
  const artById = new Map(artworks.map(a=>[a.id,a]));
  const ownedWorks=artworks.filter(a=>a.ownerWork && a.enabled!==false && a.uses.includes('gallery'));
  const personalWorks=SoraGallery.groupWorks(ownedWorks);
  const personalById=new Map(personalWorks.map(w=>[w.id,w]));
  const galleryItems=personalWorks.map(w=>w.cover);
  const picker=SoraRotation.createLibrary({...library,assets:artworks},library.settings);
  const galleryPicker=SoraRotation.createLibrary({...library,assets:galleryItems},library.settings);
  const homeGallery=[];
  for(let i=0;i<Math.min(library.settings.homeGallerySize||12,galleryItems.length);i++){
    const choices=galleryPicker.distribution({use:'gallery'}).filter(x=>!homeGallery.some(a=>a.id===x.id));
    const total=choices.reduce((sum,x)=>sum+x.probability,0);let roll=Math.random()*total;
    const choice=choices.find(x=>(roll-=x.probability)<0)||choices.at(-1);
    if(!choice)break;const art=artById.get(choice.id);homeGallery.push(art);galleryPicker.record(art);
  }
  const q = s => root.querySelector(s);
  const qa = s => [...root.querySelectorAll(s)];
  const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  if(!SORA_PREVIEW_DATA.inline && !SORA_PREVIEW_DATA.production){const link=document.createElement('a');link.href='studio.html';link.textContent='维护素材库 ↗';link.className='library-link cursor-interaction';q('.footer-links').append(link);}
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const state = { page: 'home', theme: Math.max(0,themes.findIndex(t=>t.id===library.settings.defaultIp)), project: 0, scenePlaying: !reduced.matches, deckPlaying: !reduced.matches, galleryPlaying: galleryItems.length>1&&!reduced.matches, reduced: reduced.matches, imageIndex: 0, variants: {}, locked:false };
  const initialAsset=artById.get(library.settings.defaultAsset);
  if(initialAsset && themes[state.theme].variants.includes(initialAsset.id))state.variants[themes[state.theme].id]=themes[state.theme].variants.indexOf(initialAsset.id);
  let imageRequest=0;
  const sceneDuration=(library.settings.sceneSeconds||12)*1000;
  let sceneElapsed = 0, deckElapsed = 0, galleryElapsed = 0, deckHovered = false, galleryHovered = false, layer = 0, toastTimer = 0, demoTimer = 0;
  let lastTick = performance.now();
  const safeIndex = (value, length) => Number.isInteger(value) && value >= 0 && value < length;
  const saved = window.openai?.widgetState?.modelContent;
  if (saved) {
    const ti = themes.findIndex(t => t.id === saved.theme);
    const pi = projects.findIndex(p => p.id === saved.project);
    if (ti >= 0) state.theme = ti;
    if (pi >= 0) state.project = pi;
    if (['home', 'projects', 'gallery','interests','materials'].includes(saved.page)) state.page = saved.page;
    if(themes[state.theme].variants.includes(saved.character))state.variants[themes[state.theme].id]=themes[state.theme].variants.indexOf(saved.character);
    if (typeof saved.scenePlaying === 'boolean') state.scenePlaying = saved.scenePlaying && !reduced.matches;
  }
  function persist() {
    window.openai?.setWidgetState?.({modelContent:{page:state.page, theme:themes[state.theme].id, character:themes[state.theme].variants[state.variants[themes[state.theme].id]||0], project:projects[state.project].id, scenePlaying:state.scenePlaying},privateContent:{reduced:state.reduced,locked:state.locked}})?.catch(()=>{});
  }
  function toast(message) {
    const el=q('.toast-note');el.textContent=message;el.hidden=false;
    el.style.top=Math.max(95, Math.min(root.clientHeight-70,-root.getBoundingClientRect().top+60))+'px';
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>{el.hidden=true;},2600);
  }
  function paintTheme(immediate=false) {
    const theme=themes[state.theme];const variant=state.variants[theme.id]||0;const art=artById.get(theme.variants[variant]);
    const layers=qa('.scene-layer');const ticket=++imageRequest;
    const nextLayer=immediate?layer:1-layer;const active=layers[nextLayer];
    function reveal(){
    if(ticket!==imageRequest)return;layer=nextLayer;
    const look=art.adaptive;
    Object.entries({'--accent':look.accent,'--accent-soft':look.soft,'--paper':look.paper,'--ink':look.ink,'--muted':look.muted,'--secondary':look.secondary,'--fade-duration':look.duration+'s','--drift-duration':look.driftSeconds+'s','--motion-strength':look.motion,'--image-overlay':look.overlay}).forEach(([key,value])=>root.style.setProperty(key,value));
    root.dataset.effect=look.effect;root.dataset.asset=art.id;
    const symbols={petals:['❀','✿'],snow:['❅','·'],sparks:['✧','•'],stars:['✧','✦'],motes:['·','•'],none:['','']};
    qa('.sora-petals span').forEach((s,i)=>s.textContent=(symbols[look.effect]||symbols.stars)[i%2]);
    active.classList.toggle('portrait',art.portrait);active.classList.toggle('figure',art.figure);
    active.style.objectPosition=look.focalPoint.map(x=>x+'%').join(' ');
    active.style.objectFit=look.fit==='auto'?(art.figure||art.portrait?'contain':'cover'):look.fit;
    active.classList.add('is-visible');layers[1-layer].classList.remove('is-visible');
    q('.scene-name').textContent=theme.name;q('.scene-index').textContent=String(state.theme+1).padStart(2,'0')+' / '+themes.length;
    q('.scene-subtitle').textContent=theme.caption||'让今天的心情，换一个背景。';
    q('.scene-character').textContent=art.label;
    q('.scene-original').href=art.original||art.source_image||art.source;
    q('.scene-original').textContent=`原图 ${art.dimensions.join(' × ')} ↗`;
    q('.character-switcher').innerHTML=`<label>本次出场 <select class="character-select" aria-label="选择当前 IP 的角色画面">${theme.variants.map((id,i)=>`<option value="${i}" ${variant===i?'selected':''}>${esc(artById.get(id).label)}</option>`).join('')}</select></label><button type="button" class="random-character cursor-interaction">再遇见一位 ↻</button>`;
    q('.character-select').addEventListener('change',e=>{state.variants[theme.id]=Number(e.target.value);picker.record(artById.get(theme.variants[state.variants[theme.id]]));paintTheme();persist();});
    q('.random-character').addEventListener('click',()=>{chooseScene(theme.id);persist();});
    qa('[data-theme]').forEach(b=>{const yes=Number(b.dataset.theme)===state.theme;b.classList.toggle('is-active',yes);b.setAttribute('aria-pressed',String(yes));});
    sceneElapsed=0;paintPlayback();
    }
    active.onload=reveal;active.onerror=()=>{if(ticket===imageRequest){state.scenePlaying=false;paintPlayback();toast('这张图暂时没有加载成功，可以手动换一张。');}};
    active.src=art.background||art.image;if(active.complete&&active.naturalWidth)reveal();
  }
  function paintPlayback(){
    q('.theme-toggle').textContent=state.scenePlaying?'Ⅱ 暂停轮播':'▶ 开始轮播';
    q('.theme-toggle').setAttribute('aria-pressed',String(!state.scenePlaying));
    q('.theme-state').textContent=state.scenePlaying?`世界放映中 · ${sceneDuration/1000} 秒一幕`:'停在喜欢的这一幕';
    q('.deck-toggle').textContent=state.deckPlaying?'暂停转动':'开始转动';
    q('.gallery-toggle').textContent=state.galleryPlaying?'暂停画廊':'开始画廊';
    q('.motion-button').textContent=state.reduced?'完整动效':'减弱动效';
    q('.motion-button').setAttribute('aria-pressed',String(state.reduced));
    root.dataset.reduced=String(state.reduced);
  }
  function chooseScene(ipId){const art=picker.next({ipId,use:'hero',viewport:root.clientWidth<600?'mobile':'desktop'});if(!art)return;const index=themes.findIndex(t=>t.id===art.ipId);if(index<0)return;state.theme=index;state.variants[art.ipId]=themes[index].variants.indexOf(art.id);paintTheme();}
  function changeTheme(delta,manual=true){if(manual){const next=(state.theme+delta+themes.length)%themes.length;chooseScene(themes[next].id);persist();}else chooseScene(state.locked?themes[state.theme].id:undefined);}
  q('.theme-dots').innerHTML=themes.map((t,i)=>`<button type="button" class="theme-dot cursor-interaction" data-theme="${i}" aria-label="切换到${esc(t.name)}主题" data-tooltip="${esc(t.name)}" aria-pressed="false"></button>`).join('');
  q('.ip-tags').innerHTML=themes.map((t,i)=>`<button type="button" class="cursor-interaction" data-material-ip="${esc(t.id)}">${esc(t.name)}</button>`).join('');
  qa('[data-theme]').forEach(b=>b.addEventListener('click',()=>{chooseScene(themes[Number(b.dataset.theme)].id);persist();}));
  q('.lock-theme').addEventListener('change',e=>{state.locked=e.target.checked;persist();});
  qa('[data-theme-step]').forEach(b=>b.addEventListener('click',()=>changeTheme(Number(b.dataset.themeStep))));
  q('.theme-toggle').addEventListener('click',()=>{state.scenePlaying=!state.scenePlaying;paintPlayback();persist();});
  q('.motion-button').addEventListener('click',()=>{state.reduced=!state.reduced;if(state.reduced){state.scenePlaying=false;state.deckPlaying=false;state.galleryPlaying=false;}paintPlayback();persist();});
  reduced.addEventListener('change',e=>{state.reduced=e.matches;if(e.matches){state.scenePlaying=false;state.deckPlaying=false;state.galleryPlaying=false;}paintPlayback();});

  const coverColors=['#d6e4e1','#d9d5ec','#f6dce6','#d8e2ed'];
  const coverWords=['Notes.','Panorama.','Bili.','Route.'];
  q('.deck-stage').innerHTML=projects.map((p,i)=>`<button type="button" class="deck-card cursor-interaction" data-project="${i}" aria-label="查看 ${esc(p.name)} 项目详情"><div class="deck-cover" style="--cover-color:${coverColors[i%coverColors.length]}"><span class="cover-code">${esc(p.language)} / AI VIBE</span>${p.screenshots.length?`<img src="${p.screenshots[0].image}" alt="${esc(p.name)} 真实项目截图">`:`<span class="cover-word">${esc(coverWords[i]||p.name)}</span>`}</div><div class="deck-caption"><div><b>${esc(p.name)}</b><small>${esc(p.platform)}</small></div><span>↗</span></div><div class="deck-blurb">${esc(p.tagline)}<br>点击打开详情与预览 →</div></button>`).join('');
  function paintDeck(){
    qa('.deck-card').forEach((card,i)=>{
      let offset=(i-state.project+projects.length)%projects.length;
      if(offset>projects.length/2)offset-=projects.length;
      const distance=Math.abs(offset);
      card.style.setProperty('--offset',offset);card.style.setProperty('--distance',distance);card.style.setProperty('--scale',distance===0?1:.83);card.style.setProperty('--opacity',distance===0?1:distance===1?.72:.38);card.style.setProperty('--order',5-distance);card.classList.toggle('is-active',offset===0);
    });
    q('.deck-counter').textContent=String(state.project+1).padStart(2,'0')+' / '+String(projects.length).padStart(2,'0');
    deckElapsed=0;
  }
  function moveProject(delta){state.project=(state.project+delta+projects.length)%projects.length;paintDeck();persist();}
  qa('[data-project-step]').forEach(b=>b.addEventListener('click',()=>moveProject(Number(b.dataset.projectStep))));
  q('.project-deck').addEventListener('pointerenter',()=>{deckHovered=true;});q('.project-deck').addEventListener('pointerleave',()=>{deckHovered=false;deckElapsed=0;});
  q('.project-deck').addEventListener('focusin',()=>{deckHovered=true;});q('.project-deck').addEventListener('focusout',e=>{if(!q('.project-deck').contains(e.relatedTarget)){deckHovered=false;deckElapsed=0;}});
  q('.deck-toggle').addEventListener('click',()=>{state.deckPlaying=!state.deckPlaying;paintPlayback();});
  qa('.deck-card').forEach(b=>b.addEventListener('click',()=>{state.project=Number(b.dataset.project);navigate('projects');}));

  const rail=q('.gallery-rail');
  rail.innerHTML=homeGallery.map((art,i)=>{const work=personalById.get(SoraGallery.workKey(art));return `<button type="button" class="gallery-polaroid cursor-interaction" data-home-work="${esc(work.id)}" style="--tilt:${i%2?2:-2}deg" aria-label="查看${esc(work.title)}"><img src="${art.image}" alt="${esc(work.title)}" loading="lazy"><span>${esc(work.title)}</span><small>本人作品${work.pages.length>1?' · '+work.pages.length+' 张':''}${work.metadataPending?' · 资料待补':''}</small></button>`;}).join('');
  if(!personalWorks.length)rail.innerHTML='<div class="home-gallery-empty"><p>个人作品正在整理，稍后挂上新的画面。</p><button type="button" class="text-button" data-open-materials>先翻翻偏爱素材 →</button></div>';
  q('.gallery-description p').textContent=personalWorks.length?`我的作品 · ${personalWorks.length} 个作品，${ownedWorks.length} 张原图`:'我的作品画廊 · 正在整理中';
  q('.gallery-toggle').hidden=galleryItems.length<2;
  q('.gallery-toolbar').hidden=galleryItems.length===0;
  function moveGallery(delta){
    const distance=(rail.querySelector('.gallery-polaroid')?.getBoundingClientRect().width||210)+18;
    const max=rail.scrollWidth-rail.clientWidth;
    const target=delta>0 && rail.scrollLeft>=max-3?0:delta<0 && rail.scrollLeft<=3?max:Math.max(0,Math.min(max,rail.scrollLeft+distance*delta));
    rail.scrollTo({left:target,behavior:state.reduced?'instant':'smooth'});galleryElapsed=0;
  }
  qa('[data-gallery-step]').forEach(b=>b.addEventListener('click',()=>moveGallery(Number(b.dataset.galleryStep))));
  q('.gallery-toggle').addEventListener('click',()=>{state.galleryPlaying=!state.galleryPlaying;paintPlayback();});
  rail.addEventListener('pointerenter',()=>{galleryHovered=true;});rail.addEventListener('pointerleave',()=>{galleryHovered=false;galleryElapsed=0;});
  rail.addEventListener('focusin',()=>{galleryHovered=true;});rail.addEventListener('focusout',e=>{if(!rail.contains(e.relatedTarget)){galleryHovered=false;galleryElapsed=0;}});
  const browsers={
    gallery:SoraGallery.createBrowser({root:q('.personal-image-browser'),mode:'works',assets:artworks,library,onChange:(_,options)=>{if(state.page==='gallery')setRoute(options?.history||'push');}}),
    materials:SoraGallery.createBrowser({root:q('.material-image-browser'),mode:'materials',assets:artworks,library,onChange:(_,options)=>{if(state.page==='materials')setRoute(options?.history||'push');}})
  };
  qa('[data-home-work]').forEach(button=>button.addEventListener('click',()=>{
    browsers.gallery.setState({itemId:button.dataset.homeWork},{notify:false});navigate('gallery',false);
    q('[data-view="gallery"] .browser-viewer').scrollIntoView({behavior:state.reduced?'instant':'smooth',block:'start'});
  }));
  qa('[data-open-materials]').forEach(button=>button.addEventListener('click',()=>navigate('materials')));
  root.addEventListener('sora:browse',event=>{if(['gallery','materials'].includes(event.detail?.page))navigate(event.detail.page);});

  function projectPreview(p){
    if(p.screenshots.length)return `<img class="project-screenshot" src="${p.screenshots[0].full_image||p.screenshots[0].image}" alt="${esc(p.screenshots[0].title)}真实界面"><p class="media-caption">项目 README 真实截图 · <span class="screenshot-caption">${esc(p.screenshots[0].title)}</span></p><div class="screenshot-switcher">${p.screenshots.map((s,i)=>`<button type="button" class="cursor-interaction" data-screenshot="${i}" aria-pressed="${i===0}">${esc(s.title)}</button>`).join('')}</div>`;
    if(p.id==='kanali-video')return `<p class="demo-badge">交互原理演示 · 非软件实机界面</p><div class="mini-demo"><div class="mapping-preview">PANORAMA</div><span class="media-caption demo-status">平面素材 → 指定眼位的曲面映射</span><button type="button" class="demo-button cursor-interaction" data-demo="mapping">切换映射状态</button></div>`;
    if(p.id==='bilidownloader')return `<p class="demo-badge">功能流程演示 · 非软件实机界面</p><div class="mini-demo"><div class="demo-step is-active" data-flow-step="0">① 读取可访问的视频与音轨</div><span class="demo-arrow">↓</span><div class="demo-step" data-flow-step="1">② 在浏览器内本地合并</div><span class="demo-arrow">↓</span><div class="demo-step" data-flow-step="2">③ 保存 MP4 / 音频文件</div><button type="button" class="demo-button cursor-interaction" data-demo="download">播放流程示意</button></div>`;
    if(p.id!=='codex-proxy-template')return `<div class="mini-demo"><span class="cover-word">${esc(p.name)}</span><p class="media-caption">这个项目还没有收录界面截图。可以到 GitHub 阅读 README 和使用说明。</p></div>`;
    return `<p class="demo-badge">路由结构演示 · 非软件实机界面</p><div class="mini-demo"><div class="route-demo"><span>Codex</span><b>→</b><span>专用代理</span></div><div class="route-demo"><span>其他应用</span><b>→</b><span>原有网络</span></div><button type="button" class="demo-button cursor-interaction" data-demo="route">点亮 Codex 路由</button><p class="media-caption demo-status">只改变 Codex 的代理路径</p></div>`;
  }
  function paintProject(){
    clearInterval(demoTimer);state.imageIndex=0;const p=projects[state.project];
    q('.project-switcher').innerHTML=projects.map((project,i)=>`<button type="button" class="cursor-interaction" data-detail-project="${i}" aria-pressed="${i===state.project}">${esc(project.name)}</button>`).join('');
    q('.project-detail').innerHTML=`<div class="project-detail-head"><div><h2>${esc(p.name)}</h2><p>${esc(p.tagline)}</p></div><span class="version-tag">${esc(p.version||'PUBLIC REPOSITORY')}</span></div><div class="detail-layout"><div class="project-media">${projectPreview(p)}</div><div class="detail-copy"><p>${esc(p.summary)}</p><div class="project-meta"><span>${esc(p.platform)}</span><span>${esc(p.language)}</span><span>AI Vibe Coded</span></div><h3>它能做些什么</h3><ul>${p.features.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><div class="detail-actions"><a href="${esc(p.repo)}" target="_blank" rel="noopener noreferrer" class="cursor-interaction">在 GitHub 查看 ↗</a><a href="${esc(p.entry_url||p.repo)}" target="_blank" rel="noopener noreferrer" class="cursor-interaction">${esc(p.entry_label||'阅读使用说明')} ↗</a></div></div></div><details class="limits-note"><summary class="cursor-interaction">使用前的小字说明 ＋</summary><ul>${p.limits.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p>资料来自公开仓库、README 与发布页 · 自动更新基础信息 · 许可证：${esc(p.license||'仓库未明确声明')}</p></details>`;
    qa('[data-detail-project]').forEach(b=>b.addEventListener('click',()=>{state.project=Number(b.dataset.detailProject);paintProject();paintDeck();setRoute('push');persist();}));
    qa('[data-screenshot]').forEach(b=>b.addEventListener('click',()=>{state.imageIndex=Number(b.dataset.screenshot);const shot=p.screenshots[state.imageIndex];q('.project-screenshot').src=shot.full_image||shot.image;q('.project-screenshot').alt=shot.title+'真实界面';q('.screenshot-caption').textContent=shot.title;qa('[data-screenshot]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));}));
    qa('[data-demo]').forEach(b=>b.addEventListener('click',()=>{
      if(b.dataset.demo==='mapping'){const mapped=q('.mapping-preview').classList.toggle('is-mapped');q('.demo-status').textContent=mapped?'示意：根据曲面和眼位重新映射画面':'平面素材 → 指定眼位的曲面映射';}
      if(b.dataset.demo==='route'){const active=q('.route-demo').classList.toggle('is-active');q('.demo-status').textContent=active?'Codex → 专用代理；其他应用保持原路径':'只改变 Codex 的代理路径';}
      if(b.dataset.demo==='download'){clearInterval(demoTimer);let step=0;const nodes=qa('[data-flow-step]');nodes.forEach((n,i)=>n.classList.toggle('is-active',i===0));b.textContent='流程演示中…';demoTimer=setInterval(()=>{step++;nodes.forEach((n,i)=>n.classList.toggle('is-active',i===step));if(step>=2){clearInterval(demoTimer);b.textContent='再看一次流程';}},state.reduced?100:800);}
    }));
  }
  const archivedIps=library.ips.filter(ip=>ip.systemRole!=='owner-works');
  q('.interest-count').textContent=`${archivedIps.length} 个 IP / 系列 · ${library.characters.length} 位角色 · ${artworks.filter(a=>!a.ownerWork&&a.enabled!==false).length} 张素材`;
  function archivePerson(group,person){
    const character=library.characters.find(c=>c.ipId===group.id&&(c.name===person||(c.aliases||[]).includes(person)));
    return `<button type="button" class="${person===group.favorite?'favorite-person':''}" data-material-ip="${esc(group.id)}" ${character?`data-material-character="${esc(character.id)}"`:`data-material-series="${esc(person)}"`}>${esc(interests.display_aliases?.[person]||person)}</button>`;
  }
  q('.focus-archive').innerHTML=interests.focus_groups.map(group=>{
    const theme=themes.find(t=>t.id===group.id);const people=[...new Set([...(group.characters||[]),...(group.series||[])])];
    return `<section class="focus-entry"><div class="focus-heading"><h2><button type="button" class="archive-heading-link" data-material-ip="${esc(group.id)}">${esc(group.name)} ↗</button></h2>${theme?`<button type="button" class="text-button cursor-interaction" data-favorite-theme="${esc(group.id)}">切到首页主题 ↗</button>`:''}</div>${group.favorite?`<p class="favorite-star">♥ 最喜欢：${archivePerson(group,group.favorite)}</p>`:''}<div class="favorite-chips">${people.map(person=>archivePerson(group,person)).join('')}</div>${group.themeKey==='fate'?'<p>从 stay night 的不同路线，到 Zero、Apocrypha 和 Grand Order，都是这个系列的一部分。</p>':''}${group.themeKey==='mangekyou'?'<p>历代作品，都可以在这里留下一页。</p>':''}<small>${artworks.filter(a=>!a.ownerWork&&a.enabled!==false&&a.ipId===group.id).length} 张素材已收录；没有图片的角色，也会在档案里保留位置。</small></section>`;
  }).join('');
  const focusIds=new Set(interests.focus_groups.map(group=>group.id));
  q('.extended-tags').innerHTML=archivedIps.filter(ip=>!focusIds.has(ip.id)).map(ip=>`<button type="button" data-material-ip="${esc(ip.id)}">${esc(ip.name)}</button>`).join('');
  root.addEventListener('click',event=>{
    const button=event.target.closest('[data-material-ip]');if(!button)return;
    browsers.materials.setState({ipId:button.dataset.materialIp,characterId:button.dataset.materialCharacter||'',series:button.dataset.materialSeries||''},{notify:false});
    navigate('materials');
  });
  qa('[data-favorite-theme]').forEach(b=>b.addEventListener('click',()=>{const index=themes.findIndex(t=>t.id===b.dataset.favoriteTheme);if(index>=0){state.theme=index;paintTheme();navigate('home');}}));
  const nativePage=window.top===window.self;
  function setRoute(mode='replace'){
    if(!nativePage||!mode)return;
    let route='#/'+state.page;
    if(state.page==='projects')route+='/'+encodeURIComponent(projects[state.project].id);
    if(browsers[state.page]){
      const filter=browsers[state.page].getState(),params=new URLSearchParams();
      if(state.page==='materials'&&filter.ipId)route+='/'+encodeURIComponent(filter.ipId);
      for(const [key,param] of [['characterId','character'],['kind','kind'],['use','use'],['tag','tag'],['year','year'],['series','series'],['search','q'],['itemId','item']])if(filter[key])params.set(param,filter[key]);
      if(filter.itemId&&filter.imagePage)params.set('p',String(filter.imagePage+1));
      if(params.size)route+='?'+params;
    }
    if(location.hash!==route)history[mode==='push'?'pushState':'replaceState'](null,'',route);
  }
  function navigate(page,scroll=true,historyMode='push'){
    state.page=page;root.dataset.page=page;qa('[data-view]').forEach(v=>v.hidden=v.dataset.view!==page);
    qa('.nav-item').forEach(b=>{const current=b.dataset.page===page||(page==='materials'&&b.dataset.page==='interests');b.classList.toggle('is-current',current);if(current)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
    if(page==='projects')paintProject();else clearInterval(demoTimer);
    if(scroll)root.scrollIntoView({behavior:state.reduced?'instant':'smooth',block:'start'});
    setRoute(historyMode);persist();
  }
  function readRoute(){
    if(!nativePage)return state.page;
    const [path,query='']=location.hash.replace(/^#\/?/,'').split('?');
    let parts;try{parts=path.split('/').map(decodeURIComponent);}catch(_){return 'home';}
    const page=['home','projects','gallery','interests','materials'].includes(parts[0])?parts[0]:'home';
    if(page==='projects'){const index=projects.findIndex(p=>p.id===parts[1]);if(index>=0)state.project=index;}
    if(browsers[page]){
      const params=new URLSearchParams(query),filter={ipId:page==='materials'?parts[1]||'':''};
      for(const [key,param] of [['characterId','character'],['kind','kind'],['use','use'],['tag','tag'],['year','year'],['series','series'],['search','q'],['itemId','item']])filter[key]=params.get(param)||'';
      filter.imagePage=Math.max(0,(Number(params.get('p'))||1)-1);
      browsers[page].setState(filter,{notify:false});
    }
    return page;
  }
  qa('[data-page]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.page)));
  state.page=readRoute();
  const restoreRoute=()=>navigate(readRoute(),false,null);
  if(nativePage){window.addEventListener('popstate',restoreRoute);window.addEventListener('hashchange',restoreRoute);}
  const fortunes=['全图鉴博士今天也在认真摸鱼。','能打人机，就绝不为难自己。','新的点子，又交给 AI 了。','B 段不可怕，下一把再来！'];let fortune=0;
  q('.avatar').addEventListener('click',()=>{toast(fortunes[fortune++%fortunes.length]);});
  const avatarPicker=SoraRotation.createLibrary({...library,assets:artworks},library.settings);
  const configuredAvatar=artById.get(library.settings.avatarAsset);
  const avatarArt=configuredAvatar?.uses.includes('avatar')?configuredAvatar:avatarPicker.next({use:'avatar',ipId:library.settings.defaultIp})||avatarPicker.next({use:'avatar'});
  if(avatarArt){q('.avatar').innerHTML=`<img src="${avatarArt.image}" alt="${esc(avatarArt.label)}角色头像">`;q('.avatar').classList.add('character-avatar');}
  q('.source-links').innerHTML=artworks.map(t=>`<a href="${esc(t.source)}" target="_blank" rel="noopener noreferrer" class="cursor-interaction">${esc(t.label)} · ${t.dimensions.join('×')} · ${t.source_kind==='community_wiki_game_asset'?'PRTS 来源':'原始来源'} ↗</a>`).join('');
  q('.sources-toggle').addEventListener('click',()=>{const section=q('.source-notes');section.hidden=!section.hidden;});
  let pointerFrame=0;
  root.addEventListener('pointermove',event=>{if(state.reduced||event.pointerType==='touch'||state.page!=='home'||pointerFrame)return;pointerFrame=requestAnimationFrame(()=>{const rect=root.getBoundingClientRect();const art=artById.get(root.dataset.asset);const strength=art?.adaptive.parallax||8;root.style.setProperty('--px',((event.clientX-rect.left)/rect.width-.5)*strength+'px');root.style.setProperty('--py',((event.clientY-rect.top)/700-.5)*strength*.6+'px');pointerFrame=0;});});
  root.addEventListener('pointerleave',()=>{root.style.setProperty('--px','0px');root.style.setProperty('--py','0px');});
  root.addEventListener('click',event=>{if(state.reduced||event.target.closest('a'))return;const rect=root.getBoundingClientRect();const spark=document.createElement('span');spark.className='click-spark';spark.textContent='✧';spark.setAttribute('aria-hidden','true');spark.style.left=event.clientX-rect.left+'px';spark.style.top=event.clientY-rect.top+'px';root.append(spark);setTimeout(()=>spark.remove(),850);});
  const ticker=setInterval(()=>{
    if(!root.isConnected){clearInterval(ticker);clearInterval(demoTimer);return;}
    const now=performance.now();const elapsed=Math.min(now-lastTick,250);lastTick=now;
    if(document.hidden||state.page!=='home')return;
    if(state.scenePlaying){sceneElapsed+=elapsed;if(sceneElapsed>=sceneDuration){sceneElapsed=0;changeTheme(1,false);}}
    q('.theme-progress>span').style.width=(sceneElapsed/sceneDuration*100)+'%';
    const currentMotion=artById.get(root.dataset.asset)?.adaptive.motion??.5;
    if(state.deckPlaying&&!deckHovered){deckElapsed+=elapsed;if(deckElapsed>=5200+(1-currentMotion)*1600){state.project=(state.project+1)%projects.length;paintDeck();}}
    if(state.galleryPlaying&&!galleryHovered){galleryElapsed+=elapsed;if(galleryElapsed>=3600+(1-currentMotion)*1200)moveGallery(1);}
  },100);
  window.addEventListener('openai:set_globals',event=>{const incoming=event.detail?.globals?.widgetState?.modelContent;if(!incoming)return;const ti=themes.findIndex(t=>t.id===incoming.theme);if(ti>=0&&ti!==state.theme){state.theme=ti;paintTheme();}});
  picker.record(artById.get(themes[state.theme].variants[state.variants[themes[state.theme].id]||0]));
  paintTheme(true);paintDeck();paintPlayback();
  navigate(state.page,false,'replace');
  if(globalThis.Tweak){const design={width:900,background:.96};const tweak=new Tweak({container:root,onChange:()=>{q('.blog-column').style.maxWidth=design.width+'px';root.style.setProperty('--scene-opacity',design.background);}});tweak.addSlider(design,'width',{label:'正文宽度',min:760,max:1060,step:20,unit:'px'});tweak.addSlider(design,'background',{label:'背景浓度',min:.3,max:1,step:.05});}
})();
