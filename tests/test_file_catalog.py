import base64,io,sys,tempfile,unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from file_catalog import FileCatalog,print_progress,duration_seconds
from interface import UI

class GcodeFiles(unittest.TestCase):
    def test_metadata_thumbnail_and_executed_m73_offsets(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);buf=io.BytesIO();Image.new('RGB',(300,300),'red').save(buf,format='PNG')
            encoded=base64.b64encode(buf.getvalue()).decode()
            header='; thumbnail begin 300x300 %d\n; %s\n; thumbnail end\n'%(len(encoded),encoded)
            content=(header+'; estimated printing time (normal mode) = 1h 2m 3s\n; filament used [mm] = 120.5\n'
                '; filament_type = TPU\nM73 P0 R62\nG1 E1 F300\nM73 P20 R49\nG1 E20\nM73 P100 R0\n').encode()
            path=root/'test.gcode';path.write_bytes(content)
            catalog=FileCatalog(root);self.assertEqual(catalog.files(),['test.gcode'])
            catalog.get('test.gcode');catalog.requests.join();info=catalog.get('test.gcode')
            self.assertEqual(info['estimated_time'],3723);self.assertEqual(info['filament_mm'],120.5)
            self.assertEqual(info['material'],'TPU');self.assertEqual(info['thumbnail'].size,(148,148))
            self.assertLessEqual(len(info['thumbnail'].getcolors()),24)
            self.assertEqual([m[1] for m in info['markers']],[0,.2,1])
            self.assertEqual(info['markers'][1][0],content.index(b'M73 P20 R49')+len(b'M73 P20 R49\n'))
            self.assertEqual(path.read_bytes(),content)
    def test_catalog_reloads_replaced_file_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'part.gcode';path.write_text(';TIME:60\nG1 E1\n')
            catalog=FileCatalog(root);catalog.get('part.gcode');catalog.requests.join()
            self.assertEqual(catalog.get('part.gcode')['estimated_time'],60)
            path.write_text(';TIME:1200\nG1 E20\n')
            catalog.get('part.gcode');catalog.requests.join()
            self.assertEqual(catalog.get('part.gcode')['estimated_time'],1200)
            with self.assertRaises(ValueError):catalog.path('../outside.gcode')
            with self.assertRaises(ValueError):catalog.path('secret.cfg')
    def test_preparation_does_not_count_thumbnail_bytes_as_print_progress(self):
        state={'print_stats':{'state':'printing','print_duration':0,'filament_used':0},
            'virtual_sdcard':{'progress':.14,'file_position':14000,'file_size':100000}}
        result=print_progress(state,{'markers':[(12000,0.,600)],'progress_ready':True})
        self.assertTrue(result['preparing']);self.assertEqual(result['percent'],0)
        state['print_stats'].update(print_duration=20,filament_used=5)
        result=print_progress(state,{'markers':[(12000,0.,600),(20000,.2,480)],'progress_ready':True})
        self.assertEqual(result['percent'],0)
        state['virtual_sdcard']['file_position']=20000
        result=print_progress(state,{'markers':[(12000,0.,600),(20000,.2,480)],'progress_ready':True})
        self.assertEqual(result['percent'],.2);self.assertEqual(result['remaining'],480)
    def test_progress_fallback_is_labeled_and_never_uses_file_byte_fraction(self):
        state={'print_stats':{'state':'printing','print_duration':10,'filament_used':25},'virtual_sdcard':{'progress':.9}}
        result=print_progress(state,{'progress_ready':True,'filament_mm':100,'estimated_time':120})
        self.assertEqual(result['percent'],.25);self.assertEqual(result['source'],'По филаменту')
        self.assertEqual(result['remaining'],110)
        self.assertIsNone(print_progress(state,{'progress_ready':True})['percent'])
        state['print_stats']['state']='complete'
        self.assertEqual(print_progress(state,{})['percent'],1)
    def test_three_tabs_file_confirmation_and_persistent_host_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'settings.json';ui=UI(root,path)
            state={'webhooks':{'state':'ready'},'print_stats':{'state':'standby'}}
            ui.render(state)
            self.assertEqual(ui.page,'heat')
            self.assertEqual(ui.tap(30,300),('page','heat'))
            self.assertEqual(ui.tap(115,300),('page','files'))
            self.assertEqual(ui.tap(200,300),('page','service'))
            self.assertIsNone(ui.activate(('select_file','part.gcode'),state))
            self.assertEqual(ui.page,'file');self.assertIsNone(ui.confirm)
            ui.activate(('toggle_auto_job',),state)
            ui.caps['user_macros']={'TEST':'Test'};ui.activate(('favorite','TEST'),state)
            restored=UI(root,path)
            self.assertFalse(restored.settings['auto_job']);self.assertIn('TEST',restored.settings['favorites'])
            ui.page='profile_setup';ui.activate(('edit','preset_0_extruder'),state)
            ui.activate(('value',215),state);self.assertIsNone(ui.activate(('apply',),state))
            self.assertEqual(UI(root,path).settings['presets'][0][0],215)

if __name__=='__main__':unittest.main()
