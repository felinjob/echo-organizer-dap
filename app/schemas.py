"""
Esquemas de validação de dados (Pydantic Models) para a API REST e SSE.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    """Parâmetros para varredura recursiva de um diretório de origem."""

    source_dir: str = Field(..., description="Caminho absoluto do diretório de origem.")
    dest_dir: Optional[str] = Field(
        default=None,
        description="Caminho opcional do diretório de destino para checar arquivos já organizados.",
    )


class PreviewPathRequest(BaseModel):
    """Parâmetros para pré-visualização do caminho relativo seguro (<= 200 caracteres)."""

    tags: Dict[str, Any] = Field(..., description="Dicionário de metadados propostos da faixa.")
    extension: Optional[str] = Field(default=".mp3", description="Extensão do arquivo de áudio.")


class MusicBrainzQuery(BaseModel):
    """Parâmetros de busca individual no MusicBrainz e LRCLib."""

    artist: Optional[str] = Field(default="", description="Nome do artista da faixa.")
    title: Optional[str] = Field(default="", description="Título da faixa.")
    album: Optional[str] = Field(default="", description="Nome do álbum (opcional).")
    filename: Optional[str] = Field(default="", description="Nome do arquivo bruto para fallback heurístico.")
    duration: Optional[int] = Field(default=0, description="Duração real da faixa em segundos.")


class ReleaseBatchTrackItem(BaseModel):
    """Representa um item da fila enviado para enriquecimento em lote por Release."""

    source: Optional[str] = Field(default="", description="Caminho de origem do arquivo.")
    filename: Optional[str] = Field(default="", description="Nome do arquivo.")
    proposed: Dict[str, Any] = Field(..., description="Metadados atuais propostos para a faixa.")


# Alias de compatibilidade
ReleaseBatchItem = ReleaseBatchTrackItem


class ReleaseBatchRequest(BaseModel):
    """Requisição de enriquecimento em lote centrado em Álbum (Release-Centric)."""

    items: List[ReleaseBatchTrackItem] = Field(..., description="Lista de faixas a enriquecer.")
    fetch_lyrics: Optional[bool] = Field(
        default=True,
        description="Se verdadeiro, busca também letras sincronizadas no LRCLib filtradas por duration.",
    )


class FileTagMapping(BaseModel):
    """Mapeamento entre um arquivo de origem e as tags higienizadas a serem gravadas."""

    source: str = Field(..., description="Caminho absoluto do arquivo de áudio de origem.")
    tags: Dict[str, Any] = Field(..., description="Metadados higienizados a gravar.")


class ProcessRequest(BaseModel):
    """Payload de gravação física em lote completo."""

    dest_dir: str = Field(..., description="Diretório raiz de destino (ex: unidade do Cartão SD).")
    mappings: List[FileTagMapping] = Field(..., description="Fila completa de arquivos selecionados.")
    generate_index: Optional[bool] = Field(
        default=True,
        description="Se verdadeiro, gera o arquivo library_index.txt ao concluir o lote.",
    )


class IndexRequest(BaseModel):
    """Requisição para geração avulsa do índice estrutural da biblioteca."""

    dest_dir: str = Field(..., description="Diretório raiz da biblioteca organizada.")
