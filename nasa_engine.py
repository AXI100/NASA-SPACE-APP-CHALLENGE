from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, UTC
from pathlib import Path
import json
import hashlib
import binascii
import hmac
import secrets
import re
import random
import statistics
import sqlite3
import os
import math
import requests
from .data import CROPS, CATALOG, PEST_EXAMPLES

DB = Path(os.getenv('AGRO_DB', Path(__file__).with_name('agro.sqlite3')))
FORECAST = 'https://api.open-meteo.com/v1/forecast'
POWER = 'https://power.larc.nasa.gov/api/temporal/daily/point'
SOIL = 'https://rest.isric.org/soilgrids/v2.0/properties/query'
USDA = 'https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest'
GEO = 'https://geocoding-api.open-meteo.com/v1/search'
DEFAULT = dict(operation_type='crops',livestock_heads=0,location_name='Davis, California', lat=38.5449, lon=-121.7405,
               hectares=8, crop='maize', offline=False, ph=6.1, som=2.4,
               water=25, equipment='tractor', labor='medium', market='any', tab='rotation', layer='Natural', mode='farmer')

@contextmanager
def database():
    conn = sqlite3.connect(DB, timeout=10)
    try:
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, payload TEXT, stamp TEXT)')
            yield conn
    finally:
        conn.close()

def get(key, default=None):
    with database() as conn:
        row = conn.execute('SELECT payload FROM kv WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default

def put(key, value):
    with database() as conn:
        conn.execute('INSERT OR REPLACE INTO kv VALUES (?, ?, ?)',
                     (key,json.dumps(value, ensure_ascii=False),datetime.now(UTC).isoformat()))
    return value

def append(key, value, limit=None):
    with database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT payload FROM kv WHERE key=?', (key,)).fetchone()
        items = json.loads(row[0]) if row else []
        items.append(value)
        if limit:
            items = items[-limit:]
        conn.execute('INSERT OR REPLACE INTO kv VALUES (?, ?, ?)',
                     (key,json.dumps(items, ensure_ascii=False),datetime.now(UTC).isoformat()))
    return items

def cached(key, offline, fetch):
    old = get(key)
    if not offline:
        try:
            data = fetch()
            return put(key,dict(data=data, fetched=datetime.now(UTC).isoformat(), status='live'))
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
            pass
    return {**old,'status':'cached'} if old else dict(data=None, status='unavailable', fetched=None)

def query(url, params, method='get'):
    response = requests.get(url, params=params, timeout=7) if method == 'get' else requests.post(url,json=params,timeout=(5,20))
    response.raise_for_status()
    return response.json()

PLACES = [
    dict(name='Homestead',admin1='Florida',country='United States',latitude=25.4687,longitude=-80.4776),
    dict(name='Davis',admin1='California',country='United States',latitude=38.5449,longitude=-121.7405),
    dict(name='Bogotá',country='Colombia',latitude=4.711,longitude=-74.0721),
    dict(name='São Paulo',country='Brazil',latitude=-23.5505,longitude=-46.6333),
    dict(name='Des Moines',country='United States',latitude=41.5868,longitude=-93.625),
    dict(name='Quito',country='Ecuador',latitude=-0.1807,longitude=-78.4678)]
def geocode(name, offline):
    key = 'geo:' + name.strip().casefold()
    result = cached(key,offline,lambda:query(GEO,dict(name=name,count=6,format='json')).get('results',[]))
    return result['data'] or [p for p in PLACES if name.casefold() in p['name'].casefold()]

def observations(field):
    lat, lon = field['lat'],field['lon']
    key = f'{lat:.3f},{lon:.3f}'
    end = date.today()-timedelta(days=1)
    start = end-timedelta(days=13)
    sources = {
      'Open-Meteo':lambda:query(FORECAST,dict(latitude=lat,longitude=lon,timezone='auto',forecast_days=7,
         daily='temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration',
         hourly='soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm,soil_moisture_9_to_27cm,relative_humidity_2m,temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m')),
      'NASA POWER':lambda:query(POWER,dict(latitude=lat,longitude=lon,community='AG',format='JSON',
         start=start.strftime('%Y%m%d'),end=end.strftime('%Y%m%d'),parameters='ALLSKY_SFC_SW_DWN,T2M,PRECTOTCORR'))['properties']['parameter'],
      'SoilGrids':lambda:query(SOIL,[('lat',lat),('lon',lon),('depth','0-5cm'),('value','mean')]+
          [('property',prop) for prop in ('clay','sand','silt','soc','phh2o')])['properties']['layers']}
    sources['ERA5']=lambda:query('https://archive-api.open-meteo.com/v1/archive',dict(
        latitude=lat,longitude=lon,start_date=(end-timedelta(days=370)).isoformat(),
        end_date=(end-timedelta(days=5)).isoformat(),models='era5',
        daily='temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration,soil_moisture_0_to_7cm_mean,shortwave_radiation_sum'))
    if abs(lat)<59.9:
        sources['SRTM']=lambda:srtm(lat,lon)
    if 18 <= lat <= 72 and -180 <= lon <= -65:
        sources['USGS NWIS']=lambda:nwis(lat,lon)
        sql = (f"SELECT TOP 12 c.compname,c.comppct_r,h.hzdept_r,h.hzdepb_r,h.ph1to1h2o_r,h.claytotal_r,h.om_r "
               f"FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('POINT({lon:.7f} {lat:.7f})') m "
               "JOIN component c ON c.mukey=m.mukey JOIN chorizon h ON h.cokey=c.cokey "
               "WHERE h.hzdept_r=0 ORDER BY c.comppct_r DESC")
        sources['USDA NRCS'] = lambda:query(USDA,dict(query=sql,format='JSON+COLUMNNAME'),method='post')['Table']
    def load(item):
        name, fetch = item
        return name,cached(f'{name}:{key}',field['offline'],fetch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(load,sources.items()))

def crop_for(name):
    return CROPS.get(name) or dict(names=dict.fromkeys(('en','es','pt'),name),family='unknown',
        root=1,n=-40,water=450,sow=None,sow_inches=None,moisture=None,price=300,yield_t=2,cost=550)

def compatible(field,crop):
    ranks={'low':0,'medium':1,'high':2}
    equipment={'manual':0,'tractor':1,'mechanized':2}
    needed=ranks[crop.get('labor','medium')]
    if field.get('equipment')=='mechanized' and needed==1:
        needed=0
    return (needed<=ranks[field.get('labor','medium')]
            and equipment[crop.get('equipment','manual')]<=equipment[field.get('equipment','tractor')]
            and (field.get('market')!='broad' or crop.get('market')=='broad'))

def sector(field):
    return f"{field['lat']:.5f},{field['lon']:.5f}"

def rotation(field, priorities, stress='normal', manual=None, length=5):
    drought = dict(normal=1,dry=1.3,wet=.85)[stress]
    def score(key):
        c = crop_for(key)
        return (priorities[0]*(c['n']/80+c['root']/2)+priorities[1]*(1-c['water']*drought/700)
                +priorities[2]*math.tanh((c['price']*c['yield_t']-c['cost'])/1400))
    beam = [(0,[field['crop']])]
    for _ in range(length):
        if manual:
            break
        candidates = []
        for total,path in beam:
            for key,c in CROPS.items():
                prev=crop_for(path[-1])
                if key in path[1:] or c['family'] in {crop_for(k)['family'] for k in path[-3:]} or key=='banana' or not compatible(field,c):
                    continue
                functional=(c['group']!=prev.get('group'))*.5+(c['root_pattern']!=prev.get('root_pattern'))*.35
                seasonal={c['season'],prev.get('season')}=={'warm','cool'}
                legume=1.4 if prev['n']<=-80 and c['group']=='legume' else 0
                diversity = priorities[3]*(1 if c['family'] not in {crop_for(k)['family'] for k in path} else 0)
                candidates.append((total+score(key)+diversity+40*(functional+.65*seasonal+legume)+8*(c['weed_suppression']=='high'),path+[key]))
        if not candidates:
            raise ValueError('No feasible rotation with the selected farm resources')
        beam = sorted(candidates,reverse=True)[:96]
    som, nitrogen, previous = field['som'], 0, field['crop']
    years = []
    sequence=manual or beam[0][1][1:]
    history=[field['crop']]
    cover_history=[]
    cover_pool={k:(CROPS[k]['family'],CROPS[k]['names']) for k in ('vetch','clover','rye','radish','buckwheat','flax')}
    cover_pool['phacelia']=('Boraginaceae',dict(en='Phacelia',es='Facelia',pt='Facélia'))
    cover_pool['quinoa']=('Amaranthaceae',CROPS['quinoa']['names'])
    for i,key in enumerate(sequence):
        c = crop_for(key)
        prev=crop_for(previous)
        rest=next((gap for gap,k in enumerate(reversed(history),1) if crop_for(k)['family']==c['family']),None)
        forbidden={crop_for(k)['family'] for k in history[-2:]+sequence[i:i+4]}
        if i+1<len(sequence):
            forbidden.add(crop_for(sequence[i+1])['family'])
        forbidden.update(cover_history[-3:])
        cover=next((k for k,(family,_) in cover_pool.items() if family not in forbidden),None)
        cover_history.append(cover_pool[cover][0] if cover else None)
        cover_som=.04 if cover else 0
        n_credit=min(max(0,-c['n']),prev.get('n_credit',0))
        pest=PEST_EXAMPLES.get(prev['family']) if c['family']!=prev['family'] and (rest is None or rest>=4) else None
        som = max(.1,som+cover_som+.07*c['root']+(.08 if c['n']>0 else -.11)-(drought-1)*.1)
        nitrogen += c['n']
        window=planting_window(field,c.get('season'))
        roles=[]
        if c.get('n',0)>0: roles.append('nitrogen')
        if pest: roles.append('pest')
        if c.get('weed_suppression')=='high': roles.append('weeds')
        if not roles: roles.append('diversity')
        years.append(dict(crop=key, names=c['names'],planting_months=window,roles=roles,n_balance_kg_ha=c['n'], family=c['family'], bridge=rest is not None and rest<4, interval=rest,
                          group=c.get('group'), season=c.get('season'), weed_suppression=c.get('weed_suppression'),
                          root_pattern=c.get('root_pattern'), cover=cover, cover_names=cover_pool[cover][1] if cover else None, cover_som=cover_som,
                          cover_carbon_t_co2e_ha=round(cover_som/100*.58*3900*44/12,2), erosion_protection=bool(cover),
                          compatible=compatible(field,c), pest=dict(zip(('en','es','pt'),pest)) if pest else None,
                          n_credit_kg_ha=n_credit, urea_bags=round(n_credit*field['hectares']/23,1),
                          som=round(som,2), carbon=round(som/100*.58*3900*44/12,1), nitrogen=nitrogen,
                          water=round(c['water']*drought), margin=round((c['price']*c['yield_t']-c['cost'])*field['hectares'])))
        history.append(key)
        previous = key
    return years

def calculations(field, system='drip', flow=5, n=60, p=30, k=30, grade=(15,15,15), cce=90):
    area = field['hectares']
    efficiency = dict(drip=.9,sprinkler=.75,surface=.6)[system]
    volume = field['water']*10*area/efficiency
    bags = math.ceil(max(needed/(grade_pct/100) for needed,grade_pct in zip((n,p,k),grade))*area/50)
    return dict(pump_hours=round(volume/(3.6*flow),2), water_m3=round(volume,1), npk_bags=bags,
                lime_t=round(max(0,6.5-field['ph'])*2*area/(cce/100),2), system=system, flow=flow,
                applied_npk_kg_ha=[round(bags*50/area*g/100,1) for g in grade])

def gdd(climate, base=10):
    daily = (climate.get('Open-Meteo',{}).get('data') or {}).get('daily',{})
    pairs = [(hi,lo) for hi,lo in zip(daily.get('temperature_2m_max',[]),daily.get('temperature_2m_min',[]))
             if hi is not None and lo is not None]
    return round(sum(max(0,(hi+lo)/2-base) for hi,lo in pairs),1) if pairs else None

def buyers_ranked():
    buyers = get('buyers', [])
    ranked = [{**buyer, 'net': round(buyer['price']*(1-buyer['loss_pct']/100)
               -buyer['distance_km']*buyer['freight_per_t_km'],2)} for buyer in buyers]
    return sorted(ranked, key=lambda buyer: buyer['net'], reverse=True)

def audit(field, price):
    c = crop_for(field['crop'])
    margin = c['price']*c['yield_t']-c['cost']
    score = round(max(0,min(100,50+max(-30,min(30,(margin/price-.05)*350))
                            -abs(field['ph']-6.5)*10+min(15,field['som']*4))))
    return dict(score=score, decision='buy' if score>=75 else 'negotiate' if score>=50 else 'reject',
                asking_price_per_ha=price, margin_per_ha=margin, recovery_usd_ha=round(max(0,6.5-field['ph'])*2/.9*100+max(0,3-field['som'])*150,2), basis='screening_estimate')



def soil_profile(sources):
    profile = {}
    soils = (sources.get('SoilGrids') or {}).get('data') or []
    for layer in soils:
        try:
            mean = layer['depths'][0]['values']['mean']
            factor = layer['unit_measure']['d_factor']
            if mean is not None and factor:
                profile[layer['name']] = dict(value=round(mean/factor,2), source='SoilGrids', depth='0–5 cm')
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    rows = (sources.get('USDA NRCS') or {}).get('data') or []
    if len(rows)>1:
        columns = dict(zip(rows[0],rows[1]))
        for prop, key in [('phh2o','ph1to1h2o_r'),('organic_matter','om_r')]:
            try:
                profile[prop] = dict(value=float(columns[key]), source='USDA NRCS',
                                     depth=f"0–{columns['hzdepb_r']} cm")
            except (ValueError, TypeError, KeyError):
                continue
    return profile




def srtm(lat,lon):
    delta=.001
    points=[(lat,lon),(lat+delta,lon),(lat-delta,lon)]
    if abs(lon)<179.99 and abs(lat)<59.9:
        points.extend([(lat,lon+delta),(lat,lon-delta)])
    result=query('https://api.opentopodata.org/v1/srtm90m',
                 {'locations':'|'.join(f'{a:.6f},{b:.6f}' for a,b in points)})
    elevations=[p['elevation'] for p in result['results']]
    if not elevations or elevations[0] is None:
        raise ValueError('SRTM sample unavailable')
    slope=None
    if len(elevations)==5 and all(x is not None for x in elevations):
        _,n,s,e,w=elevations
        slope=100*math.hypot((n-s)/222.39,(e-w)/(222.39*math.cos(math.radians(lat))))
    return dict(elevation_m=elevations[0],slope_pct=round(slope,2) if slope is not None else None,
                provenance='SRTM90m via OpenTopoData; slope from ~220 m central differences')

def nwis(lat,lon):
    bounds=','.join(f'{v:.5f}' for v in (lon-.15,lat-.15,lon+.15,lat+.15))
    result=query('https://waterservices.usgs.gov/nwis/iv/',
                 dict(format='json',bBox=bounds,parameterCd='00060,00065',siteStatus='active'))
    readings=[]
    for item in result['value']['timeSeries'][:20]:
        values=item['values'][0]['value']
        if values:
            value=values[-1]
            readings.append(dict(site=item['sourceInfo']['siteName'],parameter=item['variable']['variableName'],
                                 units=item['variable']['unit']['unitCode'],value=value['value'],observed=value['dateTime']))
    return readings

SOURCE_NAMES={'NASA POWER':'NASA POWER','SRTM':'SRTM','ERA5':'ERA5','USGS NWIS':'NWIS',
              'USDA NRCS':'Web Soil Survey / SSURGO / STATSGO2'}
FIELD_SOURCES=('Open-Meteo','NASA POWER','SoilGrids','USDA NRCS','ERA5','SRTM','USGS NWIS')
IMPORT_METRICS={
    'MODIS / VIIRS':{'ndvi':(-1,1)},
    'NLCD':{'forest_loss_ha':(0,100000),'period_years':(1,40)},
    'SMAP':{'root_m3_m3':(0,1)},
    'GPM IMERG':{'rain_mm':(0,1500)},
    'ECOSTRESS':{'lst_c':(-90,100)},
    'NLDAS / GLDAS':{'soil_0_27_m3_m3':(0,1)},
    'OpenET':{'actual_et_mm':(0,1000)},
}

def field_cache(field):
    key=f"{field['lat']:.3f},{field['lon']:.3f}"
    return {name:get(f'{name}:{key}',{}) for name in FIELD_SOURCES}

def build_24_dataset_brief(field, sources=None):
    sources=field_cache(field) if sources is None else sources
    imports=get('evidence:'+sector(field),{})
    weather=sources.get('Open-Meteo',{}).get('data') or {}
    local_now=datetime.now(UTC)+timedelta(seconds=weather.get('utc_offset_seconds',0))
    today=local_now.date().isoformat()
    hourly=weather.get('hourly',{})
    daily=weather.get('daily',{})
    def series(key):
        return [x for stamp,x in zip(hourly.get('time',[]),hourly.get(key,[])) if stamp.startswith(today) and x is not None]
    def day_value(key):
        return next((v for d,v in zip(daily.get('time',[]),daily.get(key,[])) if d==today),None)
    layers=[series(f'soil_moisture_{depth}cm') for depth in ('0_to_1','1_to_3','3_to_9','9_to_27')]
    profile=[sum(values)/len(values) if values else None for values in layers]
    power=sources.get('NASA POWER',{}).get('data') or {}
    def latest_power(key):
        valid=[(d,v) for d,v in (power.get(key,{}) or {}).items() if v is not None and v>-900]
        return dict(date=max(valid)[0],value=max(valid)[1]) if valid else None
    refs=[]
    connected={label:sources.get(key,{}) for key,label in SOURCE_NAMES.items()}
    for name,owner,url,topic in CATALOG:
        item=imports.get(name)
        source=connected.get(name)
        status=('imported' if item else 'available' if source and source.get('data') else
                'unavailable' if name in connected else 'catalog_only')
        refs.append(dict(name=name,agency=owner,url=url,status=status))
    clock=local_now.strftime('%Y-%m-%dT%H:00')
    next_hours=[i for i,stamp in enumerate(hourly.get('time',[])) if stamp>=clock][:3]
    application={}
    for key in ('precipitation','temperature_2m','relative_humidity_2m','wind_speed_10m','wind_gusts_10m'):
        values=hourly.get(key,[])
        application[key]=[values[i] for i in next_hours if i<len(values) and values[i] is not None]
    raw_t=hourly.get('temperature_2m',[])
    raw_rh=hourly.get('relative_humidity_2m',[])
    thi_pairs=[(raw_t[i],raw_rh[i]) for i in next_hours if i<min(len(raw_t),len(raw_rh)) and raw_t[i] is not None and raw_rh[i] is not None]
    return dict(sector=sector(field),date=today,evidence=refs,imported=imports,application_hours=application,thi_pairs=thi_pairs,
                forecast=dict(rain_mm=day_value('precipitation_sum'),min_c=day_value('temperature_2m_min'),
                   max_c=day_value('temperature_2m_max'),reference_et0_mm=day_value('et0_fao_evapotranspiration'),
                   soil_m3_m3=dict(zip(('0–1 cm','1–3 cm','3–9 cm','9–27 cm'),profile)),
                   weighted_soil_0_27_m3_m3=(sum(v*w for v,w in zip(profile,(1,2,6,18)))/27 if None not in profile else None),
                   humid_hours=sum(v>=90 for v in series('relative_humidity_2m')),
                   seven_day=[dict(date=d,rain_mm=r,et0_mm=et) for d,r,et in zip(daily.get('time',[]),
                       daily.get('precipitation_sum',[]),daily.get('et0_fao_evapotranspiration',[]))]),
                power={name:latest_power(name) for name in ('ALLSKY_SFC_SW_DWN','T2M','PRECTOTCORR')},
                soil=soil_profile(sources),
                terrain=sources.get('SRTM',{}).get('data'),
                water=(sources.get('USGS NWIS',{}).get('data') or [])[:5],
                era5=(sources.get('ERA5',{}).get('data') or {}).get('daily',{}))


def finance_snapshot(field, hectares=None, scenario='normal'):
    area=field['hectares'] if hectares is None else hectares
    crop=crop_for(field['crop'])
    rows=get('ledger',[])
    income=sum(float(r.get('amount_usd',0)) for r in rows if r.get('type')=='income')
    expense=sum(float(r.get('amount_usd',r.get('cost_usd',0))) for r in rows if r.get('type')!='income')
    stress={'normal':(1,1),'dry':(.75,1.10),'wet':(.90,1.05)}
    yield_factor,cost_factor=stress[scenario]
    projected_sales=area*crop['yield_t']*yield_factor*crop['price']
    projected_expense=area*crop['cost']*cost_factor
    expected_t=area*crop['yield_t']*yield_factor
    break_even=projected_expense/expected_t if expected_t else None
    sensitivity=[]
    for key,multiplier in [('pessimistic',.85),('base',1),('optimistic',1.15)]:
        price=crop['price']*multiplier
        revenue=expected_t*price
        sensitivity.append(dict(scenario=key,price_usd_t=round(price,2),revenue_usd=round(revenue,2),
                                margin_usd=round(revenue-projected_expense,2)))
    return dict(roi_pct=round(100*(projected_sales-projected_expense)/projected_expense,2) if projected_expense else None,
                cost_per_ha=round(projected_expense/area,2), margin_per_ha=round((projected_sales-projected_expense)/area,2),
                break_even_usd_t=round(break_even,2) if break_even is not None else None,
                sensitivity=sensitivity,income_usd=round(income,2),expense_usd=round(expense,2),cashflow_usd=round(income-expense,2),
                projected_revenue_usd=round(projected_sales,2),projected_cost_usd=round(projected_expense,2),
                projected_margin_usd=round(projected_sales-projected_expense,2),hectares=area,scenario=scenario)


def passport_key():
    with database() as conn:
        key='passport:signing_key'
        conn.execute('INSERT OR IGNORE INTO kv VALUES (?, ?, ?)',
                     (key,json.dumps(secrets.token_hex(32)),datetime.now(UTC).isoformat()))
        value=conn.execute('SELECT payload FROM kv WHERE key=?',(key,)).fetchone()[0]
    return bytes.fromhex(json.loads(value))


def sustainable_passport(field):
    identity=get('passport')
    if not identity:
        identity=put('passport','AM-'+secrets.token_hex(6).upper())
    current=sector(field)
    land_cover=get('evidence:'+current,{}).get('NLCD',{})
    loss=land_cover.get('metrics',{}).get('forest_loss_ha')
    approved=get('rotation:'+current,[])
    approved_plan=approved[-1] if approved else None
    sequence=(approved_plan or {}).get('years') or []
    draft=field.get('sequence') or []
    years=sequence or (rotation(field,[50]*4,manual=draft) if draft else [])
    baseline=field['som']/100*.58*3900*44/12
    carbon=round(years[-1]['carbon']-baseline,2) if years else None
    crop=crop_for(field['crop'])
    efficiency=round(crop['yield_t']*1000/(crop['water']*10),3) if crop['water']>0 else None
    label=re.sub(r'[^a-z0-9_-]+','-',field['crop'].lower()).strip('-')[:36] or 'custom'
    core='|'.join(('AGRO-MESH',str(identity),current,label,
                   str(carbon) if carbon is not None else 'NA',str(efficiency) if efficiency is not None else 'NA'))
    active_sources=field_cache(field)
    imports=get('evidence:'+current,{})
    verified_sources=[name for name in ('NASA POWER','SRTM','USDA NRCS') if active_sources[name].get('data')]
    verified_sources.extend(name for name in ('SMAP','GPM IMERG','ECOSTRESS','MODIS / VIIRS','NLCD') if imports.get(name))
    cert=dict(id=identity,coordinates=[field['lat'],field['lon']],sector=current,crop=field['crop'],
                location_name=field['location_name'],hectares=field['hectares'],
                operation_type=field.get('operation_type','crops'),livestock_heads=field.get('livestock_heads',0),
                nitrogen_saved_kg=rotation_totals(field,years)['nitrogen_kg'],verified_sources=verified_sources,
                verification='HMAC-SHA256; verify on the issuing AGRO MESH server',
                deforestation={'status':'land_cover_imported' if loss is not None else 'needs_land_cover_history',
                               'loss_ha':loss,'observed':land_cover.get('observed'),'provenance':land_cover.get('provenance')},
                carbon={'status':'modeled' if carbon is not None else 'needs_rotation','delta_t_co2e_ha':carbon},
                water={'status':'modeled','kg_per_m3':efficiency},
                rotation={'status':'approved' if sequence else 'draft' if years else 'needs_rotation',
                          'years':[year['crop'] for year in years],'approved_at':(approved_plan or {}).get('at')},
                ledger_count=len(get('ledger',[])))
    stable_key='passport:issued:'+hashlib.sha256((core+'\n'+canonical_passport(cert)).encode()).hexdigest()
    stamp=datetime.now(UTC).isoformat()
    signing=passport_key()
    with database() as conn:
        conn.execute('INSERT OR IGNORE INTO kv VALUES (?, ?, ?)',(stable_key,json.dumps(stamp),stamp))
        cert['issued_at']=json.loads(conn.execute('SELECT payload FROM kv WHERE key=?',(stable_key,)).fetchone()[0])
        signature=hmac.new(signing,(core+'\n'+canonical_passport(cert)).encode(),hashlib.sha256).hexdigest()
        conn.execute('INSERT OR IGNORE INTO kv VALUES (?, ?, ?)',
                     ('passport:signed:'+signature,canonical_passport(cert),stamp))
    return {**cert,'qr':core+'|'+signature}


def canonical_passport(certificate):
    return json.dumps(certificate,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def verify_passport_payload(payload):
    if len(payload)>220:
        return {'valid':False}
    fields=payload.split('|')
    if len(fields)!=7 or fields[0]!='AGRO-MESH' or fields[1]!=get('passport') or not re.fullmatch(r'[0-9a-f]{64}',fields[-1]):
        return {'valid':False}
    core,signature='|'.join(fields[:-1]),fields[-1]
    certificate=get('passport:signed:'+signature)
    signed=core if certificate is None else core+'\n'+canonical_passport(certificate)
    expected=hmac.new(passport_key(),signed.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature):
        return {'valid':False}
    if certificate is None:
        return {'valid':True,'scope':'legacy_qr'}
    return {'valid':True,'scope':'certificate','certificate':{**certificate,'qr':payload}}


STRATEGIES={
    'profit':([15,15,100,40],'normal'),
    'soil':([100,35,20,75],'normal'),
    'water':([25,100,10,45],'dry'),
}

def planting_window(field,season):
    if season=='year_round' or abs(field['lat'])<10:
        return []  # Window depends on local wet/dry seasons or cultivar.
    months=[3,4,5] if season=='warm' else [9,10,11] if season=='cool' else []
    return months if field['lat']>=0 else [((month+5)%12)+1 for month in months]

def rotation_totals(field,years):
    area=field['hectares']
    baseline=crop_for(field['crop'])['water']*len(years)
    initial=field['som']/100*.58*3900*44/12
    return dict(margin_usd=sum(y['margin'] for y in years),
                water_saved_m3=round((baseline-sum(y['water'] for y in years))*area*10) if years else None,
                nitrogen_kg=round(sum(y['n_credit_kg_ha'] for y in years)*area,1),
                urea_bags=round(sum(y['urea_bags'] for y in years),1),
                carbon_t_co2e=round((years[-1]['carbon']-initial)*area,1) if years else None)

def enrich_field(field,sources):
    updated=dict(field)
    applied=dict(field.get('auto_inputs',{}))
    locked=set(field.get('manual_inputs',[]))
    sector_id=sector(field)
    for name,info in list(applied.items()):
        if info.get('sector')!=sector_id and name not in locked:
            updated[name]=DEFAULT[name]
            applied.pop(name)
    brief=build_24_dataset_brief(updated,sources)
    soil=brief['soil']
    ph=soil.get('phh2o')
    som=soil.get('organic_matter')
    carbon=soil.get('soc')
    if not som and carbon and carbon.get('value') is not None:
        som={**carbon,'value':carbon['value']*.1724,'source':'SoilGrids organic carbon × 1.724'}
    forecast=brief['forecast']['seven_day']
    valid=[d for d in forecast if d['date']>=brief['date'] and d['et0_mm'] is not None and d['rain_mm'] is not None][:7]
    water=None
    if len(valid)==7:
        deficit=sum(d['et0_mm']-d['rain_mm'] for d in valid)
        water=dict(value=max(15,deficit) if deficit>0 else 0,source='Open-Meteo, 7-day ET0 minus precipitation')
    last_plan=field.get('last_playbook') or {}
    if last_plan.get('sector')==sector_id and last_plan.get('hold_date')==brief['date']:
        water={'value':0,'source':'playbook wet-soil/rain hold'}
    for name,entry,low,high in [('ph',ph,3,10),('som',som,.1,30),('water',water,0,500)]:
        if name in locked:
            applied.pop(name,None)
            continue
        if entry and entry.get('value') is not None and low<=entry['value']<=high:
            updated[name]=round(entry['value'],2)
            applied[name]={**entry,'sector':sector_id,'reference_date':brief['date']}
    updated['auto_inputs']=applied
    return updated

def field_diagnosis(field,brief):
    forecast=brief['forecast']
    soil=brief['soil']
    totals=forecast['seven_day']
    rain=[d['rain_mm'] for d in totals if d['date']>=brief['date'] and d['rain_mm'] is not None][:7]
    smap=brief['imported'].get('SMAP',{})
    moisture=smap.get('metrics',{}).get('root_m3_m3') if smap.get('observed')==brief['date'] else None
    moisture_source='SMAP'
    if moisture is None:
        moisture=forecast.get('weighted_soil_0_27_m3_m3')
        moisture_source='Open-Meteo 0–27 cm proxy'
    heat=forecast['max_c']
    humidity=forecast['humid_hours'] if heat is not None or forecast['rain_mm'] is not None else None
    humid=humidity or 0
    settings=field.get('calculator',{})
    calc=calculations(field,**settings)
    ph=field['ph']
    band={'potato':(5,6.2),'blueberry':(4.5,5.5),'alfalfa':(6.5,7.5),'maize':(5.8,7.2)}.get(field['crop'],(6,7))
    return dict(calculations=calc,ph=ph,som=field['som'],ph_band=band,provenance=field.get('auto_inputs',{}),
                soil_status='critical' if ph<band[0]-.6 or ph>band[1]+.6 else 'attention' if not band[0]<=ph<=band[1] or field['som']<2 else 'optimal',
                water_status='critical' if moisture is not None and moisture<.12 else 'attention' if field['water']>0 or not rain else 'optimal',
                climate_status='critical' if (heat is not None and heat>=36) or humid>=10 else 'attention' if humid>=6 or heat is None else 'optimal',
                rain_7d_mm=round(sum(rain),2) if len(rain)==7 else None,root_proxy_m3_m3=moisture,moisture_source=moisture_source if moisture is not None else None,
                humid_hours=humidity,max_c=heat,latest_forecast=brief['date'],net_irrigation_mm=field['water'])

def relay_packet(packet_id=None):
    with database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT payload FROM kv WHERE key=?',('outbox',)).fetchone()
        packets=json.loads(row[0]) if row else []
        packet=next((x for x in reversed(packets) if x.get('id')==packet_id),None) if packet_id else next((x for x in reversed(packets) if x.get('status')!='relayed'),None)
        if not packet:
            raise ValueError('No queued packet to relay')
        if packet.get('status')=='relayed' and packet.get('hops'):
            return dict(packet)
        raw=packet['text'].encode('utf-8')
        count=len(raw)
        if count>=160:
            raise ValueError('Packet exceeds 159 bytes')
        checksum=f'{binascii.crc_hqx(raw,0xffff):04X}'
        route=['field','cooperative','satellite_gateway','satellite_uplink']
        hops=[dict(origin=origin,destination=destination,timestamp_utc=datetime.now(UTC).isoformat(),
                   bytes=count,crc16=checksum) for origin,destination in zip(route,route[1:])]
        packet.update(status='relayed',bytes=count,route=route,hops=hops,simulated=True)
        conn.execute('UPDATE kv SET payload=?, stamp=? WHERE key=?',
                     (json.dumps(packets,ensure_ascii=False),datetime.now(UTC).isoformat(),'outbox'))
        return dict(packet)


def telemetry(field):
    brief=build_24_dataset_brief(field)
    years=rotation(field,field.get('priorities',[50]*4),field.get('stress','normal'),field['sequence']) if field.get('sequence') else []
    packets=get('outbox',[])
    sources=field_cache(field)
    impact=rotation_totals(field,years)
    forecast=brief['forecast']
    measurements=brief['imported']
    def current_value(source,key):
        item=measurements.get(source,{})
        return item.get('metrics',{}).get(key) if item.get('observed')==brief['date'] else None
    temperature=current_value('ECOSTRESS','lst_c')
    moisture=current_value('SMAP','root_m3_m3')
    rain=current_value('GPM IMERG','rain_mm')
    minimum=forecast['min_c']
    alerts=(max(forecast['rain_mm'] or 0,rain or 0)>=35 or
            (minimum is not None and minimum<=0) or
            (temperature is not None and temperature>=40) or
            (moisture is not None and moisture<.15))
    fertilizer=round(impact['urea_bags']*38,2)
    pumping=round((impact['water_saved_m3'] or 0)*.12,2)
    reseeding=round(field['hectares']*crop_for(field['crop'])['cost']*.25,2) if alerts else 0
    impact.update(avoided_loss_usd=round(fertilizer+pumping+reseeding,2),
                  avoided_loss_breakdown=dict(nitrogen_usd=fertilizer,pumping_usd=pumping,reseeding_usd=reseeding,
                                             active_alert=alerts,modeled=True))
    return dict(readings=dict(power=brief['power'],terrain=brief['terrain'],soil=brief['soil'],
                forecast=brief['forecast'],era5_recent={k:(v[-1] if v else None) for k,v in brief['era5'].items()},imported=brief['imported']),
                statuses={name:{'status':'cached' if source.get('data') is not None else 'unavailable',
                               'fetched':source.get('fetched')} for name,source in sources.items()},
                impact=impact,mesh=dict(packets=len(packets),bytes=sum(len(p['text'].encode()) for p in packets),
                largest_packet=max((len(p['text'].encode()) for p in packets),default=0)),demo=field.get('satellite_demo'))


def livestock_advice(field,brief=None):
    brief=build_24_dataset_brief(field) if brief is None else brief
    samples=brief.get('thi_pairs',[])
    def thi(t,rh):
        return 1.8*t+32-(.55-.0055*rh)*(1.8*t-26)
    idx=max((thi(t,rh) for t,rh in samples),default=None)
    heads=field.get('livestock_heads',0)
    area=field['hectares']
    reading=brief['imported'].get('MODIS / VIIRS',{})
    ndvi=reading.get('metrics',{}).get('ndvi')
    if ndvi is not None and (date.fromisoformat(brief['date'])-date.fromisoformat(reading['observed'])).days not in range(31):
        ndvi=None
    moisture=brief['forecast'].get('weighted_soil_0_27_m3_m3')
    measured=field.get('forage_dm_kg_ha')
    available=(measured if measured is not None else
               round(500+1600*min(1,max(0,(ndvi-.2)/.6))) if ndvi is not None else
               round(600+900*min(1,max(0,(moisture-.10)/.25))) if moisture is not None else None)
    usable=available*.4 if available is not None else None
    power=brief['power'].get('ALLSKY_SFC_SW_DWN')
    temp=brief['forecast']['max_c']
    if temp is None and brief['power'].get('T2M'):
        temp=brief['power']['T2M']['value']
    # Planning basis: one 450 kg animal unit per head, 2.5% of body weight in dry matter per day.
    demand=heads*11.25
    water_per_head=80 if idx is not None and idx>=72 else 60 if temp is not None and temp>=28 else 45
    credit={k:round(heads*rate*.35*fraction,1) for k,rate,fraction in (
        ('n',55,.35),('p',12,.65),('k',40,.80))}
    return dict(heads=heads,hectares=area,thi=round(idx,1) if idx is not None else None,
                status='critical' if idx is not None and idx>=72 else 'attention' if idx is None else 'optimal',
                ndvi=ndvi,forage_dm_kg_ha=available,forage_green_kg=round(available*area/.25) if available is not None else None,
                hay_kg=round(available*area/.85) if available is not None else None,
                forage_basis='field_entry' if measured is not None else 'NDVI planning proxy' if ndvi is not None else 'soil-moisture planning proxy' if available is not None else 'unavailable',
                stocking_ua_ha=round(heads/area,2),capacity_ua_ha_30d=round(usable/(11.25*30),2) if usable is not None else None,
                grazing_days=round(usable*area/demand,1) if usable is not None and demand else None,
                water_l_day=heads*water_per_head,water_l_head_day=water_per_head,manure_credit_kg_year=credit,
                nasa_radiation=power,temperature_reference_c=temp)


def rural_infrastructure(field,brief=None):
    brief=build_24_dataset_brief(field) if brief is None else brief
    power=brief['power'].get('ALLSKY_SFC_SW_DWN')
    solar=round(power['value']*10*.20*.78,2) if power is not None else None
    terrain=brief['terrain'] or {}
    slope=terrain.get('slope_pct')
    imerg=brief['imported'].get('GPM IMERG',{})
    rain=imerg.get('metrics',{}).get('rain_mm') if imerg.get('observed')==brief['date'] else None
    source='GPM IMERG' if rain is not None else 'Open-Meteo forecast'
    if rain is None:
        rain=brief['forecast']['rain_mm']
    road='attention' if rain is None or slope is None else 'critical' if rain>=35 or (rain>=20 and slope>=8) else 'attention' if rain>=10 or slope>=8 else 'optimal'
    humidity=brief['forecast']['humid_hours']
    storage='critical' if humidity>=10 else 'attention' if humidity>=6 or brief['forecast']['max_c'] is None else 'optimal'
    return dict(solar_kwh_day_per_10m2=solar,power_source=power,field_slope_pct=slope,rain_mm=rain,
                rain_source=source if rain is not None else None,road_screen=road,storage_screen=storage,
                humid_hours=humidity,crop_storage_moisture_pct=crop_for(field['crop'])['moisture'])


def monte_carlo_finance(field,runs=1000,brief=None,seed=None):
    if not 100<=runs<=10000:
        raise ValueError('Runs must be between 100 and 10000')
    brief=build_24_dataset_brief(field) if brief is None else brief
    daily=brief['era5']
    temperatures=daily.get('temperature_2m_max',[])
    rainfall=daily.get('precipitation_sum',[])
    # Resample paired seven-day blocks to preserve heat/rain dependence.
    weeks=[]
    for i in range(0,min(len(temperatures),len(rainfall))-6,7):
        block_t,block_r=temperatures[i:i+7],rainfall[i:i+7]
        if all(v is not None for v in block_t+block_r):
            weeks.append((statistics.mean(block_t),sum(block_r)))
    weather_source='ERA5 weekly bootstrap' if weeks else 'assumed weather variability'
    if not weeks:
        power=(field_cache(field)['NASA POWER'].get('data') or {})
        paired=[(value,power['PRECTOTCORR'][d]) for d,value in power.get('T2M',{}).items()
                if value is not None and value>-900 and d in power.get('PRECTOTCORR',{})
                and power['PRECTOTCORR'][d] is not None and power['PRECTOTCORR'][d]>=0]
        if len(paired)>=3:
            weeks=[(t,r*7) for t,r in paired]
            weather_source='NASA POWER paired daily anomalies'
    base_t=statistics.median(t for t,_ in weeks) if weeks else 26
    base_r=statistics.median(r for _,r in weeks) if weeks else 20
    crop=crop_for(field['crop'])
    area=field['hectares']
    rng=random.Random(seed)
    margins=[]
    for _ in range(runs):
        t,r=rng.choice(weeks) if weeks else (rng.gauss(base_t,4),max(0,rng.gauss(base_r,15)))
        climate=min(1.16,max(.48,1-.018*max(0,t-base_t)-.004*max(0,base_r-r)-.002*max(0,r-base_r-30)+rng.gauss(0,.06)))
        price=rng.triangular(.82,1.18,1)
        cost=rng.triangular(.90,1.17,1.03)
        margins.append(area*(crop['yield_t']*climate*crop['price']*price-crop['cost']*cost))
    margins.sort()
    def q(p):
        i=(runs-1)*p
        k=math.floor(i)
        return round(margins[k]+(margins[min(k+1,runs-1)]-margins[k])*(i-k),2)
    histogram=margin_histogram(margins)
    return dict(runs=runs,histogram=histogram,p10=q(.10),p50=q(.50),p90=q(.90),p05=q(.05),
                probability_profitable_pct=round(100*sum(m>0 for m in margins)/runs,1),
                var95_usd=round(max(0,-q(.05)),2),weather_basis=weather_source,weather_samples=len(weeks),
                scope='crop_margin_only',currency='USD')


def margin_histogram(values):
    low,high=min(values),max(values)
    if low==high:
        low,high=low-.5,high+.5
    # Put zero on a bin edge so gains and losses are never grouped under one color.
    if low<0 and high==0:
        high=.01
    if low<0<high:
        negative_bins=max(1,min(13,round(14*(-low)/(high-low))))
        positive_bins=14-negative_bins
        boundaries=[low+(-low)*i/negative_bins for i in range(negative_bins)]+[0.0]+[
            high*i/positive_bins for i in range(1,positive_bins+1)]
    else:
        boundaries=[low+(high-low)*i/14 for i in range(14)]+[high]
    counts=[0]*14
    from bisect import bisect_right
    for value in values:
        counts[min(13,bisect_right(boundaries,value)-1)]+=1
    return [dict(min_usd=boundaries[i],max_usd=boundaries[i+1],count=count,
                 outcome='loss' if boundaries[i]<0 else 'gain')
            for i,count in enumerate(counts)]


def build_ontology(field):
    brief=build_24_dataset_brief(field)
    diagnostic=field_diagnosis(field,brief)
    livestock=livestock_advice(field,brief)
    infra=rural_infrastructure(field,brief)
    risk=monte_carlo_finance(field,brief=brief,seed=47)
    season=field.get('sequence',[])
    buyers=buyers_ranked()
    score={'optimal':0,'attention':.5,'critical':1}
    health_status=livestock['status'] if livestock['heads'] else diagnostic['climate_status']
    margin_state='critical' if risk['probability_profitable_pct']<50 else 'attention' if risk['probability_profitable_pct']<80 else 'optimal'
    if field.get('operation_type')=='livestock':
        margin_state='attention'
    nodes=[
        dict(id='climate_satellite',status=diagnostic['climate_status'],metrics=dict(rain_7d_mm=diagnostic['rain_7d_mm'],solar_kwh_day=infra['solar_kwh_day_per_10m2'])),
        dict(id='soil_water',status=diagnostic['water_status'],metrics=dict(ph=diagnostic['ph'],som_pct=diagnostic['som'],net_irrigation_mm=diagnostic['net_irrigation_mm'])),
        dict(id='crop_livestock_health',status=health_status,metrics=dict(thi=livestock['thi'],heads=livestock['heads'],humid_hours=diagnostic['humid_hours'])),
        dict(id='rotation_forage',status='optimal' if season or livestock['grazing_days'] else 'attention',metrics=dict(rotation_years=len(season),grazing_days=livestock['grazing_days'])),
        dict(id='cashflow_montecarlo',status=margin_state,metrics=dict(probability_pct=risk['probability_profitable_pct'],p50_usd=risk['p50'])),
        dict(id='logistics_market',status=infra['road_screen'],metrics=dict(slope_pct=infra['field_slope_pct'],buyer_net_usd_t=buyers[0]['net'] if buyers else None)),
    ]
    links=[('climate_satellite','soil_water',.85),('climate_satellite','crop_livestock_health',.8),
           ('soil_water','rotation_forage',.7),('crop_livestock_health','rotation_forage',.65),
           ('rotation_forage','cashflow_montecarlo',.75),('climate_satellite','logistics_market',.65),
           ('logistics_market','cashflow_montecarlo',.55)]
    by_id={n['id']:n for n in nodes}
    edges=[]
    propagated={}
    order=('climate_satellite','soil_water','crop_livestock_health','rotation_forage','logistics_market','cashflow_montecarlo')
    for node_id in order:
        node=by_id[node_id]
        incoming=[dict(source=a,target=b,weight=w,modeled_pressure=round(propagated[a]*w,2))
                  for a,b,w in links if b==node_id]
        edges.extend(incoming)
        node['incoming_pressure']=max((e['modeled_pressure'] for e in incoming),default=0)
        pressure=max(score[node['status']],node['incoming_pressure'])
        propagated[node_id]=pressure
        node['propagated_status']='critical' if pressure>=.8 else 'attention' if pressure>=.45 else 'optimal'
    return dict(nodes=nodes,edges=edges,field_sector=sector(field),operation_type=field.get('operation_type','crops'),
                livestock=livestock,infrastructure=infra,finance=risk,diagnostics=diagnostic,playbook=field.get('last_playbook'),
                edge_basis='weighted screening heuristic, not calibrated causal inference')


def execute_playbook(field):
    graph=build_ontology(field)
    d,l=graph['diagnostics'],graph['livestock']
    manual='water' in field.get('manual_inputs',[])
    updated_inputs=enrich_field(field,field_cache(field))
    water=field['water'] if manual else updated_inputs['water']
    wet=d['root_proxy_m3_m3']
    rain=rural_infrastructure(field)['rain_mm']
    hold=not manual and field.get('operation_type')!='livestock' and ((wet is not None and wet>=.38) or (rain is not None and rain>=20))
    if hold:
        water=0
        updated_inputs.setdefault('auto_inputs',{})['water']={'value':0,'sector':sector(field),'source':'playbook wet-soil/rain hold'}
    heat=l['heads']>0 and l['status']=='critical'
    uv='shade, ventilation and drinking water; check animal condition' if heat else 'check root moisture before running the pump'
    plan=dict(previous_irrigation_mm=field['water'],net_irrigation_mm=water,manual_override=manual,livestock_heat_action=heat,management=uv,
              p10_usd=graph['finance']['p10'],var95_usd=graph['finance']['var95_usd'])
    alert=(f'AGRO MESH | {sector(field)} | irrigation {water:g} mm | '+('THI high; shade and water' if heat else 'verify soil moisture')).encode('utf-8')
    message=alert[:159].decode('utf-8','ignore')
    stamp=datetime.now(UTC).isoformat()
    packet=dict(id=secrets.token_hex(10),recipient='Playbook',text=message,bytes=len(message.encode()),status='queued',at=stamp)
    actual_cash=max(0,finance_snapshot(field)['cashflow_usd'])
    reserve_gap=round(max(0,graph['finance']['var95_usd']-actual_cash),2)
    steps=[dict(step='management',status='planned',**plan),dict(step='mesh',status='queued',packet=packet),
           dict(step='financial_coverage',status='calculated',cash_available_usd=actual_cash,
                reserve_gap_usd=reserve_gap,**graph['finance'])]
    updated={**updated_inputs,'water':water,'last_playbook':dict(at=stamp,sector=sector(field),
             hold_date=graph['diagnostics']['latest_forecast'] if hold else None,steps=steps),'tab':'diagnostics'}
    with database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT payload FROM kv WHERE key=?',('outbox',)).fetchone()
        packets=json.loads(row[0]) if row else []
        packets.append(packet)
        conn.executemany('INSERT OR REPLACE INTO kv VALUES (?, ?, ?)',
            [(key,json.dumps(value,ensure_ascii=False),stamp) for key,value in [('outbox',packets),('field',updated)]])
    return updated,updated['last_playbook']
