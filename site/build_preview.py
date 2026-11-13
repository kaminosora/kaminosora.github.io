"""Build the local preview or static Pages frontend from content/library.json."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageOps, ImageFilter, ImageStat
import argparse
import base64
import colorsys
import hashlib
import io
import json
import math
from library import ROOT, CATALOG, read, save, validate, local_file

GENERATED=ROOT/'generated'
MEDIA=ROOT/'media/derived'

def hexrgb(rgb):
    return '#'+''.join(f'{round(max(0,min(1,c))*255):02x}' for c in rgb)

def luminance(rgb):
    converted=[c/12.92 if c<=0.04045 else ((c+0.055)/1.055)**2.4 for c in rgb]
    return sum(c*w for c,w in zip(converted,(0.2126,0.7152,0.0722)))

def contrast(a,b):
    x,y=sorted((luminance(a),luminance(b)))
    return (y+0.05)/(x+0.05)

def analyze_image(image, appearance=None):
    appearance=appearance or {}
    rgba=image.convert('RGBA'); rgba.thumbnail((100,100))
    pixels=[(r/255,g/255,b/255) for r,g,b,a in rgba.getdata() if a>150]
    if not pixels: pixels=[(.6,.5,.7)]
    bins=[0.0]*36; sat_sum=0; light_sum=0
    for rgb in pixels:
        h,l,s=colorsys.rgb_to_hls(*rgb)
        sat_sum+=s;light_sum+=l
        if .12<l<.88 and s>.12: bins[min(35,int(h*36))]+=s*(1-abs(l-.5))
    hue=(max(range(36),key=lambda i:bins[i])+.5)/36 if max(bins)>0 else .76
    sat=sat_sum/len(pixels); light=light_sum/len(pixels)
    if appearance.get('accent'):
        value=appearance['accent'];rgb=tuple(int(value[i:i+2],16)/255 for i in (1,3,5));hue,_,sat=colorsys.rgb_to_hls(*rgb)
    paper=colorsys.hls_to_rgb(hue,.966,min(.45,max(.16,sat)))
    accent=colorsys.hls_to_rgb(hue,.38,min(.64,max(.28,sat)))
    level=.38
    while contrast(accent,paper)<4.5 and level>.15:
        level-=.01;accent=colorsys.hls_to_rgb(hue,level,min(.64,max(.28,sat)))
    soft=colorsys.hls_to_rgb(hue,.88,min(.48,max(.2,sat)))
    secondary=colorsys.hls_to_rgb((hue+.12)%1,.52,.35)
    figure=rgba.getextrema()[-1][0]<200
    ratio=image.width/image.height
    edge=ImageStat.Stat(rgba.convert('L').filter(ImageFilter.FIND_EDGES)).mean[0]/255
    motion=appearance.get('motion',round(max(.25,min(.8,.78-edge*1.8-(.16 if ratio<.65 else 0))),3))
    effect='motes' if sat<.12 else 'petals' if hue>.83 or hue<.07 else 'sparks' if hue<.18 else 'snow' if .4<hue<.61 else 'stars'
    if appearance.get('effect','auto')!='auto':effect=appearance['effect']
    return {'accent':hexrgb(accent),'soft':hexrgb(soft),'paper':hexrgb(paper),'secondary':hexrgb(secondary),'ink':hexrgb(colorsys.hls_to_rgb(hue,.20,.12)),'muted':hexrgb(colorsys.hls_to_rgb(hue,.36,.10)),'effect':effect,'motion':motion,'parallax':round(4+motion*8,2),'duration':round(1.8+edge*2,2),'driftSeconds':round(23-motion*9,2),'overlay':appearance.get('overlay',round(.12+edge*.45,3)),'brightness':round(light,3),'complexity':round(edge,3),'accentContrast':round(contrast(accent,paper),2),'figure':figure,'fit':appearance.get('fit','auto'),'focalPoint':appearance.get('focalPoint',[78,40])}

def process_asset(asset):
    path=local_file(asset['file']); digest=hashlib.sha256(path.read_bytes()).hexdigest()
    cache=GENERATED/'analysis'/f"{asset['id']}.json"
    old=read(cache) if cache.exists() else {}
    appearance=asset.get('appearance',{})
    base=MEDIA/asset['id']; thumb=base.with_suffix('.thumb.webp'); display=base.with_suffix('.display.webp')
    if old.get('sha256')==digest and old.get('appearance')==appearance and thumb.exists() and display.exists():
        info=old
    else:
        with Image.open(path) as original:
            original=ImageOps.exif_transpose(original)
            dimensions=list(original.size)
            adaptive=analyze_image(original,appearance)
            image=original.convert('RGBA' if adaptive['figure'] else 'RGB')
            image.save(display,'WEBP',quality=93,method=4)
            image.thumbnail((760,760),Image.Resampling.LANCZOS)
            image.save(thumb,'WEBP',quality=84,method=4)
            megapixels=dimensions[0]*dimensions[1]/1_000_000
            quality=round(min(1,max(.55,.55+math.sqrt(megapixels)/5)),3)
            info={'sha256':digest,'dimensions':dimensions,'quality':quality,'adaptive':adaptive,'appearance':appearance}
        save(cache,info)
    return {**asset,**{k:v for k,v in info.items() if k!='appearance'},'image':thumb.relative_to(ROOT).as_posix(),'background':display.relative_to(ROOT).as_posix(),'original':asset['file'],'figure':info['adaptive']['figure'],'portrait':info['dimensions'][0]/info['dimensions'][1]<1.2,'position':' '.join(str(x)+'%' for x in appearance.get('focalPoint',[78,40])),'short_label':asset['label'],'gallery':'gallery' in asset['uses']}

def compact_image(path,paper,budget=5400):
    # Conversation preview only: composite transparency and compress, originals stay untouched.
    with Image.open(path) as image:
        image=image.convert('RGBA'); image.thumbnail((440,440),Image.Resampling.LANCZOS)
        bg=Image.new('RGBA',image.size,paper);bg.alpha_composite(image); image=bg.convert('RGB')
        result=b''
        for edge in (440,360,280,220,160):
            miniature=image.copy();miniature.thumbnail((edge,edge),Image.Resampling.LANCZOS)
            for quality in (65,48,32):
                out=io.BytesIO();miniature.save(out,'WEBP',quality=quality,method=4);result=out.getvalue()
                if len(result)<=budget:break
            if len(result)<=budget:break
    return 'data:image/webp;base64,'+base64.b64encode(result).decode()

def js_data(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')

def build(inline=None, production=False):
    library=read(CATALOG);errors=validate(library)
    if errors:raise ValueError('\n'.join(errors))
    GENERATED.mkdir(exist_ok=True);MEDIA.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:all_art=list(pool.map(process_asset,library['assets']))
    ip_by_id={ip['id']:ip for ip in library['ips']}
    for art in all_art:
        art['name']=ip_by_id[art['ipId']]['name'];art['source_info']=art['source'];art['source']=art['source_info'].get('page','');art['source_image']=art['source_info'].get('image','');art['source_kind']=art['source_info'].get('kind','unknown')
    char_by_id={c['id']:c for c in library['characters']}
    active=[a for a in all_art if a.get('enabled',True) and ip_by_id[a['ipId']].get('enabled',True) and all(char_by_id[cid].get('enabled',True) for cid in a.get('characterIds',[]))]
    themes=[]
    for ip in library['ips']:
        variants=[a['id'] for a in active if a['ipId']==ip['id'] and 'hero' in a['uses'] and a.get('weight',1)>0 and all(char_by_id[cid].get('weight',1)>0 for cid in a.get('characterIds',[]))]
        if ip.get('enabled',True) and ip.get('rotation',{}).get('enabled',True) and variants and ip.get('rotation',{}).get('weight',1)>0:
            themes.append({**ip,'variants':variants})
    if not themes: raise ValueError('至少需要一个开启轮播的 IP 和一张 hero 素材。')
    projects=read(ROOT/'content/projects.json');interests=read(ROOT/'content/interests.json')
    focus=[ip for ip in library['ips'] if ('扩展偏爱' not in ip.get('tags',[]) or ip.get('rotation',{}).get('enabled',False)) and ip.get('systemRole')!='owner-works']
    interests['focus_groups']=[{**ip,'characters':[c['name'] for c in library['characters'] if c['ipId']==ip['id']]} if any(c['ipId']==ip['id'] for c in library['characters']) else ip for ip in focus]
    interests['extended_interests']=[ip['name'] for ip in library['ips'] if '扩展偏爱' in ip.get('tags',[]) and not ip.get('rotation',{}).get('enabled',False)]
    metadata={'ips':library['ips'],'characters':library['characters'],'settings':library['settings'],'integrations':library.get('integrations',{})}
    data={'themes':themes,'artworks':active,'projects':projects,'interests':interests,'library':metadata,'production':production}
    save(GENERATED/'asset-index.json',all_art)
    (GENERATED/'manager-data.js').write_text('globalThis.SORA_LIBRARY = '+js_data(library)+';\nglobalThis.SORA_ASSET_META = '+js_data(all_art)+';\n',encoding='utf-8')
    template=(ROOT/'sora.template.html').read_text(encoding='utf-8').replace('@@CSS@@',(ROOT/'sora.css').read_text(encoding='utf-8')).replace('@@JS@@',(ROOT/'rotation-engine.js').read_text(encoding='utf-8')+'\n'+(ROOT/'sora.js').read_text(encoding='utf-8')).replace('第三版预览','第五版').replace('预览 v3 · 未发布','v5 · 持续生长中' if production else '预览 v5 · 本地草稿').replace('以下网络视觉图仅用于本地概念预览；角色与图片权利归原权利人。Pixiv 自动同步尚未接入。','角色与引用图片的权利归原权利人，以下记录素材来源。Pixiv 作品由站长手动导入。')
    fragment=template.replace('@@DATA@@',js_data(data))
    wrapper=(ROOT/'standalone.template.html').read_text(encoding='utf-8').replace('预览 v3','v5' if production else '预览 v5')
    if production:
        wrapper=wrapper.replace('<meta name="robots" content="noindex,nofollow">','<meta name="description" content="神の空的空想便笺：AI Vibe 项目、喜欢的角色与个人作品画廊。">')
    (ROOT/'index.html').write_text(wrapper.replace('@@FRAGMENT@@',fragment),encoding='utf-8')
    inline_bytes=None
    if inline:
        small=json.loads(json.dumps(data))
        for art in small['artworks']:
            art['image']=compact_image(ROOT/art['image'],art['adaptive']['paper'])
            art.pop('background',None);art.pop('original',None);art.pop('sha256',None);art.pop('source_info',None)
        for project in small['projects']:
            for shot in project['screenshots']:
                shot['image']=compact_image(ROOT/shot['image'],'#f5f3f8',6500);shot.pop('full_image',None)
        small['inline']=True
        html=template.replace('@@DATA@@',js_data(small))
        if len(html.encode())>1_000_000:raise ValueError('对话预览超过 1 MB；桌面预览已生成，请减小 compact_image 预算。')
        Path(inline).write_text(html,encoding='utf-8',newline='\n');inline_bytes=len(html.encode())
    report={'status':'production' if production else 'preview-only','assets':len(all_art),'activeAssets':len(active),'themes':len(themes),'heroAssets':sum(len(t['variants']) for t in themes),'ips':len(library['ips']),'characters':len(library['characters']),'inlineBytes':inline_bytes,'byIp':{t['id']:len(t['variants']) for t in themes}}
    save(GENERATED/'build-report.json',report);print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inline',type=Path);parser.add_argument('--production',action='store_true');args=parser.parse_args();build(args.inline,args.production)
