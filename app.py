
    if not pendente.exists():

        return jsonify(
            ok=True,
            status="nenhum_pendente"
        )


    nome_pendente = (
        pendente.read_text(
            encoding="utf-8"
        ).strip()
    )


    if nome_recebido != nome_pendente:

        return jsonify(
            ok=False,
            error="arquivo diferente do pendente"
        ), 409


    try:
        pendente.unlink()

    except FileNotFoundError:
        pass


    return jsonify(
        ok=True,
        status="reproduzido",
        arquivo=nome_recebido
    )


@app.get("/foto/<path:name>")
def foto(name):

    if not safe(name):
        abort(404)

    arquivo = UPLOAD_DIR / name

    if not arquivo.is_file():
        abort(404)

    return send_from_directory(
        UPLOAD_DIR,
        name
    )


@app.post("/excluir/<name>")
def excluir(name):

    if not safe(name):
        abort(400)

    arquivo = UPLOAD_DIR / name

    if arquivo.is_file():
        arquivo.unlink()

    return redirect(
        url_for("index")
    )


@app.post("/excluir-sequencia/<sequence_id>")
def excluir_sequencia(sequence_id):

    if not safe(sequence_id):
        abort(400)

    for p in UPLOAD_DIR.glob(
        "*.jpg"
    ):

        info = parse_file(p)

        if (
            info
            and
            info["seq"] == sequence_id
        ):
            p.unlink()

    return redirect(
        url_for("index")
    )


@app.post("/excluir-todas")
def excluir_todas():

    for p in UPLOAD_DIR.glob("*"):

        if p.is_file():
            p.unlink()

    return redirect(
        url_for("index")
    )


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
