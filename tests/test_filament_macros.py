import configparser
from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined

CONFIG = Path(__file__).resolve().parents[1] / 'config'

class FilamentMacros(unittest.TestCase):
    def setUp(self):
        self.config = configparser.RawConfigParser()
        self.config.read([CONFIG / 'kingroon-filament-macros.cfg', CONFIG / 'kingroon-print-macros.cfg'])
        self.env = Environment('{%', '%}', '{', '}', undefined=StrictUndefined)
        self.state = dict(print_stats=dict(state='standby'), pause_resume=dict(is_paused=False),
            virtual_sdcard=dict(is_active=False), extruder=dict(can_extrude=True, target=210, temperature=210),
            manual_probe=dict(is_active=False), configfile=dict(settings=dict(extruder=dict(
                max_extrude_only_distance=101, min_extrude_temp=165, max_temp=305))),
            toolhead=dict(homed_axes='xyz', position=dict(x=90,y=50,z=173),
                axis_minimum=dict(x=-30,y=0), axis_maximum=dict(z=175)))

    def render(self, section, **params):
        def fail(message):
            raise ValueError(message)
        result=self.env.from_string(self.config[section]['gcode']).render(
            printer=self.state, params=params, action_raise_error=fail)
        return [line.strip() for line in result.splitlines() if line.strip()]

    def test_load_unload_and_purge(self):
        for mode, length, speed, expected in (
            ('LOAD',55,2,['G1 E55.0 F120.0','G1 E15 F90']),
            ('UNLOAD',75,3,['G1 E4 F90','G1 E-75.0 F180.0']),
            ('PURGE',10,1.5,['G1 E10.0 F90.0'])):
            with self.subTest(mode=mode):
                commands=self.render('gcode_macro _KR_FILAMENT', MODE=mode,LENGTH=length,SPEED=speed,TEMP=0)
                self.assertEqual([line for line in commands if line.startswith('G1')],expected)
                self.assertIn('RESTORE_GCODE_STATE NAME=KR_FILAMENT_SERVICE MOVE=0',commands)
        self.state['extruder']['can_extrude']=False
        with self.assertRaisesRegex(ValueError,'Heat the nozzle'):
            self.render('gcode_macro _KR_FILAMENT', MODE='LOAD',LENGTH=55,SPEED=2,TEMP=0)
        self.state['virtual_sdcard']['is_active']=True
        with self.assertRaisesRegex(ValueError,'Pause the print'):
            self.render('gcode_macro _KR_FILAMENT', MODE='LOAD',LENGTH=55,SPEED=2,TEMP=210)

    def test_pause_parks_once_and_skips_cold_retract(self):
        self.state['print_stats']['state']='printing'
        self.state['extruder']['can_extrude']=False
        commands=self.render('gcode_macro PAUSE',RUNOUT=1)
        self.assertEqual(commands[0],'PAUSE_BASE')
        self.assertIn('G1 Z2 F600',commands)
        self.assertFalse(any(line.startswith('G1 E') for line in commands))
        self.assertIn('SET_GCODE_VARIABLE MACRO=PAUSE VARIABLE=runout VALUE=True',commands)
        self.state['pause_resume']['is_paused']=True
        self.assertEqual(self.render('gcode_macro PAUSE'),[])

    def test_resume_checks_sensor_and_restores_cooled_target(self):
        self.state['pause_resume']['is_paused']=True
        self.state['print_stats']['state']='paused'
        self.state['extruder'].update(can_extrude=False,target=0,temperature=24)
        self.state['gcode_macro PAUSE']=dict(runout=True, cooled_target=210,retracted=1.7)
        sensor=dict(enabled=True,filament_detected=False)
        self.state['filament_switch_sensor FilamentSensor']=sensor
        with self.assertRaisesRegex(ValueError,'through the runout sensor'):
            self.render('gcode_macro RESUME')
        sensor['filament_detected']=True
        commands=self.render('gcode_macro RESUME')
        self.assertLess(commands.index('M109 S210'),commands.index('G1 E1.7 F300'))
        self.assertEqual(commands.count('RESUME_BASE'),1)

    def test_cancel_stops_heaters_fan_and_uses_base(self):
        commands=self.render('gcode_macro CANCEL_PRINT')
        self.assertIn('TURN_OFF_HEATERS',commands)
        self.assertIn('M107',commands)
        self.assertIn('CANCEL_PRINT_BASE',commands)
        self.assertNotIn('SDCARD_RESET_FILE',commands)
