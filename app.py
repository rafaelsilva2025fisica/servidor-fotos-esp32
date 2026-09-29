from flask import Flask, jsonify, render_template_string
from flask_sock import Sock
import threading
import time

app = Flask(__name__)
sock = Sock(app)

# =========================================================
# ESTADO DA ESP32
# =========================================================

lock = threading.Lock()

ultimo_sinal_esp = 0.0


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
# PAGINA PRINCIPAL
# =========================================================

@app.route("/")
def pagina():
    return render_template_string("""
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

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
    padding: 40px 20px;
}

.container {
    width: 100%;
    max-width: 700px;
}

.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 16px;
    padding: 30px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, .30);
}

h1 {
    margin-top: 0;
    margin-bottom: 8px;
}

.subtitulo {
    color: #8b949e;
    margin-bottom: 30px;
}

.status-card {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 30px;
    text-align: center;
}

#status {
    font-size: 27px;
    font-weight: bold;
}

.online {
    color: #3fb950;
}

.offline {
    color: #f85149;
}

#detalhe {
    color: #8b949e;
    margin-top: 12px;
}

.log {
    margin-top: 25px;
    background: #010409;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 15px;
    min-height: 100px;
    font-family: monospace;
    line-height: 1.5;
}

</style>

</head>


<body>

<div class="container">

    <div class="card">

        <h1>ESP32 Rafael V1</h1>

        <div class="subtitulo">
            Servidor ESP32 em tempo real
        </div>

        <div class="status-card">

            <div
                id="status"
                class="offline"
            >
                🔴 ESP32 DESCONECTADA
            </div>

            <div id="detalhe">
                Aguardando sinal da ESP32...
            </div>

        </div>

        <div
            class="log"
            id="log"
        >
            Aguardando ESP32...
        </div>

    </div>

</div>


<script>

let estadoAnterior = null;


// ======================================================
// LOG DA PAGINA
// ======================================================

function adicionarLog(texto) {

    const log =
        document.getElementById("log");

    const hora =
        new Date().toLocaleTimeString();

    log.innerHTML =
        "[" +
        hora +
        "] " +
        texto +
        "<br>" +
        log.innerHTML;
}


// ======================================================
// CONSULTAR ESTADO DO SERVIDOR
// ======================================================

async function atualizarStatus() {

    try {

        const resposta =
            await fetch(
                "/status?t=" + Date.now(),
                {
                    cache: "no-store"
                }
            );

        const dados =
            await resposta.json();

        const status =
            document.getElementById("status");

        const detalhe =
            document.getElementById("detalhe");


        // ==================================================
        // ESP ONLINE
        // ==================================================

        if (dados.esp32_online === true) {

            status.textContent =
                "🟢 ESP32 CONECTADA";

            status.className =
                "online";


            if (
                dados.segundos_desde_sinal !== null
            ) {

                detalhe.textContent =
                    "Último sinal há " +
                    dados.segundos_desde_sinal +
                    " s";

            } else {

                detalhe.textContent =
                    "ESP32 respondendo";

            }


            if (estadoAnterior !== true) {

                adicionarLog(
                    "ESP32 ficou ONLINE."
                );

                estadoAnterior = true;
            }

        }

        // ==================================================
        // ESP OFFLINE
        // ==================================================

        else {

            status.textContent =
                "🔴 ESP32 DESCONECTADA";

            status.className =
                "offline";

            detalhe.textContent =
                "Aguardando sinal da ESP32...";


            if (estadoAnterior !== false) {

                adicionarLog(
                    "ESP32 ficou OFFLINE."
                );

                estadoAnterior = false;
            }

        }

    }

    catch (erro) {

        document
            .getElementById("detalhe")
            .textContent =
            "Erro ao consultar o servidor";

    }
}


// Primeira consulta
atualizarStatus();


// Atualização automática da página.
//
// IMPORTANTE:
// É o navegador que consulta /status.
// A ESP NÃO faz GET periódico.

setInterval(
    atualizarStatus,
    1000
);

</script>

</body>

</html>
""")


# =========================================================
# STATUS DA ESP32
# =========================================================

@app.route("/status")
def status():

    segundos = segundos_desde_ultimo_sinal()

    if segundos is None:
        segundos_formatados = None
    else:
        segundos_formatados = round(segundos, 1)

    return jsonify(
        {
            "esp32_online": esp_esta_online(),
            "segundos_desde_sinal": segundos_formatados
        }
    )


# =========================================================
# WEBSOCKET DA ESP32
# =========================================================

@sock.route("/ws-esp32")
def websocket_esp32(ws):

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

    # A própria conexão já conta como primeiro sinal.
    registrar_sinal_esp()

    # Confirma para a ESP32.
    ws.send("SERVIDOR_OK")

    try:

        while True:

            # ESTA LINHA ERA UMA DAS QUE ESTAVA
            # COM SINTAXE ERRADA NA VERSAO ANTERIOR.
            mensagem = ws.receive()

            if mensagem is None:
                break

            # Qualquer mensagem recebida significa
            # que a ESP está viva.
            registrar_sinal_esp()

            print(
                "ESP32 -> SERVIDOR:",
                mensagem,
                flush=True
            )


            # =================================================
            # HEARTBEAT
            # =================================================

            if mensagem == "PING":

                ws.send("PONG")

                print(
                    "SERVIDOR -> ESP32: PONG",
                    flush=True
                )


            # =================================================
            # ESP PRONTA
            # =================================================

            elif mensagem == "PRONTO":

                ws.send("PRONTO_OK")

                print(
                    ">>> ESP32 PRONTA <<<",
                    flush=True
                )


            # =================================================
            # FUTURO: AUDIO
            # =================================================

            elif mensagem.startswith(
                "AUDIO_RECEBIDO|"
            ):

                print(
                    ">>> AUDIO RECEBIDO PELA ESP32 <<<",
                    flush=True
                )


            elif mensagem.startswith(
                "AUDIO_REPRODUZIDO|"
            ):

                print(
                    ">>> AUDIO REPRODUZIDO <<<",
                    flush=True
                )


            # =================================================
            # FUTURO: FOTO
            # =================================================

            elif mensagem.startswith(
                "FOTO_RECEBIDA|"
            ):

                print(
                    ">>> FOTO RECEBIDA <<<",
                    flush=True
                )


    except Exception as erro:

        print(
            "ERRO WEBSOCKET:",
            erro,
            flush=True
        )


    finally:

        print(
            ">>> WEBSOCKET DA ESP32 ENCERRADO <<<",
            flush=True
        )


# =========================================================
# EXECUCAO LOCAL
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
