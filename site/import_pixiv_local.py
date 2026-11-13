"""Import owner-provided Pixiv originals and metadata without any network access.

The upload manifest refers only to files already staged by the local studio.
Metadata may be a simple JSON export or an HTML page saved by the user. This
module never logs in, opens a browser, downloads a page, or reads credentials.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import uuid

import studio_core as core

OWNER_ID = '39216404'
MAX_METADATA_BYTES = 8 * 1024 * 1024


def artwork_id(value):
    value = str(value)
    if not re.fullmatch(r'[1-9][0-9]{0,19}', value):
        raise ValueError('作品编号格式无效。')
    return value


class PlainDescription(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.suppressed += 1
        elif not self.suppressed and tag in {'br', 'p', 'div', 'li', 'blockquote'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed and tag in {'p', 'div', 'li', 'blockquote'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)


def plain_description(value):
    parser = PlainDescription()
    parser.feed(value)
    parser.close()
    return re.sub(r'\n{3,}', '\n\n', ''.join(parser.parts)).strip()


def normalize_record(record, *, html_description=False, author_id=None, evidence=''):
    if not isinstance(record, dict):
        raise ValueError('每条作品资料必须包含编号、原标题、简介和标签。')
    identifier = artwork_id(record.get('id', record.get('illustId', '')))
    author = record.get('userId', record.get('authorId', author_id))
    if author is not None and str(author) != OWNER_ID:
        raise ValueError(f'作品 {identifier} 的作者编号与站长不符，已停止导入。')
    for key in ('title', 'description', 'tags'):
        if key not in record:
            raise ValueError(f'作品 {identifier} 缺少 {key}；空简介或空标签也请明确提供。')
    title = record['title']
    description = record['description']
    tags = record['tags']
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        raise ValueError(f'作品 {identifier} 的原标题需要 1–120 个字符。')
    if not isinstance(description, str) or len(description) > 50000:
        raise ValueError(f'作品 {identifier} 的简介需要是 50000 字以内的文本。')
    if isinstance(tags, dict):
        tags = tags.get('tags')
    if not isinstance(tags, list) or len(tags) > 150:
        raise ValueError(f'作品 {identifier} 的标签格式不正确。')
    normalized_tags = []
    for tag in tags:
        if isinstance(tag, dict):
            tag = tag.get('tag')
        if not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 500:
            raise ValueError(f'作品 {identifier} 包含无效标签。')
        if tag.strip() not in normalized_tags:
            normalized_tags.append(tag.strip())
    result = {'id': identifier, 'title': title.strip(),
              'description': plain_description(description) if html_description else description,
              'tags': normalized_tags, 'authorId': str(author) if author is not None else None,
              'evidence': evidence or '由站长提供的离线作品资料；未自动连接或核验 Pixiv 页面。'}
    count = record.get('pageCount')
    if count is not None:
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
            raise ValueError(f'作品 {identifier} 的总页数无效。')
        result['pageCount'] = count
    date = record.get('createdAt', record.get('createDate'))
    if date:
        if not isinstance(date, str) or len(date) > 50:
            raise ValueError(f'作品 {identifier} 的发表日期格式无效。')
        try:
            datetime.fromisoformat(date.replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError(f'作品 {identifier} 的发表日期格式无效。') from exc
        result['createdAt'] = date
    return result


def records_from_document(document, *, evidence=''):
    author = None
    if isinstance(document, dict) and 'works' in document:
        author = document.get('authorId')
        records = document['works']
    elif isinstance(document, dict) and ('id' in document or 'illustId' in document):
        records = [document]
    else:
        records = document
    if not isinstance(records, list):
        raise ValueError('JSON 资料应为作品数组，或包含 works 数组的对象。')
    return [normalize_record(record, author_id=author, evidence=evidence) for record in records]


class SavedPageMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.documents = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('id') == 'meta-preload-data':
            try:
                self.documents.append(json.loads(attrs.get('content', '')))
            except json.JSONDecodeError as exc:
                raise ValueError('保存页面中的作品资料不是完整 JSON。') from exc
        if tag == 'script' and attrs.get('id') == '__NEXT_DATA__':
            self.script = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            try:
                self.documents.append(json.loads(''.join(self.script)))
            except json.JSONDecodeError as exc:
                raise ValueError('保存页面中的作品资料不是完整 JSON。') from exc
            self.script = None


def records_from_saved_html(text, *, allowed_ids=None, evidence=''):
    """Read explicit illustration dictionaries only, never execute page scripts."""
    parser = SavedPageMetadata()
    parser.feed(text)
    parser.close()
    candidates = []
    def visit(value, depth=0):
        if depth > 25:
            return
        if isinstance(value, dict):
            illustrations = value.get('illust')
            if isinstance(illustrations, dict):
                for identifier, item in illustrations.items():
                    if re.fullmatch(r'[1-9][0-9]{0,19}', str(identifier)) and isinstance(item, dict):
                        if allowed_ids is not None and str(identifier) not in allowed_ids:
                            continue
                        if item.get('id') is not None and str(item['id']) != str(identifier):
                            raise ValueError('保存页面中的作品编号互相矛盾。')
                        candidates.append(normalize_record({**item, 'id': str(identifier)}, html_description=True, evidence=evidence))
            for key, child in value.items():
                if key != 'illust' and isinstance(child, (dict, list)):
                    visit(child, depth+1)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    visit(child, depth+1)
    for document in parser.documents:
        visit(document)
    if not candidates:
        raise ValueError('这个 HTML 没有可识别的完整作品资料；请提供包含原标题、简介、标签的 JSON 或完整保存页面。')
    return candidates


def load_metadata(paths, *, allowed_ids=None):
    records = []
    for path in paths:
        path = Path(path)
        records.extend(parse_metadata(path.read_bytes(), path.name, allowed_ids=allowed_ids))
    return records


def parse_metadata(content, filename, *, allowed_ids=None):
    """Parse uploaded bytes in memory; never save or execute the input page."""
    if len(content) > MAX_METADATA_BYTES:
        raise ValueError('单份作品资料不得超过 8 MB。')
    extension = Path(filename).suffix.lower()
    digest = hashlib.sha256(content).hexdigest()
    evidence = f'由站长提供的离线 {extension.lstrip(".").upper()} 作品资料，SHA256 {digest}；未自动连接 Pixiv。'
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('作品资料需要使用 UTF-8 编码保存。') from exc
    if extension in {'.html', '.htm'}:
        return records_from_saved_html(text, allowed_ids=allowed_ids, evidence=evidence)
    if extension == '.json':
        try:
            return records_from_document(json.loads(text), evidence=evidence)
        except json.JSONDecodeError as exc:
            raise ValueError('JSON 作品资料格式不正确。') from exc
    raise ValueError('作品资料只接受本地 JSON 或 HTML 文件。')


def update_metadata_bytes(content, filename, revision, *, engine=core):
    """Update existing owner works after a clone, without staging any image."""
    with engine.LOCK:
        engine.initialize()
        engine._ensure_revision({'revision':revision})
        data = engine._read()
        works = {}
        for asset in data['assets']:
            match = re.fullmatch(r'pixiv-([1-9][0-9]{0,19})', str(asset.get('workId','')))
            if asset.get('ownerWork') and match:
                works.setdefault(match[1], []).append(asset)
        if not works:
            raise ValueError('素材库还没有带 Pixiv 作品编号的本人作品，请先导入原图。')
        records = _metadata_index(parse_metadata(content, filename, allowed_ids=set(works)))
        if not records:
            raise ValueError('所选文件没有作品资料。')
        unknown = set(records) - set(works)
        if unknown:
            raise ValueError('这些作品还没有入库为本人作品，请先导入原图：'+'、'.join(sorted(unknown)))
        updated = 0
        changed_works = set()
        for identifier, record in records.items():
            assets = works[identifier]
            if 'pageCount' in record:
                pages = [a.get('pageIndex') for a in assets]
                if len(pages) != record['pageCount'] or set(pages) != set(range(record['pageCount'])):
                    raise ValueError(f'作品 {identifier} 的已入库页数与资料不符，请先补齐原图。')
            for asset in assets:
                before = deepcopy(asset)
                asset.update(label=record['title'], originalTitle=record['title'],
                             description=record['description'], tags=record['tags'], metadataStatus='verified')
                if 'pageCount' in record:
                    asset['pageCount'] = record['pageCount']
                if record.get('createdAt'):
                    asset['createdAt'] = record['createdAt']
                asset['source'] = {**asset.get('source',{}),
                                   'page':f'https://www.pixiv.net/artworks/{identifier}',
                                   'evidence':record['evidence']}
                if before != asset:
                    updated += 1
                    changed_works.add(identifier)
        pending = sorted(identifier for identifier, assets in works.items()
                         if any(a.get('metadataStatus') != 'verified' for a in assets))
        warnings = []
        if any(record.get('authorId') is None for record in records.values()):
            warnings.append('资料未包含作者编号，按你提供的文件整理，未进行在线核验。')
        if updated:
            warnings.extend(engine._commit(data))
        return {**engine.state(), 'updated':updated, 'updatedWorks':len(changed_works),
                'matchedWorks':len(records), 'pendingWorks':pending, 'warnings':warnings,
                'message':f'已从本地资料更新 {len(changed_works)} 个作品、{updated} 张图片；还有 {len(pending)} 个 Pixiv 作品待补资料。生成预览后可查看。'}


def _metadata_index(records):
    result = {}
    for record in records:
        identifier = record['id']
        if identifier in result:
            fields = ('title', 'description', 'tags', 'pageCount', 'createdAt', 'authorId')
            if any(result[identifier].get(k) != record.get(k) for k in fields):
                raise ValueError(f'作品 {identifier} 的离线资料存在冲突，未覆盖任何记录。')
        else:
            result[identifier] = record
    return result


def import_manifest(manifest=None, metadata=None, *, allow_pending=False, dry_run=False):
    """Apply one staged batch atomically, preserving identity on repeated imports."""
    with core.LOCK:
        core.initialize()
        if manifest is None:
            manifest = core.ROOT / '.studio/pixiv-incoming.json'
        if isinstance(manifest, (str, Path)):
            manifest = core.catalog.read(manifest)
        if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list) or not manifest['files']:
            raise ValueError('没有可导入的已暂存原图。')
        if len(manifest['files']) > 10000:
            raise ValueError('暂存图片数量超出单批限制。')
        if manifest.get('authorId') is not None and str(manifest['authorId']) != OWNER_ID:
            raise ValueError('暂存原图的作者编号与站长不符。')
        allowed_ids = set()
        for incoming in manifest['files']:
            if not isinstance(incoming, dict):
                raise ValueError('暂存图片记录格式不正确。')
            allowed_ids.add(artwork_id(incoming.get('artworkId', '')))
        data = core._read()
        owner = next((item for item in data['ips'] if item.get('systemRole') == 'owner-works'), None)
        if not owner:
            raise ValueError('找不到个人作品分类，请先从素材工作台初始化素材库。')
        if metadata is None:
            records = []
        elif isinstance(metadata, (str, Path)):
            records = load_metadata([metadata], allowed_ids=allowed_ids)
        elif isinstance(metadata, list) and all(isinstance(p, (str, Path)) for p in metadata):
            records = load_metadata(metadata, allowed_ids=allowed_ids)
        else:
            records = records_from_document(metadata)
        details = _metadata_index(records)
        originals = {}
        by_work = {}
        duplicate_inputs = 0
        for incoming in manifest['files']:
            if not isinstance(incoming, dict):
                raise ValueError('暂存图片记录格式不正确。')
            identifier = artwork_id(incoming.get('artworkId', ''))
            page = incoming.get('pageIndex')
            if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < 10000:
                raise ValueError(f'作品 {identifier} 的页码无效。')
            path, staged = core._staged(incoming.get('uploadId'))
            actual = core._image_info(path.read_bytes())
            if staged.get('sha256') != actual['sha256'] or (incoming.get('sha256') and incoming['sha256'] != actual['sha256']):
                raise ValueError(f'作品 {identifier} 的暂存原图已变化，请重新选择原图。')
            key = (identifier, page)
            if key in originals:
                if originals[key][1]['sha256'] != actual['sha256']:
                    raise ValueError(f'作品 {identifier} 第 {page+1} 张出现不同原图，未覆盖任何记录。')
                duplicate_inputs += 1
                continue
            originals[key] = (path, actual)
            by_work.setdefault(identifier, set()).add(page)
        unknown = set(details) - set(by_work)
        if unknown:
            raise ValueError('离线资料含有本批原图以外的作品编号：' + '、'.join(sorted(unknown)))
        previous = {}
        for asset in data['assets']:
            match = re.fullmatch(r'pixiv-([1-9][0-9]{0,19})', str(asset.get('workId','')))
            if match:
                page = asset.get('pageIndex')
                if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < 10000:
                    raise ValueError('现有素材库包含无效作品页码，请先在工作台整理。')
                key = (match[1], asset.get('pageIndex'))
                if key in previous:
                    raise ValueError('现有素材库包含重复作品页，请先在工作台整理。')
                previous[key] = asset
        missing = []
        page_counts = {}
        for identifier, pages in by_work.items():
            # Source metadata can explicitly reveal missing local pages. Without
            # it, only the supplied local sequence is known to be complete.
            existing_pages = {key[1] for key in previous if key[0] == identifier}
            union = pages | existing_pages
            expected_count = details.get(identifier,{}).get('pageCount', max(union)+1)
            if union != set(range(expected_count)):
                raise ValueError(f'作品 {identifier} 的原图页不完整或与资料总页数不符，请补齐后导入。')
            page_counts[identifier] = expected_count
            if identifier not in details:
                if not existing_pages or any(previous[(identifier,page)].get('metadataStatus') != 'verified' for page in existing_pages):
                    missing.append(identifier)
                elif pages - existing_pages:
                    # A replacement/extension can reuse already imported work
                    # metadata, but contradictory page-level edits require an
                    # explicit work record to resolve them.
                    siblings = [previous[(identifier,page)] for page in sorted(existing_pages)]
                    fields = ('label', 'description', 'tags')
                    if any(any(a.get(k) != siblings[0].get(k) for k in fields) for a in siblings[1:]):
                        raise ValueError(f'作品 {identifier} 各页资料不同，请提供统一作品资料后再增加原图。')
                    sibling = siblings[0]
                    details[identifier] = normalize_record({
                        'id':identifier, 'title':sibling['label'],
                        'description':sibling.get('description',''), 'tags':sibling.get('tags',[]),
                        'pageCount':expected_count, 'createdAt':sibling.get('createdAt')
                    }, evidence=sibling.get('source',{}).get('evidence',''))
        if missing and not allow_pending:
            raise ValueError('这些作品缺少原标题、简介或标签资料，尚未导入：'+'、'.join(sorted(missing))+'。确认先展示原图时才使用 --allow-pending。')
        copies = {}
        added = updated = unchanged = 0
        for key, (path, info) in originals.items():
            identifier, page = key
            existing = previous.get(key)
            if existing and not existing.get('ownerWork'):
                raise ValueError('作品编号与非本人素材冲突，已停止导入。')
            if existing:
                asset = existing
                before = deepcopy(asset)
            else:
                uid = uuid.uuid4().hex
                asset = {'id':'new-'+uid, 'uid':uid, 'ipId':owner['id'], 'characterIds':[],
                         'kind':'illustration', 'uses':['gallery'], 'weight':1, 'enabled':True,
                         'tags':[], 'description':'', 'appearance':{'fit':'auto'}, 'notes':'',
                         'file':'media/originals/pending.png'}
                before = None
                data['assets'].append(asset)
            asset.update(workId='pixiv-'+identifier, pageIndex=page, pageCount=page_counts[identifier], ownerWork=True)
            if identifier in details:
                record = details[identifier]
                asset.update(label=record['title'], originalTitle=record['title'], description=record['description'],
                             tags=record['tags'], metadataStatus='verified')
                if record.get('createdAt'):
                    asset['createdAt'] = record['createdAt']
                evidence = record['evidence']
            else:
                asset.setdefault('label', '作品 #'+identifier)
                asset.setdefault('metadataStatus', 'pending')
                evidence = asset.get('source',{}).get('evidence') or '站长提供的本地原图；原标题、简介和标签尚待作品页资料补全。'
            asset['source'] = {**asset.get('source',{}), 'page':f'https://www.pixiv.net/artworks/{identifier}',
                               'kind':'owner_provided_pixiv_work', 'image':asset.get('source',{}).get('image',''),
                               'rights':'站长提供并声明为本人作品', 'evidence':evidence}
            if before is None:
                copies[asset['uid']] = path
                added += 1
            elif before.get('sha256') != info['sha256']:
                copies[asset['uid']] = path
                updated += 1
            elif asset != before:
                updated += 1
            else:
                unchanged += 1
        # A metadata update applies to every existing page of the same work,
        # including pages that need no replacement image in this batch.
        for (identifier, page), asset in previous.items():
            if (identifier,page) in originals or identifier not in by_work:
                continue
            before = deepcopy(asset)
            asset['pageCount'] = page_counts[identifier]
            if identifier in details:
                record = details[identifier]
                asset.update(label=record['title'], originalTitle=record['title'], description=record['description'], tags=record['tags'], metadataStatus='verified')
                asset.setdefault('source',{})['evidence'] = record['evidence']
                if record.get('createdAt'):
                    asset['createdAt'] = record['createdAt']
            updated += asset != before
        warnings = []
        if missing:
            warnings.append('这些作品使用中性编号标题，简介和标签待补：'+'、'.join(sorted(missing)))
        if any(record.get('authorId') is None for record in details.values()):
            warnings.append('部分离线资料没有作者编号，身份依据为站长提供的本地文件，未进行网络核验。')
        if not dry_run and (added or updated):
            warnings.extend(core._commit(data,copies))
        return {'status':'preview' if dry_run else 'success', 'works':len(by_work), 'images':len(originals),
                'added':added, 'updated':updated, 'unchanged':unchanged, 'duplicateInputs':duplicate_inputs,
                'pendingWorks':sorted(missing), 'warnings':warnings,
                'completeness':'仅核对本地提供的作品与页码，不代表已获取账号全部作品。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--metadata',type=Path,action='append',default=[])
    parser.add_argument('--allow-pending',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:
        result=import_manifest(args.manifest, args.metadata or None, allow_pending=args.allow_pending,dry_run=args.dry_run)
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,OSError,TypeError,KeyError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
