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
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)
esp_websocket = None

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
