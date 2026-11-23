# SPDX-License-Identifier: GPL-3.0-or-later
import json
import socket
from pathlib import Path, PurePosixPath
try:
    import numpy as np
except ImportError:
    np=None

DEFAULT_SOCKET = str(Path.home()/'printer_data/comms/klippy.sock')

class API:
    def __init__(self, path=DEFAULT_SOCKET, timeout=15):
        self.path, self.sock, self.buf, self.seq = path, None, b'', 0
        self.timeout=timeout
    def close(self):
        if self.sock:
            self.sock.close()
        self.sock, self.buf = None, b''
    def call(self, method, params=None):
        try:
            if self.sock is None:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(self.timeout)
                self.sock.connect(self.path)
            self.seq += 1
            self.sock.sendall(json.dumps({'id':self.seq, 'method':method,
                'params':params or {}}).encode()+b'\x03')
            while True:
                while b'\x03' not in self.buf:
                    part = self.sock.recv(65536)
                    if not part:
                        raise OSError('Klipper API disconnected')
                    self.buf += part
                    if len(self.buf) > 2**20:
                        raise OSError('Oversized Klipper API response')
                raw, self.buf = self.buf.split(b'\x03', 1)
                response = json.loads(raw)
                if response.get('id') != self.seq:
                    continue
                if 'error' in response:
                    error=response['error']
                    raise RuntimeError(str(error.get('message',error) if isinstance(error,dict) else error))
                return response.get('result', {})
        except Exception:
            self.close()
            raise

def _encode_rle_python(pixels):
    """RGB565 values -> bounded literal/run items used by the MCU."""
    out, literal = bytearray(), []
    def flush():
        if literal:
            out.append(len(literal)-1)
            for value in literal:
                out.extend((value & 255, value >> 8))
            literal.clear()
    pos = 0
    while pos < len(pixels):
        value = pixels[pos]
        end = pos+1
        while end < len(pixels) and pixels[end] == value and end-pos < 128:
            end += 1
        if end-pos > 1:
            flush()
            out.extend((128 | (end-pos-1), value & 255, value >> 8))
            pos = end
        else:
            literal.append(value)
            pos += 1
            if len(literal) == 26:
                flush()
    flush()
    return out

def encode_rle(pixels):
    if np is None:
        return _encode_rle_python(pixels)
    px=np.asarray(pixels,dtype=np.uint16)
    if not len(px):return bytearray()
    starts=np.concatenate(([0],np.flatnonzero(px[1:]!=px[:-1])+1))
    lengths=np.diff(np.concatenate((starts,[len(px)])))
    out=bytearray();literal_start=literal_len=0
    def flush(start,count):
        while count:
            n=min(count,26)
            out.append(n-1)
            out.extend(px[start:start+n].astype('<u2').tobytes())
            start+=n;count-=n
    for start,count in zip(starts.tolist(),lengths.tolist()):
        if count==1:
            if not literal_len:literal_start=start
            literal_len+=1
            continue
        if literal_len:
            flush(literal_start,literal_len);literal_len=0
        value=int(px[start])
        while count:
            n=min(count,128)
            out.extend((128|(n-1),value&255,value>>8));count-=n
    if literal_len:flush(literal_start,literal_len)
    return out

def rgb565(image):
    if np is not None:
        rgb=np.asarray(image.convert('RGB'),dtype=np.uint16).reshape(-1,3)
        return (((rgb[:,0]&248)<<8)|((rgb[:,1]&252)<<3)|(rgb[:,2]>>3)).tolist()
    return [((r & 248)<<8) | ((g & 252)<<3) | (b>>3)
            for r,g,b in image.convert('RGB').getdata()]

def print_command(filename):
    # Klipper G-code uses shlex parsing. Refuse control characters and
    # quotes rather than permit file names to inject another command.
    if (not filename or any(ord(c)<32 or c in '\\";' for c in filename)
            or PurePosixPath(filename).is_absolute() or '..' in PurePosixPath(filename).parts
            or not filename.lower().endswith(('.gcode', '.gco'))):
        raise ValueError('Unsupported G-code file name')
    return 'SDCARD_PRINT_FILE FILENAME="%s"' % filename
