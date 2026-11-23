import sys,tempfile,unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from controls import build_command
from interface import UI


class JobPanel(unittest.TestCase):
    def test_preparation_and_live_job_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Path(directory)/'panel-settings.json'
            ui=UI(directory,settings)
            job={'filename':'sample.gcode','state':'printing','print_duration':0,'filament_used':0}
            status={'print_stats':job,'webhooks':{'state':'ready'},'fan':{'speed':.7},
                    'extruder':{'temperature':208,'target':210}}
            ui.render(status)
            self.assertIsNone(ui.tap(190,169))
            self.assertIsNone(ui.tap(40,229))
            job.update(print_duration=20,filament_used=10)
            ui.render(status)
            self.assertEqual(ui.tap(200,170),('edit','fan'))
            self.assertEqual(ui.tap(40,229),('edit','extruder'))
            ui.activate(ui.tap(200,170),status)
            self.assertEqual(ui.editor['value'],70)
            ui.activate(('adjust',5),status)
            request=ui.activate(('apply',),status)
            self.assertEqual(build_command(request['action'],status,ui.caps),'M106 S191')
            self.assertFalse(settings.exists())
            job['state']='complete'
            with self.assertRaises(ValueError):
                build_command(request['action'],status,ui.caps)

    def test_unchanged_settings_are_not_written(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Path(directory)/'panel-settings.json'
            ui=UI(directory,settings)
            ui.save_settings()
            self.assertFalse(settings.exists())
            ui.settings['auto_job']=False
            ui.save_settings()
            previous=settings.stat().st_mtime_ns
            ui.save_settings()
            self.assertEqual(settings.stat().st_mtime_ns,previous)
