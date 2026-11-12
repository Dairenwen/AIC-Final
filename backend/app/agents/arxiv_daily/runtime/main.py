from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parent / "agent-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from src.tools.arxivdaily import ArxivDailyScraper  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a normalized arXivDaily category snapshot")
    parser.add_argument("--category", default="cs.AI")
    args = parser.parse_args()

    scraper = ArxivDailyScraper()
    try:
        categories = [item.to_dict() for item in scraper.fetch_categories()]
        valid_codes = {str(item.get("code")) for item in categories}
        if args.category not in valid_codes or args.category == "cs":
            raise ValueError(f"Unknown arXivDaily CS category: {args.category}")
        papers = [item.to_dict() for item in scraper.fetch_papers(args.category)]
    finally:
        scraper.close()

    print(
        json.dumps(
            {
                "source": "https://www.arxivdaily.com/",
                "category": args.category,
                "categories": categories,
                "papers": papers,
                "fetched_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
