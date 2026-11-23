import importlib.util, random, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'klippy'))
from transport import encode_rle, print_command
from kingroon_profiles import identify, validate_rle
from prepare import patch_tree
import tempfile

def decode(stream):
    result=[]
    while stream:
        h=stream[0]; n=(h&127)+1
        if h&128:
            result.extend([stream[1]|stream[2]<<8]*n)
            stream=stream[3:]
        else:
            result.extend(stream[1+2*i]|stream[2+2*i]<<8 for i in range(n))
            stream=stream[1+2*n:]
    return result

class Protocol(unittest.TestCase):
    def test_fast_packets_remain_compatible_with_old_pixel_limit(self):
        raw=encode_rle([0]*768)
        fast=validate_rle(raw,768,max_pixels=768)
        slow=validate_rle(raw,768,max_pixels=256)
        self.assertEqual(len(fast),1)
        self.assertEqual(len(slow),3)
        self.assertEqual(decode(b''.join(fast)),decode(b''.join(slow)))
    def test_roundtrip_and_mcu_bounds(self):
        rng=random.Random(40)
        cases=[[0]*(240*16),list(range(3840)),
               [rng.randrange(65536) for _ in range(3840)],
               [i//127 for i in range(3840)]]
        for px in cases:
            raw=encode_rle(px)
            packets=validate_rle(raw,len(px))
            self.assertEqual(decode(raw),px)
            self.assertEqual(b''.join(packets),raw)
            for packet in packets:
                self.assertLessEqual(len(packet),54)
                self.assertLessEqual(len(decode(packet)),256)
    def test_reject_malformed_before_sending(self):
        for raw,count in [(b'\x80',1),(b'\x80\x00\x00',2),
                          (b'\xff\x00\x00',1),(b'\x7f'+b'\0'*256,128)]:
            with self.assertRaises(ValueError): validate_rle(raw,count)
    def test_ids(self):
        self.assertEqual(identify({'04':[0,0x85,0x85,0x52]}),'st7789v')
        self.assertEqual(identify({'d3':[0,0,0x93,0x41]}),'ili9341')
        for raw in ([255]*4,[0]*4,[0,0,0x94,0x88]):
            self.assertIsNone(identify({'04':raw,'d3':raw}))
    def test_gcode_filename_injection(self):
        self.assertEqual(print_command('folder/test file.gcode'),
                         'SDCARD_PRINT_FILE FILENAME="folder/test file.gcode"')
        for name in ('../evil.gcode','/evil.gcode','x"\nM112.gcode',
                     'x\nM112.gcode','x\\z.gcode','evil.txt',''):
            with self.assertRaises(ValueError): print_command(name)
    def test_patch_idempotence(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'src/stm32').mkdir(parents=True)
            (root/'klippy/extras').mkdir(parents=True)
            (root/'src/stm32/Kconfig').write_text('# base\n')
            (root/'src/stm32/Makefile').write_text('# base\n')
            patch_tree(root)
            before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            patch_tree(root)
            after={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before,after)

if __name__=='__main__': unittest.main()
