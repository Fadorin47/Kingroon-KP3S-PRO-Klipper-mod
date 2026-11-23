#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build DMA-enabled 500k / 1M variants in a new staging directory."""
import argparse,hashlib,json,os,shutil,subprocess
from pathlib import Path
from prepare import patch_tree
ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--klipper',type=Path,default=Path.home()/'klipper')
    p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();dest=a.destination.resolve();live=a.klipper.resolve()
    if dest.exists() or live==dest or live in dest.parents:
        raise SystemExit('Destination must be new and outside live Klipper')
    cfg=(live/'.config').read_text()
    if not all(s in cfg for s in ('CONFIG_MACH_STM32F103=y',
            'CONFIG_STM32_SERIAL_USART3=y','CONFIG_STM32_FLASH_START_7000=y')):
        raise SystemExit('Unexpected MCU / transport / bootloader')
    if 'CONFIG_SERIAL_BAUD=250000' not in cfg.splitlines():
        raise SystemExit('Expected a baseline build configuration at 250000 baud')
    tree=dest/'klipper';tree.mkdir(parents=True)
    for name in ('src','klippy','scripts'):
        shutil.copytree(live/name,tree/name,
            ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('Makefile','.config','COPYING'):
        shutil.copy2(live/name,tree/name)
    (tree/'lib').symlink_to(live/'lib',target_is_directory=True)
    patch_tree(tree)
    shutil.copy2(ROOT/'firmware/fast/serial.c',tree/'src/stm32/serial.c')
    shutil.copy2(ROOT/'firmware/fast/fsmc_lcd.c',tree/'src/stm32/kingroon_fsmc_lcd.c')
    kconfig=tree/'src/stm32/Kconfig'
    text=kconfig.read_text()
    if 'config STM32_SERIAL_RX_DMA\n' not in text:
        kconfig.write_text(text+'''
config STM32_SERIAL_RX_DMA
    bool "USART3 receive DMA for Kingroon native display"
    depends on MACH_STM32F1 && STM32_SERIAL_USART3
    default n
''')
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    for baud in (500000,1000000):
        out=dest/str(baud);out.mkdir()
        config=cfg.replace('CONFIG_SERIAL_BAUD=250000',f'CONFIG_SERIAL_BAUD={baud}')
        config+='\nCONFIG_WANT_KINGROON_FSMC_LCD=y\nCONFIG_STM32_SERIAL_RX_DMA=y\n'
        path=out/'build.config';path.write_text(config)
        make=['make','-C',str(tree),'OUT='+str(out)+'/',
              'KCONFIG_CONFIG='+str(path)]
        subprocess.run(make+['olddefconfig'],env=env,check=True)
        subprocess.run(make+['-j1'],env=env,check=True)
        assert (out/'klipper.bin').stat().st_size < 65536
        data=json.loads((out/'klipper.dict').read_text())
        assert data['config']['KINGROON_FSMC_MAX_PIXELS']==768
        assert data['config']['SERIAL_BAUD']==baud
        assert 'get_serial_errors' in data['commands']
        subprocess.run(['python3',str(tree/'scripts/update_mks_robin.py'),
            str(out/'klipper.bin'),str(out/'Robin_nano.bin')],check=True)
        print('BUILT',baud,flush=True)
    checks={str(f.relative_to(dest)):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in dest.rglob('*.bin')}
    (dest/'SHA256SUMS.json').write_text(json.dumps(checks,indent=2))
    (dest/'manifest.json').write_text(json.dumps({
        'base_commit':subprocess.check_output(['git','-C',str(live),'rev-parse','HEAD'],text=True).strip(),
        'dma_source':'Aregus393/klipper e1836310fbebd004434cb40d6e2ef4353a0df080',
        'status':'built; hardware test pending'},indent=2))
    print('FAST_BUILDS_COMPLETE',flush=True)

if __name__=='__main__':main()
