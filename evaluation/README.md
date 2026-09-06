# Synthetic retrieval evaluation

The sealed dataset contains nine original queries about the files in
`../examples/mini-project`: two development examples and seven evaluation examples.
It exercises Python and TypeScript symbols plus a Go text-window fallback.
It contains no production excerpts, personal paths or external project snapshots.

Run `python evaluate.py --lexical-only` for a lightweight smoke test, or
`python evaluate.py` after optional embedding setup. The default source directory
is resolved relative to the dataset file. Use `--dataset` and `--repo` for another
dataset/source pair. TS exact-symbol labels require the optional parser; without
it those sources remain text-searchable but cannot satisfy symbol labels.

Reports go to the per-user repository cache by default. Source excerpts are omitted
unless explicitly requested with `--include-previews`. Keep private datasets and
reports out of version control. The source hashes and dataset seal detect accidental
changes; new fixtures or labels should receive a new dataset revision.

This tiny, authored set validates retrieval mechanics. Its hit rates do not measure
quality on real-world projects, and it is not an unseen external benchmark.
