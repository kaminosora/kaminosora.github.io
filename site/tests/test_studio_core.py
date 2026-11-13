import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import library
import studio_core as core


class StudioCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.patches=[patch.object(core,'ROOT',self.root),patch.object(library,'ROOT',self.root)]
        for p in self.patches:p.start()
        self.root.joinpath('media/originals').mkdir(parents=True)
        self.original=self.root/'media/originals/old.png'
        Image.new('RGB',(40,60),'#c872ae').save(self.original)
        self.digest=hashlib.sha256(self.original.read_bytes()).hexdigest()
        data={'schemaVersion':1,'settings':{'defaultIp':'extra-01','defaultAsset':'art','avatarAsset':'art','sceneSeconds':12},
              'ips':[{'id':'extra-01','name':'作品: /名字','rotation':{'enabled':True,'weight':1}}],
              'characters':[{'id':'char','ipId':'extra-01','name':'角色A'}],
              'assets':[{'id':'art','ipId':'extra-01','characterIds':['char'],'label':'第一张原图','kind':'illustration','uses':['hero','avatar'],'file':'media/originals/old.png','source':{'page':'https://example.com/art'}}],
              'integrations':{'pixiv':{'userId':'39216404'}}}
        library.save(self.root/'content/library.json',data)

    def tearDown(self):
        for p in self.patches:p.stop()
        self.temp.cleanup()

    def payload(self,group,record,id=None):
        return {'group':group,'record':record,'id':id,'revision':core.state()['revision']}

    def upload(self,color='blue',filename='somewhere/CON:wrong.jpg'):
        out=io.BytesIO();Image.new('RGB',(31,44),color).save(out,'PNG')
        return core.stage_upload(out.getvalue(),filename)

    def test_migration_preserves_original_and_initial_references(self):
        data=core.state()['library'];asset=data['assets'][0]
        self.assertTrue(all(r.get('uid') for g in ('ips','characters','assets') for r in data[g]))
        self.assertTrue(all(not r['id'].startswith('extra-') for r in data['ips']))
        self.assertEqual(data['settings']['defaultAsset'],asset['id'])
        self.assertEqual(hashlib.sha256((self.root/asset['file']).read_bytes()).hexdigest(),self.digest)
        self.assertFalse(self.original.exists())
        self.assertTrue(list((self.root/'.studio/retired').rglob('old.png')))
        self.assertEqual(library.validate(data),[])

    def test_ip_and_character_rename_cascades_but_uid_persists(self):
        before=core.state()['library'];ip=before['ips'][0];a=before['assets'][0]
        result=core.save_record(self.payload('ips',{'name':'新的名字'},ip['id']))['library']
        new_ip=result['ips'][0];new_a=result['assets'][0]
        self.assertNotEqual(new_ip['id'],ip['id']);self.assertEqual(new_ip['uid'],ip['uid'])
        self.assertNotEqual(new_a['id'],a['id']);self.assertNotEqual(new_a['file'],a['file'])
        self.assertEqual(new_a['ipId'],new_ip['id'])
        self.assertEqual(result['settings']['defaultIp'],new_ip['id'])
        self.assertEqual(result['settings']['avatarAsset'],new_a['id'])
        old_char=result['characters'][0]
        final=core.save_record(self.payload('characters',{'name':'重命名角色'},old_char['id']))['library']
        self.assertEqual(final['assets'][0]['characterIds'],[final['characters'][0]['id']])
        self.assertNotEqual(final['assets'][0]['file'],new_a['file'])
        self.assertEqual(library.validate(final),[])

    def test_stale_revision_rejected_without_writes(self):
        p=self.payload('ips',{'name':'另一个 IP'})
        core.save_record(self.payload('ips',{'name':'先保存'}))
        before=(self.root/'content/library.json').read_bytes()
        with self.assertRaises(core.RevisionConflict):core.save_record(p)
        self.assertEqual((self.root/'content/library.json').read_bytes(),before)

    def test_failed_reference_keeps_catalog_and_original(self):
        s=core.state();a=s['library']['assets'][0]
        original=(self.root/a['file']).read_bytes()
        with self.assertRaises(ValueError):core.save_record(self.payload('assets',{'characterIds':['missing']},a['id']))
        self.assertEqual(core.state()['revision'],s['revision'])
        self.assertEqual((self.root/a['file']).read_bytes(),original)

    def test_native_upload_copies_bytes_and_ignores_supplied_ids_paths(self):
        s=core.state();upload=self.upload()
        ip=s['library']['ips'][0]['id']
        result=core.import_assets({'revision':s['revision'],'common':{'ipId':ip,'label':'CON / <原图>','id':'bad','file':'../../bad.png','uses':['gallery']},'uploadIds':[upload['uploadId']]})
        a=result['library']['assets'][-1]
        self.assertTrue((self.root/a['file']).is_file())
        self.assertNotEqual(a['id'],'bad');self.assertNotIn('..',a['file'])
        self.assertEqual(a['sha256'],upload['sha256'])
        self.assertEqual(Path(a['file']).suffix,'.png')
        result=core.import_assets({'revision':result['revision'],'common':{'ipId':ip,'label':'duplicate'},'uploadIds':[upload['uploadId']]})
        self.assertEqual(result['imported'],0);self.assertEqual(result['duplicates'],1)

    def test_replace_retains_identity_and_settings(self):
        s=core.state();a=s['library']['assets'][0];upload=self.upload('green')
        result=core.replace_asset({'id':a['id'],'uploadId':upload['uploadId'],'revision':s['revision']})['library']
        changed=result['assets'][0]
        self.assertEqual(changed['uid'],a['uid']);self.assertEqual(changed['id'],a['id'])
        self.assertNotEqual(changed['file'],a['file']);self.assertEqual(changed['sha256'],upload['sha256'])
        self.assertEqual(result['settings']['defaultAsset'],changed['id'])

    def test_invalid_image_and_duplicate_name_leave_catalog_unchanged(self):
        s=core.state()
        with self.assertRaises(ValueError):core.stage_upload(b'<html>login</html>','image.png')
        with self.assertRaises(ValueError):core.save_record(self.payload('ips',{'name':s['library']['ips'][0]['name']}))
        self.assertEqual(s['revision'],core.state()['revision'])

    def test_multi_import_is_atomic_and_records_url_provenance(self):
        s=core.state();up=self.upload();ip=s['library']['ips'][0]['id']
        with self.assertRaises(ValueError):
            core.import_assets({'revision':s['revision'],'common':{'ipId':ip},'uploadIds':[up['uploadId'],'missing']})
        self.assertEqual(core.state()['revision'],s['revision'])
        result=core.import_assets({'revision':s['revision'],'common':{'ipId':ip,'ownerWork':True,'source':{'page':'https://example.com/work'}},'uploadIds':[up['uploadId']], 'downloadSources':{up['uploadId']:'https://example.com/original.png'}})
        a=result['library']['assets'][-1]
        self.assertEqual(a['source']['image'],'https://example.com/original.png');self.assertTrue(a['ownerWork'])

    def test_work_description_is_distinct_from_maintenance_notes(self):
        s=core.state();a=s['library']['assets'][0]
        result=core.save_record(self.payload('assets',{'description':'作品第一行\n作品第二行 <保留文字>','notes':'仅用于整理的备注'},a['id']))
        saved=result['library']['assets'][0]
        self.assertEqual(saved['description'],'作品第一行\n作品第二行 <保留文字>')
        self.assertEqual(saved['notes'],'仅用于整理的备注')
        with self.assertRaises(ValueError):core.save_record(self.payload('assets',{'description':{'html':'bad'}},saved['id']))


if __name__=='__main__':unittest.main()
