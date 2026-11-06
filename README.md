# EchoOrganize

Quem usa DAPs de entrada ou intermediários — como o **SnowSky Echo Mini**, players baseados em SoCs **Rockchip, Ingenic ou Actions**, ou centrais multimídia automotivas antigas — cedo ou tarde esbarra nos mesmos problemas irritantes: álbuns que tocam fora de ordem mesmo com as faixas numeradas, capas que travam o aparelho ou aparecem como um quadrado preto, nomes truncados e acentos quebrados na tela.

A culpa quase nunca é do cartão microSD. O problema está na forma como sistemas operacionais modernos gravam em volumes **FAT32/exFAT** e em como os decodificadores de hardware desses aparelhos leem containers de áudio.

O **EchoOrganize** nasceu para resolver exatamente essa camada física e binária antes que as músicas cheguem ao cartão SD.

---

## O que acontece debaixo do capô (e como o projeto resolve)

### 1. A ordem física na tabela FAT32 e o mito do `sleep(50ms)`
Muitos firmwares embarcados não ordenam a lista de reprodução por ordem alfabética nem pelo número da tag `TRCK` — por economia de RAM, eles simplesmente leem os *slots* da tabela de diretório FAT32 na sequência física em que foram alocados no disco.

Colocar um `sleep` entre cópias no Python não resolve isso por três motivos:
1. O *Write Cache* do kernel (Windows/Linux) acumula as escritas na RAM e despeja os blocos no barramento USB/SD fora de ordem.
2. Se você grava `01.mp3`, depois `01.lrc`, depois `02.mp3`, os arquivos `.lrc` passam a ocupar slots intercalados na tabela de diretório do álbum.
3. Se a pasta do álbum já existia no cartão e teve arquivos apagados antes, a tabela FAT possui "buracos" (slots livres antigos). O driver do sistema de arquivos aloca a faixa nova no primeiro buraco livre, destruindo a sequência do disco.

**Como o [`fat32_writer.py`](app/services/fat32_writer.py) grava:**
* **Escrita em Duas Passadas por álbum:** Na **Passada 1**, o álbum inteiro é ordenado numericamente por `(disc_number, track_number)` e apenas os arquivos de áudio são gravados em sequência. Cada faixa vai para um arquivo `.tmp`, recebe as tags definitivas, passa por `file.flush()` + `os.fsync(fd)` (forçando a controladora do cartão SD a confirmar a escrita física) e é renomeada atomicamente com `os.replace`. Só quando todas as faixas de áudio do álbum estão gravadas é que a **Passada 2** começa, gravando os arquivos `.lrc` (em `utf-8-sig`, com BOM para evitar mojibake em telas CJK/latinas) no fim da tabela de diretório.
* **Desfragmentação por Staging:** Se a pasta de destino do álbum já existir no cartão SD, o álbum inteiro é montado primeiro em um diretório oculto temporário no mesmo volume e substituído ao final, garantindo uma tabela de diretório nova e contígua, sem slots órfãos.

---

### 2. Capas de álbum que não derrubam o decodificador (`SOF0 Baseline 4:2:0`)
Decodificadores JPEG de hardware em SoCs embarcados têm memória fixa e costumam falhar silenciosamente diante de **JPEGs Progressivos (`SOF2`)**, amostragem de cor `4:4:4` ou blocos gigantes de metadados `EXIF` (`APP1`) e perfil de cor `ICC` (`APP2`). Por outro lado, recomprimir toda capa cegamente em JPEG 85% degrada capas que já estavam prontas (*generation loss*).

**Como o [`cover_processor.py`](app/core/cover_processor.py) trata imagens:**
* **Inspeção binária de cabeçalho (Bypass sem perda):** Antes de abrir a imagem no Pillow, o código inspeciona os marcadores binários do JPEG. Se a imagem já for **Baseline DCT (`SOF0` / `0xFFC0`)**, não tiver marcador progressivo (`SOF2` / `0xFFC2`), estiver em subsampling **4:2:0** (`0x22, 0x11, 0x11`), não carregar blocos `APP1`/`APP2`, tiver até `500x500 px` e pesar até `150 KB`, os bytes originais são reaproveitados diretamente sem recompressão.
* **Reencoding estrito e cache por álbum:** Quando a capa precisa ser convertida (PNG, WebP, resolução alta ou JPEG progressivo), ela é redimensionada via `LANCZOS` para no máximo `500x500 px` e salva com `progressive=False`, `subsampling="4:2:0"`, `optimize=True`, `quality=85` e sem EXIF/ICC. O resultado fica em cache por álbum (RAM + SQLite), fazendo apenas **1 download e 1 redimensionamento por álbum**, não por faixa.

---

### 3. Limpeza de containers de áudio e proteção de caminho (`char[256]`)
Arquivos baixados ou ripados de várias fontes costumam acumular problemas invisíveis que travam a leitura inicial do DAP:
* **FLAC com megabytes de zeros:** Quando você remove uma capa de 5 MB de um FLAC usando o Mutagen sem limpar os blocos de padding, esses 5 MB viram preenchimento de zeros no cabeçalho do arquivo. O DAP tenta ler tudo antes do primeiro frame de áudio e engasga. Em [`audio_tagger.py`](app/services/audio_tagger.py), todo FLAC passa por `clear()`, `clear_pictures()` e é salvo com `padding=lambda info: min(info.size, 4096)`, cravando o teto de padding em **4 KB**.
* **MP3 com tags conflitantes:** Cabeçalhos residuais `APEv2` (comuns após passar pelo MP3Gain ou foobar2000) e rodapés `ID3v1` de 30 bytes são removidos, gravando estritamente em **ID3v2.3 (UTF-16)** com quadro `APIC` único (`type=3`, Front Cover).
* **Fim do bug `01 - 01 - Música` e lixo de rip:** O [`normalizer.py`](app/core/normalizer.py) converte todas as strings para **Unicode NFC** (corrigindo acentos decompostos em NFD vindos do macOS que falham em fontes bitmap), remove numeração duplicada no título, limpa sufixos como `(Official Video)`, `[Remastered 2011]`, `(320kbps)` e migra participações (`feat. X`) do título para a tag de artista da faixa, preservando o `Album Artist` para não quebrar o agrupamento da pasta.
* **Trava de 200 caracteres e FAT32:** Caracteres ilegais em FAT32 (`< > : " / \ | ? *`), nomes reservados do DOS (`CON`, `AUX`, `NUL`, `COM1`..`LPT9`) e pontos no final de nomes de pastas (como `...And Justice for All` ou `St. Germain`) são higienizados. O caminho relativo completo (`Artista/Álbum/01 - Faixa.ext`) nunca ultrapassa **200 caracteres**, truncando apenas o título da música sem cortar o número da faixa nem a extensão, protegendo o buffer `char[256]` de firmwares escritos em C.

---

### 4. Sincronização por Álbum (`Release-Centric`) e duração física real (`±3s`)
Consultar a API do MusicBrainz faixa por faixa em uma biblioteca grande leva horas devido ao limite de 1 requisição por segundo, além de misturar metadados de coletâneas ("Various Artists") no meio de álbuns de estúdio.

* Em [`musicbrainz.py`](app/services/musicbrainz.py) e [`reconciler.py`](app/core/reconciler.py), a busca agrupa os arquivos por álbum e faz **1 consulta de Release por álbum**.
* As faixas locais são cruzadas com o Release combinando **similaridade de título**, **número de faixa/disco** e a **duração física real do áudio (`duration` em segundos, tolerância `±3s`)** extraída via Mutagen — diferenciando versões de estúdio de faixas *Live*, *Radio Edit* ou *Extended*.
* As letras sincronizadas buscadas no **LRCLib** ([`lrclib.py`](app/services/lrclib.py)) também filtram os resultados pela duração física (`<= 3s`), evitando embutir timestamps deslocados. Todas as respostas (incluindo buscas sem resultado) ficam salvas localmente em SQLite (`WAL`) com auto-recuperação de schema ([`cache.py`](app/services/cache.py)).

---

## Estrutura do Projeto

```text
echo-organizer-dap/
├── app/
│   ├── main.py                   # Factory FastAPI, rotas estáticas e ciclo de vida (lifespan)
│   ├── config.py                 # Limites de hardware DAP/FAT32, rate-limits e logger
│   ├── security.py               # Proteção ASGI contra DNS Rebinding, CSRF cross-origin e CSP
│   ├── schemas.py                # Schemas Pydantic de entrada e saída da API
│   ├── api/
│   │   ├── routes_fs.py          # Explorador nativo de discos e cartões SD (/api/fs/*)
│   │   ├── routes_library.py     # Scanner de diretórios, preview de caminhos e library_index.txt
│   │   ├── routes_metadata.py    # Rotas de sincronização MusicBrainz (individual e por álbum)
│   │   └── routes_jobs.py        # Fila de gravação em background com streaming SSE e Pause/Cancel
│   ├── core/
│   │   ├── normalizer.py         # Normalização NFC, limpeza semântica, feat. e caminhos FAT32
│   │   ├── cover_processor.py    # Validação binária SOF0 4:2:0 e reencoding via Pillow
│   │   └── reconciler.py         # Reconciliação de álbuns ponderada por duração física (+-3s)
│   └── services/
│       ├── audio_tagger.py       # Leitura e gravação limpa de containers MP3, FLAC e M4A/MP4
│       ├── fat32_writer.py       # Escrita sequencial em Duas Passadas + os.fsync + staging
│       ├── musicbrainz.py        # Cliente Release-Centric MusicBrainz e Cover Art Archive
│       ├── lrclib.py             # Cliente LRCLib filtrado por duração física
│       ├── cache.py              # Cache persistente SQLite (WAL) e cache em memória de capas
│       ├── filesystem.py         # Descoberta de unidades locais/removíveis e listagem segura
│       ├── indexer.py            # Gerador do mapa library_index.txt
│       └── job_manager.py        # Worker de jobs assíncronos e eventos Server-Sent Events (SSE)
├── frontend/
│   ├── index.html                # Interface Cyberpunk Retro-Hardware (transporte DAT e visor VFD)
│   ├── styles.css                # Estilização industrial de alto contraste
│   └── app.js                    # Controle de estado por álbum, detector de colisões e cliente SSE
└── requirements.txt              # Dependências Python
```

---

## Como rodar

### Pré-requisitos
* **Python 3.10+**

### 1. Instalar dependências
```bash
pip install -r requirements.txt
```

### 2. Iniciar o servidor
```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abra no navegador: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

### Fluxo de uso rápido
1. Clique em **`[ EXPLORAR ]`** no campo de **Origem** para escolher a pasta com seus arquivos brutos (`.mp3`, `.flac`, `.m4a`, `.mp4`) e no campo de **Destino** para selecionar a unidade do cartão SD do DAP (ou uma pasta local de saída).
2. Clique em **`VARRER & ANALISAR BARRAMENTO`**. O sistema extrai a duração física real de cada faixa, aplica a limpeza local de nomes/tags e agrupa tudo em blocos de álbum mostrando o preview exato do endereço físico FAT32 (`[XXX/200B]`) e eventuais colisões de nome.
3. *(Opcional)* Clique em **`SINCRONIZAR ÁLBUNS [MUSICBRAINZ + LRCLIB]`** (ou sincronize um álbum específico) para buscar os dados oficiais do lançamento, capas frontais e letras `.lrc` sincronizadas.
4. Clique em **`GRAVAR & ORGANIZAR SEQUENCIALMENTE [FAT32]`** e acompanhe no console a gravação em Duas Passadas com barra de progresso em tempo real e controles de pausa/cancelamento.
