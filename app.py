from flask import Flask, render_template_string, jsonify
from flask_sock import Sock
import threading
import time

app = Flask(__name__)
sock = Sock(app)

# =========================================================
# ESTADO DA ESP32
# =========================================================

lock = threading.Lock()

esp_conectada = False
ultimo_sinal_esp = 0


def definir_esp_online():
    global esp_conectada
    global ultimo_sinal_esp

    with lock:
        esp_conectada = True
        ultimo_sinal_esp = time.time()


def definir_esp_offline():
    global esp_conectada

    with lock:
        esp_conectada = False


def estado_esp():
    with lock:
        return esp_conectada


# =========================================================
# PAGINA
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

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    display: flex;

    justify-content: center;

    align-items: flex-start;

    padding: 40px 20px;
}


.container {

    width: 100%;

    max-width: 700px;
}


.card {

    background: #161b22;

    border:
        1px solid
        #30363d;

    border-radius:
        16px;

    padding:
        30px;

    box-shadow:
        0 10px 35px
        rgba(0,0,0,.30);
}


h1 {

    margin-top: 0;

    margin-bottom: 8px;

    font-size: 30px;
}


.subtitulo {

    color: #8b949e;

    margin-bottom: 30px;
}


.status-card {

    background:
        #0d1117;

    border:
        1px solid
        #30363d;

    border-radius:
        12px;

    padding:
        25px;

    text-align:
        center;
}


#status {

    font-size:
        26px;

    font-weight:
        bold;
}


.online {

    color:
        #3fb950;
}


.offline {

    color:
        #f85149;
}


.info {

    margin-top:
        12px;

    color:
        #8b949e;

    font-size:
        14px;
}


.separador {

    height:
        1px;

    background:
        #30363d;

    margin:
        25px 0;
}


.log {

    background:
        #010409;

    border:
        1px solid
        #30363d;

    border-radius:
        10px;

    padding:
        15px;

    min-height:
        100px;

    font-family:
        monospace;

    color:
        #c9d1d9;
}

</style>

</head>


<body>


<div class="container">


<div class="card">


<h1>
ESP32 Rafael V1
</h1>


<div class="subtitulo">

Servidor de comunicação ESP32

</div>


<div class="status-card">


<div
    id="status"
    class="offline"
>

🔴 ESP32 DESCONECTADA

</div>


<div
    class="info"
    id="info"
>

Verificando ESP32...

</div>


</div>


<div class="separador"></div>


<h3>
Eventos
</h3>


<div
    class="log"
    id="log"
>

Página iniciada.

</div>


</div>


</div>



<script>


let estadoAnterior = null;


// ======================================================
// LOG
// ======================================================

function adicionarLog(texto) {

    const log =
        document.getElementById(
            "log"
        );


    const hora =
        new Date()
        .toLocaleTimeString();


    log.innerHTML =

        "[" +
        hora +
        "] " +
        texto +
        "<br>" +
        log.innerHTML;
}



// ======================================================
// ATUALIZAR STATUS
// ======================================================

async function verificarESP() {


    try {


        const resposta =
            await fetch(
                "/status?t=" +
                Date.now(),
                {
                    cache:
                        "no-store"
                }
            );


        const dados =
            await resposta.json();


        const status =
            document.getElementById(
                "status"
            );


        const info =
            document.getElementById(
                "info"
            );



        // ==================================================
        // ONLINE
        // ==================================================

        if (
            dados.esp32_online
        ) {


            status.textContent =
                "🟢 ESP32 CONECTADA";


            status.className =
                "online";


            info.textContent =
                "Canal WebSocket ativo";


            if (
                estadoAnterior !== true
            ) {


                adicionarLog(
                    "ESP32 conectada."
                );


                estadoAnterior =
                    true;
            }

        }


        // ==================================================
        // OFFLINE
        // ==================================================

        else {


            status.textContent =
                "🔴 ESP32 DESCONECTADA";


            status.className =
                "offline";


            info.textContent =
                "Aguardando ESP32";


            if (
                estadoAnterior !== false
            ) {


                adicionarLog(
                    "ESP32 desconectada."
                );


                estadoAnterior =
                    false;
            }

        }


    }


    catch (erro) {


        document
        .getElementById(
            "info"
        )
        .textContent =

        "Servidor temporariamente indisponível";

    }

}


// Primeira leitura

verificarESP();


// Atualização automática.
//
// IMPORTANTE:
// quem consulta aqui é o NAVEGADOR.
// A ESP32 continua sem fazer GET periódico.

setInterval(
    verificarESP,
    1000
);


</script>


</body>

</html>
""")


# =========================================================
# STATUS PARA A PAGINA
# =========================================================

@app.route("/status")
def status():

    return jsonify({

        "esp32_online":
            estado_esp()

    })


# =========================================================
# WEBSOCKET DA ESP32
# =========================================================

@sock.route("/ws-esp32")
def websocket_esp32(ws):


    print(
        "",
        flush=True
    )


    print(
        "================================",
        flush=True
    )


    print(
        ">>> ESP32 CONECTADA <<<",
        flush=True
    )


    print(
        "================================",
        flush=True
    )


    # =====================================================
    # ONLINE
    # =====================================================

    definir_esp_online()


    try:


        # =================================================
        # CONFIRMACAO PARA ESP32
        # =================================================

        ws.send(
            "SERVIDOR_OK"
        )


        # =================================================
        # MANTER CONEXAO
        # =================================================

        while True:


            mensagem =
                ws.receive()


            if mensagem is None:

                break


            # Qualquer mensagem recebida prova
            # que a ESP continua viva.

            definir_esp_online()


            print(

                "ESP32 -> SERVIDOR:",

                mensagem,

                flush=True

            )


            # =============================================
            # ESP PRONTA
            # =============================================

            if mensagem == "PRONTO":

                print(

                    ">>> ESP32 PRONTA <<<",

                    flush=True

                )


            # =============================================
            # FUTURO AUDIO
            # =============================================

            elif mensagem.startswith(
                "AUDIO_RECEBIDO|"
            ):

                print(

                    ">>> AUDIO RECEBIDO PELA ESP <<<",

                    flush=True

                )


            elif mensagem.startswith(
                "AUDIO_REPRODUZIDO|"
            ):

                print(

                    ">>> AUDIO REPRODUZIDO <<<",

                    flush=True

                )


            # =============================================
            # FUTURO FOTO
            # =============================================

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


        # =================================================
        # OFFLINE
        # =================================================

        definir_esp_offline()


        print(

            ">>> ESP32 DESCONECTADA <<<",

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
