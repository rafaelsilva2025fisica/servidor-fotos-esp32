SERVIDOR DE FOTOS - ESP32-CAM

Arquivos prontos para hospedar no Render.

O servidor possui:
- POST /upload -> recebe JPEG da ESP32
- GET / -> página para a pessoa ver as fotos
- GET /foto/NOME -> abre a foto
- GET /health -> teste do servidor
- proteção simples por token no cabeçalho X-Device-Token

IMPORTANTE SOBRE O PLANO GRATUITO DO RENDER:
O disco local do serviço gratuito é temporário. As fotos podem desaparecer quando o
serviço reiniciar, for redeployado ou ficar inativo e desligar. Portanto esta versão
é para TESTE/PROTÓTIPO. Depois adicionaremos armazenamento persistente (por exemplo,
object storage) antes do uso definitivo.

DEPLOY:
1. Crie um repositório no GitHub e coloque estes arquivos nele.
2. No Render, crie New > Web Service e conecte o repositório.
3. Build Command: pip install -r requirements.txt
4. Start Command: gunicorn app:app
5. Escolha Free.
6. Em Environment, defina DEVICE_TOKEN com um valor secreto que também colocaremos
   no código da ESP32.
7. Depois do deploy, você receberá uma URL parecida com:
   https://esp32-camera-receptor.onrender.com

TESTE PELO PC:
curl.exe -X POST "https://SEU-ENDERECO.onrender.com/upload" ^
  -H "X-Device-Token: SEU_TOKEN" ^
  -H "X-Device-ID: camera001" ^
  -H "Content-Type: image/jpeg" ^
  --data-binary "@foto.jpg"

Depois abra a URL principal no navegador.

PRÓXIMO PASSO:
Quando tivermos a URL pública e o DEVICE_TOKEN, fazemos o .ino da ESP32:
botão -> 5 fotos -> seleção -> HTTPS POST /upload.
