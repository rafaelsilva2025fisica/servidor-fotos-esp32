from flask import Flask, jsonify, render_template_string, request, send_file
from flask_sock import Sock
import threading
import time
import os
import uuid
import subprocess
import imageio_ffmpeg

app = Flask(__name__)
sock = Sock(app)

# =========================================================
# CONFIGURAÇÃO
# =========================================================

AUDIO_DIR = "/tmp/audios"
os.makedirs(AUDIO_DIR, exist_ok=True)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

lock = threading.Lock()

ultimo_sinal_esp = 0.0
esp_ws = None
audios = {}


# =========================================================
# ESTADO DA ESP32
# =========================================================

def registrar_sinal_esp():
    global ultimo_sinal_esp

    with lock:
        ultimo_sinal_esp = time.time()


def segundos_desde_ultimo_sinal():
    with lock:
        ultimo = ultimo_sinal_esp

    if ultimo <= 0:
        return None

    return time.time() - ultimo


def esp_esta_online():
    segundos = segundos_desde_ultimo_sinal()

    if segundos is None:
        return False

    return segundos < 30


# =========================================================
# PÁGINA PRINCIPAL
# =========================================================

@app.route("/")
def pagina():
    return render_template_string("""
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>ESP32 Rafael V1</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    background: #0d1117;
    color: #e6edf3;
    font-family: Arial, Helvetica, sans-serif;
    display: flex;
    justify-content: center;
    padding: 35px 18px;
}

.container {
    width: 100%;
    max-width: 720px;
}

.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 16px;
    padding: 28px;
}

h1 {
    margin-top: 0;
    margin-bottom: 5px;
}

.subtitulo {
    color: #8b949e;
    margin-bottom: 25px;
}

.status-card {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 22px;
    text-align: center;
    margin-bottom: 25px;
}

#espStatus {
    font-size: 24px;
    font-weight: bold;
}

.online {
    color: #3fb950;
}

.offline {
    color: #f85149;
}

.detalhe {
    color: #8b949e;
    margin-top: 8px;
    font-size: 14px;
}

.audio-card {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 22px;
}

.botoes {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 15px;
}

button {
    border: 0;
    border-radius: 8px;
    padding: 13px 18px;
    font-size: 15px;
    font-weight: bold;
    cursor: pointer;
}

#gravar {
    background: #f85149;
    color: white;
}

#parar {
    background: #8b949e;
    color: white;
}

#enviar {
    background: #238636;
    color: white;
}

button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
}

audio {
    width: 100%;
    margin-top: 20px;
}

#mensagem {
    margin-top: 20px;
    padding: 15px;
    border-radius: 8px;
    background: #161b22;
    min-height: 52px;
    line-height: 1.4;
}


.camera-actions {
    position: fixed;
    right: 24px;
    top: 35px;
    width: 190px;
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.camera-actions button {
    width: 100%;
    padding: 15px 18px;
}

#tirarFoto {
    background: #1f6feb;
    color: white;
}

#galeria {
    background: #8957e5;
    color: white;
}

#mensagemCamera {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px;
    color: #8b949e;
    font-size: 13px;
    line-height: 1.4;
}

@media (max-width: 1150px) {
    .camera-actions {
        position: static;
        width: 100%;
        margin-top: 18px;
    }
}

</style>

</head>

<body>

<div class="container">

    <div class="card">

        <h1>ESP32 Rafael V1</h1>

        <div class="subtitulo">
            Servidor de áudio ESP32
        </div>

        <div class="status-card">

            <div id="espStatus" class="offline">
                🔴 ESP32 DESCONECTADA
            </div>

            <div class="detalhe" id="espDetalhe">
                Aguardando ESP32...
            </div>

        </div>

        <div class="audio-card">

            <h2>🎙️ Enviar áudio</h2>

            <div class="botoes">

                <button id="gravar">
                    🎙️ GRAVAR
                </button>

                <button id="parar" disabled>
                    ⏹️ PARAR
                </button>

                <button id="enviar" disabled>
                    📤 ENVIAR PARA ESP32
                </button>

            </div>

            <audio id="player" controls></audio>

            <div id="mensagem">
                Grave um áudio para começar.
            </div>

        </div>

    </div>


    <div class="camera-actions">
        <button id="tirarFoto">
            📷 TIRAR FOTO
        </button>

        <button id="galeria">
            🖼️ GALERIA
        </button>

        <div id="mensagemCamera">
            Câmera pronta para comando.
        </div>
    </div>

</div>


<script>

let mediaRecorder = null;
let partes = [];
let audioBlob = null;
let audioAtualId = null;

const botaoGravar = document.getElementById("gravar");
const botaoParar = document.getElementById("parar");
const botaoEnviar = document.getElementById("enviar");
const player = document.getElementById("player");
const mensagem = document.getElementById("mensagem");
const botaoTirarFoto = document.getElementById("tirarFoto");
const botaoGaleria = document.getElementById("galeria");
const mensagemCamera = document.getElementById("mensagemCamera");


// ======================================================
// STATUS ESP32
// ======================================================

async function atualizarESP() {

    try {

        const resposta = await fetch(
            "/status?t=" + Date.now(),
            { cache: "no-store" }
        );

        const dados = await resposta.json();

        const status = document.getElementById("espStatus");
        const detalhe = document.getElementById("espDetalhe");

        if (dados.esp32_online) {

            status.textContent = "🟢 ESP32 CONECTADA";
            status.className = "online";

            detalhe.textContent = "WebSocket ativo";

        } else {

            status.textContent = "🔴 ESP32 DESCONECTADA";
            status.className = "offline";

            detalhe.textContent = "Aguardando ESP32...";

        }

    } catch (erro) {

        document.getElementById("espDetalhe").textContent =
            "Erro ao consultar servidor";

    }
}

atualizarESP();

setInterval(atualizarESP, 2000);


// ======================================================
// GRAVAR
// ======================================================

botaoGravar.onclick = async function() {

    try {

        const stream = await navigator.mediaDevices.getUserMedia({
            audio: true
        });

        partes = [];
        audioBlob = null;
        audioAtualId = null;

        mediaRecorder = new MediaRecorder(stream);

        mediaRecorder.ondataavailable = function(evento) {

            if (evento.data.size > 0) {
                partes.push(evento.data);
            }

        };

        mediaRecorder.onstop = function() {

            audioBlob = new Blob(
                partes,
                {
                    type: mediaRecorder.mimeType || "audio/webm"
                }
            );

            const url = URL.createObjectURL(audioBlob);

            player.src = url;

            botaoEnviar.disabled = false;

            mensagem.textContent =
                "✅ Gravação pronta. Ouça e depois envie.";

            stream.getTracks().forEach(
                track => track.stop()
            );

        };

        mediaRecorder.start();

        botaoGravar.disabled = true;
        botaoParar.disabled = false;
        botaoEnviar.disabled = true;

        mensagem.textContent = "🔴 Gravando...";

    } catch (erro) {

        mensagem.textContent =
            "❌ Não foi possível acessar o microfone.";

    }
};


// ======================================================
// PARAR
// ======================================================

botaoParar.onclick = function() {

    if (
        mediaRecorder &&
        mediaRecorder.state !== "inactive"
    ) {
        mediaRecorder.stop();
    }

    botaoGravar.disabled = false;
    botaoParar.disabled = true;

};


// ======================================================
// ENVIAR
// ======================================================

botaoEnviar.onclick = async function() {

    if (!audioBlob) {
        return;
    }

    botaoEnviar.disabled = true;

    mensagem.textContent =
        "📤 Enviando e convertendo áudio...";

    const formulario = new FormData();

    formulario.append(
        "audio",
        audioBlob,
        "gravacao.webm"
    );

    try {

        const resposta = await fetch(
            "/enviar-audio",
            {
                method: "POST",
                body: formulario
            }
        );

        const dados = await resposta.json();

        if (!resposta.ok) {

            mensagem.textContent =
                "❌ " +
                (dados.erro || "Erro ao enviar áudio.");

            botaoEnviar.disabled = false;

            return;
        }

        audioAtualId = dados.audio_id;

        mensagem.textContent =
            "📡 WAV pronto. ESP32 avisada. Aguardando download...";

        verificarConfirmacao();

    } catch (erro) {

        mensagem.textContent =
            "❌ Erro de comunicação com o servidor.";

        botaoEnviar.disabled = false;

    }
};


// ======================================================
// CONFIRMAÇÃO
// ======================================================

async function verificarConfirmacao() {

    if (!audioAtualId) {
        return;
    }

    try {

        const resposta = await fetch(
            "/audio-status/" +
            audioAtualId +
            "?t=" +
            Date.now(),
            {
                cache: "no-store"
            }
        );

        const dados = await resposta.json();

        if (dados.recebido === true) {

            mensagem.textContent =
                "✅ ÁUDIO WAV RECEBIDO PELA ESP32";

            botaoEnviar.disabled = false;

            return;
        }

        mensagem.textContent =
            "📡 Aguardando a ESP32 receber o WAV...";

        setTimeout(
            verificarConfirmacao,
            1000
        );

    } catch (erro) {

        setTimeout(
            verificarConfirmacao,
            2000
        );

    }
}



// ======================================================
// CÂMERA
// ======================================================

botaoTirarFoto.onclick = async function() {

    botaoTirarFoto.disabled = true;
    mensagemCamera.textContent = "📷 Enviando comando para a ESP32...";

    try {

        const resposta = await fetch(
            "/tirar-foto",
            {
                method: "POST"
            }
        );

        const dados = await resposta.json();

        if (!resposta.ok) {
            mensagemCamera.textContent =
                "❌ " + (dados.erro || "Não foi possível solicitar a foto.");
            botaoTirarFoto.disabled = false;
            return;
        }

        mensagemCamera.textContent =
            "✅ Comando enviado. A ESP32 vai tirar a foto, salvar no cartão e reiniciar.";

        // O botão é liberado novamente depois de alguns segundos,
        // pois a ESP32 reinicia após salvar a foto.
        setTimeout(function() {
            botaoTirarFoto.disabled = false;
        }, 5000);

    } catch (erro) {

        mensagemCamera.textContent =
            "❌ Erro de comunicação com o servidor.";

        botaoTirarFoto.disabled = false;
    }
};


// Nesta etapa a galeria é apenas o botão/área reservada.
// Na próxima etapa ligaremos aqui as fotos enviadas pela ESP32.
botaoGaleria.onclick = function() {
    mensagemCamera.textContent =
        "🖼️ Galeria preparada. As fotos aparecerão aqui na próxima etapa.";
};

</script>

</body>
</html>
""")


# =========================================================
# STATUS
# =========================================================

@app.route("/status")
def status():

    segundos = segundos_desde_ultimo_sinal()

    if segundos is None:
        segundos_formatados = None
    else:
        segundos_formatados = round(segundos, 1)

    return jsonify({
        "esp32_online": esp_esta_online(),
        "segundos_desde_sinal": segundos_formatados
    })


# =========================================================
# RECEBER WEBM E CONVERTER PARA WAV
# =========================================================

@app.route("/enviar-audio", methods=["POST"])
def enviar_audio():

    global esp_ws

    if "audio" not in request.files:

        return jsonify({
            "erro": "Nenhum áudio recebido."
        }), 400

    if not esp_esta_online():

        return jsonify({
            "erro": "ESP32 está desconectada."
        }), 503

    arquivo = request.files["audio"]

    audio_id = uuid.uuid4().hex[:12]

    caminho_webm = os.path.join(
        AUDIO_DIR,
        audio_id + ".webm"
    )

    caminho_wav = os.path.join(
        AUDIO_DIR,
        audio_id + ".wav"
    )

    arquivo.save(caminho_webm)

    print(
        ">>> WEBM RECEBIDO:",
        caminho_webm,
        flush=True
    )

    # =====================================================
    # WEBM -> WAV PCM
    #
    # 44100 Hz
    # estéreo
    # PCM signed 16-bit little endian
    # =====================================================

    try:

        comando_ffmpeg = [
            FFMPEG,
            "-y",
            "-i",
            caminho_webm,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            caminho_wav
        ]

        resultado = subprocess.run(
            comando_ffmpeg,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30
        )

        if resultado.returncode != 0:

            erro_ffmpeg = resultado.stderr.decode(
                "utf-8",
                errors="ignore"
            )

            print(
                ">>> ERRO FFMPEG <<<",
                flush=True
            )

            print(
                erro_ffmpeg,
                flush=True
            )

            return jsonify({
                "erro": "Falha ao converter o áudio para WAV."
            }), 500

    except Exception as erro:

        print(
            "ERRO NA CONVERSAO:",
            erro,
            flush=True
        )

        return jsonify({
            "erro": "Erro durante conversão do áudio."
        }), 500

    # WebM não é mais necessário.

    try:

        os.remove(caminho_webm)

    except Exception:
        pass

    if not os.path.exists(caminho_wav):

        return jsonify({
            "erro": "O WAV não foi criado."
        }), 500

    tamanho_wav = os.path.getsize(caminho_wav)

    print(
        "================================",
        flush=True
    )

    print(
        ">>> WAV CRIADO COM SUCESSO <<<",
        flush=True
    )

    print(
        "ID:",
        audio_id,
        flush=True
    )

    print(
        "TAMANHO:",
        tamanho_wav,
        "bytes",
        flush=True
    )

    print(
        "FORMATO: PCM 16-bit / 44100 Hz / stereo",
        flush=True
    )

    print(
        "================================",
        flush=True
    )

    with lock:

        audios[audio_id] = {
            "arquivo": caminho_wav,
            "recebido": False,
            "criado": time.time()
        }

        socket_atual = esp_ws

    if socket_atual is None:

        return jsonify({
            "erro": "Canal da ESP32 não está disponível."
        }), 503

    try:

        comando = "NOVO_AUDIO|" + audio_id

        socket_atual.send(comando)

        print(
            "SERVIDOR -> ESP32:",
            comando,
            flush=True
        )

    except Exception as erro:

        print(
            "ERRO AO AVISAR ESP32:",
            erro,
            flush=True
        )

        return jsonify({
            "erro": "Falha ao enviar comando para ESP32."
        }), 500

    return jsonify({
        "ok": True,
        "audio_id": audio_id,
        "formato": "wav_pcm_s16le",
        "sample_rate": 44100,
        "canais": 2,
        "tamanho": tamanho_wav
    })


# =========================================================
# DOWNLOAD DO WAV
# =========================================================

@app.route("/audio/<audio_id>", methods=["GET"])
def baixar_audio(audio_id):

    with lock:
        dados = audios.get(audio_id)

    if dados is None:

        return jsonify({
            "erro": "Áudio não encontrado."
        }), 404

    caminho = dados["arquivo"]

    if not os.path.exists(caminho):

        return jsonify({
            "erro": "Arquivo não existe."
        }), 404

    print(
        ">>> ESP32 INICIOU DOWNLOAD DO WAV:",
        audio_id,
        flush=True
    )

    return send_file(
        caminho,
        mimetype="audio/wav",
        as_attachment=False,
        download_name="audio.wav"
    )


# =========================================================
# STATUS DO ÁUDIO
# =========================================================

@app.route("/audio-status/<audio_id>")
def audio_status(audio_id):

    with lock:
        dados = audios.get(audio_id)

    if dados is None:

        return jsonify({
            "recebido": False
        })

    return jsonify({
        "recebido": dados["recebido"]
    })



# =========================================================
# TIRAR FOTO
# =========================================================

@app.route("/tirar-foto", methods=["POST"])
def tirar_foto():

    global esp_ws

    if not esp_esta_online():
        return jsonify({
            "erro": "ESP32 está desconectada."
        }), 503

    with lock:
        socket_atual = esp_ws

    if socket_atual is None:
        return jsonify({
            "erro": "Canal da ESP32 não está disponível."
        }), 503

    try:
        # Compatível com o código da ESP32 preparado anteriormente.
        socket_atual.send("TIRAR_FOTO")

        print(
            "SERVIDOR -> ESP32: TIRAR_FOTO",
            flush=True
        )

    except Exception as erro:

        print(
            "ERRO AO ENVIAR TIRAR_FOTO:",
            erro,
            flush=True
        )

        return jsonify({
            "erro": "Falha ao enviar comando para ESP32."
        }), 500

    return jsonify({
        "ok": True,
        "comando": "TIRAR_FOTO"
    })


# =========================================================
# WEBSOCKET
# =========================================================

@sock.route("/ws-esp32")
def websocket_esp32(ws):

    global esp_ws

    print(
        "================================",
        flush=True
    )

    print(
        ">>> ESP32 WEBSOCKET CONECTADA <<<",
        flush=True
    )

    print(
        "================================",
        flush=True
    )

    registrar_sinal_esp()

    with lock:
        esp_ws = ws

    try:

        ws.send("SERVIDOR_OK")

        while True:

            mensagem = ws.receive()

            if mensagem is None:
                break

            registrar_sinal_esp()

            print(
                "ESP32 -> SERVIDOR:",
                mensagem,
                flush=True
            )

            # PING

            if mensagem == "PING":

                ws.send("PONG")

            # PRONTO

            elif mensagem == "PRONTO":

                ws.send("PRONTO_OK")

            # AUDIO RECEBIDO

            elif mensagem.startswith("AUDIO_RECEBIDO|"):

                partes = mensagem.split("|", 1)

                if len(partes) == 2:

                    audio_id = partes[1]

                    with lock:

                        if audio_id in audios:

                            audios[audio_id]["recebido"] = True

                    print(
                        ">>> WAV CONFIRMADO PELA ESP32:",
                        audio_id,
                        flush=True
                    )

    except Exception as erro:

        print(
            "ERRO WEBSOCKET:",
            erro,
            flush=True
        )

    finally:

        with lock:

            if esp_ws is ws:
                esp_ws = None

        print(
            ">>> WEBSOCKET DA ESP32 ENCERRADO <<<",
            flush=True
        )


# =========================================================
# EXECUÇÃO LOCAL
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
