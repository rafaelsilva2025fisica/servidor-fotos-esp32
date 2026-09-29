from flask import Flask, render_template_string, jsonify
from flask_sock import Sock

import threading
import time


app = Flask(__name__)
sock = Sock(app)


# =========================================================
# ULTIMO SINAL RECEBIDO DA ESP32
# =========================================================

lock = threading.Lock()

ultimo_sinal_esp = 0


def registrar_esp():

    global ultimo_sinal_esp

    with lock:

        ultimo_sinal_esp = time.time()


def esp_online():

    with lock:

        ultimo = ultimo_sinal_esp


    # Considera conectada se recebemos
    # sinal nos últimos 30 segundos.

    return (
        ultimo > 0
        and
        time.time() - ultimo < 30
    )


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
    content="width=device-width, initial-scale=1"
>

<title>ESP32 Rafael V1</title>


<style>

body {

    margin: 0;

    background: #0d1117;

    color: white;

    font-family: Arial, sans-serif;

    display: flex;

    justify-content: center;

    padding: 40px 20px;
}


.card {

    width: 100%;

    max-width: 650px;

    background: #161b22;

    border: 1px solid #30363d;

    border-radius: 16px;

    padding: 30px;
}


h1 {

    margin-top: 0;
}


.status {

    margin-top: 30px;

    padding: 30px;

    text-align: center;

    background: #0d1117;

    border: 1px solid #30363d;

    border-radius: 12px;
}


#esp {

    font-size: 26px;

    font-weight: bold;
}


.online {

    color: #3fb950;
}


.offline {

    color: #f85149;
}


#detalhe {

    margin-top: 10px;

    color: #8b949e;
}


.log {

    margin-top: 25px;

    padding: 15px;

    background: #010409;

    border: 1px solid #30363d;

    border-radius: 10px;

    font-family: monospace;

    min-height: 80px;
}

</style>

</head>


<body>


<div class="card">


<h1>
ESP32 Rafael V1
</h1>


<p>
Servidor ESP32 em tempo real
</p>


<div class="status">


<div
    id="esp"
    class="offline"
>

🔴 ESP32 DESCONECTADA

</div>


<div id="detalhe">

Aguardando sinal...

</div>


</div>


<div
    class="log"
    id="log"
>

Aguardando ESP32...

</div>


</div>



<script>


let anterior = null;


function log(texto) {

    const elemento =
        document.getElementById("log");


    const hora =
        new Date()
        .toLocaleTimeString();


    elemento.innerHTML =

        "[" +
        hora +
        "] " +
        texto +
        "<br>" +
        elemento.innerHTML;
}



async function atualizar() {


    try {


        const resposta =
            await fetch(
                "/status?t=" +
                Date.now(),
                {
                    cache: "no-store"
                }
            );


        const dados =
            await resposta.json();


        const esp =
            document.getElementById("esp");


        const detalhe =
            document.getElementById("detalhe");



        if (
            dados.esp32_online
        ) {


            esp.textContent =
                "🟢 ESP32 CONECTADA";


            esp.className =
                "online";


            detalhe.textContent =
                "ESP32 respondendo normalmente";


            if (
                anterior !== true
            ) {

                log(
                    "ESP32 ficou ONLINE."
                );

                anterior = true;
            }

        }


        else {


            esp.textContent =
                "🔴 ESP32 DESCONECTADA";


            esp.className =
                "offline";


            detalhe.textContent =
                "Aguardando ESP32";


            if (
                anterior !== false
            ) {

                log(
                    "ESP32 ficou OFFLINE."
                );

                anterior = false;
            }

        }


    }


    catch (erro) {


        document
        .getElementById("detalhe")
        .textContent =

        "Erro de comunicação com servidor";

    }

}


atualizar();


setInterval(
    atualizar,
    1000
);


</script>


</body>

</html>
""")


# =========================================================
# STATUS
# =========================================================

@app.route("/status")
def status():

    return jsonify({

        "esp32_online":
            esp_online(),

        "segundos_desde_sinal":
            (
                round(
                    time.time()
                    -
                    ultimo_sinal_esp,
                    1
                )

                if ultimo_sinal_esp > 0

                else None
            )

    })


# =========================================================
# WEBSOCKET ESP32
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


    # A conexão já conta como sinal.

    registrar_esp()


    # Confirma para a ESP.

    ws.send(
        "SERVIDOR_OK"
    )


    try:


        while True:


            mensagem =
                ws.receive()


            if mensagem is None:

                break


            # Qualquer mensagem atualiza
            # o último sinal.

            registrar_esp()


            print(

                "ESP32 ->",

                mensagem,

                flush=True

            )


            # =================================================
            # HEARTBEAT
            # =================================================

            if mensagem == "PING":

                ws.send(
                    "PONG"
                )


            # =================================================
            # ESP PRONTA
            # =================================================

            elif mensagem == "PRONTO":

                ws.send(
                    "PRONTO_OK"
                )


    except Exception as erro:


        print(

            "ERRO WEBSOCKET:",

            erro,

            flush=True

        )


    finally:


        print(

            ">>> CONEXAO WEBSOCKET ENCERRADA <<<",

            flush=True

        )


# =========================================================
# LOCAL
# =========================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=5000

    )
