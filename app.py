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

foto_pedido_recebido = False
foto_pedido_recebido_em = None
foto_recebida_servidor = False
foto_recebida_servidor_em = None


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

#tirarFotos {
    background: #1f6feb;
    color: white;
    width: 100%;
    margin-top: 12px;
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

        <div class="audio-card" style="margin-top:20px;">
            <h2>📸 Câmera ESP32</h2>
            <button id="tirarFotos">📸 TIRAR FOTO</button>
            <div id="mensagemFoto" class="detalhe">
                Use o botão para solicitar uma foto.
            </div>
            <div class="detalhe">
                <a href="/fotos" style="color:#58a6ff;">Ver fotos recebidas</a>
            </div>
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
            "📡 WAV pronto. Aguardando a ESP32 confirmar que recebeu o pedido...";

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

        if (dados.reproduzido === true) {

            mensagem.textContent =
                "✅ ÁUDIO REPRODUZIDO NO BLUETOOTH"
                + (dados.reproduzido_em
                    ? " • " + dados.reproduzido_em
                    : "");

            botaoEnviar.disabled = false;

            return;
        }

        if (dados.bluetooth_falhou === true) {

            mensagem.textContent =
                "⚠️ BLUETOOTH NÃO CONECTOU / APARELHO PODE ESTAR DESLIGADO"
                + (dados.bluetooth_falhou_em ? " • " + dados.bluetooth_falhou_em : "")
                + ". Ligue o Bluetooth e envie um novo áudio.";

            botaoEnviar.disabled = false;
            return;
        }

        if (dados.download_erro === true) {

            mensagem.textContent =
                "❌ ERRO AO BAIXAR O ÁUDIO NA ESP32. Grave outro áudio e envie novamente.";

            botaoEnviar.disabled = false;
            return;

        } else if (dados.recebido === true) {

            mensagem.textContent =
                "🎧 WAV RECEBIDO. Aguardando reprodução no Bluetooth...";

        } else if (dados.download_iniciado === true) {

            mensagem.textContent =
                "⬇️ ESP32 COMEÇOU O DOWNLOAD DO ÁUDIO"
                + (dados.download_iniciado_em
                    ? " • " + dados.download_iniciado_em
                    : "");

        } else if (dados.pedido_recebido === true) {

            mensagem.textContent =
                "✅ ESP32 RECEBEU O PEDIDO. Preparando para baixar o áudio...";

        } else {

            mensagem.textContent =
                "📡 Aguardando a ESP32 confirmar que recebeu o pedido...";
        }

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
// COMANDO REMOTO - FOTO
// ======================================================

const botaoTirarFotos = document.getElementById("tirarFotos");
const mensagemFoto = document.getElementById("mensagemFoto");

botaoTirarFotos.onclick = async function() {

    botaoTirarFotos.disabled = true;
    mensagemFoto.textContent = "📡 Enviando comando para a ESP32...";

    try {

        const resposta = await fetch(
            "/comando-foto",
            {
                method: "POST",
                cache: "no-store"
            }
        );

        const dados = await resposta.json();

        if (!resposta.ok) {
            mensagemFoto.textContent =
                "❌ " + (dados.erro || "Falha ao enviar comando.");
            botaoTirarFotos.disabled = false;
            return;
        }

        mensagemFoto.textContent =
            "📡 Comando enviado. Aguardando a ESP32 confirmar...";

        verificarPedidoFoto();

    } catch (erro) {

        mensagemFoto.textContent =
            "❌ Erro de comunicação com o servidor.";

        botaoTirarFotos.disabled = false;
    }
};

async function verificarPedidoFoto() {

    try {
        const resposta = await fetch(
            "/foto-comando-status?t=" + Date.now(),
            { cache: "no-store" }
        );

        const dados = await resposta.json();

        if (dados.foto_recebida === true) {
            mensagemFoto.textContent =
                "✅ FOTO RECEBIDA PELO SERVIDOR"
                + (dados.foto_recebida_em ? " • " + dados.foto_recebida_em : "");

            botaoTirarFotos.disabled = false;
            return;
        }

        if (dados.recebido === true) {
            mensagemFoto.textContent =
                "✅ ESP32 RECEBEU O PEDIDO DA FOTO"
                + (dados.recebido_em ? " • " + dados.recebido_em : "")
                + ". Agora é só aguardar a foto.";
        }

        setTimeout(verificarPedidoFoto, 500);

    } catch (erro) {
        setTimeout(verificarPedidoFoto, 1000);
    }
}

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
            "pedido_recebido": False,
            "pedido_recebido_em": None,
            "download_iniciado": False,
            "download_iniciado_em": None,
            "download_erro": False,
            "download_erro_em": None,
            "bluetooth_falhou": False,
            "bluetooth_falhou_em": None,
            "recebido": False,
            "reproduzido": False,
            "reproduzido_em": None,
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

    horario = agora_brasilia().strftime("%d/%m/%Y %H:%M:%S")
    with lock:
        if audio_id in audios:
            audios[audio_id]["download_iniciado"] = True
            audios[audio_id]["download_iniciado_em"] = horario

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
# ESP32 AVISA QUE O DOWNLOAD FALHOU
# =========================================================

@app.route("/audio-download-erro/<audio_id>", methods=["GET"])
def audio_download_erro(audio_id):
    horario = agora_brasilia().strftime("%d/%m/%Y %H:%M:%S")

    with lock:
        if audio_id in audios:
            audios[audio_id]["download_erro"] = True
            audios[audio_id]["download_erro_em"] = horario

    print(">>> ESP32 INFORMOU ERRO NO DOWNLOAD:", audio_id, flush=True)
    return jsonify({"ok": True})


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
        "pedido_recebido": dados.get("pedido_recebido", False),
        "pedido_recebido_em": dados.get("pedido_recebido_em"),
        "download_iniciado": dados.get("download_iniciado", False),
        "download_iniciado_em": dados.get("download_iniciado_em"),
        "download_erro": dados.get("download_erro", False),
        "download_erro_em": dados.get("download_erro_em"),
        "bluetooth_falhou": dados.get("bluetooth_falhou", False),
        "bluetooth_falhou_em": dados.get("bluetooth_falhou_em"),
        "recebido": dados["recebido"],
        "reproduzido": dados.get("reproduzido", False),
        "reproduzido_em": dados.get("reproduzido_em")
    })


# =========================================================
# COMANDO REMOTO PARA TIRAR 5 FOTOS
# =========================================================

@app.route("/comando-foto", methods=["POST"])
def comando_foto():

    global esp_ws, foto_pedido_recebido, foto_pedido_recebido_em, foto_recebida_servidor, foto_recebida_servidor_em

    if not esp_esta_online():
        return jsonify({
            "ok": False,
            "erro": "ESP32 esta desconectada."
        }), 503

    with lock:
        socket_atual = esp_ws

    if socket_atual is None:
        return jsonify({
            "ok": False,
            "erro": "WebSocket da ESP32 nao esta disponivel."
        }), 503

    try:
        with lock:
            foto_pedido_recebido = False
            foto_pedido_recebido_em = None
            foto_recebida_servidor = False
            foto_recebida_servidor_em = None

        socket_atual.send("TIRAR_FOTOS")

        print(
            "SERVIDOR -> ESP32: TIRAR_FOTOS",
            flush=True
        )

        return jsonify({
            "ok": True,
            "comando": "TIRAR_FOTOS"
        })

    except Exception as erro:

        print(
            "ERRO AO ENVIAR COMANDO DE FOTO:",
            erro,
            flush=True
        )

        return jsonify({
            "ok": False,
            "erro": "Falha ao enviar comando para a ESP32."
        }), 500


# =========================================================
# STATUS DO PEDIDO DE FOTO
# =========================================================

@app.route("/foto-comando-status")
def foto_comando_status():
    with lock:
        recebido = foto_pedido_recebido
        horario = foto_pedido_recebido_em
        foto_chegou = foto_recebida_servidor
        foto_chegou_em = foto_recebida_servidor_em

    return jsonify({
        "recebido": recebido,
        "recebido_em": horario,
        "foto_recebida": foto_chegou,
        "foto_recebida_em": foto_chegou_em
    })


# =========================================================
# WEBSOCKET
# =========================================================

@sock.route("/ws-esp32")
def websocket_esp32(ws):

    global esp_ws, foto_pedido_recebido, foto_pedido_recebido_em

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

            # ESP32 RECEBEU O PEDIDO DO AUDIO

            elif mensagem.startswith("AUDIO_PEDIDO_RECEBIDO|"):

                partes = mensagem.split("|", 1)

                if len(partes) == 2:
                    audio_id = partes[1]
                    horario = agora_brasilia().strftime("%d/%m/%Y %H:%M:%S")

                    with lock:
                        if audio_id in audios:
                            audios[audio_id]["pedido_recebido"] = True
                            audios[audio_id]["pedido_recebido_em"] = horario

                    print(">>> ESP32 CONFIRMOU PEDIDO DO AUDIO:", audio_id, flush=True)

            # DOWNLOAD DO AUDIO INICIADO (compatibilidade)

            elif mensagem.startswith("AUDIO_DOWNLOAD_INICIADO|"):

                partes = mensagem.split("|", 1)

                if len(partes) == 2:
                    audio_id = partes[1]
                    horario = agora_brasilia().strftime("%d/%m/%Y %H:%M:%S")

                    with lock:
                        if audio_id in audios:
                            audios[audio_id]["download_iniciado"] = True
                            audios[audio_id]["download_iniciado_em"] = horario

                    print(">>> DOWNLOAD DO AUDIO INICIADO:", audio_id, flush=True)

            # PEDIDO DE FOTO RECEBIDO

            elif mensagem == "FOTO_PEDIDO_RECEBIDO":

                horario = agora_brasilia().strftime("%d/%m/%Y %H:%M:%S")

                with lock:
                    foto_pedido_recebido = True
                    foto_pedido_recebido_em = horario

                print(">>> ESP32 CONFIRMOU PEDIDO DE FOTO <<<", flush=True)

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

            # AUDIO REPRODUZIDO NO BLUETOOTH

            elif mensagem.startswith("BLUETOOTH_FALHOU|"):

                partes = mensagem.split("|", 1)

                if len(partes) == 2:
                    audio_id = partes[1]
                    horario = agora_brasilia().strftime("%d/%m/%Y %H:%M:%S")

                    with lock:
                        if audio_id in audios:
                            audios[audio_id]["bluetooth_falhou"] = True
                            audios[audio_id]["bluetooth_falhou_em"] = horario

                    print(
                        ">>> ESP32 INFORMOU FALHA NO BLUETOOTH:",
                        audio_id,
                        flush=True
                    )

            elif mensagem.startswith("AUDIO_REPRODUZIDO|"):

                partes = mensagem.split("|", 1)

                if len(partes) == 2:

                    audio_id = partes[1]
                    horario = agora_brasilia().strftime("%d/%m/%Y %H:%M:%S")

                    with lock:

                        if audio_id in audios:
                            audios[audio_id]["reproduzido"] = True
                            audios[audio_id]["reproduzido_em"] = horario

                    print(
                        ">>> AUDIO REPRODUZIDO NO BLUETOOTH:",
                        audio_id,
                        "|",
                        horario,
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

# =========================================================
# ADICAO - FOTOS DA ESP32
# =========================================================
# O codigo original acima nao foi alterado.
# Esta secao apenas adiciona:
#   POST /enviar-foto
#   GET  /foto/<sequencia>/<foto>
#   GET  /fotos
#   GET  /fotos-status
# =========================================================

from datetime import datetime, timezone, timedelta

FOTO_DIR = "/tmp/fotos"
os.makedirs(FOTO_DIR, exist_ok=True)

FUSO_BRASIL = timezone(timedelta(hours=-3))

lock_fotos = threading.Lock()
sequencias_fotos = {}
contador_sequencia_fotos = 0


def agora_brasilia():
    return datetime.now(FUSO_BRASIL)


@app.route("/enviar-foto", methods=["POST"])
def receber_foto():

    global contador_sequencia_fotos

    try:
        numero_foto = int(request.args.get("foto", "0"))
    except ValueError:
        numero_foto = 0

    if numero_foto < 1 or numero_foto > 5:
        return jsonify({
            "ok": False,
            "erro": "O parametro foto deve ser de 1 a 5."
        }), 400

    agora = agora_brasilia()
    sequencia_param = request.args.get("sequencia")

    # A primeira foto, sem sequencia, cria uma nova sequencia.
    if numero_foto == 1 and not sequencia_param:

        with lock_fotos:
            contador_sequencia_fotos += 1
            numero_sequencia = contador_sequencia_fotos

            sequencias_fotos[numero_sequencia] = {
                "inicio": agora.strftime("%d/%m/%Y %H:%M:%S"),
                "fotos": {}
            }

    else:

        try:
            numero_sequencia = int(sequencia_param)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "erro": "Sequencia ausente ou invalida."
            }), 400

        with lock_fotos:
            if numero_sequencia not in sequencias_fotos:
                return jsonify({
                    "ok": False,
                    "erro": "Sequencia nao encontrada."
                }), 404

    # Aceita JPEG cru ou multipart/form-data com campo chamado foto.
    if "foto" in request.files:
        imagem = request.files["foto"].read()
    else:
        imagem = request.get_data()

    if not imagem:
        return jsonify({
            "ok": False,
            "erro": "Nenhuma imagem recebida."
        }), 400

    # Assinatura inicial de um JPEG.
    if len(imagem) < 2 or imagem[:2] != b"\xff\xd8":
        return jsonify({
            "ok": False,
            "erro": "A imagem recebida nao parece ser JPEG."
        }), 400

    pasta = os.path.join(
        FOTO_DIR,
        "sequencia_" + str(numero_sequencia)
    )

    os.makedirs(pasta, exist_ok=True)

    caminho = os.path.join(
        pasta,
        "foto_" + str(numero_foto) + ".jpg"
    )

    with open(caminho, "wb") as arquivo:
        arquivo.write(imagem)

    horario = agora.strftime("%d/%m/%Y %H:%M:%S")

    # A foto já foi validada e gravada em disco. Portanto este é o ponto
    # confiável para dizer à interface que ela realmente chegou ao servidor.
    global foto_recebida_servidor, foto_recebida_servidor_em

    with lock:
        foto_recebida_servidor = True
        foto_recebida_servidor_em = horario

    with lock_fotos:

        sequencias_fotos[numero_sequencia]["fotos"][numero_foto] = {
            "arquivo": caminho,
            "horario": horario,
            "tamanho": len(imagem)
        }

        quantidade = len(
            sequencias_fotos[numero_sequencia]["fotos"]
        )

    print(
        ">>> FOTO "
        + str(numero_foto)
        + "/5 | SEQUENCIA #"
        + str(numero_sequencia)
        + " | "
        + horario
        + " | "
        + str(len(imagem))
        + " bytes",
        flush=True
    )

    return jsonify({
        "ok": True,
        "sequencia": numero_sequencia,
        "foto": numero_foto,
        "horario": horario,
        "recebidas": quantidade,
        "completa": quantidade >= 5
    })


@app.route("/foto/<int:numero_sequencia>/<int:numero_foto>")
def mostrar_foto(numero_sequencia, numero_foto):

    with lock_fotos:

        sequencia = sequencias_fotos.get(numero_sequencia)

        if sequencia is None:
            return "Sequencia nao encontrada.", 404

        foto = sequencia["fotos"].get(numero_foto)

        if foto is None:
            return "Foto nao encontrada.", 404

        caminho = foto["arquivo"]

    if not os.path.exists(caminho):
        return "Arquivo nao encontrado.", 404

    return send_file(
        caminho,
        mimetype="image/jpeg",
        as_attachment=False
    )


@app.route("/fotos-status")
def fotos_status():

    with lock_fotos:

        lista = []

        for numero_sequencia in sorted(sequencias_fotos.keys(), reverse=True):

            sequencia = sequencias_fotos[numero_sequencia]

            lista.append({
                "sequencia": numero_sequencia,
                "inicio": sequencia["inicio"],
                "quantidade": len(sequencia["fotos"]),
                "completa": len(sequencia["fotos"]) >= 5
            })

    return jsonify({
        "ok": True,
        "sequencias": lista
    })


@app.route("/fotos")
def pagina_fotos():

    # Esta pagina foi montada sem strings de tres aspas.
    # Assim evitamos o problema anterior de HTML sair da string Python.

    partes = []

    partes.append("<!DOCTYPE html>")
    partes.append("<html lang='pt-BR'>")
    partes.append("<head>")
    partes.append("<meta charset='UTF-8'>")
    partes.append("<meta name='viewport' content='width=device-width, initial-scale=1.0'>")
    partes.append("<meta http-equiv='refresh' content='2'>")
    partes.append("<title>Fotos ESP32</title>")

    partes.append("<style>")
    partes.append("body{margin:0;background:#0d1117;color:#e6edf3;font-family:Arial,sans-serif;padding:30px 16px;}")
    partes.append(".container{max-width:1000px;margin:auto;}")
    partes.append(".seq{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:20px;margin:0 0 22px 0;}")
    partes.append(".info{color:#8b949e;margin-bottom:15px;}")
    partes.append(".grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,180px));gap:12px;justify-content:start;}")
    partes.append(".foto{background:#0d1117;border:1px solid #30363d;border-radius:10px;overflow:hidden;}")
    partes.append(".foto img{width:100%;height:135px;object-fit:cover;display:block;cursor:zoom-in;}")
    partes.append(".texto{padding:11px;line-height:1.5;}")
    partes.append(".hora{color:#8b949e;font-size:14px;}")
    partes.append(".vazio{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:20px;color:#8b949e;}")
    partes.append("</style>")

    partes.append("</head>")
    partes.append("<body>")
    partes.append("<div class='container'>")
    partes.append("<h1>Fotos da ESP32</h1>")
    partes.append("<div class='info'>Atualizacao automatica a cada 2 segundos. Clique em uma foto para abrir grande em nova aba.</div>")

    with lock_fotos:

        numeros = sorted(sequencias_fotos.keys(), reverse=True)

        if not numeros:

            partes.append(
                "<div class='vazio'>Nenhuma foto recebida ainda.</div>"
            )

        else:

            # Ordem decrescente: a sequencia mais nova aparece primeiro.
            for numero_sequencia in numeros:

                sequencia = sequencias_fotos[numero_sequencia]
                fotos = sequencia["fotos"]

                partes.append("<div class='seq'>")

                partes.append(
                    "<h2>Sequencia #"
                    + str(numero_sequencia)
                    + "</h2>"
                )

                partes.append(
                    "<div class='info'>Inicio: "
                    + sequencia["inicio"]
                    + " | "
                    + str(len(fotos))
                    + "/5 fotos recebidas</div>"
                )

                partes.append("<div class='grade'>")

                for numero_foto in sorted(fotos.keys(), reverse=True):

                    foto = fotos[numero_foto]

                    url = (
                        "/foto/"
                        + str(numero_sequencia)
                        + "/"
                        + str(numero_foto)
                    )

                    partes.append("<div class='foto'>")

                    partes.append(
                        "<a href='"
                        + url
                        + "' target='_blank' rel='noopener noreferrer' title='Abrir foto grande em nova aba'>"
                        + "<img src='"
                        + url
                        + "' alt='Foto "
                        + str(numero_foto)
                        + "'>"
                        + "</a>"
                    )

                    partes.append("<div class='texto'>")

                    partes.append(
                        "<strong>Foto "
                        + str(numero_foto)
                        + "/5</strong>"
                    )

                    partes.append(
                        "<div class='hora'>Recebida: "
                        + foto["horario"]
                        + "</div>"
                    )

                    partes.append("</div>")
                    partes.append("</div>")

                partes.append("</div>")
                partes.append("</div>")

    partes.append("</div>")
    partes.append("</body>")
    partes.append("</html>")

    return "\n".join(partes)
