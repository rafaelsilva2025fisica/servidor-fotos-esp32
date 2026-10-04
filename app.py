from flask import Flask, jsonify, render_template_string, request, send_file, send_from_directory
from flask_sock import Sock
import threading
import time
import os
import base64
import uuid
import subprocess
import imageio_ffmpeg
from datetime import datetime
from openai import OpenAI

app = Flask(__name__)

# =========================================================
# GALERIA DE FOTOS
# =========================================================
PASTA_GALERIA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "galeria")
os.makedirs(PASTA_GALERIA, exist_ok=True)
sock = Sock(app)

# =========================================================
# CONFIGURAÇÃO
# =========================================================

AUDIO_DIR = "/tmp/audios"
os.makedirs(AUDIO_DIR, exist_ok=True)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna").strip()
OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip()
OPENAI_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy").strip()
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


lock = threading.Lock()

ultimo_sinal_esp = 0.0
esp_ws = None
audios = {}

# =========================================================
# SESSAO ATUAL DE EXERCICIOS
# =========================================================
exercicios_atuais = []
aguardando_resposta_menu = False
foto_sessao_atual = None
nome_foto_sessao_atual = None
quantidade_exercicios_sessao = 0


logs_esp32 = []
MAX_LOGS_ESP32 = 20

# STATUS VISUAL DA IA
status_ia = {
    "estado": "AGUARDANDO",
    "titulo": "⚪ IA AGUARDANDO",
    "detalhe": "Aguardando uma nova foto ou uma escolha de exercício.",
    "inicio": time.time()
}

def atualizar_status_ia(estado, titulo, detalhe):
    global status_ia
    with lock:
        status_ia = {
            "estado": estado,
            "titulo": titulo,
            "detalhe": detalhe,
            "inicio": time.time()
        }
    print(f"STATUS IA [{estado}] {titulo} - {detalhe}", flush=True)

def adicionar_log_esp32(nivel, mensagem):
    nivel = (nivel or "INFO").upper().strip()
    if nivel not in {"INFO", "OK", "AVISO", "ERRO"}:
        nivel = "INFO"

    item = {
        "id": uuid.uuid4().hex[:10],
        "hora": datetime.now().strftime("%d/%m %H:%M:%S"),
        "nivel": nivel,
        "mensagem": str(mensagem)[:500]
    }

    with lock:
        logs_esp32.append(item)
        if len(logs_esp32) > MAX_LOGS_ESP32:
            del logs_esp32[:-MAX_LOGS_ESP32]

    print(
        f"LOG ESP32 [{item['nivel']}] {item['mensagem']}",
        flush=True
    )

    return item



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

.ia-status-card {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 18px;
}
.ia-status-topo { font-size: 13px; color: #8b949e; margin-bottom: 10px; font-weight: bold; }
#iaStatusTitulo { font-size: 20px; font-weight: bold; margin-bottom: 8px; }
#iaStatusDetalhe { color: #c9d1d9; line-height: 1.45; }
#iaStatusTempo { color: #8b949e; font-size: 13px; margin-top: 10px; }

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


.galeria-modal {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(0,0,0,.88);
    overflow-y: auto;
    padding: 28px;
}

.galeria-modal.aberta {
    display: block;
}

.galeria-caixa {
    max-width: 1100px;
    margin: 0 auto;
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 20px;
}

.galeria-topo {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    margin-bottom: 18px;
}

.galeria-topo h2 {
    margin: 0;
}

#fecharGaleria {
    width: auto;
    background: #30363d;
    color: white;
}

.galeria-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 16px;
}

.foto-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    overflow: hidden;
}

.foto-card img {
    display: block;
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: cover;
    cursor: pointer;
}

.foto-info {
    padding: 10px;
    font-size: 12px;
    color: #8b949e;
    overflow-wrap: anywhere;
}

.galeria-vazia {
    color: #8b949e;
    padding: 30px 0;
    text-align: center;
}


/* =========================================================
   LOGS ESP32 - painel independente à esquerda
   ========================================================= */
.logs-panel {
    position: fixed;
    left: 24px;
    top: 35px;
    width: 300px;
    max-height: calc(100vh - 70px);
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 14px;
    padding: 16px;
    z-index: 20;
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.logs-topo {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
}

.logs-topo h2 {
    margin: 0;
    font-size: 18px;
}

#atualizarLogs,
#copiarLogs {
    padding: 8px 10px;
    font-size: 12px;
    background: #30363d;
    color: white;
}

#copiarLogs {
    background: #238636;
}

.logs-filtros {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}

.logs-filtros button {
    padding: 7px 9px;
    font-size: 11px;
    background: #21262d;
    color: #c9d1d9;
}

.logs-filtros button.ativo {
    outline: 2px solid #58a6ff;
}

#limparLogs {
    background: #6e2b2b;
}

.logs-lista {
    overflow-y: auto;
    min-height: 220px;
    max-height: calc(100vh - 215px);
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-right: 3px;
}

.log-item {
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 9px;
    background: #0d1117;
    font-size: 12px;
    line-height: 1.35;
}

.log-cabecalho {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
}

.log-hora {
    color: #8b949e;
}

.log-nivel {
    font-weight: bold;
}

.log-OK .log-nivel { color: #3fb950; }
.log-INFO .log-nivel { color: #58a6ff; }
.log-AVISO .log-nivel { color: #d29922; }
.log-ERRO .log-nivel { color: #f85149; }

.log-msg {
    color: #c9d1d9;
    overflow-wrap: anywhere;
}

.logs-vazio {
    color: #8b949e;
    text-align: center;
    padding: 24px 5px;
}

@media (max-width: 1400px) {
    .logs-panel {
        width: 250px;
    }
}

@media (max-width: 1150px) {
    .logs-panel {
        position: static;
        width: 100%;
        max-height: none;
        margin-bottom: 18px;
    }

    .logs-lista {
        max-height: 320px;
    }
}

</style>

</head>

<body>

<div class="logs-panel">
    <div class="logs-topo">
        <h2>📋 LOGS ESP32</h2>
        <div style="display:flex; gap:6px;">
            <button id="copiarLogs">📋 COPIAR</button>
            <button id="atualizarLogs">↻ ATUALIZAR</button>
        </div>
    </div>

    <div class="logs-filtros">
        <button class="filtro-log ativo" data-nivel="TODOS">TODOS</button>
        <button class="filtro-log" data-nivel="ERRO">ERROS</button>
        <button class="filtro-log" data-nivel="AVISO">AVISOS</button>
        <button class="filtro-log" data-nivel="OK">OK</button>
        <button class="filtro-log" data-nivel="INFO">INFO</button>
        <button id="limparLogs">LIMPAR</button>
    </div>

    <div id="logsLista" class="logs-lista">
        <div class="logs-vazio">Aguardando logs da ESP32...</div>
    </div>
</div>


<div class="container">

    <div class="card">

        <h1>ESP32 Rafael V1</h1>

        <div class="subtitulo">
            Servidor de áudio ESP32
        </div>

        <div class="ia-status-card">
            <div class="ia-status-topo">🤖 STATUS DA IA</div>
            <div id="iaStatusTitulo">⚪ IA AGUARDANDO</div>
            <div id="iaStatusDetalhe">Aguardando uma nova foto ou uma escolha de exercício.</div>
            <div id="iaStatusTempo">Neste estado há 0 s</div>
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



<div id="galeriaModal" class="galeria-modal">
    <div class="galeria-caixa">
        <div class="galeria-topo">
            <h2>🖼️ Galeria</h2>
            <button id="fecharGaleria">FECHAR</button>
        </div>
        <div id="galeriaGrid" class="galeria-grid"></div>
    </div>
</div>

<script>

// ======================================================
// LOGS ESP32
// ======================================================

const logsLista = document.getElementById("logsLista");
const atualizarLogsBtn = document.getElementById("atualizarLogs");
const copiarLogsBtn = document.getElementById("copiarLogs");
const limparLogsBtn = document.getElementById("limparLogs");
const filtrosLogs = document.querySelectorAll(".filtro-log");

let filtroLogAtual = "TODOS";
let ultimoSnapshotLogs = [];

function iconeNivel(nivel) {
    if (nivel === "OK") return "🟢";
    if (nivel === "ERRO") return "🔴";
    if (nivel === "AVISO") return "🟡";
    return "🔵";
}

function renderizarLogs() {
    // Guarda a posição atual da barra antes de reconstruir a lista.
    // Assim a atualização automática não "puxa" o usuário de volta para cima.
    const posicaoScrollAntes = logsLista.scrollTop;

    const filtrados = ultimoSnapshotLogs.filter(function(item) {
        return filtroLogAtual === "TODOS" || item.nivel === filtroLogAtual;
    });

    if (filtrados.length === 0) {
        logsLista.innerHTML =
            '<div class="logs-vazio">Nenhum log neste filtro.</div>';
        return;
    }

    logsLista.innerHTML = "";

    filtrados.forEach(function(item) {
        const caixa = document.createElement("div");
        caixa.className = "log-item log-" + item.nivel;

        const cab = document.createElement("div");
        cab.className = "log-cabecalho";

        const nivel = document.createElement("span");
        nivel.className = "log-nivel";
        nivel.textContent = iconeNivel(item.nivel) + " " + item.nivel;

        const hora = document.createElement("span");
        hora.className = "log-hora";
        hora.textContent = item.hora;

        const msg = document.createElement("div");
        msg.className = "log-msg";
        msg.textContent = item.mensagem;

        cab.appendChild(nivel);
        cab.appendChild(hora);
        caixa.appendChild(cab);
        caixa.appendChild(msg);
        logsLista.appendChild(caixa);
    });

    // Mantém exatamente a posição em que o usuário estava lendo.
    // Se estava no topo, continua no topo. Se desceu, continua onde parou.
    logsLista.scrollTop = Math.min(
        posicaoScrollAntes,
        Math.max(0, logsLista.scrollHeight - logsLista.clientHeight)
    );
}

async function carregarLogs() {
    try {
        const resposta = await fetch(
            "/api/logs?t=" + Date.now(),
            { cache: "no-store" }
        );

        const dados = await resposta.json();

        if (!resposta.ok) {
            throw new Error(dados.erro || "Erro ao consultar logs");
        }

        ultimoSnapshotLogs = (dados.logs || []).slice(0, 20);
        renderizarLogs();

    } catch (erro) {
        logsLista.innerHTML =
            '<div class="logs-vazio">Erro ao consultar logs.</div>';
    }
}

filtrosLogs.forEach(function(botao) {
    botao.onclick = function() {
        filtrosLogs.forEach(function(b) {
            b.classList.remove("ativo");
        });

        botao.classList.add("ativo");
        filtroLogAtual = botao.dataset.nivel;
        renderizarLogs();
    };
});

atualizarLogsBtn.onclick = carregarLogs;

copiarLogsBtn.onclick = async function() {
    const itens = ultimoSnapshotLogs.slice(0, 20).reverse();

    if (itens.length === 0) {
        const original = copiarLogsBtn.textContent;
        copiarLogsBtn.textContent = "SEM LOGS";
        setTimeout(() => copiarLogsBtn.textContent = original, 1200);
        return;
    }

    const texto = itens.map(function(item) {
        return "[" + item.hora + "] [" + item.nivel + "] " + item.mensagem;
    }).join("\\n");

    try {
        await navigator.clipboard.writeText(texto);
    } catch (erro) {
        const area = document.createElement("textarea");
        area.value = texto;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.focus();
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
    }

    const original = copiarLogsBtn.textContent;
    copiarLogsBtn.textContent = "✅ COPIADO";
    setTimeout(() => copiarLogsBtn.textContent = original, 1500);
};

limparLogsBtn.onclick = async function() {
    try {
        await fetch("/api/logs", { method: "DELETE" });
        ultimoSnapshotLogs = [];
        renderizarLogs();
    } catch (erro) {
        // Mantém a tela atual caso a limpeza falhe.
    }
};

// Atualização quase em tempo real, sem interferir no WebSocket da ESP32.
carregarLogs();
setInterval(carregarLogs, 1500);


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
const galeriaModal = document.getElementById("galeriaModal");
const galeriaGrid = document.getElementById("galeriaGrid");
const fecharGaleria = document.getElementById("fecharGaleria");


// ======================================================
// STATUS ESP32
// ======================================================

async function atualizarStatusIA() {
    try {
        const resposta = await fetch("/api/status-ia?t=" + Date.now(), { cache: "no-store" });
        const dados = await resposta.json();
        document.getElementById("iaStatusTitulo").textContent = dados.titulo || "⚪ IA AGUARDANDO";
        document.getElementById("iaStatusDetalhe").textContent = dados.detalhe || "";
        const iaStatusTempo = document.getElementById("iaStatusTempo");
        if (dados.estado === "AGUARDANDO") {
            iaStatusTempo.textContent = "";
        } else {
            iaStatusTempo.textContent =
                "Neste estado há " + (dados.segundos || 0) + " s";
        }
    } catch (erro) {
        document.getElementById("iaStatusTitulo").textContent = "🔴 STATUS DA IA INDISPONÍVEL";
        document.getElementById("iaStatusDetalhe").textContent = "Não foi possível consultar o estado atual.";
    }
}
atualizarStatusIA();
setInterval(atualizarStatusIA, 1000);

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


async function carregarGaleria() {
    galeriaGrid.innerHTML = '<div class="galeria-vazia">Carregando...</div>';

    try {
        const resposta = await fetch("/api/galeria");
        const dados = await resposta.json();

        if (!resposta.ok) {
            throw new Error(dados.erro || "Erro ao carregar galeria");
        }

        if (!dados.fotos || dados.fotos.length === 0) {
            galeriaGrid.innerHTML =
                '<div class="galeria-vazia">Ainda não há fotos recebidas.</div>';
            return;
        }

        galeriaGrid.innerHTML = "";

        dados.fotos.forEach(function(foto) {
            const card = document.createElement("div");
            card.className = "foto-card";

            const img = document.createElement("img");
            img.src = foto.url;
            img.alt = foto.nome;
            img.loading = "lazy";
            img.onclick = function() {
                window.open(foto.url, "_blank");
            };

            const info = document.createElement("div");
            info.className = "foto-info";
            info.textContent = foto.nome;

            card.appendChild(img);
            card.appendChild(info);
            galeriaGrid.appendChild(card);
        });

    } catch (erro) {
        galeriaGrid.innerHTML =
            '<div class="galeria-vazia">Erro ao carregar a galeria.</div>';
    }
}

botaoGaleria.onclick = async function() {
    galeriaModal.classList.add("aberta");
    await carregarGaleria();
};

fecharGaleria.onclick = function() {
    galeriaModal.classList.remove("aberta");
};

galeriaModal.onclick = function(evento) {
    if (evento.target === galeriaModal) {
        galeriaModal.classList.remove("aberta");
    }
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

@app.route("/api/status-ia")
def api_status_ia():
    with lock:
        dados = dict(status_ia)
    segundos = max(0, int(time.time() - dados.get("inicio", time.time())))
    return jsonify({
        "ok": True,
        "estado": dados.get("estado", "AGUARDANDO"),
        "titulo": dados.get("titulo", "⚪ IA AGUARDANDO"),
        "detalhe": dados.get("detalhe", ""),
        "segundos": segundos
    })


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
            "criado": time.time(),
            "menu": False,
            "tentativas_recuperacao": 0
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
# RECUPERACAO AUTOMATICA DE AUDIO
# A ESP32 chama este endpoint quando o download/gravacao falha.
# O servidor NAO chama a IA novamente: reenvia o mesmo WAV pronto.
# =========================================================

@app.route("/audio-download-erro/<audio_id>", methods=["GET"])
def audio_download_erro(audio_id):
    global esp_ws, aguardando_resposta_menu

    with lock:
        dados = audios.get(audio_id)
        socket_atual = esp_ws

        if dados is None:
            adicionar_log_esp32(
                "ERRO",
                f"ESP32 avisou falha do áudio {audio_id}, mas esse áudio não existe mais no servidor."
            )
            atualizar_status_ia(
                "ERRO_AUDIO",
                "🔴 ERRO NO ÁUDIO",
                "A ESP32 informou falha, mas o arquivo do áudio não foi encontrado no servidor."
            )
            return jsonify({"ok": False, "erro": "Áudio não encontrado."}), 404

        caminho = dados.get("arquivo")
        eh_menu = bool(dados.get("menu", False))
        tentativas = int(dados.get("tentativas_recuperacao", 0)) + 1
        dados["tentativas_recuperacao"] = tentativas
        dados["recebido"] = False

    if not caminho or not os.path.exists(caminho):
        adicionar_log_esp32(
            "ERRO",
            f"Falha no áudio {audio_id}: WAV não existe mais no disco do servidor."
        )
        atualizar_status_ia(
            "ERRO_AUDIO",
            "🔴 WAV NÃO ENCONTRADO",
            "Não foi possível reenviar o último áudio porque o arquivo WAV não existe mais."
        )
        return jsonify({"ok": False, "erro": "Arquivo WAV não existe."}), 404

    # Evita loop infinito caso exista problema persistente de rede/SD.
    if tentativas > 2:
        adicionar_log_esp32(
            "ERRO",
            f"Áudio {audio_id} falhou novamente. Limite de 2 reenvios automáticos atingido."
        )
        atualizar_status_ia(
            "ERRO_AUDIO",
            "🔴 ÁUDIO FALHOU NOVAMENTE",
            "O mesmo áudio já foi reenviado duas vezes. Verifique a rede ou o cartão SD antes de tentar novamente."
        )
        return jsonify({
            "ok": False,
            "erro": "Limite de reenvios automáticos atingido.",
            "tentativas": tentativas - 1
        }), 409

    if socket_atual is None or not esp_esta_online():
        adicionar_log_esp32(
            "ERRO",
            f"Não foi possível reenviar {audio_id}: ESP32 desconectada."
        )
        atualizar_status_ia(
            "ERRO_AUDIO",
            "🔴 ESP32 DESCONECTADA",
            "O áudio falhou, mas não posso reenviá-lo enquanto a ESP32 estiver desconectada."
        )
        return jsonify({"ok": False, "erro": "ESP32 desconectada."}), 503

    comando = ("NOVO_AUDIO_MENU|" if eh_menu else "NOVO_AUDIO|") + audio_id

    try:
        atualizar_status_ia(
            "REENVIANDO_AUDIO",
            "🟠 REENVIANDO ÚLTIMO ÁUDIO...",
            f"A ESP32 informou falha no download/gravação. Estou reenviando o mesmo WAV. Tentativa {tentativas} de 2."
        )
        adicionar_log_esp32(
            "AVISO",
            f"Falha no áudio {audio_id}. Reenviando automaticamente o mesmo WAV; tentativa {tentativas} de 2."
        )

        socket_atual.send(comando)

        # Se era um áudio com opções, o menu continua válido.
        if eh_menu:
            with lock:
                aguardando_resposta_menu = True

        adicionar_log_esp32(
            "INFO",
            f"Comando de recuperação enviado à ESP32: {comando}"
        )

        return jsonify({
            "ok": True,
            "audio_id": audio_id,
            "reenviado": True,
            "menu": eh_menu,
            "tentativa": tentativas,
            "comando": comando
        })

    except Exception as erro:
        adicionar_log_esp32(
            "ERRO",
            f"Falha ao reenviar o áudio {audio_id}: {erro}"
        )
        atualizar_status_ia(
            "ERRO_AUDIO",
            "🔴 FALHA AO REENVIAR ÁUDIO",
            "O servidor tentou repetir o último comando, mas não conseguiu enviá-lo à ESP32."
        )
        return jsonify({"ok": False, "erro": "Falha ao reenviar áudio."}), 500


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
# LOGS ESP32
# =========================================================

@app.route("/api/logs", methods=["GET"])
def api_logs():
    with lock:
        itens = list(reversed(logs_esp32))

    return jsonify({
        "ok": True,
        "quantidade": len(itens),
        "logs": itens
    })


@app.route("/api/logs", methods=["DELETE"])
def limpar_logs():
    with lock:
        logs_esp32.clear()

    return jsonify({
        "ok": True
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
        adicionar_log_esp32("INFO", "Comando TIRAR_FOTO enviado para a ESP32.")

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
# IA -> VOZ -> WAV -> ESP32
# Reutiliza exatamente o protocolo NOVO_AUDIO|ID já existente.
# =========================================================

def quantidade_por_extenso(numero):
    nomes = {
        1: "uma", 2: "duas", 3: "três", 4: "quatro", 5: "cinco",
        6: "seis", 7: "sete", 8: "oito", 9: "nove", 10: "dez"
    }
    return nomes.get(numero, str(numero))


def texto_menu_exercicios(quantidade):
    partes = []
    for numero in range(1, quantidade + 1):
        vezes = quantidade_por_extenso(numero)
        termo = "vez" if numero == 1 else "vezes"
        partes.append(
            f"Para ouvir a resolução do exercício {numero}, aperte {vezes} {termo} o botão."
        )
    partes.append(
        "Depois deste áudio, você terá quinze segundos para escolher. "
        "Se não apertar o botão, a sessão será encerrada."
    )
    return " ".join(partes)


def preparar_texto_para_tts(texto):
    """Evita pronuncias ruins de diferenciais matematicos no TTS."""
    import re
    texto = str(texto or "")
    texto = re.sub(r'(?<![A-Za-z])dx(?![A-Za-z])', 'd x', texto, flags=re.IGNORECASE)
    texto = re.sub(r'(?<![A-Za-z])dy(?![A-Za-z])', 'd y', texto, flags=re.IGNORECASE)
    texto = re.sub(r'(?<![A-Za-z])dz(?![A-Za-z])', 'd z', texto, flags=re.IGNORECASE)
    return texto


def criar_e_enviar_audio_ia(texto, menu=False):
    global esp_ws, aguardando_resposta_menu

    texto = (texto or "").strip()
    texto = preparar_texto_para_tts(texto)
    if not texto:
        adicionar_log_esp32("ERRO", "IA retornou texto vazio; áudio não criado.")
        return False

    if openai_client is None:
        adicionar_log_esp32("ERRO", "OPENAI_API_KEY ausente; áudio da IA não criado.")
        return False

    if not esp_esta_online():
        adicionar_log_esp32("ERRO", "ESP32 offline; áudio da IA não enviado.")
        return False

    audio_id = uuid.uuid4().hex[:12]
    caminho_mp3 = os.path.join(AUDIO_DIR, audio_id + "_ia.mp3")
    caminho_wav = os.path.join(AUDIO_DIR, audio_id + ".wav")

    try:
        atualizar_status_ia("CRIANDO_AUDIO", "🟠 CRIANDO ÁUDIO...", "A resposta da IA está pronta. Agora estou transformando o texto em voz.")
        adicionar_log_esp32("INFO", "Criando voz da resposta da IA...")

        resposta_audio = openai_client.audio.speech.create(
            model=OPENAI_TTS_MODEL,
            voice=OPENAI_TTS_VOICE,
            input=texto,
            response_format="mp3"
        )

        resposta_audio.stream_to_file(caminho_mp3)

        print(">>> IA TTS: MP3 CRIADO:", caminho_mp3, "<<<", flush=True)

        comando_ffmpeg = [
            FFMPEG,
            "-y",
            "-i", caminho_mp3,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            caminho_wav
        ]

        resultado = subprocess.run(
            comando_ffmpeg,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60
        )

        try:
            os.remove(caminho_mp3)
        except Exception:
            pass

        if resultado.returncode != 0 or not os.path.exists(caminho_wav):
            erro_ffmpeg = resultado.stderr.decode("utf-8", errors="ignore")
            print(">>> IA TTS: ERRO FFMPEG <<<", flush=True)
            print(erro_ffmpeg, flush=True)
            adicionar_log_esp32("ERRO", "Falha convertendo voz da IA para WAV.")
            return False

        tamanho_wav = os.path.getsize(caminho_wav)

        with lock:
            audios[audio_id] = {
                "arquivo": caminho_wav,
                "recebido": False,
                "criado": time.time(),
                "menu": bool(menu),
                "tentativas_recuperacao": 0
            }
            socket_atual = esp_ws

        if socket_atual is None:
            adicionar_log_esp32("ERRO", "WebSocket da ESP32 indisponível para áudio da IA.")
            return False

        comando = ("NOVO_AUDIO_MENU|" if menu else "NOVO_AUDIO|") + audio_id
        print(f">>> WS PARA ESP32: {comando} <<<", flush=True)
        socket_atual.send(comando)

        print("================================", flush=True)
        print(">>> AUDIO DA IA PRONTO <<<", flush=True)
        print("ID:", audio_id, flush=True)
        print("WAV:", caminho_wav, flush=True)
        print("TAMANHO:", tamanho_wav, "bytes", flush=True)
        print("FORMATO: PCM 16-bit / 44100 Hz / stereo", flush=True)
        print("SERVIDOR -> ESP32:", comando, flush=True)
        print("================================", flush=True)

        adicionar_log_esp32(
            "OK",
            f"Áudio da IA criado e enviado à ESP32: {audio_id} ({tamanho_wav} bytes)."
        )
        atualizar_status_ia(
            "AUDIO_ENVIADO",
            "📤 ÁUDIO ENVIADO PARA ESP32",
            "O áudio está pronto e a ESP32 foi avisada para baixar e reproduzir."
        )

        # A ESP sempre contará 15 s. O servidor só aceita a resposta
        # quando este áudio realmente contém um menu.
        if menu:
            with lock:
                aguardando_resposta_menu = True

        return True

    except Exception as erro:
        print(">>> ERRO CRIANDO/ENVIANDO AUDIO DA IA:", repr(erro), flush=True)
        adicionar_log_esp32("ERRO", f"Falha no áudio da IA: {erro}")
        atualizar_status_ia("ERRO", "🔴 ERRO AO CRIAR ÁUDIO", "Falha durante a criação ou envio do áudio. Consulte os logs.")
        return False


# =========================================================
# IA - ANALISA SOMENTE A FOTO NOVA RECEBIDA DA ESP32
# =========================================================

def analisar_foto_nova_com_ia(caminho, nome):
    global exercicios_atuais, foto_sessao_atual, nome_foto_sessao_atual, quantidade_exercicios_sessao

    if openai_client is None:
        print(">>> IA NAO CONFIGURADA: OPENAI_API_KEY AUSENTE <<<", flush=True)
        adicionar_log_esp32("AVISO", "Foto recebida, mas OPENAI_API_KEY não está configurada.")
        return

    try:
        atualizar_status_ia("LENDO_FOTO", "🟣 IA LENDO A FOTO...", "A foto chegou. Estou identificando os exercícios visíveis.")
        adicionar_log_esp32("INFO", f"IA iniciando análise da foto nova: {nome}")
        print(f">>> IA: ANALISANDO SOMENTE A FOTO NOVA: {nome} <<<", flush=True)

        with open(caminho, "rb") as arquivo:
            imagem_b64 = base64.b64encode(arquivo.read()).decode("ascii")

        resposta = openai_client.responses.create(
            model=OPENAI_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Leia a imagem e identifique somente os exercícios ou questões legíveis. "
                            "Responda EXATAMENTE neste formato, sem introdução e sem markdown:\\n"
                            "EXERCICIO 1: <enunciado completo do exercício 1>\\n"
                            "EXERCICIO 2: <enunciado completo do exercício 2>\\n"
                            "e assim sucessivamente. "
                            "Não invente exercício ilegível. Se houver apenas um, retorne somente EXERCICIO 1."
                        )
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:image/jpeg;base64," + imagem_b64
                    }
                ]
            }]
        )

        texto = (resposta.output_text or "").strip()
        print(">>> IA: EXERCICIOS EXTRAIDOS <<<", flush=True)
        print(texto, flush=True)

        import re
        encontrados = re.findall(
            r"EXERCICIO\s+\d+\s*:\s*(.*?)(?=\nEXERCICIO\s+\d+\s*:|\Z)",
            texto,
            flags=re.IGNORECASE | re.DOTALL
        )
        exercicios = [item.strip() for item in encontrados if item.strip()]

        if not exercicios:
            with lock:
                exercicios_atuais.clear()
                foto_sessao_atual = None
                nome_foto_sessao_atual = None
                quantidade_exercicios_sessao = 0
            adicionar_log_esp32("AVISO", "IA não encontrou exercício legível na foto.")
            criar_e_enviar_audio_ia(
                "Não consegui detectar nenhum exercício legível nesta imagem. "
                "Aguarde trinta segundos sem apertar o botão. Depois, tire outra foto.",
                menu=False
            )
            return

        quantidade = len(exercicios)

        with lock:
            # A transcrição serve apenas para contar/montar o menu.
            # A resolução usará novamente a FOTO ORIGINAL.
            exercicios_atuais = exercicios
            foto_sessao_atual = caminho
            nome_foto_sessao_atual = nome
            quantidade_exercicios_sessao = quantidade
        adicionar_log_esp32("OK", f"IA identificou {quantidade} exercício(s) na foto nova.")
        atualizar_status_ia("IDENTIFICADOS", f"🟢 {quantidade} EXERCÍCIO(S) IDENTIFICADO(S)", "A leitura terminou. Agora vou preparar o áudio com as opções.")

        texto_menu = (
            f"Identifiquei {quantidade} exercício" + ("" if quantidade == 1 else "s") + ". "
            + texto_menu_exercicios(quantidade)
        )

        criar_e_enviar_audio_ia(texto_menu, menu=True)

    except Exception as erro:
        print(">>> ERRO NA ANALISE DA IA:", repr(erro), flush=True)
        adicionar_log_esp32("ERRO", f"Falha da IA ao analisar {nome}: {erro}")
        atualizar_status_ia("ERRO", "🔴 ERRO NA IA", "Falha durante a análise da foto. Consulte os logs.")


# =========================================================
# RECEBER FOTO DA ESP32 + GALERIA
# =========================================================

@app.route("/upload-foto", methods=["POST"])
def upload_foto():
    dados = request.get_data(cache=False)

    if not dados:
        return jsonify({"erro": "Foto vazia."}), 400

    # Validação simples de JPEG: FF D8 ... FF D9.
    if len(dados) < 4 or dados[0:2] != b"\xff\xd8":
        return jsonify({"erro": "Arquivo recebido não parece JPEG."}), 400

    agora = datetime.now()
    base = agora.strftime("foto_%Y%m%d_%H%M%S_%f")
    nome = base + ".jpg"
    caminho = os.path.join(PASTA_GALERIA, nome)

    # Segurança extra contra colisão de nome.
    contador = 1
    while os.path.exists(caminho):
        nome = f"{base}_{contador}.jpg"
        caminho = os.path.join(PASTA_GALERIA, nome)
        contador += 1

    try:
        with open(caminho, "wb") as arquivo:
            arquivo.write(dados)
            arquivo.flush()
            os.fsync(arquivo.fileno())
    except Exception as erro:
        print("ERRO SALVANDO FOTO:", erro, flush=True)
        return jsonify({"erro": "Falha ao salvar foto."}), 500

    tamanho = os.path.getsize(caminho)
    if tamanho != len(dados):
        try:
            os.remove(caminho)
        except Exception:
            pass
        return jsonify({"erro": "Foto foi salva incompleta."}), 500

    print(
        f"FOTO RECEBIDA: {nome} - {tamanho} bytes",
        flush=True
    )
    adicionar_log_esp32("OK", f"Foto recebida pelo servidor: {nome} ({tamanho} bytes).")


    # A IA é disparada SOMENTE por esta foto nova já salva e validada.
    threading.Thread(
        target=analisar_foto_nova_com_ia,
        args=(caminho, nome),
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "nome": nome,
        "tamanho": tamanho,
        "url": f"/galeria/{nome}"
    }), 201


@app.route("/api/galeria", methods=["GET"])
def api_galeria():
    try:
        nomes = [
            nome for nome in os.listdir(PASTA_GALERIA)
            if nome.lower().endswith((".jpg", ".jpeg"))
        ]

        # Mais novas primeiro.
        nomes.sort(
            key=lambda nome: os.path.getmtime(
                os.path.join(PASTA_GALERIA, nome)
            ),
            reverse=True
        )

        fotos = [
            {
                "nome": nome,
                "url": f"/galeria/{nome}"
            }
            for nome in nomes
        ]

        return jsonify({
            "ok": True,
            "quantidade": len(fotos),
            "fotos": fotos
        })

    except Exception as erro:
        print("ERRO LISTANDO GALERIA:", erro, flush=True)
        return jsonify({"erro": "Falha ao listar galeria."}), 500


@app.route("/galeria/<path:nome>", methods=["GET"])
def arquivo_galeria(nome):
    return send_from_directory(PASTA_GALERIA, nome)

# =========================================================
# ESTADO LIVRE PARA NOVA FOTO (CONSULTA DA ESP32)
# =========================================================

def servidor_esta_livre_para_nova_foto():
    """
    A ESP32 só chama esta consulta quando ela própria está ociosa.
    Aqui confirmamos o outro lado: IA/servidor sem trabalho e sem menu ativo.
    """
    with lock:
        estado_atual = str(status_ia.get("estado", "")).upper().strip()
        menu_ativo = bool(aguardando_resposta_menu)

    return estado_atual == "AGUARDANDO" and not menu_ativo


# =========================================================
# WEBSOCKET
# =========================================================

@sock.route("/ws-esp32")
def websocket_esp32(ws):
    global aguardando_resposta_menu, foto_sessao_atual, nome_foto_sessao_atual, quantidade_exercicios_sessao

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
    adicionar_log_esp32("OK", "ESP32 conectada ao WebSocket do servidor.")

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

            # BOTAO FISICO DE FOTO
            if mensagem == "BOTAO_FOTO_ACIONADO":
                adicionar_log_esp32(
                    "INFO",
                    "Botao fisico de foto acionado. Servidor confirmou o comando."
                )
                ws.send("BOTAO_FOTO_OK")
                continue

            # LOG ENVIADO PELA ESP32
            if mensagem.startswith("LOG|"):
                partes_log = mensagem.split("|", 2)

                if len(partes_log) == 3:
                    adicionar_log_esp32(
                        partes_log[1],
                        partes_log[2]
                    )

                continue

            # CONSULTA: ESP32 localmente ociosa pergunta se servidor/IA também estão livres.
            if mensagem == "STATUS_LIVRE?":
                if servidor_esta_livre_para_nova_foto():
                    ws.send("STATUS_LIVRE_OK")
                    adicionar_log_esp32("OK", "ESP32 e servidor/IA livres para nova foto.")
                else:
                    ws.send("STATUS_OCUPADO")
                continue

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
                            audios[audio_id]["tentativas_recuperacao"] = 0

                    print(
                        ">>> WAV CONFIRMADO PELA ESP32:",
                        audio_id,
                        flush=True
                    )
                    adicionar_log_esp32("OK", f"ESP32 confirmou o áudio WAV {audio_id}.")
                    with lock:
                        esperando_menu_agora = aguardando_resposta_menu
                    if esperando_menu_agora:
                        atualizar_status_ia(
                            "AGUARDANDO_ESCOLHA",
                            "👆 AGUARDANDO SUA ESCOLHA",
                            "O áudio chegou à ESP32. Depois que terminar, escolha nos 15 segundos."
                        )

            # AUDIO TERMINOU DE SER REPRODUZIDO NA ESP32
            elif mensagem.startswith("AUDIO_REPRODUZIDO|"):
                partes = mensagem.split("|", 1)
                audio_id = partes[1].strip() if len(partes) == 2 else ""

                with lock:
                    dados_audio = audios.get(audio_id)
                    eh_menu = bool(dados_audio.get("menu", False)) if dados_audio else False

                    if dados_audio is not None:
                        dados_audio["reproduzido"] = True
                        dados_audio["reproduzido_em"] = time.time()

                adicionar_log_esp32(
                    "OK",
                    f"ESP32 confirmou reprodução completa do áudio {audio_id}."
                )

                # Áudio normal: a tarefa terminou. Libera o estado do servidor
                # para que STATUS_LIVRE? possa receber STATUS_LIVRE_OK.
                if not eh_menu:
                    atualizar_status_ia(
                        "AGUARDANDO",
                        "⚪ IA AGUARDANDO",
                        "Áudio reproduzido com sucesso. Aguardando uma nova foto."
                    )
                else:
                    # Áudio de menu: terminou de tocar, mas ainda há a janela
                    # de 15 segundos para o usuário escolher pelos cliques.
                    atualizar_status_ia(
                        "AGUARDANDO_ESCOLHA",
                        "👆 AGUARDANDO SUA ESCOLHA",
                        "Áudio reproduzido. Escolha o exercício durante a janela de 15 segundos."
                    )

                continue

            # ESCOLHA DE EXERCICIO PELOS CLIQUES
            elif mensagem.startswith("CLIQUES|"):
                partes = mensagem.split("|", 1)
                try:
                    quantidade = int(partes[1]) if len(partes) == 2 else 0
                except ValueError:
                    quantidade = 0

                with lock:
                    servidor_esperava_menu = aguardando_resposta_menu

                # A ESP envia CLIQUES depois de TODO áudio.
                # Fora de um menu, isso não executa absolutamente nada.
                if not servidor_esperava_menu:
                    print(
                        f">>> CLIQUES|{quantidade} IGNORADO: SERVIDOR NAO AGUARDAVA MENU <<<",
                        flush=True
                    )
                    adicionar_log_esp32(
                        "INFO",
                        f"CLIQUES|{quantidade} recebido fora de menu; ignorado."
                    )
                    continue

                # Uma resposta foi recebida para o menu atual.
                with lock:
                    aguardando_resposta_menu = False

                if quantidade <= 0:
                    with lock:
                        exercicios_atuais.clear()
                        foto_sessao_atual = None
                        nome_foto_sessao_atual = None
                        quantidade_exercicios_sessao = 0
                    print(">>> SESSAO ENCERRADA: CLIQUES|0 <<<", flush=True)
                    adicionar_log_esp32(
                        "OK",
                        "15 s sem clique: sessão encerrada e exercícios apagados."
                    )
                    atualizar_status_ia(
                        "AGUARDANDO",
                        "⚪ IA AGUARDANDO",
                        "Sessão encerrada. Aguardando uma nova foto."
                    )
                    continue

                with lock:
                    caminho_foto = foto_sessao_atual
                    nome_foto = nome_foto_sessao_atual
                    total = quantidade_exercicios_sessao

                if not caminho_foto or not os.path.exists(caminho_foto):
                    adicionar_log_esp32("ERRO", "Não existe foto original ativa para esta sessão.")
                    continue

                if quantidade > total:
                    with lock:
                        aguardando_resposta_menu = True
                    adicionar_log_esp32(
                        "AVISO",
                        f"Escolha {quantidade} inválida; existem {total} exercício(s) nesta foto."
                    )
                    continue

                adicionar_log_esp32(
                    "OK",
                    f"Exercício {quantidade} selecionado; IA relendo diretamente a foto original."
                )
                atualizar_status_ia(
                    "RESOLVENDO",
                    f"🟣 IA RESOLVENDO EXERCÍCIO {quantidade}...",
                    "Estou relendo a foto original e preparando a resolução guiada."
                )

                def resolver_exercicio_guiado_da_foto(numero, caminho_imagem, nome_imagem, total_exercicios):
                    try:
                        with open(caminho_imagem, "rb") as arquivo:
                            imagem_b64 = base64.b64encode(arquivo.read()).decode("ascii")

                        resposta = openai_client.responses.create(
                            model=OPENAI_MODEL,
                            input=[{
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            f"Observe novamente esta imagem original e resolva SOMENTE o exercício {numero}. "
                                            "Leia tudo DIRETAMENTE DA FOTO e confira a matemática antes de responder. "
                                            "A resposta será convertida em áudio para uma pessoa copiar a resolução no caderno. "

                                            "Produza SOMENTE O GUIA DE ESCRITA FIEL DA RESOLUÇÃO. "
                                            "Não dê prévia, não explique o enunciado, não ensine como fazer e não justifique operações. "
                                            "Não use comentários como 'vamos fazer', 'precisamos', 'observe que', 'como sabemos', "
                                            "'mantenha', 'continue', 'permanece' ou 'agora calculamos'. "
                                            "A pessoa quer apenas ouvir exatamente o que deve escrever, linha por linha. "

                                            f"Comece dizendo somente: 'Exercício {numero}. Guia de escrita.' "
                                            "Depois dite a resolução na ordem exata em que deve ser escrita no caderno. "
                                            "Cada nova linha deve ser COMPLETA e AUTOSSUFICIENTE. "
                                            "Nunca mande lembrar ou manter uma parte da linha anterior. "
                                            "Se uma integral continuar na próxima linha, dite a integral inteira novamente, com limites, expressão e diferencial. "
                                            "Em vez de dizer 'mantenha a integral', diga por exemplo: "
                                            "'Na linha de baixo, escreva integral de zero até dois...' e dite todo o restante da linha. "

                                            "Use apenas comandos mínimos necessários para copiar, como 'escreva', 'na linha de baixo', "
                                            "'abra parêntese', 'feche parêntese', 'abra colchete', 'feche colchete', "
                                            "'coloque o sinal de igualdade', 'limite inferior', 'limite superior', 'numerador' e 'denominador'. "

                                            "ESTRUTURAS MATEMÁTICAS: toda estrutura que possui conteúdo interno deve ser falada com abertura e fechamento explícitos, "
                                            "para a pessoa saber exatamente onde ela termina. Nunca deixe implícito o fim de uma estrutura. "

                                            "FRAÇÕES: sempre diga 'escreva uma fração'. "
                                            "Depois diga PRIMEIRO 'denominador sendo...' e dite todo o denominador. "
                                            "Em seguida diga 'e agora numerador sendo...' e dite todo o numerador. "
                                            "Assim que terminar o numerador, diga obrigatoriamente 'fim da fração'. "
                                            "Somente depois de dizer 'fim da fração' dite qualquer termo, sinal ou operação que esteja fora dela. "
                                            "Exemplo: x ao quadrado sobre dois mais x deve ser falado: "
                                            "'escreva uma fração, denominador sendo dois, e agora numerador sendo x ao quadrado, fim da fração, "
                                            "sinal de adição, x'. "

                                            "RAÍZES: sempre anuncie o começo e o fim. "
                                            "Para raiz quadrada diga 'faça uma raiz quadrada. Dentro da raiz...' e dite todo o conteúdo interno. "
                                            "Ao terminar o conteúdo diga obrigatoriamente 'fim da raiz'. "
                                            "Somente depois de 'fim da raiz' dite termos ou operações que estejam fora dela. "
                                            "Para outras raízes, identifique o índice, por exemplo 'faça uma raiz cúbica', dite o conteúdo e finalize com 'fim da raiz'. "

                                            "PARÊNTESES E COLCHETES: continue usando abertura e fechamento explícitos: "
                                            "'abra parêntese' e 'feche parêntese'; 'abra colchete' e 'feche colchete'. "
                                            "Se houver estruturas aninhadas, feche cada uma na ordem correta e fale cada fechamento. "

                                            "SINAIS: se o menos estiver entre termos realizando subtração, diga 'sinal menos de subtração'. "
                                            "Se um número ou termo tiver valor negativo, diga 'negativo' antes dele. "
                                            "Quando precisar ditar explicitamente o símbolo de um valor negativo, diga 'sinal menos de valor negativo'. "

                                            "PRONÚNCIA: escreva para a voz sempre 'd x', 'd y' e 'd z', separados; nunca dx, dy ou dz grudados. "
                                            "Fale expoentes como 'x ao quadrado', 'y ao cubo' e assim por diante. "

                                            "INTEGRAIS: dite primeiro a integral montada completa e depois cada linha da resolução pronta. "
                                            "Em toda nova linha em que ainda houver integral, fale novamente 'escreva integral de [limite inferior] até [limite superior]' "
                                            "e dite tudo que aparece naquela linha. "
                                            "Ao terminar uma integração, simplesmente dite a próxima expressão a ser escrita. "
                                            "Nunca fale 'primitiva', 'integral primitiva', 'antiderivada' ou explicações do método. "
                                            "O áudio deve ser um DITADO DA RESOLUÇÃO PRONTA, e não uma aula. "

                                            "Para Física ou outro tipo de exercício, siga a mesma regra: dite somente as linhas da resolução "
                                            "que devem ser copiadas, incluindo dados, fórmula, conversões, substituições, contas e resultado quando fizerem parte da escrita. "
                                            "Se houver itens a, b ou c, identifique o item e dite somente a resolução correspondente. "

                                            "Não use markdown, tabelas, comandos LaTeX ou nomes de comandos de formatação. "
                                            "Não pule etapas escritas importantes. Não invente informação ilegível. Não resolva outros exercícios. "
                                            "Na última linha diga 'Resposta final' e dite exatamente o resultado, com unidade quando houver. "
                                            "Se o exercício não estiver legível, diga somente que não conseguiu lê-lo com segurança e peça outra foto."
                                        )
                                    },
                                    {
                                        "type": "input_image",
                                        "image_url": "data:image/jpeg;base64," + imagem_b64
                                    }
                                ]
                            }]
                        )

                        resolucao = (resposta.output_text or "").strip()
                        if not resolucao:
                            resolucao = "Não consegui gerar a resolução deste exercício pela imagem."

                        texto_final = (
                            f"Exercício {numero}. {resolucao} "
                            "Agora você pode escolher novamente. "
                            + texto_menu_exercicios(total_exercicios)
                        )
                        criar_e_enviar_audio_ia(texto_final, menu=True)

                    except Exception as erro:
                        print(">>> ERRO AO RESOLVER PELA FOTO:", repr(erro), flush=True)
                        adicionar_log_esp32(
                            "ERRO",
                            f"Falha ao resolver exercício {numero} pela foto {nome_imagem}: {erro}"
                        )

                threading.Thread(
                    target=resolver_exercicio_guiado_da_foto,
                    args=(quantidade, caminho_foto, nome_foto, total),
                    daemon=True
                ).start()

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
        adicionar_log_esp32("AVISO", "WebSocket da ESP32 foi desconectado.")


# =========================================================
# EXECUÇÃO LOCAL
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
