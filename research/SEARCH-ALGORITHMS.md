# Algorithms for local source retrieval

This note describes general design choices. It includes no private source,
project-specific benchmark or measured performance claim.

## Separate representation, retrieval and ranking

Parsing determines what a source record represents. Retrieval finds candidate
records. Ranking orders them. Freshness maintenance keeps their locations current.
A faster index does not fix incorrect declaration boundaries or irrelevant results.

Store a relative source path, qualified symbol when available, declaration kind,
source span, content fingerprint and parser identity. Split large declarations into
bounded sections while retaining their parent identity. For languages without an
adapter, label records as text windows rather than claiming parsed symbols.

Python's built-in AST and the optional TypeScript compiler provide structured
extraction. Tree-sitter is another option for expanding language coverage.
[Python AST](https://docs.python.org/3/library/ast.html),
[TypeScript Compiler API](https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API),
[Tree-sitter](https://github.com/tree-sitter/tree-sitter).

## Lexical retrieval

Exact-symbol lookup should preserve original identifiers. Natural-language search
can additionally split camelCase and snake_case into words. SQLite FTS5 supplies
an inverted index, BM25 ranking and field weighting. Separating paths, symbols and
body text allows controlled experiments with their relevance.
[SQLite FTS5](https://www.sqlite.org/fts5.html).

Ngram indexes are useful for substring and regular-expression candidate filtering.
They address literal matching rather than semantic intent.
[GitHub code-search engineering](https://github.blog/engineering/the-technology-behind-githubs-new-code-search/),
[Russ Cox on trigram indexing](https://swtch.com/~rsc/regexp/regexp4.html).

## Semantic and hybrid retrieval

A bi-encoder embeds code and queries independently. Cached code vectors can be
compared with each query vector. A cross-encoder instead scores query/candidate
pairs together, usually after an initial shortlist.
[Sentence Transformers](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html).

Reciprocal Rank Fusion combines positions in several ranked lists. It is a simple
hybrid baseline, not a universal optimum. Normalized weighted score combinations
are another choice to compare on held-out labels.
[Original RRF paper](https://cormack.uwaterloo.ca/cormack/cormacksigir09-rrf.pdf),
[Hybrid fusion analysis](https://arxiv.org/abs/2210.11934).

The optional local encoder in this project is a pinned CodeRankEmbed revision.
Its query prefix and document treatment follow its model card. Selection of this
model does not establish that it is best for every language or repository.
[CodeRankEmbed model card](https://huggingface.co/nomic-ai/CodeRankEmbed).

## Vector indexes and context

Exact cosine or dot-product scanning is an accuracy baseline for vector retrieval.
HNSW trades memory and computation against approximate-neighbor recall; benchmark
it when vector-search cost becomes material.
[Faiss index guidance](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index),
[HNSW paper](https://arxiv.org/abs/1603.09320).

SPLADE supplies learned sparse expansion. ColBERT-style late interaction retains
multiple token representations. Both are additional model families to evaluate
rather than prerequisites for a small local tool.
[SPLADE](https://arxiv.org/abs/2107.05720),
[ColBERTv2](https://arxiv.org/abs/2112.01488).

Overlapping sections should not crowd out distinct evidence. Reference expansion
and diversity-aware selection can be tested under a fixed context budget.
[MMR](https://www.cs.cmu.edu/afs/cs/Web/People/jgc/publication/MMR_DiversityBased_Reranking_SIGIR_1998.pdf).

## Freshness and evaluation

Content-addressed caches should include model/extractor configuration. A file
watcher can identify dirty paths, but recovery scans remain necessary when events
are lost. Bloom filters can reject impossible storage lookups; they do not rank
source relevance.
[Watchman recovery](https://facebook.github.io/watchman/docs/troubleshooting),
[RocksDB Bloom filters](https://github.com/facebook/rocksdb/wiki/RocksDB-Bloom-Filter).

Freeze queries, source snapshots and acceptable target spans before tuning. Compare
retrievers on identical source records, measure function hits separately from
visible evidence coverage, and distinguish initial indexing, warm retrieval and
fresh-process latency. Count complete agent investigations before claiming token
savings. Synthetic smoke tests demonstrate mechanics; independent, appropriately
licensed datasets are needed for quality claims.
