#!/usr/bin/env python3
"""
PaperTrail — fetch and rank recent papers by semantic relevance.

Usage:
    python papertrail.py                  # uses config.yaml defaults
    python papertrail.py --days 14        # look back 14 days
    python papertrail.py --top 20         # show top 20 results
    python papertrail.py --output json    # dump to papers.json
    python papertrail.py --output csv     # dump to papers.csv
    python papertrail.py --output html    # generate papers.html report
"""

import argparse, json, csv, sys, warnings, re, time
from datetime import datetime, timedelta, timezone
from math import log

import yaml
import numpy as np
import requests
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.panel import Panel
from rich import box

warnings.filterwarnings("ignore")
console = Console()

# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)

# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_arxiv(keywords, since: datetime) -> list[dict]:
    """Query arXiv across all categories."""
    query = " OR ".join(f'abs:"{k}"' for k in keywords)
    params = {
        "search_query": query,
        "start": 0,
        "max_results": 100,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    r = requests.get("https://export.arxiv.org/api/query", params=params, timeout=20)
    r.raise_for_status()

    import xml.etree.ElementTree as ET
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    papers = []
    for entry in root.findall("atom:entry", ns):
        published_str = entry.find("atom:published", ns).text
        published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        if published < since:
            continue
        papers.append({
            "id":       entry.find("atom:id", ns).text.strip(),
            "title":    entry.find("atom:title", ns).text.strip().replace("\n", " "),
            "abstract": entry.find("atom:summary", ns).text.strip().replace("\n", " "),
            "authors":  ", ".join(
                a.find("atom:name", ns).text
                for a in entry.findall("atom:author", ns)
            ),
            "date":     published.strftime("%Y-%m-%d"),
            "source":   "arXiv",
            "journal":  "preprint",
            "if":       None,
        })
    return papers


def fetch_biorxiv(keywords, since: datetime) -> tuple[list[dict], int | None]:
    """Query bioRxiv and medRxiv, paginating via total count in API response."""
    since_str = since.strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y-%m-%d")
    papers = []
    grand_total = None
    for server in ("biorxiv", "medrxiv"):
        cursor = 0
        total = None
        while True:
            url = f"https://api.biorxiv.org/details/{server}/{since_str}/{today_str}/{cursor}/json"
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                data = r.json()
            except requests.exceptions.RequestException as e:
                console.print(f"  [yellow]Warning: {server} failed at cursor {cursor}: {e}[/]")
                break
            except ValueError as e:
                console.print(f"  [yellow]Warning: {server} invalid JSON at cursor {cursor}: {e}[/]")
                break

            messages = data.get("messages", [])
            if messages:
                msg = messages[0]
                if msg.get("status") not in ("ok", "ok ", ""):
                    console.print(f"  [yellow]Warning: {server} API error: {messages}[/]")
                    break
                if total is None:
                    # API returns "count" (this page) and "total" (all pages)
                    # They may both be ints or strings
                    for key in ("total", "count"):
                        val = msg.get(key)
                        if val is not None:
                            try:
                                candidate = int(val)
                                # Only trust if larger than a single page (>60)
                                # to avoid mistaking page count for total
                                if candidate > 60 or key == "total":
                                    total = candidate
                                    break
                            except (ValueError, TypeError):
                                pass

            batch = data.get("collection", [])
            if not batch:
                break

            for p in batch:
                papers.append({
                    "id":       f"https://doi.org/{p['doi']}",
                    "title":    p["title"].strip(),
                    "abstract": p["abstract"].strip().replace("\n", " "),
                    "authors":  p.get("authors", ""),
                    "date":     p["date"],
                    "source":   server,
                    "journal":  "preprint",
                    "if":       None,
                })

            cursor += len(batch)
            if total is not None and cursor >= total:
                break
            if cursor >= 2000:
                console.print(f"  [yellow]Warning: {server} hit 2000-paper cap[/]")
                break
            if total is None and len(batch) < 30:
                # No total and very short page → genuinely at end
                break

        if grand_total is None and total is not None:
            grand_total = total
        elif total is not None:
            grand_total = (grand_total or 0) + total

    return papers, grand_total


def fetch_pubmed(keywords, since: datetime, min_if: float, whitelist_only: bool,
                 whitelist: set[str], blacklist_fragments: list[str],
                 if_lookup: dict[str, float],
                 anchor_keywords: list[str] | None = None) -> list[dict]:
    """Query PubMed via NCBI E-utilities.
    Runs one esearch per keyword and deduplicates PMIDs, avoiding broad terms
    from flooding the results when mixed with specific ones.
    """
    days_back = (datetime.now(timezone.utc) - since).days + 1

    # Collect PMIDs per keyword separately, then deduplicate.
    # This ensures "scDNA-seq" (rare, specific) isn't drowned by "cancer" (broad).
    all_pmids: dict[str, None] = {}
    for kw in keywords:
        query = f'"{kw}"[tiab] AND hasabstract'
        base_params = {
            "db":       "pubmed",
            "term":     query,
            "reldate":  days_back,
            "datetype": "edat",
            "retmax":   500,
            "retmode":  "json",
            "sort":     "date",
        }
        try:
            # First call — get total count
            time.sleep(0.34)
            r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                             params={**base_params, "retstart": 0}, timeout=20)
            r.raise_for_status()
            result = r.json()["esearchresult"]
            count  = int(result.get("count", 0))
            console.print(f"    [dim]{kw!r}: {count} hits[/]")
            for p in result["idlist"]:
                all_pmids[p] = None

            # Paginate if more results exist
            retstart = 500
            while retstart < count:
                time.sleep(0.34)
                r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                                 params={**base_params, "retstart": retstart}, timeout=20)
                r.raise_for_status()
                batch = r.json()["esearchresult"]["idlist"]
                if not batch:
                    break
                for p in batch:
                    all_pmids[p] = None
                retstart += 500

        except Exception as e:
            console.print(f"  [yellow]Warning: PubMed query failed for '{kw}': {e}[/]")

    if not all_pmids:
        return []

    # Step 2: efetch — get full records in batches of 100
    papers = []
    pmid_list = list(all_pmids.keys())
    for i in range(0, len(pmid_list), 100):
        batch_ids = ",".join(pmid_list[i:i+100])
        fetch_params = {
            "db":      "pubmed",
            "id":      batch_ids,
            "retmode": "xml",
            "rettype": "abstract",
        }
        try:
            time.sleep(0.34)  # NCBI rate limit: max 3 req/sec without API key
            r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                             params=fetch_params, timeout=30)
            r.raise_for_status()
        except Exception as e:
            console.print(f"  [yellow]Warning: PubMed fetch failed for batch {i}: {e}[/]")
            continue

        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            continue

        for article in root.findall(".//PubmedArticle"):
            try:
                # Title
                title_el = article.find(".//ArticleTitle")
                title = "".join(title_el.itertext()).strip() if title_el is not None else ""

                # Abstract
                abstract_els = article.findall(".//AbstractText")
                abstract = " ".join("".join(el.itertext()) for el in abstract_els).strip()
                if not abstract:
                    continue

                # Journal
                journal_el = article.find(".//Journal/Title")
                journal = journal_el.text.strip() if journal_el is not None else ""

                # IF / whitelist filter
                if not journal_passes(journal, "pubmed", whitelist, blacklist_fragments,
                                      whitelist_only, min_if, if_lookup):
                    continue

                known_if = get_if(journal, if_lookup)

                # Authors
                authors = ", ".join(
                    f"{a.findtext('LastName', '')} {a.findtext('Initials', '')}".strip()
                    for a in article.findall(".//Author")
                    if a.findtext("LastName")
                )

                # Date
                pub_date = article.find(".//PubDate")
                year  = pub_date.findtext("Year", "")  if pub_date is not None else ""
                month = pub_date.findtext("Month", "") if pub_date is not None else ""
                day   = pub_date.findtext("Day", "")   if pub_date is not None else ""
                date_str = "-".join(filter(None, [year, month.zfill(2) if month.isdigit() else month, day.zfill(2) if day.isdigit() else day]))

                # DOI
                doi_el = article.find(".//ArticleId[@IdType='doi']")
                doi = doi_el.text.strip() if doi_el is not None else ""
                pmid_el = article.find(".//ArticleId[@IdType='pubmed']")
                pmid = pmid_el.text.strip() if pmid_el is not None else ""
                url = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

                papers.append({
                    "id":       url,
                    "title":    title,
                    "abstract": abstract.replace("\n", " "),
                    "authors":  authors,
                    "date":     date_str,
                    "source":   "PubMed",
                    "journal":  journal,
                    "if":       known_if,
                })
            except Exception:
                continue

    console.print(f"    [dim]→ {len(pmid_list)} unique PMIDs, {len(papers)} passed journal filter[/]")
    return papers


# ── Journal filtering ─────────────────────────────────────────────────────────

PREPRINT_SOURCES = {"biorxiv", "medrxiv", "arxiv"}


def journal_passes(journal: str, source: str,
                   whitelist: set[str], blacklist_fragments: list[str],
                   whitelist_only: bool, min_if: float,
                   if_lookup: dict[str, float]) -> bool:
    """Return True if the paper should be included based on journal filters."""
    if source.lower() in PREPRINT_SOURCES or journal.lower() == "preprint":
        return True

    jl = journal.lower().strip()
    # Strip leading "the " so "The Journal of X" matches "journal of x"
    if jl.startswith("the "):
        jl = jl[4:]

    # Blacklist fragments take priority — always exclude
    if any(frag in jl for frag in blacklist_fragments):
        return False

    if whitelist_only:
        if jl not in whitelist:
            return False
        # Also apply IF filter if set — journal must be in whitelist AND meet IF
        if min_if > 0:
            known_if = if_lookup.get(jl)
            if known_if is not None and known_if < min_if:
                return False
        return True

    # Legacy IF mode: unknown journals pass, known low-IF ones are filtered
    known_if = if_lookup.get(jl)
    if known_if is None:
        return True
    return known_if >= min_if


def get_if(journal: str, if_lookup: dict[str, float]) -> float | None:
    if not journal:
        return None
    return if_lookup.get(journal.lower().strip())




# ── Relevance scoring ─────────────────────────────────────────────────────────
#
# Hybrid score using per-keyword MAX (not centroid/mean):
#
#   sem(p)   = max_i ( k_i · p )          k_i, p are unit-normalised embeddings
#   lex(p)   = max_i ( BM25_norm(k_i, p) )
#   score(p) = α · sem(p) + (1-α) · lex(p)
#
# Taking max makes scoring monotone: adding a keyword can only raise scores.
# BM25: IDF(k) · tf·(k1+1) / (tf + k1·(1 - b + b·|p|/avgdl))
#       k1=1.5, b=0.75 (standard)

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def score_papers(papers: list[dict], keywords: list[str],
                 model_name: str, threshold: float, alpha: float = 0.3) -> list[dict]:
    """Score papers using per-keyword max similarity (semantic + BM25).

    sem(p)   = max_i ( k_i · p )           cosine, unit-normalised embeddings
    lex(p)   = max_i ( BM25_norm(k_i, p) )
    score(p) = α · sem(p) + (1-α) · lex(p)

    Hard gate: lex(p) must be > 0, i.e. at least one keyword token must
    literally appear in the text. Eliminates semantic false positives.
    """
    with console.status(f"[cyan]Loading embedding model ({model_name})…"):
        model = SentenceTransformer(model_name)

    kw_embeddings = model.encode(keywords, normalize_embeddings=True)
    texts = [f"{p['title']}. {p['abstract']}" for p in papers]

    with console.status(f"[cyan]Scoring {len(texts)} papers…"):
        paper_embeddings = model.encode(texts, normalize_embeddings=True,
                                        batch_size=32, show_progress_bar=False)

        # Semantic: max cosine similarity across all keywords
        sim_matrix = paper_embeddings @ kw_embeddings.T
        sem_scores = sim_matrix.max(axis=1)

        # BM25: per keyword, normalised independently, then take max
        tokenized = [_tokenize(t) for t in texts]
        N         = len(tokenized)
        avgdl     = sum(len(d) for d in tokenized) / max(N, 1)
        k1, b     = 1.5, 0.75
        bm_matrix = np.zeros((N, len(keywords)))
        for j, kw in enumerate(keywords):
            kw_tokens = _tokenize(kw)
            if not kw_tokens:
                continue
            df  = sum(1 for doc in tokenized if all(t in doc for t in kw_tokens))
            idf = log((N - df + 0.5) / (df + 0.5) + 1)
            for i, doc in enumerate(tokenized):
                tf = min(doc.count(t) for t in kw_tokens)
                dl = len(doc)
                bm_matrix[i, j] = idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
            mx = bm_matrix[:, j].max()
            if mx > 0:
                bm_matrix[:, j] /= mx
        lex_scores = bm_matrix.max(axis=1)

    scored = []
    for i, paper in enumerate(papers):
        sem = float(sem_scores[i])
        lex = float(lex_scores[i])
        if lex == 0:          # hard gate: keyword must appear literally
            continue
        combined = alpha * sem + (1 - alpha) * lex
        if combined >= threshold:
            scored.append({**paper, "score": round(combined, 4),
                           "score_semantic": round(sem, 4),
                           "score_lexical":  round(lex, 4)})

    return sorted(scored, key=lambda x: x["score"], reverse=True)


# ── Output ────────────────────────────────────────────────────────────────────

def print_feed(papers: list[dict], top_n: int):
    console.print(Panel.fit(
        f"[bold cyan]PaperTrail[/] — [white]{min(top_n, len(papers))} of {len(papers)} relevant papers[/]",
        box=box.DOUBLE_EDGE
    ))
    for i, p in enumerate(papers[:top_n], 1):
        score_color = "green" if p["score"] > 0.5 else "yellow" if p["score"] > 0.35 else "red"
        journal_str = p["journal"] if p["journal"] else "unknown journal"
        if_val = p.get("if")
        if_str = f" · IF {if_val:.1f}" if if_val is not None else ""
        sem = p.get("score_semantic"); lex = p.get("score_lexical")
        sub = f" [dim](sem={sem:.2f} lex={lex:.2f})[/dim]" if sem is not None else ""
        console.print(f"\n[bold]{i}. {p['title']}[/bold]")
        console.print(f"   [dim]{p['source']} · {journal_str}{if_str} · {p['date']}[/dim]")
        console.print(f"   [dim]Authors: {p['authors'][:80]}{'…' if len(p['authors']) > 80 else ''}[/dim]")
        console.print(f"   Relevance: [{score_color}]{p['score']:.2f}[/]{sub}")
        console.print(f"   [italic]{p['abstract'][:300]}{'…' if len(p['abstract']) > 300 else ''}[/italic]")
        console.print(f"   [blue underline]{p['id']}[/]")


def write_json(papers: list[dict], top_n: int, path="papers.json"):
    with open(path, "w") as f:
        json.dump(papers[:top_n], f, indent=2)
    console.print(f"[green]Saved {min(top_n, len(papers))} papers to {path}[/]")


def write_csv(papers: list[dict], top_n: int, path="papers.csv"):
    fields = ["title", "authors", "date", "source", "journal", "if", "score", "abstract", "id"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(papers[:top_n])
    console.print(f"[green]Saved {min(top_n, len(papers))} papers to {path}[/]")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch and rank recent papers.")
    parser.add_argument("--days",      type=int,   default=7)
    parser.add_argument("--top",       type=int,   default=15)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--output",    choices=["terminal", "json", "csv", "html"], default="terminal")
    parser.add_argument("--config",    default="config.yaml")
    parser.add_argument("--verbose",   action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)

    raw_kw = cfg["keywords"]
    if raw_kw and isinstance(raw_kw[0], dict):
        keywords = [k["term"] for k in raw_kw]
        weights  = [float(k.get("weight", 1.0)) for k in raw_kw]
    else:
        keywords = [str(k) for k in raw_kw]
        weights  = [1.0] * len(keywords)

    threshold           = args.threshold if args.threshold is not None else cfg.get("threshold", 0.30)
    sources             = cfg.get("sources", ["arxiv", "biorxiv", "pubmed"])
    min_if              = cfg.get("min_impact_factor", 5.0)
    whitelist_only      = cfg.get("whitelist_only", True)
    model               = cfg.get("embedding_model", "FremyCompany/BioLORD-2023")
    alpha               = cfg.get("semantic_weight", 0.3)

    # Load journal lists from config — all lowercase for matching
    raw_whitelist       = cfg.get("journal_whitelist", {})
    blacklist_fragments = [f.lower() for f in cfg.get("journal_blacklist_fragments", [])]
    # whitelist is a set of names; if_lookup maps name → IF for display
    whitelist  = {j.lower().strip() for j in raw_whitelist}
    if_lookup  = {j.lower().strip(): float(v) for j, v in raw_whitelist.items()
                  if isinstance(v, (int, float))}
    anchor_keywords = cfg.get("anchor_keywords", None)

    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    all_uniform = all(w == 1.0 for w in weights)
    kw_display = ", ".join(
        k if all_uniform else f"{k}({w:.1f})"
        for k, w in zip(keywords, weights)
    )
    console.print(f"[bold]Keywords:[/] {kw_display}")
    console.print(f"[bold]Searching:[/] past {args.days} days across {', '.join(sources)}")
    console.print(f"[bold]Threshold:[/] {threshold}\n")

    all_papers = []
    if "arxiv" in sources:
        with console.status("[cyan]Fetching arXiv…"):
            fetched = fetch_arxiv(keywords, since)
        all_papers += fetched
        console.print(f"  arXiv:   [green]{len(fetched)} papers[/]")

    if "biorxiv" in sources:
        with console.status("[cyan]Fetching bioRxiv / medRxiv…"):
            fetched, biorxiv_total = fetch_biorxiv(keywords, since)
        all_papers += fetched
        total_str = f" of {biorxiv_total} total" if biorxiv_total else ""
        console.print(f"  bioRxiv: [green]{len(fetched)}{total_str} papers[/]")

    if "pubmed" in sources:
        with console.status("[cyan]Fetching PubMed…"):
            fetched = fetch_pubmed(keywords, since, min_if, whitelist_only,
                                   whitelist, blacklist_fragments, if_lookup,
                                   anchor_keywords)
        all_papers += fetched
        console.print(f"  PubMed:  [green]{len(fetched)} papers[/]")

    # Deduplicate by normalised title
    seen, unique = set(), []
    for p in all_papers:
        key = re.sub(r"\s+", " ", p["title"].lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(p)

    console.print(f"\n[bold]Total unique papers:[/] {len(unique)} — scoring relevance…\n")

    if not unique:
        console.print("[yellow]No papers found. Try increasing --days or broadening keywords.[/]")
        sys.exit(0)

    ranked = score_papers(unique, keywords, model, threshold, alpha)
    console.print(f"[bold]Above threshold ({threshold}):[/] {len(ranked)} papers\n")

    if args.verbose and ranked:
        scores = [p["score"] for p in ranked]
        console.print(f"  Score range: {min(scores):.3f} – {max(scores):.3f}, mean: {sum(scores)/len(scores):.3f}")
        console.print(f"  α={alpha} (semantic weight), 1-α={1-alpha:.1f} (lexical weight)\n")

    if args.output == "terminal":
        print_feed(ranked, args.top)
    elif args.output == "json":
        write_json(ranked, args.top)
    elif args.output == "csv":
        write_csv(ranked, args.top)
    elif args.output == "html":
        from write_html import write_html
        write_html(ranked, args.top, keywords, args.days, sources)
        console.print(f"[green]Saved {min(args.top, len(ranked))} papers to papers.html[/]")


if __name__ == "__main__":
    main()