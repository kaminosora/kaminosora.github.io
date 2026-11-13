"""Local content library: validate, import originals, import owner-provided works."""
from pathlib import Path
import argparse
import json
import re
import shutil
import hashlib
from urllib.parse import urlparse
from PIL import Image

ROOT=Path(__file__).resolve().parent
CATALOG=ROOT/'content/library.json'
KINDS={'character-art','character-sheet','key-visual','illustration','wallpaper','screenshot','photo','logo','other'}
USES={'hero','gallery','avatar','card','reference'}

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def local_file(value, root=None):
    root=ROOT if root is None else root
    if not isinstance(value,str) or not value or Path(value).is_absolute() or ':' in value:
        raise ValueError('file 必须是预览目录内的相对路径')
    path=(root/value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('file 不得越出预览目录')
    if path.suffix.lower() not in {'.jpg','.jpeg','.png','.webp','.avif','.gif'}:
        raise ValueError('素材只接受常见栅格图片格式')
    return path

def safe_url(value):
    return not value or (isinstance(value,str) and urlparse(value).scheme in {'https','http'} and bool(urlparse(value).netloc) and not urlparse(value).username and not urlparse(value).password)

def validate(data, check_files=True):
    errors=[]
    if data.get('schemaVersion')!=1: errors.append('schemaVersion 必须为 1')
    indexes={}
    for group in ('ips','characters','assets'):
        records=data.get(group)
        if not isinstance(records,list):
            errors.append(group+' 必须是数组'); records=[]
        index={}
        for item in records:
            if not isinstance(item,dict): errors.append(group+' 记录必须是对象'); continue
            key=item.get('id','')
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',key): errors.append(f'{group}: 无效 ID {key}')
            if key in index: errors.append(f'{group}: 重复 ID {key}')
            index[key]=item
            weight=item.get('weight',1)
            if not isinstance(weight,(int,float)) or isinstance(weight,bool) or not 0<=weight<=100: errors.append(f'{key}: weight 范围为 0–100')
        indexes[group]=index
    for ip in indexes['ips'].values():
        weight=ip.get('rotation',{}).get('weight',1)
        if not isinstance(weight,(int,float)) or isinstance(weight,bool) or not 0<=weight<=100: errors.append(ip['id']+': IP weight 范围为 0–100')
        if not isinstance(ip.get('name'),str) or not ip['name'].strip(): errors.append(ip['id']+': 缺少名称')
    for c in indexes['characters'].values():
        if c.get('ipId') not in indexes['ips']: errors.append(c['id']+': IP 不存在')
        if not isinstance(c.get('name'),str) or not c['name'].strip(): errors.append(c['id']+': 缺少名称')
    for a in indexes['assets'].values():
        key=a['id']
        if a.get('ipId') not in indexes['ips']: errors.append(key+': IP 不存在')
        if not isinstance(a.get('label'),str) or not a['label'].strip(): errors.append(key+': 缺少标题')
        people=a.get('characterIds',[])
        if not isinstance(people,list): errors.append(key+': characterIds 必须是数组'); people=[]
        for cid in people:
            c=indexes['characters'].get(cid)
            if not c or c.get('ipId')!=a.get('ipId'): errors.append(key+': 角色不存在或属于其他 IP: '+str(cid))
        if a.get('kind') not in KINDS: errors.append(key+': 不支持的素材类型')
        if not isinstance(a.get('uses'),list) or any(u not in USES for u in a.get('uses',[])): errors.append(key+': 不支持的用途')
        for field in ('page','image'):
            if not safe_url(a.get('source',{}).get(field,'')): errors.append(key+': 来源地址只支持 http(s)')
        appearance=a.get('appearance',{})
        if appearance.get('fit','auto') not in {'auto','cover','contain'}: errors.append(key+': fit 只支持 auto/cover/contain')
        if appearance.get('effect','auto') not in {'auto','petals','snow','sparks','stars','motes','none'}: errors.append(key+': effect 类型不支持')
        if appearance.get('accent') and not re.fullmatch(r'#[a-fA-F0-9]{6}',appearance['accent']): errors.append(key+': accent 必须为 #RRGGBB')
        focal=appearance.get('focalPoint',[78,40])
        if not isinstance(focal,list) or len(focal)!=2 or any(not isinstance(n,(int,float)) or not 0<=n<=100 for n in focal): errors.append(key+': focalPoint 为两个 0–100 数值')
        for field in ('motion','overlay'):
            if field in appearance and (not isinstance(appearance[field],(int,float)) or not 0<=appearance[field]<=1): errors.append(key+': '+field+' 范围为 0–1')
        try:
            path=local_file(a.get('file'))
            if check_files:
                with Image.open(path) as im: im.verify()
        except (ValueError,OSError,TypeError) as exc: errors.append(f'{key}: {exc}')
    settings=data.get('settings',{})
    if not isinstance(settings.get('sceneSeconds',12),(int,float)) or not 4<=settings.get('sceneSeconds',12)<=120: errors.append('sceneSeconds 范围为 4–120')
    return errors

def commit(data):
    errors=validate(data)
    if errors: raise ValueError('\n'.join(errors))
    backup=ROOT/'content/backups'/('library-'+__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
    if CATALOG.exists():
        backup.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(CATALOG,backup)
    save(CATALOG,data)

def finish_import(data,copies):
    # Plan and validate the entire batch before writing any image. Matching
    # orphaned files from an interrupted copy are safe to reuse on retry.
    errors=validate(data,False)
    if errors:raise ValueError('\n'.join(errors))
    for src,dst in copies:
        if dst.exists() and hashlib.sha256(src.read_bytes()).digest()!=hashlib.sha256(dst.read_bytes()).digest():
            raise ValueError('目标已存在且内容不同，停止覆盖：'+str(dst))
    for src,dst in copies:
        if not dst.exists():
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    commit(data)

def import_manifest(path):
    data=read(CATALOG); imported=0;copies=[]
    manifest=read(path); manifest=manifest.get('assets',[]) if isinstance(manifest,dict) else manifest
    for item in manifest:
        if any(a['id']==item['id'] for a in data['assets']): continue
        ip=item['ip_id']; person=item.get('character','')
        if not any(x['id']==ip for x in data['ips']): raise ValueError('先创建 IP: '+ip)
        people=[c['id'] for c in data['characters'] if c['ipId']==ip and any(n==person or n in person for n in [c['name']]+c.get('aliases',[]))]
        src=Path(item['original_path']).resolve()
        with Image.open(src) as im: im.verify()
        dst=ROOT/'media/originals'/ip/(item['id']+src.suffix.lower())
        copies.append((src,dst))
        uses=[('reference' if u in {'profile','character-profile'} else u) for u in item.get('recommended_usages',['hero','gallery'])]
        uses=[u for u in uses if u in USES] or ['reference']
        data['assets'].append({'id':item['id'],'ipId':ip,'characterIds':people,'label':person or item['id'],'file':dst.relative_to(ROOT).as_posix(),'kind':item.get('kind','character-art'),'uses':uses,'tags':item.get('tags',[]),'enabled':True,'weight':1,'source':{'page':item['source_page'],'image':item['source_image'],'kind':item.get('source_kind','community_mirror'),'rights':item.get('rights_note','原权利人所有'),'evidence':item.get('identity_evidence','')},'appearance':{'fit':'auto','focalPoint':[78,40]},'notes':'保留来源原图，未放大或 AI 重绘。'})
        imported+=1
    finish_import(data,copies); return imported

def import_works(path):
    """Explicit local import only. Never sends credentials or fetches Pixiv."""
    path=Path(path).resolve(); incoming=read(path); data=read(CATALOG)
    if str(incoming.get('authorId'))!=data['integrations']['pixiv']['userId']: raise ValueError('作者 ID 与站长 Pixiv ID 不一致')
    if not any(ip['id']=='personal-art' for ip in data['ips']): data['ips'].append({'id':'personal-art','name':'我的作品','enabled':True,'rotation':{'enabled':False,'weight':1},'tags':['本人作品']})
    added=0;copies=[]
    for work in incoming.get('works',[]):
        wid=str(work['id'])
        if not wid.isdigit(): raise ValueError('作品 ID 必须为数字')
        for page,file in enumerate(work.get('files',[])):
            key=f'pixiv-{wid}-p{page}'
            if any(a['id']==key for a in data['assets']): continue
            src=local_file(file,path.parent)
            with Image.open(src) as image: image.verify()
            dst=ROOT/'media/originals/personal-art'/(key+src.suffix.lower());copies.append((src,dst))
            data['assets'].append({'id':key,'ipId':'personal-art','characterIds':[],'label':work['title']+(f' · {page+1}' if len(work['files'])>1 else ''),'file':dst.relative_to(ROOT).as_posix(),'kind':'illustration','uses':['gallery'],'tags':work.get('tags',[]),'enabled':True,'weight':1,'ownerWork':True,'source':{'page':'https://www.pixiv.net/artworks/'+wid,'image':'','kind':'owner_provided_pixiv_work','rights':'站长手动提供并声明为本人作品'},'appearance':{'fit':'auto'},'notes':'本地手动导入；非 API 自动同步。'})
            added+=1
    finish_import(data,copies);return added

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('validate')
    p=sub.add_parser('import-manifest');p.add_argument('file')
    p=sub.add_parser('import-works');p.add_argument('file')
    p=sub.add_parser('apply');p.add_argument('file')
    args=parser.parse_args()
    try:
        if args.command=='validate':
            errors=validate(read(CATALOG))
            if errors: raise ValueError('\n'.join(errors))
            print('素材库校验通过。')
        elif args.command=='import-manifest': print('新增素材：',import_manifest(args.file))
        elif args.command=='import-works': print('新增本人作品图片：',import_works(args.file))
        elif args.command=='apply': commit(read(args.file)); print('已校验、备份并应用素材库。请运行 build_preview.py。')
    except (ValueError,OSError,KeyError,TypeError) as exc:
        parser.exit(1,str(exc)+'\n')
