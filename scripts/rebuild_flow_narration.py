#!/usr/bin/env python3
"""One-shot: pace + merge narration on a saved flow, write a new site-graph revision.

Run on the box that owns registry.db (do not rsync sqlite).

  .venv/bin/python scripts/rebuild_flow_narration.py \\
      --product-id <id> --flow-id <id> --publish
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--flow-id", required=True)
    parser.add_argument(
        "--db",
        default=os.environ.get("NAVIGATOR_REGISTRY_DB", "registry.db"),
    )
    parser.add_argument(
        "--source",
        choices=("published", "latest"),
        default="published",
        help="Which revision to rebuild (default: published / live).",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Activate the rebuilt revision for live demos.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip Groq merge; only split monologues and fill empties.",
    )
    args = parser.parse_args()

    from navigator.app.registry import Registry
    from navigator.automation.explore.runner import groq_asker
    from navigator.client.content import rebuild_yaml_narration
    from navigator.core.groq_keys import groq_key_candidates
    from navigator.knowledge.site_graph import parse_site_graph

    registry = Registry(args.db)
    if args.source == "latest":
        rev = registry.latest_revision(args.product_id)
    else:
        published = registry.published_revision(args.product_id)
        rev = registry.get_revision(args.product_id, published)

    latest = registry.latest_revision(args.product_id)
    if args.publish and latest.revision != rev.revision:
        print(
            f"[warn] latest rev {latest.revision} != source rev {rev.revision}; "
            "publish will make the rebuild the new latest",
            flush=True,
        )

    ask_text = None
    if not args.no_llm:
        keys = groq_key_candidates()
        if keys:
            ask_text = groq_asker(keys[0])
        else:
            print("[warn] no Groq keys — split/fill only, no LLM merge", flush=True)

    graph = parse_site_graph(rev.yaml)
    persona = graph.effective_persona()
    new_yaml = rebuild_yaml_narration(
        rev.yaml,
        flow_id=args.flow_id,
        ask_text=ask_text,
        product_name=persona.product_name,
    )
    new_graph = parse_site_graph(new_yaml)
    lines = new_graph.flow_narration_lines(args.flow_id)
    narrated = sum(1 for l in lines if str(l).strip())
    print(
        f"[rebuild] {args.flow_id}: {narrated}/{len(lines)} spoken lines "
        f"(from rev {rev.revision})",
        flush=True,
    )
    for i, line in enumerate(lines):
        words = len(str(line).split())
        preview = " ".join(str(line).split()[:12])
        print(f"  [{i}] {words}w  {preview}", flush=True)

    saved = registry.put_site_graph(
        args.product_id, new_yaml, "recorded", publish=bool(args.publish)
    )
    print(
        f"[rebuild] wrote rev {saved.revision} publish={saved.published}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
