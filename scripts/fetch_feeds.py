#!/usr/bin/env python3
"""Research & Tech Intelligence Aggregator - feed fetcher.

Fetches arXiv queries, RSS/Atom blogs and GitHub release feeds, then
normalises, classifies (math / quantum / ai), deduplicates, scores and writes
``public/data/feed.json``. Existing items are merged so history accumulates.

Usage:
    python scripts/fetch_feeds.py [--output PATH] [--max-age-days N] [--max-items N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "public" / "data" / "feed.json"
USER_AGENT = (
    "ResearchIntelAggregator/1.0 (+https://github.com/; personal research feed)"
)
TIMEOUT = 25
ARXIV_API = "https://export.arxiv.org/api/query"

# --------------------------------------------------------------------------
# Relevance keywords: domain -> {regex fragment: weight}
# --------------------------------------------------------------------------
KEYWORDS: dict[str, dict[str, float]] = {
    "math": {
        r"commutative algebra": 4, r"commutative ring": 4, r"noetherian": 3,
        r"local ring": 3, r"cohen-macaulay": 4, r"gorenstein": 3,
        r"gr[oö]bner basis|gr[oö]bner bases": 3, r"regular sequence": 2,
        r"homological algebra": 3, r"hilbert function": 3, r"ideal(s)?": 1,
        r"associative algebra": 3, r"lie algebra": 3, r"hopf algebra": 3,
        r"ring theory": 3, r"module(s)? over": 2, r"polynomial ring": 3,
        r"group theory": 3, r"finite group": 3, r"simple group": 3,
        r"cayley graph": 2, r"representation of groups?": 2,
        r"algebraic geometry": 4, r"scheme(s)?": 1, r"moduli space": 3,
        r"derived categor(y|ies)": 3, r"sheaf|sheaves|cohomology": 2,
        r"hodge": 2, r"birational": 3, r"tropical": 2, r"motive(s)?": 2,
        r"representation theory": 4, r"quiver": 3, r"cluster algebra": 3,
        r"categorification": 3, r"langlands": 3, r"galois": 2,
        r"algebra(ic)?": 1, r"conjecture": 1, r"proof": 1,
    },
    "quantum": {
        r"quantum error correction": 5, r"error[- ]correct(ing|ion)? code": 3,
        r"fault[- ]tolerant": 5, r"surface code": 5, r"logical qubit(s)?": 5,
        r"stabilizer": 3, r"qldpc|ldpc code": 4, r"magic state": 4,
        r"qaoa": 5, r"vqe|variational quantum eigensolver": 5,
        r"quantum phase estimation|\bqpe\b": 5, r"variational quantum": 4,
        r"quantum algorithm(s)?": 4, r"quantum advantage|quantum supremacy": 4,
        r"grover": 3, r"shor'?s": 3, r"hamiltonian simulation": 4,
        r"quantum hardware": 4, r"superconducting qubit(s)?": 4,
        r"trapped[- ]ion": 4, r"neutral atom": 3, r"photonic quantum": 3,
        r"qubit(s)?": 3, r"quantum comput(er|ing|ers)": 3,
        r"quantum processor": 3, r"qiskit|cirq|pennylane|braket": 3,
        r"quantum": 1, r"entanglement": 2, r"noisy intermediate": 3, r"nisq": 3,
        r"quantum error mitigation|error mitigation": 4,
    },
    "ai": {
        r"vllm": 6, r"pagedattention|paged attention": 6, r"kv[- ]cache": 5,
        r"speculative decoding": 5, r"flash ?attention": 5,
        r"continuous batching": 4, r"prefix caching": 4, r"inference optimi[sz]ation": 4,
        r"llm inference|serving": 3, r"quantization|quantisation|quantized": 3,
        r"\bawq\b|\bgptq\b|fp8|int4": 3, r"tensor parallel(ism)?": 3,
        r"transformer(s)?": 3, r"attention mechanism": 2, r"self-attention": 2,
        r"state[- ]space model(s)?|\bssm(s)?\b": 5, r"mamba": 6, r"rwkv": 6,
        r"mixture[- ]of[- ]experts|\bmoe\b": 5, r"linear attention": 5,
        r"diffusion (language )?model(s)?|diffusion llm": 4,
        r"hybrid architecture": 3, r"retnet|hyena|griffin|xlstm": 4,
        r"long[- ]context": 3, r"context window": 2, r"rope|rotary embedding": 2,
        r"large language model(s)?|\bllm(s)?\b": 2, r"language model(s)?": 1,
        r"reasoning model(s)?": 2, r"fine-?tuning|lora": 1,
        r"hugging ?face": 2, r"pytorch": 1,
    },
}

DOMAIN_LABELS = {
    "math": "Mathematics (Algebra)",
    "quantum": "Quantum Computing",
    "ai": "LLMs & AI Architectures",
}

# --------------------------------------------------------------------------
# Sources
#   type: paper | blog | release
#   weight: source authority 0..1
#   filter: if True, item must reach ``min_rel`` raw relevance to be kept
# --------------------------------------------------------------------------
LLM_TERMS = (
    '"KV cache" OR abs:PagedAttention OR abs:vLLM OR abs:"speculative decoding" '
    'OR abs:FlashAttention OR abs:"state space model" OR abs:Mamba OR abs:RWKV '
    'OR abs:"mixture of experts" OR abs:"linear attention" OR abs:"diffusion language model" '
    'OR abs:"LLM inference" OR abs:"quantization" OR abs:"long context"'
)

ARXIV_SOURCES: list[dict[str, Any]] = [
    {"name": "arXiv math.AC", "domain": "math", "query": "cat:math.AC", "weight": 0.9, "filter": False, "max": 30},
    {"name": "arXiv math.RA", "domain": "math", "query": "cat:math.RA", "weight": 0.9, "filter": False, "max": 30},
    {"name": "arXiv math.GR", "domain": "math", "query": "cat:math.GR", "weight": 0.9, "filter": False, "max": 30},
    {"name": "arXiv math.AG", "domain": "math", "query": "cat:math.AG", "weight": 0.9, "filter": False, "max": 30},
    {"name": "arXiv math.RT", "domain": "math", "query": "cat:math.RT", "weight": 0.9, "filter": False, "max": 30},
    {"name": "arXiv quant-ph", "domain": "quantum", "query": "cat:quant-ph", "weight": 0.85, "filter": True, "min_rel": 3, "max": 80},
    {
        "name": "arXiv cs.CL/LG/AI",
        "domain": "ai",
        "query": f"(cat:cs.CL OR cat:cs.LG OR cat:cs.AI) AND (abs:{LLM_TERMS})",
        "weight": 0.85, "filter": True, "min_rel": 4, "max": 100,
    },
]

FEED_SOURCES: list[dict[str, Any]] = [
    # --- Mathematics ---
    {"name": "Quanta Magazine", "url": "https://www.quantamagazine.org/feed/", "type": "blog", "domain": "math", "weight": 0.9, "filter": True, "min_rel": 1},
    {"name": "Terence Tao", "url": "https://terrytao.wordpress.com/feed/", "type": "blog", "domain": "math", "weight": 0.95, "filter": False},
    {"name": "SageMath Releases", "url": "https://github.com/sagemath/sage/releases.atom", "type": "release", "domain": "math", "weight": 0.7, "filter": False},
    {"name": "Macaulay2 Releases", "url": "https://github.com/Macaulay2/M2/releases.atom", "type": "release", "domain": "math", "weight": 0.7, "filter": False},
    {"name": "GAP Releases", "url": "https://github.com/gap-system/gap/releases.atom", "type": "release", "domain": "math", "weight": 0.7, "filter": False},
    # --- Quantum ---
    {"name": "The Quantum Insider", "url": "https://thequantuminsider.com/feed/", "type": "blog", "domain": "quantum", "weight": 0.7, "filter": True, "min_rel": 2},
    {"name": "Shtetl-Optimized", "url": "https://scottaaronson.blog/?feed=rss2", "type": "blog", "domain": "quantum", "weight": 0.9, "filter": False},
    {"name": "Google Quantum AI", "url": "https://research.google/blog/rss/", "type": "blog", "domain": "quantum", "weight": 0.85, "filter": True, "min_rel": 3},
    {"name": "Qiskit Releases", "url": "https://github.com/Qiskit/qiskit/releases.atom", "type": "release", "domain": "quantum", "weight": 0.8, "filter": False},
    {"name": "Cirq Releases", "url": "https://github.com/quantumlib/Cirq/releases.atom", "type": "release", "domain": "quantum", "weight": 0.75, "filter": False},
    {"name": "PennyLane Releases", "url": "https://github.com/PennyLaneAI/pennylane/releases.atom", "type": "release", "domain": "quantum", "weight": 0.7, "filter": False},
    # --- AI ---
    {"name": "vLLM Blog", "url": "https://vllm.ai/blog/rss.xml", "type": "blog", "domain": "ai", "weight": 1.0, "filter": False},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "type": "blog", "domain": "ai", "weight": 0.85, "filter": True, "min_rel": 2},
    {"name": "Lil'Log", "url": "https://lilianweng.github.io/index.xml", "type": "blog", "domain": "ai", "weight": 0.9, "filter": False},
    {"name": "Ahead of AI", "url": "https://magazine.sebastianraschka.com/feed", "type": "blog", "domain": "ai", "weight": 0.85, "filter": True, "min_rel": 2},
    {"name": "vLLM Releases", "url": "https://github.com/vllm-project/vllm/releases.atom", "type": "release", "domain": "ai", "weight": 1.0, "filter": False},
    {"name": "Transformers Releases", "url": "https://github.com/huggingface/transformers/releases.atom", "type": "release", "domain": "ai", "weight": 0.95, "filter": False},
    {"name": "Mamba Releases", "url": "https://github.com/state-spaces/mamba/releases.atom", "type": "release", "domain": "ai", "weight": 0.8, "filter": False},
    {"name": "FlashAttention Releases", "url": "https://github.com/Dao-AILab/flash-attention/releases.atom", "type": "release", "domain": "ai", "weight": 0.85, "filter": False},
    {"name": "SGLang Releases", "url": "https://github.com/sgl-project/sglang/releases.atom", "type": "release", "domain": "ai", "weight": 0.8, "filter": False},
]

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_COMPILED = {
    dom: [(re.compile(rf"(?<![a-z0-9])(?:{pat})(?![a-z0-9])", re.I), w, pat) for pat, w in kws.items()]
    for dom, kws in KEYWORDS.items()
}
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "fbclid", "gclid", "source"}
_ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/([^?#\s]+?)(?:v\d+)?(?:\.pdf)?(?:[?#].*)?$", re.I)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_url(url: str) -> str:
    url = (url or "").strip()
    m = _ARXIV_ID.search(url)
    if m:
        return f"https://arxiv.org/abs/{m.group(1)}"
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in _TRACKING]
    path = p.path.rstrip("/") or "/"
    return urlunparse(("https", p.netloc.lower(), path, "", urlencode(query), ""))


def make_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


def clean_text(html: str, limit: int = 420) -> str:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^Abstract:?\s*", "", text, flags=re.I)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return text


def norm_title(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_date(entry: Any) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return now_utc()


def entry_authors(entry: Any) -> list[str]:
    names = [a.get("name", "").strip() for a in entry.get("authors", []) if a.get("name")]
    if not names and entry.get("author"):
        names = [entry["author"].strip()]
    return names[:4] + (["et al."] if len(names) > 4 else [])


def relevance(text_title: str, text_body: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Return raw relevance per domain and the matched keyword labels."""
    raw: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for dom, patterns in _COMPILED.items():
        total = 0.0
        found: list[tuple[float, str]] = []
        for rx, weight, _pat in patterns:
            t_hits = rx.findall(text_title)
            b_hits = rx.findall(text_body)
            if t_hits or b_hits:
                m = rx.search(text_title) or rx.search(text_body)
                label = m.group(0).lower() if m else ""
                # Title matches count double; repeats saturate.
                total += weight * (2.0 if t_hits else 0.0) + weight * min(len(b_hits), 3) * 0.6
                found.append((weight, label))
        raw[dom] = total
        found.sort(key=lambda x: -x[0])
        seen: list[str] = []
        for _, label in found:
            if label and label not in seen:
                seen.append(label)
        hits[dom] = seen[:5]
    return raw, hits


def score_item(raw_rel: float, published: datetime, weight: float, kind: str) -> float:
    rel = min(1.0, raw_rel / 18.0)
    age_h = max(0.0, (now_utc() - published).total_seconds() / 3600)
    recency = math.exp(-age_h / 96.0)  # ~4-day time constant
    bonus = 0.05 if kind == "release" else 0.0
    return round(100 * min(1.0, 0.5 * rel + 0.3 * recency + 0.2 * weight + bonus), 1)


def build_item(entry: Any, src: dict[str, Any], kind: str, extra_tags: list[str] | None = None) -> dict[str, Any] | None:
    title = clean_text(entry.get("title", ""), 220)
    link = entry.get("link") or ""
    if not title or not link:
        return None
    body_html = entry.get("summary") or ""
    if not body_html and entry.get("content"):
        body_html = entry["content"][0].get("value", "")
    summary = clean_text(body_html)
    published = parse_date(entry)

    raw, hits = relevance(title, summary)
    domain = src["domain"]
    # Reclassify if another domain clearly dominates (e.g. a quantum post in a general feed).
    best = max(raw, key=lambda d: raw[d])
    if best != domain and raw[best] >= max(6.0, raw[domain] * 1.8):
        domain = best
    rel = raw[domain]

    if src.get("filter") and rel < src.get("min_rel", 2) * 2:
        return None

    tags = list(dict.fromkeys((extra_tags or []) + hits[domain]))[:6]
    return {
        "id": make_id(link),
        "title": title,
        "url": canonical_url(link),
        "source": src["name"],
        "also_in": [],
        "type": kind,
        "domain": domain,
        "tags": tags,
        "summary": summary,
        "authors": entry_authors(entry) if kind == "paper" else [],
        "published": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "relevance": round(rel, 1),
        "weight": src["weight"],
        "score": score_item(rel, published, src["weight"], kind),
    }


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def http_get(url: str, params: dict[str, Any] | None = None, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:  # noqa: PERF203
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last))


def fetch_feed_source(src: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = feedparser.parse(http_get(src["url"]))
    items = [build_item(e, src, src["type"]) for e in parsed.entries[:40]]
    return [i for i in items if i]


def fetch_arxiv_source(src: dict[str, Any]) -> list[dict[str, Any]]:
    params = {
        "search_query": src["query"],
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": src.get("max", 50),
    }
    parsed = feedparser.parse(http_get(ARXIV_API, params))
    out = []
    for e in parsed.entries:
        cats = [t.get("term", "") for t in e.get("tags", []) if t.get("term")]
        item = build_item(e, src, "paper", extra_tags=cats[:2])
        if item:
            out.append(item)
    return out


def fetch_all() -> tuple[list[dict[str, Any]], dict[str, str]]:
    items: list[dict[str, Any]] = []
    status: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_feed_source, s): s for s in FEED_SOURCES}
        # arXiv politely in the main thread, sequentially (3s etiquette).
        for s in ARXIV_SOURCES:
            try:
                got = fetch_arxiv_source(s)
                items.extend(got)
                status[s["name"]] = f"ok ({len(got)})"
                log(f"[ok]   {s['name']}: {len(got)}")
            except Exception as exc:  # noqa: BLE001
                status[s["name"]] = f"error: {exc}"[:160]
                log(f"[fail] {s['name']}: {exc}")
            time.sleep(3)
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                got = fut.result()
                items.extend(got)
                status[s["name"]] = f"ok ({len(got)})"
                log(f"[ok]   {s['name']}: {len(got)}")
            except Exception as exc:  # noqa: BLE001
                status[s["name"]] = f"error: {exc}"[:160]
                log(f"[fail] {s['name']}: {exc}")
    return items, status


# --------------------------------------------------------------------------
# Dedup / merge
# --------------------------------------------------------------------------
def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup by id first, then by fuzzy title similarity (keeps best score)."""
    by_id: dict[str, dict[str, Any]] = {}
    for it in items:
        cur = by_id.get(it["id"])
        if cur is None or it["score"] > cur["score"]:
            by_id[it["id"]] = it
    ordered = sorted(by_id.values(), key=lambda i: -i["score"])
    kept: list[dict[str, Any]] = []
    sigs: list[set[str]] = []
    for it in ordered:
        sig = norm_title(it["title"])
        dup_idx = next((k for k, s in enumerate(sigs) if len(sig) >= 4 and jaccard(sig, s) >= 0.85), None)
        if dup_idx is None:
            kept.append(it)
            sigs.append(sig)
        else:
            other = kept[dup_idx]
            if it["source"] != other["source"] and it["source"] not in other["also_in"]:
                other["also_in"].append(it["source"])
    return kept


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if data.get("meta", {}).get("sample"):
        return []  # discard bundled sample data on first real run
    return data.get("items", [])


def rescore(items: list[dict[str, Any]]) -> None:
    for it in items:
        published = datetime.strptime(it["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        it["score"] = score_item(it.get("relevance", 0), published, it.get("weight", 0.7), it["type"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--max-age-days", type=int, default=45)
    ap.add_argument("--max-items", type=int, default=600)
    args = ap.parse_args()

    existing = load_existing(args.output)
    existing_ids = {i["id"] for i in existing}
    fresh, status = fetch_all()
    log(f"Fetched {len(fresh)} candidate items; {len(existing)} existing.")

    if not fresh and existing:
        log("No new items fetched; keeping existing data.")

    now = now_utc()
    first_seen = {i["id"]: i.get("first_seen") for i in existing}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for it in fresh:
        it["first_seen"] = first_seen.get(it["id"]) or stamp
    merged = deduplicate(fresh + existing)
    for it in merged:
        it.setdefault("first_seen", stamp)

    cutoff = now - timedelta(days=args.max_age_days)
    merged = [
        i for i in merged
        if datetime.strptime(i["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= cutoff
    ]
    rescore(merged)
    merged.sort(key=lambda i: (-i["score"], i["published"]))
    merged = merged[: args.max_items]
    merged.sort(key=lambda i: i["published"], reverse=True)

    counts = {d: sum(1 for i in merged if i["domain"] == d) for d in DOMAIN_LABELS}
    payload = {
        "meta": {
            "generated_at": stamp,
            "sample": False,
            "total": len(merged),
            "new_this_run": sum(1 for i in merged if i["id"] not in existing_ids),
            "domains": DOMAIN_LABELS,
            "counts": counts,
            "sources": status,
        },
        "items": merged,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"Wrote {len(merged)} items to {args.output} ({counts}).")
    failed = [k for k, v in status.items() if v.startswith("error")]
    if failed:
        log(f"Sources with errors: {', '.join(failed)}")
    # Only fail the build if nothing at all is available.
    return 0 if merged else 1


if __name__ == "__main__":
    sys.exit(main())
