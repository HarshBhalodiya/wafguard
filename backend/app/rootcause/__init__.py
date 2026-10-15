"""
Root-cause evidence-graph package (brief PHASE 3C).

Builds a node/edge view of *why* a lot looked risky: equipment/chamber →
sensor issue → mechanism → defect pattern → yield loss, with every edge
carrying a strength and a link to the evidence that justifies it.

The causal links are NOT hardcoded in Python — they live in
`graph_rules.json` next to this module so an engineer can read and change
them (brief: "store rules in a file so an engineer can read them").

This package never reads `data/synthetic/ground_truth.parquet`: the graph is
built from the root-cause engine's measured evidence plus those rules.
"""
