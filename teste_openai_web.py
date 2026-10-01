"""
Módulo de teste web da OpenAI.
Analisa a foto mais recente da pasta "galeria" sem alterar o fluxo da ESP32.
"""

import os
import base64
import mimetypes
from flask import Blueprint, Response
from openai import OpenAI

teste_openai_bp = Blueprint("teste_openai", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA_GALERIA = os.path.join(BASE_DIR, "galeria")


def _foto_mais_recente():
    if not os.path.isdir(PASTA_GALERIA):
        return None

    fotos = [
        os.path.join(PASTA_GALERIA, nome)
        for nome in os.listdir(PASTA_GALERIA)
        if nome.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]

    if not fotos:
        return None

    return max(fotos, key=os.path.getmtime)


def _data_url(caminho):
    mime, _ = mimetypes.guess_type(caminho)
    mime = mime or "image/jpeg"

    with open(caminho, "rb") as arquivo:
        dados = base64.b64encode(arquivo.read()).decode("utf-8")

    return f"data:{mime};base64,{dados}"


@teste_openai_bp.route("/teste-openai", methods=["GET"])
def teste_openai():
    chave = os.getenv("OPENAI_API_KEY")

    if not chave:
        return Response(
            "ERRO: OPENAI_API_KEY não encontrada no Environment do Render.",
            status=500,
            mimetype="text/plain",
        )

    foto = os.path.join(BASE_DIR, "prova.jpeg")

    if not foto:
        return Response(
            "ERRO: nenhuma foto foi encontrada na pasta galeria.\n"
            "Tire/envie uma foto pela ESP32 e tente novamente.",
            status=404,
            mimetype="text/plain",
        )

    prompt = """
Analise cuidadosamente esta fotografia de uma folha, prova ou lista de exercícios.

ESTE É APENAS UM TESTE DE LEITURA. NÃO RESOLVA OS EXERCÍCIOS.

Faça o seguinte:
1. Identifique quantos exercícios distintos aparecem.
2. Para cada exercício, transcreva o enunciado e as expressões matemáticas
   com a maior fidelidade possível.
3. Avalie cada exercício separadamente quanto à legibilidade.
4. Não adivinhe símbolos, expoentes, sinais, limites, raízes, denominadores,
   domínios ou partes cortadas.
5. Se houver dúvida relevante, marque o exercício como NÃO LEGÍVEL e diga
   exatamente o que precisa aparecer melhor em uma nova foto.
6. Se um exercício estiver legível, mantenha-o como legível mesmo que outro
   exercício da foto esteja ruim.

Use este formato:

FOTO ANALISADA
Quantidade de exercícios encontrados: N

EXERCÍCIO 1
Legível: SIM ou NÃO
Enunciado: ...
Problema de leitura: NENHUM ou descrição do problema
Nova foto necessária: SIM ou NÃO

Repita para todos.

RESUMO
Legíveis: ...
Precisam de nova foto: ...

Não resolva nenhuma questão.
"""

    try:
        cliente = OpenAI(api_key=chave)

        resposta = cliente.responses.create(
            model="gpt-5.6-luna",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": _data_url(foto),
                            "detail": "high",
                        },
                    ],
                }
            ],
        )

        cabecalho = (
            "TESTE OPENAI - FOTO MAIS RECENTE\n"
            "=================================\n"
            f"Arquivo analisado: {os.path.basename(foto)}\n\n"
        )

        return Response(
            cabecalho + resposta.output_text,
            mimetype="text/plain; charset=utf-8",
        )

    except Exception as erro:
        return Response(
            "ERRO AO CONSULTAR A OPENAI\n"
            "==========================\n"
            f"{type(erro).__name__}: {erro}",
            status=500,
            mimetype="text/plain; charset=utf-8",
        )
