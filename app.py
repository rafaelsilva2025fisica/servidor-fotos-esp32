import os
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory, render_template_string, abort, redirect, url_for

app=Flask(__name__)
UPLOAD_DIR=Path(os.environ.get("UPLOAD_DIR","uploads"))
UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
DEVICE_TOKEN=os.environ.get("DEVICE_TOKEN","troque-este-token")

HTML=r"""
<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Documentos recebidos</title>
<style>
body{font-family:Arial,sans-serif;max-width:1250px;margin:28px auto;padding:0 15px;background:#111;color:#eee}
h1{margin-bottom:4px}.sub{color:#aaa;margin-bottom:22px}
.seq{border-top:3px solid #555;padding:18px 0 28px;margin-top:12px}
.seqhead{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}
.seqtitle{font-size:21px;font-weight:bold}.time{color:#bbb;margin-top:4px}
.photos{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin-top:15px}
.card{background:#1d1d1d;border:1px solid #333;border-radius:10px;padding:9px}
img{width:100%;height:170px;object-fit:contain;background:#000;border-radius:7px}
.num{font-weight:bold;margin:7px 0}.actions{display:flex;gap:6px;flex-wrap:wrap}
a.btn,button{border:0;border-radius:6px;padding:8px 10px;cursor:pointer;text-decoration:none;font-size:13px}
.open{background:#eee;color:#111}.del{background:#a5221a;color:#fff}.all{background:#76150f;color:#fff}
.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
</style></head><body>
<div class="top"><div><h1>Documentos recebidos</h1><div class="sub">Sequências enviadas pela ESP32-CAM</div></div>
{% if groups %}<form method="post" action="/excluir-todas" onsubmit="return confirm('Excluir TODAS as fotos?')"><button class="all">Excluir tudo</button></form>{% endif %}
</div>
{% for g in groups %}
<section class="seq">
 <div class="seqhead">
  <div><div class="seqtitle">SEQUÊNCIA {{g.display}}</div><div class="time">{{g.time}}</div></div>
  <form method="post" action="/excluir-sequencia/{{g.id}}" onsubmit="return confirm('Excluir esta sequência inteira?')"><button class="all">Excluir sequência</button></form>
 </div>
 <div class="photos">
 {% for p in g.photos %}
  <div class="card">
   <a href="/foto/{{p.file}}" target="_blank"><img src="/foto/{{p.file}}" loading="lazy"></a>
   <div class="num">Foto {{p.number}}</div>
   <div class="actions">
    <a class="btn open" href="/foto/{{p.file}}" target="_blank">Abrir</a>
    <form method="post" action="/excluir/{{p.file}}" onsubmit="return confirm('Excluir esta foto?')"><button class="del">Excluir</button></form>
   </div>
  </div>
 {% endfor %}
 </div>
</section>
{% else %}<p>Nenhuma sequência recebida ainda.</p>{% endfor %}
</body></html>
"""

def safe(s):
    return bool(s) and Path(s).name==s and s not in(".","..")

def parse_file(p):
    # nome: device__sequence__photoNN__timestamp.jpg
    parts=p.stem.split("__")
    if len(parts)<4: return None
    try: num=int(parts[2].replace("photo",""))
    except: num=0
    return {"file":p.name,"device":parts[0],"seq":parts[1],"number":num,"stamp":parts[3]}

@app.get("/")
def index():
    grouped=defaultdict(list)
    for p in UPLOAD_DIR.glob("*.jpg"):
        x=parse_file(p)
        if x: grouped[x["seq"]].append(x)
    groups=[]
    for sid,photos in grouped.items():
        photos.sort(key=lambda x:x["number"])
        stamp=max(x["stamp"] for x in photos)
        try:
            dt=datetime.strptime(stamp,"%Y%m%d-%H%M%S-%f").replace(tzinfo=timezone.utc)
            # Exibe horário do servidor em UTC para não fingir timezone do destinatário.
            shown=dt.strftime("%d/%m/%Y - %H:%M:%S UTC")
        except: shown=stamp
        groups.append({"id":sid,"photos":photos,"stamp":stamp,"time":shown})
    groups.sort(key=lambda g:g["stamp"],reverse=True)
    # Numeração visual: mais recente recebe o maior número acumulado
    total=len(groups)
    for i,g in enumerate(groups):
        g["display"]=f"{total-i:03d}"
    return render_template_string(HTML,groups=groups)

@app.get("/health")
def health(): return {"ok":True}

@app.post("/upload")
def upload():
    if request.headers.get("X-Device-Token","") != DEVICE_TOKEN:
        return jsonify(error="token invalido"),401
    device="".join(c for c in request.headers.get("X-Device-ID","camera001") if c.isalnum() or c in "-_")[:40] or "camera001"
    seq="".join(c for c in request.headers.get("X-Sequence-ID","sem-sequencia") if c.isalnum() or c in "-_")[:80] or "sem-sequencia"
    try: number=int(request.headers.get("X-Photo-Number","0"))
    except: number=0
    data=request.get_data() if request.content_type and "image/jpeg" in request.content_type else None
    if not data: return jsonify(error="nenhuma imagem recebida"),400
    if len(data)>8*1024*1024: return jsonify(error="imagem muito grande"),413
    stamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    name=f"{device}__{seq}__photo{number:02d}__{stamp}.jpg"
    (UPLOAD_DIR/name).write_bytes(data)
    return jsonify(ok=True,arquivo=name)

@app.get("/foto/<path:name>")
def foto(name):
    if not safe(name) or not (UPLOAD_DIR/name).is_file(): abort(404)
    return send_from_directory(UPLOAD_DIR,name)

@app.post("/excluir/<name>")
def excluir(name):
    if not safe(name): abort(400)
    p=UPLOAD_DIR/name
    if p.is_file(): p.unlink()
    return redirect(url_for("index"))

@app.post("/excluir-sequencia/<sid>")
def excluir_seq(sid):
    if not safe(sid): abort(400)
    for p in UPLOAD_DIR.glob("*.jpg"):
        x=parse_file(p)
        if x and x["seq"]==sid: p.unlink()
    return redirect(url_for("index"))

@app.post("/excluir-todas")
def excluir_todas():
    for p in UPLOAD_DIR.glob("*"):
        if p.is_file(): p.unlink()
    return redirect(url_for("index"))

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
