"""Local, transactional content operations. The editor never accepts file paths or IDs to create."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid

from PIL import Image
import library as catalog

ROOT = Path(__file__).resolve().parent
MAX_IMAGE_BYTES = 80 * 1024 * 1024
MAX_PIXELS = 80_000_000
FORMATS = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif', 'AVIF': '.avif'}


class ProjectLock:
    """Reentrant per-thread lock plus an OS lock shared by workbench instances."""
    def __init__(self):
        self.thread_lock=threading.RLock()
        self.local=threading.local()

    def __enter__(self):
        self.thread_lock.acquire()
        depth=getattr(self.local,'depth',0)
        if depth:
            self.local.depth=depth+1
            return self
        stream=None
        try:
            path=ROOT/'.studio/write.lock'
            path.parent.mkdir(parents=True,exist_ok=True)
            stream=path.open('a+b')
            if path.stat().st_size==0:stream.write(b'\0');stream.flush()
            started=time.monotonic()
            while True:
                try:
                    stream.seek(0)
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic()-started>=60:
                        raise ValueError('另一个素材工作台正在保存或构建，请等待完成后再试。') from exc
                    time.sleep(.05)
            self.local.stream=stream
            self.local.depth=1
            return self
        except Exception:
            if stream:stream.close()
            self.thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self.local.depth-=1
        if self.local.depth==0:
            stream=self.local.stream
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            except OSError:
                # Closing the descriptor releases the OS lock as well.
                pass
            finally:
                stream.close()
                del self.local.stream
                self.thread_lock.release()
        else:
            self.thread_lock.release()


LOCK = ProjectLock()


class RevisionConflict(ValueError):
    pass


def _catalog():
    return ROOT / 'content/library.json'


def _read():
    return catalog.read(_catalog())


def _revision():
    return hashlib.sha256(_catalog().read_bytes()).hexdigest()


def _name(value, limit=120):
    if not isinstance(value, str):
        raise ValueError('请填写名称。')
    value = unicodedata.normalize('NFKC', value).strip()
    value = ''.join(c for c in value if unicodedata.category(c) != 'Cc')
    if not value or len(value) > limit:
        raise ValueError(f'名称需要 1–{limit} 个字符。')
    return value


def _slug(value, limit=18):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', '-', unicodedata.normalize('NFKC', value))
    value = value.strip('. -')[:limit].rstrip('. -') or 'untitled'
    if value.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        value = 'item-' + value
    return value


def _identifier(prefix, name, uid, scope=''):
    digest = hashlib.sha256((scope + '\0' + name.casefold()).encode()).hexdigest()[:10]
    return f'{prefix}-{digest}-{uid[:10]}'


def _image_info(content, decode=True):
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError('图片不能为空，且单张不能超过 80 MB。')
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in FORMATS:
                raise ValueError('支持 JPG、PNG、WebP、GIF 和 AVIF 原图。')
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('图片超过 8000 万像素，请选用合适尺寸的原图。')
            info = {'width':image.width, 'height':image.height, 'ext':FORMATS[image.format],
                    'bytes':len(content), 'sha256':hashlib.sha256(content).hexdigest()}
            image.verify()
        if decode:
            # JPEG.verify() only checks the header. Decode before accepting an
            # upload so a truncated scan does not break the subsequent build.
            with Image.open(io.BytesIO(content)) as image:
                image.load()
        return info
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('无法读取图片；请使用原始图片文件，而不是网页或快捷方式。') from exc


def _checked_path(value):
    return catalog.local_file(value, ROOT)


def _normalize(data):
    """Regenerate every name-derived reference together; immutable uid tracks identity."""
    old_ids = {g:{r.get('id'):r for r in data[g]} for g in ('ips','characters','assets')}
    maps = {g:{} for g in old_ids}
    seen_uids = set()
    for group, prefix in (('ips','ip'),('characters','ch'),('assets','art')):
        seen_names = set()
        seen_ids = set()
        for record in data[group]:
            record.setdefault('uid', uuid.uuid4().hex)
            if not isinstance(record['uid'],str) or not re.fullmatch(r'[a-f0-9]{32}', record['uid']) or record['uid'] in seen_uids:
                raise ValueError('内部身份无效，请恢复备份后重试。')
            seen_uids.add(record['uid'])
            name_field = 'label' if group == 'assets' else 'name'
            record[name_field] = _name(record.get(name_field))
            old_id = record.get('id')
            if not isinstance(old_id,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',old_id) or old_id in seen_ids:
                raise ValueError('素材库包含重复或失效的编号，请恢复备份后重试。')
            seen_ids.add(old_id)
            scope = ''
            if group != 'ips':
                if record.get('ipId') not in maps['ips']:
                    raise ValueError('所属 IP 已不存在，请重新选择。')
                record['ipId'] = maps['ips'][record['ipId']]
                scope = record['ipId']
            signature = (scope, record[name_field].casefold())
            if group != 'assets' and signature in seen_names:
                raise ValueError('同一范围内已有这个名称，请直接编辑已有条目。')
            seen_names.add(signature)
            record['id'] = _identifier(prefix, record[name_field], record['uid'], scope)
            maps[group][old_id] = record['id']
            if group == 'assets':
                try:
                    record['characterIds'] = [maps['characters'][c] for c in record.get('characterIds',[])]
                except KeyError as exc:
                    raise ValueError('所选角色已不存在，请重新选择。') from exc
    for key, group in (('defaultIp','ips'),('defaultAsset','assets'),('avatarAsset','assets')):
        value = data['settings'].get(key)
        if value:
            data['settings'][key] = maps[group].get(value, value)
    return maps


def _commit(data, file_sources=None):
    """Copy first, validate, atomically switch catalog, then retire unreferenced originals."""
    previous = _read()
    file_sources = file_sources or {}
    maps = _normalize(data)
    ips = {r['id']:r for r in data['ips']}
    chars = {r['id']:r for r in data['characters']}
    copies = []
    for asset in data['assets']:
        ip = ips[asset['ipId']]
        src = file_sources.get(asset['uid']) or _checked_path(asset['file'])
        content = src.read_bytes()
        # Existing unchanged bytes were decoded during import. Renaming an IP
        # need not decompress every high-resolution image again.
        info = _image_info(content, decode=asset.get('sha256') != hashlib.sha256(content).hexdigest())
        people = [chars[c] for c in asset.get('characterIds',[]) if c in chars]
        person_part = (_slug(people[0]['name'])+'-'+people[0]['uid'][:8]) if len(people)==1 else ('group-'+hashlib.sha256('|'.join(sorted(c['uid'] for c in people)).encode()).hexdigest()[:10] if people else 'scenes')
        name = f"{asset['kind']}-{_slug(asset['label'],24)}-{asset['uid'][:8]}-{info['sha256'][:10]}{info['ext']}"
        relative = Path('media/originals') / (_slug(ip['name'])+'-'+ip['uid'][:8]) / person_part / name
        dst = _checked_path(relative.as_posix())
        if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest() != info['sha256']:
            raise ValueError('自动命名发现文件冲突，未覆盖任何原图，请重新导入。')
        if src != dst:
            copies.append((src, dst))
        asset['file'] = relative.as_posix()
        asset['sha256'] = info['sha256']
        asset['dimensions'] = [info['width'],info['height']]
        asset['bytes'] = info['bytes']
    errors = catalog.validate(data, False)
    if errors:
        raise ValueError('\n'.join(errors))
    for key,group in (('defaultIp','ips'),('defaultAsset','assets'),('avatarAsset','assets')):
        if data['settings'].get(key) and data['settings'][key] not in {r['id'] for r in data[group]}:
            raise ValueError('默认主题或头像已失效，请重新选择。')
    # New copies are recoverable orphans if the process stops before the atomic swap.
    for src,dst in copies:
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = dst.with_name(dst.name+'.tmp-'+uuid.uuid4().hex[:8])
            shutil.copyfile(src,temp)
            os.replace(temp,dst)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup = ROOT / '.studio/backups' / f'library-{stamp}.json'
    backup.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(_catalog(),backup)
    temp = _catalog().with_suffix('.json.tmp')
    catalog.save(temp,data)
    os.replace(temp,_catalog())
    # The catalog and all originals are committed now. Optional cache/retirement
    # failures must never report that this successful save was rolled back.
    warnings = []
    # Cache paths follow regenerated IDs as well; the builder rechecks hash+appearance.
    for old,new in maps['assets'].items():
        if old == new or not old:
            continue
        for directory,suffix in (('generated/analysis','.json'),('media/derived','.display.webp'),('media/derived','.thumb.webp')):
            source = ROOT/directory/(old+suffix)
            dest = ROOT/directory/(new+suffix)
            if source.exists() and not dest.exists():
                try:
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    cache_tmp=dest.with_name(dest.name+'.tmp-'+uuid.uuid4().hex[:8])
                    shutil.copyfile(source,cache_tmp)
                    os.replace(cache_tmp,dest)
                except OSError:
                    warnings.append('内容已保存；部分预览缓存未能迁移，生成预览时会重新生成。')
    used = {a['file'] for a in data['assets']}
    for old in previous['assets']:
        if old['file'] not in used:
            source = _checked_path(old['file'])
            if source.is_file():
                target = ROOT/'.studio/retired'/stamp/old['file']
                try:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(source,target)
                except OSError:
                    warnings.append('内容已保存；部分旧原图仍被占用，已留在原处，新版本引用不受影响。')
    warnings = list(dict.fromkeys(warnings))
    try:
        status_path=ROOT/'.studio/maintenance.json'
        status_tmp=status_path.with_suffix('.json.tmp')
        catalog.save(status_tmp,{'savedAt':stamp,'warnings':warnings})
        os.replace(status_tmp,status_path)
    except OSError:
        warnings.append('内容已保存；本次维护提示未能写入本机记录。')
    return warnings


def initialize():
    with LOCK:
        data = _read()
        if data.get('studioSchema') == 1:
            return
        for ip in data['ips']:
            ip['themeKey'] = ip['id']
        if not any(ip.get('systemRole')=='owner-works' for ip in data['ips']):
            owner = next((ip for ip in data['ips'] if ip['id']=='personal-art'),None)
            if owner:
                owner['systemRole']='owner-works'
            else:
                data['ips'].append({'id':'personal-art','name':'我的作品','enabled':True,'systemRole':'owner-works','rotation':{'enabled':False,'weight':1},'tags':['本人作品']})
        data['studioSchema']=1
        data['integrations']['pixiv'].update({'mode':'manual-import','status':'manual'})
        _commit(data)


def state():
    with LOCK:
        initialize()
        data=_read()
        warnings=_configuration_warnings(data)
        status_path=ROOT/'.studio/maintenance.json'
        if status_path.exists():
            try:
                recorded=catalog.read(status_path).get('warnings',[])
                if isinstance(recorded,list):warnings.extend(x for x in recorded if isinstance(x,str))
            except (OSError,ValueError,AttributeError):
                pass
        metrics={}
        asset_index=ROOT/'generated/asset-index.json'
        if asset_index.exists():
            try:
                current={a['id']:a for a in data['assets']}
                for asset in catalog.read(asset_index):
                    saved=current.get(asset.get('id'))
                    if saved and saved.get('sha256')==asset.get('sha256'):
                        metrics[asset['id']]={k:asset[k] for k in ('dimensions','quality','sha256') if k in asset}
            except (OSError,ValueError,TypeError,AttributeError):
                metrics={}
        return {'library':data,'revision':_revision(),'meta':{
            'workspace':str(ROOT), 'previewUrl':'/index.html',
            'limits':{'imageMB':80,'pixels':MAX_PIXELS},
            'workflow':'选图或粘贴图片直链 → 填写信息 → 保存 → 生成预览 → 发布到 GitHub',
            'guidance':'名称、编号、路径与引用由工作台自动维护。原图保存在项目内，Git 记录发布版本。',
            'commands':{'open':'双击「启动素材工作台.cmd」','build':'点击「生成预览」','publish':'点击「发布到 GitHub」'},
            'warnings':list(dict.fromkeys(warnings)),
            'assetMetrics':metrics,
        }}


def _configuration_warnings(data):
    ips={ip['id']:ip for ip in data['ips']};characters={c['id']:c for c in data['characters']}
    active=[a for a in data['assets'] if a.get('enabled',True) and ips[a['ipId']].get('enabled',True)
            and all(characters[c].get('enabled',True) for c in a.get('characterIds',[]))]
    heroes=[a for a in active if 'hero' in a['uses'] and a.get('weight',1)>0
            and ips[a['ipId']].get('rotation',{}).get('enabled',True)
            and ips[a['ipId']].get('rotation',{}).get('weight',1)>0
            and all(characters[c].get('weight',1)>0 for c in a.get('characterIds',[]))]
    warnings=[];settings=data['settings']
    if not heroes:
        warnings.append('当前没有可轮播的首页图。请启用至少一个 IP，并为它启用一张“首页轮播”素材后再生成网页。')
    elif settings.get('defaultIp') and settings['defaultIp'] not in {a['ipId'] for a in heroes}:
        warnings.append('默认 IP 暂无可轮播的素材，首页会从其他可用 IP 开始；你的默认偏好仍然保留。')
    elif settings.get('defaultAsset') and not any(a['id']==settings['defaultAsset'] and (not settings.get('defaultIp') or a['ipId']==settings['defaultIp']) for a in heroes):
        warnings.append('默认首图当前不符合轮播条件，首页会使用该 IP 的其他画面；你的首图偏好仍然保留。')
    if settings.get('avatarAsset') and not any(a['id']==settings['avatarAsset'] and 'avatar' in a['uses'] for a in active):
        warnings.append('指定头像当前未启用或未勾选“头像”，页面会使用其他可用头像。')
    return warnings


def _ensure_revision(payload):
    if payload.get('revision') != _revision():
        raise RevisionConflict('素材库已在其他窗口修改。请刷新工作台，确认最新内容后再保存。')


def _clean_record(group, record):
    allowed = {
        'ips':{'name','enabled','rotation','weight','caption','series','tags','favorite','notes'},
        'characters':{'ipId','name','aliases','enabled','weight','tags','notes'},
        'assets':{'ipId','characterIds','label','description','kind','uses','tags','enabled','weight','source','appearance','notes','ownerWork'},
        'settings':{'defaultIp','defaultAsset','avatarAsset','sceneSeconds','recentWindow','noveltyBoost','homeGallerySize'},
    }
    if group not in allowed or not isinstance(record,dict):
        raise ValueError('不支持的编辑类型。')
    result = {k:deepcopy(v) for k,v in record.items() if k in allowed[group]}
    def number(value, lower, upper, label, integer=False):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not lower<=value<=upper or not math.isfinite(value) or (integer and not float(value).is_integer()):
            raise ValueError(f'{label}需要是 {lower}–{upper} 范围内的'+('整数。' if integer else '数值。'))
    for field in ('name','label'):
        if field in result and not isinstance(result[field],str):
            raise ValueError('名称和图片标题需要填写文字。')
    for field in ('ipId','defaultIp','defaultAsset','avatarAsset'):
        if field in result and not isinstance(result[field],str):
            raise ValueError('请从列表中选择有效的 IP 或图片。')
    if 'weight' in result:number(result['weight'],0,100,'抽取权重')
    for field in ('tags','aliases','series','characterIds','uses'):
        if field in result:
            if not isinstance(result[field],list) or len(result[field]) > 150 or any(not isinstance(v,str) or len(v)>500 for v in result[field]):
                raise ValueError('标签、角色和分类需要使用列表填写。')
            result[field] = list(dict.fromkeys(v.strip() for v in result[field] if v.strip()))
    for field in ('notes','caption','favorite'):
        if field in result and (not isinstance(result[field],str) or len(result[field])>10000):
            raise ValueError('备注太长或内容不正确。')
    if 'description' in result and (not isinstance(result['description'],str) or len(result['description'])>50000):
        raise ValueError('作品简介需要是 50000 字以内的纯文本。')
    for field in ('source','appearance','rotation'):
        if field in result and not isinstance(result[field],dict):
            raise ValueError('图片显示或来源配置不正确。')
    for field in ('enabled','ownerWork'):
        if field in result and not isinstance(result[field],bool):
            raise ValueError('开关值无效。')
    if 'rotation' in result:
        result['rotation']={k:v for k,v in result['rotation'].items() if k in {'enabled','weight'}}
        rotation=result['rotation']
        if 'enabled' in rotation and not isinstance(rotation['enabled'],bool):raise ValueError('轮播开关值无效。')
        if 'weight' in rotation:number(rotation['weight'],0,100,'IP 轮播权重')
    if 'appearance' in result:
        result['appearance']={k:v for k,v in result['appearance'].items() if k in {'fit','effect','accent','focalPoint','motion','overlay'}}
        appearance=result['appearance']
        for field in ('fit','effect','accent'):
            if field in appearance and not isinstance(appearance[field],str):raise ValueError('显示方式和配色需要使用页面提供的选项。')
        for field in ('motion','overlay'):
            if field in appearance:number(appearance[field],0,1,'动效或遮罩强度')
        if 'focalPoint' in appearance:
            if not isinstance(appearance['focalPoint'],list) or len(appearance['focalPoint'])!=2:raise ValueError('图片焦点需要两个坐标。')
            for value in appearance['focalPoint']:number(value,0,100,'图片焦点')
    if 'source' in result:
        source = result['source']
        result['source']={k:v for k,v in source.items() if k in {'page','image','kind','rights','evidence'}}
        if any(not isinstance(v,str) or len(v)>5000 for v in result['source'].values()):
            raise ValueError('来源信息无效。')
    if group=='settings':
        for field,low,high,label,integer in [('sceneSeconds',4,120,'轮播间隔',False),('recentWindow',0,128,'近期记忆数量',True),('noveltyBoost',0,5,'新鲜感补偿',False),('homeGallerySize',1,48,'首页画廊数量',True)]:
            if field in result:number(result[field],low,high,label,integer)
    return result


def save_record(payload):
    with LOCK:
        initialize();_ensure_revision(payload)
        data=_read();group=payload.get('group');record=_clean_record(group,payload.get('record'))
        uid=None
        if group=='settings':
            data['settings'].update(record)
        else:
            existing=next((r for r in data[group] if r['id']==payload.get('id')),None)
            if payload.get('id') and existing is None:
                raise ValueError('条目已不存在，请刷新后再试。')
            if not existing:
                if group=='assets':
                    raise ValueError('请通过导入图片创建素材。')
                existing={'id':'new-'+uuid.uuid4().hex,'uid':uuid.uuid4().hex,'enabled':True,'weight':1}
                if group=='ips': existing.update({'rotation':{'enabled':False,'weight':1},'tags':[]})
                else: existing.update({'aliases':[],'tags':[]})
                data[group].append(existing)
            for nested in ('source','rotation'):
                if nested in record:record[nested]={**existing.get(nested,{}),**record[nested]}
            existing.update(record);uid=existing['uid']
        warnings=_commit(data)
        result=state();warnings=list(dict.fromkeys(result['meta']['warnings']+warnings))
        return {**result,'savedUid':uid,'warnings':warnings,'message':'已保存；编号、文件名与相关引用已统一更新。点击生成预览查看结果。'}


def stage_upload(content, filename):
    with LOCK:
        info=_image_info(content);upload_id=uuid.uuid4().hex
        staging=ROOT/'.studio/uploads';staging.mkdir(parents=True,exist_ok=True)
        (staging/(upload_id+info['ext'])).write_bytes(content)
        name=Path(str(filename).replace('\\','/')).name[:180] or 'image'+info['ext']
        catalog.save(staging/(upload_id+'.json'),{**info,'name':name})
        return {**info,'uploadId':upload_id,'name':name}


def _staged(upload_id):
    if not isinstance(upload_id,str) or not re.fullmatch(r'[a-f0-9]{32}',upload_id):
        raise ValueError('图片上传标识无效，请重新选择图片。')
    meta=ROOT/'.studio/uploads'/(upload_id+'.json')
    if not meta.exists():
        raise ValueError('图片上传已失效，请重新选择图片。')
    info=catalog.read(meta)
    path=meta.with_suffix(info['ext'])
    if not path.is_file():raise ValueError('上传原图已不存在，请重新选择。')
    return path,info


def import_assets(payload):
    with LOCK:
        initialize();_ensure_revision(payload)
        data=_read();common=_clean_record('assets',payload.get('common',{}))
        uploads=payload.get('uploadIds',[])
        if not isinstance(uploads,list) or not 1<=len(uploads)<=100:
            raise ValueError('一次请选择 1–100 张图片。')
        copies={};added=[];duplicates=0
        for upload in uploads:
            path,info=_staged(upload)
            if any(a.get('sha256')==info['sha256'] and a['ipId']==common.get('ipId') and bool(a.get('ownerWork'))==bool(common.get('ownerWork')) for a in data['assets']):
                duplicates+=1;continue
            uid=uuid.uuid4().hex
            label=common.get('label','').strip() or Path(info['name']).stem
            if len(uploads)>1 and common.get('label','').strip():label+=f' · {len(added)+1}'
            asset={'id':'new-'+uid,'uid':uid,'characterIds':[],'kind':'illustration','uses':['gallery'],'tags':[],'enabled':True,'weight':1,'source':{'page':'','image':'','kind':'user_import','rights':''},'appearance':{'fit':'auto'},'notes':'',**deepcopy(common),'label':label,'file':'media/originals/pending.png','sha256':info['sha256']}
            if payload.get('downloadSources',{}).get(upload):
                asset['source']['image']=payload['downloadSources'][upload]
            data['assets'].append(asset);copies[uid]=path;added.append(uid)
        warnings=_commit(data,copies) if added else []
        result=state();warnings=list(dict.fromkeys(result['meta']['warnings']+warnings))
        return {**result,'savedUid':added[0] if added else None,'imported':len(added),'duplicates':duplicates,'warnings':warnings,
                'message':f'已导入 {len(added)} 张图片，跳过 {duplicates} 张重复原图；文件已复制到项目。'}


def replace_asset(payload):
    with LOCK:
        initialize();_ensure_revision(payload)
        data=_read();asset=next((a for a in data['assets'] if a['id']==payload.get('id')),None)
        if not asset:raise ValueError('找不到要替换的素材。')
        path,info=_staged(payload.get('uploadId'))
        warnings=_commit(data,{asset['uid']:path})
        result=state();warnings=list(dict.fromkeys(result['meta']['warnings']+warnings))
        return {**result,'savedUid':asset['uid'],'warnings':warnings,'message':'原图已替换；引用已保留。生成预览后将重新分析颜色与动效。'}


if __name__=='__main__':
    initialize()
    s=state()
    print(json.dumps({'revision':s['revision'],'counts':{g:len(s['library'][g]) for g in ('ips','characters','assets')}},ensure_ascii=False))
