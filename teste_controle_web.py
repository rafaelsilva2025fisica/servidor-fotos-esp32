import os
import re
import base64
import mimetypes
import threading
from flask import Blueprint, Response, jsonify, request
from openai import OpenAI

teste_controle_bp = Blueprint('teste_controle', __name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOTO_TESTE = os.path.join(BASE_DIR, 'prova.jpeg')
LOCK = threading.Lock()

ESTADO = {
    'preparado': False,
    'processando': False,
    'erro': None,
    'situacao': '',
    'menu_texto': '',
    'opcoes': {},
    'roteiros': {},
    'audio_atual': 'menu',
    'ultima_escolha': None,
}

def _data_url(caminho):
    mime, _ = mimetypes.guess_type(caminho)
    mime = mime or 'image/jpeg'
    with open(caminho, 'rb') as f:
        dados = base64.b64encode(f.read()).decode('utf-8')
    return f'data:{mime};base64,{dados}'

def _numero_por_extenso(n):
    nomes = {1:'um',2:'dois',3:'três',4:'quatro',5:'cinco',6:'seis',7:'sete',8:'oito',9:'nove',10:'dez'}
    return nomes.get(n, str(n))

def _prompt():
    return r'''Você é o cérebro de um dispositivo de estudo por áudio. Analise a fotografia de uma prova de matemática e produza roteiros faláveis em português brasileiro.

REGRAS:
- Leia cuidadosamente. Não adivinhe conteúdo ilegível.
- Resolva internamente e confira a matemática.
- Para CADA exercício legível, produza exatamente estas seções: CONFIRMAÇÃO DO ENUNCIADO, PRÉVIA, GUIA DE ESCRITA, RESPOSTA FINAL.
- Na CONFIRMAÇÃO, repita os dados essenciais: números, sinais, expoentes, equações, limites e domínio.
- Na PRÉVIA, explique brevemente o caminho e já informe a resposta final.
- O GUIA é um GPS de escrita: comandos curtos, uma ação de escrita por comando. Não leia expressões longas de uma vez.
- A resolução pode ser enxugada para reduzir o áudio, mas nenhuma passagem importante pode parecer inventada. Antes de uma transformação não óbvia, explique em uma frase curta o que está sendo feito.
- Sempre que mudar de linha diga primeiro: "Agora pule uma linha." Depois: "Na nova linha..."
- Diferencie sinal de subtração de número negativo.
- Para frações, diga numerador e denominador.
- Diga claramente qual variável está sendo integrada e o que permanece constante quando isso for relevante.
- Não use LaTeX na fala.
- Não inclua opções de botões dentro dos exercícios; o servidor acrescentará o menu dinamicamente.

FORMATO OBRIGATÓRIO, sem texto fora dele:
SITUAÇÃO DA FOTO
<resumo curto dizendo quantos exercícios foram encontrados e quais estão legíveis>

<<<EXERCICIO 1>>>
CONFIRMAÇÃO DO ENUNCIADO
...
PRÉVIA
...
GUIA DE ESCRITA
...
RESPOSTA FINAL
...
<<<FIM EXERCICIO 1>>>

Repita o bloco para todos os exercícios legíveis, numerando em ordem.'''

def _separar_saida(texto):
    m = re.search(r'SITUAÇÃO DA FOTO\s*(.*?)(?=<<<EXERCICIO\s+\d+>>>)', texto, re.S | re.I)
    situacao = m.group(1).strip() if m else 'Foto analisada.'
    roteiros = {}
    padrao = re.compile(r'<<<EXERCICIO\s+(\d+)>>>\s*(.*?)\s*<<<FIM EXERCICIO\s+\1>>>', re.S | re.I)
    for num, corpo in padrao.findall(texto):
        roteiros[int(num)] = f'Exercício {num}.\n\n{corpo.strip()}'
    return situacao, roteiros

def _menu_inicial(situacao, roteiros):
    partes = ['Foto capturada e analisada.', situacao]
    qtd = len(roteiros)
    if qtd:
        partes.append(f'Já preparei {qtd} áudios de resolução separados.')
        for i in sorted(roteiros):
            partes.append(f'Para ouvir a resolução do exercício {i}, aperte o botão {_numero_por_extenso(i)} vez' + ('' if i == 1 else 'es') + '.')
        partes.append('Depois que este áudio terminar, você terá vinte segundos para escolher.')
    return '\n'.join(partes)

def _menu_apos_exercicio(atual, roteiros):
    # Opções continuam dinâmicas no servidor. Para este primeiro teste:
    # 1 repete o atual; depois oferecemos os demais exercícios.
    opcoes = {1: atual}
    candidatos = [n for n in sorted(roteiros) if n != atual]
    for clique, exercicio in enumerate(candidatos, start=2):
        opcoes[clique] = exercicio
    frases = [f'Fim da resolução do exercício {atual}.']
    frases.append('Para ouvir novamente esta resolução, aperte o botão uma vez.')
    for clique, exercicio in opcoes.items():
        if clique == 1:
            continue
        frases.append(f'Para ir para a resolução do exercício {exercicio}, aperte o botão {_numero_por_extenso(clique)} vezes.')
    frases.append('Depois que este áudio terminar, você terá vinte segundos para escolher.')
    return '\n'.join(frases), opcoes

def _gerar_tts(texto):
    chave = os.getenv('OPENAI_API_KEY')
    if not chave:
        raise RuntimeError('OPENAI_API_KEY não encontrada.')
    cliente = OpenAI(api_key=chave)
    audio = cliente.audio.speech.create(
        model='gpt-4o-mini-tts', voice='alloy', input=texto, speed=0.5,
        instructions=('Fale em português brasileiro. Use voz calma, clara e didática, como um professor orientando um aluno que escreve no caderno. Faça pequenas pausas naturais entre instruções. Não acrescente palavras ao texto.'),
        response_format='mp3')
    return audio.content

@teste_controle_bp.route('/api/teste-controle/preparar', methods=['POST'])
def preparar():
    with LOCK:
        if ESTADO['processando']:
            return jsonify({'ok': False, 'erro': 'Análise já em andamento.'}), 409
        ESTADO['processando'] = True
        ESTADO['erro'] = None
    try:
        if not os.path.exists(FOTO_TESTE):
            raise FileNotFoundError('prova.jpeg não foi encontrada no diretório do app.py.')
        chave = os.getenv('OPENAI_API_KEY')
        if not chave:
            raise RuntimeError('OPENAI_API_KEY não encontrada.')
        cliente = OpenAI(api_key=chave)
        resposta = cliente.responses.create(model='gpt-5.6-luna', input=[{'role':'user','content':[{'type':'input_text','text':_prompt()},{'type':'input_image','image_url':_data_url(FOTO_TESTE),'detail':'high'}]}])
        situacao, roteiros = _separar_saida(resposta.output_text)
        if not roteiros:
            raise RuntimeError('A IA não devolveu nenhum bloco de exercício no formato esperado.')
        menu = _menu_inicial(situacao, roteiros)
        opcoes = {n:n for n in sorted(roteiros)}
        with LOCK:
            ESTADO.update({'preparado':True,'situacao':situacao,'menu_texto':menu,'opcoes':opcoes,'roteiros':roteiros,'audio_atual':'menu','ultima_escolha':None})
        return jsonify({'ok':True,'quantidade':len(roteiros),'situacao':situacao,'menu':menu,'opcoes':opcoes})
    except Exception as e:
        with LOCK: ESTADO['erro'] = f'{type(e).__name__}: {e}'
        return jsonify({'ok':False,'erro':f'{type(e).__name__}: {e}'}), 500
    finally:
        with LOCK: ESTADO['processando'] = False

@teste_controle_bp.route('/api/teste-controle/estado')
def estado():
    with LOCK:
        return jsonify({k:v for k,v in ESTADO.items() if k != 'roteiros'})

@teste_controle_bp.route('/api/teste-controle/cliques', methods=['POST'])
def cliques():
    dados = request.get_json(silent=True) or {}
    try: n = int(dados.get('cliques', 0))
    except Exception: n = 0
    with LOCK:
        if not ESTADO['preparado']:
            return jsonify({'ok':False,'erro':'Prepare a prova primeiro.'}), 409
        destino = ESTADO['opcoes'].get(n)
        if destino is None:
            return jsonify({'ok':False,'erro':f'{n} clique(s) não correspondem a uma opção do menu atual.','opcoes':ESTADO['opcoes']}), 400
        ESTADO['ultima_escolha'] = n
        ESTADO['audio_atual'] = f'exercicio_{destino}'
        texto_base = ESTADO['roteiros'][destino]
        menu_final, novas_opcoes = _menu_apos_exercicio(destino, ESTADO['roteiros'])
        ESTADO['opcoes'] = novas_opcoes
        texto = texto_base + '\n\n' + menu_final
    return jsonify({'ok':True,'cliques':n,'exercicio':destino,'audio_url':f'/teste-controle/audio/exercicio/{destino}.mp3?t={__import__("time").time()}','novas_opcoes':novas_opcoes,'texto':texto})

@teste_controle_bp.route('/teste-controle/audio/menu.mp3')
def audio_menu():
    with LOCK:
        if not ESTADO['preparado']:
            return Response('Prepare a prova primeiro.', status=409)
        texto = ESTADO['menu_texto']
    try: return Response(_gerar_tts(texto), mimetype='audio/mpeg', headers={'Cache-Control':'no-store'})
    except Exception as e: return Response(f'ERRO TTS: {e}', status=500)

@teste_controle_bp.route('/teste-controle/audio/exercicio/<int:num>.mp3')
def audio_exercicio(num):
    with LOCK:
        if num not in ESTADO['roteiros']:
            return Response('Exercício não preparado.', status=404)
        texto_base = ESTADO['roteiros'][num]
        menu_final, _ = _menu_apos_exercicio(num, ESTADO['roteiros'])
        texto = texto_base + '\n\n' + menu_final
    try: return Response(_gerar_tts(texto), mimetype='audio/mpeg', headers={'Cache-Control':'no-store'})
    except Exception as e: return Response(f'ERRO TTS: {e}', status=500)

@teste_controle_bp.route('/teste-controle')
def pagina():
    return Response(r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Teste do cérebro</title><style>body{font-family:Arial;max-width:850px;margin:30px auto;padding:0 18px;background:#0d1117;color:#e6edf3}button{padding:14px 18px;margin:5px;border:0;border-radius:9px;font-weight:bold;cursor:pointer}.prep{background:#238636;color:white}.click{background:#1f6feb;color:white}audio{width:100%;margin:18px 0}.box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px;margin:15px 0}pre{white-space:pre-wrap;word-break:break-word}</style></head><body><h1>Teste do cérebro — prova.jpeg</h1><div class="box"><button class="prep" onclick="preparar()">1. ANALISAR PROVA E PREPARAR</button><p id="status">Aguardando.</p></div><div class="box"><h2>Áudio atual</h2><audio id="player" controls></audio><button onclick="tocarMenu()">▶ TOCAR MENU</button></div><div class="box"><h2>Simular botão físico</h2><p>Estes botões enviam somente a quantidade de cliques.</p><button class="click" onclick="clicar(1)">1 CLIQUE</button><button class="click" onclick="clicar(2)">2 CLIQUES</button><button class="click" onclick="clicar(3)">3 CLIQUES</button><button class="click" onclick="clicar(4)">4 CLIQUES</button></div><div class="box"><h2>Estado / texto</h2><pre id="saida"></pre></div><script>const p=document.getElementById('player'),s=document.getElementById('status'),o=document.getElementById('saida');async function preparar(){s.textContent='Analisando prova.jpeg...';let r=await fetch('/api/teste-controle/preparar',{method:'POST'});let d=await r.json();o.textContent=JSON.stringify(d,null,2);if(d.ok){s.textContent='Pronto: '+d.quantidade+' exercício(s).';p.src='/teste-controle/audio/menu.mp3?t='+Date.now();p.play();}else{s.textContent='Erro: '+d.erro}}function tocarMenu(){p.src='/teste-controle/audio/menu.mp3?t='+Date.now();p.play()}async function clicar(n){s.textContent='Enviando somente CLIQUES:'+n;let r=await fetch('/api/teste-controle/cliques',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cliques:n})});let d=await r.json();o.textContent=JSON.stringify(d,null,2);if(d.ok){s.textContent='Servidor interpretou '+n+' clique(s) e escolheu exercício '+d.exercicio;p.src=d.audio_url;p.play();}else{s.textContent='Erro: '+d.erro}}</script></body></html>''', mimetype='text/html; charset=utf-8')
