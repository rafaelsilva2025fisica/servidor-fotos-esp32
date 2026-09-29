from flask import Flask, render_template_string
from flask_sock import Sock
import threading

app = Flask(__name__)
sock = Sock(app)

lock = threading.Lock()

esp_ws = None
site_clients = set()


# =========================================================
# ENVIAR EVENTO PARA TODAS AS PAGINAS ABERTAS
# =========================================================

def enviar_site(mensagem):

    mortos = []

    with lock:
        clientes = list(site_clients)

    for ws in clientes:
        try:
            ws.send(mensagem)
        except Exception:
            mortos.append(ws)

    if mortos:
        with lock:
            for ws in mortos:
                site_clients.discard(ws)


# =========================================================
# PAGINA
# =========================================================

@app.get("/")
def index():

    return render_template_string("""
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
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

    font-family:
        Arial,
        Helvetica,
        sans-serif;

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

    box-shadow:
        0 10px 30px
        rgba(0,0,0,.25);
}


h1 {

    margin-top: 0;

    margin-bottom: 8px;
}


.sub {

    color: #8b949e;

    margin-bottom: 28px;
}


.status-box {

    padding: 22px;

    border-radius: 12px;

    text-align: center;

    border: 1px solid #30363d;

    background: #0d1117;
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


#canal {

    margin-top: 10px;

    color: #8b949e;

    font-size: 14px;
}


.log {

    margin-top: 22px;

    padding: 16px;

    background: #010409;

    border: 1px solid #30363d;

    border-radius: 10px;

    min-height: 100px;

    font-family: monospace;

    white-space: pre-wrap;
}

</style>

</head>


<body>


<div class="container">


<div class="card">


<h1>
ESP32 Rafael V1
</h1>


<div class="sub">

Comunicação em tempo real com o ESP32.

</div>


<div class="status-box">


<div
    id="espStatus"
    class="offline"
>

🔴 ESP32 DESCONECTADA

</div>


<div id="canal">

Conectando página ao servidor...

</div>


</div>


<div
    class="log"
    id="log"
>

Aguardando eventos...

</div>


</div>


</div>



<script>


let ws = null;

let timerReconexao = null;


// =====================================================
// LOG
// =====================================================

function log(texto) {

    const elemento =
        document.getElementById(
            "log"
        );


    const hora =
        new Date()
        .toLocaleTimeString();


    elemento.textContent =

        "[" +
        hora +
        "] " +
        texto +
        "\\n" +
        elemento.textContent;
}



// =====================================================
// ESP ONLINE
// =====================================================

function mostrarOnline() {

    const elemento =
        document.getElementById(
            "espStatus"
        );


    elemento.textContent =
        "🟢 ESP32 CONECTADA";


    elemento.className =
        "online";
}



// =====================================================
// ESP OFFLINE
// =====================================================

function mostrarOffline() {

    const elemento =
        document.getElementById(
            "espStatus"
        );


    elemento.textContent =
        "🔴 ESP32 DESCONECTADA";


    elemento.className =
        "offline";
}



// =====================================================
// WEBSOCKET DA PAGINA
// =====================================================

function conectarSite() {


    const protocolo =

        location.protocol === "https:"

        ? "wss://"

        : "ws://";


    ws = new WebSocket(

        protocolo +

        location.host +

        "/ws-site"

    );



    // -------------------------------------------------
    // PAGINA CONECTOU AO SERVIDOR
    // -------------------------------------------------

    ws.onopen = () => {


        document
        .getElementById(
            "canal"
        )
        .textContent =

        "Página conectada ao servidor em tempo real";


        log(
            "Canal da página conectado."
        );

    };



    // -------------------------------------------------
    // SERVIDOR ENVIOU EVENTO
    // -------------------------------------------------

    ws.onmessage = (evento) => {


        const mensagem =
            evento.data;


        log(
            "Servidor: " +
            mensagem
        );


        if (
            mensagem ===
            "ESP_ONLINE"
        ) {

            mostrarOnline();
        }


        if (
            mensagem ===
            "ESP_OFFLINE"
        ) {

            mostrarOffline();
        }

    };



    // -------------------------------------------------
    // WEBSOCKET DA PAGINA CAIU
    // -------------------------------------------------

    ws.onclose = () => {


        document
        .getElementById(
            "canal"
        )
        .textContent =

        "Canal da página desconectado. Reconectando...";


        clearTimeout(
            timerReconexao
        );


        timerReconexao =
            setTimeout(
                conectarSite,
                2000
            );

    };



    ws.onerror = () => {

        try {

            ws.close();

        } catch (erro) {

        }

    };

}


conectarSite();


</script>


</body>

</html>
""")


# =========================================================
# WEBSOCKET DA ESP32
# =========================================================

@sock.route("/ws-esp32")
def websocket_esp32(ws):

    global esp_ws


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


    # -----------------------------------------------------
    # GUARDAR CONEXAO
    # -----------------------------------------------------

    with lock:

        esp_ws = ws


    # -----------------------------------------------------
    # AVISAR PAGINA
    # -----------------------------------------------------

    enviar_site(
        "ESP_ONLINE"
    )


    try:


        # -------------------------------------------------
        # CONFIRMAR PARA ESP32
        # -------------------------------------------------

        ws.send(
            "SERVIDOR_OK"
        )


        # -------------------------------------------------
        # ESPERAR MENSAGENS
        # -------------------------------------------------

        while True:


            mensagem =
                ws.receive()


            if mensagem is None:

                break


            print(

                "ESP32 -> SERVIDOR:",

                mensagem,

                flush=True

            )


            # ---------------------------------------------
            # ESP DISSE QUE ESTA PRONTA
            # ---------------------------------------------

            if mensagem == "PRONTO":

                enviar_site(
                    "ESP_ONLINE"
                )


            # ---------------------------------------------
            # FUTURO: AUDIO RECEBIDO
            # ---------------------------------------------

            elif mensagem.startswith(
                "AUDIO_RECEBIDO|"
            ):

                enviar_site(
                    mensagem
                )


            # ---------------------------------------------
            # FUTURO: AUDIO TOCADO
            # ---------------------------------------------

            elif mensagem.startswith(
                "AUDIO_REPRODUZIDO|"
            ):

                enviar_site(
                    mensagem
                )


            # ---------------------------------------------
            # FUTURO: FOTO
            # ---------------------------------------------

            elif mensagem.startswith(
                "FOTO_RECEBIDA|"
            ):

                enviar_site(
                    mensagem
                )


    except Exception as erro:


        print(

            "ERRO WEBSOCKET ESP32:",

            erro,

            flush=True

        )


    finally:


        # -------------------------------------------------
        # REMOVER ESP
        # -------------------------------------------------

        with lock:

            if esp_ws is ws:

                esp_ws = None


        print(

            ">>> ESP32 DESCONECTADA <<<",

            flush=True

        )


        # -------------------------------------------------
        # AVISAR SITE
        # -------------------------------------------------

        enviar_site(
            "ESP_OFFLINE"
        )



# =========================================================
# WEBSOCKET DO SITE
# =========================================================

@sock.route("/ws-site")
def websocket_site(ws):


    print(

        ">>> PAGINA CONECTADA AO WEBSOCKET <<<",

        flush=True

    )


    # -----------------------------------------------------
    # REGISTRAR PAGINA
    # -----------------------------------------------------

    with lock:

        site_clients.add(
            ws
        )


        online_agora =

            esp_ws is not None


    try:


        # -------------------------------------------------
        # INFORMAR ESTADO ATUAL IMEDIATAMENTE
        #
        # Isso resolve justamente o problema:
        # abriu a pagina depois da ESP conectar?
        # Ela recebe o estado correto mesmo assim.
        # -------------------------------------------------

        if online_agora:

            ws.send(
                "ESP_ONLINE"
            )

        else:

            ws.send(
                "ESP_OFFLINE"
            )


        # -------------------------------------------------
        # MANTER PAGINA CONECTADA
        # -------------------------------------------------

        while True:


            mensagem =
                ws.receive()


            if mensagem is None:

                break


    except Exception as erro:


        print(

            "WEBSOCKET SITE ENCERRADO:",

            erro,

            flush=True

        )


    finally:


        # -------------------------------------------------
        # REMOVER PAGINA
        # -------------------------------------------------

        with lock:

            site_clients.discard(
                ws
            )


        print(

            ">>> PAGINA DESCONECTADA DO WEBSOCKET <<<",

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
