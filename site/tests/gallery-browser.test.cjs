const test = require('node:test');
const assert = require('node:assert/strict');
const G = require('../gallery-browser.js');

const library = {
  ips: [
    {id:'genshin',name:'原神',series:[]},
    {id:'arknights',name:'明日方舟',series:[]},
    {id:'fate',name:'Fate 系列',series:['Fate/Grand Order']},
    {id:'empty-ip',name:'尚未补图的世界',enabled:false},
    {id:'mine',name:'本人作品',systemRole:'owner-works'}
  ],
  characters: [
    {id:'keqing',ipId:'genshin',name:'刻晴',aliases:['Keqing']},
    {id:'nilou',ipId:'genshin',name:'妮露'},
    {id:'amiya',ipId:'arknights',name:'阿米娅'}
  ]
};
const material = (id,fields={}) => ({id,ipId:'genshin',characterIds:['keqing'],label:'刻晴立绘',kind:'character-art',uses:['hero'],tags:['紫色'],enabled:true,...fields});
const work = (id,fields={}) => ({id,ipId:'mine',ownerWork:true,uses:['gallery'],label:'作品 #123',source:'https://www.pixiv.net/artworks/123',tags:[],...fields});

test('own works are grouped by Pixiv work, with sorted pages and current editable labels', () => {
  const input = [
    work('p2',{pageIndex:2,metadataStatus:'pending',originalTitle:'旧标题'}),
    material('official'),
    work('p0',{pageIndex:0,label:'编辑后的标题',description:'站长的说明',tags:['草稿'],publishedAt:'2024-05-01'}),
    work('p1',{pageIndex:1,tags:['草稿','角色']}),
    work('disabled',{workId:'pixiv-disabled',enabled:false}),
    work('avatar',{workId:'pixiv-avatar',uses:['avatar']})
  ];
  const snapshot = JSON.stringify(input);
  const grouped = G.groupWorks(input);
  assert.equal(grouped.length,1);
  assert.deepEqual(grouped[0].pages.map(page=>page.id),['p0','p1','p2']);
  assert.equal(grouped[0].title,'编辑后的标题');
  assert.equal(grouped[0].description,'站长的说明');
  assert.equal(grouped[0].year,'2024');
  assert.deepEqual(grouped[0].tags,['草稿','角色']);
  assert.equal(grouped[0].metadataPending,true);
  assert.equal(JSON.stringify(input),snapshot);
});

test('explicit work IDs survive source changes; local-only imports stay separate', () => {
  assert.equal(G.workKey(work('a',{workId:'stable-work',source:'https://example.test/new'})),'stable-work');
  assert.equal(G.workKey(work('a',{source:{page:'https://www.pixiv.net/en/artworks/987?lang=en'}})),'pixiv-987');
  assert.equal(G.workKey(work('local-a',{source:''})),'local-a');
  assert.equal(G.workKey(work('local-b',{source:''})),'local-b');
  assert.equal(G.workKey(work('fake',{source:'https://www.pixiv.net.evil.test/artworks/987'})),'fake');
});

test('a 17-page import renders as 12 works and never fills gaps with fandom materials', () => {
  const originals=[];
  for(let index=0;index<12;index++)for(let page=0;page<(index===0?6:1);page++)originals.push(work(`work-${index}-${page}`,{workId:`pixiv-${index}`,pageIndex:page,pageCount:index===0?6:1}));
  const grouped=G.groupWorks([...originals,material('illustration',{uses:['gallery']})]);
  assert.equal(grouped.length,12);
  assert.equal(grouped.reduce((sum,item)=>sum+item.pages.length,0),17);
  assert.deepEqual(G.groupWorks([material('only-material')]),[]);
});

test('material browsing preserves reference-only and non-gallery assets', () => {
  const items=G.materialItems([
    material('reference',{uses:['reference']}),
    material('hero',{uses:['hero']}),
    material('gallery',{uses:['gallery']}),
    material('disabled',{enabled:false}),
    work('mine')
  ]);
  assert.deepEqual(items.map(item=>item.id),['reference','hero','gallery']);
  assert.deepEqual(G.filterItems(items,{use:'reference'},library).map(item=>item.id),['reference']);
});

test('the material tree keeps zero-image IPs and characters, excluding the owner system folder', () => {
  const tree=G.materialTree(library,[material('a'),material('b',{characterIds:[]}),work('mine')]);
  assert.deepEqual(tree.map(ip=>ip.id),['genshin','arknights','fate','empty-ip']);
  assert.equal(tree.find(ip=>ip.id==='genshin').count,2);
  assert.equal(tree.find(ip=>ip.id==='genshin').children.find(person=>person.id==='keqing').count,1);
  assert.equal(tree.find(ip=>ip.id==='genshin').children.find(person=>person.id==='nilou').count,0);
  assert.equal(tree.find(ip=>ip.id==='empty-ip').count,0);
  assert.equal(tree.find(ip=>ip.id==='arknights').children[0].count,0);
});

test('IP, character, kind, use and tag filters are conjunctive; aliases are searchable', () => {
  const items=G.materialItems([
    material('target',{uses:['hero','reference']}),
    material('wrong-person',{characterIds:['nilou']}),
    material('wrong-kind',{kind:'wallpaper'}),
    material('wrong-tag',{tags:['蓝色']}),
    material('other-ip',{ipId:'arknights',characterIds:['amiya'],label:'阿米娅'})
  ]);
  const filter={ipId:'genshin',characterId:'keqing',kind:'character-art',use:'reference',tag:'紫色',search:'KEQING'};
  assert.deepEqual(G.filterItems(items,filter,library).map(item=>item.id),['target']);
  assert.equal(G.filterItems(items,{ipId:'empty-ip'},library).length,0);
  assert.equal(G.filterItems(items,{ipId:'genshin',characterId:'amiya'},library).length,0);
});

test('owner filters operate on the whole work and expose an honest unclassified category', () => {
  const items=G.groupWorks([
    work('a0',{workId:'a',pageIndex:0,publishedAt:'2025-01-01'}),
    work('a1',{workId:'a',pageIndex:1,tags:['完成稿'],description:'夏天的记录'}),
    work('b',{workId:'b',metadataStatus:'pending'})
  ]);
  assert.deepEqual(G.filterItems(items,{tag:'完成稿',year:'2025',search:'夏天'},library).map(item=>item.id),['a']);
  assert.deepEqual(G.filterItems(items,{tag:G.UNTAGGED},library).map(item=>item.id),['b']);
  assert.equal(items[1].title,'作品 #123');
  assert.deepEqual(items[1].tags,[]);
  assert.equal(items[1].description,'');
});

test('series links filter named material without inventing images for an empty series', () => {
  const items=G.materialItems([material('fgo',{ipId:'fate',label:'Fate/Grand Order 主视觉',characterIds:[]})]);
  assert.equal(G.filterItems(items,{ipId:'fate',series:'Fate/Grand Order'},library).length,1);
  assert.equal(G.filterItems(items,{ipId:'fate',series:'Fate/Zero'},library).length,0);
});

test('importer createdAt dates produce real year filters without deriving years from IDs', () => {
  const items=G.groupWorks([
    work('dated',{workId:'dated',createdAt:'2023-08-17T12:30:00+09:00'}),
    work('undated',{workId:'pixiv-20260101',metadataStatus:'pending'})
  ]);
  assert.equal(items[0].year,'2023');
  assert.equal(items[1].year,'');
  assert.deepEqual(G.filterItems(items,{year:'2023'},library).map(item=>item.id),['dated']);
});
