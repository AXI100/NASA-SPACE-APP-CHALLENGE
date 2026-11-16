# AGRO MESH

Demo local para el NASA Space Apps Challenge. Permite comparar planes de rotación a cinco años en función del suelo, el agua, el margen y la diversidad. Interfaz y agente en inglés, español y portugués.

## Inicio en Windows

Desde PowerShell, abre la carpeta **AGRO MESH** y ejecuta:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 18765
```

Abre http://127.0.0.1:18765 en Edge o Chrome. El servidor debe estar en el mismo equipo que el navegador. El primer arranque descarga los modelos de voz en segundo plano. Para ver qué modelos están listos abre http://127.0.0.1:18765/api/bootstrap y revisa el objeto `voice`. Puedes copiar de otro equipo los directorios de los modelos en `backend/models/`. Windows necesita una voz instalada en el idioma elegido para la lectura cuando el navegador carece de una voz local.

## Preparar y usar fuera de cobertura

1. Instala las dependencias y provisiona los modelos con conexión. Si preparas otro equipo Windows x64 con Python 3.13, puedes descargar las ruedas de las dependencias con `pip wheel -r requirements.txt -w wheelhouse` y luego usar `pip install --no-index --find-links wheelhouse -r requirements.txt` en el equipo sin conexión.
2. Busca el municipio y pulsa **Sync observations**. En **Field Diagnostics → View Scientific Evidence** figuran los datos disponibles y su fecha de adquisición. Para visualizar capas sin cobertura, importa PNG, JPEG o WebP con sus límites geográficos y selecciona el mes y la capa correspondientes.
3. Activa **Simulate Airplane Mode** o desconecta el equipo. Para forzar el modo local desde el arranque, antes de ejecutar Uvicorn usa `$env:AGRO_OFFLINE='1'`. El navegador usa un service worker para los archivos de la interfaz. Las operaciones de voz, escritura y cálculo usan el servidor local. Busca Davis, Bogotá, São Paulo, Des Moines o Quito para probar geocodificación sin sincronización previa.
4. Pulsa el micrófono para grabar y vuelve a pulsarlo para terminar. En conversación continua, la detección de pausa termina el turno y la grabación se rearma después de la respuesta. El chat de texto utiliza el mismo agente.

Prueba «Cambia el tamaño a 12 hectáreas», «Quiero regenerar el suelo y ahorrar agua», «Compara eso con sembrar sorgo», «¿Y cuánto cuesta eso?», «Muéstrame la capa NDVI» o «Anota que hoy apliqué 4 sacos de compost y gasté 120 dólares». Tras registrar una cotización en **Finance**, pregunta por compradores y almacenamiento.

## Datos y modelos

- La aplicación almacena datos y acciones en `backend/agro.sqlite3`. Configura `AGRO_DB` para usar otro archivo. Las capas raster importadas se guardan en IndexedDB para ese origen del navegador. Para respaldarlas, conserva también los archivos originales y sus límites.
- **24 referencias** científicas agrupadas. Web Soil Survey y SSURGO/STATSGO2 comparten una ficha. Hay siete conectores de observaciones y uno de geocodificación, sujetos a cobertura y disponibilidad. Las demás referencias sirven para consulta o para identificar capas que puedes preparar e importar. Las capas muestran ausencia de datos cuando no hay un ráster para ese sitio y mes.
- Open-Meteo aporta el pronóstico a siete días y NASA POWER el período solicitado de 14 días. La disponibilidad y el retraso dependen de cada proveedor. El mapa en línea usa Esri para la vista Natural. El simulador impide solicitudes de teselas al entrar en modo avión.
- Los 32 perfiles representan cultivos, con atributos y valores económicos de referencia. Para cultivos personalizados se aplica un perfil genérico. La búsqueda de haz selecciona familias diferentes entre años consecutivos, deja tres campañas intermedias antes de volver a una familia botánica y puntúa cuatro prioridades. El balance de nitrógeno es acumulativo. La gráfica de carbono estima el stock suponiendo 30 cm de suelo, 1,3 t/m³ de densidad aparente y 58% de carbono en la materia orgánica.
- Riego: volumen = lámina neta × hectáreas × 10 / eficiencia. Horas = volumen / (3,6 × caudal L/s). NPK 15-15-15: calcula sacos de formulado para cubrir el mayor requerimiento relativo y devuelve las cantidades de nutrientes resultantes para revisar excedentes. Encalado: estimación lineal de planificación hasta pH 6,5, ajustada por el equivalente de carbonato cálcico. GDD de pronóstico: suma de grados sobre base 10 °C, con mínimos diarios de cero.
- La selección de cultivos, dosis y proyecciones son estimaciones. Para decisiones de campo incorpora clima local, análisis de suelo, pH tampón, historial de plagas, etiqueta del producto y precios de campaña.
- La evaluación de tierra usa margen estimado, precio, pH y materia orgánica ingresados. La VRA produce tasas ilustrativas, sin zonificación observacional. La comparación de compradores ordena cotizaciones ingresadas según USD/t netos tras flete y merma.
- El pasaporte registra evidencia y actividad locales con un ID y QR. La verificación de deforestación y eficiencia hídrica, y la aceptación para créditos o seguros, requieren evidencias y entidades externas.
- Los mensajes de hasta 159 bytes UTF-8 se encolan localmente. Puedes leer el QR con otro dispositivo o abrir el borrador mediante el cliente SMS si existe un número. El laboratorio mesh simula la retransmisión. El despacho satelital y mesh real requieren hardware, protocolo y servicio compatibles.

## Informe científico y capas

`GET /api/observations` devuelve el informe estructurado. Con `?cached=true` solo lee el caché. Open-Meteo aporta pronóstico de siete días, ET₀ de referencia y humedad volumétrica por profundidad. ERA5 aporta una serie histórica separada. NASA POWER aporta radiación, temperatura y precipitación. SRTM90m vía OpenTopoData aporta elevación y pendiente aproximada, USGS NWIS aporta lecturas regionales de estaciones y SoilGrids/NRCS aportan propiedades del suelo según cobertura. El promedio modelado de 0–27 cm se pondera por espesor cuando están disponibles los cuatro estratos. El valor regional de SMAP se mantiene por separado porque cubre otra profundidad y resolución. ET₀ y la evapotranspiración real de OpenET son variables distintas.

NASA GIBS publica capas regionales MODIS, NDVI y SMAP. Sin conexión, el mapa conserva el polígono, la cuadrícula de coordenadas y las capas raster importadas. NDRE y ECOSTRESS se pueden visualizar con un ráster importado por fecha y límites. La capa de SMAP de GIBS es una visualización regional y el agente usa los valores numéricos de observaciones importadas con procedencia.

En el diagnóstico puedes importar un archivo JSON con una observación numérica. Usa fecha de adquisición real y una referencia rastreable. Ejemplo de estructura (con valores ilustrativos que debes sustituir por la observación):

```json
{"source":"SMAP","observed":"2026-11-15","provenance":"URL o identificador del producto y píxel","metrics":{"root_m3_m3":0.22}}
```

Pares admitidos: `SMAP / root_m3_m3`, `GPM IMERG / rain_mm`, `ECOSTRESS / lst_c`, `NLDAS / GLDAS / soil_0_27_m3_m3`, `OpenET / actual_et_mm`. Las 24 referencias incluyen recursos de catálogo y fuentes sin acceso automático. El agente solo emite alertas diarias con datos fechados para ese día. Las señales de lluvia, helada, suelo húmedo y temperatura superficial requieren comprobación en el lote.

El micrófono requiere autorización inicial del navegador. En escucha continua, una pausa envía la pregunta y el agente vuelve a escuchar tras hablar. También puedes decir «Deja de escuchar». La API acepta WAV PCM e IEEE float de hasta 30 segundos y convierte la entrada a PCM16 mono a 16 kHz. Piper reproduce inglés y español y portugués utiliza el respaldo local.

## Rotaciones aprobadas

Selecciona equipo, mano de obra y demanda antes de optimizar. Se pueden revisar y modificar planes de cinco años, o solicitar cuatro años por voz. Al aprobar, se guarda la secuencia con la fecha, coordenadas y el polígono del sector. Los planes incompatibles se pueden explorar como borradores. Las coberturas, el crédito de N y el costo inicial de recuperación del suelo son estimaciones de planificación. Los ejemplos de patógenos describen posibles rupturas de hospedaje y dependen de la especie, el historial del lote y los cultivos intermedios.

## Agente local

El motor integrado interpreta las acciones del dominio y guarda los últimos 15 turnos. Si Ollama local tiene `llama3.2`, AGRO MESH lo precalienta al arrancar e intenta cada turno primero con ese modelo, historial y herramientas. Al ejecutar una acción, el agente devuelve el resultado a Ollama para redactar la respuesta. El indicador muestra el motor del último turno. Si Ollama no está disponible, el motor integrado sigue operativo. La transcripción recibe WAV PCM de 16 bits, mono, a 16 kHz. La aplicación toma la voz de síntesis del equipo o de Windows mediante pyttsx3. Los datos de conversación, audio y bitácora se procesan localmente.

Piper sintetiza en el equipo con `en_US-lessac-medium` y `es_ES-davefx-medium`. El primer arranque con conexión descarga cada modelo ONNX y su configuración en `backend/models/piper/` (unos 63 MB por voz). Comprueba los dos indicadores `piper` de `/api/bootstrap` antes de desconectar. El botón Auto-Read reproduce los WAV devueltos por el servidor. Si una voz Piper no está disponible, incluido el portugués, se usa pyttsx3 en Windows y después Web Speech con voces locales. El reconocimiento sigue usando Vosk. El ZIP contiene el código; puedes copiar la carpeta de modelos a otra instalación para evitar la descarga.

## Referencias de integración

- [Open-Meteo Forecast](https://open-meteo.com/en/docs)
- [NASA POWER](https://power.larc.nasa.gov/docs/)
- [SoilGrids](https://rest.isric.org/soilgrids/v2.0/docs)
- [Vosk Models](https://alphacephei.com/vosk/models)
- [Ollama Tool Calling](https://ollama.com/blog/tool-support)
- [Leaflet 1.9.4](https://leafletjs.com/reference.html)


## Voz híbrida y auditoría

Configura `.env` a partir de `.env.example` para habilitar ElevenLabs. Con la clave configurada y el modo avión desactivado, el servidor envía el texto de cada respuesta al servicio para producir MP3. Piper sigue siendo la primera voz en modo avión. El reconocimiento del navegador solicita repetir la frase cuando Vosk no obtiene texto. En modo avión ese reconocimiento requiere soporte local y el paquete de idioma instalado.

El mapa usa observaciones importadas de SMAP y ECOSTRESS cuando coinciden con el mes, y muestra la procedencia de los indicadores calculados con ERA5 o datos de pronóstico disponibles. El vigor modelado no equivale a una medición espectral NDVI/NDRE. En la cuadrícula 3×3 se muestra un valor regional compartido.

El pasaporte permite exportar datos JSON. El QR firmado incluye ID, coordenadas, cultivo, cambio modelado de carbono y eficiencia hídrica modelada. Para comprobarlo en el servidor emisor envía `POST /api/passport/verify?payload=...` con el QR como valor codificado. La firma verifica esos campos y no certifica prácticas, beneficios ni ausencia de deforestación. Las observaciones de cobertura importadas bajo NLCD usan `forest_loss_ha` y opcionalmente `period_years`; la tarjeta muestra la pérdida reportada o indica que falta historial.

## Estrategias, diagnóstico y demo NASA

Usa «Cambia estrategia a máxima ganancia», «Regenerar suelo y nitrógeno», «Ahorro extremo de agua», «Haz el diagnóstico completo de mi parcela» o «Consulta el ROI y la sensibilidad de precios». Al cambiar de ubicación se leen las fuentes disponibles y se conservan los valores editados manualmente. La lámina sugerida usa ET₀ menos precipitación de siete días; aplica un mínimo de 15 mm si hay déficit positivo. El panel de suelo indica una estimación de cal y requiere análisis de suelo y material antes de fijar dosis de composta. Las ventanas de siembra son orientativas por estación y hemisferio. Los resultados económicos y el carbono son estimaciones.

«Muestra impacto NASA» abre la telemetría. «Simula alerta satelital NASA» dibuja una observación de demostración en el mapa sin modificar los registros científicos. «Simula el salto Mesh LoRa satélite» toma un paquete ya encolado y lo marca como retransmitido en la simulación local.

## Centro de comando agropecuario

En Ajustes de finca elige cultivos, ganado o explotación integrada. Puedes decir «Tengo 40 cabezas de ganado y 8 hectáreas de maíz», «Dame un diagnóstico de ganado y pasturas», «Calcula energía solar y caminos rurales», «Ejecuta Monte Carlo» o «Ejecutar playbook autónomo». El grafo propaga señales mediante pesos heurísticos, no inferencia causal validada. El playbook guarda el ajuste de riego planificado, encola un aviso local y estima la brecha de reserva. No acciona equipos, radios ni pasarelas.

El THI usa pares horarios de temperatura y humedad de Open-Meteo y activa atención crítica desde 72. Las estimaciones de agua, estiércol y pastura usan supuestos documentados en el motor, incluidas unidades animales de 450 kg, 11,25 kg de materia seca por día y 40% de aprovechamiento. La biomasa se estima con un NDVI importado bajo MODIS / VIIRS o con humedad modelada cuando está disponible, y puede introducirse con `forage_dm_kg_ha`. NASA POWER se usa como referencia térmica y radiación, y el solar supone 20% de eficiencia del panel y factor de sistema 0,78. IMERG importado y pendiente SRTM proveen un filtro regional de caminos; no sustituyen inspección ni medición de grano. Monte Carlo remuestrea climatología ERA5, recurre a anomalías emparejadas de NASA POWER o a supuestos identificados, y estima el margen del cultivo activo, sin modelar todavía el margen ganadero ni una cobertura de seguro vinculante.

La cámara procesa la imagen en el navegador, envía JPEG a 1024 px al servidor local y acompaña un muestreo RGB para calcular proporciones HSV en Python. Con Ollama Vision, solo el modelo local recibe la foto. Si solo hay un modelo de texto, recibe métricas y contexto; las proporciones incluyen fondo e iluminación y no identifican lesiones. El botón de sol activa el tema de alto contraste. Si Vosk queda sin texto, el navegador utiliza la transcripción capturada en paralelo cuando existe. En modo avión solo se usa reconocimiento del navegador si está disponible el procesamiento local.

## Panel ejecutivo y visión local

El panel ejecutivo presenta clima y satélite, suelo y riego, y ganancias y riesgo. En Finanzas se muestran cuatro resultados de la simulación en lenguaje cotidiano. El botón de contraste solar sigue en la barra superior.

Cuando Ollama ofrece `llama3.2-vision`, el servidor intenta precalentarlo con una retención de 30 minutos y admite hasta 120 segundos de espera en las solicitudes. Si el motor local rechaza ese modelo, AGRO MESH intenta otro modelo visual instalado y después interpreta únicamente las métricas HSV mediante Ollama de texto o reglas locales. Ollama 0.40.16 devolvió el error de arquitectura `mllama` en este equipo, aun con el manifiesto oficial vigente.

El reconocimiento paralelo se inicia sin argumentos en Chrome/Edge. Conserva transcripciones parciales y finales. La ganancia automática solo aplica a señales con pico de al menos 0,005.

## Ventana de siembra, escenarios y pasaporte firmado

La tarjeta de siembra combina el pronóstico de siete días, la humedad disponible cerca de raíces y una banda térmica orientativa de 10–35 °C. Revisa las condiciones en sitio y el calendario de la variedad, sobre todo si faltan datos. Finanzas muestra la distribución de 1.000 escenarios de margen en 14 intervalos, con cero como frontera entre pérdidas y equilibrio o ganancia.

La estimación de pérdidas evitadas usa USD 38 por saco de urea evitado, USD 0,12 por m³ de agua ahorrado y 25% del costo de campaña de la superficie cuando hay una alerta activa de datos reales. La alerta NASA simulada queda fuera de esa estimación.

La alerta de parcela se genera con datos disponibles localmente y se encola en UTF-8 con un máximo de 159 bytes. El relé simulado registra origen, destino, hora UTC y CRC16-CCITT en cada salto. El último salto representa el enlace satelital simulado.

El nuevo QR HMAC-SHA256 autentica el contenido completo emitido y guardado en el servidor local. Conserva compatibilidad de verificación para QR antiguos, que cubren solo los campos del propio QR. El sello acredita la integridad del registro en el servidor emisor, no una auditoría externa de resultados de campo.
