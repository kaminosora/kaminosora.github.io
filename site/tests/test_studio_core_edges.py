"""Failure-path coverage for the local editor's content transactions."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import library
import studio_core as core
import test_studio_core as fixtures


class StudioEdgeTests(unittest.TestCase):
    setUp=fixtures.StudioCoreTests.setUp
    tearDown=fixtures.StudioCoreTests.tearDown
    payload=fixtures.StudioCoreTests.payload
    upload=fixtures.StudioCoreTests.upload

    def test_truncated_jpeg_is_rejected_before_it_enters_the_library(self):
        output=io.BytesIO();Image.new('RGB',(80,80),'blue').save(output,'JPEG')
        damaged=output.getvalue()[:-20]
        with Image.open(io.BytesIO(damaged)) as header_only:
            header_only.verify()  # The old header-only check accepts this file.
        before=core.state()['revision']
        with self.assertRaisesRegex(ValueError,'无法读取图片'):
            core.stage_upload(damaged,'original.jpg')
        self.assertEqual(core.state()['revision'],before)

    def test_bad_numeric_and_boolean_configuration_never_saves(self):
        state=core.state();revision=state['revision'];asset=state['library']['assets'][0];ip=state['library']['ips'][0]
        cases=[('settings',None,{'homeGallerySize':'12'}),('settings',None,{'homeGallerySize':49}),
               ('settings',None,{'recentWindow':1.5}),('settings',None,{'recentWindow':True}),
               ('settings',None,{'noveltyBoost':float('nan')}),('settings',None,{'sceneSeconds':float('inf')}),
               ('settings',None,{'sceneSeconds':10**400}),
               ('ips',ip['id'],{'rotation':{'enabled':'false'}}),
               ('ips',ip['id'],{'rotation':{'weight':True}}),
               ('assets',asset['id'],{'appearance':{'motion':True}}),
               ('assets',asset['id'],{'appearance':{'focalPoint':[False,40]}}),
               ('assets',asset['id'],{'label':12})]
        for group,record_id,record in cases:
            with self.subTest(record=record),self.assertRaises(ValueError):
                core.save_record({'revision':revision,'group':group,'id':record_id,'record':record})
            self.assertEqual(core.state()['revision'],revision)

    def test_partial_source_rotation_preserved_and_auto_appearance_resets_overrides(self):
        state=core.state();ip=state['library']['ips'][0]
        changed=core.save_record(self.payload('ips',{'rotation':{'weight':3}},ip['id']))
        self.assertEqual(changed['library']['ips'][0]['rotation'],{'enabled':True,'weight':3})
        asset=changed['library']['assets'][0]
        core.save_record(self.payload('assets',{'source':{'page':'https://example.com/old','kind':'official','evidence':'原始记录'},
                                               'appearance':{'fit':'cover','accent':'#112233','motion':.4}},asset['id']))
        result=core.save_record(self.payload('assets',{'source':{'page':'https://example.com/new'},'appearance':{'fit':'auto'}},asset['id']))
        new_asset=result['library']['assets'][0]
        self.assertEqual(new_asset['source']['kind'],'official')
        self.assertEqual(new_asset['source']['evidence'],'原始记录')
        self.assertEqual(new_asset['appearance'],{'fit':'auto'})

    def test_failure_before_catalog_swap_keeps_previous_catalog_and_original(self):
        before=core.state();asset=before['library']['assets'][0];real_replace=core.os.replace
        def fail_catalog(source,target):
            if Path(target)==self.root/'content/library.json':raise PermissionError('locked catalog')
            return real_replace(source,target)
        with patch.object(core.os,'replace',side_effect=fail_catalog),self.assertRaises(PermissionError):
            core.save_record(self.payload('ips',{'name':'原子重命名'},before['library']['ips'][0]['id']))
        self.assertEqual(core.state()['revision'],before['revision'])
        self.assertTrue((self.root/asset['file']).is_file())

    def test_post_commit_cache_failure_reports_saved_with_warning(self):
        before=core.state();asset=before['library']['assets'][0]
        cache=self.root/'generated/analysis'/f"{asset['id']}.json"
        cache.parent.mkdir(parents=True);cache.write_text('{}',encoding='utf-8')
        real_copy=core.shutil.copyfile
        def fail_cache(source,target,*args,**kwargs):
            if Path(source)==cache:raise PermissionError('cache locked')
            return real_copy(source,target,*args,**kwargs)
        with patch.object(core.shutil,'copyfile',side_effect=fail_cache):
            result=core.save_record(self.payload('ips',{'name':'缓存迁移测试'},before['library']['ips'][0]['id']))
        self.assertNotEqual(result['revision'],before['revision'])
        self.assertTrue(any('缓存' in warning for warning in result['warnings']))
        self.assertTrue(any('缓存' in warning for warning in core.state()['meta']['warnings']))
        self.assertTrue((self.root/result['library']['assets'][0]['file']).is_file())

    def test_post_commit_retirement_failure_preserves_both_originals(self):
        before=core.state();asset=before['library']['assets'][0];old_path=self.root/asset['file'];real_replace=core.os.replace
        canonical_old=old_path.resolve();retired_root=(self.root/'.studio/retired').resolve();injected=[]
        def fail_retirement(source,target):
            # Windows runners may expose TMP with an 8.3 alias (RUNNER~1),
            # while local_file() canonicalizes the same file to its long name.
            if Path(source).resolve()==canonical_old and Path(target).resolve().is_relative_to(retired_root):
                injected.append((source,target))
                raise PermissionError('opened image')
            return real_replace(source,target)
        with patch.object(core.os,'replace',side_effect=fail_retirement):
            result=core.save_record(self.payload('assets',{'label':'原图占用测试'},asset['id']))
        self.assertEqual(len(injected),1,'The retirement failure must actually be injected.')
        new_path=self.root/result['library']['assets'][0]['file']
        self.assertNotEqual(new_path,old_path)
        self.assertEqual(new_path.read_bytes(),old_path.read_bytes())
        self.assertTrue(any('旧原图' in warning for warning in result['warnings']))

    def test_duplicate_identity_is_rejected_without_ambiguous_remapping(self):
        before=core.state();data=copy.deepcopy(before['library'])
        data['ips'][1]['uid']=data['ips'][0]['uid']
        with core.LOCK,self.assertRaisesRegex(ValueError,'内部身份'):
            core._commit(data)
        self.assertEqual(core.state()['revision'],before['revision'])

    def test_disabling_default_preserves_preference_and_explains_next_action(self):
        before=core.state();settings=copy.deepcopy(before['library']['settings']);ip=before['library']['ips'][0]
        result=core.save_record(self.payload('ips',{'rotation':{'enabled':False}},ip['id']))
        self.assertEqual(result['library']['settings'],settings)
        self.assertTrue(any('没有可轮播' in text for text in result['meta']['warnings']))

    def test_metrics_only_reuse_current_image_hash(self):
        before=core.state();asset=before['library']['assets'][0]
        library.save(self.root/'generated/asset-index.json',[{'id':asset['id'],'sha256':asset['sha256'],'dimensions':[40,60],'quality':.7}])
        self.assertEqual(core.state()['meta']['assetMetrics'][asset['id']]['quality'],.7)
        uploaded=self.upload('yellow')
        replaced=core.replace_asset({'revision':before['revision'],'id':asset['id'],'uploadId':uploaded['uploadId']})
        self.assertNotIn(asset['id'],replaced['meta']['assetMetrics'])

    def test_project_lock_serializes_other_process_and_is_reentrant(self):
        marker=self.root/'acquired.txt'
        module_dir=Path(core.__file__).resolve().parent
        script=("import sys\nfrom pathlib import Path\n"
                "sys.path.insert(0,sys.argv[1])\nimport studio_core as core\n"
                "core.ROOT=Path(sys.argv[2])\nprint('waiting',flush=True)\n"
                "with core.LOCK:\n    Path(sys.argv[3]).write_text('acquired')\n")
        flags={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
        process=None
        try:
            with core.LOCK:
                with core.LOCK:
                    self.assertEqual(core.state()['library']['settings']['sceneSeconds'],12)
                process=subprocess.Popen([sys.executable,'-X','utf8','-c',script,str(module_dir),str(self.root),str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,**flags)
                self.assertEqual(process.stdout.readline().strip(),'waiting')
                time.sleep(.15)
                self.assertFalse(marker.exists())
            stdout,stderr=process.communicate(timeout=8)
            self.assertEqual(process.returncode,0,stderr)
            self.assertEqual(marker.read_text(),'acquired')
        finally:
            if process and process.poll() is None:
                process.kill();process.communicate()


if __name__=='__main__':unittest.main()
