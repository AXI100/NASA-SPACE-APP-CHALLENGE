import base64
import binascii
import colorsys
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, UTC
from uuid import uuid4
import requests
from . import nasa_engine as science

TABS = dict(rotation='rotation|rotacion|rotacao',map='map|mapa',diagnostics='diagnostics|diagnostico',
            finance='finance|finanzas|financas',community='community|comunidad|comunidade',passport='passport|pasaporte|passaporte')
PROPS = {
 'tool_livestock_and_pasture_advisor':{},
 'tool_rural_infrastructure':{},
 'tool_run_monte_carlo':{},
 'tool_execute_playbook':{},
 'tool_rotation_strategy':dict(strategy='string'),
 'tool_complete_diagnosis':{},
 'tool_nasa_impact':{},
 'tool_satellite_demo':{},
 'tool_mesh_relay':dict(packet_id='string'),
 'tool_financial_advisor_and_cashflow':dict(operation='string',amount_usd='number',quantity_t='number',unit_price_usd='number',hectares='number',scenario='string',notes='string'),
 'tool_spray_window_and_pest_doctor':dict(symptoms='string'),
 'tool_manage_and_rank_buyers':dict(operation='string',name='string',distance_km='number',price='number',freight_per_t_km='number',loss_pct='number'),
 'tool_sustainable_passport':{},
 'tool_crop_guide':dict(crop='string'),
 'tool_daily_field_advice':dict(topic='string'),
 'tool_update_field':dict(crop='string',hectares='number',location_name='string',operation_type='string',livestock_heads='integer'),
 'tool_optimize_rotation':dict(priorities='array',stress_scenario='string',length='integer',equipment='string',labor='string',market='string'),
 'tool_calculate_irrigation_npk_ph':dict(irrigation_system='string',pump_flow_ls='number'),
 'tool_evaluate_land_purchase':dict(asking_price_per_ha='number'),
 'tool_send_voice_message_or_order':dict(recipient_name='string',product='string',quantity='string',message_type='string'),
 'tool_log_field_activity':dict(activity_type='string',input_used='string',cost_usd='number',notes='string'),
 'tool_check_market_and_postharvest':dict(crop='string',compare_crop='string'),
 'tool_alert_report':{},
 'tool_navigate_and_control_ui':dict(tab_name='string',map_layer='string',mode='string',offline='boolean',continuous='boolean')}
TOOLS = [dict(type='function',function=dict(name=name,description=name.replace('_',' '),
          parameters=dict(type='object',properties={p:dict(type=t,**({'items':{'type':'number'}} if t=='array' else {}))
                                                   for p,t in props.items()}))) for name,props in PROPS.items()]

OLLAMA_URL = 'http://127.0.0.1:11434'

def warm_ollama():
    with requests.Session() as client:
        client.trust_env=False
        try:
            tags=client.get(OLLAMA_URL+'/api/tags',timeout=3)
            tags.raise_for_status()
            models=[name for entry in tags.json().get('models',[])
                    if not entry.get('remote_model') and isinstance((name:=entry.get('name') or entry.get('model')),str)
                    and ':cloud' not in name.casefold()]
        except (requests.RequestException,ValueError,TypeError,AttributeError):
            return
        for family,timeout in (('llama3.2',45),('llama3.2-vision',120)):
            model=next((name for name in models if name in (family,family+':latest')),
                       next((name for name in models if name.split(':')[0]==family),None))
            if not model:
                continue
            try:
                response=client.post(OLLAMA_URL+'/api/chat',json={
                    'model':model,'messages':[],'keep_alive':'30m','stream':False},timeout=timeout)
                response.raise_for_status()
            except requests.RequestException:
                continue


def spoken_text(text):
    text=re.sub(r'(?m)^\s*(?:#{1,6}|[-*•])\s+','',text)
    text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
    return ' '.join(text.replace('#','').replace('*','').replace('`','').split())


def plain(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',value.lower()) if not unicodedata.combining(c))

def crop_in(text):
    aliases = dict(corn='maize',bananas='banana',bananos='banana',tomatoes='tomato',potatoes='potato',beans='common bean',soya='soybean')
    aliases.update({plain(alias):key for key,crop in science.CROPS.items() for alias in crop['names'].values()})
    return next((aliases[k] for k in sorted(aliases,key=len,reverse=True) if re.search(r'\b'+re.escape(k)+r'\b',text)),None)

class AgroVoiceAgent:
    def __init__(self, field, lang):
        self.field = dict(field)
        self.lang = lang
        self.evidence = science.build_24_dataset_brief(field)

    def say(self,en,es,pt):
        return dict(en=en,es=es,pt=pt)[self.lang]

    def ollama_chat(self, client, model, messages, tools=None, timeout=45):
        payload=dict(model=model,messages=messages,stream=True,keep_alive='30m',
                     options={'num_predict':320,'temperature':0.1})
        if tools:
            payload['tools']=tools
        content,calls=[],[]
        with client.post(OLLAMA_URL+'/api/chat',json=payload,timeout=timeout,stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                chunk=json.loads(line)
                if chunk.get('error'):
                    raise ValueError(str(chunk['error']))
                message=chunk.get('message') or {}
                content.append(message.get('content') or '')
                calls.extend(message.get('tool_calls') or [])
                if chunk.get('done'):
                    reply=dict(role='assistant',content=''.join(content))
                    if calls:
                        reply['tool_calls']=calls
                    return reply
        raise ValueError('Incomplete Ollama response')

    def ollama(self, client, text, history):
        resp=client.get(OLLAMA_URL+'/api/tags',timeout=3.0)
        resp.raise_for_status()
        models=resp.json().get('models',[])
        names=[name for m in models if not m.get('remote_model')
               and isinstance((name:=m.get('name') or m.get('model')),str)
               and name and ':cloud' not in name.casefold()]
        if not names:
            return None
        model=next((name for name in ('llama3.2:latest','llama3.2') if name in names),None)
        model=model or next((name for name in names if name.split(':')[0]=='llama3.2'),names[0])
        snapshot=self.field_summary()
        snapshot['planning_calculations']=science.calculations(self.field)
        brief=json.dumps(snapshot,ensure_ascii=False,separators=(',',':'))
        tone=('Use everyday terms: bags, litres, and visible symptoms.' if self.field.get('mode')!='agronomist' else
              'Use technical units and mechanisms such as cmol/kg, C/N, mm/day, m³/m³ and active-ingredient classes when relevant and supported. Never invent measurements or chemical rates.')
        messages=[dict(role='system',content=f'You are AGRO MESH, a senior field agronomist across soil science, plant nutrition, irrigation hydraulics, pathology, IPM, crop physiology, rotations, ruminant nutrition, grazing, animal heat stress, rural solar, machinery, postharvest and farm economics. Advise on any crop using sound agronomic principles, even outside the catalog. Reply in {self.lang}. Start with the direct answer and relevant numbers, finish with one practical field step. Spoken sentences only: no headings, bullets or Markdown. {tone} Base answers on this active field snapshot. For fertilizer purchase questions state the calculated 50 kg NPK bag count and tonnes of lime, identify both as planning estimates based on field inputs, and recommend confirming requirements with soil tests. Separate measured, entered and forecast values. Use tools only for explicit actions. Data: {brief}.')]
        for turn in history[-4:]:
            messages.extend([dict(role='user',content=turn['text']),dict(role='assistant',content=turn['reply'])])
        known,hint=self.intent(text, conversational=True)
        choices=None
        if known:
            choices=[tool for tool in TOOLS if tool['function']['name']==known]
            supplied={k:v for k,v in hint.items() if v is not None}
            messages.append(dict(role='system',content=f'This request is a valid action. Call {known} now. Parsed arguments: {json.dumps(supplied,ensure_ascii=False)}. Do not say you lack access.'))
        messages.append(dict(role='user',content=text))
        return model,messages,self.ollama_chat(client,model,messages,choices)

    def ollama_turn(self, text, history):
        with requests.Session() as client:
            client.trust_env=False
            try:
                turn=self.ollama(client,text,history)
            except (requests.RequestException,ValueError,KeyError,TypeError,AttributeError):
                return None
            if not turn:
                return None
            model,messages,message=turn
            engine=f'ollama:{model.removesuffix(":latest")}'
            calls=message.get('tool_calls') or []
            if not calls:
                known,parsed=self.intent(text, conversational=True)
                if not known:
                    reply=spoken_text(message.get('content') or '')
                    if reply and re.search(r'fertiliz|fertilisant|abono|adubo|npk',plain(text)):
                        reply=self.complete_input_figures(reply)
                    return (reply,[],engine) if reply else None
                # A prose-only answer cannot perform a recognized command.
                calls=[dict(type='function',function=dict(name=known,arguments=parsed))]
                message=dict(role='assistant',content='',tool_calls=calls)
            actions, results, local_replies = [],[],[]
            for call in calls:
                function=call.get('function') or {}
                name,args=function.get('name'),function.get('arguments')
                try:
                    if not isinstance(name,str) or name not in PROPS or not isinstance(args,dict):
                        raise ValueError('Unsupported tool or arguments')
                    reply,action=self.execute(name,args,text)
                    actions.append(action)
                    result={'ok':True,'spoken_summary':reply,'result':action['result']}
                except (ValueError,KeyError,TypeError,OverflowError):
                    reply=self.say('I need valid details for that action.','Necesito datos válidos para esa acción.','Preciso de dados válidos para essa ação.')
                    result={'ok':False,'spoken_summary':reply}
                local_replies.append(reply)
                results.append(dict(role='tool',tool_name=name or 'unknown',content=json.dumps(result,ensure_ascii=False)))
            # Preserve the original assistant tool calls in Ollama's conversation protocol.
            final_messages=[
                dict(role='system',content=f'Answer the original question in {self.lang}. Use the confirmed tool results, mention failures and be concise. Plain spoken text, no Markdown.'),
                messages[-1],message,*results,
                dict(role='user',content=f'Answer my original question in {self.lang} in one or two sentences. Start with the confirmed outcome. If the action succeeded, omit apologies and claims of missing information. Confirmed spoken result: {" ".join(local_replies)}'),
            ]
            try:
                final=self.ollama_chat(client,model,final_messages)
                reply=spoken_text(final.get('content') or '')
            except (requests.RequestException,ValueError,KeyError,TypeError,AttributeError):
                reply=''
            return reply or spoken_text(' '.join(local_replies)),actions,engine

    def vision(self,text,jpeg,rgb,width,height):
        try:
            jpeg_bytes=base64.b64decode(jpeg,validate=True)
            pixels=base64.b64decode(rgb,validate=True)
        except binascii.Error as error:
            raise ValueError('Invalid image encoding') from error
        if not jpeg_bytes.startswith(b'\xff\xd8\xff') or not jpeg_bytes.endswith(b'\xff\xd9') or len(pixels)!=width*height*3:
            raise ValueError('Expected JPEG and matching RGB thumbnail')
        counts=dict(green_pct=0,yellow_pct=0,brown_pct=0)
        for r,g,b in zip(pixels[::3],pixels[1::3],pixels[2::3]):
            hue,sat,value=colorsys.rgb_to_hsv(r/255,g/255,b/255)
            hue*=360
            if sat<.18 or value<.12:
                continue
            if 70<=hue<=165:
                counts['green_pct']+=1
            elif 38<=hue<70 and value>=.38:
                counts['yellow_pct']+=1
            elif 8<=hue<45 and value<.68:
                counts['brown_pct']+=1
        metrics={k:round(100*v/(width*height),1) for k,v in counts.items()}
        reply=''
        engine='image-color-rules'
        try:
            with requests.Session() as client:
                client.trust_env=False
                response=client.get(OLLAMA_URL+'/api/tags',timeout=3)
                response.raise_for_status()
                names=[name for m in response.json().get('models',[]) if not m.get('remote_model')
                       and isinstance((name:=m.get('name') or m.get('model')),str) and ':cloud' not in name.casefold()]
                vision_families=('llama3.2-vision','llava','llava-llama3','llava-phi3','moondream','minicpm-v','qwen2.5vl','qwen3-vl','bakllava')
                multimodal=[(name,True) for family in vision_families for name in names if name.split(':')[0]==family]
                text_model=next((name for name in names if name.split(':')[0]=='llama3.2'),None)
                candidates=multimodal+([(text_model,False)] if text_model else [])
                f=self.field
                forecast=self.evidence['forecast']
                context=dict(crop=f['crop'],operation_type=f.get('operation_type','crops'),mode=f['mode'],
                             rain_mm=forecast['rain_mm'],humid_hours=forecast['humid_hours'],
                             relative_humidity=self.evidence['application_hours']['relative_humidity_2m'],
                             ph=self.evidence['soil'].get('phh2o',{'value':f['ph'],'source':'field entry'}),
                             whole_frame_HSV_metrics=metrics)
                base=f'Answer in {self.lang}. Identify visible signs and plausible alternatives, separate observation from diagnosis, and give a safe inspection or sampling step. Color fractions include background and lighting and cannot identify disease or lesions. Field context: {json.dumps(context,ensure_ascii=False)}.'
                for model,vision in candidates:
                    instructions=base
                    if not vision:
                        instructions+=' You cannot see the image. Interpret only the supplied color metrics and field data and state that limitation.'
                    message=dict(role='user',content=text)
                    if vision:
                        message['images']=[jpeg]
                    timeout=120 if model.split(':')[0]=='llama3.2-vision' else 45
                    try:
                        answer=self.ollama_chat(client,model,[dict(role='system',content=instructions),message],timeout=timeout)
                        reply=spoken_text(answer.get('content') or '')
                    except (requests.RequestException,ValueError,KeyError,TypeError,AttributeError):
                        continue
                    if reply:
                        engine=('ollama-vision:' if vision else 'ollama-color:')+model.removesuffix(':latest')
                        break
        except (requests.RequestException,ValueError,KeyError,TypeError,AttributeError):
            pass
        if not reply:
            g,y,b=(metrics[k] for k in ('green_pct','yellow_pct','brown_pct'))
            reply=self.say(f'The photo contains about {g}% green, {y}% yellow and {b}% brown pixels. Lighting and background affect these numbers. Inspect several plants or animals for shared signs before deciding what to do.',
                           f'La foto contiene aproximadamente {g}% de píxeles verdes, {y}% amarillos y {b}% marrones. La luz y el fondo afectan las cifras. Revisa varias plantas o animales para encontrar signos comunes antes de decidir.',
                           f'A foto contém cerca de {g}% de pixels verdes, {y}% amarelos e {b}% marrons. Luz e fundo afetam os números. Observe várias plantas ou animais para identificar sinais em comum antes de decidir.')
        return dict(reply=reply,engine=engine,image_metrics=metrics,updated_state=self.field,actions=[])

    def field_summary(self):
        f=self.field
        forecast=self.evidence['forecast']
        soil=self.evidence['soil']
        return dict(location=f['location_name'],crop=science.crop_for(f['crop'])['names'][self.lang],hectares=f['hectares'],
                    soil_ph=soil.get('phh2o') or {'value':f.get('ph'),'source':'field entry'},
                    organic_matter_pct=soil.get('organic_matter') or {'value':f.get('som'),'source':'field entry'},
                    rain_forecast_mm=forecast['rain_mm'],soil_moisture_m3_m3=forecast['soil_m3_m3'],
                    srtm=self.evidence.get('terrain'),cashflow=science.finance_snapshot(f),
                    operation_type=f.get('operation_type','crops'),livestock_heads=f.get('livestock_heads',0),
                    active_rotation=[science.crop_for(k)['names'][self.lang] for k in f.get('sequence',[])],
                    observation_date=self.evidence['date'],
                    sources={x['name']:x['status'] for x in self.evidence['evidence'] if x['status'] in ('available','imported')})

    def intent(self, text, *, conversational=False):
        t = plain(text)
        crop = crop_in(t)
        amount = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:ha\b|hectar)',t)
        number = re.search(r'\d+(?:[.,]\d+)?',t)
        value = float(number[0].replace(',','.')) if number else None
        heads=re.search(r'\b(\d+)\s*(?:(?:head|cabezas?|cabecas?)\s*(?:of|de)?\s*)?(?:cattle|livestock|cows?|ganado|gado|vacas?)\b',t)
        if heads and re.search(r'i have|tenho|tengo|set|configure|cambia|altere',t):
            return 'tool_update_field',dict(livestock_heads=int(heads[1]),operation_type='integrated' if amount or crop else 'livestock',
                    hectares=float(amount[1].replace(',','.')) if amount else None,crop=crop)
        if re.search(r'operat|perfil|mode|modo',t):
            operation='integrated' if re.search(r'integrat|integrad|mixt|misto',t) else 'livestock' if re.search(r'livestock|ganader|gado|pecuaria',t) else 'crops' if re.search(r'crop|cultiv|agricultur',t) else None
            if operation:
                return 'tool_update_field',dict(operation_type=operation)
        if re.search(r'playbook|plano autonomo|plan autonomo',t) and re.search(r'run|execute|ejecut|execut|activ',t):
            return 'tool_execute_playbook',{}
        if re.search(r'monte carlo|\bvar\b|riesgo de margen|risco de margem',t):
            return 'tool_run_monte_carlo',{}
        if re.search(r'solar|caminos? rur|estrada|rural road|silo|milk cool|enfriamiento lact|resfriamento',t):
            return 'tool_rural_infrastructure',{}
        if re.search(r'pastur|pastor|grazing|forage|forraje|forragem|livestock|ganado|ganader|pecuaria|rebanho|manure|estiercol|esterco|\bthi\b',t):
            return 'tool_livestock_and_pasture_advisor',{}
        if re.search(r'simulat|simula|demo',t) and re.search(r'satellite.*alert|alert.*sat[eé]lit|alert.*nasa',t):
            return 'tool_satellite_demo',{}
        if re.search(r'impact.*nasa|nasa.*impact|telemetr',t):
            return 'tool_nasa_impact',{}
        if re.search(r'mesh|lora|relay|retransmi|relevo',t) and re.search(r'simulat|simula|relay|retransmi|salto|envia',t):
            return 'tool_mesh_relay',{}
        if re.search(r'full diagnos|complete diagnos|diagnostico complet|diagnostique.*complet|diagnos.*toda',t):
            return 'tool_complete_diagnosis',{}
        if re.search(r'maxim.*(?:profit|ganancia|lucro)|(?:maximum|maxima|maximo) (?:profit|ganancia|lucro)',t):
            return 'tool_rotation_strategy',{'strategy':'profit'}
        if re.search(r'(?:extreme|extremo).*?(?:water|agua)|(?:water|agua).*?(?:extreme|extremo)',t):
            return 'tool_rotation_strategy',{'strategy':'water'}
        if re.search(r'regener.*(?:soil|suelo|solo).*(?:nitrogen|nitrogenio)|soil and nitrogen',t):
            return 'tool_rotation_strategy',{'strategy':'soil'}
        if re.search(r'\broi\b|sensibilid.*(?:precio|preco)|price sensitivity|break.even|punto de equilibrio|ponto de equilibrio',t):
            return 'tool_financial_advisor_and_cashflow',{'operation':'summary','notes':'sensitivity'}
        dollars=re.search(r'(?:\$\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:dollars?|dolares|usd))',t)
        money=float(next(filter(None,dollars.groups())).replace(',','.')) if dollars else None
        buyer=re.search(r'(?:add (?:the )?buyer|agrega (?:al )?comprador|añade (?:al )?comprador|adicione (?:o )?comprador)\s+(.+?)\s+(?:at|a)\s+(\d+(?:[.,]\d+)?)\s*(?:km|kil[oóô]met|quil[oóô]met)',text,re.I)
        if buyer and money is not None:
            return 'tool_manage_and_rank_buyers',dict(operation='add',name=buyer[1],distance_km=float(buyer[2].replace(',','.')),price=money)
        if re.search(r'best buyer|mejor comprador|melhor comprador|rank buyers|ranking de compradores',t):
            return 'tool_manage_and_rank_buyers',dict(operation='rank')
        if re.search(r'fumig|pulveriz|spray|manchas?|spots?|much[ao] humed|muita umidade|mildew',t):
            return 'tool_spray_window_and_pest_doctor',dict(symptoms=text)
        if re.search(r'(resume|resuma|resumen|summari|explain).*?(passport|pasaporte|passaporte)',t):
            return 'tool_sustainable_passport',{}
        if money is not None and re.search(r'\b(sold|sale|venta|venda|vend[ií]|vendi|gasto|despesa|expense|gaste|gastei|spent|paid|paguei|pague)\b',t):
            sale=bool(re.search(r'\b(sold|vendi|sale|venta|venda)\b',t))
            tonnes=re.search(r'(\d+(?:[.,]\d+)?)\s*(?:tonnes?|tons?|toneladas?)',t)
            quantity=float(tonnes[1].replace(',','.')) if tonnes else None
            return 'tool_financial_advisor_and_cashflow',dict(operation='income' if sale else 'expense',notes=text,
                quantity_t=quantity if sale else None,unit_price_usd=money if sale and quantity else None,
                amount_usd=None if sale and quantity else money)
        if re.search(r'(what if|que pasa si|e se|simula).*(drought|sequia|seca|hectar|\bha\b)|ganancia|profit|cash.?flow|fluxo de caixa|cuanto llevo gast|quanto.*gastei|how much.*spent',t):
            return 'tool_financial_advisor_and_cashflow',dict(operation='simulate' if amount or re.search('drought|sequia|seca',t) else 'summary',
                hectares=float(amount[1].replace(',','.')) if amount else None,scenario='dry' if re.search('drought|sequia|seca',t) else 'normal')
        order = re.match(r'(?:tell|dile a|diga a|avise a|mande para|avisa a)\s+([\w-]+)',text,re.I)
        if order:
            quantity=re.search(r'\d+(?:[.,]\d+)?\s*(?:pounds?|lbs?|libras?|quilos?|kg|tonnes?|toneladas?)',t)
            return 'tool_send_voice_message_or_order',dict(recipient_name=order[1],product=crop or '',
                quantity=quantity[0] if quantity else number[0] if number else '',message_type='order' if crop else 'alert')
        if re.search(r'\b(log|record|anota|registre|registra|apliquei|aplique)\b',t):
            cost = re.search(r'(?:\$\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:dollars|dolares|usd))',t)
            return 'tool_log_field_activity',dict(activity_type='fieldwork',input_used=crop or '',
                    cost_usd=float(next(filter(None,cost.groups()))) if cost else 0,notes=text)
        location = re.search(r'(?:move to|location to|ubicaci[oó]n a|localiza[cç][aã]o para|municipio de|munic[ií]pio para)\s+(.+)',text,re.I)
        if crop and re.search(r'plant|grow|sow|sembr|cultiv|\bhow to plan\b',t) and re.search(r'how|como|guide|guia',t):
            return 'tool_crop_guide',dict(crop=crop)
        if not location and re.search(r'change|set|cambia|muda|troca|altere|switch',t):
            location=re.search(r'\b(?:in|en|em)\s+([^?.!,]+)[?.!]?$',text,re.I)
        if location or ((amount or crop) and not re.search(r'rota|audit|compra|purchas|compare|compara',t) and re.search(r'change|set|cambia|muda|troca|altere|switch',t)) or (amount and not re.search(r'rota|audit|compra|purchas|compare|compara',t)):
            return 'tool_update_field',dict(hectares=float(amount[1].replace(',','.')) if amount else None,
                    crop=crop,location_name=location[1] if location else None)
        if re.search(r'brief|alert report|resumen.*alert|resumo.*alert|alertas criticas',t):
            return 'tool_alert_report',{}
        if re.search(r'(stop listening|pause (?:the )?microphone|deja de escuchar|apaga el microfono|pare de ouvir|desligue o microfone)',t):
            return 'tool_navigate_and_control_ui',dict(continuous=False)
        offline=re.search(r'airplane|modo aviao|modo avion|offline|sin conexion|sem internet|back online|modo online',t)
        if offline and re.search(r'online|offline|activ|desactiv|ativ|turn|simul|modo|enable|disable',t):
            disabled=bool(re.search(r'desactiv|desativ|disable|turn off|back online|modo online',t))
            return 'tool_navigate_and_control_ui',dict(offline=not disabled)
        if re.search(r'optimi|optimi[sz]|regener|ahorr|economizar|rotacao de|rotacion de|rotacao.*anos|rotacion.*anos|rota.*(?:years?|anos?)|haz.*rotacion|faca.*rotacao|(?:design|plan|disena|planeje|monte).*rota',t):
            weights=[90 if re.search(p,t) else 50 for p in ('soil|suelo|solo|regener','water|agua','money|dinero|lucro','divers')]
            return 'tool_optimize_rotation',dict(priorities=weights,
                length=4 if re.search(r'\b(?:4|four|cuatro|quatro)\s*(?:years?|anos?)',t) else 5,
                labor='low' if re.search('low labor|little labor|poca mano|pouca mao',t) else 'high' if re.search('high labor|mucha mano|muita mao',t) else None,
                market='broad' if re.search('amplia demanda|demanda ampla|broad demand',t) else None,
                equipment='manual' if re.search('backpack|mochila|manual',t) else 'mechanized' if re.search('no.till|siembra directa|plantio direto|mecaniz',t) else 'tractor' if re.search('tractor|trator',t) else None,
                stress_scenario='dry' if re.search('drought|sequia|seca',t) else 'normal')
        if re.search(r'pump|bomba|npk|lime|encalad|calcari|fertiliz|abono|fertilisant',t):
            if conversational and not re.search(r'^(?:calculate|calcula|calcule|calcular|run|execute)\b|\b(?:l/s|litros por segundo|liters per second)\b',t.strip(' ¿?!.')):
                return None,{}
            return 'tool_calculate_irrigation_npk_ph',dict(irrigation_system='sprinkler' if re.search('sprink|aspers',t) else 'drip',
                    pump_flow_ls=value if value and re.search(r'l/s|litros por segundo|liters per second',t) else 5)
        if re.search(r'buy land|comprar tierra|compra de tierra|comprar terra|land purchase|arriendo|arrendamento|comprar (?:este |esse |um )?(?:terreno|lote)|buy (?:this )?(?:property|plot|field)',t):
            return 'tool_evaluate_land_purchase',dict(asking_price_per_ha=value)
        if re.search(r'market|mercado|buyer|comprador|storage|almacen|armazen|postharvest|compare|compara',t):
            pieces=re.split(r'\b(?:against|versus|vs|contra|com|with)\b',t,maxsplit=1)
            if len(pieces)==2 and re.search('compare|compara',t):
                return 'tool_check_market_and_postharvest',dict(crop=crop_in(pieces[0]),compare_crop=crop_in(pieces[1]))
            return 'tool_check_market_and_postharvest',dict(crop=crop)
        layer=next((s for s in ('NDVI','NDRE','SMAP','ECOSTRESS','Natural') if re.search(r'\b'+s.lower()+r'\b',t)),None)
        if layer is None:
            layer=next((name for name,alias in [('NDVI',r'en de ve i|n d v i'),('NDRE',r'en de erre e|n d r e'),('SMAP',r'esmap|s map'),('ECOSTRESS',r'eco stress|eco estres')] if re.search(alias,t)),None)
        for tab, terms in TABS.items():
            if re.search(r'\b(?:'+terms+r')\b',t) or layer:
                mode='agronomist' if 'agronom' in t else 'farmer' if re.search('farmer|campesino|agricultor',t) else None
                return 'tool_navigate_and_control_ui',dict(tab_name='map' if layer else tab,map_layer=layer,mode=mode)
        if re.search('agronom|farmer mode|modo campesino|modo agricultor|modo produtor',t):
            return 'tool_navigate_and_control_ui',dict(mode='agronomist' if 'agronom' in t else 'farmer')
        if re.search('today|tractor|trator|hoje|hoy|melhoro|mejoro|improve.*soil',t):
            return 'tool_daily_field_advice',dict(topic=text)
        return None,{}

    def execute(self,name,args,text):
        a = {key:val for key,val in args.items() if key in PROPS[name] and val is not None}
        f = self.field
        payload = {}
        if name == 'tool_livestock_and_pasture_advisor':
            payload=science.livestock_advice(f,self.evidence)
            f['tab']='diagnostics'
            h=payload['heads']; thi=payload['thi']; water=payload['water_l_day']; days=payload['grazing_days']
            credit=payload['manure_credit_kg_year']
            reply=self.say(f'Herd: {h} head. Heat index: {thi if thi is not None else "unavailable"}. Estimated water: {water} litres per day.',
                          f'Hato de {h} cabezas. THI: {thi if thi is not None else "sin datos"}. Agua estimada: {water} litros al día.',
                          f'Rebanho de {h} cabeças. ITU: {thi if thi is not None else "sem dados"}. Água estimada: {water} litros por dia.')
            if days is not None:
                reply+=' '+self.say(f'Estimated grazing period is {days} days. Check residual forage before moving the electric fence.',
                                    f'Ocupación estimada: {days} días. Comprueba el forraje residual antes de mover la cerca eléctrica.',
                                    f'Ocupação estimada: {days} dias. Confira a forragem residual antes de mover a cerca elétrica.')
            if h:
                reply+=' '+self.say(f'Modeled manure credit: {credit["n"]} kg N, {credit["p"]} kg P and {credit["k"]} kg K per year.',
                                    f'Crédito estimado de estiércol: {credit["n"]} kg de N, {credit["p"]} kg de P y {credit["k"]} kg de K al año.',
                                    f'Crédito estimado de esterco: {credit["n"]} kg de N, {credit["p"]} kg de P e {credit["k"]} kg de K ao ano.')
            if payload['status']=='critical':
                reply+=' '+self.say('Provide shade, airflow and fresh drinking water now.','Da sombra, ventilación y agua limpia ahora.','Forneça sombra, ventilação e água limpa agora.')
            reply+=' '+self.say('Use boots and gloves when handling manure or sick animals.',
                                'Usa botas y guantes al manejar estiércol o animales enfermos.',
                                'Use botas e luvas ao lidar com esterco ou animais doentes.')
        elif name == 'tool_rural_infrastructure':
            payload=science.rural_infrastructure(f,self.evidence)
            f['tab']='map'
            solar=payload['solar_kwh_day_per_10m2']
            reply=self.say(f'Estimated solar energy per ten square metres: {solar if solar is not None else "unavailable"} kilowatt-hours per day. Check drainage and road conditions in person. Measure grain moisture before sealing the silo.',
                           f'Energía solar estimada por diez metros cuadrados: {solar if solar is not None else "sin datos"} kilovatios hora al día. Comprueba drenaje y camino in situ. Mide la humedad del grano antes de cerrar el silo.',
                           f'Energia solar estimada por dez metros quadrados: {solar if solar is not None else "sem dados"} quilowatts-hora por dia. Verifique drenagem e estrada no local. Meça a umidade do grão antes de fechar o silo.')
        elif name == 'tool_run_monte_carlo':
            payload=science.monte_carlo_finance(f,brief=self.evidence)
            f['tab']='finance'
            p10,p50,p90,prob,var=(payload[k] for k in ('p10','p50','p90','probability_profitable_pct','var95_usd'))
            reply=self.say(f'For crop margins, P10 is {p10} dollars, median {p50} and P90 {p90}. Profit in {prob} percent of scenarios. Downside VaR at 95 percent is {var} dollars. Review cash reserves before committing costs.',
                           f'Para el margen del cultivo, P10 es {p10} dólares, mediana {p50} y P90 {p90}. Hay ganancia en {prob} por ciento de escenarios. VaR al 95 por ciento: {var} dólares. Revisa la reserva de caja antes de comprometer gastos.',
                           f'Para a margem da cultura, P10 é {p10} dólares, mediana {p50} e P90 {p90}. Lucro em {prob} por cento dos cenários. VaR de 95 por cento: {var} dólares. Confira a reserva de caixa antes de comprometer despesas.')
        elif name == 'tool_execute_playbook':
            self.field,f_result=science.execute_playbook(f)
            f=self.field
            payload=f_result
            plan,mesh,risk=payload['steps']
            reply=self.say(f'Playbook saved. Planned irrigation is {plan["net_irrigation_mm"]} millimetres. Queued a {mesh["packet"]["bytes"]}-byte Mesh alert. Modeled VaR is {risk["var95_usd"]} dollars. Verify field conditions before operating equipment.',
                           f'Playbook guardado. Lámina planificada: {plan["net_irrigation_mm"]} milímetros. Aviso Mesh de {mesh["packet"]["bytes"]} bytes en cola. VaR modelado: {risk["var95_usd"]} dólares. Verifica el lote antes de manejar equipos.',
                           f'Playbook salvo. Lâmina planejada: {plan["net_irrigation_mm"]} milímetros. Alerta Mesh de {mesh["packet"]["bytes"]} bytes na fila. VaR modelado: {risk["var95_usd"]} dólares. Verifique a área antes de operar equipamentos.')
        elif name == 'tool_rotation_strategy':
            strategy=a.get('strategy')
            if strategy not in science.STRATEGIES:
                raise ValueError('Unknown rotation strategy')
            priorities,stress=science.STRATEGIES[strategy]
            years=science.rotation(f,priorities,stress)
            f.update(priorities=priorities,stress=stress,sequence=[year['crop'] for year in years],tab='rotation')
            totals=science.rotation_totals(f,years)
            payload=dict(years=years,totals=totals,strategy=strategy)
            crops=', '.join(y['names'][self.lang] for y in years)
            total=totals['margin_usd']
            reply=self.say(f'Five-year plan: {crops}. Estimated total margin, {total:,} US dollars. Review seed availability before approving.',
                           f'Plan a cinco años: {crops}. Margen total estimado de {total:,} dólares. Confirma disponibilidad de semilla antes de aprobar.',
                           f'Plano de cinco anos: {crops}. Margem total estimada de {total:,} dólares. Confira a disponibilidade de sementes antes de aprovar.')
        elif name == 'tool_complete_diagnosis':
            sources=science.observations(f)
            f.update(science.enrich_field(f,sources),tab='diagnostics')
            self.evidence=science.build_24_dataset_brief(f,sources)
            payload=science.field_diagnosis(f,self.evidence)
            reply=self.diagnosis_reply(payload)
        elif name == 'tool_nasa_impact':
            payload=science.telemetry(f)
            f['tab']='diagnostics'
            impact=payload['impact']
            reply=self.say(f'NASA impact panel open. Estimated nitrogen credit: {impact["nitrogen_kg"]} kg. The panel shows observed sources and modeled impact.',
                           f'Panel de impacto NASA abierto. Crédito de nitrógeno estimado: {impact["nitrogen_kg"]} kg. El panel muestra fuentes e impacto modelado.',
                           f'Painel de impacto NASA aberto. Crédito estimado de nitrogênio: {impact["nitrogen_kg"]} kg. O painel mostra fontes e impacto modelado.')
        elif name == 'tool_satellite_demo':
            demo=dict(date=datetime.now(UTC).date().isoformat(),lst_c=41.2,root_m3_m3=.13,simulated=True)
            f.update(satellite_demo=demo,tab='map',layer='ECOSTRESS')
            payload=dict(satellite_demo=demo)
            reply=self.say('NASA demo only. The simulated surface is 41.2 degrees Celsius and root-zone moisture is 13 percent. Check root moisture and crop wilting before irrigating.',
                           'Simulación NASA. La superficie simulada está a 41,2 grados Celsius y la humedad radicular al 13 por ciento. Comprueba la humedad en raíces y la marchitez antes de regar.',
                           'Simulação NASA. A superfície simulada está a 41,2 graus Celsius e a umidade radicular a 13 por cento. Confira a umidade na raiz e o murchamento antes de irrigar.')
        elif name == 'tool_mesh_relay':
            payload=science.relay_packet(a.get('packet_id'))
            f['tab']='community'
            b=payload['bytes']
            reply=self.say(f'Simulated relay of {b} bytes from the field to the cooperative and satellite gateway.',
                           f'Retransmisión simulada de {b} bytes de la parcela a la cooperativa y la pasarela satelital.',
                           f'Retransmissão simulada de {b} bytes da área à cooperativa e ao gateway de satélite.')
        elif name == 'tool_financial_advisor_and_cashflow':
            reply,payload=self.financial_tool(a,text)
            f['tab']='finance'
        elif name == 'tool_spray_window_and_pest_doctor':
            reply,payload=self.spray_tool(a.get('symptoms',text))
            f['tab']='diagnostics'
        elif name == 'tool_manage_and_rank_buyers':
            reply,payload=self.buyer_tool(a)
            f['tab']='finance'
        elif name == 'tool_sustainable_passport':
            payload=science.sustainable_passport(f)
            carbon=payload['carbon']['delta_t_co2e_ha']
            efficiency=payload['water']['kg_per_m3']
            count=len(payload['rotation']['years'])
            reply=self.say(f'Passport {payload["id"]}. The rotation records {count} years. Modeled water efficiency: {efficiency} kilograms per cubic metre.',
                           f'Pasaporte {payload["id"]}. La rotación registra {count} años. Eficiencia hídrica modelada: {efficiency} kilos por metro cúbico.',
                           f'Passaporte {payload["id"]}. A rotação registra {count} anos. Eficiência hídrica modelada: {efficiency} quilos por metro cúbico.')
            if carbon is not None:
                reply+=' '+self.say(f'Modeled carbon change: {carbon} tonnes of carbon dioxide equivalent per hectare.',
                                    f'Cambio de carbono modelado: {carbon} toneladas de CO₂ equivalente por hectárea.',
                                    f'Mudança de carbono modelada: {carbon} toneladas de CO₂ equivalente por hectare.')
            loss=payload['deforestation']['loss_ha']
            if loss is None:
                reply+=' '+self.say('Deforestation status needs land-cover history.','Falta el historial de cobertura para verificar deforestación.','Falta o histórico de cobertura para verificar desmatamento.')
            else:
                reply+=' '+self.say(f'The imported land-cover record reports {loss} hectares of forest loss.',
                                    f'El registro de cobertura importado reporta {loss} hectáreas de pérdida forestal.',
                                    f'O registro de cobertura importado informa {loss} hectares de perda florestal.')
            f['tab']='passport'
        elif name == 'tool_update_field':
            new = dict(f)
            if 'operation_type' in a:
                if a['operation_type'] not in ('crops','livestock','integrated'):
                    raise ValueError('Invalid operation type')
                new['operation_type']=a['operation_type']
            if 'livestock_heads' in a:
                heads=float(a['livestock_heads'])
                if not 0<=heads<=100000 or not heads.is_integer():
                    raise ValueError('Invalid head count')
                new['livestock_heads']=int(heads)
            if 'hectares' in a:
                ha=float(a['hectares'])
                if not 0 < ha <= 100000:
                    raise ValueError('Area out of range')
                new['hectares']=ha
            if 'crop' in a:
                new['crop']=str(a['crop']).strip().lower()[:80]
            if a.get('location_name') and plain(str(a['location_name']).strip())!=plain(f['location_name']):
                results=science.geocode(str(a['location_name']),f['offline'])
                if not results:
                    raise ValueError('Location unavailable')
                p=results[0]
                new.update(lat=p['latitude'],lon=p['longitude'],location_name=p['name'])
            if any(f[k]!=new[k] for k in ('crop','lat','lon')):
                new.pop('sequence',None)
            if (f['lat'],f['lon'])!=(new['lat'],new['lon']):
                new['polygon']=[]
            moved=(f['lat'],f['lon'])!=(new['lat'],new['lon'])
            if moved:
                new.pop('satellite_demo',None)
                sources=science.observations(new)
                new=science.enrich_field(new,sources)
                new['tab']='diagnostics'
                self.evidence=science.build_24_dataset_brief(new,sources)
            if a.keys() & {'operation_type','livestock_heads'}:
                new['tab']='diagnostics'
            self.field = f = new
            payload=dict(hectares=f['hectares'],location_name=f['location_name'],crop=f['crop'],
                         operation_type=f.get('operation_type','crops'),livestock_heads=f.get('livestock_heads',0))
            if moved:
                payload['diagnostics']=science.field_diagnosis(f,self.evidence)
            reply=self.say('I updated your field','Actualicé tu parcela','Atualizei sua parcela')+f": {f['location_name']}, {f['hectares']:g} ha."
            if a.keys() & {'operation_type','livestock_heads'}:
                reply+=' '+self.say(f'{payload["livestock_heads"]} cattle recorded.',f'{payload["livestock_heads"]} cabezas registradas.',f'{payload["livestock_heads"]} cabeças registradas.')
            if moved:
                reply+=' '+self.diagnosis_reply(payload['diagnostics'])
        elif name == 'tool_optimize_rotation':
            weights=[float(x) for x in a.get('priorities',[50]*4)]
            if len(weights)!=4 or any(not 0<=w<=100 for w in weights):
                raise ValueError('Invalid priorities')
            scenario=a.get('stress_scenario','normal')
            length=int(a.get('length',5))
            if length not in (4,5):
                raise ValueError('Choose four or five years')
            choices={'equipment':('manual','tractor','mechanized'),'labor':('low','medium','high'),'market':('any','broad')}
            resources={k:a[k] for k,allowed in choices.items() if k in a and a[k] in allowed}
            years=science.rotation({**f,**resources},weights,scenario,length=length)
            f.update(resources)
            f.update(tab='rotation',priorities=weights,stress=scenario,sequence=[y['crop'] for y in years])
            payload=dict(years=years)
            sequence=', '.join(y['names'][self.lang] for y in years)
            reply=self.say('Rotation with crop family breaks','Rotación con descansos por familia','Rotação com intervalos por família')+f": {sequence}."
        elif name == 'tool_calculate_irrigation_npk_ph':
            flow=float(a.get('pump_flow_ls',5))
            if not 0 < flow <= 10000:
                raise ValueError('Invalid flow')
            payload=science.calculations(f,a.get('irrigation_system','drip'),flow)
            f.update(tab='diagnostics',irrigation_system=payload['system'],pump_flow_ls=flow)
            h,b,l=(payload[k] for k in ('pump_hours','npk_bags','lime_t'))
            reply=self.say(f'Planning estimate: {h} pump hours, {b} bags of 50 kg and {l} tonnes of lime.',
                           f'Estimación: {h} horas de bomba, {b} sacos de 50 kg y {l} toneladas de cal.',
                           f'Estimativa: {h} horas de bomba, {b} sacos de 50 kg e {l} toneladas de calcário.')
        elif name == 'tool_evaluate_land_purchase':
            price=float(a.get('asking_price_per_ha',0))
            if not 0 < price <= 1e8:
                raise ValueError('Enter a positive USD price per hectare')
            payload=science.audit(f,price)
            f.update(tab='finance',asking_price=price)
            decision=dict(buy=self.say('consider buying','considera comprar','considere comprar'),
                          negotiate=self.say('negotiate','negocia','negocie'),reject=self.say('pass','descarta','descarte'))[payload['decision']]
            reply=self.say('Preliminary score','Puntuación preliminar','Pontuação preliminar')+f" {payload['score']}/100. {decision.capitalize()}."
            recovery=payload['recovery_usd_ha']
            reply+=' '+self.say(f'First-year soil recovery estimate: USD {recovery:,.0f}/ha.',
                                f'Recuperación estimada del suelo el primer año: USD {recovery:,.0f}/ha.',
                                f'Recuperação estimada do solo no primeiro ano: USD {recovery:,.0f}/ha.')
        elif name == 'tool_send_voice_message_or_order':
            if not a.get('recipient_name'):
                raise ValueError('Recipient required')
            product = science.crop_for(str(a.get('product','')))['names'][self.lang]
            quantity = str(a.get('quantity','')).strip()
            message = self.say(f'Ready: {quantity} {product}.',f'Listo: {quantity} de {product}.',
                               f'Pronto: {quantity} de {product}.') if product and quantity and a.get('message_type')=='order' else text
            message = message.encode('utf-8')[:159].decode('utf-8','ignore')
            payload=dict(id=uuid4().hex,recipient=str(a['recipient_name'])[:80],text=message,
                         bytes=len(message.encode()),status='queued',type=a.get('message_type','alert'))
            science.append('outbox',payload)
            f.update(tab='community')
            reply=self.say('Saved in the local outbox for transfer.','Guardé el mensaje en la bandeja local para transferirlo.',
                           'Salvei a mensagem na caixa de saída local para transferência.')
        elif name == 'tool_log_field_activity':
            cost=float(a.get('cost_usd',0))
            if not 0 <= cost <= 1e8:
                raise ValueError('Invalid cost')
            payload=dict(id=uuid4().hex,at=datetime.now(UTC).isoformat(),type='expense',amount_usd=cost,activity_type=str(a.get('activity_type','fieldwork'))[:80],
                         input_used=str(a.get('input_used',''))[:120],cost_usd=cost,notes=str(a.get('notes',text))[:1000])
            science.append('ledger',payload)
            f.update(tab='finance')
            reply=self.say('I recorded the activity and its cost.','Registré la labor y su costo.','Registrei a atividade e seu custo.')
        elif name == 'tool_check_market_and_postharvest':
            key=str(a.get('crop',f['crop'])).lower()
            crop=science.crop_for(key)
            buyers=science.buyers_ranked()
            payload=dict(crop=key,moisture=crop['moisture'],price=crop['price'],buyers=buyers[:3])
            f.update(tab='finance',market_crop=key)
            moisture = f"{crop['moisture']}%" if crop['moisture'] else self.say('not available','sin dato','sem dado')
            label=crop['names'][self.lang]
            reply=self.say(f'{label}: reference USD {crop["price"]}/t. Storage moisture: {moisture}.',
                           f'{label}: referencia USD {crop["price"]}/t. Humedad de almacenamiento: {moisture}.',
                           f'{label}: referência USD {crop["price"]}/t. Umidade de armazenamento: {moisture}.')
            if buyers:
                best=buyers[0]
                reply+=' '+self.say(f"Best entered quote: {best['name']}, USD {best['net']}/t net.",
                                   f"Mejor cotización registrada: {best['name']}, USD {best['net']}/t netos.",
                                   f"Melhor oferta registrada: {best['name']}, USD {best['net']}/t líquidos.")
            baseline=str(a.get('compare_crop',f['crop'])).lower()
            if (a.get('compare_crop') or re.search('compare|compara',plain(text))) and key!=baseline:
                base=science.crop_for(baseline)
                payload['comparison']=dict(crop=baseline,price=base['price'],margin_per_ha=base['price']*base['yield_t']-base['cost'])
                reply+=' '+self.say(f'{base["names"][self.lang]} reference price: USD {base["price"]}/t.',
                                    f'Precio de referencia de {base["names"][self.lang]}: USD {base["price"]}/t.',
                                    f'Preço de referência de {base["names"][self.lang]}: USD {base["price"]}/t.')
                delta=crop['water']-base['water']
                diff=(crop['price']*crop['yield_t']-crop['cost'])-(base['price']*base['yield_t']-base['cost'])
                reply+=' '+self.say(f'Compared with {base["names"][self.lang]}: {delta:+} mm per season and USD {diff:+.0f}/ha margin.',
                    f'Frente a {base["names"][self.lang]}: {delta:+} mm por campaña y USD {diff:+.0f}/ha de margen.',
                    f'Em relação a {base["names"][self.lang]}: {delta:+} mm por safra e USD {diff:+.0f}/ha de margem.')
        elif name == 'tool_crop_guide':
            key= crop_in(plain(str(a.get('crop',f['crop'])))) or str(a.get('crop',f['crop'])).lower()
            payload=science.crop_for(key)
            c=payload
            group=dict(cereal=('cereal','cereal','cereal'),legume=('legume','leguminosa','leguminosa'),
                       leaf=('leaf crop','cultivo de hoja','cultura folhosa'),root=('root crop','raíz o tubérculo','raiz ou tubérculo'),
                       fruit=('fruit or oilseed','fruto u oleaginosa','fruto ou oleaginosa'))
            season=dict(cool=('cool','fría','fria'),warm=('warm','cálida','quente'),year_round=('year-round','todo el año','ano todo'))
            weeds=dict(high=('high','alta','alta'),medium=('medium','media','média'),low=('low','baja','baixa'))
            label=c['names'][self.lang]
            if key not in science.CROPS:
                raise ValueError('No crop profile')
            kind=self.say(*group[c['group']]);when=self.say(*season[c['season']]);weed=self.say(*weeds[c['weed_suppression']])
            metrics=f"{c['sow']} cm / {c['sow_inches']} in · {c['water']} mm · {c['n']:+} kg N/ha · {c['yield_t']} t/ha"
            reply=self.say(f'{label} is a {kind} ({c["family"]}). Season: {when}. Reference sowing depth, seasonal water, net nitrogen and expected yield:',
                           f'{label} pertenece a {c["family"]}. Grupo: {kind}. Estación: {when}. Profundidad de siembra, agua por campaña, balance de N y rendimiento de referencia:',
                           f'{label} pertence a {c["family"]}. Grupo: {kind}. Estação: {when}. Profundidade de semeadura, água por safra, balanço de N e rendimento de referência:')+' '+metrics+'. '+self.say(f'Weed suppression: {weed}.',f'Supresión de malezas: {weed}.',f'Supressão de plantas daninhas: {weed}.')
        elif name == 'tool_daily_field_advice':
            reply,payload=self.daily_advice(str(a.get('topic',text)))
        elif name == 'tool_alert_report':
            reply,critical=self.briefing()
            payload=dict(critical=critical,brief=self.evidence)
        else:
            if isinstance(a.get('continuous'),bool):
                payload['continuous']=a['continuous']
            if isinstance(a.get('offline'),bool):
                f['offline']=a['offline'] or os.getenv('AGRO_OFFLINE')=='1'
                payload['offline']=f['offline']
            if a.get('tab_name') in TABS:
                f['tab']=a['tab_name']
            if a.get('map_layer') in ('Natural','NDVI','NDRE','SMAP','ECOSTRESS'):
                f.update(tab='map',layer=a['map_layer'])
            if a.get('mode') in ('farmer','agronomist'):
                f['mode']=a['mode']
            if payload.get('continuous') is False:
                reply=self.say('Continuous listening paused.','Escucha continua pausada.','Escuta contínua pausada.')
            elif 'offline' in payload:
                reply=self.say('Airplane mode is on.','Modo avión activado.','Modo avião ativado.') if f['offline'] else self.say('Data syncing is enabled.','Sincronización habilitada.','Sincronização habilitada.')
            else:
                reply=self.say('I updated the view.','Cambié la vista.','Atualizei a visualização.')
        if name!='tool_execute_playbook':
            science.put('field',f)
        return reply,dict(tool=name,result=payload,tab=f['tab'])

    def diagnosis_reply(self,d):
        c=d['calculations']
        p,s,h,w,l,u=(d['ph'],d['som'],c['pump_hours'],c['water_m3'],c['lime_t'],d['humid_hours'] if d['humid_hours'] is not None else '—')
        return self.say(f'Field diagnosis: pH {p}, organic matter {s} percent. Planning estimates are {l} tonnes of lime and {h} pump hours for {w} cubic metres. High humidity hours: {u}. Check root moisture and use a soil test to confirm lime before applying.',
                        f'Diagnóstico: pH {p}, materia orgánica {s} por ciento. Estimación de {l} toneladas de cal y {h} horas de bomba para {w} metros cúbicos. Horas de humedad alta: {u}. Comprueba la humedad en raíces y confirma la cal con análisis de suelo.',
                        f'Diagnóstico: pH {p}, matéria orgânica {s} por cento. Estimativa de {l} toneladas de calcário e {h} horas de bomba para {w} metros cúbicos. Horas de umidade alta: {u}. Confira a umidade radicular e confirme a cal com análise de solo.')

    def financial_tool(self,a,text):
        op=a.get('operation','summary')
        if op not in ('summary','simulate','income','expense'):
            raise ValueError('Invalid operation')
        area=float(a.get('hectares',self.field['hectares']))
        scenario=a.get('scenario','normal')
        if not 0<area<=100000 or scenario not in ('normal','dry','wet'):
            raise ValueError('Invalid scenario')
        amount=None
        if op in ('income','expense'):
            amount=(float(a['quantity_t'])*float(a['unit_price_usd']) if op=='income' and 'quantity_t' in a and 'unit_price_usd' in a
                    else float(a['amount_usd']))
            if not 0<amount<=1e8:
                raise ValueError('Invalid amount')
            amount=round(amount,2)
            science.append('ledger',dict(id=uuid4().hex,at=datetime.now(UTC).isoformat(),type=op,amount_usd=amount,
                cost_usd=amount if op=='expense' else 0,notes=str(a.get('notes',text))[:1000],crop=self.field['crop']))
        result=science.finance_snapshot(self.field,area,scenario)
        result.update(operation=op,entry_usd=amount)
        i,e,c,m=(result[k] for k in ('income_usd','expense_usd','cashflow_usd','projected_margin_usd'))
        reply=self.say(f'You have recorded USD {i:,.2f} in income and USD {e:,.2f} in expenses. Net cash is USD {c:,.2f}. Estimated production margin for {area:g} hectares is USD {m:,.2f}.',
                       f'Hay USD {i:,.2f} de ingresos y USD {e:,.2f} de gastos registrados. El saldo neto es USD {c:,.2f}. Margen de producción estimado para {area:g} hectáreas: USD {m:,.2f}.',
                       f'Você registrou USD {i:,.2f} de receitas e USD {e:,.2f} de despesas. O saldo líquido é USD {c:,.2f}. Margem de produção estimada para {area:g} hectares: USD {m:,.2f}.')
        if a.get('notes')=='sensitivity':
            low,base,high=result['sensitivity']
            roi,threshold=result['roi_pct'],result['break_even_usd_t']
            reply+=' '+self.say(f'Estimated ROI {roi} percent and break-even {threshold} dollars per tonne. Net margins at low, base and high prices: {low["margin_usd"]}, {base["margin_usd"]} and {high["margin_usd"]} dollars.',
                f'ROI estimado de {roi} por ciento y equilibrio a {threshold} dólares por tonelada. Márgenes con precios bajo, base y alto: {low["margin_usd"]}, {base["margin_usd"]} y {high["margin_usd"]} dólares.',
                f'ROI estimado de {roi} por cento e equilíbrio de {threshold} dólares por tonelada. Margens nos preços baixo, base e alto: {low["margin_usd"]}, {base["margin_usd"]} e {high["margin_usd"]} dólares.')
        if op=='simulate':
            reply+=' '+self.say('Review input costs and confirm your buyer’s net offer before committing spending.',
                                'Revisa los insumos y confirma la oferta neta del comprador antes de comprometer gastos.',
                                'Revise os insumos e confirme a oferta líquida do comprador antes de comprometer gastos.')
        return reply,result

    def buyer_tool(self,a):
        if a.get('operation')=='add':
            name=str(a.get('name','')).strip()
            distance,price,freight,loss=(float(a.get(k,d)) for k,d in
                [('distance_km',-1),('price',-1),('freight_per_t_km',.16),('loss_pct',2)])
            if not name or not (0<=distance<=30000 and 0<=price<=1e5 and 0<=freight<=1000 and 0<=loss<=100):
                raise ValueError('Invalid buyer quote')
            science.append('buyers',dict(name=name[:100],distance_km=distance,price=price,freight_per_t_km=freight,loss_pct=loss))
        buyers=science.buyers_ranked()
        if not buyers:
            return self.say('There are no buyer quotes yet.','Todavía no hay cotizaciones de compradores.','Ainda não há cotações de compradores.'),dict(buyers=[])
        name,net=buyers[0]['name'],buyers[0]['net']
        return self.say(f'{name} has the highest net offer: USD {net:.2f} per tonne after freight and losses.',
                        f'{name} deja la mejor oferta: USD {net:.2f} por tonelada tras flete y merma.',
                        f'{name} deixa a melhor oferta líquida: USD {net:.2f} por tonelada após frete e perdas.'),dict(buyers=buyers[:5])

    def spray_tool(self,symptoms):
        hours=self.evidence['application_hours']
        rain,heat,wind,gust=(hours[key] for key in ('precipitation','temperature_2m','wind_speed_10m','wind_gusts_10m'))
        forecast=self.evidence['forecast']
        factors=[]
        if rain and sum(rain)>0: factors.append(self.say('rain in the next hours','lluvia en las próximas horas','chuva nas próximas horas'))
        if heat and max(heat)>=30: factors.append(self.say('high temperature','temperatura alta','temperatura alta'))
        if gust and max(gust)>=20: factors.append(self.say('wind gusts','rachas de viento','rajadas de vento'))
        if factors:
            reply=self.say('Postpone applying because of ','Pospón la aplicación por ','Adie a aplicação por ')+', '.join(factors)+'.'
        elif all(len(v)==3 for v in (rain,heat,wind,gust)):
            reply=self.say('The next three forecast hours pass the rain, heat and gust screen. Check wind onsite, the label and its required rain-free interval.',
                           'Las próximas tres horas pasan el filtro de lluvia, calor y rachas. Verifica el viento en el lote, la etiqueta y su intervalo sin lluvia.',
                           'As próximas três horas passam pelo filtro de chuva, calor e rajadas. Verifique o vento na área, o rótulo e o intervalo sem chuva.')
        else:
            reply=self.say('Current hourly rain, heat or wind is missing. Check conditions on site before applying.',
                           'Faltan datos horarios de lluvia, calor o viento. Comprueba las condiciones en el lote antes de aplicar.',
                           'Faltam dados horários de chuva, calor ou vento. Confira as condições na área antes de aplicar.')
        family=science.crop_for(self.field['crop'])['family']
        if forecast['humid_hours']>=6 or re.search('spot|mancha|mildew|mofo',plain(symptoms)):
            reply+=' '+self.say(f'For {family}, inspect both leaf surfaces, photograph the lesions and avoid wetting the foliage until you identify the cause.',
                                f'En {family}, revisa ambas caras de la hoja, fotografía las lesiones y evita mojar el follaje hasta identificar la causa.',
                                f'Em {family}, examine os dois lados da folha, fotografe as lesões e evite molhar a folhagem até identificar a causa.')
        reply+=' '+self.say('Wear the protective equipment stated on the product label.',
                            'Usa el equipo de protección indicado en la etiqueta del producto.',
                            'Use o equipamento de proteção indicado no rótulo.')
        return reply,dict(family=family,forecast_hours=hours,factors=factors,nasa_power=self.evidence['power'])

    def complete_input_figures(self,reply):
        values=science.calculations(self.field)
        normalized=reply.replace(',','.')
        bags, lime=values['npk_bags'],f'{values["lime_t"]:g}'
        if not re.search(rf'(?<![\d.]){bags}\b',normalized):
            return self.input_estimate(include_advice=False)
        if not re.search(rf'(?<![\d.]){re.escape(lime)}(?!\d)',normalized):
            reply+=' '+self.say(f'The separate lime estimate is {lime} tonnes.',
                                f'La estimación separada de cal es de {lime} toneladas.',
                                f'A estimativa separada de calcário é de {lime} toneladas.')
        if not re.search(r'soil test|analisis de suelo|analise de solo',plain(reply)):
            reply+=' '+self.say('Use these as planning estimates and confirm both with a soil test before buying.',
                                'Usa estas cifras para planificar y confírmalas con un análisis de suelo antes de comprar.',
                                'Use esses valores para planejar e confirme com uma análise de solo antes de comprar.')
        return reply

    def input_estimate(self,include_advice=True):
        f=self.field
        c=science.calculations(f)
        n,l,h,w=(c[k] for k in ('npk_bags','lime_t','pump_hours','water_m3'))
        reply=self.say(
            f'For {f["hectares"]:g} hectares in {f["location_name"]}, the planning estimate is {n} bags of 50 kg NPK and {l:g} tonnes of lime. The modeled irrigation dose uses {w:g} cubic metres and {h:g} pump hours at 5 litres per second. Confirm the fertilizer grade and lime requirement with a soil test before buying.',
            f'Para {f["hectares"]:g} hectáreas en {f["location_name"]}, la estimación es de {n} sacos de 50 kg de NPK y {l:g} toneladas de cal. El riego modelado usa {w:g} metros cúbicos y {h:g} horas de bomba a 5 litros por segundo. Confirma la fórmula del fertilizante y la necesidad de cal con un análisis de suelo antes de comprar.',
            f'Para {f["hectares"]:g} hectares em {f["location_name"]}, a estimativa é de {n} sacos de 50 kg de NPK e {l:g} toneladas de calcário. A irrigação modelada usa {w:g} metros cúbicos e {h:g} horas de bombeamento a 5 litros por segundo. Confirme a fórmula do adubo e a necessidade de calcário com análise de solo antes de comprar.')
        return reply+' '+self.daily_advice()[0] if include_advice else reply

    def field_answer(self,text):
        q=plain(text)
        v=self.field_summary()
        if re.search(r'fertiliz|fertilisant|abono|adubo|nutri|npk|irrig|riego|pump|bomba|recommend|recomend|what should|que debo|o que devo',q):
            return self.input_estimate()
        if re.search(r'pest|plaga|praga|fung|hongo|disease|doenca|enfermedad|insect|inset',q):
            return self.spray_tool(text)[0]
        if re.search(r'density|densidad|densidade|spacing|espaciam',q):
            return self.say(f'For {v["crop"]}, select the target plant population from the variety and local water supply. Seeds per hectare equal target plants divided by germination and expected field emergence fractions. Count plants along several rows after emergence.',
                            f'Para {v["crop"]}, fija la población objetivo según variedad y agua disponible. Divide las plantas objetivo por las fracciones de germinación y emergencia de campo. Cuenta plantas en varias hileras tras la emergencia.',
                            f'Para {v["crop"]}, escolha a população-alvo pela variedade e pela água disponível. Divida a população pelas frações de germinação e emergência. Conte plantas em várias linhas após a emergência.')
        if re.search(r'margin|margen|margem|rentab|profit|lucro',q):
            return self.financial_tool({'operation':'summary'},text)[0]
        if re.search(r'hectar|area|superficie|acre',q):
            return self.say(f'Your field covers {v["hectares"]:g} hectares.',f'Tu parcela tiene {v["hectares"]:g} hectáreas.',f'Sua área tem {v["hectares"]:g} hectares.')
        if re.search(r'\bph\b|acidez|organic|organica|organico',q):
            ph,som=v['soil_ph'],v['organic_matter_pct']
            return self.say(f'pH {ph["value"]} ({ph["source"]}). Organic matter {som["value"]}% ({som["source"]}).',
                            f'pH {ph["value"]} ({ph["source"]}). Materia orgánica {som["value"]}% ({som["source"]}).',
                            f'pH {ph["value"]} ({ph["source"]}). Matéria orgânica {som["value"]}% ({som["source"]}).')
        if re.search(r'rain|lluvia|llueve|chuva|precipit',q):
            rain=v['rain_forecast_mm']
            if rain is not None:
                return self.say(f'The cached forecast shows {rain:g} mm today.',f'El pronóstico guardado indica {rain:g} mm hoy.',f'A previsão em cache indica {rain:g} mm hoje.')
            return self.say('The cache has no rain forecast for today.','El caché no tiene pronóstico de lluvia para hoy.','O cache não tem previsão de chuva para hoje.')
        if re.search(r'altitude|elevaci|elevation|altitud|pendiente|slope|decliv',q):
            terrain=v['srtm']
            if terrain:
                slope=terrain.get('slope_pct')
                return self.say(f'SRTM elevation is {terrain["elevation_m"]} m.',f'SRTM estima {terrain["elevation_m"]} m de altitud.',f'O SRTM estima {terrain["elevation_m"]} m de altitude.')+(f' {slope}%.' if slope is not None else '')
            return self.say('No SRTM sample is cached for this field.','No hay una muestra SRTM guardada para este lote.','Não há amostra SRTM em cache para esta área.')
        if re.search(r'rotaci|rotat|rota[cç]',q) and v['active_rotation']:
            return self.say('The active rotation is ','La rotación activa es ','A rotação ativa é ')+', '.join(v['active_rotation'])+'.'
        if re.search(r'humid|moist|umidade',q):
            values=[f'{depth}: {x*100:.1f}%' for depth,x in v['soil_moisture_m3_m3'].items() if x is not None]
            return ('; '.join(values)+'.') if values else self.say('No soil moisture is cached for today.','No hay humedad del suelo guardada para hoy.','Não há umidade do solo em cache para hoje.')
        return self.input_estimate()

    def daily_advice(self, topic=''):
        field=self.field
        forecast=self.evidence['forecast']
        today=self.evidence['date']
        layers=forecast['soil_m3_m3']
        moisture=next((layers[k] for k in ('3–9 cm','0–1 cm','9–27 cm') if layers[k] is not None),None)
        rain,heat,et=(forecast[k] for k in ('rain_mm','max_c','reference_et0_mm'))
        humidity=forecast['humid_hours']
        ph=self.evidence['soil'].get('phh2o',{}).get('value')
        smap=self.recent_metric('SMAP','root_m3_m3')
        imerg=self.recent_metric('GPM IMERG','rain_mm')
        thermal=self.recent_metric('ECOSTRESS','lst_c')
        power=self.evidence['power'].get('ALLSKY_SFC_SW_DWN')
        next_crop=(field.get('sequence') or [field['crop']])[0]
        label=science.crop_for(next_crop)['names'][self.lang]
        if moisture is None and rain is None and imerg is None:
            reply=self.say('Today’s local forecast is missing. Test soil at root depth before watering and check whether it crumbles before driving.',
                           'Falta el pronóstico local de hoy. Revisa la humedad a profundidad de raíz antes de regar y comprueba si el suelo se desmenuza antes de entrar con el tractor.',
                           'Falta a previsão local de hoje. Confira a umidade na raiz antes de irrigar e veja se o solo se desmancha antes de trafegar.')
        else:
            reply=self.say('Postpone tractor work and test trafficability in the field.','Pospón el tractor y comprueba la firmeza en el lote.','Adie o trator e confira a firmeza no campo.') if (moisture is not None and moisture>=.35) or max(rain or 0,imerg or 0)>=12 else self.say('Check that the soil crumbles before driving the tractor.','Comprueba que el suelo se desmenuza antes de pasar el tractor.','Verifique se o solo se desmancha antes de passar o trator.')
            if moisture is not None and moisture<.19 and rain is not None and rain<3 and (et or 0)>2:
                reply+=' '+self.say('Check root-zone moisture and irrigate if the crop is dry.','Revisa la humedad en las raíces y riega si el cultivo está seco.','Verifique a umidade na raiz e irrigue se a cultura estiver seca.')
            if humidity>=6 and (heat is None or 12<=heat<=32):
                reply+=' '+self.say('Scout foliage for fungal symptoms after prolonged humidity.','Inspecciona el follaje en busca de hongos tras la humedad prolongada.','Inspecione a folhagem em busca de fungos após a umidade prolongada.')
            for value,unit,name in [(rain,'mm',self.say('Rain','Lluvia','Chuva')),(heat,'°C',self.say('High','Máxima','Máxima')),(moisture*100 if moisture is not None else None,'%',self.say('Shallow soil moisture','Humedad superficial','Umidade superficial'))]:
                if value is not None:
                    reply+=f' {name}: {value:.1f} {unit}.'
        reply+=' '+self.say(f'Next crop to plan for: {label}.',f'Próximo cultivo para planificar: {label}.',f'Próxima cultura a planejar: {label}.')
        if smap is not None:
            reply+=f' SMAP: {smap*100:.1f}%.'
            if smap<.18 and (rain or 0)<3:
                reply+=' '+self.say('Regional root-zone moisture is low. Check roots before irrigating.',
                                    'La humedad regional en raíces es baja. Comprueba el lote antes de regar.',
                                    'A umidade regional na raiz está baixa. Confira a área antes de irrigar.')
        if imerg is not None:
            reply+=f' GPM IMERG: {imerg:.1f} mm.'
        if thermal is not None:
            reply+=f' ECOSTRESS: {thermal:.1f} °C.'
        if field['mode']=='agronomist' and power:
            reply+=f" NASA POWER: {power['value']} kWh/m²/day ({power['date']})."
        if ph is not None and ph<5.8:
            reply+=' '+self.say('Mapped pH is low. Sample before budgeting lime.','El pH cartografiado es bajo. Toma una muestra antes de presupuestar cal.','O pH mapeado é baixo. Colete uma amostra antes de orçar calcário.')
        if re.search('soil|suelo|solo|compac',plain(topic)):
            sequence=field.get('sequence')
            cover=science.rotation(field,field.get('priorities',[50]*4),field.get('stress','normal'),sequence)[0]['cover_names'] if sequence else None
            if cover:
                name=cover[self.lang]
                reply+=' '+self.say(f'Plan an intermediate cover of {name}, if local planting dates allow.',
                                    f'Planifica una cobertura intermedia de {name} si el calendario local lo permite.',
                                    f'Planeje uma cobertura intermediária de {name} se o calendário local permitir.')
            else:
                reply+=' '+self.say('Keep residues on bare soil and measure compaction before tilling.',
                                    'Conserva rastrojos sobre suelo desnudo y mide la compactación antes de labrar.',
                                    'Mantenha resíduos sobre o solo descoberto e meça a compactação antes de preparar o solo.')
        if re.search(r'tractor|trator|machinery|maquinaria|maquina',plain(topic)):
            reply+=' '+self.say('Check the seat belt and rollover protection, and use the required eye, hearing and foot protection.',
                                'Comprueba el cinturón y la protección antivuelco. Usa la protección ocular, auditiva y de pies indicada.',
                                'Confira o cinto e a proteção contra capotamento. Use a proteção ocular, auditiva e dos pés indicada.')
        return reply,dict(date=today,rain_mm=rain,max_c=heat,soil_moisture=moisture,soil_ph=ph,next_crop=next_crop)

    def recent_metric(self,source,metric):
        item=self.evidence['imported'].get(source,{})
        return item.get('metrics',{}).get(metric) if item.get('observed')==self.evidence['date'] else None

    def briefing(self):
        forecast=self.evidence['forecast']
        alerts=[]
        rain=forecast['rain_mm']
        minimum=forecast['min_c']
        moisture=forecast['soil_m3_m3']['3–9 cm']
        lst=self.recent_metric('ECOSTRESS','lst_c')
        imerg=self.recent_metric('GPM IMERG','rain_mm')
        if max(rain or 0,imerg or 0)>=35:
            alerts.append(self.say('Heavy rain signal. Check drainage and postpone field traffic.',
                                   'Señal de lluvia intensa. Revisa el drenaje y pospón el tránsito.',
                                   'Sinal de chuva forte. Confira a drenagem e adie o tráfego.'))
        if minimum is not None and minimum<=0:
            alerts.append(self.say('Frost risk. Protect sensitive crops.','Riesgo de helada. Protege los cultivos sensibles.','Risco de geada. Proteja as culturas sensíveis.'))
        if lst is not None and lst>=40:
            alerts.append(self.say('ECOSTRESS shows a hot surface. Check crop water stress on site.','ECOSTRESS indica superficie caliente. Comprueba si hay estrés hídrico en el lote.','ECOSTRESS indica superfície quente. Verifique o estresse hídrico no campo.'))
        if moisture is not None and moisture>=.35:
            alerts.append(self.say('Wet soil may compact under machinery. Check trafficability before entering.','El suelo mojado puede compactarse con maquinaria. Comprueba su firmeza antes de entrar.','O solo úmido pode compactar sob máquinas. Verifique a firmeza antes de entrar.'))
        if forecast['humid_hours']>=6:
            alerts.append(self.say('Prolonged humidity. Scout crops for fungal symptoms.','Humedad prolongada. Revisa síntomas de hongos.','Umidade prolongada. Verifique sintomas de fungos.'))
        if alerts:
            return ' '.join(alerts),True
        if all(x is None for x in (rain,minimum,moisture,lst,imerg)):
            return self.say('There is no current cached weather or thermal evidence for this field.','Faltan datos actuales de clima o temperatura superficial en el caché del lote.','Faltam dados atuais de clima ou temperatura da superfície no cache.'),False
        return self.say('No critical thresholds were crossed in the available data.','Los datos disponibles no superan los umbrales críticos.','Os dados disponíveis não ultrapassam os limites críticos.'),False

    def ask(self,text,session):
        key='chat:'+session
        history=science.get(key,[])
        turn=self.ollama_turn(text,history)
        if turn:
            reply,actions,engine=turn
            science.append(key,dict(text=text,reply=reply,actions=actions,engine=engine),15)
            return dict(reply=reply,actions=actions,updated_state=self.field,engine=engine)
        engine='offline-rules'
        name,args=self.intent(text)
        reply=None
        actions=[]
        if name:
            try:
                reply,action=self.execute(name,args,text)
                actions.append(action)
                if name=='tool_calculate_irrigation_npk_ph' and self.intent(text,conversational=True)[0] is None:
                    reply=self.input_estimate()
            except (ValueError,KeyError,TypeError,OverflowError) as error:
                reply=self.say('I need valid details for that action.','Necesito datos válidos para esa acción.','Preciso de dados válidos para essa ação.')
        elif not reply:
            t=plain(text)
            if re.search('brief|resumen|resumo',t):
                reply,_=self.briefing()
            elif re.search('cost|cuesta|custa',t):
                crop=crop_in(t) or next((a['result']['crop'] for turn in reversed(history) for a in turn.get('actions',[])
                                         if a['tool']=='tool_check_market_and_postharvest'),self.field['crop'])
                cost=science.crop_for(crop)['cost']*self.field['hectares']
                reply=self.say(f'The reference campaign budget is USD {cost:,.0f}.',
                               f'El presupuesto de referencia es USD {cost:,.0f} por campaña.',
                               f'O orçamento de referência é de USD {cost:,.0f} por safra.')
            elif re.search('no rain|no llueve|nao chover|sequ|drought',t):
                reply=self.say('Without rain, check root-zone moisture and budget irrigation against crop demand.',
                               'Sin lluvia, revisa humedad en las raíces y ajusta el riego a la necesidad del cultivo.',
                               'Sem chuva, verifique a umidade nas raízes e ajuste a irrigação à demanda da cultura.')
            else:
                reply=self.field_answer(text)
        science.append(key,dict(text=text,reply=reply,actions=actions,engine=engine),15)
        return dict(reply=reply,actions=actions,updated_state=self.field,engine=engine)

