# SPDX-License-Identifier: GPL-3.0-or-later
"""Printer commands validated against fresh state before execution."""
import json,math,queue,re,socket,subprocess,threading,time
from transport import API,print_command

# Inner detent ring measured from the user's STEP: 70 grooves, 360/70 degrees.
KNOB_CLICKS_PER_TURN=70

def screw_clicks(value,results,caps):
    """Round raw measured height difference once, without minute quantization."""
    if value.get('is_base'):return 0
    base=next((v for v in results.values() if v.get('is_base')),None)
    pitch=caps.get('screw_pitch')
    if base is None or not pitch:return None
    try:
        delta=abs(float(base['z'])-float(value['z']))
        if not math.isfinite(delta):return None
        return math.floor(delta/float(pitch)*KNOB_CLICKS_PER_TURN+.5+1e-9)
    except (KeyError,TypeError,ValueError):return None

FAVORITES=[('COLDPULL','Cold pull','idle'),('SCREWS_TILT_CALCULATE','Винты стола','idle'),
           ('PROBE_CALIBRATE','Z-offset','idle'),('LOAD','Загрузка нити','idle_or_paused_hot'),
           ('UNLOAD','Выгрузка нити','idle_or_paused_hot'),('PURGE_FILAMENT','Продавить нить','idle_or_paused_hot')]
BASE_OBJECTS=('webhooks','print_stats','extruder','heater_bed','virtual_sdcard',
              'toolhead','fan','gcode_move','pause_resume','manual_probe','screws_tilt_adjust','display_status')

def read_capabilities(api):
    objects=set(api.call('objects/list')['objects'])
    commands=set(api.call('gcode/help'))
    settings=api.call('objects/query',{'objects':{'configfile':['settings']}})['status']['configfile']['settings']
    limits={n:float(settings.get(n,{}).get('max_temp',v))-1 for n,v in
            (('extruder',250),('heater_bed',110))}
    thread=settings.get('screws_tilt_adjust',{}).get('screw_thread','CW-M3')
    pitch={'M3':.5,'M4':.7,'M5':.8,'M6':1.0}.get(str(thread).upper().rsplit('-',1)[-1])
    hidden={'LOAD','UNLOAD','PURGE_FILAMENT','LOAD_FILAMENT','UNLOAD_FILAMENT','M600','M900','PAUSE','RESUME',
        'CANCEL_PRINT','PRINT_START','PRINT_END','START_PRINT','END_PRINT','BED_MESH','LOW_TEMP_CHECK_T','SETUP_KAMP_MESHING'}
    user_macros={}
    for section,options in settings.items():
        if not section.startswith('gcode_macro '):continue
        name=section.split(' ',1)[1].upper()
        if name in hidden or name.startswith('_') or 'rename_existing' in options or name not in commands:continue
        user_macros[name]=str(options.get('description',name))
    extruder=settings.get('extruder',{})
    return {'objects':objects,'commands':commands,'limits':limits,'screw_pitch':pitch,
        'user_macros':user_macros,'extrude_distance':min(100,float(extruder.get('max_extrude_only_distance',50))),
        'extrude_speed':min(10,float(extruder.get('max_extrude_only_velocity',10)))}

def read_state(api,caps):
    info=api.call('info')
    if info['state']!='ready':return {'webhooks':{'state':info['state'],'state_message':info['state_message']}}
    objects={n:None for n in BASE_OBJECTS if n in caps.get('objects',BASE_OBJECTS)}
    if 'configfile' in caps.get('objects',()):objects['configfile']=['save_config_pending']
    return api.call('objects/query',{'objects':objects})['status']

def ip_addresses():
    try:
        records=json.loads(subprocess.check_output(['ip','-j','-4','addr','show','up'],timeout=2))
        return [a['local'] for r in records for a in r.get('addr_info',[])
                if a.get('scope')=='global' and not r['ifname'].startswith(('docker','veth','br-'))]
    except (OSError,ValueError,subprocess.SubprocessError):return []

def number(value,low,high):
    value=float(value)
    if not math.isfinite(value) or not low<=value<=high:raise ValueError('Значение вне допустимого диапазона')
    return value

def require_idle(state):
    if state.get('print_stats',{}).get('state') in ('printing','paused'):
        raise ValueError('Недоступно во время печати или паузы')

def build_command(action,state,caps):
    """Returns G-code only; does not send anything or change the printer."""
    kind=action[0]
    if kind=='restart':
        require_idle(state);return 'RESTART'
    if state.get('webhooks',{}).get('state')!='ready':raise ValueError('Klipper не готов')
    active=state.get('manual_probe',{}).get('is_active',False)
    if kind=='job_tune':
        _,filename,setting,value=action
        job=state.get('print_stats',{})
        if (job.get('filename')!=filename or job.get('state') not in ('printing','paused')
                or (job.get('print_duration',0)<.5 and job.get('filament_used',0)<=0)):
            raise ValueError('Настройка доступна после начала текущей печати')
        if setting in ('extruder','heater_bed'):
            return build_command(('heater',setting,value),state,caps)
        if setting in ('speed','flow','fan'):
            return build_command((setting,value),state,caps)
        raise ValueError('Неизвестный параметр печати')
    if kind=='touch_calibration':
        require_idle(state)
        if active:raise ValueError('Сначала завершите Z-калибровку')
        return None
    if kind=='heater':
        heater=action[1]
        if heater not in ('extruder','heater_bed'):raise ValueError('Неизвестный нагреватель')
        value=number(action[2],0,caps['limits'][heater])
        return 'SET_HEATER_TEMPERATURE HEATER=%s TARGET=%g'%(heater,value)
    if kind=='cool':return 'TURN_OFF_HEATERS'
    if kind=='preset':
        require_idle(state)
        nozzle=number(action[1],0,caps['limits']['extruder']);bed=number(action[2],0,caps['limits']['heater_bed'])
        return 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=%g\nSET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=%g'%(nozzle,bed)
    if kind=='fan':return 'M106 S%d'%round(number(action[1],0,100)*255/100)
    if kind=='speed':return 'M220 S%g'%number(action[1],25,150)
    if kind=='flow':return 'M221 S%g'%number(action[1],50,150)
    if kind=='extrude':
        if active:raise ValueError('Сначала завершите Z-калибровку')
        paused=state.get('pause_resume',{}).get('is_paused',False)
        if state.get('virtual_sdcard',{}).get('is_active',False) or (state.get('print_stats',{}).get('state')=='printing' and not paused):
            raise ValueError('Сначала поставьте печать на паузу')
        if not state.get('extruder',{}).get('can_extrude',False):raise ValueError('Сопло недостаточно нагрето')
        limit=caps.get('extrude_distance',50)
        length=number(action[1],-limit,limit)
        speed=number(action[2],.5,caps.get('extrude_speed',10))
        return ('SAVE_GCODE_STATE NAME=KR_UI_EXTRUDE\nM220 S100\nM221 S100\nM83\n'
            'G1 E%g F%g\nRESTORE_GCODE_STATE NAME=KR_UI_EXTRUDE MOVE=0\nM400')%(length,speed*60)
    if kind=='jog':
        require_idle(state)
        if active:raise ValueError('Сначала завершите Z-калибровку')
        axis=str(action[1]).lower()
        if axis not in ('x','y','z'):raise ValueError('Неизвестная ось')
        delta=number(action[2],-10,10)
        head=state.get('toolhead',{})
        if axis not in head.get('homed_axes',''):raise ValueError('Сначала выполните парковку оси')
        i='xyz'.index(axis);target=head['position'][i]+delta
        low=head['axis_minimum'][i];high=head['axis_maximum'][i]
        if axis=='z':low=max(0,low)
        if not low-1e-6<=target<=high+1e-6:raise ValueError('Достигнут предел хода оси')
        feed=300 if axis=='z' else 1800
        return 'SAVE_GCODE_STATE NAME=KR_UI_JOG\nG91\nG1 %s%g F%d\nRESTORE_GCODE_STATE NAME=KR_UI_JOG MOVE=0\nM400'%(axis.upper(),delta,feed)
    if kind=='home':
        require_idle(state)
        if active:raise ValueError('Сначала завершите Z-калибровку')
        axis=action[1]
        if axis not in ('X','Y','Z','XYZ'):raise ValueError('Неизвестная ось')
        return 'G28' if axis=='XYZ' else 'G28 '+axis
    if kind=='macro':
        name=action[1]
        favorite={n:mode for n,_,mode in FAVORITES}
        favorite.update({n:'idle' for n in caps.get('user_macros',{})})
        if name not in favorite or name not in caps.get('commands',()):raise ValueError('Макрос недоступен')
        if active:raise ValueError('Сначала завершите Z-калибровку')
        if favorite[name]=='idle':require_idle(state)
        else:
            paused=state.get('pause_resume',{}).get('is_paused',False)
            if state.get('virtual_sdcard',{}).get('is_active',False) or (state.get('print_stats',{}).get('state')=='printing' and not paused):
                raise ValueError('Сначала поставьте печать на паузу')
            if not state.get('extruder',{}).get('can_extrude',False):raise ValueError('Сопло недостаточно нагрето')
        return name
    if kind=='mesh':
        require_idle(state)
        if active:raise ValueError('Сначала завершите Z-калибровку')
        if 'BED_MESH_CALIBRATE' not in caps.get('commands',()):raise ValueError('Карта стола недоступна')
        return 'G28\nBED_MESH_CALIBRATE'
    if kind=='screws':
        require_idle(state)
        if active:raise ValueError('Сначала завершите Z-калибровку')
        if 'SCREWS_TILT_CALCULATE' not in caps.get('commands',()):raise ValueError('Калибровка винтов недоступна')
        homed=state.get('toolhead',{}).get('homed_axes','')
        return ('' if all(axis in homed for axis in 'xyz') else 'G28\n')+'SCREWS_TILT_CALCULATE'
    if kind=='probe':
        require_idle(state)
        if active:raise ValueError('Z-калибровка уже выполняется')
        if 'PROBE_CALIBRATE' not in caps.get('commands',()):raise ValueError('Z-калибровка недоступна')
        return 'PROBE_CALIBRATE'
    if kind=='testz':
        require_idle(state)
        if not active:raise ValueError('Z-калибровка не запущена')
        return 'TESTZ Z=%g'%number(action[1],-.1,.1)
    if kind in ('accept','abort'):
        require_idle(state)
        if not active:raise ValueError('Нет активной Z-калибровки')
        return 'ACCEPT' if kind=='accept' else 'ABORT'
    if kind=='save':
        require_idle(state)
        if active:raise ValueError('Сначала завершите Z-калибровку')
        if not state.get('configfile',{}).get('save_config_pending',False):raise ValueError('Нет изменений для сохранения')
        return 'SAVE_CONFIG'
    if kind=='file':
        require_idle(state)
        if active:raise ValueError('Сначала завершите Z-калибровку')
        return print_command(action[1])
    if kind in ('pause','resume','cancel'):
        job=state.get('print_stats',{}).get('state')
        if kind=='pause' and job!='printing':raise ValueError('Печать не выполняется')
        if kind=='resume' and job!='paused':raise ValueError('Печать не на паузе')
        if kind=='cancel' and job not in ('printing','paused'):raise ValueError('Нет активной печати')
        return {'pause':'PAUSE','resume':'RESUME','cancel':'CANCEL_PRINT'}[kind]
    raise ValueError('Неизвестное действие')

class CommandRunner:
    def __init__(self,path,caps):
        self.path,self.caps=path,caps;self.requests=queue.Queue();self.results=queue.Queue()
        self.busy=False;self.label='';self.progress='';self.started=0
        threading.Thread(target=self._run,daemon=True).start()
        threading.Thread(target=self._output,daemon=True).start()
    def submit(self,request):
        if self.busy:return False
        self.busy=True;self.label=request['label'];self.progress='';self.started=time.monotonic()
        self.requests.put(request);return True
    def _run(self):
        while True:
            request=self.requests.get();api=API(self.path,timeout=1200)
            try:
                command=build_command(request['action'],read_state(api,self.caps),self.caps)
                if command is None:raise ValueError('Это действие выполняется службой экрана')
                try:api.call('gcode/script',{'script':command})
                except OSError:
                    if request['action'][0] not in ('restart','save'):raise
                self.results.put((request,None))
            except Exception as exc:self.results.put((request,str(exc)))
            finally:api.close();self.busy=False
    def _output(self):
        while True:
            api=API(self.path,timeout=2)
            try:
                api.call('gcode/subscribe_output',{'response_template':{'event':'output'}})
                while True:
                    while b'\x03' not in api.buf:
                        try:part=api.sock.recv(65536)
                        except socket.timeout:continue
                        if not part:raise OSError('Disconnected')
                        api.buf+=part
                    raw,api.buf=api.buf.split(b'\x03',1);msg=json.loads(raw)
                    response=msg.get('params',{}).get('response','')
                    if self.busy and ('ColdPull' in response or 'Шаг ' in response):
                        clean=re.sub(r'^//\s*','',response)
                        self.progress=re.sub(r'^ColdPull[: ]*','',clean)[:180]
            except Exception:time.sleep(2)
            finally:api.close()
