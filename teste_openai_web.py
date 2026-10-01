"""
TESTE WEB — FOTO -> OPENAI -> RESOLUÇÃO GUIADA EM TEXTO

Rota:
    /teste-openai

Neste estágio NÃO gera áudio.
A página mostra exatamente o roteiro que futuramente será enviado ao TTS.
"""

import os
import base64
import mimetypes
from flask import Blueprint, Response
from openai import OpenAI

teste_openai_bp = Blueprint("teste_openai", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Neste teste usamos especificamente a prova colocada no repositório.
FOTO_TESTE = os.path.join(BASE_DIR, "prova.jpeg")


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
            "ERRO: OPENAI_API_KEY não encontrada.",
            status=500,
            mimetype="text/plain",
        )

    if not os.path.exists(FOTO_TESTE):
        return Response(
            "ERRO: prova.jpeg não foi encontrada no repositório.",
            status=404,
            mimetype="text/plain",
        )

    prompt = r"""
Você está criando um ROTEIRO DE FALA para ensinar uma pessoa a escrever,
no caderno, a resolução dos exercícios matemáticos presentes na fotografia.

IMPORTANTE:
- Primeiro leia e confira cada exercício.
- Resolva matematicamente cada exercício antes de criar o roteiro.
- Se algum símbolo, limite, expoente, sinal, integrando ou trecho essencial
  estiver ilegível ou ambíguo, NÃO ADIVINHE e NÃO resolva aquele exercício.
  Diga exatamente qual parte precisa de uma nova foto.
- Neste teste, sua saída será TEXTO. Esse texto será posteriormente convertido
  em áudio por TTS.

============================================================
ESTILO DO NOSSO GUIA
============================================================

O roteiro NÃO deve parecer uma solução escrita formal sendo simplesmente lida.
Ele deve funcionar como uma pessoa ao lado do aluno dizendo exatamente o que
ele deve escrever no caderno.

Antes da escrita de cada exercício, faça uma PRÉVIA curta e didática:
- diga qual é a ideia do exercício;
- explique de onde vêm os limites/domínio;
- diga qual variável será integrada primeiro;
- avise antecipadamente se aparecer substituição, regra da cadeia,
  coordenadas cilíndricas/esféricas ou outra técnica importante;
- explique brevemente o caminho até a resposta.

Depois comece a RESOLUÇÃO GUIADA.

REGRAS DA RESOLUÇÃO GUIADA:

1. NÃO enxugue etapas importantes.
   Mostre especialmente:
   - região de integração;
   - como cada limite foi encontrado;
   - domínio;
   - integral montada;
   - integração na variável interna;
   - aplicação dos limites;
   - integral restante;
   - resultado final.

2. Quando a região/domínio for derivada de curvas ou equações, diga o que foi
   feito em cada termo para chegar aos limites. Não pule diretamente para o
   domínio final.

3. Diga explicitamente o que escrever.
   Prefira:
   "Escreva..."
   "Na linha de baixo, escreva..."
   "Agora coloque..."
   "À direita, escreva..."

4. Não use comandos artificiais como:
   "agora pare",
   "espere cinco segundos",
   "faça uma pausa".
   O sistema físico controlará o tempo do aluno.

5. DIFERENCIE SUBTRAÇÃO DE NÚMERO NEGATIVO.
   Não diga simplesmente "dois menos catorze" quando isso puder ser ambíguo.
   Para a operação 2 - 14, diga:
   "Escreva o número dois. Agora coloque o sinal de subtração. Depois escreva
   o número catorze."
   Para um número negativo, diga explicitamente:
   "Escreva o número negativo catorze."

6. FRAÇÕES:
   Antes de ditar uma fração, avise que é uma fração.
   Diga claramente o numerador e o denominador.
   Exemplo:
   "Agora faça uma fração. No numerador, escreva x ao quadrado. No
   denominador, escreva dois."

7. PARÊNTESES:
   Quando um parêntese envolver uma expressão, deixe claro onde ele começa e
   termina.
   Exemplo:
   "Abra um parêntese. Escreva ... Feche o parêntese."
   Não trate uma expressão grande como se fosse um pequeno parêntese isolado.

8. POTÊNCIAS:
   Fale explicitamente "ao quadrado", "ao cubo", etc.

9. LIMITES DE INTEGRAÇÃO:
   Fale de forma inequívoca.
   Exemplo:
   "Escreva uma integral em y, com limite inferior um e limite superior dois."

10. ORDEM DE INTEGRAÇÃO:
    Sempre deixe claro em qual variável estamos trabalhando.
    Exemplo:
    "Vamos resolver primeiro a integral interna, que está em y. Durante essa
    integração, x é tratado como constante."

11. Quando uma variável for constante na integração atual, explique o efeito
    disso na conta. Não diga apenas que ela "vai para fora" se isso puder
    confundir o aluno.

12. Quando aplicar limites, mostre primeiro a substituição no limite superior
    e depois no inferior, antes de simplificar.

13. RESULTADO FINAL:
    No final diga claramente:
    "Resposta final..."
    e dite o resultado sem ambiguidade.

14. O texto deve ser confortável para TTS em português brasileiro.
    Evite excesso de símbolos soltos na parte falada.
    Entretanto, apresente também a EXPRESSÃO MATEMÁTICA correspondente entre
    colchetes em cada etapa, para podermos conferir visualmente durante este
    teste. O conteúdo entre colchetes NÃO fará parte do áudio no sistema final.

============================================================
FORMATO DA RESPOSTA
============================================================

Comece com:

SITUAÇÃO DA FOTO
- Quantidade de exercícios encontrados.
- Quais estão legíveis.
- Quais precisam de nova foto.
- Uma frase curta dizendo o que há em cada exercício.

Depois, para CADA exercício legível:

============================================================
EXERCÍCIO N
============================================================

ENUNCIADO RECONHECIDO
(transcrição fiel)

RESOLUÇÃO MATEMÁTICA DE CONTROLE
(resolução correta e completa, destinada à conferência do sistema;
não é o roteiro de áudio)

PRÉVIA FALADA
(texto que será falado antes de começar a escrever)

RESOLUÇÃO GUIADA FALADA

ETAPA 1
FALA:
...
CONFERÊNCIA:
[expressão matemática correspondente]

ETAPA 2
FALA:
...
CONFERÊNCIA:
[...]

Continue com quantas etapas forem necessárias. Não force um número fixo de
etapas. Uma etapa deve representar uma unidade natural de escrita/raciocínio.

RESULTADO FINAL FALADO
...

Ao terminar todos os exercícios, escreva:

OPÇÕES INICIAIS SUGERIDAS
Crie opções DINÂMICAS adequadas ao que foi encontrado na foto.
Por exemplo, se houver três exercícios:
"Um toque para começar o exercício um.
Dois toques para começar o exercício dois.
Três toques para começar o exercício três."

Essas opções NÃO são regras fixas do ESP32. São escolhas criadas por você
para este contexto específico.

Não gere áudio neste teste.
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
                            "image_url": _data_url(FOTO_TESTE),
                            "detail": "high",
                        },
                    ],
                }
            ],
        )

        cabecalho = (
            "TESTE — RESOLUÇÃO GUIADA EM TEXTO\n"
            "===================================\n"
            f"Arquivo analisado: {os.path.basename(FOTO_TESTE)}\n"
            "Áudio: NÃO GERADO NESTE TESTE\n\n"
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
