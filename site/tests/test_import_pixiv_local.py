import copy
import hashlib
import html
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import import_pixiv_local as importer
import studio_core as core
import test_studio_core as fixtures


class PixivLocalImportTests(unittest.TestCase):
    def setUp(self):
        fixtures.StudioCoreTests.setUp(self)
        core.state()

    tearDown = fixtures.StudioCoreTests.tearDown
    upload = fixtures.StudioCoreTests.upload

    def manifest(self, count=2, identifier='123456'):
        files = []
        for page, color in enumerate(['blue', 'green', 'orange'][:count]):
            upload = self.upload(color, f'{identifier}_p{page}.png')
            files.append({**upload, 'artworkId':identifier, 'pageIndex':page})
        return {'files':files}

    def metadata(self, **changes):
        return [{'id':'123456', 'title':'「作品」：第１话／星空？',
                 'description':'第一行\n第二行 <原样文本>', 'tags':['原创', '星空'],
                 'authorId':importer.OWNER_ID, 'pageCount':2, **changes}]

    def owners(self):
        return sorted([a for a in core._read()['assets'] if a.get('ownerWork')], key=lambda a:a['pageIndex'])

    def test_pending_is_explicit_and_preview_has_no_library_writes(self):
        manifest = self.manifest()
        before = (self.root/'content/library.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '缺少原标题'):
            importer.import_manifest(manifest)
        with patch.object(core, '_commit', wraps=core._commit) as commit:
            result = importer.import_manifest(manifest, allow_pending=True, dry_run=True)
            commit.assert_not_called()
        self.assertEqual(result['images'], 2)
        self.assertEqual(result['works'], 1)
        self.assertEqual(result['pendingWorks'], ['123456'])
        self.assertEqual((self.root/'content/library.json').read_bytes(), before)

    def test_batch_commits_once_and_preserves_every_original_byte(self):
        manifest = self.manifest()
        with patch.object(core, '_commit', wraps=core._commit) as commit:
            result = importer.import_manifest(manifest, allow_pending=True)
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(result['added'], 2)
        assets = self.owners()
        owner = next(i for i in core._read()['ips'] if i.get('systemRole') == 'owner-works')
        for item, asset in zip(manifest['files'], assets):
            staged, _ = core._staged(item['uploadId'])
            target = self.root/asset['file']
            self.assertEqual(target.read_bytes(), staged.read_bytes())
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), item['sha256'])
            self.assertEqual(asset['label'], '作品 #123456')
            self.assertEqual(asset['metadataStatus'], 'pending')
            self.assertEqual(asset['workId'], 'pixiv-123456')
            self.assertEqual(asset['pageCount'], 2)
            self.assertEqual(asset['ipId'], owner['id'])
            self.assertEqual(asset['source']['page'], 'https://www.pixiv.net/artworks/123456')

    def test_reimport_is_idempotent_then_metadata_updates_same_pages(self):
        manifest = self.manifest()
        importer.import_manifest(manifest, allow_pending=True)
        original = self.owners()
        before = core._revision()
        with patch.object(core, '_commit', wraps=core._commit) as commit:
            result = importer.import_manifest(manifest, allow_pending=True)
            commit.assert_not_called()
        self.assertEqual(result['unchanged'], 2)
        self.assertEqual(core._revision(), before)
        # Only one staged image is needed when filling all existing pages'
        # metadata; the other page's identity and original are retained.
        partial = {'files':[manifest['files'][0]]}
        result = importer.import_manifest(partial, self.metadata())
        self.assertEqual((result['added'], result['updated']), (0, 2))
        for before, after in zip(original, self.owners()):
            self.assertEqual(before['uid'], after['uid'])
            self.assertEqual(before['sha256'], after['sha256'])
            self.assertEqual(after['metadataStatus'], 'verified')
            self.assertEqual(after['label'], self.metadata()[0]['title'])
            self.assertEqual(after['description'], self.metadata()[0]['description'])
            self.assertEqual(after['tags'], ['原创', '星空'])

    def test_duplicate_inputs_are_deduplicated_but_conflicting_pages_rejected(self):
        manifest = self.manifest(1)
        manifest['files'].append(copy.deepcopy(manifest['files'][0]))
        result = importer.import_manifest(manifest, self.metadata(pageCount=1))
        self.assertEqual((result['added'], result['duplicateInputs']), (1, 1))
        before = core._revision()
        other = self.upload('red')
        manifest['files'].append({**other, 'artworkId':'123456', 'pageIndex':0})
        with self.assertRaisesRegex(ValueError, '不同原图'):
            importer.import_manifest(manifest, self.metadata(pageCount=1))
        self.assertEqual(before, core._revision())

    def test_missing_pages_hash_tampering_and_wrong_author_are_atomic(self):
        manifest = self.manifest()
        before = core._revision()
        with self.assertRaisesRegex(ValueError, '原图页不完整'):
            importer.import_manifest(manifest, self.metadata(pageCount=3))
        gap = copy.deepcopy(manifest)
        gap['files'][1]['pageIndex'] = 2
        with self.assertRaisesRegex(ValueError, '原图页不完整'):
            importer.import_manifest(gap, allow_pending=True)
        with self.assertRaisesRegex(ValueError, '作者编号'):
            importer.import_manifest(manifest, self.metadata(authorId='123'))
        manifest['files'][0]['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, '原图已变化'):
            importer.import_manifest(manifest, allow_pending=True)
        self.assertEqual(before, core._revision())
        self.assertEqual(self.owners(), [])

    def test_structured_saved_page_ignores_unrelated_work_and_removes_html(self):
        manifest = self.manifest()
        record = {**self.metadata()[0], 'description':'<p>第一行</p><script>secret()</script><p>第二行<br>第三行</p>',
                  'tags':{'tags':[{'tag':'原创'},{'tag':'星空'}]}}
        source = {'illust':{'123456':record,'999999':{**record,'id':'999999','authorId':'111'}}}
        saved = self.root/'saved-work.html'
        saved.write_text('<meta id="meta-preload-data" content="'+html.escape(json.dumps(source,ensure_ascii=False),quote=True)+'">',encoding='utf-8')
        result = importer.import_manifest(manifest, saved)
        self.assertEqual(result['added'], 2)
        for asset in self.owners():
            self.assertEqual(asset['description'], '第一行\n\n第二行\n第三行')
            self.assertNotIn('secret', asset['description'])
            self.assertIn('SHA256',asset['source']['evidence'])
            self.assertNotIn(str(self.root),asset['source']['evidence'])

    def test_unknown_html_and_conflicting_metadata_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '没有可识别'):
            importer.records_from_saved_html('<title>guessed title</title><meta property="og:title" content="Other title">')
        manifest = self.manifest()
        before = core._revision()
        with self.assertRaisesRegex(ValueError, '资料存在冲突'):
            importer.import_manifest(manifest, self.metadata()+self.metadata(title='不同标题'))
        with self.assertRaisesRegex(ValueError, '本批原图以外'):
            importer.import_manifest(manifest, self.metadata(id='999999'))
        self.assertEqual(before, core._revision())

    def test_json_file_and_additional_page_reuse_existing_work_metadata(self):
        manifest = self.manifest(1)
        metadata_file = self.root/'metadata.json'
        metadata_file.write_text(json.dumps({'authorId':importer.OWNER_ID, 'works':self.metadata(pageCount=1)},ensure_ascii=False),encoding='utf-8')
        importer.import_manifest(manifest, metadata_file)
        original = self.owners()[0]
        second = self.upload('green')
        result = importer.import_manifest({'files':[{**second,'artworkId':'123456','pageIndex':1}]})
        self.assertEqual(result['added'],1)
        assets = self.owners()
        self.assertEqual(assets[0]['uid'], original['uid'])
        for asset in assets:
            self.assertEqual(asset['label'], original['label'])
            self.assertEqual(asset['metadataStatus'],'verified')
            self.assertEqual(asset['pageCount'],2)

    def test_metadata_update_needs_no_staged_images_and_preserves_identity(self):
        importer.import_manifest(self.manifest(), allow_pending=True)
        before = self.owners()
        content = json.dumps(self.metadata(),ensure_ascii=False).encode('utf-8')
        with patch.object(core, '_staged', side_effect=AssertionError('Must not use staged images')):
            result = importer.update_metadata_bytes(content,'作品.json',core._revision())
        self.assertEqual(result['updatedWorks'],1)
        self.assertEqual(result['updated'],2)
        self.assertEqual(result['pendingWorks'],[])
        for old, asset in zip(before,self.owners()):
            self.assertEqual(asset['uid'],old['uid'])
            self.assertEqual(asset['sha256'],old['sha256'])
            self.assertEqual(asset['label'],self.metadata()[0]['title'])
        with patch.object(core,'_commit',wraps=core._commit) as commit:
            repeated = importer.update_metadata_bytes(content,'作品.json',core._revision())
            commit.assert_not_called()
        self.assertEqual(repeated['updated'],0)

    def test_metadata_update_rejects_stale_revision_and_unknown_work_atomically(self):
        importer.import_manifest(self.manifest(), allow_pending=True)
        revision = core._revision()
        content = json.dumps(self.metadata()).encode('utf-8')
        with self.assertRaises(core.RevisionConflict):
            importer.update_metadata_bytes(content,'works.json','stale')
        mixed = json.dumps(self.metadata()+self.metadata(id='999999')).encode('utf-8')
        with self.assertRaisesRegex(ValueError,'还没有入库'):
            importer.update_metadata_bytes(mixed,'works.json',revision)
        with self.assertRaisesRegex(ValueError,'最多|不得超过'):
            importer.update_metadata_bytes(b'x'*(importer.MAX_METADATA_BYTES+1),'works.json',revision)
        self.assertEqual(core._revision(),revision)
        self.assertTrue(all(a['metadataStatus']=='pending' for a in self.owners()))


if __name__ == '__main__':
    unittest.main()
