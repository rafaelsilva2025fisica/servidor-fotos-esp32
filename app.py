import os
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

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

# O token continua sendo lido do Environment do Render.
# NÃO coloque seu token diretamente aqui.
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "troque-este-token")


HTML = """
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>Documentos recebidos</title>

<style>

body {
    font-family: Arial, sans-serif;
    max-width: 1250px;
    margin: 28px auto;
    padding: 0 15px;
    background: #111;
    color: #eee;
}

h1 {
    margin-bottom: 4px;
}

.sub {
    color: #aaa;
    margin-bottom: 22px;
}

.top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}

.seq {
    border-top: 3px solid #555;
    padding: 18px 0 28px;
    margin-top: 12px;
}

.seqhead {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
}

.seqtitle {
    font-size: 21px;
    font-weight: bold;
}

.time {
    color: #bbb;
    margin-top: 4px;
}

.photos {
    display: grid;
    grid-template-columns:
        repeat(auto-fill, minmax(190px, 1fr));
    gap: 12px;
    margin-top: 15px;
}

.card {
    background: #1d1d1d;
    border: 1px solid #333;
    border-radius: 10px;
    padding: 9px;
}

img {
    width: 100%;
    height: 170px;
    object-fit: contain;
    background: #000;
    border-radius: 7px;
}

.num {
    font-weight: bold;
    margin: 7px 0;
}

.actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
}

a.btn,
button {
    border: 0;
    border-radius: 6px;
    padding: 8px 10px;
    cursor: pointer;
    text-decoration: none;
    font-size: 13px;
}

.open {
    background: #eee;
    color: #111;
}

.del {
    background: #a5221a;
    color: white;
}

.all {
    background: #76150f;
    color: white;
}

</style>

</head>


<body>


<div class="top">

    <div>

        <h1>Documentos recebidos</h1>

        <div class="sub">
            Sequências enviadas pela ESP32-CAM
        </div>

    </div>


    {% if groups %}

    <form
        method="post"
        action="/excluir-todas"
        onsubmit="return confirm('Excluir TODAS as fotos?')"
    >

        <button class="all">
            Excluir tudo
        </button>

    </form>

    {% endif %}

</div>


{% for g in groups %}

<section class="seq">


    <div class="seqhead">

        <div>

            <div class="seqtitle">
                SEQUÊNCIA {{ g.display }}
            </div>

            <div class="time">
                {{ g.time }}
            </div>

        </div>


        <form
            method="post"
            action="/excluir-sequencia/{{ g.id }}"
            onsubmit="return confirm('Excluir esta sequência inteira?')"
        >

            <button class="all">
                Excluir sequência
            </button>

        </form>

    </div>


    <div class="photos">


        {% for p in g.photos %}

        <div class="card">


            <a
                href="/foto/{{ p.file }}"
                target="_blank"
            >

                <img
                    src="/foto/{{ p.file }}"
                    loading="lazy"
                >

            </a>


            <div class="num">
                Foto {{ p.number }}
            </div>


            <div class="actions">


                <a
                    class="btn open"
                    href="/foto/{{ p.file }}"
                    target="_blank"
                >
                    Abrir
                </a>


                <form
                    method="post"
                    action="/excluir/{{ p.file }}"
                    onsubmit="return confirm('Excluir esta foto?')"
                >

                    <button class="del">
                        Excluir
                    </button>

                </form>


            </div>


        </div>

        {% endfor %}


    </div>


</section>

{% else %}

<p>
    Nenhuma sequência recebida ainda.
</p>

{% endfor %}


</body>

</html>
"""


# ==========================================================
# SEGURANÇA BÁSICA DOS NOMES
# ==========================================================

def safe(value):

    return (
        bool(value)
        and Path(value).name == value
        and value not in (".", "..")
    )


# ==========================================================
# LER INFORMAÇÕES DO NOME DA FOTO
# ==========================================================

def parse_file(p):

    # Formato:
    #
    # camera001__sequencia__photo01__data.jpg

    parts = p.stem.split("__")

    if len(parts) < 4:
        return None


    try:

        number = int(
            parts[2].replace(
                "photo",
                ""
            )
        )

    except:

        number = 0


    return {

        "file": p.name,

        "device": parts[0],

        "seq": parts[1],

        "number": number,

        "stamp": parts[3],
    }


# ==========================================================
# PÁGINA PRINCIPAL
# ==========================================================

@app.get("/")
def index():

    grouped = defaultdict(list)


    for p in UPLOAD_DIR.glob("*.jpg"):

        info = parse_file(p)

        if info:

            grouped[
                info["seq"]
            ].append(info)


    groups = []


    for sequence_id, photos in grouped.items():

        photos.sort(
            key=lambda x: x["number"]
        )


        stamp = max(
            x["stamp"]
            for x in photos
        )


        try:

            dt = datetime.strptime(
                stamp,
                "%Y%m%d-%H%M%S-%f"
            ).replace(
                tzinfo=timezone.utc
            )


            shown = dt.strftime(
                "%d/%m/%Y - %H:%M:%S UTC"
            )


        except:

            shown = stamp


        groups.append({

            "id": sequence_id,

            "photos": photos,

            "stamp": stamp,

            "time": shown,
        })


    # Sequência mais recente primeiro

    groups.sort(
        key=lambda g: g["stamp"],
        reverse=True
    )


    total = len(groups)


    for i, g in enumerate(groups):

        g["display"] = (
            f"{total - i:03d}"
        )


    return render_template_string(
        HTML,
        groups=groups
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


    # Verifica o token

    token = request.headers.get(
        "X-Device-Token",
        ""
    )


    if token != DEVICE_TOKEN:

        return jsonify(
            error="token invalido"
        ), 401


    # Identificação da câmera

    device = request.headers.get(
        "X-Device-ID",
        "camera001"
    )


    device = "".join(

        c
        for c in device

        if c.isalnum()
        or c in "-_"

    )[:40]


    if not device:

        device = "camera001"


    # Identificação da sequência

    sequence_id = request.headers.get(
        "X-Sequence-ID",
        "sem-sequencia"
    )


    sequence_id = "".join(

        c
        for c in sequence_id

        if c.isalnum()
        or c in "-_"

    )[:80]


    if not sequence_id:

        sequence_id = "sem-sequencia"


    # Número da foto

    try:

        number = int(
            request.headers.get(
                "X-Photo-Number",
                "0"
            )
        )

    except:

        number = 0


    # Recebe o JPEG

    data = None


    if (
        request.content_type
        and "image/jpeg"
        in request.content_type
    ):

        data = request.get_data()


    if not data:

        return jsonify(
            error="nenhuma imagem recebida"
        ), 400


    # Limite de 8 MB por foto

    if len(data) > 8 * 1024 * 1024:

        return jsonify(
            error="imagem muito grande"
        ), 413


    # Horário em que o servidor recebeu a foto

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S-%f"
    )


    # Nome da foto

    name = (

        f"{device}"
        f"__{sequence_id}"
        f"__photo{number:02d}"
        f"__{stamp}"
        f".jpg"
    )


    (
        UPLOAD_DIR
        / name
    ).write_bytes(data)


    return jsonify(

        ok=True,

        arquivo=name
    )


# ==========================================================
# ABRIR FOTO
# ==========================================================

@app.get("/foto/<path:name>")
def foto(name):


    if not safe(name):

        abort(404)


    p = UPLOAD_DIR / name


    if not p.is_file():

        abort(404)


    return send_from_directory(
        UPLOAD_DIR,
        name
    )


# ==========================================================
# EXCLUIR UMA FOTO
# ==========================================================

@app.post("/excluir/<name>")
def excluir(name):


    if not safe(name):

        abort(400)


    p = UPLOAD_DIR / name


    if p.is_file():

        p.unlink()


    return redirect(
        url_for("index")
    )


# ==========================================================
# EXCLUIR UMA SEQUÊNCIA INTEIRA
# ==========================================================

@app.post("/excluir-sequencia/<sequence_id>")
def excluir_sequencia(sequence_id):


    if not safe(sequence_id):

        abort(400)


    for p in UPLOAD_DIR.glob("*.jpg"):


        info = parse_file(p)


        if (
            info
            and info["seq"] == sequence_id
        ):

            p.unlink()


    return redirect(
        url_for("index")
    )


# ==========================================================
# EXCLUIR TUDO
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
            "10000"
        )
    )


    app.run(

        host="0.0.0.0",

        port=port
    )
