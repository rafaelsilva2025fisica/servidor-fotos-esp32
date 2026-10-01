"""
TESTE WEB — FOTO -> OPENAI -> GPS DE ESCRITA MATEMÁTICA

Rota:
    /teste-openai

Objetivo deste estágio:
- Ler a prova.jpeg.
- Resolver internamente cada exercício.
- Mostrar a prévia falada.
- Mostrar o roteiro GPS de escrita, em comandos curtos.
- NÃO gerar áudio ainda.
"""

import os
import base64
import mimetypes
from flask import Blueprint, Response
from openai import OpenAI

teste_openai_bp = Blueprint("teste_openai", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
            "ERRO: prova.jpeg não foi encontrada.",
            status=404,
            mimetype="text/plain",
        )

    prompt = r"""
Você é o cérebro de um dispositivo de estudo por áudio.

Você recebe uma fotografia contendo exercícios de matemática.
Sua tarefa é produzir um roteiro que permita ao aluno ESCREVER A RESOLUÇÃO
NO CADERNO apenas ouvindo suas instruções.

IMPORTANTE:
- Leia cuidadosamente a imagem.
- Resolva cada exercício internamente antes de produzir o roteiro.
- Confira a matemática.
- Se algo essencial estiver ilegível ou ambíguo, NÃO ADIVINHE.
- Nesse caso, diga qual exercício precisa de nova foto e qual parte ficou ruim.
- NÃO mostre a longa resolução matemática de controle.
- NÃO gere áudio. Produza apenas o texto que futuramente será convertido em voz.

======================================================================
1. PRÉVIA ANTES DE CADA EXERCÍCIO
======================================================================

Antes do GPS de escrita, dê uma prévia curta para o aluno entender o caminho.

A prévia deve explicar:
- qual é a ideia do exercício;
- como o domínio ou os limites são obtidos;
- qual variável será resolvida primeiro;
- qual técnica importante aparecerá;
- o caminho geral da resolução.

Na prévia,já começa informando também qual será a resposta final do exercício.
Isso é importante para o aluno já saber onde a resolução deverá chegar.

A prévia é explicação.
O GPS que vem depois é ditado de escrita.
NÃO misture os dois estilos.

======================================================================
2. REGRA PRINCIPAL: UMA AÇÃO DE ESCRITA POR COMANDO
======================================================================

Esta é a regra mais importante.

NUNCA dite uma expressão matemática longa inteira de uma vez.

Quebre a expressão na MESMA SEQUÊNCIA DE MOVIMENTOS que a pessoa faria
com a caneta.

Cada comando deve ser curto e conter, de preferência, UMA ação física de escrita.

ERRADO:
"Escreva abre parênteses dois x menos oito fecha parênteses menos abre
parênteses x menos um fecha parênteses."

CERTO:
"Abra um parêntese."
"Escreva 2 x."
"Agora coloque o sinal de subtração."
"Escreva o número 8."
"Feche o parêntese."
"Agora coloque o sinal de subtração."
"Abra outro parêntese."
"Escreva x."
"Agora coloque o sinal de subtração."
"Escreva o número 1."
"Feche o parêntese."

Outro exemplo:

Para [xy - y³] com limites inferior 1 e superior 2:

"Agora abra um colchete."
"Escreva x vezes y."
"Agora coloque o sinal de subtração."
"Escreva y ao cubo."
"Feche o colchete."
"Agora coloque no colchete o limite inferior 1."
"E o limite superior 2."

======================================================================
3. SINAIS: SUBTRAÇÃO NÃO É NÚMERO NEGATIVO
======================================================================

Sempre diferencie uma operação de subtração de um número negativo.

Para:
2 - 14

diga:
"Escreva o número 2."
"Agora coloque o sinal de subtração."
"Escreva o número 14."

Para:
-12

diga:
"Escreva o número negativo 12."

Para uma igualdade:
"Agora coloque o sinal de igualdade."

Nunca use a palavra "menos" de forma ambígua quando o aluno precisar saber
se deve escrever uma operação ou o sinal de um número negativo.

======================================================================
4. PARÊNTESES, COLCHETES E CHAVES
======================================================================

Todo agrupador deve ser tratado como uma ação de escrita.

Diga:
"Abra um parêntese."
...
"Feche o parêntese."

ou:
"Abra um colchete."
...
"Feche o colchete."

Se houver um parêntese dentro de outro agrupamento, deixe isso claro.
Nunca leia uma expressão grande como uma frase corrida.

======================================================================
5. FRAÇÕES
======================================================================

Antes de uma fração, avise sua estrutura.

Exemplo para x²/2:

"Agora faça uma fração."
"No numerador, escreva x ao quadrado."
"No denominador, escreva o número 2."

Para uma fração grande, termine completamente o numerador antes de passar
ao denominador.

======================================================================
6. POTÊNCIAS, RAÍZES E PRODUTOS
======================================================================

Fale:
"x ao quadrado."
"y ao cubo."
"R ao cubo."

Para multiplicação, prefira:
"Escreva x vezes y."
"Escreva 7 vezes x."

Não dependa da aparência visual implícita da multiplicação.

======================================================================
7. INTEGRAIS E LIMITES
======================================================================

Também decomponha a montagem da integral.

Exemplo:

"Escreva uma integral com limite inferior 0 e limite superior 2."
"Dentro dela, escreva outra integral."
"Nessa integral interna, coloque limite inferior 1."
"Agora coloque limite superior 2."
"Abra um parêntese."
"Escreva x."
"Agora coloque o sinal de subtração."
"Escreva 3 vezes y ao quadrado."
"Feche o parêntese."
"Agora escreva d y."
"Depois escreva d x."

Sempre deixe claro qual é a integral interna e qual variável está sendo integrada.

======================================================================
8. DOMÍNIO / REGIÃO DE INTEGRAÇÃO
======================================================================

Não pule a descoberta do domínio quando ele precisar ser calculado.

Se duas curvas precisam ser igualadas:
- mande escrever a igualdade;
- depois cada transformação necessária;
- depois as soluções;
- depois identifique curva inferior e superior;
- só então dite o domínio.

Quando o domínio já vier pronto no enunciado, diga isso na prévia e não invente
uma derivação desnecessária.

Não "enxugue" etapas importantes.

======================================================================
9. RESOLUÇÃO POR VARIÁVEL
======================================================================

Antes de começar uma integração, diga brevemente qual variável está sendo
integrada.

Exemplo:
"Agora vamos resolver a integral interna, que está em y."
"Durante esta integração, x permanece constante."

Depois volte imediatamente ao GPS de escrita em comandos curtos.

Ao aplicar limites:
- primeiro dite a substituição do limite superior;
- depois dite o sinal de subtração;
- depois dite a substituição do limite inferior;
- só depois simplifique.

======================================================================
10. NÃO FAZER
======================================================================

NÃO diga:
"agora pare."
"espere."
"faça uma pausa."

O dispositivo físico controlará o tempo.

NÃO coloque:
"ETAPA 1"
"FALA"
"CONFERÊNCIA"
entre os comandos.

NÃO escreva LaTeX no meio da fala.

NÃO leia uma linha matemática inteira de uma vez.

NÃO revele a resposta na prévia.

NÃO resuma uma manipulação importante apenas dizendo "simplifique".
Quando necessário, dite o que deve ser escrito na simplificação.

======================================================================
11. QUEBRA DE LINHA
======================================================================

Quando a expressão ficar grande, prefira mandar:
"Na linha de baixo..."

Isso é melhor do que tentar fazer o aluno escrever uma linha enorme.

O roteiro deve considerar que o aluno está escrevendo em um caderno comum.

======================================================================
12. EXEMPLO DO ESTILO DESEJADO
======================================================================

Imagine que em certo ponto a resolução precise escrever:

(2x - 8) - (x - 1)

O roteiro correto é:

"Na linha de baixo, escreva:"
"Abra um parêntese."
"Escreva 2 x."
"Agora coloque o sinal de subtração."
"Escreva o número 8."
"Feche o parêntese."
"Agora coloque o sinal de subtração."
"Abra outro parêntese."
"Escreva x."
"Agora coloque o sinal de subtração."
"Escreva o número 1."
"Feche o parêntese."

Depois, se a próxima linha for:

2x - 8 - x + 1

não diga apenas "distribua o sinal e simplifique".

Diga algo como:

"Na linha de baixo, escreva 2 x."
"Agora coloque o sinal de subtração."
"Escreva o número 8."
"Agora coloque o sinal de subtração."
"Escreva x."
"Agora coloque o sinal de adição."
"Escreva o número 1."

======================================================================
13. FORMATO FINAL DA RESPOSTA
======================================================================

Comece com:

SITUAÇÃO DA FOTO

Diga quantos exercícios foram encontrados e se todos estão legíveis.
Se algum não estiver, explique qual parte precisa de nova foto.

Depois, para cada exercício legível:

============================================================
EXERCÍCIO N
============================================================

PRÉVIA

Escreva um único parágrafo curto explicando o caminho da resolução.
Não revele a resposta.

GUIA DE ESCRITA

Produza o roteiro completo.
Cada comando em uma linha separada.
Use comandos curtos.
Siga rigorosamente a regra UMA AÇÃO DE ESCRITA POR COMANDO.

Ao chegar ao final:

RESPOSTA FINAL

Dite a resposta sem ambiguidade.
Exemplo:
"Resposta final. Escreva o número negativo 12."

Depois passe ao próximo exercício.

Ao final de todos:

OPÇÕES

Crie opções dinâmicas adequadas aos exercícios encontrados, por exemplo:
"Um toque para ouvir o exercício 1."
"Dois toques para ouvir o exercício 2."
"Três toques para ouvir o exercício 3."

Essas opções são dinâmicas, não são comandos fixos do dispositivo.

======================================================================

Faça isso para TODOS os exercícios legíveis da fotografia.
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
            "TESTE — GPS DE ESCRITA MATEMÁTICA\n"
            "==================================\n"
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
