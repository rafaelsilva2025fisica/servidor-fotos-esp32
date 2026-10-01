"""
Teste mínimo: Render -> OpenAI API

Pré-requisitos:
1) No Render, variável de ambiente:
   OPENAI_API_KEY = sua chave secreta

2) No requirements.txt:
   openai>=1.0.0

Este arquivo NÃO altera o app.py.
"""

import os
import sys
from openai import OpenAI


def main():
    print("=" * 50)
    print("TESTE OPENAI API")
    print("=" * 50)

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print("ERRO: OPENAI_API_KEY não foi encontrada.")
        print("Confira a variável Environment no Render.")
        sys.exit(1)

    print("OPENAI_API_KEY encontrada no ambiente.")
    print("Enviando teste para a OpenAI...")

    try:
        client = OpenAI(api_key=api_key)

        response = client.responses.create(
            model="gpt-5.6-luna",
            input=(
                "Responda somente com esta frase, sem acrescentar nada: "
                "OPENAI CONECTADA COM SUCESSO"
            ),
        )

        print()
        print("Resposta recebida:")
        print(response.output_text)
        print()
        print("=" * 50)
        print("TESTE CONCLUÍDO")
        print("=" * 50)

    except Exception as exc:
        print()
        print("FALHA AO CHAMAR A OPENAI")
        print(f"Tipo: {type(exc).__name__}")
        print(f"Detalhes: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
