import os
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template_string, abort

app = Flask(__name__)

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Defina DEVICE_TOKEN no painel da hospedagem.
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "troque-este-token")

HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fotos recebidas</title>
<style>
body{font-family:Arial,sans-serif;max-width:1000px;margin:30px auto;padding:0 15px;background:#111;color:#eee}
h1{margin-bottom:5px}.sub{color:#aaa;margin-bottom:25px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px}
.card{background:#1d1d1d;border:1px solid #333;border-radius:12px;padding:12px}
img{width:100%;height:220px;object-fit:contain;background:#000;border-radius:8px}
.meta{font-size:13px;color:#bbb;margin-top:8px;word-break:break-all}
a{color:#8ecbff}
</style>
</head>
<body>
<h1>Documentos recebidos</h1>
<div class="sub">Fotos enviadas pela ESP32-CAM</div>
<div class="grid">
{% for f in files %}
<div class="card">
<a href="/foto/{{f}}" target="_blank"><img src="/foto/{{f}}"></a>
<div class="meta">{{f}}</div>
</div>
{% else %}
<p>Nenhuma foto recebida ainda.</p>
{% endfor %}
</div>
</body>
</html>
"""

@app.get("/")
def index():
    files = sorted(
        [p.name for p in UPLOAD_DIR.glob("*") if p.is_file()],
        reverse=True
    )
    return render_template_string(HTML, files=files)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/upload")
def upload():
    token = request.headers.get("X-Device-Token", "")
    if token != DEVICE_TOKEN:
        return jsonify(error="token invalido"), 401

    device = request.headers.get("X-Device-ID", "camera001")
    device = "".join(c for c in device if c.isalnum() or c in "-_")[:40] or "camera001"

    data = None
    ext = ".jpg"

    # Aceita JPEG bruto enviado diretamente pela ESP32.
    if request.content_type and "image/jpeg" in request.content_type:
        data = request.get_data()

    # Também aceita multipart/form-data para testes pelo PC.
    elif "photo" in request.files:
        photo = request.files["photo"]
        data = photo.read()
        ext = Path(photo.filename or "foto.jpg").suffix.lower() or ".jpg"

    if not data:
        return jsonify(error="nenhuma imagem recebida"), 400

    # Limite simples de 8 MB.
    if len(data) > 8 * 1024 * 1024:
        return jsonify(error="imagem muito grande"), 413

    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    name = f"{device}_{now}{ext}"
    (UPLOAD_DIR / name).write_bytes(data)

    return jsonify(ok=True, arquivo=name, url=f"/foto/{name}")

@app.get("/foto/<path:name>")
def foto(name):
    p = UPLOAD_DIR / name
    if not p.is_file():
        abort(404)
    return send_from_directory(UPLOAD_DIR, name)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
