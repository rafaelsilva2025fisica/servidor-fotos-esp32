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
AUDIO_DIR = Path(os.environ.get("AUDIO_DIR", "audios"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

DEVICE_TOKEN = os.environ.get(
    "DEVICE_TOKEN",
    "troque-este-token"
)


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

.audio-box {
    background: #1d1d1d;
    border: 2px solid #444;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 30px;
}

.audio-box h2 {
    margin-top: 0;
}

.audio-buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 15px;
}

button {
    border: 0;
    border-radius: 7px;
    padding: 10px 14px;
    cursor: pointer;
    font-size: 14px;
}

button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
}

.gravar {
    background: #c62828;
    color: white;
}

.parar,
.novo {
    background: #555;
    color: white;
}

.enviar {
    background: #1976d2;
    color: white;
}

#statusGravacao {
    margin-top: 15px;
    font-weight: bold;
}

#tempo {
    color: #ff5252;
    margin-top: 8px;
    font-size: 20px;
}

#audioPreview {
    width: 100%;
    max-width: 500px;
    margin-top: 15px;
    display: none;
}

#statusEnvio {
    margin-top: 15px;
    color: #90caf9;
    font-weight: bold;
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

.card img {
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

a.btn {
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

<div class="audio-box">

    <h2>🎙 Resposta em áudio</h2>

    <div>
        Grave, ouça e depois envie para a ESP32.
    </div>

    <div class="audio-buttons">

        <button
            id="btnGravar"
            class="gravar"
            onclick="iniciarGravacao()"
        >
            🎙 Gravar áudio
        </button>

        <button
            id="btnParar"
            class="parar"
            onclick="pararGravacao()"
            disabled
        >
            ⏹ Parar
        </button>

        <button
            id="btnNovo"
            class="novo"
            onclick="gravarNovamente()"
            disabled
        >
            🗑 Gravar novamente
        </button>

        <button
            id="btnEnviar"
            class="enviar"
            onclick="enviarAudio()"
            disabled
        >
            📤 Enviar para ESP32
        </button>

    </div>

    <div id="statusGravacao">
        Pronto para gravar.
    </div>

    <div id="tempo"></div>

    <audio
        id="audioPreview"
        controls
    ></audio>

    <div id="statusEnvio"></div>

</div>


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

<p>Nenhuma sequência recebida ainda.</p>

{% endfor %}


<script>

let mediaRecorder = null;
let audioChunks = [];
let audioBlob = null;
let audioURL = null;
let inicioGravacao = null;
let timerInterval = null;


/*
==========================================================
GRAVAR
==========================================================
*/

async function iniciarGravacao() {

    try {

        const stream =
            await navigator.mediaDevices.getUserMedia({
                audio: true
            });

        audioChunks = [];
        audioBlob = null;

        mediaRecorder =
            new MediaRecorder(stream);

        mediaRecorder.ondataavailable =
            function(event) {

                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }

            };

        mediaRecorder.onstop =
            function() {

                const tipo =
                    mediaRecorder.mimeType
                    || "audio/webm";

                audioBlob =
                    new Blob(
                        audioChunks,
                        { type: tipo }
                    );

                if (audioURL) {
                    URL.revokeObjectURL(audioURL);
                }

                audioURL =
                    URL.createObjectURL(audioBlob);

                const player =
                    document.getElementById(
                        "audioPreview"
                    );

                player.src = audioURL;
                player.style.display = "block";

                document.getElementById(
                    "statusGravacao"
                ).innerText =
                    "Áudio gravado. Ouça antes de enviar.";

                document.getElementById(
                    "btnNovo"
                ).disabled = false;

                document.getElementById(
                    "btnEnviar"
                ).disabled = false;

                stream
                    .getTracks()
                    .forEach(
                        track => track.stop()
                    );

            };

        mediaRecorder.start();

        inicioGravacao = Date.now();

        atualizarTempo();

        timerInterval =
            setInterval(
                atualizarTempo,
                250
            );

        document.getElementById(
            "btnGravar"
        ).disabled = true;

        document.getElementById(
            "btnParar"
        ).disabled = false;

        document.getElementById(
            "btnNovo"
        ).disabled = true;

        document.getElementById(
            "btnEnviar"
        ).disabled = true;

        document.getElementById(
            "statusEnvio"
        ).innerText = "";

        document.getElementById(
            "statusGravacao"
        ).innerText =
            "🔴 Gravando...";

    }

    catch (erro) {

        document.getElementById(
            "statusGravacao"
        ).innerText =
            "Não foi possível acessar o microfone.";

        console.error(erro);

    }
}


function atualizarTempo() {

    if (!inicioGravacao) {
        return;
    }

    const segundos =
        Math.floor(
            (Date.now() - inicioGravacao)
            / 1000
        );

    const minutos =
        Math.floor(segundos / 60);

    const resto =
        segundos % 60;

    document.getElementById(
        "tempo"
    ).innerText =
        String(minutos).padStart(2, "0")
        + ":"
        + String(resto).padStart(2, "0");

}


function pararGravacao() {

    if (
        mediaRecorder &&
        mediaRecorder.state !== "inactive"
    ) {
        mediaRecorder.stop();
    }

    clearInterval(timerInterval);

    document.getElementById(
        "btnParar"
    ).disabled = true;

}


/*
==========================================================
GRAVAR NOVAMENTE
==========================================================
*/

function gravarNovamente() {

    audioBlob = null;
    audioChunks = [];

    if (audioURL) {

        URL.revokeObjectURL(audioURL);
        audioURL = null;

    }

    const player =
        document.getElementById(
            "audioPreview"
        );

    player.pause();
    player.removeAttribute("src");
    player.style.display = "none";

    document.getElementById(
        "btnGravar"
    ).disabled = false;

    document.getElementById(
        "btnParar"
    ).disabled = true;

    document.getElementById(
        "btnNovo"
    ).disabled = true;

    document.getElementById(
        "btnEnviar"
    ).disabled = true;

    document.getElementById(
        "tempo"
    ).innerText = "";

    document.getElementById(
        "statusEnvio"
    ).innerText = "";

    document.getElementById(
        "statusGravacao"
    ).innerText =
        "Pronto para gravar novamente.";

}


/*
==========================================================
CONVERTER PARA PCM 44.1 kHz ESTEREO 16-BIT
==========================================================
*/

async function converterParaWav44100(blob) {

    const arrayBuffer =
        await blob.arrayBuffer();

    const contexto =
        new AudioContext();

    const audioOriginal =
        await contexto.decodeAudioData(
            arrayBuffer.slice(0)
        );

    const duracao =
        audioOriginal.duration;

    const totalFrames =
        Math.ceil(
            duracao * 44100
        );

    const offline =
        new OfflineAudioContext(
            1,
            totalFrames,
            44100
        );

    const source =
        offline.createBufferSource();

    source.buffer =
        audioOriginal;

    source.connect(
        offline.destination
    );

    source.start(0);

    const renderizado =
        await offline.startRendering();

    await contexto.close();

    const mono =
        renderizado.getChannelData(0);

    /*
       WAV:
       44 bytes de cabeçalho
       +
       cada frame:
       2 bytes esquerda
       2 bytes direita
    */

    const buffer =
        new ArrayBuffer(
            44 + mono.length * 4
        );

    const view =
        new DataView(buffer);


    function texto(offset, texto) {

        for (
            let i = 0;
            i < texto.length;
            i++
        ) {

            view.setUint8(
                offset + i,
                texto.charCodeAt(i)
            );

        }
    }


    texto(0, "RIFF");

    view.setUint32(
        4,
        36 + mono.length * 4,
        true
    );

    texto(8, "WAVE");
    texto(12, "fmt ");

    view.setUint32(
        16,
        16,
        true
    );

    // PCM
    view.setUint16(
        20,
        1,
        true
    );

    // Stereo
    view.setUint16(
        22,
        2,
        true
    );

    // 44.1 kHz
    view.setUint32(
        24,
        44100,
        true
    );

    // byte rate
    view.setUint32(
        28,
        44100 * 4,
        true
    );

    // block align
    view.setUint16(
        32,
        4,
        true
    );

    // 16 bits
    view.setUint16(
        34,
        16,
        true
    );

    texto(36, "data");

    view.setUint32(
        40,
        mono.length * 4,
        true
    );


    let pos = 44;


    for (
        let i = 0;
        i < mono.length;
        i++
    ) {

        let sample =
            Math.max(
                -1,
                Math.min(
                    1,
                    mono[i]
                )
            );

        sample =
            sample < 0
            ? sample * 32768
            : sample * 32767;

        const valor =
            Math.round(sample);

        // esquerda
        view.setInt16(
            pos,
            valor,
            true
        );

        pos += 2;

        // direita
        view.setInt16(
            pos,
            valor,
            true
        );

        pos += 2;

    }


    return new Blob(
        [buffer],
        {
            type: "audio/wav"
        }
    );
}


/*
==========================================================
ENVIAR PARA ESP32
==========================================================
*/

async function enviarAudio() {

    if (!audioBlob) {
        return;
    }

    const botao =
        document.getElementById(
            "btnEnviar"
        );

    botao.disabled = true;

    document.getElementById(
        "statusEnvio"
    ).innerText =
        "Preparando áudio para o Bluetooth...";

    try {

        const wav =
            await converterParaWav44100(
                audioBlob
            );

        document.getElementById(
            "statusEnvio"
        ).innerText =
            "Enviando áudio...";

        const resposta =
            await fetch(
                "/enviar-audio",
                {

                    method: "POST",

                    headers: {
                        "Content-Type":
                            "audio/wav"
                    },

                    body: wav

                }
            );

        const dados =
            await resposta.json();

        if (!resposta.ok) {

            throw new Error(
                dados.error
                || "Erro no envio"
            );

        }

        document.getElementById(
            "statusEnvio"
        ).innerText =
            "✅ Áudio enviado. Aguardando a ESP32.";

        document.getElementById(
            "statusGravacao"
        ).innerText =
            "Mensagem pronta para reprodução.";

    }

    catch (erro) {

        document.getElementById(
            "statusEnvio"
        ).innerText =
            "❌ Erro ao preparar/enviar o áudio.";

        botao.disabled = false;

        console.error(erro);

    }
}

</script>

</body>
</html>
"""


def safe(value):

    return (
        bool(value)
        and Path(value).name == value
        and value not in (".", "..")
    )


def parse_file(p):

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

    except Exception:

        number = 0

    return {

        "file": p.name,
        "device": parts[0],
        "seq": parts[1],
        "number": number,
        "stamp": parts[3],

    }


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
            key=lambda x:
                x["number"]
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

        except Exception:

            shown = stamp


        groups.append({

            "id": sequence_id,
            "photos": photos,
            "stamp": stamp,
            "time": shown,

        })


    groups.sort(
        key=lambda g:
            g["stamp"],
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


@app.get("/health")
def health():

    return {
        "ok": True
    }


# ==========================================================
# FOTO RECEBIDA DA ESP32
# ==========================================================

@app.post("/upload")
def upload():

    token = request.headers.get(
        "X-Device-Token",
        ""
    )

    if token != DEVICE_TOKEN:

        return jsonify(
            error="token invalido"
        ), 401


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


    try:

        number = int(
            request.headers.get(
                "X-Photo-Number",
                "0"
            )
        )

    except Exception:

        number = 0


    data = request.get_data()

    if not data:

        return jsonify(
            error="nenhuma imagem recebida"
        ), 400


    if len(data) > 8 * 1024 * 1024:

        return jsonify(
            error="imagem muito grande"
        ), 413


    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S-%f"
    )


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
# AUDIO PCM/WAV RECEBIDO DO NAVEGADOR
# ==========================================================

@app.post("/enviar-audio")
def enviar_audio():

    data = request.get_data()

    if not data:

        return jsonify(
            error="audio vazio"
        ), 400


    # Limite 12 MB
    if len(data) > 12 * 1024 * 1024:

        return jsonify(
            error="audio muito grande"
        ), 413


    # Agora aceitamos somente WAV preparado pelo navegador.
    if (
        len(data) < 44
        or data[0:4] != b"RIFF"
        or data[8:12] != b"WAVE"
    ):

        return jsonify(
            error="audio precisa estar em WAV"
        ), 400


    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S-%f"
    )


    nome = (
        f"audio__{stamp}.wav"
    )


    (
        AUDIO_DIR
        / nome
    ).write_bytes(data)


    pendente = (
        AUDIO_DIR
        / "pendente.txt"
    )

    pendente.write_text(
        nome,
        encoding="utf-8"
    )


    return jsonify(
        ok=True,
        arquivo=nome,
        status="aguardando_esp32"
    )


# ==========================================================
# ESP32 VERIFICA SE EXISTE AUDIO
# ==========================================================

@app.get("/audio-pendente")
def audio_pendente():

    token = request.headers.get(
        "X-Device-Token",
        ""
    )

    if token != DEVICE_TOKEN:

        return jsonify(
            error="token invalido"
        ), 401


    pendente = (
        AUDIO_DIR
        / "pendente.txt"
    )


    if not pendente.exists():

        return jsonify(
            pendente=False
        )


    nome = pendente.read_text(
        encoding="utf-8"
    ).strip()


    arquivo = (
        AUDIO_DIR
        / nome
    )


    if not arquivo.exists():

        try:
            pendente.unlink()
        except Exception:
            pass

        return jsonify(
            pendente=False
        )


    return jsonify(
        pendente=True,
        arquivo=nome,
        url=f"/baixar-audio/{nome}"
    )


# ==========================================================
# ESP32 BAIXA AUDIO
# ==========================================================

@app.get("/baixar-audio/<name>")
def baixar_audio(name):

    token = request.headers.get(
        "X-Device-Token",
        ""
    )

    if token != DEVICE_TOKEN:
        abort(401)

    if not safe(name):
        abort(404)

    arquivo = (
        AUDIO_DIR
        / name
    )

    if not arquivo.is_file():
        abort(404)

    return send_from_directory(
        AUDIO_DIR,
        name
    )


# ==========================================================
# ESP32 CONFIRMA DEPOIS DE REPRODUZIR
# ==========================================================

@app.post("/confirmar-audio")
def confirmar_audio():

    token = request.headers.get(
        "X-Device-Token",
        ""
    )

    if token != DEVICE_TOKEN:

        return jsonify(
            error="token invalido"
        ), 401


    dados = request.get_json(
        silent=True
    ) or {}


    nome_recebido = dados.get(
        "arquivo",
        ""
    )


    if not nome_recebido:

        return jsonify(
            error="arquivo nao informado"
        ), 400


    pendente = (
        AUDIO_DIR
        / "pendente.txt"
    )


    if not pendente.exists():

        return jsonify(
            ok=True,
            status="nenhum_pendente"
        )


    nome_pendente = (
        pendente.read_text(
            encoding="utf-8"
        ).strip()
    )


    if nome_recebido != nome_pendente:

        return jsonify(
            ok=False,
            error="arquivo diferente do pendente"
        ), 409


    try:
        pendente.unlink()

    except FileNotFoundError:
        pass


    return jsonify(
        ok=True,
        status="reproduzido",
        arquivo=nome_recebido
    )


@app.get("/foto/<path:name>")
def foto(name):

    if not safe(name):
        abort(404)

    arquivo = UPLOAD_DIR / name

    if not arquivo.is_file():
        abort(404)

    return send_from_directory(
        UPLOAD_DIR,
        name
    )


@app.post("/excluir/<name>")
def excluir(name):

    if not safe(name):
        abort(400)

    arquivo = UPLOAD_DIR / name

    if arquivo.is_file():
        arquivo.unlink()

    return redirect(
        url_for("index")
    )


@app.post("/excluir-sequencia/<sequence_id>")
def excluir_sequencia(sequence_id):

    if not safe(sequence_id):
        abort(400)

    for p in UPLOAD_DIR.glob(
        "*.jpg"
    ):

        info = parse_file(p)

        if (
            info
            and
            info["seq"] == sequence_id
        ):
            p.unlink()

    return redirect(
        url_for("index")
    )


@app.post("/excluir-todas")
def excluir_todas():

    for p in UPLOAD_DIR.glob("*"):

        if p.is_file():
            p.unlink()

    return redirect(
        url_for("index")
    )


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
