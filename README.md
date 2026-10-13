# PaperTrail

A weekly digest of new papers that match a lab's research interests. Every
Monday, PaperTrail fetches the past week's papers from **arXiv**, **bioRxiv /
medRxiv** and **PubMed**, ranks them by relevance to a set of keywords, and
publishes an interactive report you can search, filter and keep a reading
list in.

The current setup tracks tumor evolution, clonal evolution, chromosomal
instability / aneuploidy, genomic instability and computational pathology.
Everything is driven by [`config.yaml`](config.yaml), so it can follow any
topic.

## The report

[`papers.html`](papers.html) is always the latest week. Every week is also
archived as [`reports/YYYY-MM-DD.html`](reports/). Each report is a single
self-contained file, so you can open it straight from disk or serve it with
GitHub Pages.

- **Week navigation:** a dropdown, ‹ › buttons and the `[` / `]` keys
- **Search:** in titles, abstracts, authors and journals, for the current
  week or **all weeks** at once
- **Filters:** source, matched keyword, minimum score; sort by relevance,
  date or impact factor
- **Reading list:** star papers and mark them as read; Unread and Starred
  views; export starred papers as Markdown
- **Why a paper is there:** the keywords it matched, and its score

Stars and read marks are kept in your browser (`localStorage`). They carry
over between weeks but don't sync across browsers or people.

## How papers are selected

1. **Fetch** the papers of the window:
   - **bioRxiv / medRxiv:** every paper in the window
   - **PubMed:** one title/abstract search per keyword
   - **arXiv:** one abstract search over all keywords
2. **Filter journals:** PubMed papers must come from a journal on the
   whitelist in `config.yaml`; preprints always pass.
3. **De-duplicate** by title, ignoring case and punctuation, so a preprint
   and its journal version count once.
4. **Score** each paper with the
   [BioLORD-2023](https://huggingface.co/FremyCompany/BioLORD-2023) embedding
   model: its title and abstract are compared with each keyword's
   **description**, and the best match is the score.
5. **Gate:**
   - Papers that literally mention a core keyword need a score of at least
     `threshold` (0.40).
   - All other papers need `rescue_threshold` (0.60), so strongly on-topic
     papers phrased differently still get in.
   - Broad **context keywords** ("cancer", "genomics") help fetch candidates
     but can't get a paper in on their own.

This usually gives 20–35 papers a week.

**Keyword descriptions matter more than anything else.** A short term like
"tumor evolution" is embedded as a vague concept close to any tumor-biology
paper. A sentence like *"how cancer cell populations evolve over time,
reconstructed from phylogenetic trees of tumors"* is embedded as what you
actually mean.

## Running it locally

Requires Python 3.12.

```bash
pip install -r requirements.txt
python papertrail.py --output html          # writes papers.html and reports/<today>.html
```

The embedding model (about 420 MB) is downloaded on the first run.

| Option | Default | Meaning |
|---|---|---|
| `--days N` | 7 | Length of the window in days |
| `--until YYYY-MM-DD` | today | End of the window (exclusive), e.g. to re-run a past week |
| `--output` | `terminal` | `terminal`, `json`, `csv` or `html` |
| `--top N` | 15 | Maximum number of papers in the output |
| `--threshold X` | from config | Override `threshold` |
| `--sources a,b` | from config | Only these sources, e.g. `biorxiv,pubmed` |
| `--config PATH` | `config.yaml` | Configuration file |
| `--verbose` | off | Print score statistics |

Windows are whole days: `--until 2026-09-21 --days 7` covers Sep 14–20.
Consecutive weekly runs therefore neither overlap nor leave gaps, and past
weeks can be re-run exactly.

## Configuration

All settings are in [`config.yaml`](config.yaml):

| Key | Purpose |
|---|---|
| `keywords` | Core topics, each with a `term` (matched literally) and a `description` (used for scoring) |
| `context_keywords` | Broad terms used only to fetch candidates |
| `threshold`, `rescue_threshold` | Minimum score with and without a core-keyword match |
| `sources` | Any of `arxiv`, `biorxiv`, `pubmed` |
| `journal_whitelist` | Accepted journals and their impact factors (impact factors are shown on cards) |
| `whitelist_only`, `min_impact_factor` | How strictly PubMed journals are filtered |
| `journal_blacklist_fragments` | Journal-name fragments that are always excluded |
| `semantic_weight` | Mix of embedding score (1.0) and keyword counts (0.0) |
| `embedding_model` | Sentence-transformers model used for scoring |

## Automation

[`.github/workflows/weekly.yml`](.github/workflows/weekly.yml) runs every
Monday at 07:00 UTC. It fetches the previous 7 days and commits
`papers.html` and `reports/`. You can also run it by hand from the
**Actions** tab (*Run workflow*).

Every run re-renders all archived weeks, so older reports link to newer ones
and pick up changes to the page template.

## Repository layout

```
papertrail.py         fetching, journal filtering, scoring, CLI
write_html.py         report page template, weekly archive, search index
config.yaml           keywords, thresholds, sources, journal lists
papers.html           latest weekly report
reports/              one page per week + search-index.js for "All weeks"
.github/workflows/    weekly GitHub Action
```

## Known limitations

- **arXiv can refuse requests** (HTTP 406) from some networks. The pipeline
  retries with back-off, then skips arXiv for that run instead of failing.
  The archived weeks from Jul 20 to Sep 21, 2026 were re-run locally without
  arXiv for this reason.
- **Weeks recovered from older reports** (Jun 19 and Jun 26, 2026) have
  shortened author lists.
- **Some PubMed dates are journal issue dates** (e.g. `2026-Oct`), which
  can be later than the week the paper appeared.

## Web app (in development)

A multi-user version for the lab is being developed on a separate `webapp`
branch. It adds per-person 👍 / 👎 that adjust each person's ranking, a
shared "Lab picks" list, and a server-side archive.
