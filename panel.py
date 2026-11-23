#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact portrait UI, drawn directly through Klipper, no X/GTK needed."""
import argparse, base64, functools, json, logging, math, queue, statistics, threading, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageChops
from transport import API, DEFAULT_SOCKET, encode_rle, rgb565, print_command

W, H = 240, 320
BG, CARD, FG, MUTED, GREEN, RED = '#101924','#213146','#edf3fc','#a8b9cb','#3ad2a0','#f56c78'

@functools.lru_cache(maxsize=8)
def font(size):
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                 'C:/Windows/Fonts/arial.ttf'):
        try:
            return ImageFont.truetype(path,size)
        except OSError:
            pass
    raise RuntimeError('Install fonts-dejavu-core for Cyrillic UI text')

def fit(draw, text, width, f):
    text = str(text)
    while text and draw.textlength(text, font=f)>width:
        text = text[:-1]
    return text

from interface import UI,ESTOP
from controls import read_capabilities,read_state,ip_addresses,CommandRunner

def status_query(api,caps=None):
    if caps is not None:return read_state(api,caps)
    return api.call('objects/query',{'objects':{
        name:None for name in ('webhooks','print_stats','extruder',
                              'heater_bed','virtual_sdcard')}})['status']

def touch_point(raw,cal):
    x,y = (raw['y'],raw['x']) if cal['swap_xy'] else (raw['x'],raw['y'])
    return (min(W-1,max(0,round(cal['x'][0]*x+cal['x'][1]))),
            min(H-1,max(0,round(cal['y'][0]*y+cal['y'][1]))))

class Display:
    def __init__(self,api):
        self.api,self.previous,self.epoch = api,None,None
    def draw(self,image):
        current=image.convert('RGB')
        info=self.api.call('fsmc_display/info')
        if not info['ready']:
            raise RuntimeError(info.get('error') or 'Display not initialized')
        if self.epoch != info['epoch']:
            self.previous=None
        batch_limit=min(2048,max(512,int(info.get('batch_bytes',512))))
        delta=ImageChops.difference(current,self.previous) if self.previous is not None else None
        # Crop each changed band to its changed pixel bounds. In
        # particular, temperature digits do not resend the whole row.
        pending=[]
        pending_bytes=0
        def flush():
            nonlocal pending,pending_bytes
            if pending:
                self.api.call('fsmc_display/draw_rle',{'rects':pending})
                pending,pending_bytes=[],0
        for y in range(0,H,16):
            x0,x1,y0,y1=0,W-1,y,min(H,y+16)-1
            if delta is not None:
                box=delta.crop((0,y,W,y1+1)).getbbox()
                if box is None:continue
                x0,x1,y0,y1=box[0],box[2]-1,y+box[1],y+box[3]-1
            pixels=rgb565(current.crop((x0,y0,x1+1,y1+1)))
            raw=encode_rle(pixels)
            if pending_bytes+len(raw)>batch_limit:
                flush()
            pending.append([x0,y0,x1,y1,base64.b64encode(raw).decode()])
            pending_bytes+=len(raw)
            if pending_bytes>=batch_limit:
                flush()
        flush()
        self.previous,self.epoch=current,info['epoch']

def calibrate(api,display,path):
    targets=[(25,25),(214,25),(214,294),(25,294),(120,160)]
    samples=[]
    def wait_release():
        end=time.monotonic()+45
        while time.monotonic()<end:
            if not api.call('fsmc_touch/read')['pressed']:
                return
            time.sleep(.05)
        raise RuntimeError('Touch stuck pressed')
    for index,(x,y) in enumerate(targets,1):
        wait_release()
        image=Image.new('RGB',(W,H),BG)
        d=ImageDraw.Draw(image)
        d.line((x-12,y,x+12,y),fill='red',width=3)
        d.line((x,y-12,x,y+12),fill='red',width=3)
        display.draw(image)
        print('CALIBRATION_TARGET %d/5 at %d,%d' % (index,x,y),flush=True)
        end=time.monotonic()+180
        raw=[]
        while time.monotonic()<end:
            reading=api.call('fsmc_touch/read')
            if reading['pressed']:
                raw.append(reading)
            elif len(raw)>=5:
                break
            else:
                raw=[]
            time.sleep(.04)
        if len(raw)<5:
            raise RuntimeError('No stable touch detected')
        raw=raw[2:]
        samples.append((x,y,statistics.median(r['x'] for r in raw),statistics.median(r['y'] for r in raw)))
        print('CALIBRATION_CAPTURED %d/5' % index,flush=True)
    def fitline(raw,screen):
        mr,ms=statistics.mean(raw),statistics.mean(screen)
        var=sum((r-mr)**2 for r in raw)
        if var<100:
            raise RuntimeError('Touch calibration has no axis range')
        a=sum((r-mr)*(s-ms) for r,s in zip(raw,screen))/var
        b=ms-a*mr
        error=sum((a*r+b-s)**2 for r,s in zip(raw,screen))
        return [a,b],error
    candidates=[]
    for swap in (False,True):
        cx,ex=fitline([p[3 if swap else 2] for p in samples],[p[0] for p in samples])
        cy,ey=fitline([p[2 if swap else 3] for p in samples],[p[1] for p in samples])
        candidates.append((ex+ey,{'swap_xy':swap,'x':cx,'y':cy}))
    error,cal=min(candidates,key=lambda pair:pair[0])
    if math.sqrt(error/(2*len(samples)))>12:
        raise RuntimeError('Calibration error too large; retry')
    cal.update(width=W,height=H)
    Path(path).write_text(json.dumps(cal,indent=2))
    print('Touch calibration saved:',path)

def touch_worker(path,cal,events,stop,ui):
    api=API(path)
    points=[]
    generation=None
    blocked=False
    while not stop.is_set():
        try:
            r=api.call('fsmc_touch/read')
            point=touch_point(r,cal)
            is_stop=ESTOP[0]<=point[0]<=ESTOP[2] and ESTOP[1]<=point[1]<=ESTOP[3]
            if ui.visible_generation != ui.generation and not is_stop:
                points=[]
                generation=None
                blocked=True
            elif blocked and not is_stop:
                blocked=r['pressed']
            elif r['pressed']:
                if not points:
                    generation=ui.generation
                points=(points+[touch_point(r,cal)])[-20:]
            elif len(points)>=2 and (generation==ui.generation or is_stop):
                events.put((round(statistics.median(p[0] for p in points)),
                            round(statistics.median(p[1] for p in points)),generation))
                points=[]
            else:
                points=[]
            stop.wait(.05)
        except (OSError,RuntimeError):
            points=[]
            stop.wait(2)
    api.close()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--socket',default=DEFAULT_SOCKET)
    ap.add_argument('--gcodes',default=str(Path.home()/'printer_data/gcodes'))
    ap.add_argument('--cal',default=str(Path(__file__).with_name('touch-cal.json')))
    ap.add_argument('--calibrate',action='store_true')
    ap.add_argument('--preview',help='Save sample UI PNG; no printer access')
    args=ap.parse_args()
    ui=UI(args.gcodes)
    if args.preview:
        ui.addresses=['192.0.2.10']
        ui.render({'webhooks':{'state':'ready'},'print_stats':{'state':'printing',
            'filename':'Kingroon_test.gcode','print_duration':1320},
            'extruder':{'temperature':199.7,'target':200},
            'heater_bed':{'temperature':59.8,'target':60},
            'virtual_sdcard':{'progress':.42}}).save(args.preview)
        return
    api=API(args.socket)
    display=Display(api)
    if args.calibrate:
        calibrate(api,display,args.cal)
        return
    cal=json.loads(Path(args.cal).read_text())
    if (cal.get('width'),cal.get('height')) != (W,H):
        raise RuntimeError('Calibrate this portrait panel first')
    caps=read_capabilities(api)
    ui.caps=caps
    runner=CommandRunner(args.socket,caps)
    events,stop=queue.Queue(),threading.Event()
    pending_event=None;last_ip=-100;was_ready=True
    touch_thread=threading.Thread(target=touch_worker,args=(args.socket,cal,events,stop,ui),daemon=True)
    touch_thread.start()
    def emergency():
        estop_api=API(args.socket,timeout=5)
        try:estop_api.call('emergency_stop')
        except Exception as exc:logging.error('Emergency stop: %s',exc)
        finally:estop_api.close()
    try:
        while True:
            try:
                status=status_query(api,caps)
                ready=status.get('webhooks',{}).get('state')=='ready'
                if ready and not was_ready:
                    caps=read_capabilities(api);ui.caps=caps;runner.caps=caps
                was_ready=ready
                if time.monotonic()-last_ip>30:
                    ui.addresses=ip_addresses();last_ip=time.monotonic()
                while not runner.results.empty():
                    request,error=runner.results.get_nowait()
                    if error:ui.error=error
                    else:
                        ui.notice('Готово')
                        if request.get('after'):ui.page=request['after']
                ui.busy=runner.busy;ui.busy_label=runner.label;ui.progress=runner.progress
                while pending_event is not None or not events.empty():
                    event=pending_event if pending_event is not None else events.get_nowait()
                    pending_event=None;x,y,generation=event
                    is_stop=ESTOP[0]<=x<=ESTOP[2] and ESTOP[1]<=y<=ESTOP[3]
                    if generation!=ui.generation and not is_stop:continue
                    action=('estop',) if is_stop else ui.tap(x,y)
                    if not action:continue
                    if action[0]=='estop':
                        threading.Thread(target=emergency,daemon=True).start()
                        ui.confirm=None;ui.notice('Аварийная остановка')
                    else:
                        request=ui.activate(action,status)
                        if request and request['action'][0]=='touch_calibration':
                            from controls import build_command
                            if runner.busy:ui.notice('Дождитесь завершения команды')
                            else:
                                stopped=False
                                try:
                                    build_command(request['action'],status_query(api,caps),caps)
                                    stop.set();stopped=True;touch_thread.join(timeout=3)
                                    calibrate(api,display,args.cal)
                                    cal=json.loads(Path(args.cal).read_text())
                                    ui.notice('Калибровка сохранена')
                                except (OSError,ValueError,RuntimeError) as exc:ui.error=str(exc)
                                finally:
                                    if stopped:
                                        stop=threading.Event()
                                        while not events.empty():events.get_nowait()
                                        pending_event=None;display.previous=None;ui.page='screen'
                                        touch_thread=threading.Thread(target=touch_worker,args=(args.socket,cal,events,stop,ui),daemon=True)
                                        touch_thread.start()
                        elif request and not runner.submit(request):ui.notice('Дождитесь завершения команды')
                    break
                ui.busy=runner.busy;ui.busy_label=runner.label;ui.progress=runner.progress
                display.draw(ui.render(status))
                ui.visible_generation=ui.generation
                try:pending_event=events.get(timeout=.40)
                except queue.Empty:pending_event=None
            except (OSError,RuntimeError,ValueError,KeyError) as exc:
                logging.warning('Display: %s',exc)
                api.close();display.previous=None
                ui.confirm=None;ui.buttons=[];pending_event=None;was_ready=False
                while not events.empty():events.get_nowait()
                stop.wait(1)
    finally:
        stop.set();api.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    main()
