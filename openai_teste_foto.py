"""
TESTE 2 — FOTO -> OPENAI -> IDENTIFICAR EXERCÍCIOS

Uso no Render:
    python openai_teste_foto.py nome_da_foto.jpg

Exemplos:
    python openai_teste_foto.py prova.jpg
    python openai_teste_foto.py /tmp/prova.png

Pré-requisitos:
- OPENAI_API_KEY configurada no Environment do Render
- requirements.txt contendo: openai
"""

import os
import sys
import base64
import mimetypes
from pathlib import Path
from openai import OpenAI


def imagem_para_data_url(caminho: Path) -> str:
    mime, _ = mimetypes.guess_type(str(caminho))
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise ValueError(
            "Formato não suportado neste teste. Use JPG, JPEG, PNG, WEBP ou GIF."
        )

    dados = base64.b64encode(caminho.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{dados}"


def main():
    print("=" * 64)
    print("TESTE DE LEITURA DE FOTO — OPENAI")
    print("=" * 64)

    if len(sys.argv) < 2:
        print("ERRO: informe o caminho da foto.")
        print("Exemplo: python openai_teste_foto.py prova.jpg")
        sys.exit(1)

    caminho = Path(sys.argv[1])

    if not caminho.exists():
        print(f"ERRO: arquivo não encontrado: {caminho}")
        sys.exit(1)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERRO: OPENAI_API_KEY não encontrada no ambiente.")
        sys.exit(1)

    print(f"Foto encontrada: {caminho}")
    print("Enviando imagem para análise...")
    print()

    prompt = """
Analise cuidadosamente a fotografia de uma folha/prova/lista de exercícios.

Neste teste você NÃO deve resolver os exercícios.

Sua tarefa é:
1. Identificar quantos exercícios distintos aparecem na imagem.
2. Para cada exercício, transcrever o enunciado e as expressões matemáticas
   com a maior fidelidade possível.
3. Avaliar separadamente se cada exercício está legível com segurança.
4. Se houver qualquer dúvida relevante em símbolo, expoente, sinal, limite,
   denominador, raiz, domínio, desenho ou trecho do enunciado, marque esse
   exercício como NÃO LEGÍVEL. Não adivinhe.
5. Quando não estiver legível, explique exatamente o que ficou duvidoso e
   diga qual parte deve aparecer melhor em uma nova foto.
6. Um exercício legível não deve ser rejeitado apenas porque outro exercício
   da mesma foto está ruim.

Responda em português do Brasil, usando EXATAMENTE esta organização:

FOTO ANALISADA
Quantidade de exercícios encontrados: N

EXERCÍCIO 1
Legível: SIM ou NÃO
Enunciado: ...
Problema de leitura: NENHUM
Nova foto necessária: NÃO

EXERCÍCIO 2
Legível: SIM ou NÃO
Enunciado: ...
Problema de leitura: ...
Nova foto necessária: SIM ou NÃO

Repita o bloco para todos os exercícios encontrados.

No final escreva:
RESUMO
Legíveis: ...
Precisam de nova foto: ...

Não resolva nenhuma questão neste teste.
"""

    try:
        client = OpenAI(api_key=api_key)
        data_url = imagem_para_data_url(caminho)

        response = client.responses.create(
            model="gpt-5.6-luna",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": data_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
        )

        print(response.output_text)
        print()
        print("=" * 64)
        print("TESTE CONCLUÍDO")
        print("=" * 64)

    except Exception as exc:
        print()
        print("FALHA AO ANALISAR A FOTO")
        print(f"Tipo: {type(exc).__name__}")
        print(f"Detalhes: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
