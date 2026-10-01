"""
TESTE DE ÁUDIO — exercício 1
Adicione este arquivo ao mesmo diretório do app.py.

No app.py, registre este blueprint:
    from teste_audio_web import teste_audio_bp
    app.register_blueprint(teste_audio_bp)

Rotas:
    /teste-audio          -> página com player
    /teste-audio.mp3      -> gera/entrega o MP3
"""

import os
from flask import Blueprint, Response
from openai import OpenAI

teste_audio_bp = Blueprint("teste_audio", __name__)

# Primeiro teste: usamos um roteiro controlado do exercício 1.
# Depois ligaremos esta etapa automaticamente à saída da análise da foto.
TEXTO_AUDIO = """
Exercício um.

Prévia.
Neste exercício, a região é um retângulo.
O x varia de zero até dois.
E o y varia de um até dois.
Vamos montar a integral com y na parte interna.
Primeiro vamos integrar em y, mantendo x como constante.
Depois aplicamos os limites de y.
Em seguida, resolvemos a integral que restar em x.
Ao final, vamos chegar ao número negativo doze.

Agora começa o guia de escrita.

Escreva uma integral com limite inferior zero e limite superior dois.
Dentro dela, escreva outra integral.
Nessa integral interna, coloque limite inferior um.
Agora coloque limite superior dois.
Abra um parêntese.
Escreva x.
Agora coloque o sinal de subtração.
Escreva três vezes y ao quadrado.
Feche o parêntese.
Agora escreva d y.
Depois escreva d x.

Na linha de baixo, mantenha a integral de zero até dois.
Agora abra um colchete.
Escreva x vezes y.
Agora coloque o sinal de subtração.
Escreva y ao cubo.
Feche o colchete.
Agora coloque no colchete o limite inferior um.
E o limite superior dois.
Depois escreva d x.

Na linha de baixo, mantenha a integral de zero até dois.
Abra um parêntese.
Escreva dois x.
Agora coloque o sinal de subtração.
Escreva o número oito.
Feche o parêntese.
Agora coloque o sinal de subtração.
Abra outro parêntese.
Escreva x.
Agora coloque o sinal de subtração.
Escreva o número um.
Feche o parêntese.
Depois escreva d x.

Na linha de baixo, mantenha a integral de zero até dois.
Abra um parêntese.
Escreva x.
Agora coloque o sinal de subtração.
Escreva o número sete.
Feche o parêntese.
Depois escreva d x.

Na linha de baixo, abra um colchete.
Agora faça uma fração.
No numerador, escreva x ao quadrado.
No denominador, escreva o número dois.
Agora coloque o sinal de subtração.
Escreva sete vezes x.
Feche o colchete.
Agora coloque no colchete o limite inferior zero.
E o limite superior dois.

Na linha de baixo, abra um parêntese.
Agora faça uma fração.
No numerador, escreva dois ao quadrado.
No denominador, escreva o número dois.
Agora coloque o sinal de subtração.
Escreva sete vezes dois.
Feche o parêntese.
Agora coloque o sinal de subtração.
Abra outro parêntese.
Agora faça uma fração.
No numerador, escreva zero ao quadrado.
No denominador, escreva o número dois.
Agora coloque o sinal de subtração.
Escreva sete vezes zero.
Feche o parêntese.

Na linha de baixo, escreva o número dois.
Agora coloque o sinal de subtração.
Escreva o número catorze.
Agora coloque o sinal de subtração.
Escreva o número zero.
Agora coloque o sinal de igualdade.
Escreva o número negativo doze.

Resposta final.
Número negativo doze.
"""


@teste_audio_bp.route("/teste-audio", methods=["GET"])
def pagina_teste_audio():
    html = """
    <!doctype html>
    <html lang="pt-BR">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Teste de áudio - Exercício 1</title>
      <style>
        body { font-family: Arial, sans-serif; max-width: 760px; margin: 40px auto;
               padding: 0 20px; line-height: 1.5; }
        audio { width: 100%; margin: 20px 0; }
        a { display:inline-block; padding:12px 18px; border:1px solid #888;
            border-radius:8px; text-decoration:none; color:inherit; }
        .nota { background:#f3f3f3; padding:14px; border-radius:8px; }
      </style>
    </head>
    <body>
      <h1>Teste de áudio — Exercício 1</h1>
      <p>Prévia + GPS de escrita.</p>

      <audio controls preload="none">
        <source src="/teste-audio.mp3" type="audio/mpeg">
        Seu navegador não conseguiu reproduzir o áudio.
      </audio>

      <p><a href="/teste-audio.mp3" download="exercicio_1_guiado.mp3">Baixar MP3</a></p>

      <div class="nota">
        Primeiro teste: voz propositalmente calma e didática.
        Depois ajustaremos voz, ritmo e pausas.
      </div>
    </body>
    </html>
    """
    return Response(html, mimetype="text/html; charset=utf-8")


@teste_audio_bp.route("/teste-audio.mp3", methods=["GET"])
def gerar_audio():
    chave = os.getenv("OPENAI_API_KEY")
    if not chave:
        return Response(
            "ERRO: OPENAI_API_KEY não encontrada.",
            status=500,
            mimetype="text/plain",
        )

    try:
        cliente = OpenAI(api_key=chave)

        # GPT-4o Mini TTS é o modelo especializado de geração de fala.
        audio = cliente.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=TEXTO_AUDIO,
            instructions=(
                "Fale em português brasileiro. "
                "Use voz calma, clara e didática, como um professor orientando "
                "um aluno que está escrevendo no caderno. "
                "Fale mais devagar que uma conversa normal. "
                "Faça uma pequena pausa natural entre cada instrução. "
                "Dê ênfase às expressões 'sinal de subtração', "
                "'limite inferior', 'limite superior', 'numerador' e 'denominador'. "
                "Não acrescente nenhuma palavra ao texto."
            ),
            response_format="mp3",
        )

        # Compatível com versões recentes do SDK.
        dados = audio.content

        return Response(
            dados,
            mimetype="audio/mpeg",
            headers={
                "Content-Disposition": 'inline; filename="exercicio_1_guiado.mp3"',
                "Cache-Control": "no-store",
            },
        )

    except Exception as erro:
        return Response(
            "ERRO AO GERAR ÁUDIO\n"
            "===================\n"
            f"{type(erro).__name__}: {erro}",
            status=500,
            mimetype="text/plain; charset=utf-8",
        )
