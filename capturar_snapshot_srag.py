"""Captura auditável de versões semanais do SIVEP-Gripe.

Uso:
    python capturar_snapshot_srag.py
    python capturar_snapshot_srag.py --anos 2025 2026 --data 2026-10-20
    python capturar_snapshot_srag.py --dry-run

O script não refaz o painel nem os modelos. Ele preserva o arquivo publicado no
dia da captura e registra URL, hash, tamanho e metadados HTTP em um manifesto
append-only. Isso permite substituir o pseudo-tempo-real por backtests baseados
no que realmente estava disponível em cada semana.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import requests


RAIZ = Path(__file__).resolve().parent
PORTAL = "https://dadosabertos.saude.gov.br"
S3 = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br"
SLUG = "srag-2019-a-2026"
HDR = {"User-Agent": "vsr-snapshot/1.0 (pesquisa academica; contato local)"}
RE_ARQ = re.compile(r"INFLUD\d{2}-\d{2}-\d{2}-\d{4}\.parquet", re.I)
LOG = logging.getLogger("snapshot_srag")

# Só é usado se as três rotas de descoberta falharem. A descoberta tem prioridade.
URLS_CONHECIDAS = {
    2025: f"{S3}/SRAG/2025/INFLUD25-19-10-2026.parquet",
    2026: f"{S3}/SRAG/2026/INFLUD26-19-10-2026.parquet",
}


def _data_publicacao(url: str) -> tuple[int, int, int]:
    """Retorna uma chave ordenável; URLs malformadas nunca vencem uma válida."""
    nome = Path(url.split("?", 1)[0]).name
    m = re.search(r"INFLUD\d{2}-(\d{2})-(\d{2})-(\d{4})\.parquet", nome, re.I)
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (0, 0, 0)


def _extrair_urls(texto: str) -> dict[int, str]:
    texto = texto.replace("\\/", "/")
    padrao = re.compile(
        r"https://s3\.sa-east-1\.amazonaws\.com/ckan\.saude\.gov\.br/SRAG/(\d{4})/"
        r"INFLUD\d{2}-\d{2}-\d{2}-\d{4}\.parquet",
        re.I,
    )
    achados: dict[int, str] = {}
    for m in padrao.finditer(texto):
        ano, url = int(m.group(1)), m.group(0)
        if ano not in achados or _data_publicacao(url) > _data_publicacao(achados[ano]):
            achados[ano] = url
    return achados


def _via_api() -> str:
    r = requests.get(
        f"{PORTAL}/api/3/action/package_show",
        params={"id": SLUG}, headers=HDR, timeout=60,
    )
    r.raise_for_status()
    return json.dumps(r.json())


def _via_s3(anos: list[int]) -> str:
    partes: list[str] = []
    for ano in anos:
        r = requests.get(S3, params={"list-type": "2", "prefix": f"SRAG/{ano}/"}, headers=HDR, timeout=60)
        r.raise_for_status()
        if "<ListBucketResult" not in r.text:
            raise RuntimeError("listagem do bucket não é pública")
        partes.extend(f"{S3}/{chave}" for chave in re.findall(r"<Key>([^<]+)</Key>", r.text))
    return " ".join(partes)


def _via_paginas() -> str:
    r = requests.get(f"{PORTAL}/dataset/{SLUG}", headers=HDR, timeout=60)
    r.raise_for_status()
    ids = list(dict.fromkeys(re.findall(rf"/dataset/{SLUG}/resource/([0-9a-f\-]{{36}})", r.text)))
    if not ids:
        raise RuntimeError("página do portal sem links de recursos")
    paginas = []
    for ident in ids:
        pagina = requests.get(f"{PORTAL}/dataset/{SLUG}/resource/{ident}", headers=HDR, timeout=60)
        pagina.raise_for_status()
        paginas.append(pagina.text)
    return " ".join(paginas)


def descobrir_urls(anos: list[int]) -> dict[int, str]:
    """Usa as mesmas três rotas do notebook 01, sempre escolhendo a publicação mais recente."""
    achados: dict[int, str] = {}
    for nome, consulta in (("API CKAN", _via_api), ("listagem S3", lambda: _via_s3(anos)), ("páginas", _via_paginas)):
        try:
            novos = _extrair_urls(consulta())
            LOG.info("descoberta via %s: %d arquivo(s)", nome, len(novos))
            for ano, url in novos.items():
                if ano not in achados or _data_publicacao(url) > _data_publicacao(achados[ano]):
                    achados[ano] = url
            if all(ano in achados for ano in anos):
                break
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            LOG.warning("descoberta via %s falhou: %s", nome, str(exc)[:160])
    for ano in anos:
        if ano not in achados and ano in URLS_CONHECIDAS:
            achados[ano] = URLS_CONHECIDAS[ano]
            LOG.warning("ano %s: usando URL conhecida; confirme na próxima captura", ano)
    ausentes = sorted(set(anos) - set(achados))
    if ausentes:
        raise RuntimeError(f"nenhuma URL encontrada para {ausentes}; snapshot não será parcial")
    return {ano: achados[ano] for ano in anos}


def sha256(caminho: Path, bloco: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as arq:
        for bloco_bytes in iter(lambda: arq.read(bloco), b""):
            h.update(bloco_bytes)
    return h.hexdigest()


def validar_parquet(caminho: Path) -> None:
    if not caminho.exists() or caminho.stat().st_size == 0:
        raise RuntimeError(f"arquivo vazio ou ausente: {caminho}")
    try:
        pq.ParquetFile(caminho).metadata
    except Exception as exc:  # noqa: BLE001 -- mensagem contextual para dado externo
        raise RuntimeError(f"Parquet inválido: {caminho.name}") from exc


def baixar(url: str, destino: Path, tentativas: int = 6) -> dict[str, str | int | None]:
    """Baixa com retomada e só promove o .part após validação do Parquet."""
    parcial = destino.with_suffix(destino.suffix + ".part")
    for tentativa in range(1, tentativas + 1):
        inicio = parcial.stat().st_size if parcial.exists() else 0
        cab = dict(HDR)
        if inicio:
            cab["Range"] = f"bytes={inicio}-"
        try:
            with requests.get(url, headers=cab, stream=True, timeout=(30, 300)) as resposta:
                resposta.raise_for_status()
                if inicio and resposta.status_code != 206:
                    parcial.unlink(missing_ok=True)
                    inicio = 0
                total = resposta.headers.get("Content-Length")
                with parcial.open("ab" if inicio else "wb") as arq:
                    for bloco in resposta.iter_content(1 << 20):
                        if bloco:
                            arq.write(bloco)
                esperado = inicio + int(total) if total else None
                if esperado is not None and parcial.stat().st_size < esperado:
                    raise RuntimeError(f"download incompleto: {parcial.stat().st_size}/{esperado} bytes")
                meta = {
                    "last_modified_servidor": resposta.headers.get("Last-Modified"),
                    "etag": resposta.headers.get("ETag"),
                    "bytes_esperados": esperado,
                }
            validar_parquet(parcial)
            parcial.replace(destino)
            return meta
        except (requests.RequestException, OSError, RuntimeError) as exc:
            if tentativa == tentativas:
                raise RuntimeError(f"download falhou para {url}") from exc
            espera = min(2**tentativa, 60)
            LOG.warning("tentativa %d/%d falhou (%s); repetindo em %ss", tentativa, tentativas, type(exc).__name__, espera)
            time.sleep(espera)
    raise AssertionError("laço de tentativas deveria ter encerrado")


def ler_manifesto(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    try:
        return [json.loads(linha) for linha in linhas if linha.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"manifesto corrompido: {caminho}") from exc


def registrar(caminho: Path, registro: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("a", encoding="utf-8", newline="\n") as arq:
        arq.write(json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n")


def principal(args: argparse.Namespace) -> int:
    anos = sorted(set(args.anos))
    rotulo = date.fromisoformat(args.data).isoformat()
    raiz = Path(args.raiz).resolve()
    manifesto = raiz / "manifesto_snapshots.jsonl"
    anteriores = ler_manifesto(manifesto)
    feitos_hoje = {int(x["ano"]) for x in anteriores if x.get("data_captura") == rotulo}
    if feitos_hoje and not args.forcar:
        raise RuntimeError(f"já há captura em {rotulo} para {sorted(feitos_hoje)}; use --forcar apenas após investigação")

    urls = descobrir_urls(anos)
    if args.dry_run:
        for ano, url in urls.items():
            print(f"{ano}: {url}")
        return 0

    anteriores_por_hash = {x.get("sha256"): x for x in anteriores if x.get("sha256")}
    for ano, url in urls.items():
        nome = Path(url.split("?", 1)[0]).name
        destino = raiz / rotulo / str(ano) / nome
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            raise RuntimeError(f"destino já existe: {destino}; não sobrescrevendo snapshot")
        meta = baixar(url, destino)
        digest = sha256(destino)
        igual = anteriores_por_hash.get(digest)
        if igual:
            # A repetição é registrada sem multiplicar cópias idênticas; o hash mantém a auditabilidade.
            destino.unlink()
            arquivo = igual["arquivo"]
            acao = "referencia_hash_existente"
        else:
            try:
                arquivo = destino.relative_to(RAIZ).as_posix()
            except ValueError:
                arquivo = str(destino)
            acao = "novo_arquivo"
        registro = {
            "acao": acao,
            "ano": ano,
            "arquivo": arquivo,
            "bytes": int(destino.stat().st_size) if destino.exists() else int(igual["bytes"]),
            "data_captura": rotulo,
            "data_publicacao": date(*_data_publicacao(url)).isoformat(),
            "etag": meta["etag"],
            "last_modified_servidor": meta["last_modified_servidor"],
            "registrado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sha256": digest,
            "url": url,
        }
        registrar(manifesto, registro)
        print(f"{ano}: {acao}; sha256={digest[:12]}; arquivo={arquivo}")
    return 0


def argumentos() -> argparse.Namespace:
    hoje = date.today()
    p = argparse.ArgumentParser(description="Captura versões semanais do SIVEP-Gripe sem alterar o pipeline.")
    p.add_argument("--anos", type=int, nargs="+", default=[hoje.year - 1, hoje.year], help="anos mutáveis a capturar")
    p.add_argument("--data", default=hoje.isoformat(), help="rótulo ISO da captura (padrão: hoje)")
    p.add_argument("--raiz", default=str(RAIZ / "data" / "raw" / "snapshots" / "SRAG"), help="diretório de snapshots")
    p.add_argument("--dry-run", action="store_true", help="descobre URLs, mas não baixa nem grava")
    p.add_argument("--forcar", action="store_true", help="permite segunda captura na mesma data; nunca sobrescreve arquivos")
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        sys.exit(principal(argumentos()))
    except (RuntimeError, ValueError) as exc:
        LOG.error("snapshot não concluído: %s", exc)
        sys.exit(2)
