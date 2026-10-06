"""Download, cache, and load the BEIR SciFact benchmark.

The archive is treated as untrusted data: it is never extracted to disk — the three
known members are read directly out of the zip and parsed as JSONL/TSV only.
"""

from __future__ import annotations

import json
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
ZIP_PATH = DATA_DIR / "scifact.zip"
URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"

_CORPUS = "scifact/corpus.jsonl"
_QUERIES = "scifact/queries.jsonl"
_QRELS_TEST = "scifact/qrels/test.tsv"


@dataclass(frozen=True)
class Doc:
    doc_id: str
    title: str
    text: str


@dataclass(frozen=True)
class SciFact:
    corpus: list[Doc]
    queries: dict[str, str]  # test queries only: query_id -> text
    qrels: dict[str, dict[str, int]]  # query_id -> {doc_id: relevance}


def ensure_downloaded() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ZIP_PATH.exists():
        tmp = ZIP_PATH.with_suffix(".part")
        with urllib.request.urlopen(URL, timeout=120) as resp, open(tmp, "wb") as fh:  # noqa: S310
            fh.write(resp.read())
        tmp.replace(ZIP_PATH)
    return ZIP_PATH


def _read_member(zf: zipfile.ZipFile, name: str) -> str:
    return zf.read(name).decode("utf-8")


def load() -> SciFact:
    with zipfile.ZipFile(ensure_downloaded()) as zf:
        corpus = []
        for line in _read_member(zf, _CORPUS).splitlines():
            if line.strip():
                row = json.loads(line)
                corpus.append(
                    Doc(str(row["_id"]), str(row.get("title") or ""), str(row.get("text") or ""))
                )

        qrels: dict[str, dict[str, int]] = {}
        for i, line in enumerate(_read_member(zf, _QRELS_TEST).splitlines()):
            parts = line.strip().split("\t")
            if i == 0 and parts[0] == "query-id":
                continue
            if len(parts) != 3:
                continue
            qid, did, score = parts
            if int(score) > 0:
                qrels.setdefault(qid, {})[did] = int(score)

        queries: dict[str, str] = {}
        for line in _read_member(zf, _QUERIES).splitlines():
            if line.strip():
                row = json.loads(line)
                qid = str(row["_id"])
                if qid in qrels:
                    queries[qid] = str(row["text"])

    return SciFact(corpus=corpus, queries=queries, qrels=qrels)
