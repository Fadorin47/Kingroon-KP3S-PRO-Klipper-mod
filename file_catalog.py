# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded G-code metadata/thumbnail reads in a background thread."""
import base64,bisect,io,math,queue,re,threading
from pathlib import Path
from PIL import Image

THUMBNAIL=re.compile(rb';\s*thumbnail(?:_PNG|_JPG)? begin\s+\d+x\d+\s+\d+[^\r\n]*\r?\n(.*?)\r?\n;\s*thumbnail(?:_PNG|_JPG)? end',re.S|re.I)
M73=re.compile(rb'^\s*M73\s+([^;\r\n]*)',re.I)

def duration_seconds(text):
    return sum(float(n)*{'d':86400,'h':3600,'m':60,'s':1}[unit.lower()]
        for n,unit in re.findall(r'(\d+(?:\.\d+)?)\s*([dhms])',text,re.I))

def time_label(seconds):
    if seconds is None or not math.isfinite(seconds):return '—'
    minutes=max(0,math.ceil(seconds/60))
    return ('%d ч %02d мин'%(minutes//60,minutes%60)) if minutes>=60 else '%d мин'%minutes

def _metadata(path):
    size=path.stat().st_size
    with path.open('rb') as stream:
        head=stream.read(2*1024*1024)
        stream.seek(max(0,size-256*1024));tail=stream.read(256*1024)
    text=(head+b'\n'+tail).decode('utf-8',errors='replace')
    result={'estimated_time':None,'filament_mm':None,'material':'','thumbnail':None,
            'markers':[],'progress_ready':False,'size':size}
    match=re.search(r';\s*estimated printing time[^=\r\n]*=([^\r\n]+)',text,re.I)
    if match:result['estimated_time']=duration_seconds(match.group(1)) or None
    if not result['estimated_time']:
        match=re.search(r';\s*TIME:\s*(\d+(?:\.\d+)?)',text,re.I)
        if match:result['estimated_time']=float(match.group(1))
    match=re.search(r';\s*filament used\s*\[mm\]\s*[=:]\s*([^\r\n]+)',text,re.I)
    if match:result['filament_mm']=sum(float(n) for n in re.findall(r'\d+(?:\.\d+)?',match.group(1))) or None
    if result['filament_mm'] is None:
        match=re.search(r';\s*Filament used:\s*(\d+(?:\.\d+)?)\s*m\b',text,re.I)
        if match:result['filament_mm']=float(match.group(1))*1000
    match=re.search(r';\s*(?:filament_type|material_type)\s*[=:]\s*([^\r\n;]+)',text,re.I)
    if match:result['material']=match.group(1).strip().strip('"')[:24]
    if not result['material']:
        match=re.search(r'_(PLA|PETG|TPU|ABS|ASA)(?:_|\.)',path.name,re.I)
        if match:result['material']=match.group(1).upper()
    if not result['estimated_time']:result['estimated_time']=duration_seconds(path.stem.rsplit('_',1)[-1]) or None
    for match in THUMBNAIL.finditer(head):
        try:
            encoded=b''.join(line.lstrip(b'; ').strip() for line in match.group(1).splitlines())
            if len(encoded)>2*1024*1024:continue
            raw=base64.b64decode(encoded,validate=True)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width*source.height>4096*4096:continue
                source.thumbnail((148,148))
                image=source.convert('RGBA')
                # Transparent thumbnails are composited on the panel's background.
                bg=Image.new('RGBA',image.size,(33,49,70,255));bg.alpha_composite(image)
                # A small palette keeps UART image traffic and MCU work bounded.
                result['thumbnail']=bg.convert('RGB').quantize(colors=24,dither=Image.Dither.NONE).convert('RGB')
                break
        except (ValueError,OSError,Image.DecompressionBombError):continue
    return result

def _markers(path):
    result=[];position=0;last_percent=None
    with path.open('rb') as stream:
        for line in stream:
            position+=len(line);match=M73.match(line)
            if not match:continue
            params=match.group(1)
            p=re.search(rb'(?:^|\s)P\s*(-?\d+(?:\.\d+)?)',params,re.I)
            r=re.search(rb'(?:^|\s)R\s*(-?\d+(?:\.\d+)?)',params,re.I)
            if p:last_percent=max(0,min(1,float(p.group(1))/100))
            if last_percent is not None:
                result.append((position,last_percent,max(0,float(r.group(1))*60) if r else None))
    return result

class FileCatalog:
    def __init__(self,root):
        self.root=Path(root).resolve();self.cache={};self.pending=set();self.lock=threading.Lock()
        self.requests=queue.Queue()
        threading.Thread(target=self._worker,daemon=True).start()
    def path(self,name):
        path=(self.root/name).resolve()
        if self.root not in path.parents or path.suffix.lower() not in ('.gcode','.gco'):
            raise ValueError('Недопустимый путь G-code')
        return path
    def files(self):
        entries=[]
        for path in self.root.rglob('*'):
            try:
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.gcode','.gco'):
                    self.path(str(path.relative_to(self.root)))
                    entries.append((path.stat().st_mtime_ns,path.relative_to(self.root).as_posix()))
            except (OSError,ValueError):continue
        return [name for _,name in sorted(entries,key=lambda v:(-v[0],v[1]))]
    def get(self,name):
        try:
            path=self.path(name);stat=path.stat();signature=(stat.st_size,stat.st_mtime_ns)
        except (OSError,ValueError):return {}
        key=(name,signature)
        with self.lock:
            cached=self.cache.get(name)
            if cached and cached[0]==signature:return cached[1]
            if key not in self.pending:
                self.pending.add(key);self.requests.put((name,signature))
        return {}
    def _worker(self):
        while True:
            name,signature=self.requests.get();key=(name,signature)
            try:
                path=self.path(name);info=_metadata(path)
                with self.lock:self.cache[name]=(signature,info)
                markers=_markers(path)
                info={**info,'markers':markers,'marker_positions':[m[0] for m in markers],'progress_ready':True}
                with self.lock:
                    self.cache[name]=(signature,info)
                    while len(self.cache)>64:self.cache.pop(next(iter(self.cache)))
            except (OSError,ValueError):
                with self.lock:self.cache[name]=(signature,{'progress_ready':True,'error':True})
            finally:
                with self.lock:self.pending.discard(key)
                self.requests.task_done()

def print_progress(status,metadata):
    job=status.get('print_stats',{});state=job.get('state','standby')
    elapsed=max(0,float(job.get('print_duration',0)))
    used=max(0,float(job.get('filament_used',0)))
    if state=='complete':return {'percent':1.,'remaining':0.,'source':'Готово','preparing':False}
    preparing=state in ('printing','paused') and elapsed<.5 and used<=0
    if preparing:return {'percent':0.,'remaining':None,'source':'Подготовка','preparing':True}
    percent=None;remaining=None;source='Печать'
    markers=metadata.get('markers',[])
    if markers:
        position=status.get('virtual_sdcard',{}).get('file_position',0)
        index=bisect.bisect_right(metadata.get('marker_positions',[m[0] for m in markers]),position)-1
        percent=markers[index][1] if index>=0 else 0.
        remaining=markers[index][2] if index>=0 else None
        source='По данным слайсера'
    elif metadata.get('progress_ready'):
        layers=job.get('info',{});total=layers.get('total_layer');current=layers.get('current_layer')
        if total and current is not None:percent=current/total;source='По слоям'
        elif metadata.get('filament_mm'):
            percent=used/metadata['filament_mm'];source='По филаменту'
    if percent is not None:percent=max(0,min(1,percent))
    estimated=metadata.get('estimated_time')
    if remaining is None and estimated:
        remaining=max(0,estimated*(1-percent)) if markers and percent is not None else max(0,estimated-elapsed)
    return {'percent':percent,'remaining':remaining,'source':source,'preparing':False}
