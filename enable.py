#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Enable host modules only when the MCU reports matching TFT24 firmware."""
import argparse, datetime, json, shutil
from pathlib import Path
from transport import API, DEFAULT_SOCKET

ROOT=Path(__file__).resolve().parent
INCLUDE='[include kingroon-display.cfg]'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',default=DEFAULT_SOCKET)
    parser.add_argument('--disable',action='store_true')
    parser.add_argument('--apply',action='store_true',help='Without this flag, check only')
    args=parser.parse_args()
    api=API(args.socket)
    info=api.call('info')
    status=api.call('objects/query',{'objects':{name:None for name in
        ('mcu','print_stats','extruder','heater_bed')}})['status']
    if status['print_stats']['state'] in ('printing','paused'):
        raise SystemExit('Finish or cancel the active job first')
    if any(status[n]['target'] for n in ('extruder','heater_bed')):
        raise SystemExit('Turn heaters off first')
    if not args.disable and status['mcu']['mcu_constants'].get('KINGROON_TFT24') != 1:
        raise SystemExit('MCU does not report KINGROON_TFT24=1. Flash matching firmware first.')
    cfg=Path(info['config_file'])
    tree=Path(info['klipper_path'])
    text=cfg.read_text()
    changes={}
    if args.disable:
        changes[cfg]='\n'.join(line for line in text.split('\n') if line.strip()!=INCLUDE)
    else:
        for f in (ROOT/'klippy').glob('*.py'):
            changes[tree/'klippy/extras'/f.name]=f.read_text()
        display_config=(ROOT/'config/kingroon-display.cfg').read_text()
        if status['mcu']['mcu_constants'].get('KINGROON_FSMC_MAX_PIXELS',256)>=768:
            display_config=display_config.replace('address_setup: 15','address_setup: 6').replace('data_setup: 30','data_setup: 6')
        changes[cfg.parent/'kingroon-display.cfg']=display_config
        if INCLUDE not in text:
            changes[cfg]=INCLUDE+'\n'+text
    print('Files:',*[str(p) for p in changes],sep='\n  ')
    if not args.apply:
        print('Check passed. Re-run with --apply to save and RESTART Klipper.')
        return
    backup=ROOT/'backups'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    originals={}
    try:
        for n,(p,content) in enumerate(changes.items()):
            originals[p]=p.read_bytes() if p.exists() else None
            if originals[p] is not None:
                (backup/('%02d-%s'%(n,p.name))).write_bytes(originals[p])
            tmp=p.with_name(p.name+'.kingroon-tmp')
            tmp.write_text(content)
            tmp.replace(p)
    except Exception:
        for p,original in originals.items():
            if original is None:
                if p.exists(): p.unlink()
            else: p.write_bytes(original)
        raise
    (backup/'files.json').write_text(json.dumps([str(p) for p in changes],indent=2))
    print('Saved. Backup:',backup)
    try:
        api.call('gcode/script',{'script':'RESTART'})
    except (OSError,RuntimeError):
        # A restart normally disconnects API clients.
        pass
    print('RESTART requested; check Fluidd status and fsmc_display/info.')

if __name__=='__main__': main()
