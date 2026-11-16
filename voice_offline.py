import io
import logging
import math
import struct
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import wave
import zipfile
import requests

ROOT = Path(__file__).with_name('models')
MODELS = dict(en='vosk-model-small-en-us-0.15',es='vosk-model-small-es-0.42',
              pt='vosk-model-small-pt-0.3')
PIPER_ROOT = ROOT/'piper'
PIPER_BASE = 'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/'
PIPER_MODELS = {
    'en': ('en/en_US/lessac/medium', 'en_US-lessac-medium'),
    'es': ('es/es_ES/davefx/medium', 'es_ES-davefx-medium'),
}
PIPER_CACHE = {}
piper_lock = Lock()
log = logging.getLogger('uvicorn.error')
loaded = {}
lock = Lock()

def ready(path):
    if (path/'am'/'final.mdl').is_file():
        return all((path/name).is_file() for name in ('conf/mfcc.conf','graph/HCLr.fst','graph/Gr.fst'))
    return all((path/name).is_file() for name in ('final.mdl','mfcc.conf','HCLr.fst','Gr.fst'))

def provision():
    if os.getenv('AGRO_OFFLINE') == '1':
        return
    ROOT.mkdir(exist_ok=True)
    for name in MODELS.values():
        if ready(ROOT/name):
            continue
        try:
            with TemporaryDirectory(dir=ROOT) as stage:
                target = Path(stage)
                archive = target/'model.zip'
                with requests.get(f'https://alphacephei.com/vosk/models/{name}.zip',stream=True,timeout=(5,20)) as response:
                    response.raise_for_status()
                    with archive.open('wb') as handle:
                        for chunk in response.iter_content(65536):
                            handle.write(chunk)
                            if handle.tell() > 180_000_000:
                                raise ValueError('Model archive too large')
                with zipfile.ZipFile(archive) as zipped:
                    if sum(info.file_size for info in zipped.infolist()) > 400_000_000:
                        raise ValueError('Expanded model too large')
                    for info in zipped.infolist():
                        path = (target/info.filename).resolve()
                        if not path.is_relative_to(target.resolve()):
                            raise ValueError('Unsafe archive member')
                    zipped.extractall(target)
                if not ready(target/name):
                    raise ValueError('Incomplete Vosk archive')
                installed=ROOT/name
                if ready(installed):
                    continue
                previous=target/'previous'
                if installed.exists():
                    installed.replace(previous)
                try:
                    (target/name).replace(installed)
                except OSError:
                    if previous.exists() and not installed.exists():
                        previous.replace(installed)
                    raise
        except (OSError, requests.RequestException, zipfile.BadZipFile, ValueError) as error:
            print(f'Model {name} unavailable: {error}')

def normalize_wav(raw):
    if len(raw)<12 or raw[:4]!=b'RIFF' or raw[8:12]!=b'WAVE':
        raise ValueError('Invalid WAV header')
    end=8+int.from_bytes(raw[4:8],'little')
    if end>len(raw):
        raise ValueError('Incomplete WAV')
    fmt=data=None
    offset=12
    while offset+8<=end:
        tag,size=struct.unpack_from('<4sI',raw,offset)
        start=offset+8
        if start+size>end:
            raise ValueError('Incomplete WAV chunk')
        if tag==b'fmt ': fmt=raw[start:start+size]
        if tag==b'data': data=memoryview(raw)[start:start+size]
        offset=start+size+(size%2)
    if not fmt or len(fmt)<16 or data is None:
        raise ValueError('Missing WAV format or data')
    code,channels,rate,_,align,bits=struct.unpack_from('<HHIIHH',fmt)
    if code==65534 and len(fmt)>=40 and fmt[26:40]==bytes.fromhex('000000001000800000aa00389b71'):
        code=int.from_bytes(fmt[24:26],'little')
    width=bits//8
    if (not 1<=channels<=8 or not 8000<=rate<=192000 or align!=channels*width
            or not ((code==1 and bits in (8,16,24,32)) or (code==3 and bits in (32,64)))):
        raise ValueError('Unsupported WAV format')
    if not data or len(data)%align or len(data)//align>rate*30:
        raise ValueError('WAV must contain up to 30 seconds of complete samples')
    if (code,channels,rate,bits)==(1,1,16000,16):
        return data.tobytes()
    def sample(i):
        if code==3:
            value=struct.unpack_from('<f' if bits==32 else '<d',data,i)[0]
            return max(-1,min(1,value))*32768 if math.isfinite(value) else 0
        if bits==8:
            return (data[i]-128)*256
        return int.from_bytes(data[i:i+width],'little',signed=True)*2**(16-bits)
    frames=[sum(sample(i+c*width) for c in range(channels))/channels for i in range(0,len(data),align)]
    result=bytearray(max(1,len(frames)*16000//rate)*2)
    for i in range(len(result)//2):
        start=i*rate/16000
        j=int(start)
        if rate>16000:
            stop=min(len(frames),(i+1)*rate/16000)
            value=sum(frames[k]*(min(stop,k+1)-max(start,k)) for k in range(j,math.ceil(stop)))/(stop-start)
        else:
            value=frames[j]+(frames[min(j+1,len(frames)-1)]-frames[j])*(start-j)
        struct.pack_into('<h',result,i*2,max(-32768,min(32767,round(value))))
    return bytes(result)


def transcribe(raw, lang):
    from vosk import Model, KaldiRecognizer
    audio=normalize_wav(raw)
    with lock:
        if lang not in loaded:
            path = ROOT/MODELS[lang]
            if not ready(path):
                raise FileNotFoundError(str(path))
            loaded[lang] = Model(str(path))
    recognizer = KaldiRecognizer(loaded[lang],16000)
    parts = []
    for i in range(0,len(audio),8000):
        if recognizer.AcceptWaveform(audio[i:i+8000]):
            parts.append(json.loads(recognizer.Result()).get('text',''))
    parts.append(json.loads(recognizer.FinalResult()).get('text',''))
    return ' '.join(filter(None,parts))

def speak(text, lang):
    import pyttsx3
    with lock:
        engine = pyttsx3.init()
        voices = engine.getProperty('voices')
        for voice in voices:
            descriptor = (voice.id+' '+voice.name+' '+str(voice.languages)).lower()
            if any(s in descriptor for s in dict(en=('en-us','english','0409'),es=('es-es','spanish','0c0a'),pt=('pt-br','portuguese','0416'))[lang]):
                engine.setProperty('voice',voice.id)
                break
        else:
            if not voices:
                return False
            engine.setProperty('voice', voices[0].id)
        engine.say(text)
        engine.runAndWait()
        return True



def piper_ready(lang):
    if lang not in PIPER_MODELS:
        return False
    _, name = PIPER_MODELS[lang]
    return (PIPER_ROOT/f'{name}.onnx').is_file() and (PIPER_ROOT/f'{name}.onnx.json').is_file()


def provision_piper():
    if os.getenv('AGRO_OFFLINE') == '1':
        return
    PIPER_ROOT.mkdir(parents=True, exist_ok=True)
    for lang, (folder, name) in PIPER_MODELS.items():
        if piper_ready(lang):
            continue
        try:
            with TemporaryDirectory(dir=PIPER_ROOT) as directory:
                stage = Path(directory)
                for suffix, limit in (('.onnx', 100_000_000), ('.onnx.json', 100_000)):
                    filename = name + suffix
                    with requests.get(PIPER_BASE+folder+'/'+filename,stream=True,timeout=(8,45)) as response:
                        response.raise_for_status()
                        with (stage/filename).open('wb') as output:
                            for chunk in response.iter_content(65536):
                                if output.tell()+len(chunk)>limit:
                                    raise ValueError('Piper download exceeds size limit')
                                output.write(chunk)
                json.loads((stage/f'{name}.onnx.json').read_text(encoding='utf-8'))
                for artifact in stage.iterdir():
                    artifact.replace(PIPER_ROOT/artifact.name)
        except (OSError, requests.RequestException, ValueError) as error:
            log.warning('Piper model %s unavailable: %s',name,error)


def synthesize_wav(text, lang):
    if not piper_ready(lang):
        return None
    try:
        from piper import PiperVoice

        with piper_lock:
            if lang not in PIPER_CACHE:
                _, name = PIPER_MODELS[lang]
                PIPER_CACHE[lang] = PiperVoice.load(PIPER_ROOT/f'{name}.onnx')
            audio = io.BytesIO()
            with wave.open(audio,'wb') as wav:
                PIPER_CACHE[lang].synthesize_wav(text,wav)
        return audio.getvalue()
    except Exception:
        log.exception('Piper synthesis failed for %s; using local speech fallback',lang)
        return None


def voice_setting(name, default=''):
    if name in os.environ:
        return os.environ[name].strip()
    try:
        lines=(ROOT.parent.parent/'.env').read_text(encoding='utf-8-sig').splitlines()
    except (OSError, UnicodeError):
        return default
    for line in lines:
        key, separator, value=line.partition('=')
        if separator and key.strip()==name:
            return value.strip().strip('"').strip("'")
    return default


def synthesize_elevenlabs(text, lang):
    import re
    key=voice_setting('ELEVENLABS_API_KEY')
    speaker=voice_setting('ELEVENLABS_VOICE_ID','JBFqnCBsd6RMkjVDRZzb')
    if not key or not re.fullmatch(r'[A-Za-z0-9_-]{4,80}',speaker):
        return None
    try:
        response=requests.post(f'https://api.elevenlabs.io/v1/text-to-speech/{speaker}',
            headers={'xi-api-key':key,'Accept':'audio/mpeg'},
            json={'text':text,'model_id':'eleven_multilingual_v2','language_code':lang},timeout=8)
        response.raise_for_status()
        if response.headers.get('Content-Type','').startswith('audio/mpeg') and response.content:
            return response.content
    except requests.RequestException:
        pass
    return None
