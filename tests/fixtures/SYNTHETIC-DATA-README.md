# Synthetic fixtures - NOT real evidence

`synthetic-sentiment-gold-300.jsonl` and `synthetic-sentiment-predictions-300.jsonl` are machine-generated
test data: organisation numbers 900000000+, "Test Company N" names, invented article URLs, and labels that
repeat positive/negative/neutral/mixed in a fixed cycle. They exist only to exercise code paths.

They must never be used as competition evidence, never be merged into observations, and any report built from
them (accuracy 1.0, qualification_passed true) says nothing about real-world sentiment quality.

`sentiment-smoke-*.jsonl` are the original 4-item smoke fixtures used by the unit tests.
Real sentiment qualification needs >= 300 human-labelled, exact-company, independently sourced items.
