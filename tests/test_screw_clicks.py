import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from controls import screw_clicks,KNOB_CLICKS_PER_TURN

class ScrewClicks(unittest.TestCase):
    def test_m4_seventy_detents_and_rounding(self):
        self.assertEqual(KNOB_CLICKS_PER_TURN,70)
        caps={'screw_pitch':.7}
        for height,expected in ((.7,70),(.01,1),(.0049,0),(.005,1),(.015,2),(.7*2,140),(-.06,6)):
            value={'z':height,'sign':'CW','adjust':'00:00'}
            results={'screw1':{'is_base':True,'z':0},'screw2':value}
            self.assertEqual(screw_clicks(value,results,caps),expected)
            self.assertEqual(screw_clicks(results['screw1'],results,caps),0)
    def test_uses_actual_reference_and_raw_height_not_rounded_minutes(self):
        results={'screw1':{'z':1.035,'adjust':'00:03'},'screw3':{'is_base':True,'z':1}}
        self.assertEqual(screw_clicks(results['screw1'],results,{'screw_pitch':.7}),4)
        results['screw1']['adjust']='99:59'
        self.assertEqual(screw_clicks(results['screw1'],results,{'screw_pitch':.7}),4)
        self.assertIsNone(screw_clicks(results['screw1'],results,{}))
        self.assertIsNone(screw_clicks({'z':float('nan')},results,{'screw_pitch':.7}))

if __name__=='__main__':unittest.main()
