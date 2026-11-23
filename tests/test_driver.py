import base64, contextlib, sys, types, unittest
from unittest import mock
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Only mcu.get_printer_mcu is used by the constructor, which these tests
# deliberately bypass; no real Klipper process or hardware is accessed.
sys.modules.setdefault('mcu',types.ModuleType('mcu'))
from klippy.fsmc_display import FSMCDisplay

class Request:
    error=RuntimeError
    def __init__(self,rects): self.rects=rects
    def get(self,name): return self.rects
    def send(self,response): self.response=response

def display(controller):
    d=FSMCDisplay.__new__(FSMCDisplay)
    d.calls=[]
    d.controller='auto'
    d.detected_controller=controller
    d.readings={'04':[0,0,0,0]}
    d.probe=lambda: d.readings
    d.printer=types.SimpleNamespace(command_error=RuntimeError)
    d.draw_mutex=contextlib.nullcontext()
    d.reset_pin='reset'; d.backlight_pin='light'
    d._set_pin=lambda *v:d.calls.append(('pin',v))
    d._pause=lambda delay:d.calls.append(('pause',delay))
    d.send_cmd=lambda *v:d.calls.append(('cmd',v))
    d.wait_idle=lambda:d.calls.append(('sync',))
    d.init_epoch=0; d.madctl=8
    d.rle_max_pixels=256
    return d

class Driver(unittest.TestCase):
    def test_info_is_safe_before_mcu_configuration(self):
        config=mock.MagicMock()
        config.get.side_effect=lambda key,default=None: {'madctl':'0x00'}.get(key,default)
        config.getint.side_effect=lambda key,default=None,**kw: {'width':240}.get(key,default)
        config.getchoice.return_value='auto'
        config.getboolean.return_value=False
        with mock.patch('klippy.fsmc_display.mcu.get_printer_mcu',create=True):
            d=FSMCDisplay(config)
        req=Request(None);d._handle_info(req)
        self.assertFalse(req.response['ready'])
        self.assertEqual(req.response['batch_bytes'],512)
        self.assertEqual(req.response['max_pixels'],256)
    def test_unknown_id_does_not_reset_or_initialize(self):
        d=display(None)
        with self.assertRaises(RuntimeError): d.init_display()
        self.assertEqual(d.calls,[])
        self.assertFalse(d.panel_ready)
    def test_profile_dispatch_and_delays(self):
        for profile in ('st7789v','ili9341'):
            d=display(profile)
            d.init_display()
            self.assertTrue(d.panel_ready)
            self.assertEqual(d.init_epoch,1)
            commands=[v[1][0] for v in d.calls if v[0]=='cmd']
            self.assertIn(0x36,commands)
            self.assertIn(0x3a,commands)
            self.assertEqual(0xcb in commands,profile=='ili9341')
            # All panel delays follow a queue flush, not an enqueued command.
            for i,call in enumerate(d.calls):
                if call[0]=='pause' and i>2:
                    self.assertEqual(d.calls[i-1],('sync',))
    def test_whole_batch_is_validated_before_writes(self):
        d=display('st7789v')
        d.is_ready=d.panel_ready=True
        d.width,d.height=240,320
        d.draw_rle=lambda *v:d.calls.append(('draw',v))
        one=base64.b64encode(b'\x80\x00\x00').decode()
        req=Request([[0,0,0,0,one],[0,1,1,1,one]])
        with self.assertLogs(level='ERROR'):
            with self.assertRaises(RuntimeError):d._handle_draw_rle(req)
        self.assertEqual(d.calls,[])

if __name__=='__main__':unittest.main()
