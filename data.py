# Reference scenario values, editable in code. USD and metric units.
# id | ES | PT | family | root m | net N kg/ha | water mm | sow cm | safe storage % | USD/t | t/ha | USD/ha
ROWS = """
Maize|Maíz|Milho|Poaceae|1.5|-85|550|5|13|205|8|990
Soybean|Soja|Soja|Fabaceae|1.2|35|470|4|13|460|3|750
Sorghum|Sorgo|Sorgo|Poaceae|1.7|-55|380|3|13|215|5|610
Wheat|Trigo|Trigo|Poaceae|1.3|-65|450|4|13|240|5|680
Barley|Cebada|Cevada|Poaceae|1.1|-50|390|4|13|220|4|560
Oat|Avena|Aveia|Poaceae|1.1|-45|400|4|13|205|4|530
Rye|Centeno|Centeio|Poaceae|1.4|-38|350|3|13|230|3.5|490
Rice|Arroz|Arroz|Poaceae|0.5|-75|850|3|13|390|5.5|1350
Millet|Mijo|Painço|Poaceae|1.1|-40|305|2|12|290|2.7|480
Cowpea|Caupí|Feijão-caupi|Fabaceae|1.2|42|320|4|12|715|1.5|550
Common bean|Frijol|Feijão|Fabaceae|0.7|20|400|4|13|860|1.8|800
Alfalfa|Alfalfa|Alfafa|Fabaceae|2.0|65|650|1|16|195|8|900
Clover|Trébol|Trevo|Fabaceae|0.6|55|380|1|15|140|5|510
Field pea|Guisante|Ervilha|Fabaceae|0.8|32|385|5|14|360|2.5|600
Lentil|Lenteja|Lentilha|Fabaceae|0.6|25|290|4|13|730|1.5|570
Chickpea|Garbanzo|Grão-de-bico|Fabaceae|1.0|27|340|6|12|670|1.7|680
Peanut|Maní|Amendoim|Fabaceae|0.8|21|500|5|8|630|3.1|1100
Vetch|Veza|Ervilhaca|Fabaceae|0.9|52|340|3|13|180|4|410
Canola|Canola|Canola|Brassicaceae|1.2|-55|460|2|8|480|2.5|790
Mustard|Mostaza|Mostarda|Brassicaceae|0.9|-40|330|2|9|530|1.4|440
Radish|Rábano|Rabanete|Brassicaceae|0.8|-30|290|2||380|10|1750
Sunflower|Girasol|Girassol|Asteraceae|1.7|-43|390|5|8|445|2.5|620
Potato|Papa|Batata|Solanaceae|0.6|-95|555|10||280|24|3800
Tomato|Tomate|Tomate|Solanaceae|0.8|-85|540|1||430|36|8400
Pepper|Pimiento|Pimentão|Solanaceae|0.6|-70|510|1||780|17|6100
Pumpkin|Calabaza|Abóbora|Cucurbitaceae|0.9|-58|440|4||360|15|2400
Cucumber|Pepino|Pepino|Cucurbitaceae|0.6|-60|460|2||460|24|4900
Flax|Lino|Linho|Linaceae|0.9|-33|335|2|9|550|1.5|530
Buckwheat|Trigo sarraceno|Trigo-sarraceno|Polygonaceae|0.6|-25|300|3|13|480|1.4|400
Quinoa|Quinua|Quinoa|Amaranthaceae|0.9|-46|350|2|12|900|1.6|720
Cassava|Yuca|Mandioca|Euphorbiaceae|1.1|-50|520|8||155|20|1250
Banana|Banano|Banana|Musaceae|0.6|-125|1200|25||390|29|7100
"""
CROPS = {}
for row in ROWS.strip().splitlines():
    en,es,pt,family,root,n,water,sow,moisture,price,yield_t,cost = row.split('|')
    CROPS[en.lower()] = dict(names=dict(en=en,es=es,pt=pt), family=family,
        root=float(root), root_type='fibrous' if family in ('Poaceae','Musaceae') else 'taproot_or_storage',
        n=int(n), water=int(water), sow=int(sow), sow_inches=round(int(sow)/2.54,2),
        moisture=int(moisture) if moisture else None, price=int(price), yield_t=float(yield_t), cost=int(cost))

# SSURGO and Web Soil Survey share one entry because WSS distributes SSURGO.
CATALOG = [
('SMAP','NASA','https://smap.jpl.nasa.gov/','Soil moisture'),
('GPM IMERG','NASA','https://gpm.nasa.gov/data/imerg','Precipitation'),
('Landsat','USGS / NASA','https://www.usgs.gov/landsat-missions','Multispectral imagery'),
('MODIS / VIIRS','NASA / NOAA','https://lpdaac.usgs.gov/','Vegetation and thermal products'),
('NASA POWER','NASA','https://power.larc.nasa.gov/','Weather and radiation'),
('SRTM','NASA / USGS','https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm','Elevation'),
('ECOSTRESS','NASA','https://ecostress.jpl.nasa.gov/','Land surface temperature'),
('Ag Data Commons','USDA','https://agdatacommons.nal.usda.gov/','Research data'),
('Web Soil Survey / SSURGO / STATSGO2','USDA NRCS','https://websoilsurvey.nrcs.usda.gov/','US soil survey'),
('CropScape CDL','USDA NASS','https://nassgeodata.gmu.edu/CropScape/','Crop land cover'),
('PRISM','Oregon State University','https://prism.oregonstate.edu/','US climate grids'),
('NAIP','USDA','https://www.fsa.usda.gov/resources/programs/national-agriculture-imagery-program','Aerial imagery'),
('NLCD','USGS','https://www.usgs.gov/centers/eros/science/national-land-cover-database','US land cover'),
('NLDAS / GLDAS','NASA','https://ldas.gsfc.nasa.gov/','Land surface simulations'),
('GAEZ','FAO / IIASA','https://gaez.fao.org/','Crop suitability'),
('CoCoRaHS','CoCoRaHS','https://www.cocorahs.org/','Gauge precipitation'),
('NWIS','USGS','https://waterdata.usgs.gov/nwis','Water observations'),
('AMS Market News','USDA','https://www.ams.usda.gov/market-news','Market reports'),
('Quick Stats','USDA NASS','https://quickstats.nass.usda.gov/','Agricultural statistics'),
('OpenET','OpenET','https://openetdata.org/','Evapotranspiration'),
('LANDFIRE','USFS / USGS','https://landfire.gov/','Vegetation and disturbance'),
('GEDI','NASA','https://gedi.umd.edu/','Canopy structure'),
('Sentinel-2','ESA / Copernicus','https://sentinels.copernicus.eu/web/sentinel/missions/sentinel-2','Red-edge and optical imagery'),
('ERA5','ECMWF / Copernicus','https://www.ecmwf.int/en/forecasts/dataset/ecmwf-reanalysis-v5','Climate reanalysis')]



COOL = set('wheat barley oat rye alfalfa clover lentil chickpea vetch canola mustard radish potato flax'.split()) | {'field pea'}
HIGH_WEEDS = {'rye','buckwheat','mustard','sorghum','radish','millet'}
ROOTS = {'potato','cassava','radish'}
LEAF = {'mustard'}
HIGH_LABOR = {'tomato','pepper','cucumber','banana','pumpkin','potato','rice','cassava','radish'}
for key,crop in CROPS.items():
    family=crop['family']
    group=('cereal' if family=='Poaceae' else 'legume' if family=='Fabaceae' else
           'root' if key in ROOTS else 'leaf' if key in LEAF else 'fruit')
    crop.update(group=group, season='year_round' if key=='banana' else 'cool' if key in COOL else 'warm',
                weed_suppression='high' if key in HIGH_WEEDS else 'low' if key in HIGH_LABOR else 'medium',
                labor='high' if key in HIGH_LABOR else 'low' if key in HIGH_WEEDS|{'lentil','sunflower','flax'} else 'medium',
                equipment='tractor' if key=='rice' else 'manual',
                root_pattern='deep' if crop['root']>=1.2 else 'shallow',
                market='limited' if key in {'vetch','clover','flax','buckwheat','quinoa'} else 'broad',
                n_credit={'soybean':20,'alfalfa':45,'clover':30,'vetch':35,'cowpea':25,'field pea':20,
                          'common bean':12,'lentil':14,'chickpea':15,'peanut':12}.get(key,0))

PEST_EXAMPLES = {
    'Poaceae':('preferential grass hosts / some cereal take-all cycles','huéspedes gramíneos / algunos ciclos del mal del pie','hospedeiros gramíneos / alguns ciclos do mal-do-pé'),
    'Solanaceae':('potato early blight (Alternaria solani)','tizón temprano de la papa (Alternaria solani)','pinta-preta da batata (Alternaria solani)'),
    'Brassicaceae':('clubroot (Plasmodiophora brassicae)','hernia de las crucíferas (Plasmodiophora brassicae)','hérnia das crucíferas (Plasmodiophora brassicae)'),
    'Asteraceae':('sunflower downy mildew (Plasmopara halstedii)','mildiu del girasol (Plasmopara halstedii)','míldio do girassol (Plasmopara halstedii)'),
    'Fabaceae':('pea root rot (Aphanomyces euteiches)','pudrición de raíz del guisante (Aphanomyces euteiches)','podridão radicular da ervilha (Aphanomyces euteiches)'),
    'Cucurbitaceae':('cucumber Fusarium wilt (F. oxysporum f. sp. cucumerinum)','marchitez del pepino (F. oxysporum f. sp. cucumerinum)','murcha do pepino (F. oxysporum f. sp. cucumerinum)'),
}
