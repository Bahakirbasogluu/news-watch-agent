# newswatch

A news-watch agent. Every hour it scans the press for the companies on a watch-list and sends one deduplicated digest of what is new. When nothing is new it sends nothing.

The design comes from a system I built for a bank's relationship teams, who used to follow press coverage of their clients by hand. The code here is written from scratch and runs on invented companies and feeds; it contains no client code or data.

## How a run works

```
watch-list ──> sources ──> allow-list ──> name gate ──> dedup ──> match ──> classify ──> store
                              │              │            │         │                      │
                           dropped        dropped      dropped   rejected,              NEW rows
                                                                 remembered                │
                                                                                digest <───┘
                                                                         (only if there are NEW rows)
```

The steps run in a fixed order, cheapest first. The model is the last and most expensive step, and it is only asked about articles that name the company and have not been seen before.

## Design decisions

- **The model judges; it does not search or write.** It receives the title and snippet of an article that was already fetched and answers two bounded questions: is this about the company, and what kind of news is it. Titles, URLs, domains and dates never pass through it.
- **Evidence must be a quote, and code checks it.** A match has to come with the exact words that show it. If the quote is not in the article, the match is downgraded to "uncertain". Uncertain items are delivered with a flag, never dropped.
- **Identity is a database key.** A URL is canonicalised (tracking parameters, fragment, `www` and case removed, query sorted), hashed, and stored as `customer:hash` under a primary key with create-only inserts. Two runs that race cannot both store the same article.
- **Syndicated copies are caught by title.** A normalised title, without the publisher suffix aggregators append, is compared against the customer's titles of the last 14 days. Very short titles are not used as a signal.
- **Rejections are remembered.** An article the matcher rejects is stored with that status, so it is never sent to the model again.
- **The digest is built from the store, not from the run.** It mails every row still marked NEW and stamps them only after a successful send. A run that stored articles and then failed to send leaves them for the next run, which makes delivery at-least-once with no retry bookkeeping.
- **No news, no e-mail.** An empty digest is never sent. Critical items (sanctions or fraud at high severity) get a separate alert first.
- **Failures are contained.** A source that times out is recorded and the other sources still count; a customer that fails does not end the run; without its dedup sets a customer is skipped rather than risking duplicates. Each run has a row with its counters and errors.
- **Bounded work.** A cap on evaluations per run defers the rest, and the customer order rotates by the hour so the same customers are not always last.
- **Instructions inside articles are data.** Text that looks like a prompt injection is flagged for the reader, and the prompts tell the model not to follow it.

## Quick start

```bash
pip install -e ".[dev]"
newswatch demo
pytest
```

`newswatch demo` runs three consecutive hours over the sample feeds. Output, shortened:

```
08:15  run 1: succeeded  (fetched=15, evaluated=7, new=7, duplicate=2, not_an_article=1, negative_keyword=3, no_name=2)

  E-MAIL  Critical alert: 1 item(s)
  | Northwind Supplies
  | ------------------
  | [critical] fraud: Northwind Supplies faces fraud probe over invoicing - Daily Ledger
  |   dailyledger.example  https://dailyledger.example/business/northwind-fraud-probe

  E-MAIL  News digest: 7 new item(s)
  | ...
  | Atlas Lojistik
  | --------------
  | [low] operations: Atlas wins regional distribution contract [to verify] [check content]
  |   tradepress.example  https://tradepress.example/logistics/atlas-contract
  | [medium] regulatory: Atlas Lojistik'e rekabet soruşturması açıldı
  |   ekonomigunlugu.example  https://ekonomigunlugu.example/atlas-lojistik-sorusturma

09:15  run 2: succeeded  (fetched=14, duplicate=9, no_name=1, negative_keyword=2, evaluated=2, new=2)

  E-MAIL  News digest: 2 new item(s)
  | ...

10:15  run 3: succeeded  (fetched=14, duplicate=11, negative_keyword=2, no_name=1)
  nothing new: no e-mail sent
```

What the first hour shows: one fraud story arrives three times (the same link with tracking parameters and a syndicated copy on another domain) and is stored once; a share-price page, a different company with a similar name and two articles that do not name the customer are dropped before any judgement; an article that only uses the short alias is delivered as "to verify"; and an article carrying an instruction aimed at the model is delivered with a "check content" flag.

To run against Google News RSS for your own watch-list:

```bash
newswatch --watchlist my-watchlist.json --db watch.db run
```

## Using a model

The default backend is deterministic: a name matcher and a keyword classifier. With an Azure OpenAI deployment the same two steps are done by a model with structured outputs:

```bash
pip install -e ".[azure]"
cp .env.example .env   # fill in endpoint, key and deployment, then export them
newswatch --backend azure demo
```

Any model that can fill a Pydantic schema works: implement `parse(system, user, schema)` from `analysis.py`.

## As a tool for other agents

`bridge.py` exposes the engine as an MCP server, so a low-code agent or a scheduled flow can orchestrate it without owning any of the logic:

| Tool | What it does |
| --- | --- |
| `run_watchlist` | New items for the whole watch-list that this consumer has not confirmed, with a ready digest |
| `confirm_delivery` | Marks a run as delivered, so its items are not returned again |
| `search_news` | Gated, deduplicated news for one company with the quoted evidence, no model |
| `compute_dedup_key` | The identity of an article for a customer |

The caller's whole job is: list the watch-list, call `run_watchlist` once, deliver the digest if there are items, call `confirm_delivery`. A run that is never confirmed resurfaces on the next call, and delivery history is kept per consumer, so two flows can share one engine. Deduplication and evidence stay on the engine's side, where a prompt cannot skip them.

## Tests

38 tests with hand-written fakes for the source, matcher, classifier and mailer. They cover:

- link variants of one article sharing a key
- the gate's drops and its evidence
- the downgrade of a match without a real quote
- deduplication across runs and by title
- the evaluation cap and deferral
- a failing source and a failing store
- recovery of a digest after a failed send
- the bridge's pending and confirm cycle, in process and over the MCP protocol
- the three-hour sample scenario above

## Layout

```
src/newswatch/
  models.py       Customer, Candidate, Item, RunReport
  sources.py      source interface, RSS adapter, Google News query builder
  canonical.py    URL canonicalisation, dedup key, title normalisation
  gate.py         allow-list, article page, name evidence, negative keywords, injection flag
  analysis.py     matcher and classifier: deterministic and model-based
  storage.py      SQLite store: items, runs, delivery history
  pipeline.py     one run of the watch-list
  digest.py       digest from NEW rows, stamped after send
  bridge.py       MCP server over the same engine
  cli.py          newswatch demo / newswatch run
samples/          watch-list and two hours of feeds
tests/
```

## License

MIT
