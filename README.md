# Research & Tech Intelligence Aggregator

A hybrid **Python + static frontend** dashboard that curates, scores and displays the latest high-signal items across:

| Domain | Sources |
|---|---|
| ∑ **Mathematics (Algebra)** | arXiv `math.AC`, `math.RA`, `math.GR`, `math.AG`, `math.RT`; Quanta; Terence Tao; SageMath / Macaulay2 / GAP releases |
| ⚛ **Quantum Computing** | arXiv `quant-ph` (QEC, fault tolerance, QAOA/VQE/QPE, hardware); Shtetl-Optimized; Quantum Insider; Google Research; Qiskit / Cirq / PennyLane releases |
| 🧠 **LLMs & AI Architectures** | arXiv `cs.CL/LG/AI` (vLLM, KV cache, speculative decoding, SSMs, Mamba, RWKV, MoE, linear attention…); vLLM & Hugging Face blogs; Lil'Log; Ahead of AI; vLLM / Transformers / Mamba / FlashAttention / SGLang releases |

A GitHub Action runs `scripts/fetch_feeds.py` every 6 hours, writes `public/data/feed.json`, and deploys `public/` to GitHub Pages. The browser also re-polls the JSON every 5 minutes (toggle **Live**).

## How it works

- **Fetcher** (`scripts/fetch_feeds.py`): pulls arXiv API + RSS/Atom feeds, cleans HTML, classifies each item into a domain, scores it, deduplicates (canonical URL, arXiv ID without version, fuzzy title Jaccard ≥ 0.85), merges with the previous `feed.json` and prunes old items.
- **Score (0–100)** = 50 % keyword relevance (title matches weigh 2×) + 30 % recency (exponential decay, ~4-day constant) + 20 % source authority (+5 for releases). Keyword weights live in `KEYWORDS`; sources in `ARXIV_SOURCES` / `FEED_SOURCES`.
- **Frontend**: Tailwind (CDN) + vanilla JS. Search (supports `"phrases"` and `-exclude`), domain / type / time filters, sort, tag cloud, bookmarks and read-state (localStorage, with JSON export/import), "New" badges, dark/light theme, keyboard shortcuts (`/`, `r`, `t`).

## Local usage

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python scripts/fetch_feeds.py          # writes public/data/feed.json (takes ~30 s, arXiv rate-limit friendly)
python -m http.server 8000 --directory public
# open http://localhost:8000
```

Options: `--output PATH`, `--max-age-days 45`, `--max-items 600`.

The repo ships with **sample** data (`"sample": true`); the first real run replaces it, and the UI shows a banner while sample data is displayed.

## Deploying to GitHub Pages

1. Push this repo to GitHub (default branch `main`).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. **Actions → "Update feed and deploy to GitHub Pages" → Run workflow** (or just push).
4. The site appears at `https://<user>.github.io/<repo>/`.

If your branch rules block pushes by `github-actions[bot]`, the commit step is skipped harmlessly; the deployed site still has fresh data, but history between runs won't accumulate.

## Customising

- Add a feed: append a dict to `FEED_SOURCES` (`name`, `url`, `type`, `domain`, `weight`, `filter`, `min_rel`).
- Add an arXiv query: append to `ARXIV_SOURCES` (any [arXiv API](https://info.arxiv.org/help/api/user-manual.html) `search_query`).
- Tune relevance: edit weights in `KEYWORDS`.
- Change cron: edit the `schedule` in `.github/workflows/update_and_deploy.yml`.

## Notes

- Tailwind is loaded via the Play CDN (v3) for zero-build hosting; swap in a compiled stylesheet for stricter production use.
- A single failing source never breaks the run; per-source status is recorded in `meta.sources`.
