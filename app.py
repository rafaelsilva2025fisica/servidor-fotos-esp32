from flask import Flask, jsonify, render_template_string, request, send_file, send_from_directory
from werkzeug.utils import secure_filename
from flask_sock import Sock
import threading
import time
import os
import base64
import uuid
import textwrap
import html
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

# Galeria adicional de resoluções (independente da galeria de fotos).
PASTA_RESOLUCOES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "galeria_resolucoes")
os.makedirs(PASTA_RESOLUCOES, exist_ok=True)
resolucoes_jobs = {}
resolucoes_lock = threading.Lock()
resolucoes_semaforo = threading.Semaphore(1)  # evita vários lotes simultâneos
GERAR_RESOLUCOES_AUTOMATICAS = os.environ.get("GERAR_RESOLUCOES_AUTOMATICAS", "1") == "1"


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
openai_client = OpenAI(api_key=OPENAI_API_KEY, timeout=90.0, max_retries=1) if OPENAI_API_KEY else None


# RLock evita deadlock quando uma rotina com lock chama outra que tambem usa lock.
lock = threading.RLock()

ultimo_sinal_esp = 0.0
esp_ws = None
audios = {}
reset_geracao = 0  # invalida trabalhos de audio iniciados antes do reset
reset_pendente = False  # reenviar na proxima conexao da placa

# Arquivos enviados manualmente para /downloads no SD da ESP32
arquivos_sd = {}

# =========================================================
# SESSAO ATUAL DE EXERCICIOS
# =========================================================
exercicios_atuais = []
aguardando_resposta_menu = False
foto_sessao_atual = None
nome_foto_sessao_atual = None
quantidade_exercicios_sessao = 0
gabarito_rapido_disponivel = False
texto_ultimo_gabarito = None
audios_exercicios_sessao = {}  # historico da foto
indice_audio_atual = 0  # 0 = menu inicial; -1 = gabarito; 1..N = exercicio
id_audio_atual = None

# Material-base opcional para a resolução dos exercícios do submenu.
# Também pode ser definido no Render por MATERIAL_BASE_FILE_ID.
material_base_file_id = os.environ.get("MATERIAL_BASE_FILE_ID", "").strip() or None
material_base_nome = "material configurado no Render" if material_base_file_id else None
MATERIAL_BASE_PREFIXO = "materialbase__"


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
        estado_anterior = str(status_ia.get("estado", "AGUARDANDO")).upper().strip()

        status_ia = {
            "estado": estado,
            "titulo": titulo,
            "detalhe": detalhe,
            "inicio": time.time()
        }

        socket_atual = esp_ws

    estado_novo = str(estado).upper().strip()

    # O servidor é a fonte do estado LIVRE/OCUPADO.
    # Só avisa a ESP32 quando houver uma transição REAL:
    # qualquer estado ocupado -> AGUARDANDO.
    if estado_anterior != "AGUARDANDO" and estado_novo == "AGUARDANDO":
        if socket_atual is not None:
            try:
                socket_atual.send("SISTEMA_LIVRE")
                adicionar_log_esp32(
                    "OK",
                    "Servidor mudou de OCUPADO para LIVRE e avisou a ESP32."
                )
            except Exception as erro:
                print(
                    ">>> NAO FOI POSSIVEL ENVIAR SISTEMA_LIVRE:",
                    repr(erro),
                    flush=True
                )

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
# PROTECAO: OPERACOES ABANDONADAS PELA ESP32
# Nao cancela processamento de IA; apenas espera por resposta da placa.
# =========================================================
ESTADOS_ESP_COM_TIMEOUT = {"REENVIANDO_AUDIO", "AGUARDANDO_AUDIO", "AGUARDANDO_REPRODUCAO", "AUDIO_ENVIADO", "AGUARDANDO_ESCOLHA", "BAIXANDO_AUDIO"}
ESTADOS_ERRO_COM_TIMEOUT = {"ERRO", "ERRO_AUDIO"}
TIMEOUT_ESTADO_ERRO = 30
TIMEOUT_ESTADO_ESP = 180  # segundos

def vigiar_estado_esp():
    while True:
        time.sleep(10)
        try:
            with lock:
                estado = str(status_ia.get("estado", "")).upper().strip()
                inicio = float(status_ia.get("inicio", time.time()))
            limite = (TIMEOUT_ESTADO_ERRO if estado in ESTADOS_ERRO_COM_TIMEOUT
                      else TIMEOUT_ESTADO_ESP if estado in ESTADOS_ESP_COM_TIMEOUT else None)
            if limite is not None and time.time() - inicio > limite:
                # Revalida o estado sob o lock para nao encerrar uma operacao nova.
                with lock:
                    ainda_antigo = (status_ia.get("estado") == estado and
                                    float(status_ia.get("inicio", 0)) == inicio)
                    if ainda_antigo:
                        # Evita aceitar cliques de um menu expirado.
                        global aguardando_resposta_menu
                        aguardando_resposta_menu = False
                if ainda_antigo:
                    adicionar_log_esp32("AVISO", f"Estado {estado} expirou apos {limite}s; liberando sistema.")
                    atualizar_status_ia("AGUARDANDO", "⚪ IA AGUARDANDO", "Operacao sem confirmacao ou erro anterior; sistema liberado.")
        except Exception as erro:
            print("ERRO NO VIGILANTE DE ESTADO:", repr(erro), flush=True)

threading.Thread(target=vigiar_estado_esp, daemon=True).start()

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

#resetLimpo {
    background: #da3633;
    color: white;
}

#uploadEsp {
    background: #238636;
    color: white;
}

#uploadMaterialBase {
    background: #1f6feb;
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

/* GALERIA DE MATERIAIS */
.material-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 14px;
}
.material-card.ativo { outline: 2px solid #3fb950; }
.material-nome { font-weight: bold; overflow-wrap: anywhere; margin-bottom: 8px; }
.material-status { color: #8b949e; font-size: 12px; margin-bottom: 10px; }
.material-acoes { display:flex; flex-wrap:wrap; gap:8px; }
.material-acoes button { padding:9px 11px; font-size:12px; }
.usar-material { background:#238636; color:white; }
.excluir-material { background:#da3633; color:white; }
#desativarMaterial { background:#8b949e; color:white; }
#novoMaterialGaleria { background:#1f6feb; color:white; }

/* GALERIA DE ÁUDIOS */
#audiosGaleria { background:#d29922; color:white; }
.audio-galeria-card {
    background:#161b22; border:1px solid #30363d; border-radius:10px; padding:14px;
}
.audio-galeria-titulo { font-weight:bold; margin-bottom:6px; overflow-wrap:anywhere; }
.audio-galeria-info { color:#8b949e; font-size:12px; margin-bottom:10px; }
.audio-galeria-card audio { width:100%; margin:8px 0 12px; }
.audio-galeria-acoes { display:flex; gap:8px; flex-wrap:wrap; }
.audio-galeria-acoes button { padding:9px 11px; font-size:12px; }
.excluir-audio { background:#da3633; color:white; }
#apagarTodosAudios { background:#da3633; color:white; }

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
        <p><a href="/treinador" style="color:#58a6ff;font-weight:bold">🧪 Laboratório de treinamento da IA — enviar exercício e testar narração</a></p>

        <div class="subtitulo">
            Servidor de áudio ESP32
        </div>

        <div class="ia-status-card">
            <div class="ia-status-topo">🤖 STATUS DA IA</div>
            <div id="sistemaOcupacao" style="font-size:18px; font-weight:bold; margin-bottom:10px;">🟢 SISTEMA LIVRE</div>
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
        <button id="abrirResolucoes" style="background:#0e7490;color:white;">📐 GALERIA DE RESOLUÇÕES</button>

        <button id="resetLimpo">
            🧹 RESET LIMPO
        </button>

        <input id="arquivoEsp" type="file" style="display:none;">
        <button id="uploadEsp">
            📁 UPLOAD PARA ESP32
        </button>

        <div id="mensagemUploadEsp" style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;color:#8b949e;font-size:13px;line-height:1.4;">
            Arquivos serão salvos em /downloads no cartão SD.
        </div>

        <input id="materialBaseInput" type="file" accept=".pdf,.txt,.md,.doc,.docx" style="display:none;">
        <button id="uploadMaterialBase">
            📚 GALERIA DE MATERIAIS
        </button>
        <div id="mensagemMaterialBase" style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;color:#8b949e;font-size:13px;line-height:1.4;">
            Nenhum material-base ativo.
        </div>

        <button id="audiosGaleria">
            🔊 GALERIA DE ÁUDIOS
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

<div id="resolucoesModal" class="galeria-modal">
    <div class="galeria-caixa">
        <div class="galeria-topo"><h2>📐 Resoluções automáticas</h2>
            <button id="fecharResolucoes" style="background:#30363d;color:white;">FECHAR</button></div>
        <div id="resolucoesEstado" style="color:#8b949e;margin-bottom:14px;"></div>
        <div id="resolucoesGrid" class="galeria-grid"></div>
    </div>
</div>

<div id="materiaisModal" class="galeria-modal">
    <div class="galeria-caixa">
        <div class="galeria-topo">
            <h2>📚 Galeria de Materiais</h2>
            <button id="fecharMateriais">FECHAR</button>
        </div>
        <div class="botoes" style="margin-top:0; margin-bottom:18px;">
            <button id="novoMaterialGaleria">+ ADICIONAR MATERIAL</button>
            <button id="desativarMaterial">DESATIVAR MATERIAL</button>
        </div>
        <div id="materiaisGrid" class="galeria-grid"></div>
    </div>
</div>

<div id="audiosModal" class="galeria-modal">
    <div class="galeria-caixa">
        <div class="galeria-topo">
            <h2>🔊 Galeria de Áudios</h2>
            <button id="fecharAudios">FECHAR</button>
        </div>
        <div class="botoes" style="margin-top:0; margin-bottom:18px;">
            <button id="apagarTodosAudios">🗑 APAGAR TODOS</button>
        </div>
        <div id="audiosGrid" class="galeria-grid"></div>
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
const botaoResetLimpo = document.getElementById("resetLimpo");
const botaoUploadEsp = document.getElementById("uploadEsp");
const arquivoEspInput = document.getElementById("arquivoEsp");
const mensagemUploadEsp = document.getElementById("mensagemUploadEsp");
const botaoUploadMaterialBase = document.getElementById("uploadMaterialBase");
const materialBaseInput = document.getElementById("materialBaseInput");
const mensagemMaterialBase = document.getElementById("mensagemMaterialBase");
const materiaisModal = document.getElementById("materiaisModal");
const materiaisGrid = document.getElementById("materiaisGrid");
const fecharMateriais = document.getElementById("fecharMateriais");
const novoMaterialGaleria = document.getElementById("novoMaterialGaleria");
const desativarMaterial = document.getElementById("desativarMaterial");
const mensagemCamera = document.getElementById("mensagemCamera");
const galeriaModal = document.getElementById("galeriaModal");
const galeriaGrid = document.getElementById("galeriaGrid");
const fecharGaleria = document.getElementById("fecharGaleria");
const botaoAudiosGaleria = document.getElementById("audiosGaleria");
const audiosModal = document.getElementById("audiosModal");
const audiosGrid = document.getElementById("audiosGrid");
const fecharAudios = document.getElementById("fecharAudios");
const apagarTodosAudios = document.getElementById("apagarTodosAudios");


// Galeria adicional: isolada das fotos e do fluxo de áudio.
const resolucoesModal = document.getElementById("resolucoesModal");
let atualizadorResolucoes = null;
let fotoResolucoesSelecionada = null;
async function carregarResolucoes() {
    try {
        const filtro = fotoResolucoesSelecionada ? "&foto=" + encodeURIComponent(fotoResolucoesSelecionada) : "";
        const r = await fetch("/api/resolucoes?t=" + Date.now() + filtro, {cache:"no-store"});
        const dados = await r.json();
        if (!r.ok) throw new Error("Falha ao consultar resoluções");
        const grid = document.getElementById("resolucoesGrid");
        grid.replaceChildren();
        // Exibe as resoluções na ordem numérica crescente: 1, 2, 3...
        // Mantém a ordem recebida para arquivos sem número identificável.
        const imagensOrdenadas = [...(dados.imagens || [])].sort((a, b) => {
            const numero = item => {
                const match = String(item.nome || "").match(/__(\d+)_/);
                return match ? Number(match[1]) : Number.POSITIVE_INFINITY;
            };
            return numero(a) - numero(b);
        });
        imagensOrdenadas.forEach(item => {
            const card = document.createElement("div"); card.className = "foto-card";
            const a = document.createElement("a"); a.href = item.url; a.target = "_blank"; a.rel = "noopener";
            const img = document.createElement("img"); img.src = item.url; img.alt = item.nome;
            a.appendChild(img); card.appendChild(a);
            const info = document.createElement("div"); info.className = "foto-info";
            const indice = item.nome.match(/__(\\d+)_/);
            info.textContent = indice ? "Resolução " + Number(indice[1]) : item.nome;
            card.appendChild(info);
            // Link de download individual, compatível com celular e computador.
            const baixar = document.createElement("a");
            baixar.href = "/resolucoes/baixar/" + encodeURIComponent(item.nome);
            baixar.textContent = "⬇️ BAIXAR IMAGEM";
            baixar.setAttribute("aria-label", "Baixar " + (indice ? "resolução " + Number(indice[1]) : item.nome));
            baixar.style.cssText = "display:block;margin:8px;padding:12px;text-align:center;background:#238636;color:white;border-radius:8px;font-weight:bold;text-decoration:none;touch-action:manipulation;";
            card.appendChild(baixar);
            grid.appendChild(card);
        });
        if (!dados.imagens || !dados.imagens.length) grid.textContent = "Ainda não há resoluções prontas para esta foto.";
        const ativos = (dados.jobs || []).filter(j => j.estado !== "concluido");
        document.getElementById("resolucoesEstado").textContent = ativos.length
            ? "⏳ Gerando imagens: " + ativos.map(j => j.concluidos + "/" + j.total).join(", ")
            : "Resoluções disponíveis: " + (dados.imagens || []).length;
    } catch(e) { document.getElementById("resolucoesEstado").textContent = "Erro: " + e.message; }
}
function abrirBibliotecaResolucoes(foto = null) {
    fotoResolucoesSelecionada = foto;
    resolucoesModal.querySelector("h2").textContent = foto
        ? "📐 Resoluções da foto: " + foto
        : "📐 Resoluções automáticas";
    resolucoesModal.classList.add("aberta");
    clearInterval(atualizadorResolucoes);
    carregarResolucoes();
    atualizadorResolucoes = setInterval(carregarResolucoes, 4000);
}
document.getElementById("abrirResolucoes").onclick = function() {
    abrirBibliotecaResolucoes();
};
document.getElementById("fecharResolucoes").onclick = function() {
    resolucoesModal.classList.remove("aberta"); clearInterval(atualizadorResolucoes);
};
resolucoesModal.onclick = function(e) {
    if (e.target === resolucoesModal) document.getElementById("fecharResolucoes").click();
};

// ======================================================
// STATUS ESP32
// ======================================================

async function atualizarStatusIA() {
    try {
        const resposta = await fetch("/api/status-ia?t=" + Date.now(), { cache: "no-store" });
        const dados = await resposta.json();
        document.getElementById("iaStatusTitulo").textContent = dados.titulo || "⚪ IA AGUARDANDO";
        document.getElementById("iaStatusDetalhe").textContent = dados.detalhe || "";
        document.getElementById("sistemaOcupacao").textContent =
            dados.sistema_livre ? "🟢 SISTEMA LIVRE" : "🟠 SISTEMA OCUPADO";
        document.getElementById("iaStatusTempo").textContent =
            "Neste estado há " + (dados.segundos || 0) + " s";
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
// UPLOAD DE ARQUIVO DIRETO PARA /downloads DA ESP32
// ======================================================

botaoUploadEsp.onclick = function() {
    arquivoEspInput.value = "";
    arquivoEspInput.click();
};

arquivoEspInput.onchange = async function() {
    const arquivo = arquivoEspInput.files && arquivoEspInput.files[0];
    if (!arquivo) return;

    botaoUploadEsp.disabled = true;
    mensagemUploadEsp.textContent = "📤 Enviando " + arquivo.name + " para o servidor...";

    const formulario = new FormData();
    formulario.append("arquivo", arquivo, arquivo.name);

    try {
        const resposta = await fetch("/upload-para-esp", {
            method: "POST",
            body: formulario
        });

        const dados = await resposta.json();

        if (!resposta.ok) {
            throw new Error(dados.erro || "Falha ao enviar arquivo.");
        }

        mensagemUploadEsp.textContent =
            "⬇️ ESP32 baixando " + dados.nome + " para /downloads...";

        const id = dados.arquivo_id;

        async function verificarArquivo() {
            try {
                const r = await fetch("/arquivo-esp-status/" + id + "?t=" + Date.now(), {
                    cache: "no-store"
                });
                const status = await r.json();

                if (status.salvo === true) {
                    mensagemUploadEsp.textContent =
                        "✅ " + status.nome + " salvo em /downloads/" + status.nome;
                    botaoUploadEsp.disabled = false;
                    return;
                }

                if (status.erro) {
                    throw new Error(status.erro);
                }

                setTimeout(verificarArquivo, 1000);
            } catch (erro) {
                mensagemUploadEsp.textContent = "❌ " + erro.message;
                botaoUploadEsp.disabled = false;
            }
        }

        verificarArquivo();

    } catch (erro) {
        mensagemUploadEsp.textContent = "❌ " + erro.message;
        botaoUploadEsp.disabled = false;
    }
};

// ======================================================
// GALERIA DE MATERIAIS DA IA
// ======================================================

botaoUploadMaterialBase.onclick = async function() {
    materiaisModal.classList.add("aberta");
    await carregarMateriais();
};

novoMaterialGaleria.onclick = function() {
    materialBaseInput.value = "";
    materialBaseInput.click();
};

fecharMateriais.onclick = function() {
    materiaisModal.classList.remove("aberta");
};

materiaisModal.onclick = function(evento) {
    if (evento.target === materiaisModal) materiaisModal.classList.remove("aberta");
};

materialBaseInput.onchange = async function() {
    const arquivo = materialBaseInput.files && materialBaseInput.files[0];
    if (!arquivo) return;
    novoMaterialGaleria.disabled = true;
    mensagemMaterialBase.textContent = "📤 Adicionando material à galeria...";
    const formulario = new FormData();
    formulario.append("arquivo", arquivo, arquivo.name);
    try {
        const resposta = await fetch("/material-base", {method:"POST", body:formulario});
        const dados = await resposta.json();
        if (!resposta.ok) throw new Error(dados.erro || "Falha ao enviar material.");
        mensagemMaterialBase.textContent = "✅ Material ativo: " + dados.nome;
        await carregarMateriais();
    } catch (erro) {
        mensagemMaterialBase.textContent = "❌ " + erro.message;
    } finally {
        novoMaterialGaleria.disabled = false;
    }
};

async function carregarMateriais() {
    materiaisGrid.innerHTML = '<div class="galeria-vazia">Carregando materiais...</div>';
    try {
        const resposta = await fetch("/api/materiais?t=" + Date.now(), {cache:"no-store"});
        const dados = await resposta.json();
        if (!resposta.ok) throw new Error(dados.erro || "Erro ao carregar materiais.");
        if (!dados.materiais || dados.materiais.length === 0) {
            materiaisGrid.innerHTML = '<div class="galeria-vazia">Nenhum material cadastrado.</div>';
            return;
        }
        materiaisGrid.innerHTML = "";
        dados.materiais.forEach(function(mat) {
            const card=document.createElement("div");
            card.className="material-card" + (mat.ativo ? " ativo" : "");
            const nome=document.createElement("div");
            nome.className="material-nome"; nome.textContent=(mat.ativo ? "🟢 " : "⚪ ") + mat.nome;
            const status=document.createElement("div");
            status.className="material-status"; status.textContent=mat.ativo ? "MATERIAL ATIVO" : "Disponível";
            const acoes=document.createElement("div"); acoes.className="material-acoes";
            if (!mat.ativo) {
                const usar=document.createElement("button"); usar.className="usar-material"; usar.textContent="USAR ESTE";
                usar.onclick=async function(){
                    const r=await fetch("/material-base/ativar", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({file_id:mat.file_id})});
                    const d=await r.json(); if(!r.ok){alert(d.erro||"Falha ao ativar.");return;}
                    mensagemMaterialBase.textContent="✅ Material ativo: " + d.nome; await carregarMateriais();
                };
                acoes.appendChild(usar);
            }
            const excluir=document.createElement("button"); excluir.className="excluir-material"; excluir.textContent="🗑 EXCLUIR";
            excluir.onclick=async function(){
                if(!confirm("Excluir " + mat.nome + " da galeria?")) return;
                const r=await fetch("/material-base/"+encodeURIComponent(mat.file_id),{method:"DELETE"});
                const d=await r.json(); if(!r.ok){alert(d.erro||"Falha ao excluir.");return;}
                await consultarMaterialBase(); await carregarMateriais();
            };
            acoes.appendChild(excluir); card.appendChild(nome); card.appendChild(status); card.appendChild(acoes); materiaisGrid.appendChild(card);
        });
    } catch(erro) { materiaisGrid.innerHTML='<div class="galeria-vazia">❌ '+erro.message+'</div>'; }
}

desativarMaterial.onclick = async function() {
    const r=await fetch("/material-base/desativar",{method:"POST"});
    const d=await r.json();
    if(!r.ok){alert(d.erro||"Falha ao desativar.");return;}
    mensagemMaterialBase.textContent="⚪ Nenhum material-base ativo.";
    await carregarMateriais();
};

async function consultarMaterialBase() {
    try {
        const resposta=await fetch("/material-base/status?t="+Date.now(),{cache:"no-store"});
        const dados=await resposta.json();
        mensagemMaterialBase.textContent=dados.ativo ? "✅ Material ativo: "+(dados.nome||"arquivo configurado") : "⚪ Nenhum material-base ativo.";
    } catch(erro) {}
}
consultarMaterialBase();

// ======================================================
// GALERIA DE ÁUDIOS
// ======================================================

botaoAudiosGaleria.onclick = async function() {
    audiosModal.classList.add("aberta");
    await carregarAudios();
};

fecharAudios.onclick = function() {
    audiosModal.classList.remove("aberta");
};

audiosModal.onclick = function(evento) {
    if (evento.target === audiosModal) audiosModal.classList.remove("aberta");
};

async function carregarAudios() {
    audiosGrid.innerHTML = '<div class="galeria-vazia">Carregando áudios...</div>';

    try {
        const resposta = await fetch("/api/audios?t=" + Date.now(), {cache:"no-store"});
        const dados = await resposta.json();
        if (!resposta.ok) throw new Error(dados.erro || "Erro ao carregar áudios.");

        if (!dados.audios || dados.audios.length === 0) {
            audiosGrid.innerHTML = '<div class="galeria-vazia">Ainda não há áudios no servidor.</div>';
            return;
        }

        audiosGrid.innerHTML = "";

        dados.audios.forEach(function(item) {
            const card = document.createElement("div");
            card.className = "audio-galeria-card";

            const titulo = document.createElement("div");
            titulo.className = "audio-galeria-titulo";
            titulo.textContent = "🔊 " + item.titulo;

            const info = document.createElement("div");
            info.className = "audio-galeria-info";
            info.textContent = item.hora + " • " + item.tamanho_formatado;

            const playerAudio = document.createElement("audio");
            playerAudio.controls = true;
            playerAudio.preload = "metadata";
            playerAudio.src = item.url;

            const acoes = document.createElement("div");
            acoes.className = "audio-galeria-acoes";

            const excluir = document.createElement("button");
            excluir.className = "excluir-audio";
            excluir.textContent = "🗑 EXCLUIR";
            excluir.onclick = async function() {
                if (!confirm("Excluir este áudio do servidor?")) return;

                const r = await fetch("/api/audios/" + encodeURIComponent(item.id), {
                    method:"DELETE"
                });
                const d = await r.json();

                if (!r.ok) {
                    alert(d.erro || "Não foi possível excluir o áudio.");
                    return;
                }

                await carregarAudios();
            };

            acoes.appendChild(excluir);
            card.appendChild(titulo);
            card.appendChild(info);
            card.appendChild(playerAudio);
            card.appendChild(acoes);
            audiosGrid.appendChild(card);
        });

    } catch (erro) {
        audiosGrid.innerHTML = '<div class="galeria-vazia">❌ ' + erro.message + '</div>';
    }
}

apagarTodosAudios.onclick = async function() {
    if (!confirm("Apagar TODOS os áudios que já foram recebidos pela ESP32?")) return;

    const r = await fetch("/api/audios", {method:"DELETE"});
    const d = await r.json();

    if (!r.ok) {
        alert(d.erro || "Não foi possível apagar os áudios.");
        return;
    }

    await carregarAudios();
};


// ======================================================
// CÂMERA
// ======================================================

botaoResetLimpo.onclick = async function() {
    const confirmou = window.confirm(
        "Limpar as memorias pendentes da ESP32 e reiniciar? Fotos e audios armazenados serao preservados."
    );

    if (!confirmou) return;

    botaoResetLimpo.disabled = true;
    mensagemCamera.textContent = "🧹 Solicitando reset limpo...";

    try {
        const resposta = await fetch("/reset-limpo", { method: "POST" });
        const dados = await resposta.json();

        if (!resposta.ok) {
            throw new Error(dados.erro || "Falha ao solicitar reset limpo.");
        }

        mensagemCamera.textContent =
            "✅ RESET LIMPO enviado. A ESP32 vai limpar os marcadores e reiniciar.";

        setTimeout(function() {
            botaoResetLimpo.disabled = false;
        }, 6000);

    } catch (erro) {
        mensagemCamera.textContent = "❌ " + erro.message;
        botaoResetLimpo.disabled = false;
    }
};


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
            const botaoResolucoes = document.createElement("button");
            botaoResolucoes.type = "button";
            botaoResolucoes.textContent = "📐 Ver resoluções desta foto";
            botaoResolucoes.style.cssText = "margin:8px;width:calc(100% - 16px);padding:10px;cursor:pointer;";
            botaoResolucoes.onclick = function() {
                galeriaModal.classList.remove("aberta");
                abrirBibliotecaResolucoes(foto.nome);
            };
            card.appendChild(botaoResolucoes);
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
        "segundos": segundos,
        "sistema_livre": str(dados.get("estado", "AGUARDANDO")).upper().strip() == "AGUARDANDO"
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
    # 8000 Hz
    # mono
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
            "8000",
            "-ac",
            "1",
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
        "FORMATO: PCM 16-bit / 8000 Hz / mono",
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
            "tipo_galeria": "manual",
            "titulo_galeria": "Áudio manual",
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

        # Áudio enviado manualmente pelo site também coloca o sistema
        # em OCUPADO. Quando a ESP32 confirmar AUDIO_REPRODUZIDO,
        # o handler já existente volta para AGUARDANDO e dispara
        # SISTEMA_LIVRE, fazendo as 3 piscadas.
        atualizar_status_ia(
            "AUDIO_MANUAL_ENVIADO",
            "📤 ÁUDIO ENVIADO PARA ESP32",
            "Áudio manual enviado. Aguardando a ESP32 terminar a reprodução."
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
        "sample_rate": 8000,
        "canais": 1,
        "tamanho": tamanho_wav
    })



# =========================================================
# GALERIA DE ÁUDIOS
# Permite ouvir no navegador os mesmos WAVs enviados à ESP32.
# =========================================================

def _formatar_tamanho_audio(tamanho):
    tamanho = int(tamanho or 0)
    if tamanho >= 1024 * 1024:
        return f"{tamanho / (1024 * 1024):.1f} MB"
    if tamanho >= 1024:
        return f"{tamanho / 1024:.0f} KB"
    return f"{tamanho} B"


@app.route("/api/audios", methods=["GET"])
def api_audios():
    itens = []

    with lock:
        snapshot = [
            (audio_id, dict(info))
            for audio_id, info in audios.items()
        ]

    for audio_id, info in snapshot:
        caminho = info.get("arquivo")
        if not caminho or not os.path.exists(caminho):
            continue

        criado = float(info.get("criado") or 0)
        titulo = str(info.get("titulo_galeria") or "").strip()

        if not titulo:
            titulo = "Menu da foto" if info.get("menu") else "Áudio"

        itens.append({
            "id": audio_id,
            "titulo": titulo,
            "tipo": info.get("tipo_galeria", "audio"),
            "hora": datetime.fromtimestamp(criado).strftime("%d/%m/%Y %H:%M:%S") if criado else "",
            "criado": criado,
            "recebido": bool(info.get("recebido")),
            "tamanho": os.path.getsize(caminho),
            "tamanho_formatado": _formatar_tamanho_audio(os.path.getsize(caminho)),
            "url": f"/api/audios/{audio_id}/arquivo"
        })

    itens.sort(key=lambda item: item["criado"], reverse=True)
    return jsonify({"ok": True, "audios": itens})


@app.route("/api/audios/<audio_id>/arquivo", methods=["GET"])
def ouvir_audio_galeria(audio_id):
    with lock:
        info = dict(audios.get(audio_id) or {})

    caminho = info.get("arquivo")
    if not caminho or not os.path.exists(caminho):
        return jsonify({"erro": "Áudio não encontrado."}), 404

    return send_file(
        caminho,
        mimetype="audio/wav",
        conditional=True,
        download_name=os.path.basename(caminho)
    )


@app.route("/api/audios/<audio_id>", methods=["DELETE"])
def excluir_audio_galeria(audio_id):
    with lock:
        info = dict(audios.get(audio_id) or {})

    if not info:
        return jsonify({"erro": "Áudio não encontrado."}), 404

    # Não apaga enquanto a ESP32 ainda pode precisar baixar o WAV.
    if not info.get("recebido"):
        return jsonify({
            "erro": "A ESP32 ainda não confirmou o recebimento deste áudio. Aguarde antes de excluir."
        }), 409

    caminho = info.get("arquivo")
    try:
        if caminho and os.path.exists(caminho):
            os.remove(caminho)
    except Exception as erro:
        return jsonify({"erro": f"Falha ao apagar o arquivo: {erro}"}), 500

    with lock:
        audios.pop(audio_id, None)

    adicionar_log_esp32("INFO", f"Áudio {audio_id} excluído da galeria do servidor.")
    return jsonify({"ok": True, "audio_id": audio_id})


@app.route("/api/audios", methods=["DELETE"])
def excluir_todos_audios_galeria():
    apagados = 0
    preservados = 0

    with lock:
        snapshot = [
            (audio_id, dict(info))
            for audio_id, info in audios.items()
        ]

    for audio_id, info in snapshot:
        # Preserva qualquer áudio que ainda não foi confirmado pela ESP32.
        if not info.get("recebido"):
            preservados += 1
            continue

        caminho = info.get("arquivo")
        try:
            if caminho and os.path.exists(caminho):
                os.remove(caminho)
            with lock:
                audios.pop(audio_id, None)
            apagados += 1
        except Exception:
            preservados += 1

    adicionar_log_esp32(
        "INFO",
        f"Limpeza da galeria de áudios: {apagados} apagado(s), {preservados} preservado(s)."
    )
    return jsonify({
        "ok": True,
        "apagados": apagados,
        "preservados": preservados
    })


# =========================================================
# GALERIA DE MATERIAIS DA IA
# Os arquivos ficam armazenados na API de arquivos da OpenAI.
# O prefixo identifica somente os materiais desta galeria.
# =========================================================

def _nome_material_exibicao(nome_api):
    nome_api = str(nome_api or "")
    if nome_api.startswith(MATERIAL_BASE_PREFIXO):
        return nome_api[len(MATERIAL_BASE_PREFIXO):]
    return nome_api

def _listar_materiais_openai():
    if openai_client is None:
        return []
    itens=[]
    pagina=openai_client.files.list(limit=100)
    while True:
        for arq in pagina.data:
            nome_api=str(getattr(arq,"filename","") or "")
            if nome_api.startswith(MATERIAL_BASE_PREFIXO):
                itens.append({
                    "file_id": arq.id,
                    "nome": _nome_material_exibicao(nome_api),
                    "ativo": arq.id == material_base_file_id,
                    "criado": getattr(arq,"created_at",None)
                })
        if not getattr(pagina,"has_more",False):
            break
        ultimo=pagina.data[-1].id if pagina.data else None
        if not ultimo: break
        pagina=openai_client.files.list(limit=100, after=ultimo)
    itens.sort(key=lambda x: x.get("criado") or 0, reverse=True)
    return itens

@app.route("/material-base", methods=["POST"])
def enviar_material_base():
    global material_base_file_id, material_base_nome
    if openai_client is None:
        return jsonify({"erro":"OPENAI_API_KEY ausente."}),500
    if "arquivo" not in request.files:
        return jsonify({"erro":"Nenhum arquivo recebido."}),400
    recebido=request.files["arquivo"]
    nome=secure_filename(recebido.filename or "")
    if not nome:
        return jsonify({"erro":"Nome de arquivo inválido."}),400
    extensao=os.path.splitext(nome)[1].lower()
    if extensao not in {".pdf",".txt",".md",".doc",".docx"}:
        return jsonify({"erro":"Use PDF, TXT, MD, DOC ou DOCX."}),400
    # O nome temporário vira o filename remoto e permite reconstruir a galeria após restart do Render.
    caminho=os.path.join(AUDIO_DIR, MATERIAL_BASE_PREFIXO + nome)
    recebido.save(caminho)
    try:
        with open(caminho,"rb") as arquivo:
            enviado=openai_client.files.create(file=arquivo,purpose="user_data")
        material_base_file_id=enviado.id
        material_base_nome=nome
        adicionar_log_esp32("OK",f"Material adicionado e ativado: {nome}.")
        return jsonify({"ok":True,"nome":nome,"file_id":enviado.id})
    except Exception as erro:
        adicionar_log_esp32("ERRO",f"Falha ao enviar material-base: {erro}")
        return jsonify({"erro":"Falha ao enviar o material-base para a IA."}),500
    finally:
        try: os.remove(caminho)
        except Exception: pass

@app.route("/api/materiais", methods=["GET"])
def api_materiais():
    try:
        return jsonify({"ok":True,"materiais":_listar_materiais_openai()})
    except Exception as erro:
        return jsonify({"erro":f"Falha ao listar materiais: {erro}"}),500

@app.route("/material-base/ativar", methods=["POST"])
def ativar_material_base():
    global material_base_file_id, material_base_nome
    dados=request.get_json(silent=True) or {}
    file_id=str(dados.get("file_id","")).strip()
    if not file_id:
        return jsonify({"erro":"Material inválido."}),400
    try:
        materiais=_listar_materiais_openai()
        escolhido=next((m for m in materiais if m["file_id"]==file_id),None)
        if not escolhido:
            return jsonify({"erro":"Material não encontrado na galeria."}),404
        material_base_file_id=file_id
        material_base_nome=escolhido["nome"]
        adicionar_log_esp32("OK",f"Material ativo: {material_base_nome}.")
        return jsonify({"ok":True,"nome":material_base_nome,"file_id":file_id})
    except Exception as erro:
        return jsonify({"erro":f"Falha ao ativar material: {erro}"}),500

@app.route("/material-base/desativar", methods=["POST"])
def desativar_material_base():
    global material_base_file_id, material_base_nome
    material_base_file_id=None
    material_base_nome=None
    adicionar_log_esp32("INFO","Material-base desativado; IA resolverá sem material de referência.")
    return jsonify({"ok":True})

@app.route("/material-base/<file_id>", methods=["DELETE"])
def excluir_material_base(file_id):
    global material_base_file_id, material_base_nome
    try:
        materiais=_listar_materiais_openai()
        escolhido=next((m for m in materiais if m["file_id"]==file_id),None)
        if not escolhido:
            return jsonify({"erro":"Material não encontrado na galeria."}),404
        openai_client.files.delete(file_id)
        if material_base_file_id==file_id:
            material_base_file_id=None
            material_base_nome=None
        adicionar_log_esp32("INFO",f"Material excluído: {escolhido['nome']}.")
        return jsonify({"ok":True,"nome":escolhido["nome"]})
    except Exception as erro:
        return jsonify({"erro":f"Falha ao excluir material: {erro}"}),500

@app.route("/material-base/status", methods=["GET"])
def status_material_base():
    return jsonify({"ativo":bool(material_base_file_id),"nome":material_base_nome,"file_id":material_base_file_id})


# =========================================================
# UPLOAD MANUAL -> /downloads DA ESP32
# =========================================================

@app.route("/upload-para-esp", methods=["POST"])
def upload_para_esp():
    global esp_ws

    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo selecionado."}), 400

    if not esp_esta_online():
        return jsonify({"erro": "ESP32 está desconectada."}), 503

    recebido = request.files["arquivo"]
    nome = secure_filename(recebido.filename or "")

    if not nome:
        return jsonify({"erro": "Nome de arquivo inválido."}), 400

    arquivo_id = uuid.uuid4().hex[:12]
    caminho = os.path.join(AUDIO_DIR, "sd_" + arquivo_id + "_" + nome)
    recebido.save(caminho)

    if not os.path.exists(caminho) or os.path.getsize(caminho) <= 0:
        return jsonify({"erro": "Arquivo vazio ou não foi salvo no servidor."}), 400

    with lock:
        arquivos_sd[arquivo_id] = {
            "arquivo": caminho,
            "nome": nome,
            "salvo": False,
            "criado": time.time()
        }
        socket_atual = esp_ws

    if socket_atual is None:
        return jsonify({"erro": "Canal da ESP32 não está disponível."}), 503

    comando = f"ARQUIVO_SD|{arquivo_id}|{nome}"

    try:
        socket_atual.send(comando)
        adicionar_log_esp32(
            "INFO",
            f"Upload solicitado para /downloads/{nome} ({os.path.getsize(caminho)} bytes)."
        )
    except Exception as erro:
        return jsonify({"erro": f"Falha ao avisar a ESP32: {erro}"}), 500

    return jsonify({
        "ok": True,
        "arquivo_id": arquivo_id,
        "nome": nome,
        "tamanho": os.path.getsize(caminho)
    })


@app.route("/arquivo-esp/<arquivo_id>", methods=["GET"])
def baixar_arquivo_esp(arquivo_id):
    with lock:
        dados = arquivos_sd.get(arquivo_id)

    if dados is None:
        return jsonify({"erro": "Arquivo não encontrado."}), 404

    caminho = dados["arquivo"]
    if not os.path.exists(caminho):
        return jsonify({"erro": "Arquivo não existe mais no servidor."}), 404

    return send_file(
        caminho,
        as_attachment=False,
        download_name=dados["nome"]
    )


@app.route("/arquivo-esp-status/<arquivo_id>", methods=["GET"])
def status_arquivo_esp(arquivo_id):
    with lock:
        dados = arquivos_sd.get(arquivo_id)
        if dados is None:
            return jsonify({"salvo": False, "erro": "Arquivo não encontrado."}), 404
        return jsonify({
            "salvo": bool(dados.get("salvo", False)),
            "nome": dados.get("nome", "")
        })


# =========================================================
# DOWNLOAD DO WAV
# =========================================================

@app.route("/audio/<audio_id>", methods=["GET"])
def baixar_audio(audio_id):

    with lock:
        dados = audios.get(audio_id)

    if dados is None or dados.get("cancelado_reset"):

        return jsonify({
            "erro": "Áudio não encontrado ou cancelado pelo reset."
        }), 404

    caminho = dados["arquivo"]

    if not os.path.exists(caminho):

        return jsonify({
            "erro": "Arquivo não existe."
        }), 404

    # Flask/Werkzeug suporta Range (206) e Content-Range com conditional=True.
    # O ESP32 solicita blocos de tamanho limitado e pode retomar apos queda.
    tamanho = os.path.getsize(caminho)
    faixa = request.headers.get("Range", "")
    print(f">>> DOWNLOAD WAV {audio_id}: {faixa or 'completo'} / {tamanho} bytes", flush=True)

    # O watchdog conta inatividade entre blocos, nao duracao total do download.
    with lock:
        estado_atual = str(status_ia.get("estado", "")).upper()
        if estado_atual in ESTADOS_ESP_COM_TIMEOUT:
            status_ia["estado"] = "BAIXANDO_AUDIO"
            status_ia["titulo"] = "⬇️ BAIXANDO ÁUDIO"
            status_ia["detalhe"] = f"ESP32 recebendo WAV {audio_id} em blocos."
            status_ia["inicio"] = time.time()

    resposta = send_file(
        caminho,
        mimetype="audio/wav",
        as_attachment=False,
        download_name="audio.wav",
        conditional=True,
        etag=True,
    )
    resposta.headers["Accept-Ranges"] = "bytes"
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


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
            # ID antigo do SD: nao prender o status global nem tentar reenviar.
            pass
        else:
            caminho = dados.get("arquivo")
            eh_menu = bool(dados.get("menu", False))
            tentativas = int(dados.get("tentativas_recuperacao", 0)) + 1
            dados["tentativas_recuperacao"] = tentativas
            dados["recebido"] = False

    if dados is None or dados.get("cancelado_reset"):
        adicionar_log_esp32("AVISO", f"Recuperacao ignorada: audio antigo/inexistente {audio_id}.")
        return jsonify({"ok": False, "erro": "Audio antigo nao encontrado; descarte a pendencia no SD."}), 404

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

@app.route("/reset-limpo", methods=["POST"])
def reset_limpo_esp32():
    global esp_ws, reset_geracao, reset_pendente
    global aguardando_resposta_menu, foto_sessao_atual, nome_foto_sessao_atual
    global quantidade_exercicios_sessao, gabarito_rapido_disponivel
    global texto_ultimo_gabarito, indice_audio_atual, id_audio_atual

    # Reset prioritario: nao depende do estado OCUPADO, do audio ou do WebSocket.
    # Preserva as galerias e os arquivos no disco.
    with lock:
        reset_geracao += 1
        reset_pendente = True
        aguardando_resposta_menu = False
        exercicios_atuais.clear()
        foto_sessao_atual = None
        nome_foto_sessao_atual = None
        quantidade_exercicios_sessao = 0
        gabarito_rapido_disponivel = False
        texto_ultimo_gabarito = None
        audios_exercicios_sessao.clear()
        indice_audio_atual = 0
        id_audio_atual = None
        # Impede que o firmware baixe novamente WAVs de sessoes anteriores.
        for item in audios.values():
            item["cancelado_reset"] = True
        socket_atual = esp_ws

    atualizar_status_ia("AGUARDANDO", "⚪ IA AGUARDANDO", "Reset limpo: sessao e audios pendentes cancelados.")
    adicionar_log_esp32("AVISO", "RESET LIMPO: sessao limpa; audios antigos bloqueados.")

    if socket_atual is not None:
        try:
            socket_atual.send("RESET_LIMPO")
            with lock:
                reset_pendente = False
            adicionar_log_esp32("AVISO", "RESET_LIMPO enviado; aguardando reinicio/reconexao.")
            return jsonify({"ok": True, "comando": "RESET_LIMPO", "pendente": True})
        except Exception as erro:
            adicionar_log_esp32("AVISO", f"ESP32 sem resposta ao reset; comando pendente para reconexao: {erro}")

    return jsonify({"ok": True, "comando": "RESET_LIMPO", "pendente": True,
                    "aviso": "Servidor limpo; ESP32 recebera reset quando reconectar."})


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


def opcoes_menu(exercicios, incluir_gabarito=False, atual=0):
    """Mapeamento falado e recebido: 1 repete, 2+ seleciona outro item."""
    itens = [("exercicio", i, str(ex.get("numero", i)).strip())
             for i, ex in enumerate(exercicios, 1)]
    if incluir_gabarito:
        itens.append(("gabarito", -1, "gabarito rápido"))
    if atual:
        itens = [item for item in itens if item[1] != atual]
    return itens


def texto_menu_exercicios(exercicios, incluir_gabarito=False, repetir_gabarito=False, atual=0):
    partes = []
    if atual:
        nome = ("gabarito rápido" if atual == -1
                else f"exercício {exercicios[atual - 1].get('numero', atual)}")
        partes.append(f"Um clique para ouvir novamente o {nome}.")
    inicio = 2 if atual else 1
    for posicao, (tipo, indice, numero) in enumerate(
            opcoes_menu(exercicios, incluir_gabarito, atual), start=inicio):
        destino = numero if tipo == "gabarito" else f"exercício {numero}"
        partes.append(f"{quantidade_por_extenso(posicao).capitalize()} "
                      f"{'clique' if posicao == 1 else 'cliques'} para o {destino}.")
    partes.append("Para encerrar, não aperte o botão durante quinze segundos.")
    return " ".join(partes)


def reenviar_audio_da_sessao(audio_id):
    """Reaproveita WAV e ID já existentes, sem consultar IA/TTS."""
    global aguardando_resposta_menu
    with lock:
        dados = audios.get(audio_id)
        if dados is None or dados.get("cancelado_reset"):
            return False
        socket = esp_ws
    if not dados or not os.path.isfile(dados.get("arquivo", "")) or socket is None:
        return False
    try:
        socket.send("NOVO_AUDIO_MENU|" + audio_id)
        with lock:
            aguardando_resposta_menu = True
        atualizar_status_ia("AUDIO_ENVIADO", "📤 REPETINDO EXERCÍCIO", "Reutilizando WAV anterior da sessão.")
        adicionar_log_esp32("OK", f"Reutilizando WAV {audio_id}; sem IA nem TTS.")
        return True
    except Exception as erro:
        adicionar_log_esp32("ERRO", f"Falha repetindo WAV {audio_id}: {erro}")
        return False


def preparar_texto_para_tts(texto):
    """Evita pronuncias ruins de diferenciais matematicos no TTS."""
    import re
    texto = str(texto or "")
    # Pronuncia convencional do guia: "del" identifica somente os diferenciais.
    # Nao alterar a escrita matematica da resolucao, apenas o texto para voz.
    for variavel, falado in (("theta", "teta"), ("teta", "teta"), ("θ", "teta"),
                            ("x", "xis"), ("y", "ipsilon"), ("z", "ze"), ("r", "erre"),
                            ("t", "te"), ("u", "u"), ("v", "ve")):
        texto = re.sub(r'(?<![A-Za-z])d\s*' + re.escape(variavel) + r'(?![A-Za-z])',
                       'del ' + falado, texto, flags=re.IGNORECASE)
    return texto


def criar_e_enviar_audio_ia(texto, menu=False, velocidade=1.0):
    global esp_ws, aguardando_resposta_menu
    with lock:
        geracao_inicio = reset_geracao

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
            response_format="mp3",
            speed=velocidade
        )

        resposta_audio.stream_to_file(caminho_mp3)

        print(">>> IA TTS: MP3 CRIADO:", caminho_mp3, "<<<", flush=True)

        comando_ffmpeg = [
            FFMPEG,
            "-y",
            "-i", caminho_mp3,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "8000",
            "-ac", "1",
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
            if geracao_inicio != reset_geracao:
                adicionar_log_esp32("AVISO", "Audio antigo descartado apos RESET LIMPO.")
                return False
            audios[audio_id] = {
                "arquivo": caminho_wav,
                "recebido": False,
                "criado": time.time(),
                "menu": bool(menu),
                "tipo_galeria": (
                    "resolucao" if float(velocidade) < 0.99
                    else ("menu" if menu else "ia")
                ),
                "titulo_galeria": (
                    "Resolução de exercício"
                    if float(velocidade) < 0.99
                    else ("Menu da foto" if menu else "Resposta da IA")
                ),
                "tentativas_recuperacao": 0
            }
            socket_atual = esp_ws

        if socket_atual is None:
            adicionar_log_esp32("ERRO", "WebSocket da ESP32 indisponível para áudio da IA.")
            return False

        with lock:
            if geracao_inicio != reset_geracao:
                audios[audio_id]["cancelado_reset"] = True
                return False
        comando = ("NOVO_AUDIO_MENU|" if menu else "NOVO_AUDIO|") + audio_id
        print(f">>> WS PARA ESP32: {comando} <<<", flush=True)
        socket_atual.send(comando)

        print("================================", flush=True)
        print(">>> AUDIO DA IA PRONTO <<<", flush=True)
        print("ID:", audio_id, flush=True)
        print("WAV:", caminho_wav, flush=True)
        print("TAMANHO:", tamanho_wav, "bytes", flush=True)
        print("FORMATO: PCM 16-bit / 8000 Hz / mono", flush=True)
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

        return audio_id

    except Exception as erro:
        print(">>> ERRO CRIANDO/ENVIANDO AUDIO DA IA:", repr(erro), flush=True)
        adicionar_log_esp32("ERRO", f"Falha no áudio da IA: {erro}")
        atualizar_status_ia("ERRO", "🔴 ERRO AO CRIAR ÁUDIO", "Falha durante a criação ou envio do áudio. Consulte os logs.")
        return False



# Mesma instrução de narração usada na ESP32 e no laboratório web.
PROMPT_DITADO_EXERCICIO = (
    "Leia literalmente a resolução matemática do exercício {numero}, respeitando exatamente "
    "os símbolos, a ordem visual e as quebras de linha. Não explique, não recalcule e não "
    "invente passos. Fale os títulos das seções, como Rotacional e Divergente. "
    "A cada nova linha de expressão matemática diga exatamente: Linha de baixo. "
    "Nunca diga Pula linha nem Próxima linha. "
    "Leia TODOS os sinais explicitamente: + = sinal mais; − ou - = sinal menos; "
    "= = sinal de igual; × = sinal de multiplicação vetorial quando for entre vetores, "
    "caso contrário sinal de multiplicação; · = sinal de multiplicação escalar entre vetores; "
    "÷ = sinal de divisão. Não omita sinais dentro de parênteses, frações ou resultados. "
    "Pronuncie ∂ como dél, jamais derivada parcial. "
    "Para cada fração vertical diga: Fração, numerador, [expressão de cima], "
    "denominador, [expressão de baixo], fim da fração. "
    "Leia parênteses como abre parênteses e fecha parênteses, expoentes como ao quadrado, "
    "ao cubo etc., e índices como subscrito xis, subscrito ípsilon, subscrito zê. "
    "REGRA OBRIGATÓRIA DE MAIÚSCULAS PARA TODAS AS LETRAS: sempre que uma letra "
    "estiver em maiúscula, diga o nome da letra seguido de maiúsculo (ou maiúscula "
    "conforme a pronúncia natural); se estiver em minúscula, diga apenas o nome "
    "da letra, sem falar minúsculo. Preserve a diferença em todas as fórmulas, "
    "vetores, índices e variáveis, incluindo letras gregas quando houver distinção. "
    "Exemplos: F = efe maiúsculo; f = efe; Fₓ = efe maiúsculo subscrito xis; "
    "Rₓ = érre maiúsculo subscrito xis; x = xis; X = xis maiúsculo; "
    "A = á maiúsculo; a = á. Não anuncie maiúsculo para símbolos ∂ ou ∇. "
    "Leia x como xis, y como ípsilon e z como zê. "
    "Para ∇ diga sinal de gradiente. Leia módulos e integrais indicando limites "
    "e delimitadores necessários. Não use Markdown nem LaTeX; entregue somente as palavras "
    "que serão pronunciadas. Se o enunciado ou a resolução estiver ilegível, não invente."
)

def prompt_ditado_exercicio(numero):
    return PROMPT_DITADO_EXERCICIO.replace("{numero}", str(numero))

# =========================================================
# PADRÃO COMPARTILHADO: RESOLVER -> OTIMIZAR -> NARRAR
# Exemplos aprovados no laboratório também orientam a ESP32.
# =========================================================
import json

PASTA_TREINAMENTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "treinamento_ia")
os.makedirs(PASTA_TREINAMENTO, exist_ok=True)
ARQUIVO_EXEMPLOS = os.path.join(PASTA_TREINAMENTO, "exemplos_aprovados.json")
exemplos_lock = threading.RLock()

PADRAO_RESOLUCAO = (
    "Resolva somente o exercício solicitado, em português brasileiro, com cálculos corretos. "
    "FORMATO DA RESOLUÇÃO ESCRITA: texto Unicode legível, sem LaTeX ou Markdown matemático. "
    "Use ², ³, ∂, ∇, ×, · e −. Escreva derivadas como ∂(expressão)/∂x. "
    "Comece diretamente pela substituição nas operações solicitadas, sem repetir fórmula geral abstrata. "
    "Quando houver rotacional e divergente, escreva OBRIGATORIAMENTE os títulos Rotacional e Divergente, cada um em sua própria linha antes dos respectivos cálculos. "
    "REGRA DE CONCISÃO: em derivadas parciais simples de polinômios, faça a derivação mentalmente "
    "e passe DIRETAMENTE da expressão de derivadas para a linha de subtração ou soma dos resultados. "
    "No rotacional, para cada componente Rₓ, Rᵧ e R_z, use SOMENTE duas linhas: "
    "primeiro a expressão com as duas derivadas parciais e depois a igualdade "
    "com os resultados derivados e a simplificação final. "
    "Exemplo: Rₓ = ∂(2xz+y²)/∂y − ∂(x²+2yz)/∂z; na linha seguinte Rₓ = 2y − 2y = 0. "
    "NÃO escreva separadamente ∂(2xz+y²)/∂y = 0+2y nem ∂(x²+2yz)/∂z = 0+2y "
    "quando as derivadas forem imediatas. "
    "No divergente, quando as componentes Fₓ, Fᵧ e F_z já estiverem identificadas, "
    "não repita os polinômios dentro das derivadas: escreva diretamente "
    "∂Fₓ/∂x = parcelas = resultado, ∂Fᵧ/∂y = parcelas = resultado, "
    "∂F_z/∂z = parcelas = resultado, cada um em sua linha. "
    "Mostre obrigatoriamente as parcelas intermediárias, inclusive termos nulos quando existirem, "
    "como ∂Fₓ/∂x = 2y + 0 = 2y; não abrevie diretamente para 2y. "
    "Não coloque parênteses ao redor de Fₓ, Fᵧ ou F_z no numerador da derivada: "
    "use ∂Fₓ/∂x, nunca ∂(Fₓ)/∂x. "
    "Finalize com ∇·F = resultado final diretamente simplificado e ordenado "
    "(ex.: ∇·F = 2x + 2y + 2z), sem escrever antes a soma em outra ordem. "
    "Atenção: Fₓ sozinho NÃO é a derivada e Rₓ designa rotacional, nunca divergente. "
    "Para exercícios de função potencial escalar, escreva OBRIGATORIAMENTE os títulos em linhas próprias e nesta ordem: Passo Integração em x; Passo Integração em y; Passo Verificação em z; Resultado final. " "Use o título geral Função potencial somente se não substituir esses quatro títulos. Adapte a variável da etapa se a integração começar por outra componente. "
    "Nunca pule a operação de integração: após ∂φ/∂x = P, mostre φ = ∫(P) dx e só então a expressão integrada. "
    "Quando determinar g(y,z) a partir de ∂g/∂y = Q, mostre g(y,z) = ∫(Q) dy antes do resultado. "
    "Se houver integração em z, mostre a integral correspondente. Para integrais indefinidas, use ∫(expressão) dx, dy ou dz, sem INT{}{}. "
    "Use φ ou ϕ de maneira consistente, respeitando a notação do enunciado; não omita constantes ou funções de integração. "
    "EXCEÇÃO IMPORTANTE: para derivadas difíceis, regras do produto, cadeia, quociente, "
    "derivadas implícitas, integrais ou manipulações não triviais, mantenha as etapas "
    "intermediárias realmente necessárias para verificar a conta. "
    "Não omita sinais, limites, substituições essenciais nem resultados. "
    "Não use matrizes para rotacional se o cálculo direto for mais simples. "
    "Para integrais definidas, use INT{inferior}{superior} conforme o formato do servidor. "
    "Para integral de linha de campo conservativo, não refaça os itens anteriores: Passo Aplicação do teorema, Passo Substituição dos pontos e Resultado final uma vez no encerramento. Calcule os potenciais dos extremos separadamente e finalize diretamente com ∫_C F·dr = valor. "
    "EM TODOS os tipos de exercícios, coloque Resultado final em linha própria imediatamente antes da última expressão conclusiva; uma única vez e nunca no meio da resolução. "
    "Nos títulos das etapas, nunca use números, enumeração, hífen ou travessão: escreva exatamente Passo Integração em x, Passo Integração em y, Passo Verificação em z e Resultado final (uma vez só no encerramento), adaptando as variáveis ao exercício. Retorne SOMENTE a resolução escrita."
)

EXEMPLO_APROVADO = (
    "EXEMPLO DE ESTILO COMPACTO, NÃO COPIE RESULTADOS PARA OUTROS EXERCÍCIOS.\n"
    "Para F=(2xy+z², x²+2yz, 2xz+y²):\n"
    "Rotacional\n"
    "Rₓ = ∂(2xz+y²)/∂y − ∂(x²+2yz)/∂z\n"
    "Rₓ = 2y − 2y = 0\n\n"
    "Rᵧ = ∂(2xy+z²)/∂z − ∂(2xz+y²)/∂x\n"
    "Rᵧ = 2z − 2z = 0\n\n"
    "R_z = ∂(x²+2yz)/∂x − ∂(2xy+z²)/∂y\n"
    "R_z = 2x − 2x = 0\n"
    "∇×F = (0,0,0)\n\n"
    "Divergente\n"
    "∂Fₓ/∂x = 2y + 0 = 2y\n"
    "∂Fᵧ/∂y = 0 + 2z = 2z\n"
    "∂F_z/∂z = 2x + 0 = 2x\n"
    "∇·F = 2x + 2y + 2z\n"
    "Derivadas triviais são calculadas diretamente; mantenha passos para operações difíceis."
)

def carregar_exemplos_aprovados():
    with exemplos_lock:
        try:
            with open(ARQUIVO_EXEMPLOS, 'r', encoding='utf-8') as f:
                dados = json.load(f)
            return dados if isinstance(dados, list) else []
        except (OSError, ValueError):
            return []

def contexto_exemplos():
    exemplos = carregar_exemplos_aprovados()[-5:]
    if not exemplos:
        return EXEMPLO_APROVADO
    blocos = [EXEMPLO_APROVADO, "EXEMPLOS APROVADOS PELO USUÁRIO (referência de estilo, nunca copie resultados):"]
    for ex in exemplos:
        blocos.append("Resolução aprovada:\n" + ex['esperada'][:4000] +
                      "\nErro a evitar: " + ex.get('erro', '')[:1200])
    return "\n\n".join(blocos)

REGRA_COMPLETUDE_CURVAS = '\nREGRA DE COMPLETUDE GEOMÉTRICA (OBRIGATÓRIA): em integrais de linha sobre fronteira\nfechada, identifique TODOS os trechos da fronteira, incluindo segmentos verticais\nou horizontais implícitos nos limites da região. Determine os vértices/pontos de\ninterseção e a orientação solicitada. Para cada trecho, escreva ponto inicial,\nponto final e parametrização; confira que o fim de um coincide com o início do\nseguinte e que o último termina no início do primeiro. Só depois integre e some\nTODAS as contribuições. Não considere uma curva fechada se houver lacuna.\nExemplo geométrico (não copie os valores para outros problemas): para\n0 ≤ x ≤ 1 e 0 ≤ y ≤ x², a fronteira possui TRÊS partes: y=0 de (0,0) a (1,0),\nx=1 de (1,0) a (1,1), e y=x² de (1,1) a (0,0), nesta ordem anti-horária.\nSe uma parte estiver ausente, a resposta está INCOMPLETA: corrija antes de\nentregar, mesmo que as integrais já feitas estejam certas. Em outros exercícios,\nreconstrua a geometria a partir da foto, sem assumir três trechos.\nEm itens isolados, faça todos os cálculos auxiliares necessários, mas não\nresolva separadamente outros subitens. Não presuma que itens anteriores foram feitos.\n'

def garantir_titulo_resultado_final(resolucao):
    """Garante um único marcador audível antes da última expressão conclusiva."""
    import re
    linhas = [linha.rstrip() for linha in str(resolucao).splitlines()]
    # Não transforma mensagens de erro ou ausência de enunciado em respostas matemáticas.
    texto = " ".join(linhas).lower()
    if not linhas or any(x in texto for x in (
        "enunciado não encontrado", "enunciado nao encontrado",
        "não encontrei o enunciado", "nao encontrei o enunciado",
        "revisao_insegura", "revisão insegura"
    )):
        return resolucao
    # Remove somente títulos isolados; mantém expressões e cálculos intactos.
    linhas = [linha for linha in linhas if not re.fullmatch(
        r"\s*(?:#{1,4}\s*)?resultado\s+final\s*[:：.]?\s*", linha, re.I
    )]
    while linhas and not linhas[-1].strip():
        linhas.pop()
    if not linhas:
        return resolucao
    # Não cria um resultado numérico: usa a última linha já produzida e auditada.
    ultima = linhas.pop().strip()
    if not ultima or not any(c in ultima for c in "=≃≈→:"):
        linhas.append(ultima)
        return "\n".join(linhas).strip()  # Evita inventar conclusão.
    while linhas and not linhas[-1].strip():
        linhas.pop()
    return "\n".join(linhas + ["", "Resultado final", ultima]).strip()


def executar_resolucao_padronizada(numero, imagem_url, arquivo_base_id=None):
    """Fluxo único para laboratório e exercícios escritos enviados pela ESP32."""
    conteudo = [{'type': 'input_text', 'text': (
        f'Leia a imagem e resolva SOMENTE o exercício {numero}.\n' +
        PADRAO_RESOLUCAO + '\n\n' + REGRA_COMPLETUDE_CURVAS + '\n\n' + contexto_exemplos())}]
    if arquivo_base_id:
        conteudo.append({'type':'input_file','file_id':arquivo_base_id})
    conteudo.append({'type':'input_image','image_url':imagem_url})
    primeira = openai_client.responses.create(model=OPENAI_MODEL,
        input=[{'role':'user','content':conteudo}])
    escrita = (primeira.output_text or '').strip()
    if not escrita:
        raise ValueError('A IA não produziu a resolução escrita.')

    # Revisão com acesso à foto original: verifica também cortes indevidos de cálculos.
    segunda = openai_client.responses.create(model=OPENAI_MODEL,
        input=[{'role':'user','content':[
            {'type':'input_text','text':(
                'Revise esta resolução comparando-a à FOTO ORIGINAL. ' + REGRA_COMPLETUDE_CURVAS + ' Corrija erros matemáticos, '
                'sinais, limites e resultados. Elimine fórmulas preparatórias redundantes, '
                'repetições do lado esquerdo e passos sem operação matemática nova. '
                'Não omita passos indispensáveis para verificar a conta, mas corte passos triviais. '
                'Inclua os títulos Rotacional e Divergente quando ambas as operações forem solicitadas. Para função potencial, separe OBRIGATORIAMENTE em Passo Integração em x, Passo Integração em y, Passo Verificação em z e Resultado final (adapte variáveis ao caso). Mostre cada integral indefinida explicitamente (por exemplo φ = ∫(2xy+z²) dx) ANTES do resultado integrado; mostre também g(y,z) = ∫(2yz) dy quando aplicável. Não pule integrais. '
                'No rotacional, cada componente simples deve ter apenas a linha com as duas derivadas '
                'e a linha seguinte com os resultados e a subtração, sem derivadas individuais. '
                'No divergente, use ∂Fₓ/∂x = parcelas = resultado (ex.: 2y + 0 = 2y), ∂Fᵧ/∂y e ∂F_z/∂z analogamente; mostre os termos nulos. Não use parênteses em torno de Fₓ, Fᵧ ou F_z: nunca ∂(Fₓ)/∂x. Resultado ∇·F diretamente ordenado, sem repetir soma desordenada. Não confunda com Rₓ do rotacional. '
                'Não use LaTeX ou comandos como frac, partial, nabla; somente texto Unicode legível. '
                'Preserve o método mais simples e a notação legível. '
                'Se não puder conferir a matemática, responda somente REVISAO_INSEGURA. '
                'Em qualquer tipo de exercício, mantenha Resultado final exatamente uma vez, imediatamente antes da última expressão conclusiva. '
                'Devolva SOMENTE a resolução escrita corrigida.\n\n' +
                PADRAO_RESOLUCAO + '\n\nRESOLUÇÃO ORIGINAL:\n' + escrita)},
            {'type':'input_image','image_url':imagem_url}
        ]}])
    escrita = (segunda.output_text or '').strip()
    if not escrita or escrita == 'REVISAO_INSEGURA':
        raise ValueError('REVISAO_INSEGURA')

    escrita = garantir_titulo_resultado_final(escrita)

    # A narração acompanha a resolução escrita, linha por linha, sem alterar a matemática.
    # As quebras são enumeradas para reduzir a chance de a IA pular a locução de transição.
    linhas_escritas = [linha.strip() for linha in escrita.splitlines() if linha.strip()]
    escrita_para_narrar = "\n".join(
        f"LINHA {indice}: {linha}" for indice, linha in enumerate(linhas_escritas, 1)
    )
    terceira = openai_client.responses.create(model=OPENAI_MODEL,
        input=[{'role':'user','content':[{'type':'input_text','text':(
            'Transforme a RESOLUÇÃO ESCRITA em narração LITERAL, sem resolver novamente. '
            'Cada LINHA numerada corresponde a uma linha visual da imagem. Não fale os números '
            'de identificação LINHA 1, LINHA 2 etc. Antes de CADA linha depois da primeira, '
            'diga exatamente Linha de baixo. Inclusive antes de um novo título ou resultado. '
            'Fale os títulos Rotacional e Divergente quando aparecerem. '
            'Leia todos os sinais: + como sinal mais, − ou - como sinal menos, '
            '= como sinal de igual, × como sinal de multiplicação vetorial quando aplicável, '
            '· como sinal de multiplicação escalar quando aplicável e ÷ como sinal de divisão. '
            'Pronuncie ∂ como dél, NUNCA derivada parcial. '
            'Para cada fração diga Fração, numerador, conteúdo de cima, denominador, '
            'conteúdo de baixo, fim da fração, preservando cada sinal em ambos. '
            'Leia ( como abre parênteses, ) como fecha parênteses, ² como ao quadrado, '
            '³ como ao cubo, x como xis, y como ípsilon, z como zê. '
            'Regra obrigatória para TODAS as letras: pronuncie explicitamente maiúsculo '
            'quando a letra escrita for maiúscula; quando for minúscula, fale só o nome '
            'da letra, sem dizer minúsculo. Preserve maiúsculas em vetores, componentes, '
            'índices e variáveis. F = efe maiúsculo; f = efe; Fₓ = efe maiúsculo '
            'subscrito xis; Rₓ = érre maiúsculo subscrito xis; Rᵧ = érre maiúsculo '
            'subscrito ípsilon; R_z = érre maiúsculo subscrito zê; X = xis maiúsculo; '
            'x = xis. Para ∇ diga sinal de gradiente. '
            'Não acrescente explicações, justificativas, contas nem palavras introdutórias. '
            'Não diga Pula linha nem Próxima linha. Sem Markdown ou LaTeX. '
            'Responda SOMENTE o texto que será falado. '
            'EXEMPLO DE LEITURA: Rₓ = 6y − 4y = 2y vira '
            'érre maiúsculo subscrito xis, sinal de igual, seis ípsilon, sinal menos, '
            'quatro ípsilon, sinal de igual, dois ípsilon. '
            'EXEMPLO DE FRAÇÃO: ∂(5xz+3y²)/∂y vira '
            'fração, numerador, dél, abre parênteses, cinco xis zê, sinal mais, '
            'três ípsilon ao quadrado, fecha parênteses, denominador, dél ípsilon, fim da fração. '
            'RESOLUÇÃO ESCRITA APROVADA (LINHAS VISUAIS):\n' + escrita_para_narrar
        )}]}])
    narracao = (terceira.output_text or '').strip()
    if not narracao:
        raise ValueError('A IA não produziu o texto para áudio.')
    return escrita, narracao

# =========================================================
# IA - ANALISA SOMENTE A FOTO NOVA RECEBIDA DA ESP32
# =========================================================

def analisar_foto_nova_com_ia(caminho, nome):
    global exercicios_atuais, foto_sessao_atual, nome_foto_sessao_atual, quantidade_exercicios_sessao, gabarito_rapido_disponivel, texto_ultimo_gabarito, audios_exercicios_sessao, indice_audio_atual, id_audio_atual

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
                            "Leia a imagem e identifique cada QUESTÃO E CADA SUBITEM legível como opção INDEPENDENTE do menu. "
                            "Se houver questão 1 com itens a e b, devolva EXERCICIO 1A e EXERCICIO 1B em linhas distintas. "
                            "Se houver questão 3 com itens a e b, devolva EXERCICIO 3A e EXERCICIO 3B. "
                            "Cada subitem deve trazer o enunciado comum da questão e a condição específica do subitem. "
                            "Não devolva EXERCICIO 3 contendo a e b juntos quando ambos forem legíveis. "
                            "Preserve obrigatoriamente o NÚMERO REAL impresso ao lado de cada exercício na foto. "
                            "NÃO renumere os exercícios começando em 1. "
                            "Exemplo: se a foto mostrar somente os exercícios 3 e 4, retorne EXERCICIO 3 e EXERCICIO 4. "
                            "Responda EXATAMENTE neste formato, sem introdução e sem markdown:\\n"
                            "EXERCICIO <numero real>: <enunciado completo>\\n"
                            "EXERCICIO <numero real>: <enunciado completo>\\n"
                            "Não invente exercício ilegível."
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
            r"EXERCICIO\s+([A-Za-z0-9._-]+)\s*:\s*(.*?)(?=\nEXERCICIO\s+[A-Za-z0-9._-]+\s*:|\Z)",
            texto,
            flags=re.IGNORECASE | re.DOTALL
        )
        exercicios = [
            {"numero": numero.strip(), "enunciado": enunciado.strip()}
            for numero, enunciado in encontrados
            if enunciado.strip()
        ]

        if not exercicios:
            with lock:
                exercicios_atuais.clear()
                texto_ultimo_gabarito = None
                foto_sessao_atual = None
                nome_foto_sessao_atual = None
                quantidade_exercicios_sessao = 0
                gabarito_rapido_disponivel = False
            adicionar_log_esp32("AVISO", "IA não encontrou exercício legível na foto.")
            criar_e_enviar_audio_ia(
                "Não consegui detectar nenhum exercício legível nesta imagem. "
                "Aguarde trinta segundos sem apertar o botão. Depois, tire outra foto.",
                menu=False,
                velocidade=1.4
            )
            return

        quantidade = len(exercicios)

        # Verifica se TODAS as questões têm alternativas objetivas.
        # Na dúvida, não oferece o gabarito rápido.
        gabarito_disponivel = False
        try:
            verificacao = openai_client.responses.create(
                model=OPENAI_MODEL,
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": (
                        "Confira na FOTO ORIGINAL se TODOS os exercícios identificados são questões "
                        "de múltipla escolha com alternativas para marcar (A/B/C/D/E ou similares). "
                        "Responda EXATAMENTE SIM se todos forem de múltipla escolha; "
                        "caso haja alguma questão discursiva, cálculo sem alternativas ou dúvida, responda NAO."
                    )},
                    {"type": "input_image", "image_url": "data:image/jpeg;base64," + imagem_b64}
                ]}]
            )
            gabarito_disponivel = (verificacao.output_text or "").strip().upper() == "SIM"
        except Exception as erro_tipo:
            adicionar_log_esp32("AVISO", f"Não foi possível classificar a foto para gabarito rápido: {erro_tipo}")

        with lock:
            # A transcrição serve apenas para contar/montar o menu.
            # A resolução usará novamente a FOTO ORIGINAL.
            exercicios_atuais = exercicios
            audios_exercicios_sessao.clear()
            indice_audio_atual = 0
            id_audio_atual = None
            foto_sessao_atual = caminho
            nome_foto_sessao_atual = nome
            quantidade_exercicios_sessao = quantidade
            gabarito_rapido_disponivel = gabarito_disponivel
        adicionar_log_esp32("OK", f"Menu: {quantidade} item(ns) independente(s): " + ", ".join(numeros for numeros in [str(e.get("numero", "")) for e in exercicios]))
        atualizar_status_ia("IDENTIFICADOS", f"🟢 {quantidade} EXERCÍCIO(S) IDENTIFICADO(S)", "A leitura terminou. Agora vou preparar o áudio com as opções.")

        # Confirmação visual na ESP32: a IA realmente terminou de ler a foto.
        # Não dispara apenas porque a foto chegou ao servidor.
        with lock:
            socket_atual = esp_ws

        if socket_atual is not None:
            try:
                socket_atual.send("IA_LEU_FOTO")
                adicionar_log_esp32(
                    "OK",
                    "IA terminou de ler a foto; comando IA_LEU_FOTO enviado à ESP32."
                )
            except Exception as erro:
                adicionar_log_esp32(
                    "AVISO",
                    f"IA leu a foto, mas não foi possível enviar IA_LEU_FOTO à ESP32: {erro}"
                )

        numeros_identificados = [str(item.get("numero", "")).strip() for item in exercicios]
        if quantidade == 1:
            identificacao = f"Identifiquei 1 exercício: exercício {numeros_identificados[0]}. "
        else:
            identificacao = (
                f"Identifiquei {quantidade} exercícios: "
                + ", ".join(f"exercício {numero}" for numero in numeros_identificados[:-1])
                + f" e exercício {numeros_identificados[-1]}. "
            )

        texto_menu = identificacao + texto_menu_exercicios(exercicios, incluir_gabarito=gabarito_disponivel)

        criar_e_enviar_audio_ia(texto_menu, menu=True, velocidade=1.4)

        # Recurso EXTRA: só inicia depois do fluxo principal preparar o menu.
        # Não altera status_ia, esp_ws, áudios nem variáveis da sessão.
        if GERAR_RESOLUCOES_AUTOMATICAS:
            iniciar_resolucoes_em_segundo_plano(caminho, nome, exercicios)


    except Exception as erro:
        print(">>> ERRO NA ANALISE DA IA:", repr(erro), flush=True)
        adicionar_log_esp32("ERRO", f"Falha da IA ao analisar {nome}: {erro}")
        atualizar_status_ia("ERRO", "🔴 ERRO NA IA", "Falha durante a análise da foto. Consulte os logs.")


# =========================================================
# RESOLUÇÕES AUTOMÁTICAS - GALERIA INDEPENDENTE
# =========================================================

def _partes_matematicas(linha):
    """Separa integrais INT{inferior}{superior} sem confundir y² e 1."""
    partes = []
    pos = 0
    while pos < len(linha):
        inicio = linha.find("INT{", pos)
        if inicio < 0:
            partes.append(("texto", linha[pos:]))
            break
        if inicio > pos:
            partes.append(("texto", linha[pos:inicio]))
        cursor = inicio + 3
        limites = []
        for _ in range(2):
            if cursor >= len(linha) or linha[cursor] != "{":
                break
            profundidade = 1
            começo = cursor + 1
            cursor += 1
            while cursor < len(linha) and profundidade:
                if linha[cursor] == "{":
                    profundidade += 1
                elif linha[cursor] == "}":
                    profundidade -= 1
                cursor += 1
            if profundidade:
                break
            limites.append(linha[começo:cursor-1])
        if len(limites) == 2:
            partes.append(("integral", limites))
            pos = cursor
        else:
            partes.append(("texto", linha[inicio:inicio+4]))
            pos = inicio + 4
    return partes


def _svg_resolucao(titulo, conteudo):
    """SVG com limites de integração posicionados em cima e embaixo."""
    largura, margem, passo = 1200, 55, 44
    linhas = []
    for paragrafo in conteudo.splitlines():
        if not paragrafo.strip():
            linhas.append("")
        elif "INT{" in paragrafo:
            # Nunca quebrar uma fórmula dentro dos limites de integração.
            linhas.append(paragrafo.strip())
        else:
            linhas.extend(textwrap.wrap(paragrafo, width=82,
                                        break_long_words=False,
                                        break_on_hyphens=False) or [""])
    linhas = linhas[:360]
    altura = max(360, 180 + len(linhas) * passo + 65)
    elementos = [f'<text x="{margem}" y="85" font-size="36" font-weight="bold" fill="#e8f0ff">{html.escape(titulo)}</text>']
    for i, linha in enumerate(linhas):
        y = 155 + i * passo
        if "INT{" not in linha:
            elementos.append(f'<text x="{margem}" y="{y}" font-size="24" fill="#e2e8f0">{html.escape(linha)}</text>')
            continue
        x = float(margem)
        for tipo, valor in _partes_matematicas(linha):
            if tipo == "texto":
                if not valor:
                    continue
                elementos.append(f'<text x="{x:.1f}" y="{y}" font-size="24" fill="#e2e8f0">{html.escape(valor)}</text>')
                # Estimativa conservadora; funciona sem instalar fontes/pacotes.
                x += sum(7 if c in " il.,:;()" else 14 if c in "MW" else 12.8 for c in valor)
            else:
                inferior, superior = valor
                # O símbolo integral e seus limites ocupam um bloco independente.
                elementos.append(f'<text x="{x:.1f}" y="{y+5}" font-size="48" fill="#e2e8f0">∫</text>')
                lx = x + 29
                elementos.append(f'<text x="{lx:.1f}" y="{y-17}" font-size="17" fill="#e2e8f0">{html.escape(superior)}</text>')
                elementos.append(f'<text x="{lx:.1f}" y="{y+19}" font-size="17" fill="#e2e8f0">{html.escape(inferior)}</text>')
                x += 42 + max(len(inferior), len(superior)) * 10
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{largura}" height="{altura}" '
            f'viewBox="0 0 {largura} {altura}"><rect width="100%" height="100%" fill="#101827"/>'
            + ''.join(elementos) + '</svg>')



# =========================================================
# IMAGENS MATEMÁTICAS PNG (sem MathJax no navegador)
# Requer matplotlib no requirements.txt do Render.
# =========================================================
def _latex_para_imagem(escrita, titulo, destino):
    """Formata o texto revisado em linhas LaTeX e renderiza com mathtext.
    Se a conversão falhar, desenha o texto original em PNG sem perder conteúdo.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import re
    from matplotlib.font_manager import FontProperties

    # Conversão dedicada: NÃO pede nova solução, apenas transcrição visual.
    pedido = (
        'Converta a resolução a seguir em linhas para renderização matemática. '
        'NÃO resolva novamente, NÃO mude nenhuma operação, valor, sinal ou ordem. '
        'Responda APENAS uma linha por linha de saída. Cada linha deve ser '
        'MAT: <expressão LaTeX> para fórmulas ou TXT: <texto> para títulos e explicações. '
        'Use \\frac{\\partial(...)}{\\partial y} para derivadas parciais, '
        'frações verticais \\frac{a}{b}, \\nabla, \\times, \\cdot, '
        'expoentes ^{2} e subscritos _{x}. '
        'Não use \\begin, \\end, \\left, \\right, \\text, alinhamentos, '
        'delimitadores $ ou \\[. '
        'Mathtext do matplotlib será usado, portanto produza comandos simples. '
        'Não agrupe operações de linhas distintas. '
        'Nunca numere títulos de etapas e não use hífen ou travessão após Passo. Preserve como TXT os quatro títulos Passo Integração em x, Passo Integração em y, Passo Verificação em z e Resultado final, quando presentes; não transforme títulos em MAT. Preserve TODAS as linhas de integração, incluindo integrais indefinidas ∫(expressão) dx, dy ou dz, usando o comando int no LaTeX. Preserve o título Função potencial como TXT. '
        'Em derivadas de componentes, represente F_x, F_y, F_z diretamente no numerador: '
        r'use \frac{\partial F_x}{\partial x}, NUNCA \frac{\partial(F_x)}{\partial x}; '
        'não adicione parênteses que não existam no texto original. '
        'Para INT{a}{b}, escreva \\int_{a}^{b}. '
        'RESOLUÇÃO ORIGINAL:\n' + escrita[:16000]
    )
    resposta = openai_client.responses.create(model=OPENAI_MODEL,
        input=[{'role':'user','content':[{'type':'input_text','text':pedido}]}])
    bruto = (resposta.output_text or '').strip()
    itens = []
    for linha in bruto.splitlines():
        linha = linha.strip()
        if linha.startswith('MAT:'):
            itens.append(('math', linha[4:].strip().strip('$')))
        elif linha.startswith('TXT:'):
            itens.append(('text', linha[4:].strip()))
    # Remove enumeração automática dos títulos de etapas no PNG.
    # Não altera expressões matemáticas nem números de exercícios.
    import re as _re_cabecalhos
    itens = [
        (tp, _re_cabecalhos.sub(r'^\s*(?:\d+\s*[.)]\s*|Passo\s*[-–—:]\s*)',
                                 lambda m: 'Passo ' if m.group(0).lstrip().lower().startswith('passo') else 'Passo ',
                                 vl, count=1) if tp == 'text' and
         _re_cabecalhos.match(r'^\s*(?:\d+\s*[.)]\s*|Passo\s*[-–—:]\s*)', vl) and
         _re_cabecalhos.search(r'integração|integracao|verificação|verificacao|substituição|substituicao|simplificação|simplificacao|aplicação|aplicacao', vl, _re_cabecalhos.I)
         else vl)
        for tp, vl in itens
    ]
    if not itens:
        raise ValueError('Conversão matemática sem linhas válidas.')
    # Título obrigatório na imagem quando a resolução realmente calcula potencial.
    # A conversão em LaTeX pode omitir títulos, por isso a inserção é feita no PNG.
    import re as _re_pot
    potencial = bool(_re_pot.search(r'(?:[φϕ]|\\(?:var)?phi)', escrita)) and bool(
        _re_pot.search(r'(?:[∂∫]|\\(?:partial|int)|função potencial|funcao potencial)', escrita, _re_pot.I))
    # A integral de linha pode USAR phi sem estar CALCULANDO a funcao potencial.
    # Nao inserir automaticamente etapas de integracao no exercicio 1c.
    potencial = potencial and bool(_re_pot.search(
        r'(?:g\s*\(\s*y\s*,\s*z\s*\)|h\s*\(\s*z\s*\)|Passo\s+Integra[cç][aã]o|\\int\s*\(?\s*2xy)',
        escrita, _re_pot.I))
    if potencial:
        tem_titulo = any(tipo == 'text' and _re_pot.search(r'fun[cç][aã]o\s+potencial|potencial\s+escalar', valor, _re_pot.I) for tipo, valor in itens)
        if not tem_titulo:
            itens.insert(0, ('text', 'Função potencial'))

    # Organiza as etapas uma única vez, sem interpretar resultados
    # intermediários como se fossem o resultado final.
    if potencial:
        import re as _re_etapas

        def _normalizar_titulo(valor):
            return _re_etapas.sub(r"[^a-z0-9]", "", valor.lower().translate(
                str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc")))

        def _etapa_textual(tipo, valor):
            if tipo != 'text':
                return None
            t = _normalizar_titulo(valor)
            if 'integracaoemx' in t: return 1
            if 'integracaoemy' in t: return 2
            if 'verificacaoemz' in t: return 3
            if t in ('resultadofinal', 'resultado', 'resultadofinal:'): return 4
            return None

        # Remove TODOS os títulos das etapas recebidos do modelo.
        # Isso impede títulos duplicados ou fora de ordem.
        limpos = [(tp, vl) for tp, vl in itens if _etapa_textual(tp, vl) is None]
        titulos = {
            1: 'Passo Integração em x',
            2: 'Passo Integração em y',
            3: 'Passo Verificação em z',
            4: 'Resultado final',
        }

        def _detectar_etapa(tipo, valor):
            if tipo != 'math':
                return None
            t = _re_etapas.sub(r'\s+', '', valor.lower())
            # A detecção das etapas não classifica a função potencial
            # completa como resultado, pois ela também pode surgir no meio.
            if ('partial' in t or '∂' in t) and ('partialz' in t or '∂z' in t) and ('phi' in t or 'φ' in t or 'ϕ' in t):
                return 3
            if ('h' in t and ('prime' in t or "'" in t)) or ('h(z)' in t and '=c' in t):
                return 3
            if ('g(' in t or 'partialg' in t or '∂g' in t) and ('partialy' in t or '∂y' in t or 'dy' in t):
                return 2
            if ('phi' in t or 'φ' in t or 'ϕ' in t) and ('partialx' in t or '∂x' in t or ('int' in t and 'dx' in t)):
                return 1
            return None

        # O último cálculo com phi(x,y,z) = ... é o resultado final.
        # Nunca marca como resultado final um cálculo intermediário.
        pos_final = None
        for i, (tp, vl) in enumerate(limpos):
            if tp == 'math':
                t = _re_etapas.sub(r'\s+', '', vl.lower())
                if ('phi(x,y,z)' in t or 'φ(x,y,z)' in t or 'ϕ(x,y,z)' in t) and '=' in t and 'partial' not in t and '∂' not in t:
                    pos_final = i

        # As etapas são posicionadas pelo primeiro cálculo que lhes pertence.
        marcadores = {}
        for i, (tp, vl) in enumerate(limpos):
            etapa = _detectar_etapa(tp, vl)
            if etapa in (1, 2, 3) and etapa not in marcadores:
                marcadores[etapa] = i
        if pos_final is not None:
            marcadores[4] = pos_final
        if marcadores:
            organizados = []
            # Cada título é inserido uma única vez, na posição calculada.
            for i, item in enumerate(limpos):
                for etapa in (1, 2, 3, 4):
                    if marcadores.get(etapa) == i:
                        organizados.append(('text', titulos[etapa]))
                organizados.append(item)
            itens = organizados
        else:
            itens = limpos

    # Insere os títulos no próprio PNG, independentemente de a IA esquecê-los.
    # Detecta a notação matemática (R_x, R_y, R_z, nabla cross/dot,
    # derivadas de F_x/F_y/F_z) e não inventa seções para outros exercícios.
    def classificar_secao(tipo, valor):
        import re
        t = valor.strip().replace('\\', '')
        t = re.sub(r'\s+', '', t).lower()
        if tipo == 'text':
            if t in ('rotacional', 'rotacional:', 'cálculodorotacional'):
                return 'Rotacional'
            if t in ('divergente', 'divergente:', 'divergência', 'divergência:'):
                return 'Divergente'
        # Os resultados de rotacional têm R_x/R_y/R_z ou nabla x F.
        if re.match(r'^r(?:_[{]?[xyz][}]?|[ₓᵧz])\s*=', t) or ('nabla' in t and 'times' in t):
            return 'Rotacional'
        if ('nabla' in t and 'cdot' in t) or ('∇' in t and '·' in t):
            return 'Divergente'
        if ('partial' in t or '∂' in t) and re.search(r'f(?:_[{]?[xyz][}]?|[ₓᵧz])', t):
            return 'Divergente'
        return None

    secoes = {classificar_secao(tipo, valor) for tipo, valor in itens}
    if 'Rotacional' in secoes and 'Divergente' in secoes:
        organizados = []
        secao_atual = None
        for tipo, valor in itens:
            secao = classificar_secao(tipo, valor)
            if tipo == 'text' and secao in ('Rotacional', 'Divergente'):
                # Título original será substituído pelo título padronizado.
                if secao != secao_atual:
                    organizados.append(('text', secao))
                    secao_atual = secao
                continue
            if secao and secao != secao_atual:
                organizados.append(('text', secao))
                secao_atual = secao
            organizados.append((tipo, valor))
        itens = organizados

    def desenhar(itens, fallback=False):
        # Ajusta altura à quantidade de linhas; nenhuma fórmula vira código no HTML.
        n = min(len(itens), 240)
        fig = plt.figure(figsize=(13, max(3.5, 1.0 + n * .65)), dpi=145,
                         facecolor='#ffffff')
        fig.text(.045, .985, titulo, fontsize=22, fontweight='bold', va='top', color='#17233a')
        passo = .90 / max(n, 1)
        for i, (tipo, conteudo) in enumerate(itens[:n]):
            y = .94 - i * passo
            if not conteudo:
                continue
            if tipo == 'math' and not fallback:
                fig.text(.045, y, '$' + conteudo + '$', fontsize=16, va='top', color='#111827')
            else:
                fig.text(.045, y, conteudo, fontsize=14, va='top', color='#111827',
                         fontfamily='DejaVu Sans')
        fig.savefig(destino, dpi=145, facecolor='white', bbox_inches='tight', pad_inches=.25)
        plt.close(fig)
    try:
        desenhar(itens)
    except Exception:
        # Sem fórmula invisível: em caso de LaTeX inválido, PNG legível em Unicode.
        try:
            plt.close('all')
        except Exception:
            pass
        desenhar([('text', linha) for linha in escrita.splitlines()], fallback=True)
    return destino


def _criar_png_resolucao(escrita, titulo, prefixo='treinador'):
    nome = f'{prefixo}_{uuid.uuid4().hex[:12]}.png'
    destino = os.path.join(PASTA_RESOLUCOES, nome)
    _latex_para_imagem(escrita, titulo, destino)
    return '/resolucoes/' + nome


def iniciar_resolucoes_em_segundo_plano(caminho, nome, exercicios):
    if openai_client is None or not exercicios:
        return
    job_id = uuid.uuid4().hex[:12]
    with resolucoes_lock:
        resolucoes_jobs[job_id] = {"foto": nome, "estado": "aguardando", "total": len(exercicios), "concluidos": 0}
    threading.Thread(target=_gerar_resolucoes_extras,
                     args=(job_id, caminho, nome, [dict(e) for e in exercicios]), daemon=True).start()


def _gerar_resolucoes_extras(job_id, caminho, nome, exercicios):
    # Não mexer nas variáveis globais da sessão principal.
    with resolucoes_semaforo:
        with resolucoes_lock:
            resolucoes_jobs[job_id]["estado"] = "processando"
        try:
            with open(caminho, "rb") as f:
                foto_b64 = base64.b64encode(f.read()).decode("ascii")
            for i, exercicio in enumerate(exercicios, 1):
                numero = str(exercicio.get("numero", i))
                enunciado = str(exercicio.get("enunciado", ""))
                try:
                    resposta = openai_client.responses.create(
                        model=OPENAI_MODEL,
                        input=[{"role": "user", "content": [
                            {"type": "input_text", "text": (
                                "Resolva APENAS o exercício " + numero + " da foto, passo a passo, em português. "
                                + REGRA_COMPLETUDE_CURVAS + " "
                                "REGRA DE ESCOPO: não repita resoluções de letras anteriores (a, b etc.), mesmo que estejam na mesma questão. "
                                "Use resultados anteriores somente como dados, sem refazer rotacional, divergente ou encontrar novamente a função potencial. "
                                "Se a letra pedir integral de linha de campo conservativo com função potencial conhecida, "
                                "mostre apenas: Passo Aplicação do teorema; ∫_C F·dr = φ(B) − φ(A); "
                                "Passo Substituição dos pontos; cálculo separado de φ(B) e φ(A); "
                                "Resultado final; ∫_C F·dr = valor. "
                                "O título Resultado final aparece uma única vez e a última linha contém só a expressão e o valor simplificado, "
                                "sem repetir φ(B) − φ(A) ou reordenar contas. "
                                "Obrigatório em qualquer exercício: título Resultado final uma vez, em linha própria antes da última expressão. "
                                "Confira a FOTO ORIGINAL e use o enunciado identificado apenas como apoio: " + enunciado + ". "
                                "Coloque os títulos Rotacional e Divergente em linhas separadas antes de cada seção, SOMENTE quando solicitadas no exercício atual. Para exercícios que PEDEM CALCULAR função potencial, divida a resolução com os títulos Passo Integração em x, Passo Integração em y, Passo Verificação em z e Resultado final, adaptando variáveis conforme necessário. Ao integrar, escreva explicitamente φ = ∫(expressão) dx antes do resultado integrado; faça o mesmo para g(y,z) = ∫(expressão) dy e integrais seguintes quando necessárias. Nunca pule a integral que gera uma função. Se o rotacional envolver derivadas parciais simples, escreva cada componente em duas linhas: a expressão das duas derivadas e, logo abaixo, a subtração dos resultados (ex.: Rₓ = 2y − 2y = 0). Não crie linhas para derivadas individuais triviais. Não numere títulos nem use traços entre Passo e a descrição; escreva Passo Integração em x, Passo Integração em y, Passo Verificação em z e Resultado final uma única vez no encerramento quando aplicável. Para o divergente, se Fₓ, Fᵧ e F_z já estiverem identificadas, escreva ∂Fₓ/∂x = parcelas = resultado, ∂Fᵧ/∂y = parcelas = resultado, ∂F_z/∂z = parcelas = resultado (ex.: 2y + 0 = 2y), mostrando os termos nulos e sem repetir polinômios inteiros. Não use parênteses em Fₓ/Fᵧ/F_z: escreva ∂Fₓ/∂x e nunca ∂(Fₓ)/∂x. Escreva ∇·F uma única vez, com o resultado já ordenado e simplificado. Nunca use Rₓ para divergente. Em cálculos difíceis, preserve etapas essenciais. "
                                "Se o exercício estiver ilegível ou faltarem dados, explique a limitação; não invente valores. "
                                "Escreva em texto simples Unicode, sem Markdown, LaTeX, frac, partial ou nabla. REGRA OBRIGATÓRIA: "
                                "toda integral definida deve ser escrita no formato INT{limite inferior}{limite superior}, "
                                "por exemplo INT{y²}{1} INT{0}{1−x} 1 dz dx. "
                                "NUNCA use integrais com limites Unicode colados, como ∫ᵧ²¹. "
                                "Coloque fórmulas longas em linhas separadas. Use ², ³ e frações como (a)/(b). "
                                "Na resposta final, escreva somente a expressão matemática e seu valor, "
                                "sem acrescentar palavras como cubo, cúbico, unidades cúbicas ou traduções estrangeiras. "
                                "Use exclusivamente português brasileiro no texto explicativo."
                            )},
                            {"type": "input_image", "image_url": "data:image/jpeg;base64," + foto_b64}
                        ]}]
                    )
                    conteudo = (resposta.output_text or "").strip()
                    if not conteudo:
                        raise ValueError("A IA retornou resolução vazia")
                    # Auditoria independente: impede curvas fechadas incompletas.
                    auditoria = openai_client.responses.create(
                        model=OPENAI_MODEL,
                        input=[{"role": "user", "content": [
                            {"type": "input_text", "text": (
                                "AUDITORIA MATEMÁTICA INDEPENDENTE do exercício " + numero + ". "
                                + REGRA_COMPLETUDE_CURVAS +
                                "Compare a solução com a FOTO ORIGINAL e com o enunciado. "
                                "Verifique cada trecho de curva, orientação, integrais e resultado. "
                                "Se faltar trecho, CORRIJA a resolução INTEIRA, mostrando-o e "
                                "recalculando a soma. Não altere o escopo do subitem. "
                                "Se a solução estiver correta, devolva-a sem mudanças matemáticas. "
                                "Preserve texto Unicode, títulos sem numeração, INT{inferior}{superior} "
                                "para integrais definidas e Resultado final uma única vez. "
                                "Em todo exercício, use Resultado final uma única vez, antes da última expressão. "
                                "Não use Markdown nem LaTeX. Responda SOMENTE a resolução revisada. "
                                "ENUNCIADO IDENTIFICADO: " + enunciado +
                                "\nRESOLUÇÃO A AUDITAR:\n" + conteudo
                            )},
                            {"type": "input_image", "image_url": "data:image/jpeg;base64," + foto_b64}
                        ]}]
                    )
                    revisada = (auditoria.output_text or "").strip()
                    if not revisada:
                        raise ValueError("Auditoria matemática retornou texto vazio")
                    conteudo = garantir_titulo_resultado_final(revisada)
                    # Remove sufixos de unidade redundantes em outros idiomas na resposta final.
                    import re
                    conteudo = re.sub(r"(?im)(?<=\d)\s+(?:куб(?:ических)?|cubic\s+units?)\s*$", "", conteudo)
                    # Imagem PNG de matemática, com frações verticais.
                    # Mantém o prefixo da foto para o filtro da galeria.
                    _criar_png_resolucao(conteudo, f'Exercício {numero}',
                                        prefixo=f'{os.path.splitext(nome)[0]}__{i:02d}')
                except Exception as erro:
                    print(f'GALERIA RESOLUCOES: erro no exercício {numero}: {erro!r}', flush=True)
                finally:
                    with resolucoes_lock:
                        resolucoes_jobs[job_id]["concluidos"] += 1
        except Exception as erro:
            print('GALERIA RESOLUCOES: falha no lote:', repr(erro), flush=True)
        finally:
            with resolucoes_lock:
                resolucoes_jobs[job_id]["estado"] = "concluido"
                # Limita histórico em RAM
                while len(resolucoes_jobs) > 50:
                    resolucoes_jobs.pop(next(iter(resolucoes_jobs)))


@app.route("/api/resolucoes", methods=["GET"])
def api_resolucoes():
    foto = request.args.get("foto", "").strip()
    if foto and (os.path.basename(foto) != foto or
                 not foto.lower().endswith((".jpg", ".jpeg"))):
        return jsonify({"erro": "Nome de foto inválido."}), 400
    arquivos = [n for n in os.listdir(PASTA_RESOLUCOES) if n.lower().endswith((".png", ".svg"))]
    if foto:
        prefixo = os.path.splitext(foto)[0] + "__"
        arquivos = [n for n in arquivos if n.startswith(prefixo)]
    arquivos.sort(key=lambda n: os.path.getmtime(os.path.join(PASTA_RESOLUCOES, n)), reverse=True)
    with resolucoes_lock:
        jobs = [dict(j) for j in resolucoes_jobs.values() if not foto or j.get("foto") == foto][-10:]
    return jsonify({"foto": foto or None,
                    "imagens": [{"nome": n, "url": "/resolucoes/" + n} for n in arquivos],
                    "jobs": jobs})


@app.route("/resolucoes/baixar/<path:nome>", methods=["GET"])
def baixar_resolucao(nome):
    # Restringe o download aos arquivos de imagem gerados pela galeria.
    if (not nome or os.path.basename(nome) != nome or
            not nome.lower().endswith((".png", ".svg"))):
        return jsonify({"erro": "Nome de imagem inválido."}), 400
    return send_from_directory(PASTA_RESOLUCOES, nome, as_attachment=True,
                               download_name=nome)


@app.route("/resolucoes/<path:nome>", methods=["GET"])
def arquivo_resolucao(nome):
    resposta = send_from_directory(PASTA_RESOLUCOES, nome, as_attachment=False)
    resposta.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return resposta


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
# WEBSOCKET
# =========================================================

@sock.route("/ws-esp32")
def websocket_esp32(ws):
    global aguardando_resposta_menu, foto_sessao_atual, nome_foto_sessao_atual, quantidade_exercicios_sessao, gabarito_rapido_disponivel, texto_ultimo_gabarito, indice_audio_atual, id_audio_atual

    global esp_ws, reset_pendente

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

        # Um reset pedido offline tem prioridade sobre qualquer trabalho anterior.
        with lock:
            enviar_reset = reset_pendente
        if enviar_reset:
            with lock:
                reset_pendente = False
            ws.send("RESET_LIMPO")
            adicionar_log_esp32("AVISO", "RESET_LIMPO pendente enviado na reconexao.")
        else:
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

            # LOG ENVIADO PELA ESP32
            if mensagem.startswith("LOG|"):
                partes_log = mensagem.split("|", 2)

                if len(partes_log) == 3:
                    adicionar_log_esp32(
                        partes_log[1],
                        partes_log[2]
                    )

                continue

            if mensagem in ("RESET_LIMPO_OK", "RESET_OK"):
                with lock:
                    reset_pendente = False
                adicionar_log_esp32("OK", "ESP32 confirmou reset limpo.")
                continue

            # PING

            if mensagem == "PING":

                ws.send("PONG")

            # PRONTO

            elif mensagem == "PRONTO":

                ws.send("PRONTO_OK")

            # ARQUIVO MANUAL SALVO NO SD
            elif mensagem.startswith("ARQUIVO_SD_SALVO|"):
                partes = mensagem.split("|", 2)
                arquivo_id = partes[1].strip() if len(partes) > 1 else ""
                nome = partes[2].strip() if len(partes) > 2 else ""

                with lock:
                    dados_arquivo = arquivos_sd.get(arquivo_id)
                    if dados_arquivo is not None:
                        dados_arquivo["salvo"] = True

                adicionar_log_esp32(
                    "OK",
                    f"ESP32 confirmou /downloads/{nome}."
                )
                continue

            # AUDIO RECEBIDO

            elif mensagem.startswith("AUDIO_RECEBIDO|"):

                partes = mensagem.split("|", 1)

                if len(partes) == 2:

                    audio_id = partes[1]

                    with lock:

                        if audio_id in audios and audios[audio_id].get("cancelado_reset"):
                            continue
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
                    if not esperando_menu_agora:
                        atualizar_status_ia("AGUARDANDO_REPRODUCAO", "🔊 AGUARDANDO REPRODUÇÃO", "WAV recebido; aguardando Bluetooth e reprodução.")
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
                    if dados_audio is not None and dados_audio.get("cancelado_reset"):
                        continue
                    eh_menu = bool(dados_audio.get("menu", False)) if dados_audio else False

                    if dados_audio is not None:
                        dados_audio["reproduzido"] = True
                        dados_audio["reproduzido_em"] = time.time()

                adicionar_log_esp32(
                    "OK",
                    f"ESP32 confirmou reprodução completa do áudio {audio_id}."
                )

                # Áudio normal encerra o serviço.
                # Áudio de menu continua ocupado até chegar CLIQUES|n.
                if not eh_menu:
                    atualizar_status_ia(
                        "AGUARDANDO",
                        "⚪ IA AGUARDANDO",
                        "Áudio reproduzido. Aguardando uma nova foto."
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
                        audios_exercicios_sessao.clear()
                        indice_audio_atual = 0
                        id_audio_atual = None
                        texto_ultimo_gabarito = None
                        foto_sessao_atual = None
                        nome_foto_sessao_atual = None
                        quantidade_exercicios_sessao = 0
                        gabarito_rapido_disponivel = False
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
                    permite_gabarito = gabarito_rapido_disponivel

                if not caminho_foto or not os.path.exists(caminho_foto):
                    adicionar_log_esp32("ERRO", "Não existe foto original ativa para esta sessão.")
                    continue

                # No menu inicial, 1..N escolhem exercicios.
                # Depois de uma resolucao, 1 repete o WAV atual;
                # 2+ sao os demais exercicios na ordem da foto.
                with lock:
                    atual = indice_audio_atual
                    audio_para_repetir = id_audio_atual
                    opcoes_atuais = [dict(item) for item in exercicios_atuais]
                if atual:
                    if quantidade == 1:
                        if audio_para_repetir and reenviar_audio_da_sessao(audio_para_repetir):
                            continue
                        adicionar_log_esp32("ERRO", "Audio atual indisponivel para repeticao.")
                        continue
                    destinos = opcoes_menu(opcoes_atuais, permite_gabarito, atual)
                    indice_destino = quantidade - 2
                    if indice_destino < 0 or indice_destino >= len(destinos):
                        adicionar_log_esp32("AVISO", f"Escolha {quantidade} invalida no menu atual.")
                        with lock:
                            aguardando_resposta_menu = True
                        continue
                    tipo_destino, indice_escolhido, _ = destinos[indice_destino]
                    quantidade = total + 1 if tipo_destino == "gabarito" else indice_escolhido

                if permite_gabarito and quantidade == total + 1:
                    with lock:
                        opcoes_gabarito = [dict(item) for item in exercicios_atuais]
                    atualizar_status_ia("RESOLVENDO", "🟣 IA PREPARANDO GABARITO RÁPIDO...", "Conferindo as alternativas diretamente na foto original.")
                    adicionar_log_esp32("INFO", "Gabarito rápido selecionado; conferindo todas as questões na foto.")

                    def gerar_gabarito_rapido(caminho_imagem, opcoes):
                        try:
                            with open(caminho_imagem, "rb") as arquivo:
                                imagem_b64 = base64.b64encode(arquivo.read()).decode("ascii")
                            numeros = [str(item.get("numero", "")).strip() for item in opcoes]
                            pedido = (
                                "Confira a FOTO ORIGINAL e resolva TODAS as questões objetivas "
                                "da lista de números: " + ", ".join(numeros) + ". "
                                "Leia os enunciados e alternativas completos. Não adivinhe. "
                                "Retorne UMA LINHA por questão, na mesma ordem, SOMENTE neste formato: "
                                "EXERCICIO <numero>: LETRA <A/B/C/D/E> ou EXERCICIO <numero>: ILEGIVEL. "
                                "Use ILEGIVEL se não puder determinar a alternativa com segurança. "
                                "Não inclua justificativas, outras questões ou textos adicionais."
                            )
                            resposta = openai_client.responses.create(model=OPENAI_MODEL, input=[{
                                "role": "user", "content": [
                                    {"type": "input_text", "text": pedido},
                                    {"type": "input_image", "image_url": "data:image/jpeg;base64," + imagem_b64}
                                ]
                            }])
                            import re
                            bruto = (resposta.output_text or "").strip()
                            linhas = {}
                            for numero, resultado in re.findall(
                                r"(?im)^\s*EXERCICIO\s+([A-Za-z0-9._-]+)\s*:\s*(LETRA\s+[A-E]|ILEGIVEL)\s*$", bruto
                            ):
                                if numero not in linhas:
                                    linhas[numero] = resultado.upper().strip()
                            partes = []
                            for numero in numeros:
                                resultado = linhas.get(numero, "ILEGIVEL")
                                if resultado.startswith("LETRA "):
                                    partes.append(f"Exercício {numero}, letra {resultado[-1]}.")
                                else:
                                    partes.append(f"Exercício {numero}, não consegui determinar a alternativa com segurança.")
                            texto_gabarito = "Gabarito rápido. " + " ".join(partes)
                            with lock:
                                global texto_ultimo_gabarito
                                texto_ultimo_gabarito = texto_gabarito
                            texto_com_menu = (texto_gabarito + " Agora você pode escolher novamente. "
                                + texto_menu_exercicios(opcoes, incluir_gabarito=True, atual=-1))
                            audio_gabarito = criar_e_enviar_audio_ia(texto_com_menu, menu=True, velocidade=1.4)
                            if audio_gabarito:
                                with lock:
                                    global indice_audio_atual, id_audio_atual
                                    indice_audio_atual = -1
                                    id_audio_atual = audio_gabarito
                            else:
                                atualizar_status_ia("ERRO", "🔴 FALHA NO GABARITO", "Não foi possível enviar o áudio.")
                        except Exception as erro:
                            adicionar_log_esp32("ERRO", f"Falha no gabarito rápido: {erro}")
                            atualizar_status_ia("ERRO", "🔴 ERRO NO GABARITO", "Falha ao conferir a foto.")

                    threading.Thread(target=gerar_gabarito_rapido, args=(caminho_foto, opcoes_gabarito), daemon=True).start()
                    continue

                if quantidade > total:
                    with lock:
                        aguardando_resposta_menu = True
                    adicionar_log_esp32(
                        "AVISO",
                        f"Escolha {quantidade} inválida; existem {total} exercício(s) nesta foto."
                    )
                    continue

                with lock:
                    opcoes_exercicios = [dict(item) for item in exercicios_atuais]
                    exercicio_escolhido = dict(opcoes_exercicios[quantidade - 1])

                numero_real = str(exercicio_escolhido.get("numero", quantidade)).strip()

                adicionar_log_esp32(
                    "OK",
                    f"Opção {quantidade} selecionada; corresponde ao exercício {numero_real}. IA relendo diretamente a foto original."
                )
                atualizar_status_ia(
                    "RESOLVENDO",
                    f"🟣 IA RESOLVENDO EXERCÍCIO {numero_real}...",
                    "Estou relendo a foto original e preparando a resolução guiada."
                )

                def resolver_exercicio_guiado_da_foto(numero, caminho_imagem, nome_imagem, opcoes_exercicios, indice_opcao):
                    try:
                        with open(caminho_imagem, "rb") as arquivo:
                            imagem_b64 = base64.b64encode(arquivo.read()).decode("ascii")

                        # Captura o material ativo no momento da resolução.
                        arquivo_base_id = material_base_file_id

                        # Enunciado já extraído na primeira leitura da foto.
                        enunciado_exercicio = ""
                        for item_exercicio in opcoes_exercicios:
                            if str(item_exercicio.get("numero", "")).strip() == str(numero).strip():
                                enunciado_exercicio = str(item_exercicio.get("enunciado", "")).strip()
                                break

                        # Decide automaticamente se é objetiva ou resposta escrita/cálculo.
                        resposta_tipo = openai_client.responses.create(
                            model=OPENAI_MODEL,
                            input=[{
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            f"Classifique SOMENTE o exercício {numero}. "
                                            "Responda apenas OBJETIVA ou ESCRITA. "
                                            "OBJETIVA: múltipla escolha ou alternativas para marcar, inclusive questões teóricas A, B, C, D, E. "
                                            "ESCRITA: cálculo desenvolvido, matemática, física, discursiva, texto, demonstração ou resposta que precisa ser desenvolvida."
                                        )
                                    },
                                    {
                                        "type": "input_image",
                                        "image_url": "data:image/jpeg;base64," + imagem_b64
                                    }
                                ]
                            }]
                        )
                        tipo_exercicio = (resposta_tipo.output_text or "ESCRITA").strip().upper()
                        eh_objetiva = "OBJETIVA" in tipo_exercicio
                        velocidade_audio = 1.2 if eh_objetiva else 1.0
                        adicionar_log_esp32(
                            "INFO",
                            f"Exercício {numero}: {'OBJETIVA' if eh_objetiva else 'ESCRITA'}; áudio {velocidade_audio}x."
                        )

                        conteudo_ia = [
                                    {
                                        "type": "input_text",
                                        "text": prompt_ditado_exercicio(numero)
                                    }
                        ]

                        if eh_objetiva:
                            conteudo_ia[0]["text"] = (
                                f"Observe novamente esta imagem original e responda SOMENTE o exercício {numero}. "
                                "Leia tudo DIRETAMENTE DA FOTO e confira cuidadosamente antes de responder. "
                                "É uma questão objetiva. Identifique a alternativa correta e copie SOMENTE o texto da alternativa correta, fiel à foto. "
                                "Responda em uma única linha no formato exato: LETRA: B | TEXTO: texto integral da alternativa B. "
                                "Não dê explicações, justificativas, nem mencione outras alternativas. "
                                "Não repita o enunciado, pois ele será lido separadamente antes da resposta. "
                                "Não use guia de escrita linha por linha, markdown, tabelas ou comandos LaTeX. "
                                "Não invente informação ilegível e não responda outros exercícios."
                            )

                        if arquivo_base_id:
                            conteudo_ia.append({
                                "type": "input_file",
                                "file_id": arquivo_base_id
                            })

                        conteudo_ia.append({
                            "type": "input_image",
                            "image_url": "data:image/jpeg;base64," + imagem_b64
                        })

                        # A foto continua sendo a fonte do enunciado. O material-base
                        # serve para método, notação, exemplos e padrão de resolução.
                        if arquivo_base_id:
                            conteudo_ia[0]["text"] += (
                                " MATERIAL-BASE: há um arquivo de referência anexado. "
                                "Use-o prioritariamente como base para o método, a notação, a organização e o padrão da resolução. "
                                "Procure nele exemplos ou procedimentos compatíveis com este exercício. "
                                "Não invente conteúdo que não esteja legível na foto e não troque o enunciado pelo material-base. "
                            )

                        if eh_objetiva:
                            resposta = openai_client.responses.create(
                                model=OPENAI_MODEL,
                                input=[{"role": "user", "content": conteudo_ia}]
                            )
                            resolucao = (resposta.output_text or "").strip()
                        else:
                            escrita_aprovada, resolucao = executar_resolucao_padronizada(
                                numero, "data:image/jpeg;base64," + imagem_b64, arquivo_base_id
                            )
                        if not resolucao:
                            resolucao = "Não consegui gerar a resolução deste exercício pela imagem."

                        if eh_objetiva:
                            import re
                            # Extrai somente a letra e o texto da alternativa escolhida.
                            padrao = re.fullmatch(
                                r"\s*LETRA\s*:\s*([A-E])\s*\|\s*TEXTO\s*:\s*(.+?)\s*",
                                resolucao, flags=re.IGNORECASE | re.DOTALL
                            )
                            if not padrao:
                                adicionar_log_esp32("ERRO", f"Resposta objetiva {numero} fora do formato; audio nao enviado para evitar resposta incorreta.")
                                atualizar_status_ia("ERRO", "🔴 RESPOSTA NÃO VALIDADA", "Não consegui confirmar a letra e o texto da alternativa.")
                                return
                            letra = padrao.group(1).upper()
                            texto_alternativa = padrao.group(2).strip()
                            # O campo enunciado extraido pode conter alternativas; retira-as da leitura.
                            pergunta = re.split(r"(?im)(?:^|\n)\s*(?:alternativas\s*:\s*)?(?:[A-E]\s*[)\.\-:]\s+)", enunciado_exercicio, maxsplit=1)[0].strip()
                            texto_final = (f"Exercício {numero}. {pergunta}. " if pergunta else f"Exercício {numero}. ")
                            texto_final += f"Resposta correta é a letra {letra}. {texto_alternativa}. "
                            texto_final += "Agora escolha outro exercício, ou repita este. " + texto_menu_exercicios(opcoes_exercicios, incluir_gabarito=permite_gabarito, atual=indice_opcao)
                        else:
                            abertura_audio = f"Exercício {numero}. "  # Não reler enunciado: priorizar resolução curta.
                            texto_final = (abertura_audio + "Resolução. " + resolucao + " "
                                           "Agora você pode escolher novamente. "
                                           + texto_menu_exercicios(opcoes_exercicios, incluir_gabarito=permite_gabarito, atual=indice_opcao))
                        # Todo áudio de exercício já termina com o menu da mesma foto.
                        # Um clique repete; os demais são anunciados na ordem dinâmica.
                        audio_gerado = criar_e_enviar_audio_ia(texto_final, menu=True, velocidade=velocidade_audio)
                        if audio_gerado:
                            with lock:
                                global indice_audio_atual, id_audio_atual
                                audios_exercicios_sessao[indice_opcao] = audio_gerado
                                indice_audio_atual = indice_opcao
                                id_audio_atual = audio_gerado
                        else:
                            atualizar_status_ia("ERRO", "🔴 FALHA NO ÁUDIO", "Não foi possível enviar a resposta à ESP32.")

                    except Exception as erro:
                        print(">>> ERRO AO RESOLVER PELA FOTO:", repr(erro), flush=True)
                        adicionar_log_esp32(
                            "ERRO",
                            f"Falha ao resolver exercício {numero} pela foto {nome_imagem}: {erro}"
                        )
                        atualizar_status_ia("ERRO", "🔴 ERRO NA RESOLUÇÃO", "Falha ao resolver exercício; sistema será liberado automaticamente.")

                threading.Thread(
                    target=resolver_exercicio_guiado_da_foto,
                    args=(numero_real, caminho_foto, nome_foto, opcoes_exercicios, quantidade),
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
        with lock:
            sem_socket = esp_ws is None
            estado_desconectado = str(status_ia.get("estado", "")).upper()
        if sem_socket and estado_desconectado in ESTADOS_ESP_COM_TIMEOUT:
            atualizar_status_ia("ERRO_AUDIO", "🔴 ESP32 DESCONECTADA", "Conexão perdida durante a entrega do áudio; aguardando recuperação.")



# =========================================================
# LABORATÓRIO DE TREINAMENTO: ISOLADO DA ESP32
# =========================================================
# Mostra a resolução matemática e o texto exato que seria falado.
# A prévia TTS é gerada apenas no navegador, sem transmitir à ESP32.
TREINADOR_MAX_BYTES = 12 * 1024 * 1024

TREINADOR_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Laboratório de treinamento da IA</title>
<style>
body{background:#0d1117;color:#e6edf3;font-family:Arial,sans-serif;margin:0;padding:25px}
main{max-width:950px;margin:auto}.painel{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:22px;margin:15px 0}
a{color:#58a6ff}button{background:#238636;color:white;border:0;padding:12px 17px;border-radius:8px;cursor:pointer;font-weight:bold}
button:disabled{opacity:.5;cursor:wait}input,textarea{background:#0d1117;border:1px solid #484f58;color:#fff;padding:10px;border-radius:7px;max-width:100%}
textarea{width:100%;min-height:160px;box-sizing:border-box;line-height:1.55}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.55;background:#0d1117;padding:16px;border-radius:8px}
small{color:#8b949e}img{max-width:100%;max-height:280px;object-fit:contain}audio{width:100%;margin-top:12px}
</style></head><body><main><p><a href="/">← Voltar ao servidor</a></p><h1>🧪 Laboratório de treinamento da IA</h1>
<p>Envie uma foto, confira a resolução escrita e veja o roteiro <strong>exato</strong> que será entregue à voz. Nada é enviado à ESP32.</p>
<section class="painel"><form id="form"><label>Imagem do exercício (JPG, PNG ou WEBP)</label><p><input type="file" id="imagem" accept="image/jpeg,image/png,image/webp" required></p>
<p><img id="preview" style="display:none" alt="Imagem escolhida"></p>
<label>Número ou item a resolver (opcional): <input id="numero" placeholder="Ex.: 1 ou 1a" style="width:150px"></label>
<p><button id="resolver" type="submit">Resolver e mostrar narração</button></p></form><div id="estado" role="status"></div></section>
<section id="resultado" style="display:none">
<div class="painel"><h2>1. Resolução em imagem</h2><small>Frações verticais e cálculos diretos, sem passos triviais repetidos. PNG gerado no servidor.</small><p><a id="baixarImagem" href="#" target="_blank">Abrir imagem em tamanho original</a></p><img id="imagemResolucao" alt="Resolução matemática renderizada" style="display:block;max-height:none;width:100%;height:auto;background:white;border-radius:8px"></div>
<div class="painel"><h2>Resolução em texto (cópia)</h2><pre id="escrita"></pre></div>
<div class="painel"><h2>Correção do padrão da IA</h2>
<small>Se a resolução estiver diferente do esperado, cole a versão correta e registre o erro. Os exemplos aprovados serão usados nas próximas questões, inclusive da ESP32.</small>
<p><label>Resolução esperada</label><textarea id="esperada" placeholder="Cole aqui a resolução corrigida e aprovada"></textarea></p>
<p><label>O que a IA fez de errado?</label><textarea id="erroTreino" style="min-height:75px" placeholder="Ex.: pulou derivadas, repetiu fórmula geral, criou passo desnecessário"></textarea></p>
<p><button id="salvarExemplo" type="button">💾 Salvar exemplo aprovado</button> <span id="estadoExemplo"></span></p></div>
<div class="painel"><h2>2. Texto que a IA enviaria ao áudio</h2><small>Texto literal do ditado matemático, depois da revisão. Edite para experimentar ajustes.</small>
<textarea id="fala" spellcheck="false"></textarea><p><button id="ouvir" type="button">▶ Gerar prévia de áudio</button> <button id="copiar" type="button">Copiar texto</button></p><div id="statusAudio"></div><audio id="player" controls style="display:none"></audio></div>
</section></main><script>
const form=document.getElementById('form'),imagem=document.getElementById('imagem'),preview=document.getElementById('preview');
let audioUrl=null;
imagem.addEventListener('change',()=>{if(imagem.files[0]){preview.src=URL.createObjectURL(imagem.files[0]);preview.style.display='block'}});
form.addEventListener('submit',async ev=>{ev.preventDefault();const btn=document.getElementById('resolver');btn.disabled=true;document.getElementById('estado').textContent='Analisando imagem, resolvendo e revisando...';document.getElementById('resultado').style.display='none';
try{const dados=new FormData();dados.append('imagem',imagem.files[0]);dados.append('numero',document.getElementById('numero').value);
const resp=await fetch('/api/treinador/resolver',{method:'POST',body:dados});const json=await resp.json();if(!resp.ok)throw Error(json.erro||'Falha');
document.getElementById('escrita').textContent=json.escrita;document.getElementById('imagemResolucao').src=json.imagem_url;document.getElementById('baixarImagem').href=json.imagem_url;document.getElementById('fala').value=json.narracao;document.getElementById('resultado').style.display='block';document.getElementById('estado').textContent='Pronto. Confira o texto e gere a prévia quando quiser.';
}catch(e){document.getElementById('estado').textContent='Erro: '+e.message}finally{btn.disabled=false}});
document.getElementById('salvarExemplo').onclick=async()=>{
const esperado=document.getElementById('esperada').value.trim(),erro=document.getElementById('erroTreino').value.trim();
const status=document.getElementById('estadoExemplo');
if(!esperado){status.textContent='Preencha a resolução esperada.';return;}
try{const r=await fetch('/api/treinador/exemplos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({esperada:esperado,erro})});
const j=await r.json();if(!r.ok)throw Error(j.erro||'Falha');status.textContent='Exemplo salvo. Total: '+j.total;
}catch(e){status.textContent='Erro: '+e.message;}};
document.getElementById('copiar').onclick=async()=>{await navigator.clipboard.writeText(document.getElementById('fala').value)};
document.getElementById('ouvir').onclick=async()=>{const btn=document.getElementById('ouvir');btn.disabled=true;document.getElementById('statusAudio').textContent='Gerando voz...';
try{const resp=await fetch('/api/treinador/audio',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({texto:document.getElementById('fala').value})});if(!resp.ok){const j=await resp.json();throw Error(j.erro||'Erro na voz')}
if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(await resp.blob());const player=document.getElementById('player');player.src=audioUrl;player.style.display='block';document.getElementById('statusAudio').textContent='Áudio pronto.';await player.play().catch(()=>{});
}catch(e){document.getElementById('statusAudio').textContent='Erro: '+e.message}finally{btn.disabled=false}};
</script></body></html>"""

@app.route('/treinador', methods=['GET'])
def pagina_treinador():
    return render_template_string(TREINADOR_HTML)

@app.route('/api/treinador/resolver', methods=['POST'])
def treinador_resolver():
    if openai_client is None:
        return jsonify({'erro':'OPENAI_API_KEY não configurada.'}), 503
    arquivo = request.files.get('imagem')
    if arquivo is None:
        return jsonify({'erro':'Selecione uma imagem.'}), 400
    dados = arquivo.read(TREINADOR_MAX_BYTES + 1)
    if not dados or len(dados) > TREINADOR_MAX_BYTES:
        return jsonify({'erro':'Imagem vazia ou maior que 12 MB.'}), 400
    tipo = arquivo.mimetype
    assinaturas = {'image/jpeg':dados[:3]==b'\xff\xd8\xff', 'image/png':dados[:8]==b'\x89PNG\r\n\x1a\n', 'image/webp':dados[8:12]==b'WEBP' and dados[:4]==b'RIFF'}
    if not assinaturas.get(tipo, False):
        return jsonify({'erro':'Envie uma imagem JPG, PNG ou WEBP válida.'}), 400
    numero = (request.form.get('numero') or '').strip()[:30]
    if not numero: numero = 'visível na imagem'
    imagem_url = 'data:'+tipo+';base64,'+base64.b64encode(dados).decode('ascii')
    try:
        escrita, narracao = executar_resolucao_padronizada(numero, imagem_url, material_base_file_id)
        imagem_resolucao = _criar_png_resolucao(escrita, f'Exercício {numero}')
        return jsonify({'ok':True,'escrita':escrita,'narracao':narracao,'imagem_url':imagem_resolucao})
    except ValueError as erro:
        return jsonify({'erro':str(erro)}), 422
    except Exception as erro:
        app.logger.exception('Erro no laboratório de treinamento')
        return jsonify({'erro':'Falha ao consultar a IA: '+str(erro)}), 502

@app.route('/api/treinador/exemplos', methods=['POST'])
def treinador_salvar_exemplo():
    corpo = request.get_json(silent=True) or {}
    esperada = str(corpo.get('esperada', '')).strip()
    erro = str(corpo.get('erro', '')).strip()
    if not 20 <= len(esperada) <= 10000 or len(erro) > 2000:
        return jsonify({'erro':'Resolução esperada: 20 a 10000 caracteres; erro: até 2000.'}), 400
    with exemplos_lock:
        exemplos = carregar_exemplos_aprovados()
        exemplos.append({'esperada':esperada, 'erro':erro, 'criado_em':datetime.now().isoformat()})
        exemplos = exemplos[-30:]
        temporario = ARQUIVO_EXEMPLOS + '.tmp'
        with open(temporario,'w',encoding='utf-8') as f:
            json.dump(exemplos,f,ensure_ascii=False,indent=2)
        os.replace(temporario,ARQUIVO_EXEMPLOS)
    return jsonify({'ok':True,'total':len(exemplos)})

@app.route('/api/treinador/audio', methods=['POST'])
def treinador_audio():
    if openai_client is None:
        return jsonify({'erro':'OPENAI_API_KEY não configurada.'}), 503
    corpo=request.get_json(silent=True) or {}
    texto=str(corpo.get('texto','')).strip()
    if not texto or len(texto)>18000:
        return jsonify({'erro':'Texto vazio ou muito longo (máximo 18 mil caracteres).'}), 400
    try:
        # Prévia separada: não entra na galeria de áudio nem envia via WebSocket.
        fala=openai_client.audio.speech.create(model=OPENAI_TTS_MODEL,voice=OPENAI_TTS_VOICE,input=texto,response_format='mp3')
        from io import BytesIO
        return send_file(BytesIO(fala.content),mimetype='audio/mpeg',download_name='previa_treinador.mp3')
    except Exception as erro:
        app.logger.exception('Erro no TTS de treinamento')
        return jsonify({'erro':'Falha ao gerar a voz: '+str(erro)}), 502

# =========================================================
# EXECUÇÃO LOCAL
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
