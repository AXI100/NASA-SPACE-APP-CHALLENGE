from contextlib import asynccontextmanager
from datetime import date, datetime, UTC
from pathlib import Path
from threading import Thread, RLock
from typing import Literal
from uuid import uuid4
import os
import wave
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from pydantic import BaseModel, Field
from . import nasa_engine as n, voice_offline as voice
from .voice_agent import AgroVoiceAgent, warm_ollama

@asynccontextmanager
async def lifespan(app):
    Thread(target=warm_ollama,daemon=True).start()
    if not field()['offline']:
        Thread(target=voice.provision,daemon=True).start()
        Thread(target=voice.provision_piper,daemon=True).start()
    yield

app=FastAPI(title='AGRO MESH',version='0.5.2',lifespan=lifespan)
guard=RLock()
Lang=Literal['en','es','pt']

class FieldUpdate(BaseModel):
    operation_type:Literal['crops','livestock','integrated']|None=None
    livestock_heads:int|None=Field(default=None,ge=0,le=100000)
    forage_dm_kg_ha:float|None=Field(default=None,ge=0,le=50000)
    equipment:Literal['manual','tractor','mechanized']|None=None
    labor:Literal['low','medium','high']|None=None
    market:Literal['any','broad']|None=None
    location_name:str|None=Field(default=None,max_length=140)
    lat:float|None=Field(default=None,ge=-90,le=90)
    lon:float|None=Field(default=None,ge=-180,le=180)
    hectares:float|None=Field(default=None,gt=0,le=100000)
    crop:str|None=Field(default=None,min_length=1,max_length=80)
    offline:bool|None=None
    ph:float|None=Field(default=None,ge=3,le=10)
    som:float|None=Field(default=None,ge=.1,le=30)
    water:float|None=Field(default=None,ge=0,le=500)
    tab:Literal['rotation','map','diagnostics','finance','community','passport']|None=None
    mode:Literal['farmer','agronomist']|None=None
    layer:Literal['Natural','NDVI','NDRE','SMAP','ECOSTRESS']|None=None
    polygon:list[tuple[float,float]]|None=Field(default=None,max_length=100)

class Plan(BaseModel):
    priorities:list[float]=Field(default_factory=lambda:[50]*4,min_length=4,max_length=4)
    stress:Literal['normal','dry','wet']='normal'
    manual:list[str]|None=Field(default=None,min_length=4,max_length=5)
    length:int=Field(default=5,ge=4,le=5)

class Evidence(BaseModel):
    source:str
    observed:date
    provenance:str=Field(min_length=5,max_length=500)
    metrics:dict[str,float]

class VisionRequest(BaseModel):
    text:str=Field(min_length=1,max_length=2400)
    lang:Lang='en'
    jpeg:str=Field(min_length=12,max_length=2_800_000)
    rgb:str=Field(min_length=4,max_length=65536)
    width:int=Field(ge=1,le=128)
    height:int=Field(ge=1,le=128)

class Ask(BaseModel):
    text:str=Field(min_length=1,max_length=2400)
    session:str=Field(default='local',pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    lang:Lang='en'

class Calc(BaseModel):
    system:Literal['drip','sprinkler','surface']='drip'
    flow:float=Field(default=5,gt=0,le=10000)
    n:float=Field(default=60,ge=0,le=1000)
    p:float=Field(default=30,ge=0,le=1000)
    k:float=Field(default=30,ge=0,le=1000)
    cce:float=Field(default=90,gt=0,le=200)

class Buyer(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    distance_km:float=Field(ge=0,le=30000)
    price:float=Field(ge=0,le=100000)
    freight_per_t_km:float=Field(default=.16,ge=0,le=1000)
    loss_pct:float=Field(default=2,ge=0,le=100)

class Contact(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    phone:str=Field(pattern=r'^\+?[0-9]{5,16}$')

class Message(BaseModel):
    recipient:str=Field(min_length=1,max_length=80)
    text:str=Field(min_length=1,max_length=159)

def field():
    return {**n.DEFAULT,**n.get('field',{}),'offline':os.getenv('AGRO_OFFLINE')=='1' or n.get('field',{}).get('offline',False)}

@app.get('/api/bootstrap')
def bootstrap():
    with guard:
        passport = n.get('passport') or n.put('passport','AM-'+uuid4().hex[:12].upper())
    return dict(field=field(),crops=n.CROPS,catalog=n.CATALOG,ledger=n.get('ledger',[]),
                outbox=n.get('outbox',[]),buyers=n.get('buyers',[]),contacts=n.get('contacts',[]),
                passport=passport,rotations=n.get('rotation:'+n.sector(field()),[]),
                piper={lang:voice.piper_ready(lang) for lang in voice.MODELS},
                voice={lang:voice.ready(voice.ROOT/name) for lang,name in voice.MODELS.items()})

@app.patch('/api/field')
def update(update:FieldUpdate):
    with guard:
        old=field()
        changes=update.model_dump(exclude_none=True)
        if os.getenv('AGRO_OFFLINE')=='1':
            changes['offline']=True
        if 'polygon' in changes and any(not (-90<=lat<=90 and -180<=lon<=180) for lat,lon in changes['polygon']):
            raise HTTPException(422,'Invalid polygon coordinates')
        if changes.get('location_name') and 'lat' not in changes and 'lon' not in changes and changes['location_name']!=old['location_name']:
            matches=n.geocode(changes['location_name'],old['offline'])
            if not matches:
                raise HTTPException(404,'Location unavailable in the local cache')
            changes.update(lat=matches[0]['latitude'],lon=matches[0]['longitude'])
        changed={key for key,value in changes.items() if value!=old.get(key)}
        manual=set(old.get('manual_inputs',[]))|({'ph','som','water'} & changes.keys())
        updated={**old,**changes,'manual_inputs':sorted(manual)}
        if changed & {'lat','lon'}:
            updated.setdefault('polygon',[])
            if 'polygon' not in changes:
                updated['polygon']=[]
            updated.pop('satellite_demo',None)
            updated=n.enrich_field(updated,n.observations(updated))
            updated['tab']='diagnostics'
        elif manual or changed & {'location_name'}:
            updated=n.enrich_field(updated,n.field_cache(updated))
        if changed & {'lat','lon','crop','som'}:
            updated.pop('sequence',None)
        return n.put('field',updated)

@app.get('/api/geocode')
def geocode(q:str):
    return n.geocode(q[:140],field()['offline']) if q.strip() else []

@app.get('/api/observations')
def observations(cached:bool=False):
    active=field()
    data=n.observations({**active,'offline':active['offline'] or cached})
    with guard:
        latest=field()
        if n.sector(latest)==n.sector(active):
            active=n.enrich_field(latest,data)
            if active!=latest:
                n.put('field',active)
        else:
            return observations_for(latest,n.field_cache(latest))
    return observations_for(active,data)

def observations_for(active,data):
    brief=n.build_24_dataset_brief(active,data)
    brief['demo']=active.get('satellite_demo')
    return dict(sources=data,gdd=n.gdd(data),soil=brief['soil'],brief=brief,
                diagnostics=n.field_diagnosis(active,brief),field=active)

@app.get('/api/finance')
def finance():
    current=field()
    return {**n.finance_snapshot(current),'monte_carlo':n.monte_carlo_finance(current,seed=47)}

@app.post('/api/finance/simulate')
def risk_simulation(lang:Lang='en'):
    return run_action('tool_run_monte_carlo',{},lang)

@app.get('/api/ontology')
def ontology():
    return n.build_ontology(field())

@app.post('/api/playbook/execute')
def playbook(lang:Lang='en'):
    return run_action('tool_execute_playbook',{},lang)

@app.get('/api/telemetry')
def telemetry():
    return n.telemetry(field())

class LedgerEntry(BaseModel):
    operation:Literal['income','expense']
    amount_usd:float=Field(gt=0,le=1e8)
    notes:str=Field(default='',max_length=1000)

def run_action(tool,args,lang):
    with guard:
        agent=AgroVoiceAgent(field(),lang)
        try:
            reply,action=agent.execute(tool,args,'')
        except (ValueError,KeyError,TypeError) as error:
            raise HTTPException(422,str(error)) from error
        return dict(reply=reply,actions=[action],updated_state=agent.field,engine='offline-rules')

@app.post('/api/strategy/{strategy}')
def strategy(strategy:Literal['profit','soil','water'],lang:Lang='en'):
    return run_action('tool_rotation_strategy',{'strategy':strategy},lang)

@app.post('/api/ledger')
def ledger(body:LedgerEntry,lang:Lang='en'):
    return run_action('tool_financial_advisor_and_cashflow',body.model_dump(),lang)

@app.post('/api/demo/alert')
def satellite_demo(lang:Lang='en'):
    return run_action('tool_satellite_demo',{},lang)

@app.post('/api/outbox/relay')
def relay(lang:Lang='en'):
    return run_action('tool_mesh_relay',{},lang)

@app.post('/api/evidence')
def import_evidence(body:Evidence):
    limits=n.IMPORT_METRICS.get(body.source,{})
    if not body.metrics or any(k not in limits or not limits[k][0]<=v<=limits[k][1] for k,v in body.metrics.items()):
        raise HTTPException(422,'Unsupported source, metric or range')
    if body.observed>date.today():
        raise HTTPException(422,'Observation date is in the future')
    with guard:
        key='evidence:'+n.sector(field())
        items=n.get(key,{})
        items[body.source]=body.model_dump(mode='json')
        n.put(key,items)
    return {'source':body.source,'status':'imported'}

@app.post('/api/rotation')
def rotation(plan:Plan):
    if any(not 0<=w<=100 for w in plan.priorities):
        raise HTTPException(422,'Invalid priority')
    with guard:
        f=field()
        try:
            years=n.rotation(f,plan.priorities,plan.stress,plan.manual,plan.length)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        n.put('field',{**f,'priorities':plan.priorities,'stress':plan.stress,'sequence':[y['crop'] for y in years]})
        return years

@app.post('/api/rotation/approve')
def approve_rotation():
    with guard:
        f=field()
        if not f.get('sequence'):
            raise HTTPException(422,'Create a rotation first')
        plan=n.rotation(f,f.get('priorities',[50]*4),f.get('stress','normal'),f['sequence'])
        if any(y['bridge'] or not y['compatible'] for y in plan):
            raise HTTPException(422,'Resolve crop family or resource conflicts before approving')
        entry=dict(at=datetime.now(UTC).isoformat(),lat=f['lat'],lon=f['lon'],polygon=f.get('polygon',[]),
                   sector=n.sector(f),years=plan)
        return n.append('rotation:'+entry['sector'],entry)

@app.post('/api/calculations')
def calculations(body:Calc,remember:bool=False):
    settings=body.model_dump()
    with guard:
        current=field()
        result=n.calculations(current,**settings)
        if remember:
            n.put('field',{**current,'calculator':settings,'irrigation_system':settings['system'],'pump_flow_ls':settings['flow']})
        return result

@app.get('/api/audit')
def audit(price:float):
    if not 0<price<=1e8:
        raise HTTPException(422,'Invalid price')
    return n.audit(field(),price)

@app.post('/api/buyers')
def buyer(body:Buyer):
    return n.append('buyers',body.model_dump())

@app.post('/api/contacts')
def contact(body:Contact):
    return n.append('contacts',body.model_dump())

@app.post('/api/outbox')
def outbox(body:Message):
    length=len(body.text.encode())
    if length>=160:
        raise HTTPException(422,'Maximum 159 UTF-8 bytes')
    msg=dict(id=uuid4().hex,**body.model_dump(),bytes=length,status='queued',at=datetime.now(UTC).isoformat())
    return n.append('outbox',msg)

@app.post('/api/ai/ask')
def ask(body:Ask):
    with guard:
        return AgroVoiceAgent(field(),body.lang).ask(body.text,body.session)

@app.post('/api/ai/vision')
def vision(body:VisionRequest):
    try:
        return AgroVoiceAgent(field(),body.lang).vision(body.text,body.jpeg,body.rgb,body.width,body.height)
    except ValueError as error:
        raise HTTPException(422,str(error)) from error

@app.get('/api/voice/briefing')
def briefing(lang:Lang='en'):
    reply, critical = AgroVoiceAgent(field(),lang).briefing()
    return dict(reply=reply,critical=critical)

@app.post('/api/voice/transcribe')
async def transcribe(request:Request,lang:Lang='en'):
    from asyncio import to_thread
    audio=bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio)>24_000_000:
            raise HTTPException(413,'WAV upload limit: 24 MB')
    try:
        text=await to_thread(voice.transcribe,bytes(audio),lang)
    except (ValueError,wave.Error,EOFError) as error:
        raise HTTPException(422,'Expected a PCM or IEEE float WAV with up to 30 seconds of audio') from error
    except (RuntimeError,FileNotFoundError,ImportError) as error:
        raise HTTPException(503,'Vosk model is not available locally') from error
    return dict(text=text,engine='vosk-offline')

@app.get('/api/passport')
def passport():
    with guard:
        return n.sustainable_passport(field())

@app.post('/api/passport/verify')
def verify_passport(payload:str):
    return n.verify_passport_payload(payload)

@app.post('/api/voice/speak')
def speak(body:Ask, desktop_only:bool=False):
    if not desktop_only:
        if not field()['offline']:
            mp3=voice.synthesize_elevenlabs(body.text,body.lang)
            if mp3:
                return Response(mp3,media_type='audio/mpeg',
                                headers={'Cache-Control':'no-store','X-Voice-Engine':'elevenlabs'})
        audio=voice.synthesize_wav(body.text,body.lang)
        if audio is not None:
            return Response(audio,media_type='audio/wav',
                            headers={'Cache-Control':'no-store','X-Voice-Engine':'piper'})
    try:
        spoken=voice.speak(body.text,body.lang)
    except Exception:
        voice.log.exception('Desktop speech synthesis failed')
        spoken=False
    return dict(spoken=spoken,engine='pyttsx3' if spoken else 'web-speech-local')

app.mount('/',StaticFiles(directory=Path(__file__).resolve().parents[1]/'frontend',html=True),name='frontend')

