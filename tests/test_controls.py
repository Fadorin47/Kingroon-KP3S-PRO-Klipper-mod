import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from controls import build_command,FAVORITES
from interface import UI

class Controls(unittest.TestCase):
    def setUp(self):
        self.caps={'commands':{n for n,_,_ in FAVORITES}|{'BED_MESH_CALIBRATE'},
                   'limits':{'extruder':304,'heater_bed':114}}
        self.state={'webhooks':{'state':'ready'},'print_stats':{'state':'standby'},
            'extruder':{'can_extrude':False},'pause_resume':{'is_paused':False},
            'toolhead':{'homed_axes':'xyz','position':[180,100,1,0],
                        'axis_minimum':[-30,0,-5,0],'axis_maximum':[181,212,175,0]}}
    def test_heater_limits_and_invalid_numbers(self):
        self.assertIn('TARGET=240',build_command(('heater','extruder',240),self.state,self.caps))
        for action in (('heater','extruder',305),('heater','heater_bed',115),
                       ('heater','extruder',float('nan')),('heater','other',200)):
            with self.assertRaises(ValueError):build_command(action,self.state,self.caps)
    def test_jog_preserves_modes_and_checks_planned_limits(self):
        cmd=build_command(('jog','X',1),self.state,self.caps)
        self.assertIn('SAVE_GCODE_STATE',cmd);self.assertIn('RESTORE_GCODE_STATE',cmd)
        self.assertIn('G1 X1 F1800',cmd);self.assertTrue(cmd.endswith('M400'))
        for action in (('jog','X',10),('jog','Z',-10)):
            with self.assertRaises(ValueError):build_command(action,self.state,self.caps)
        self.state['toolhead']['homed_axes']=''
        with self.assertRaises(ValueError):build_command(('jog','Y',1),self.state,self.caps)
    def test_motion_and_calibration_are_blocked_during_print(self):
        self.state['print_stats']['state']='printing'
        for action in (('home','XYZ'),('jog','Y',1),('macro','COLDPULL'),('mesh',),('probe',),('screws',)):
            with self.assertRaises(ValueError):build_command(action,self.state,self.caps)
        self.assertEqual(build_command(('fan',50),self.state,self.caps),'M106 S128')
        self.assertEqual(build_command(('speed',80),self.state,self.caps),'M220 S80')
    def test_macro_allowlist_and_filament_state_temperature_requirements(self):
        with self.assertRaises(ValueError):build_command(('macro','M600'),self.state,self.caps)
        with self.assertRaises(ValueError):build_command(('macro','LOAD'),self.state,self.caps)
        self.state['pause_resume']['is_paused']=True
        with self.assertRaises(ValueError):build_command(('macro','LOAD'),self.state,self.caps)
        self.state['extruder']['can_extrude']=True
        for name in ('LOAD','UNLOAD'):
            for job,paused,sd_active,allowed in (('standby',False,False,True),('complete',False,False,True),
                    ('paused',True,False,True),('printing',False,True,False),('printing',True,True,False)):
                self.state['print_stats']['state']=job
                self.state['pause_resume']['is_paused']=paused
                self.state['virtual_sdcard']={'is_active':sd_active}
                if allowed:self.assertEqual(build_command(('macro',name),self.state,self.caps),name)
                else:
                    with self.assertRaises(ValueError):build_command(('macro',name),self.state,self.caps)
    def test_probe_steps_require_active_calibration(self):
        with self.assertRaises(ValueError):build_command(('testz',-.01),self.state,self.caps)
        self.state['manual_probe']={'is_active':True}
        self.assertEqual(build_command(('testz',-.01),self.state,self.caps),'TESTZ Z=-0.01')
        with self.assertRaises(ValueError):build_command(('testz',-1),self.state,self.caps)
        with self.assertRaises(ValueError):build_command(('save',),self.state,self.caps)
    def test_screws_only_home_when_xyz_position_is_unknown(self):
        self.assertEqual(build_command(('screws',),self.state,self.caps),'SCREWS_TILT_CALCULATE')
        for homed in ('','x','xy','xz','yz'):
            self.state['toolhead']['homed_axes']=homed
            self.assertEqual(build_command(('screws',),self.state,self.caps),'G28\nSCREWS_TILT_CALCULATE')
        self.state['toolhead']['homed_axes']='xyz'
        self.assertEqual(build_command(('screws',),self.state,self.caps),'SCREWS_TILT_CALCULATE')
    def test_manual_extrusion_is_hot_idle_or_paused_and_preserves_modes(self):
        for job,paused,hot,active,allowed in (('standby',False,True,False,True),
                ('paused',True,True,False,True),('printing',False,True,True,False),
                ('standby',False,False,False,False),('paused',True,False,False,False)):
            self.state['print_stats']['state']=job;self.state['pause_resume']['is_paused']=paused
            self.state['extruder']['can_extrude']=hot;self.state['virtual_sdcard']={'is_active':active}
            if not allowed:
                with self.assertRaises(ValueError):build_command(('extrude',20,5),self.state,self.caps)
            else:
                command=build_command(('extrude',-20,5),self.state,self.caps)
                self.assertIn('M83\nG1 E-20 F300',command)
                self.assertIn('M220 S100\nM221 S100',command)
                self.assertIn('RESTORE_GCODE_STATE NAME=KR_UI_EXTRUDE MOVE=0',command)
                self.assertTrue(command.endswith('M400'))
        self.state['print_stats']['state']='standby';self.state['extruder']['can_extrude']=True
        for action in (('extrude',101,5),('extrude',20,0),('extrude',20,50),('extrude',float('nan'),5)):
            with self.assertRaises(ValueError):build_command(action,self.state,self.caps)
    def test_host_calibration_and_custom_macros_are_state_guarded(self):
        self.assertIsNone(build_command(('touch_calibration',),self.state,self.caps))
        self.caps['user_macros']={'CUSTOM_CLEAN':'Clean'};self.caps['commands'].add('CUSTOM_CLEAN')
        self.assertEqual(build_command(('macro','CUSTOM_CLEAN'),self.state,self.caps),'CUSTOM_CLEAN')
        self.state['print_stats']['state']='printing'
        for action in (('touch_calibration',),('macro','CUSTOM_CLEAN')):
            with self.assertRaises(ValueError):build_command(action,self.state,self.caps)
    def test_editor_does_not_send_until_apply_and_clamps_values(self):
        ui=UI('.');ui.caps=self.caps
        self.assertIsNone(ui.activate(('edit','extruder'),self.state))
        self.assertIsNone(ui.activate(('value',999),self.state))
        self.assertEqual(ui.editor['value'],304)
        request=ui.activate(('apply',),self.state)
        self.assertEqual(request['action'],('heater','extruder',304))

if __name__=='__main__':unittest.main()
