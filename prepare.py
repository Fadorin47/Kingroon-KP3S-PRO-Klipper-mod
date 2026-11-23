#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build in a separate checkout. Does not flash, restart, or edit live config."""
import argparse, hashlib, json, os, shutil, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
MARKER='config WANT_KINGROON_FSMC_LCD'

def patch_tree(tree):
    tree=Path(tree)
    kconfig=tree/'src/stm32/Kconfig'
    text=kconfig.read_text()
    if MARKER not in text:
        text+='''
# Experimental Kingroon TFT24 native display (GPLv3).
config WANT_KINGROON_FSMC_LCD
    bool "Kingroon TFT24 FSMC display (experimental)"
    depends on MACH_STM32F103
    default n
'''
        kconfig.write_text(text)
    makefile=tree/'src/stm32/Makefile'
    rule='src-$(CONFIG_WANT_KINGROON_FSMC_LCD) += stm32/kingroon_fsmc_lcd.c'
    text=makefile.read_text()
    if rule not in text:
        makefile.write_text(text+'\n'+rule+'\n')
    shutil.copy2(ROOT/'firmware/fsmc_lcd.c',tree/'src/stm32/kingroon_fsmc_lcd.c')
    for f in (ROOT/'klippy').glob('*.py'):
        shutil.copy2(f,tree/'klippy/extras'/f.name)

def run(args,**kwargs):
    subprocess.run([str(a) for a in args],check=True,**kwargs)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--klipper',type=Path,default=Path.home()/'klipper')
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--build',action='store_true')
    args=p.parse_args()
    source=args.klipper.resolve()
    target=args.destination.resolve()
    if target.exists() or source==target or source in target.parents:
        raise SystemExit('Destination must be new and outside live Klipper')
    cfg=(source/'.config').read_text()
    if 'CONFIG_MACH_STM32F103=y' not in cfg:
        raise SystemExit('Only the F103-compatible MCU build is supported')
    if 'CONFIG_STM32_FLASH_START_7000=y' not in cfg:
        raise SystemExit('Expected existing 28 KiB MKS bootloader')
    if 'CONFIG_STM32_SERIAL_USART3=y' not in cfg:
        raise SystemExit('Expected existing USART3 PB11/PB10 connection')
    if 'CONFIG_SERIAL_BAUD=250000' not in cfg:
        raise SystemExit('Expected existing 250000 baud; do not change transport')
    target.mkdir(parents=True)
    # Preserve the exact files used by the current MCU if available.
    backup=target/'original'
    backup.mkdir()
    for name in ('.config','out/klipper.bin','out/klipper.dict'):
        src=source/name
        if src.is_file():
            shutil.copy2(src,backup/src.name)
    configdir=Path.home()/'printer_data/config'
    if configdir.is_dir():
        shutil.copytree(configdir,backup/'printer-config',ignore_dangling_symlinks=True)
    commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    tree=target/'klipper'
    tree.mkdir()
    # A full Klipper checkout contains hundreds of MB of other MCU SDKs.
    # Copy only authoring files; reuse read-only SDKs via a directory link.
    for name in ('src','klippy','scripts'):
        shutil.copytree(source/name,tree/name,
            ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('Makefile','.config','COPYING'):
        shutil.copy2(source/name,tree/name)
    (tree/'lib').symlink_to(source/'lib',target_is_directory=True)
    (target/'manifest.json').write_text(json.dumps({'source_commit':commit,
        'source_path':str(source),'status':'experimental; not hardware tested',
        'transport':'USART3 PB11/PB10, USB serial bridge, 250000 baud',
        'application_address':'0x08007000'},indent=2))
    def build(out,config):
        out.mkdir()
        cfgpath=out/'build.config'
        cfgpath.write_text(config)
        common=['make','-C',tree,'KCONFIG_CONFIG='+str(cfgpath),'OUT='+str(out)+'/']
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        run(common+['olddefconfig'],env=env)
        run(common+['-j2'],env=env)
        # Klipper's F103 linker config reserves 64 KiB for the application
        # starting at 0x08007000. Keep that original budget unchanged.
        if (out/'klipper.bin').stat().st_size > 64*1024:
            raise SystemExit('Firmware exceeds the original linker flash budget')
        run(['python3',tree/'scripts/update_mks_robin.py',out/'klipper.bin',out/'Robin_nano.bin'])
    if args.build:
        build(target/'baseline',cfg)
    patch_tree(tree)
    cfg+='\nCONFIG_WANT_KINGROON_FSMC_LCD=y\n'
    (target/'display.config').write_text(cfg)
    if args.build:
        build(target/'display',cfg)
    checksums={str(f.relative_to(target)):hashlib.sha256(f.read_bytes()).hexdigest()
        for f in target.rglob('*.bin')}
    (target/'SHA256SUMS.json').write_text(json.dumps(checksums,indent=2))
    print('Prepared:',target)
    print('Live Klipper and printer.cfg were not changed. Nothing was flashed.')

if __name__=='__main__':
    main()
