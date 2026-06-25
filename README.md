# EchoOrganize

O **EchoOrganize** é um utilitário local projetado para automatizar a padronização de tags de metadados e a reestruturação física de pastas de arquivos de áudio. Ele foi planejado especificamente para contornar restrições severas de hardware e firmware encontradas em reprodutores de áudio digital portátil (**DAPs**), como o *SnoSky Echo Mini*, e em sistemas de som automotivo legados.

A arquitetura do projeto consiste em um servidor de retaguarda (*backend*) em Python 3 utilizando **FastAPI** e a biblioteca **Mutagen** para manipulação de binários, pareado com uma interface web de página única (*frontend*) desenvolvida em HTML5, CSS3 e JavaScript assíncrono.

---

## O que este projeto resolve?

* Se você é um **entusiasta ou usuário iniciante**, provavelmente já notou que, ao transferir músicas para o seu tocador de música ou cartão SD, as faixas aparecem fora de ordem, as imagens de capa não carregam, letras não sincronizam ou os álbuns se dividem em pastas duplicadas.
* Se você é um **desenvolvedor**, sabe que isso ocorre por limitações clássicas de memória e firmware em chips embarcados. O EchoOrganize automatiza a correção de todos esses problemas de baixo nível sem que você precise editar arquivo por arquivo manualmente.

---

## Otimizações de Baixo Nível para Hardware DAP

Diferente de organizadores de mídia genéricos para computadores, o EchoOrganize implementa rotinas específicas para o ecossistema de dispositivos embarcados:

### Escrita Sequencial Controlada (Correção para FAT32)
Sistemas operacionais de DAPs básicos reproduzem arquivos com base na ordem física de inserção na tabela de alocação de arquivos (FAT), ignorando a ordenação lógica dos metadados ou os nomes dos arquivos. 
* **A Solução Técnica:** O motor de cópia do EchoOrganize ordena a fila de arquivos na memória RAM antes de iniciar o processo de gravação. Durante a transferência para a mídia externa, o script impõe uma pausa controlada de 50 milissegundos entre as operações de escrita física. Isso força o controlador de armazenamento do sistema operacional hospedeiro a alocar os clusters do cartão SD em ordem cronológica estrita, respeitando a sequência de discos e faixas do álbum.

### Otimização e Compressão de Capas (Pillow Lanczos)
A decodificação de imagens de alta resolução (como capas de álbuns de 3000x3000px com compressão progressiva) exige memória de vídeo e processamento de CPU indisponíveis em DAPs de baixo consumo, o que gera travamentos e lentidão na navegação.
* **A Solução Técnica:** O pipeline intercepta as imagens de capa originais embutidas, converte o espaço de cores para RGB de canal único, reduz a resolução para um teto estrito de 500x500 pixels utilizando amostragem com filtro espacial LANCZOS e salva a nova imagem em formato JPEG otimizado com fator de qualidade de 85%.

### Gravação em Formato ID3v2.3
O formato de tags ID3v2.4 (mais recente) adota codificação UTF-8 nativa e modificou a nomenclatura de várias tabelas importantes. Muitos firmwares antigos de DAPs e multimídias de carros travam ou exibem caracteres corrompidos ao ler esse formato.
* **A Solução Técnica:** O EchoOrganize padroniza a escrita de arquivos MP3 forçando estritamente o formato de tags ID3v2.3 (utilizando codificação UTF-16 para caracteres especiais), garantindo compatibilidade universal com qualquer leitor do mercado.

---

## Recursos de Organização e Padronização

### 1. Agrupamento Inteligente por Artista do Álbum (*Album Artist*)
Evita a fragmentação de álbuns que possuem participações especiais (*featuring*). O sistema lê a tag de Artista do Álbum (`TPE2` no MP3, `albumartist` no FLAC ou `aART` no M4A) para centralizar todos os arquivos sob a mesma pasta principal de artista. Caso o cantor de uma faixa específica seja diferente do artista do álbum, o arquivo de áudio físico é gerado no formato:

`[Faixa] - [Artista da Faixa] - [Título].ext`

Isso assegura que o metadado individual de autoria de cada música seja preservado no nome do arquivo físico e nas tags de reprodução, sem gerar pastas duplicadas ou vazias na raiz do cartão SD.

### 2. Suporte Nativo a Álbuns Multi-Disco
Identifica o volume por meio das tabelas `TPOS` ou `discnumber`. Em casos de caixas de álbuns ou edições especiais com múltiplos CDs, o arquivo final recebe o prefixo `[Disco]-[Faixa]` (exemplo: `1-01 - Nome da Música.mp3`, `2-01 - Nome da Música.mp3`). Isso mantém o ordenamento correto e evita colisões de nomes dentro da mesma pasta.

### 3. Engine de Letras Sincronizadas (.lrc)
* **Geração de Arquivo Sidecar:** Consulta a API do LRCLib para extrair marcações de tempo e salvar um arquivo externo de legenda `.lrc` com o mesmo nome exato da música na mesma pasta, permitindo que a tela de DAPs exiba as letras sincronizadas com a reprodução.
* **Embutimento Interno:** Adiciona versões em texto plano, limpas de marcações de tempo, diretamente dentro do cabeçalho do arquivo de áudio (`USLT` no MP3, `lyrics` no FLAC ou `©lyr` no M4A) para sistemas que não suportam leitura de arquivos de legenda externos.

### 4. Tratamento Automático de Coletâneas (*Various Artists*)
Compilações, álbuns de coletâneas e trilhas sonoras com múltiplos intérpretes são identificados e agrupados na estrutura de diretórios `Various Artists / [Nome do Álbum]`, impedindo a criação de dezenas de pastas avulsas para artistas que possuem apenas uma música no seu dispositivo.

---

## Estrutura do Repositório

```text
echo-organizer-dap/
├── frontend/                  # Código e recursos da Interface Web
│   ├── index.html             # Estrutura visual e área de arrastar arquivos (Drop Zone)
│   ├── styles.css             # Estilização visual (Efeitos Glassmorphism e layouts)
│   └── app.js                 # Consumo de APIs e gerenciamento de estado do frontend
├── main.py                    # Servidor FastAPI, endpoints REST e pontes do SO
├── musicbrainz_service.py     # Cliente HTTP para serviços MusicBrainz e LRCLib (com rate limiting)
├── tagger_service.py          # Manipulação binária de tags e rotina de cópia física
├── Dockerfile                 # Instruções de montagem da imagem do container
├── docker-compose.yml         # Orquestração do ecossistema e mapeamento de volumes
└── requirements.txt           # Dependências do ecossistema Python
```

---

## Guia de Instalação e Execução

Selecione um dos dois métodos abaixo para inicializar a aplicação no seu computador.

### Método A: Execução Local Nativa (Altamente Recomendado)

Este método é o recomendado para obter a melhor experiência de usuário, pois permite que o backend do EchoOrganize se comunique diretamente com o seu sistema operacional para abrir as caixas de seleção de pasta nativas do Windows ou Linux ao clicar em **"Procurar..."**.

#### Pré-requisitos:
* Ter o Python 3.10 ou superior instalado na sua máquina (certifique-se de marcar a opção **"Add Python to PATH"** durante a instalação).

#### Passo a Passo:
1. Abra o terminal de comando de sua preferência (PowerShell, Prompt de Comando ou Terminal Linux) e navegue até a pasta do projeto:
   ```bash
   cd "C:\Caminho\Para\O\Projeto\echo-organizer-dap"
   ```
2. Instale as bibliotecas necessárias declaradas no arquivo de dependências:
   ```bash
   pip install -r requirements.txt
   ```
3. Inicie o servidor web utilizando o Uvicorn:
   ```bash
   python -m uvicorn main:app --host 127.0.0.1 --port 8000
   ```
4. Abra o seu navegador web e acesse o endereço da aplicação local: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

### Método B: Execução Isolada via Docker

Este método é recomendado caso você prefira não instalar o Python ou dependências diretamente na sua máquina hospedeira, ou queira rodar o projeto de forma completamente isolada.

#### Pré-requisitos:
* Ter o Docker e o Docker Compose instalados no sistema operacional.

#### Passo a Passo:
1. Abra o arquivo `docker-compose.yml` e ajuste os mapeamentos de volumes sob a chave `volumes` para que o container consiga enxergar seus discos rígidos ou o cartão SD da sua máquina local:
   ```yaml
   volumes:
     # Mapeia a pasta de músicas do seu computador para dentro do container
     - C:\Users\SeuUsuario\Music:/app/host_music
     # Mapeia a unidade física do seu cartão SD ou pasta de destino
     - E:\Music:/app/host_destination
   ```
2. Abra o terminal na raiz do projeto e construa/inicialize os containers em plano de fundo:
   ```bash
   docker compose up --build -d
   ```
3. Acesse em seu navegador: [http://localhost:8000](http://localhost:8000)

> ⚠️ **Aviso de Configuração para Usuários de Docker:**
> Por restrições de isolamento e segurança de containers, o backend em execução no Docker não conseguirá abrir o explorador de arquivos nativo do seu computador. Os botões **"Procurar..."** ficarão desabilitados. Você deverá preencher os caminhos manualmente utilizando as rotas internas que mapeou no passo anterior (exemplo: `/app/host_music`) ou utilizar o arrasto de pastas diretamente no navegador.

---

## Manual de Operação da Interface

1. **Definição de Rotas:** Insira os caminhos das suas pastas. O campo *"Origem"* representa a pasta de músicas desorganizadas que o programa irá ler. O campo *"Destino"* é a pasta limpa (ou a unidade de disco do cartão SD) onde a nova estrutura higienizada será gravada.
2. **Utilizando o Drag & Drop:** Se não quiser digitar o caminho, você pode arrastar uma pasta de música do seu computador e soltá-la em qualquer lugar da janela do navegador. O EchoOrganize executará uma busca heurística em locais comuns para decifrar a rota absoluta do arquivo e carregá-la. Se houver nomes idênticos em mais de um lugar, uma tela interativa solicitará que você confirme a pasta correta.
3. **Varredura e Mesclagem de Arquivos:** Ao definir a rota ou soltar uma pasta, a varredura se inicia. O programa lê apenas extensões compatíveis (`.mp3`, `.flac`, `.m4a`, `.mp4`). Se você arrastar pastas consecutivas, o sistema somará os arquivos na tabela de trabalho, permitindo consolidar múltiplos locais em uma única rodada. Arquivos já organizados no destino aparecem marcados em verde e são desmarcados por padrão.
4. **Consulta no MusicBrainz (Opcional):** Se as músicas originais não contiverem metadados ou nomes compreensíveis, selecione os itens desejados e clique em *"Buscar Lote MusicBrainz"* ou use a busca individual em cada linha. O backend gerencia o intervalo de requisições exigido pelos servidores externos (janela de 1.2 segundos por faixa) para evitar bloqueios de IP.
5. **Gravação Física Ordenada:** Clique no botão azul *"Gravar e Organizar Sequencialmente"* na parte inferior da interface. O sistema criará as pastas estruturadas no formato `Artista/Álbum/Faixa`, processará e comprimirá as imagens, embutirá as letras e escreverá fisicamente os arquivos de áudio e os arquivos `.lrc` na ordem temporal descrita no método FAT32.
6. **Relatório da Biblioteca:** Ao final de todo o processo, a ferramenta gravará um arquivo chamado `library_index.txt` na raiz da pasta de destino, contendo um mapa em árvore e estatísticas estruturais exatas sobre todas as músicas organizadas na sua biblioteca de áudio portátil.
