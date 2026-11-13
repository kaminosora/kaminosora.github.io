import copy
import sys
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from library import validate, local_file
from build_preview import analyze_image
import library

class LibraryTests(unittest.TestCase):
    def data(self):
        return {'schemaVersion':1,'ips':[{'id':'ip','name':'IP'}],'characters':[{'id':'char','ipId':'ip','name':'Character'}],'assets':[{'id':'art','ipId':'ip','characterIds':['char'],'label':'Art','file':'media/originals/a.png','kind':'illustration','uses':['hero'],'weight':1,'source':{'page':'https://example.com'},'appearance':{'focalPoint':[70,40]}}]}

    def test_valid_config_and_input_unchanged(self):
        data=self.data();before=copy.deepcopy(data)
        self.assertEqual(validate(data,False),[]);self.assertEqual(data,before)

    def test_invalid_references_urls_paths_are_rejected(self):
        for field,value in [('characterIds',['missing']),('file','../outside.png'),('uses',['everything'])]:
            with self.subTest(field=field):
                data=self.data();data['assets'][0][field]=value;self.assertTrue(validate(data,False))
        data=self.data();data['assets'][0]['source']['page']='javascript:alert(1)';self.assertTrue(validate(data,False))

    def test_duplicate_id_and_invalid_weights(self):
        data=self.data();data['assets'].append(copy.deepcopy(data['assets'][0]));self.assertTrue(validate(data,False))
        for weight in (-1,float('nan'),101,True):
            data=self.data();data['assets'][0]['weight']=weight;self.assertTrue(validate(data,False))

    def test_extreme_palette_still_readable(self):
        for color in ('white','black','yellow','cyan','magenta','#16fd29'):
            with self.subTest(color=color):
                result=analyze_image(Image.new('RGB',(20,20),color))
                self.assertGreaterEqual(result['accentContrast'],4.5)
                self.assertTrue(.25<=result['motion']<=.8)

    def test_auto_theme_changes_with_image(self):
        warm=analyze_image(Image.new('RGB',(20,20),'#e85028'))
        cool=analyze_image(Image.new('RGB',(20,20),'#3288c5'))
        self.assertNotEqual(warm['accent'],cool['accent']);self.assertNotEqual(warm['effect'],cool['effect'])

    def test_transparency_and_overrides(self):
        image=Image.new('RGBA',(30,50),(0,0,0,0));image.paste((220,100,150,255),(8,8,20,40))
        result=analyze_image(image,{'effect':'none','motion':0,'focalPoint':[20,30]})
        self.assertTrue(result['figure']);self.assertEqual(result['effect'],'none');self.assertEqual(result['motion'],0)
        self.assertEqual(result['focalPoint'],[20,30])

    def test_owner_import_missing_page_writes_nothing_then_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=root/'content/library.json'
            library.save(catalog,{'schemaVersion':1,'ips':[],'characters':[],'assets':[],'integrations':{'pixiv':{'userId':'39216404'}}})
            source=root/'source';source.mkdir();Image.new('RGB',(8,8),'blue').save(source/'one.png')
            manifest=source/'works.json';library.save(manifest,{'authorId':'39216404','works':[{'id':'123','title':'Test','files':['one.png','two.png']}]})
            with patch.object(library,'ROOT',root),patch.object(library,'CATALOG',catalog):
                with self.assertRaises(FileNotFoundError):library.import_works(manifest)
                self.assertFalse((root/'media').exists())
                self.assertEqual(library.read(catalog)['assets'],[])
                Image.new('RGB',(8,8),'red').save(source/'two.png')
                self.assertEqual(library.import_works(manifest),2)
                self.assertEqual(library.import_works(manifest),0)
                self.assertEqual(len(library.read(catalog)['assets']),2)
                self.assertEqual(library.validate(library.read(catalog)),[])

    def test_import_recovers_identical_orphan_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=root/'content/library.json'
            library.save(catalog,{'schemaVersion':1,'ips':[],'characters':[],'assets':[],'integrations':{'pixiv':{'userId':'39216404'}}})
            src=root/'one.png';Image.new('RGB',(8,8),'blue').save(src)
            orphan=root/'media/originals/personal-art/pixiv-123-p0.png';orphan.parent.mkdir(parents=True);orphan.write_bytes(src.read_bytes())
            manifest=root/'works.json';library.save(manifest,{'authorId':'39216404','works':[{'id':'123','title':'Test','files':['one.png']}]})
            with patch.object(library,'ROOT',root),patch.object(library,'CATALOG',catalog):
                self.assertEqual(library.import_works(manifest),1)
                self.assertTrue(orphan.exists())

if __name__=='__main__':unittest.main()
