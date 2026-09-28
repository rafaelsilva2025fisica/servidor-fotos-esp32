import os
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    render_template_string,
    abort,
    redirect,
    url_for,
)

app = Flask(__name__)

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Continua usando o DEVICE_TOKEN que configuramos no Render.
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "Rafael@1992")


HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <title>Fotos recebidas</title>

    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 1050px;
            margin: 30px auto;
            padding: 0 15px;
            background: #111;
            color: #eee;
        }

        h1 {
            margin-bottom: 5px;
        }

        .sub {
            color: #aaa;
            margin-bottom: 20px;
        }

        .top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }

        .grid {
            display: grid;
            grid-template-columns:
                repeat(auto-fill, minmax(260px, 1fr));
            gap: 18px;
        }

        .card {
            background: #1d1d1d;
            border: 1px solid #333;
            border-radius: 12px;
            padding: 12px;
        }

        img {
            width: 100%;
            height: 220px;
            object-fit: contain;
            background: #000;
            border-radius: 8px;
        }

        .meta {
            font-size: 13px;
            color: #bbb;
            margin: 8px 0;
            word-break: break-all;
        }

        .actions {
            display: flex;
            gap: 8px;
        }

        a.btn,
        button {
            border: 0;
            border-radius: 7px;
            padding: 9px 12px;
            font-size: 14px;
            cursor: pointer;
            text-decoration: none;
        }

        .open {
            background: #e8e8e8;
            color: #111;
        }

        .del {
            background: #b3261e;
            color: white;
        }

        .all {
            background: #7a1510;
            color: white;
        }

        .empty {
            color: #aaa;
        }
    </style>
</head>

<body>

<h1>Documentos recebidos</h1>

<div class="sub">
    Fotos enviadas pela ESP32-CAM
</div>

<div class="top">

    <div>
        {{ files|length }} foto(s)
    </div>

    {% if files %}

    <form
        method="post"
        action="/excluir-todas"
        onsubmit="return confirm('Excluir TODAS as fotos?');"
    >
        <button class="all" type="submit">
            Excluir todas
        </button>
    </form>

    {% endif %}

</div>


<div class="grid">

{% for f in files %}

<div class="card">

    <a href="/foto/{{ f }}" target="_blank">

        <img
            src="/foto/{{ f }}"
            loading="lazy"
        >

    </a>

    <div class="meta">
        {{ f }}
    </div>

    <div class="actions">

        <a
            class="btn open"
            href="/foto/{{ f }}"
            target="_blank"
        >
            Abrir foto
        </a>


        <form
            method="post"
            action="/excluir/{{ f }}"
            onsubmit="return confirm('Excluir esta foto?');"
        >

            <button
                class="del"
                type="submit"
            >
                Excluir
            </button>

        </form>

    </div>

</div>

{% else %}

<div class="empty">
    Nenhuma foto recebida ainda.
</div>

{% endfor %}

</div>

</body>
</html>
"""


def safe_file(name):

    return (
        Path(name).name == name
        and name not in ("", ".", "..")
    )


# ==========================================================
# PÁGINA PRINCIPAL
# ==========================================================

@app.get("/")
def index():

    files = sorted(
        [
            p.name
            for p in UPLOAD_DIR.glob("*")
            if p.is_file()
        ],
        reverse=True,
    )

    return render_template_string(
        HTML,
        files=files,
    )


# ==========================================================
# TESTE DO SERVIDOR
# ==========================================================

@app.get("/health")
def health():

    return {
        "ok": True
    }


# ==========================================================
# RECEBER FOTO DA ESP32
# ==========================================================

@app.post("/upload")
def upload():

    token = request.headers.get(
        "X-Device-Token",
        "",
    )

    if token != DEVICE_TOKEN:

        return jsonify(
            error="token invalido"
        ), 401


    device = request.headers.get(
        "X-Device-ID",
        "camera001",
    )


    device = "".join(
        c
        for c in device
        if c.isalnum()
        or c in "-_"
    )[:40]


    if not device:
        device = "camera001"


    data = None
    ext = ".jpg"


    # JPEG bruto enviado pela ESP32

    if (
        request.content_type
        and "image/jpeg"
        in request.content_type
    ):

        data = request.get_data()


    # Também aceita multipart/form-data

    elif "photo" in request.files:

        photo = request.files["photo"]

        data = photo.read()

        ext = (
            Path(
                photo.filename
                or "foto.jpg"
            )
            .suffix
            .lower()
            or ".jpg"
        )


    if not data:

        return jsonify(
            error="nenhuma imagem recebida"
        ), 400


    # Máximo de 8 MB

    if len(data) > 8 * 1024 * 1024:

        return jsonify(
            error="imagem muito grande"
        ), 413


    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d_%H%M%S_%f"
    )


    name = (
        f"{device}_"
        f"{now}"
        f"{ext}"
    )


    (
        UPLOAD_DIR
        / name
    ).write_bytes(data)


    return jsonify(
        ok=True,
        arquivo=name,
        url=f"/foto/{name}",
    )


# ==========================================================
# ABRIR FOTO
# ==========================================================

@app.get("/foto/<path:name>")
def foto(name):

    if not safe_file(name):
        abort(400)

    p = UPLOAD_DIR / name

    if not p.is_file():
        abort(404)

    return send_from_directory(
        UPLOAD_DIR,
        name,
    )


# ==========================================================
# EXCLUIR UMA FOTO
# ==========================================================

@app.post("/excluir/<name>")
def excluir(name):

    if not safe_file(name):
        abort(400)

    p = UPLOAD_DIR / name

    if p.is_file():
        p.unlink()

    return redirect(
        url_for("index")
    )


# ==========================================================
# EXCLUIR TODAS
# ==========================================================

@app.post("/excluir-todas")
def excluir_todas():

    for p in UPLOAD_DIR.glob("*"):

        if p.is_file():
            p.unlink()

    return redirect(
        url_for("index")
    )


# ==========================================================
# EXECUÇÃO LOCAL
# ==========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
