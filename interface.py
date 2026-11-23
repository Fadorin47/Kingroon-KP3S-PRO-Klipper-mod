# SPDX-License-Identifier: GPL-3.0-or-later
"""Portrait touchscreen pages and local UI state; no printer I/O here."""
import functools,json,math,time
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from controls import FAVORITES,build_command,screw_clicks,KNOB_CLICKS_PER_TURN
from file_catalog import FileCatalog,print_progress,time_label

W,H=240,320
BG,CARD,FG,MUTED,GREEN,RED='#101924','#213146','#edf3fc','#a8b9cb','#3ad2a0','#f56c78'
ACTIVE,DISABLED='#28594e','#172331'
ESTOP=(178,4,234,40)
@functools.lru_cache(maxsize=12)
def font(size):
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','C:/Windows/Fonts/arial.ttf'):
        try:return ImageFont.truetype(p,size)
        except OSError:pass
    raise RuntimeError('Install fonts-dejavu-core')

def clip(d,text,width,f):
    text=str(text)
    if d.textlength(text,font=f)<=width:return text
    while text and d.textlength(text+'…',font=f)>width:text=text[:-1]
    return text+'…'

@functools.lru_cache(maxsize=256)
def _wrapped(text,width,size):
    f=font(size)
    result=[]
    for paragraph in str(text).split('\n'):
        line=''
        for ch in paragraph:
            if line and f.getlength(line+ch)>width:
                result.append(line);line=''
            line+=ch
        if line:result.append(line)
    return tuple(result)

def wrap(d,text,width,f,maxlines=4):
    result=list(_wrapped(str(text),width,f.size))
    if len(result)>maxlines:
        result=result[:maxlines];result[-1]=clip(d,result[-1]+'…',width,f)
    return result

class UI:
    def __init__(self,gcode_dir,settings_path=None):
        self.page='heat';self.offset=0;self.confirm=None;self.buttons=[]
        self.gcode_dir=Path(gcode_dir);self.message='';self.last_state='standby'
        self.catalog=FileCatalog(gcode_dir);self.selected_file=''
        self.settings_path=Path(settings_path) if settings_path else Path(__file__).with_name('panel-settings.json')
        self.settings={'auto_job':True,'favorites':['COLDPULL'],'presets':[[200,60],[240,80]]}
        try:
            saved=json.loads(self.settings_path.read_text(encoding='utf-8'))
            if isinstance(saved.get('auto_job'),bool):self.settings['auto_job']=saved['auto_job']
            if isinstance(saved.get('favorites'),list):self.settings['favorites']=[n for n in saved['favorites'] if isinstance(n,str)]
            presets=saved.get('presets')
            if isinstance(presets,list) and len(presets)==2 and all(isinstance(p,list) and len(p)==2 and all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=304 for v in p) for p in presets):self.settings['presets']=presets
        except (OSError,ValueError,TypeError):pass
        self.saved_settings=json.dumps(self.settings,ensure_ascii=False,indent=2)
        self.message_until=0
        self.generation=0;self.visible_generation=-1;self.layout=None
        self.files_cache=[];self.files_refreshed=0;self.editor=None
        self.axis='X';self.step=1.;self.extrude_length=10.;self.extrude_speed=5.;self.error='';self.addresses=[]
        self.macro_offset=0
        self.busy=False;self.busy_label='';self.progress=''
        self.caps={'commands':set(),'limits':{'extruder':249,'heater_bed':109}}
    def notice(self,text):
        self.message=text;self.message_until=time.monotonic()+3
    def save_settings(self):
        content=json.dumps(self.settings,ensure_ascii=False,indent=2)
        if content==self.saved_settings:return
        try:
            temporary=self.settings_path.with_suffix('.tmp')
            temporary.write_text(content,encoding='utf-8')
            temporary.replace(self.settings_path)
            self.saved_settings=content
        except OSError:self.error='Не удалось сохранить настройки экрана'
    def allowed(self,action,status):
        if self.busy:return False
        try:build_command(action,status,self.caps);return True
        except (ValueError,KeyError,TypeError):return False
    def tuning_value(self,key,status):
        if key in ('extruder','heater_bed'):return status.get(key,{}).get('target',0)
        if key=='fan':return round(status.get('fan',{}).get('speed',0)*100)
        if key=='extrude_speed':return self.extrude_speed
        factor='speed_factor' if key=='speed' else 'extrude_factor'
        return round(status.get('gcode_move',{}).get(factor,1)*100)
    def render(self,status):
        im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im);d.fontmode='1'
        self.buttons=[]
        def text(x,y,s,size=14,color=FG,width=None):
            d.text((x,y),clip(d,s,width or W-x-8,font(size)),font=font(size),fill=color)
        def lines(x,y,s,size=14,n=4,width=216):
            for i,line in enumerate(wrap(d,s,width,font(size),n)):
                text(x,y+i*(size+4),line,size,width=width)
        def button(box,label,action,enabled=True,selected=False,color=CARD,size=14,subtitle=None):
            x0,y0,x1,y1=box
            d.rounded_rectangle(box,radius=5,fill=color if enabled else DISABLED,
                                outline=GREEN if selected else None,width=1)
            f=font(size);label=clip(d,label,x1-x0-10,f)
            y=y0+((y1-y0-size)//2-2 if subtitle is None else 7)
            d.text(((x0+x1-d.textlength(label,font=f))/2,y),label,font=f,fill=FG if enabled else MUTED)
            if subtitle:
                small=font(11);s=clip(d,subtitle,x1-x0-10,small)
                d.text(((x0+x1-d.textlength(s,font=small))/2,y0+min(29,y1-y0-14)),s,font=small,fill=MUTED)
            if enabled:self.buttons.append((box,action))
        def send_button(box,label,action,after=None,**kw):
            button(box,label,('send',action,label,after),enabled=self.allowed(action,status),**kw)
        def ask_button(box,label,action,details,after=None,**kw):
            button(box,label,('ask',action,label,details,after),enabled=self.allowed(action,status),**kw)
        def temperature(box,obj,label,editable=True):
            x0,y0,x1,y1=box;v=status.get(obj,{})
            d.rounded_rectangle(box,radius=5,fill=CARD)
            text(x0+7,y0+3,label,11,color=MUTED,width=x1-x0-14)
            text(x0+7,y0+16,'%.0f°'%v.get('temperature',0),22,width=x1-x0-14)
            text(x0+7,y0+42,'Цель: %.0f°'%v.get('target',0),11,color=GREEN,width=x1-x0-14)
            if editable and not self.busy:self.buttons.append((box,('edit',obj)))
        def thumbnail(name,box,padding=4):
            metadata=self.catalog.get(name);image=metadata.get('thumbnail')
            x0,y0,x1,y1=box
            d.rounded_rectangle(box,radius=4,fill=CARD)
            if image:
                resized=image.copy();resized.thumbnail((x1-x0+1-padding,y1-y0+1-padding))
                im.paste(resized,(x0+(x1-x0+1-resized.width)//2,y0+(y1-y0+1-resized.height)//2))
            else:
                d.rectangle((x0+13,y0+10,x1-13,y1-10),outline=MUTED,width=1)
                text(x0+17,y0+24,'G',17,color=MUTED,width=x1-x0-30)
            return metadata
        def service_row(y,label,subtitle,target):
            box=(8,y,232,y+47)
            d.rounded_rectangle(box,radius=5,fill=CARD)
            text(16,y+5,label,15,width=198)
            text(16,y+28,subtitle,11,color=MUTED,width=198)
            text(215,y+13,'›',17,width=12)
            self.buttons.append((box,('page',target)))

        job=status.get('print_stats',{});state=job.get('state','standby')
        ready=status.get('webhooks',{}).get('state')=='ready'
        manual=status.get('manual_probe',{}).get('is_active',False)
        if state in ('printing','paused') and self.last_state not in ('printing','paused') and self.settings['auto_job'] and not self.confirm and not manual:
            self.page='home';self.editor=None
        self.last_state=state
        page='zprobe' if manual else self.page
        job_progress=print_progress(status,self.catalog.get(job['filename'])) if page in ('home','job_details') and job.get('filename') else None
        titles={'home':'Печать','files':'Файлы G-code','file':'Выбранный файл','menu':'Сервис','service':'Сервис','heat':'Управление',
            'fan':'Обдув','axes':'Управление','extruder':'Управление','print':'Настройка печати','macros':'Макросы',
            'calibration':'Калибровка','zprobe':'Z-offset','screws':'Винты стола','activity':self.busy_label,
            'info':'Сеть и система','editor':'Регулировка','screen':'Экран','favorites':'Избранные макросы',
            'profiles':'Преднагрев','profile_setup':'Профили нагрева','job_details':'О задании'}
        title=titles.get(page,'KINGROON')
        if page=='home' and job_progress and job_progress['preparing']:title='Подготовка'
        elif page=='home' and state=='paused':title='Пауза'
        if page=='editor' and self.editor:title=self.editor['title']
        text(8,4,title,16,width=164)
        state_label={'printing':'Печать','paused':'Пауза','standby':'Готов к работе',
                     'complete':'Печать завершена','cancelled':'Печать отменена','error':'Ошибка'}.get(state,state)
        message=self.message if time.monotonic()<self.message_until else ''
        headerline=('Выполняется: '+self.busy_label) if self.busy else message or state_label
        if page in ('heat','axes','extruder','service','menu') and state not in ('printing','paused') and not self.busy and not message:
            headerline='IP '+(self.addresses[0] if self.addresses else 'не определён')
        if page=='extruder' and not self.busy and not status.get('extruder',{}).get('can_extrude'):
            headerline='Сначала нагрейте сопло'
        text(8,26,headerline,11,color=GREEN if headerline.startswith('IP ') else MUTED,width=166)
        button(ESTOP,'СТОП',('estop',),color='#87313b',size=13)
        if page in ('heat','axes','extruder') and ready and not (self.error or self.confirm):
            for i,(label,target) in enumerate((('Нагрев','heat'),('Оси','axes'),('Нить','extruder'))):
                button((8+i*77,50,78+i*77,82),label,('page',target),selected=page==target,size=13)

        if self.error:
            text(12,58,'Команда не выполнена',16,color=RED)
            lines(12,90,self.error,14,6)
            button((8,230,232,274),'Закрыть',('clear_error',))
        elif not ready:
            text(10,63,'Klipper не готов',18,color=RED)
            lines(10,99,status.get('webhooks',{}).get('state_message','Нет связи'),13,6)
            ask_button((8,229,232,274),'Перезапустить Klipper',('restart',),'Перезапуск управления принтером.')
        elif self.confirm:
            label,details,request=self.confirm
            text(10,55,label,17)
            lines(10,88,details,14,6)
            button((8,231,115,274),'Отмена',('back',))
            button((125,231,232,274),'Выполнить',('confirm',),enabled=not self.busy,color=ACTIVE)
        elif page=='home':
            filename=job.get('filename','')
            preparing=bool(job_progress and job_progress['preparing'])
            if filename:
                text(8,43,Path(filename).name,11,width=224)
                thumbnail(filename,(7,59,154,206),padding=0)
                percent=job_progress['percent']
                if preparing:
                    lines(162,95,'Запуск\nзадания',12,2,width=70)
                else:
                    overlay=(9,188,153,206)
                    region=(overlay[0],overlay[1],overlay[2]+1,overlay[3]+1)
                    patch=im.crop(region)
                    im.paste(Image.blend(patch,Image.new('RGB',patch.size,BG),.86),region[:2])
                    text(13,188,'%d%%'%round(percent*100) if percent is not None else '—',14,color=GREEN,width=38)
                    eta=job_progress['remaining']
                    text(52,190,'≈ '+time_label(eta)+' ›' if eta is not None else 'Осталось: — ›',11,width=98)
                    self.buttons.append(((7,59,154,206),('page','job_details')))
                    if state in ('printing','paused'):
                        for i,(key,label) in enumerate((('speed','Скорость'),('flow','Поток'),('fan','Обдув'))):
                            y=65+i*45
                            button((161,y,232,y+39),label,('edit',key),enabled=not self.busy,size=11,subtitle='%g%%'%self.tuning_value(key,status))
                    d.rectangle((8,210,231,212),fill=CARD)
                    if percent:d.rectangle((8,210,8+round(percent*223),212),fill=GREEN)
                for x,key,label in ((8,'extruder','Сопло'),(123,'heater_bed','Стол')):
                    heater=status.get(key,{})
                    box=(x,216,x+109,245)
                    editable=state in ('printing','paused') and not preparing and not self.busy
                    d.rounded_rectangle(box,radius=4,fill=CARD)
                    text(x+5,216,label+(' ›' if editable else ''),10,width=99)
                    text(x+5,227,'%.0f°'%heater.get('temperature',0),15,width=50)
                    text(x+58,229,'→ %.0f°'%heater.get('target',0),11,color=GREEN,width=48)
                    if editable:self.buttons.append((box,('edit',key)))
            else:text(12,91,'Нет активной печати',18)
            if state in ('printing','paused'):
                if state=='printing':button((8,247,115,279),'Пауза',('send',('pause',),'Пауза',None),enabled=not preparing and self.allowed(('pause',),status),color=ACTIVE)
                else:ask_button((8,247,115,279),'Продолжить',('resume',),'Продолжить текущую печать?',color=ACTIVE)
                ask_button((125,247,232,279),'Отменить',('cancel',),'Отменить текущую печать?')
            else:
                button((8,247,232,279),'Выбрать G-code',('page','files'))
        elif page=='job_details':
            lines(10,52,Path(job.get('filename','')).name,12,2,width=220)
            progress=job_progress or {}
            elapsed=max(0,int(job.get('print_duration',0)))
            layers=job.get('info') or {}
            details=[('Прошло','%d:%02d:%02d'%(elapsed//3600,elapsed//60%60,elapsed%60)),
                     ('Осталось',time_label(progress.get('remaining'))),
                     ('Прогресс',progress.get('source','—'))]
            if layers.get('total_layer') and layers.get('current_layer') is not None:
                details.append(('Слой','%s / %s'%(layers['current_layer'],layers['total_layer'])))
            for i,(label,value) in enumerate(details):
                y=100+31*i
                text(10,y,label,12,color=MUTED,width=77)
                text(94,y,value,13,width=138)
            button((8,235,232,275),'К печати',('page','home'))
        elif page in ('menu','service'):
            for y,label,subtitle,target in ((54,'Калибровки','Винты · Z-offset · карта','calibration'),
                    (109,'Макросы','Cold pull · избранные','macros'),(164,'Экран','Сенсор · поведение экрана','screen'),
                    (219,'Сеть и система','IP · состояние Klipper','info')):
                service_row(y,label,subtitle,target)
        elif page=='heat':
            temperature((8,91,116,154),'extruder','Сопло ›')
            temperature((124,91,232,154),'heater_bed','Стол ›')
            value=round(status.get('fan',{}).get('speed',0)*100)
            text(10,165,'Обдув детали: %d%% ›'%value,12,color=MUTED)
            self.buttons.append(((8,160,232,184),('page','fan')))
            for i,v in enumerate((0,25,50,100)):
                x=8+i*58;send_button((x,188,x+50,222),'%d%%'%v,('fan',v),selected=v==value,size=13)
            button((8,235,115,274),'Преднагрев',('page','profiles'),size=12)
            ask_button((125,235,232,274),'Выключить',('cool',),'Отключить все нагреватели?',size=12)
        elif page=='editor' and self.editor:
            e=self.editor;v=e['value'];unit=e['unit']
            job_name=e.get('job_name')
            key=e['key'];heater=key in ('extruder','heater_bed')
            if job_name and heater:
                actual=status.get(key,{}).get('temperature',0)
                text(10,55,'Сейчас %.0f° · цель %g°'%(actual,self.tuning_value(key,status)),12,color=MUTED)
            elif job_name:
                text(10,55,'Сейчас %g%%'%self.tuning_value(key,status),12,color=MUTED)
            else:text(10,55,'Выбранное значение',12,color=MUTED)
            value='%g%s'%(v,unit);f=font(36)
            d.text(((W-d.textlength(value,font=f))/2,77),value,font=f,fill=GREEN)
            text(10,125,'Диапазон: %g…%g%s'%(e['min'],e['max'],unit),12,color=MUTED)
            deltas=(-5,-1,1,5) if job_name and not heater else (-10,-1,1,10)
            for i,delta in enumerate(deltas):
                x=8+i*58
                button((x,152,x+50,195),('%+d'%delta),('adjust',delta),size=18)
            presets=() if job_name else e['presets']
            for i,preset in enumerate(presets):
                x=8+i*78
                button((x,204,x+68,235),'%g%s'%(preset,unit),('value',preset),size=13)
            button((8,244,115,277),'Отмена',('editor_cancel',))
            can_apply=self.allowed(('job_tune',job_name,key,v),status) if job_name else not self.busy
            button((125,244,232,277),'Применить',('apply',),enabled=can_apply,color=ACTIVE)
        elif page=='fan':
            value=round(status.get('fan',{}).get('speed',0)*100)
            text(10,60,'Обдув детали',14,color=MUTED)
            text(10,85,'%d%%'%value,38,color=GREEN)
            button((124,87,232,131),'Изменить',('edit','fan'))
            for i,v in enumerate((0,25,50,100)):
                x=8+(i%2)*116;y=150+(i//2)*65
                send_button((x,y,x+108,y+53),'%d%%'%v,('fan',v),selected=v==value,size=21)
        elif page=='axes':
            head=status.get('toolhead',{});i='XYZ'.index(self.axis)
            for n,axis in enumerate('XYZ'):
                button((8+n*78,92,76+n*78,123),axis,('axis',axis),selected=self.axis==axis,size=18)
            homed=self.axis.lower() in head.get('homed_axes','')
            pos=head.get('position',[0,0,0])[i]
            text(12,131,'%s = %.2f мм'%(self.axis,pos) if homed else self.axis+': нет парковки',18,color=FG if homed else MUTED)
            for sign,x in ((-1,8),(1,125)):
                send_button((x,161,x+107,204),'−' if sign<0 else '+',('jog',self.axis,self.step*sign),size=28)
            for n,step in enumerate((.1,1,10)):
                button((8+n*78,213,76+n*78,240),'%g мм'%step,('step',step),selected=self.step==step,size=13)
            ask_button((8,249,115,278),'Домой '+self.axis,('home',self.axis),'Ось начнёт движение к концевику.',size=12)
            ask_button((125,249,232,278),'Все XYZ',('home','XYZ'),'Все оси начнут парковку.',size=12)
        elif page=='extruder':
            nozzle=status.get('extruder',{})
            text(10,88,'Сопло %.0f° / цель %.0f°'%(nozzle.get('temperature',0),nozzle.get('target',0)),14,width=222)
            self.buttons.append(((8,85,232,110),('edit','extruder')))
            text(10,110,'Длина подачи',11,color=MUTED)
            for i,length in enumerate((10,50,100)):
                button((8+i*78,128,76+i*78,158),'%d мм'%length,('extrude_length',length),selected=self.extrude_length==length,enabled=length<=self.caps.get('extrude_distance',50),size=14)
            button((8,164,232,190),'Скорость: %g мм/с ›'%self.extrude_speed,('edit','extrude_speed'),size=12)
            send_button((8,197,115,232),'Втянуть',('extrude',-self.extrude_length,self.extrude_speed),size=14)
            send_button((125,197,232,232),'Подать',('extrude',self.extrude_length,self.extrude_speed),size=14)
            filament_actions=(
                ('LOAD','Загрузить','Вставьте нить до захвата шестернями. Подать 55 мм и продавить 15 мм?'),
                ('UNLOAD','Выгрузить','Подать 4 мм и отвести 75 мм? Затем аккуратно выньте нить рукой.'),
                ('PURGE_FILAMENT','Ещё 10 мм','Продавить через нагретое сопло ещё 10 мм?'),
            )
            for i,(name,label,detail) in enumerate(filament_actions):
                x=8+i*78
                ask_button((x,240,x+68,277),label,('macro',name),detail,after='extruder',size=11)
        elif page=='print':
            for y,label,key,factor in ((54,'Скорость','speed','speed_factor'),(126,'Поток','flow','extrude_factor')):
                value=round(status.get('gcode_move',{}).get(factor,1)*100)
                button((8,y,232,y+60),label+' · %d%%'%value,('edit',key),size=20)
            button((8,201,232,239),'Обдув детали',('page','fan'))
            button((8,247,232,278),'К заданию',('page','home'),size=13)
        elif page=='files':
            if time.monotonic()-self.files_refreshed>5:
                self.files_cache=self.catalog.files()
                self.files_refreshed=time.monotonic()
            self.offset=min(self.offset,max(0,((len(self.files_cache)-1)//2)*2))
            for i,name in enumerate(self.files_cache[self.offset:self.offset+2]):
                y=54+i*85;box=(8,y,232,y+78)
                d.rounded_rectangle(box,radius=5,fill=CARD)
                metadata=thumbnail(name,(12,y+7,76,y+71))
                lines(84,y+6,Path(name).stem,12,2,140)
                text(84,y+47,metadata.get('material') or 'G-code',11,color=MUTED,width=140)
                text(84,y+62,'≈ '+time_label(metadata.get('estimated_time')),11,color=MUTED,width=140)
                if self.allowed(('file',name),status):self.buttons.append((box,('select_file',name)))
            if not self.files_cache:text(12,84,'Нет G-code файлов',16,color=MUTED)
            text(10,229,'%d–%d из %d'%(self.offset+1 if self.files_cache else 0,
                min(self.offset+2,len(self.files_cache)),len(self.files_cache)),11,color=MUTED)
            button((8,247,115,278),'‹ Назад',('offset',-2),enabled=self.offset>0)
            button((125,247,232,278),'Далее ›',('offset',2),enabled=self.offset+2<len(self.files_cache))
        elif page=='file':
            name=self.selected_file
            lines(10,51,Path(name).name,12,2,220)
            metadata=thumbnail(name,(8,95,122,209))
            text(132,99,metadata.get('material') or 'G-code',14,width=100)
            text(132,129,'≈ '+time_label(metadata.get('estimated_time')),12,width=100)
            filament=metadata.get('filament_mm')
            if filament:text(132,159,'%.1f м'%(filament/1000),12,width=100)
            text(132,185,'От слайсера',10,color=MUTED,width=100)
            button((8,236,115,277),'Назад',('page','files'))
            ask_button((125,236,232,277),'Печатать',('file',name),Path(name).name,after='home',color=ACTIVE)
        elif page=='macros':
            available=self.caps.get('user_macros',{})
            macros=[name for name in self.settings['favorites'] if name in available or name=='COLDPULL' and name in self.caps.get('commands',())]
            self.macro_offset=min(self.macro_offset,max(0,((len(macros)-1)//3)*3))
            for i,name in enumerate(macros[self.macro_offset:self.macro_offset+3]):
                y=54+i*51;label='Cold pull' if name=='COLDPULL' else name
                action=('macro',name)
                detail='Текущий макрос: 260 → 145 °C, движение осей и подача филамента. Подготовьте материал.' if name=='COLDPULL' else 'Запустить '+name+'? Макрос из вашей конфигурации может включать нагрев и движение.'
                ask_button((8,y,232,y+43),label,action,detail,'activity',size=16)
            if not macros:lines(12,69,'Выберите нужные команды в избранное. Загрузка нити находится в «Контроль → Нить».',14,5)
            if len(macros)>3:
                button((8,209,115,235),'‹',('macro_offset',-3),enabled=self.macro_offset>0)
                button((125,209,232,235),'›',('macro_offset',3),enabled=self.macro_offset+3<len(macros))
            button((8,243,232,278),'Выбрать избранные',('page','favorites'),size=13)
        elif page=='favorites':
            names=sorted(self.caps.get('user_macros',{}))
            self.macro_offset=min(self.macro_offset,max(0,((len(names)-1)//4)*4))
            for i,name in enumerate(names[self.macro_offset:self.macro_offset+4]):
                selected=name in self.settings['favorites'];y=52+i*43
                button((8,y,232,y+37),('✓ ' if selected else '+ ')+name,('favorite',name),selected=selected,size=13)
            if not names:lines(12,70,'Нет доступных пользовательских макросов.',15,4)
            button((8,231,76,275),'‹',('macro_offset',-4),enabled=self.macro_offset>0)
            button((86,231,154,275),'Назад',('page','macros'),size=12)
            button((164,231,232,275),'›',('macro_offset',4),enabled=self.macro_offset+4<len(names))
        elif page=='screen':
            button((8,57,232,112),'К печати автоматически',('toggle_auto_job',),subtitle='Включено' if self.settings['auto_job'] else 'Выключено',selected=self.settings['auto_job'],size=14)
            ask_button((8,126,232,181),'Калибровка сенсора',('touch_calibration',),'Нажмите пять появляющихся точек. Калибровка доступна только в простое.','screen',size=14)
            button((8,234,232,274),'Сервис',('page','service'))
        elif page=='profiles':
            for i,(nozzle,bed) in enumerate(self.settings['presets']):
                y=59+i*71
                ask_button((8,y,232,y+59),'%g° / %g°'%(nozzle,bed),('preset',nozzle,bed),'Нагреть сопло до %g °C и стол до %g °C?'%(nozzle,bed),subtitle='Профиль %d'%(i+1),size=20)
            button((8,218,232,257),'Настроить профили',('page','profile_setup'),size=13)
        elif page=='profile_setup':
            for i,(nozzle,bed) in enumerate(self.settings['presets']):
                y=52+i*92;text(10,y,'Профиль %d'%(i+1),13,color=MUTED)
                button((8,y+24,115,y+72),'Сопло %g°'%nozzle,('edit','preset_%d_extruder'%i),size=12)
                button((125,y+24,232,y+72),'Стол %g°'%bed,('edit','preset_%d_heater_bed'%i),size=12)
            button((8,245,232,278),'Преднагрев',('page','profiles'),size=13)
        elif page=='calibration':
            choices=[('Карта стола',('mesh',),'Будут парковка и измерение стола датчиком.','calibration'),
                ('Винты стола',('screws',),'Измерить высоту у винтов. Парковка — только если оси ещё не найдены.','screws'),
                ('Z-offset',('probe',),'Будет парковка. Затем настройте зазор по бумаге.','zprobe'),
                ('Сохранить результат',('save',),'Сохранить калибровку? Klipper перезапустится.','home')]
            for i,(label,action,detail,after) in enumerate(choices):
                y=54+i*55;ask_button((8,y,232,y+45),label,action,detail,after)
        elif page=='zprobe':
            if manual:
                value=status.get('manual_probe',{}).get('z_position')
                text(10,55,'Проверяйте зазор по бумаге',13,color=MUTED)
                text(10,79,'Z = %.3f мм'%value if value is not None else 'Настройка зазора',23,color=GREEN)
                for i,(label,delta) in enumerate((('−0.1',-.1),('+0.1',.1),('−0.01',-.01),('+0.01',.01))):
                    x=8+(i%2)*116;y=119+(i//2)*54
                    send_button((x,y,x+108,y+44),label,('testz',delta),size=21)
                send_button((8,231,115,275),'Прервать',('abort',),after='calibration')
                ask_button((125,231,232,275),'Принять',('accept',),'Принять найденный зазор? После этого результат можно сохранить.','calibration')
            else:
                lines(12,69,'Подготовка и парковка осей…' if self.busy else 'Калибровка не активна. Запустите Z-offset в разделе калибровки.',15,5)
                button((8,225,232,273),'Калибровка',('page','calibration'))
        elif page=='screws':
            results=status.get('screws_tilt_adjust',{}).get('results',{})
            text(12,51,'%d щелчков / оборот'%KNOB_CLICKS_PER_TURN,11,color=MUTED)
            if not results:lines(12,83,'Результатов пока нет. Выполните измерение винтов.',15,5)
            for i,(key,value) in enumerate(list(results.items())[:4]):
                y=74+i*37
                text(12,y,'Винт '+key.replace('screw',''),14,width=81)
                clicks=screw_clicks(value,results,self.caps)
                direction={'CW':'↻','CCW':'↺'}.get(value.get('sign'),'')
                label='Опорный' if value.get('is_base') else 'Нет данных' if clicks is None else 'Не трогать' if clicks==0 else '%s %d щел.'%(direction,clicks)
                text(100,y,label,16,color=GREEN,width=132)
            if results:
                text(12,222,'↻ по часовой · ↺ против',11,color=MUTED)
                ask_button((8,246,115,277),'Повторить',('screws',),'Повторно измерить винты стола? Будет движение осей.','screws',size=13)
                button((125,246,232,277),'Назад',('page','calibration'))
            else:button((8,234,232,274),'Калибровка',('page','calibration'))
        elif page=='activity':
            text(12,56,self.busy_label or 'Команда завершена',18)
            v=status.get('extruder',{})
            text(12,91,'Сопло %.0f / %.0f °C'%(v.get('temperature',0),v.get('target',0)),17,color=GREEN)
            lines(12,132,self.progress or ('Команда выполняется…' if self.busy else 'Готово'),14,6)
            button((8,235,232,275),'На обзор',('page','home'))
        elif page=='info':
            text(12,59,'Адрес для браузера',14,color=MUTED)
            for i,address in enumerate(self.addresses[:3]):text(12,91+i*30,address,20,color=GREEN)
            text(12,199,'http://'+(self.addresses[0] if self.addresses else '—'),13)
            text(12,225,'Klipper: '+('готов' if ready else 'не готов'),13,color=MUTED)
            ask_button((8,247,232,278),'Перезапустить Klipper',('restart',),'Перезапустить Klipper? Оси потребуется найти заново.','info',size=13)

        print_page='home' if state in ('printing','paused') else 'files'
        control_pages=('heat','axes','extruder','fan','profiles','profile_setup')
        selected_page=self.editor['parent'] if page=='editor' and self.editor else page
        print_pages=('home','files','file','print','job_details')
        for i,(label,target) in enumerate((('Контроль','heat'),('Печать',print_page),('Сервис','service'))):
            selected=(target=='heat' and selected_page in control_pages or target==print_page and selected_page in print_pages or target=='service' and selected_page not in control_pages+print_pages)
            button((2+i*80,282,78+i*80,319),label,('page',target),
                   enabled=not self.confirm and not manual,selected=selected,size=13)
        layout=(tuple(self.buttons),self.confirm,self.editor and self.editor['value'],state,ready,self.busy)
        if layout!=self.layout:self.generation+=1;self.layout=layout
        return im
    def tap(self,x,y):
        for box,action in self.buttons:
            if box[0]<=x<=box[2] and box[1]<=y<=box[3]:return action
    def activate(self,action,status):
        """Local navigation or a structured request for CommandRunner."""
        kind=action[0];self.message=''
        if kind=='page':
            self.page=action[1];self.confirm=None;self.editor=None
            if self.page in ('macros','favorites'):self.macro_offset=0
            return
        if kind=='offset':self.offset=max(0,self.offset+action[1]);return
        if kind=='macro_offset':self.macro_offset=max(0,self.macro_offset+action[1]);return
        if kind=='axis':self.axis=action[1];return
        if kind=='step':self.step=action[1];return
        if kind=='extrude_length':self.extrude_length=action[1];return
        if kind=='toggle_auto_job':
            self.settings['auto_job']=not self.settings['auto_job'];self.save_settings();return
        if kind=='favorite':
            name=action[1]
            if name not in self.caps.get('user_macros',{}):return
            favorites=self.settings['favorites']
            if name in favorites:favorites.remove(name)
            else:favorites.append(name)
            self.save_settings();return
        if kind=='clear_error':self.error='';return
        if kind=='edit':
            key=action[1]
            preset_index=None
            if key.startswith('preset_'):
                _,index,key=key.split('_',2);preset_index=int(index)
            specs={'extruder':('Сопло','°C',0,self.caps['limits']['extruder'],(0,200,240)),
                'heater_bed':('Стол','°C',0,self.caps['limits']['heater_bed'],(0,60,80)),
                'fan':('Обдув','%',0,100,(0,50,100)),
                'speed':('Скорость','%',25,150,(50,100,150)),
                'flow':('Поток','%',50,150,(90,100,110)),
                'extrude_speed':('Скорость подачи',' мм/с',.5,self.caps.get('extrude_speed',10),(1,3,5))}
            title,unit,low,high,presets=specs[key]
            if preset_index is not None:value=self.settings['presets'][preset_index][0 if key=='extruder' else 1]
            else:value=self.tuning_value(key,status)
            job_name=status.get('print_stats',{}).get('filename') if self.page=='home' else None
            self.editor=dict(key=key,title=title,unit=unit,min=low,max=high,presets=presets,
                             value=max(low,min(high,value)),parent=self.page,preset_index=preset_index,job_name=job_name)
            self.page='editor';return
        if kind in ('adjust','value') and self.editor:
            e=self.editor;e['value']=max(e['min'],min(e['max'],e['value']+action[1] if kind=='adjust' else action[1]));return
        if kind=='editor_cancel':self.page=self.editor['parent'];self.editor=None;return
        if kind=='apply' and self.editor:
            e=self.editor;key=e['key'];request=('heater',key,e['value']) if key in ('extruder','heater_bed') else (key,e['value'])
            self.page=e['parent'];self.editor=None
            if e.get('job_name'):
                if e['value']==self.tuning_value(key,status):return
                request=('job_tune',e['job_name'],key,e['value'])
            if e.get('preset_index') is not None:
                self.settings['presets'][e['preset_index']][0 if key=='extruder' else 1]=e['value'];self.save_settings();return
            if key=='extrude_speed':self.extrude_speed=e['value'];return
            return {'action':request,'label':e['title'],'after':self.page}
        if kind=='select_file':
            self.selected_file=action[1];self.page='file';self.confirm=None;return
        if kind=='ask':
            _,request,label,detail,after=action
            self.confirm=(label,detail,{'action':request,'label':label,'after':after});return
        if kind=='back':self.confirm=None;return
        if kind=='confirm':
            request=self.confirm[2];self.confirm=None
            if request['after'] in ('activity','zprobe'):self.page=request['after']
            return request
        if kind=='send':return {'action':action[1],'label':action[2],'after':action[3]}
