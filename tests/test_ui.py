import sys,tempfile,unittest,base64
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from panel import UI,Display,W,H
from PIL import Image
from transport import rgb565
from test_protocol import decode

class ScreenAPI:
    def __init__(self):
        self.screen=[0]*(W*H);self.rects=[];self.epoch=1
    def call(self,method,params=None):
        if method=='fsmc_display/info':return {'ready':True,'epoch':self.epoch}
        assert method=='fsmc_display/draw_rle'
        for rect in params['rects']:
            x0,y0,x1,y1,encoded=rect
            px=decode(base64.b64decode(encoded))
            assert len(px)==(x1-x0+1)*(y1-y0+1)
            for y in range(y0,y1+1):
                offset=(y-y0)*(x1-x0+1)
                self.screen[y*W+x0:y*W+x1+1]=px[offset:offset+x1-x0+1]
            self.rects.append(rect)
        return {}

class TouchLayout(unittest.TestCase):
    def test_cropped_updates_reconstruct_exact_frame(self):
        api=ScreenAPI();display=Display(api)
        image=Image.new('RGB',(W,H),'black')
        display.draw(image);api.rects=[]
        image.putpixel((20,100),(255,0,0))
        image.putpixel((22,107),(0,255,0))
        display.draw(image)
        self.assertEqual(api.screen,rgb565(image))
        self.assertEqual([r[:4] for r in api.rects],[[20,100,22,107]])
        api.rects=[]
        display.draw(image)
        self.assertEqual(api.rects,[])
        api.epoch+=1;api.screen=[0]*(W*H)
        display.draw(image)
        self.assertEqual(api.screen,rgb565(image))
    def test_temperature_refresh_preserves_touch_layout(self):
        with tempfile.TemporaryDirectory() as td:
            ui=UI(td)
            status={'webhooks':{'state':'ready'},'print_stats':{'state':'standby'}}
            ui.render(status)
            gen=ui.generation
            status['extruder']={'temperature':25,'target':0}
            ui.render(status)
            self.assertEqual(ui.generation,gen)
            ui.page='axes'
            ui.render(status)
            self.assertGreater(ui.generation,gen)
    def test_changed_file_selection_invalidates_old_taps(self):
        with tempfile.TemporaryDirectory() as td:
            ui=UI(td);ui.page='files'
            status={'webhooks':{'state':'ready'},'print_stats':{'state':'standby'}}
            ui.render(status)
            gen=ui.generation
            (Path(td)/'test.gcode').write_text('; test')
            ui.files_refreshed=0
            ui.render(status)
            self.assertGreater(ui.generation,gen)
            self.assertEqual(ui.tap(50,75),('select_file','test.gcode'))
            ui.catalog.requests.join()

if __name__=='__main__':unittest.main()
