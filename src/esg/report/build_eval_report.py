"""Generate a combined HTML evaluation report comparing extraction methods.

Usage:
    uv run python -m src.esg.report.build_eval_report \\
        --di  output/esg/document_intelligence/<run>/eval_results.json \\
        --raw output/esg/raw/<run>/eval_results.json \\
        --custom output/esg/custom/<run>/eval_results.json \\
        -o output/esg/report.html

At least one method must be provided.  The report is written to the path given
by ``-o`` (defaults to ``report.html`` in the current directory).
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

_REPORT_DIR = Path(__file__).resolve().parent

# Keys to keep from each row
ROW_KEYS = {
    "inputs.expected",
    "inputs.actual",
    "inputs.scenario",
    "inputs.document",
    "outputs.hit_rate.hit_rate",
}


def _strip_row(row: dict) -> dict:
    return {k: v for k, v in row.items() if k in ROW_KEYS}


def _load_eval(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        "rows": [_strip_row(r) for r in data["rows"]],
        "metrics": data["metrics"],
        "run": path.parent.name,
    }


def _replace_placeholder(html: str, name: str, value: str) -> str:
    """Replace {{NAME}} or {{ NAME }} (tolerant of formatter-added spaces).

    Uses a lambda replacement to avoid ``re.sub`` interpreting backslash
    escape sequences (e.g. ``\\n``) in the *value* string.
    """
    pattern = r"\{\{\s*" + re.escape(name) + r"\s*\}\}"
    return re.sub(pattern, lambda _m: value, html)


def build_report(
    *,
    di_path: Path | None = None,
    raw_path: Path | None = None,
    custom_path: Path | None = None,
    output_path: Path,
) -> Path:
    methods: dict[str, dict] = {}
    if di_path:
        methods["Document Intelligence"] = _load_eval(di_path)
    if custom_path:
        methods["Custom"] = _load_eval(custom_path)
    if raw_path:
        methods["Raw"] = _load_eval(raw_path)

    if not methods:
        raise SystemExit("At least one of --di, --raw, or --custom must be provided.")

    combined = {"methods": methods}

    template = (_REPORT_DIR / "template.html").read_text(encoding="utf-8")
    css = (_REPORT_DIR / "style.css").read_text(encoding="utf-8")

    html = template
    html = _replace_placeholder(html, "CSS", css)
    html = _replace_placeholder(html, "DATA_JSON", json.dumps(combined, ensure_ascii=False))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a combined HTML evaluation report",
    )
    parser.add_argument("--di", type=Path, default=None, help="Document Intelligence eval_results.json")
    parser.add_argument("--raw", type=Path, default=None, help="Raw extraction eval_results.json")
    parser.add_argument("--custom", type=Path, default=None, help="Custom extraction eval_results.json")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output HTML path")
    args = parser.parse_args()

    if args.output is None:
        timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
        args.output = Path(f"{timestamp}_report.html")

    for name, path in [("--di", args.di), ("--raw", args.raw), ("--custom", args.custom)]:
        if path and not path.resolve().exists():
            raise SystemExit(f"{name} file not found: {path}")

    out = build_report(
        di_path=args.di.resolve() if args.di else None,
        raw_path=args.raw.resolve() if args.raw else None,
        custom_path=args.custom.resolve() if args.custom else None,
        output_path=args.output.resolve(),
    )
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
