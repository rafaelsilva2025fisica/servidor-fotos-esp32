from flask import Flask, render_template_string, jsonify
from flask_sock import Sock
import threading

app = Flask(__name__)
sock = Sock(app)

esp_lock = threading.Lock()
esp_socket = None


def esp_online():
    with esp_lock:
        return esp_socket is not None


@app.get("/")
def index():
    return render_template_string("""
<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>ESP32 Rafael V1</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 650px;
            margin: 50px auto;
            padding: 20px;
            background: #f4f4f4;
        }
        .card {
            background: white;
            padding: 30px;
            border-radius: 14px;
            box-shadow: 0 4px 18px rgba(0,0,0,.10);
            text-align: center;
        }
        #status {
            font-size: 24px;
            font-weight: bold;
            margin-top: 20px;
        }
        .online { color: green; }
        .offline { color: #b00020; }
    </style>
</head>
<body>
    <div class="card">
        <h1>ESP32 Rafael V1</h1>
        <p>Teste limpo de conexão WebSocket.</p>
        <div id="status" class="offline">Verificando ESP32...</div>
    </div>

<script>
async function atualizar() {
    try {
        const r = await fetch("/status", {cache: "no-store"});
        const j = await r.json();
        const el = document.getElementById("status");

        if (j.esp32_online) {
            el.textContent = "🟢 ESP32 CONECTADA";
            el.className = "online";
        } else {
            el.textContent = "🔴 ESP32 DESCONECTADA";
            el.className = "offline";
        }
    } catch (e) {
        const el = document.getElementById("status");
        el.textContent = "⚠️ Erro ao consultar servidor";
        el.className = "offline";
    }
}

atualizar();
setInterval(atualizar, 2000);
</script>
</body>
</html>
""")


@app.get("/status")
def status():
    return jsonify(esp32_online=esp_online())


@sock.route("/ws-esp32")
def ws_esp32(ws):
    global esp_socket

    print("================================")
    print(">>> ESP32 WEBSOCKET CONECTADA <<<")
    print("================================", flush=True)

    with esp_lock:
        esp_socket = ws

    try:
        ws.send("SERVIDOR_OK")

        while True:
            mensagem = ws.receive()

            if mensagem is None:
                break

            print("ESP32:", mensagem, flush=True)

            if mensagem == "PING":
                ws.send("PONG")

    except Exception as erro:
        print("WEBSOCKET ENCERRADO:", erro, flush=True)

    finally:
        with esp_lock:
            if esp_socket is ws:
                esp_socket = None

        print(">>> ESP32 WEBSOCKET DESCONECTADA <<<", flush=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
