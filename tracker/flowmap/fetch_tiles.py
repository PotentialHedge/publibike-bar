import math, os, urllib.request, time
Z=14; LON0,LON1,LAT0,LAT1=8.460,8.610,47.330,47.435
def tx(lon): return (lon+180)/360*2**Z
def ty(lat): r=math.radians(lat); return (1-math.log(math.tan(r)+1/math.cos(r))/math.pi)/2*2**Z
x0,x1=int(tx(LON0)),int(tx(LON1)); y0,y1=int(ty(LAT1)),int(ty(LAT0))
SRC={'light':'Canvas/World_Light_Gray_Base','dark':'Canvas/World_Dark_Gray_Base'}
for style,path in SRC.items():
  for z,sub in [(Z,1),(Z+1,2)]:
    os.makedirs(f'tiles/{style}{z}',exist_ok=True)
    for x in range(x0*sub,(x1+1)*sub):
        for y in range(y0*sub,(y1+1)*sub):
            p=f'tiles/{style}{z}/{x}_{y}.png'
            if os.path.exists(p): continue
            req=urllib.request.Request(f'https://server.arcgisonline.com/ArcGIS/rest/services/{path}/MapServer/tile/{z}/{y}/{x}',headers={'User-Agent':'flowmap-render/1.0'})
            open(p,'wb').write(urllib.request.urlopen(req,timeout=30).read()); time.sleep(0.03)
