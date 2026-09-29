
</style>

</head>

<body>

<div class="container">

    <a class="voltar" href="/">
        ← Voltar para o áudio
    </a>

    <h1>📸 Fotos da ESP32</h1>

    <div class="subtitulo">
        Sequências recebidas pelo servidor
    </div>
"""

    if not dados_sequencias:

        html += """
    <div class="vazio">
        Nenhuma foto recebida ainda.
    </div>
"""

    else:

        for sequencia in dados_sequencias:

            html += (
                '<div class="sequencia">'
                '<h2>Sequência #'
                + str(sequencia["numero"])
                + '</h2>'
                '<div class="inicio">'
                'Iniciada em '
                + sequencia["inicio"]
                + ' • '
                + str(len(sequencia["fotos"]))
                + '/5 fotos recebidas'
                '</div>'
                '<div class="grade">'
            )

            for foto in sequencia["fotos"]:

                html += (
                    '<div class="foto">'
                    '<img src="'
                    + foto["url"]
                    + '" alt="Foto '
                    + str(foto["numero"])
                    + '">'
                    '<div class="info">'
                    '<strong>Foto '
                    + str(foto["numero"])
                    + '/5</strong>'
                    '<div class="horario">'
                    'Recebida: '
                    + foto["horario"]
                    + '</div>'
                    '</div>'
                    '</div>'
                )

            html += """
                </div>
            </div>
"""

    html += """
</div>

<script>

// Atualiza automaticamente para as fotos irem
// aparecendo conforme chegam.
setTimeout(function() {
    window.location.reload();
}, 2000);

</script>

</body>
</html>
"""

    return html


# =========================================================
# STATUS DAS FOTOS EM JSON
# =========================================================

@app.route("/fotos-status")
def status_fotos():

    with lock_fotos:

        resultado = []

        for numero_sequencia in sorted(
            sequencias_fotos.keys()
        ):

            sequencia = sequencias_fotos[
                numero_sequencia
            ]

            resultado.append({
                "sequencia": numero_sequencia,
                "inicio": sequencia["inicio"],
                "quantidade":
                    len(sequencia["fotos"]),
                "completa":
                    len(sequencia["fotos"]) >= 5
            })

    return jsonify({
        "ok": True,
        "sequencias": resultado
    })
