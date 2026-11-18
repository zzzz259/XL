"""Check the isolated test Nginx GET paths, response counts and body bytes."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

COMBINED = re.compile(r'"([A-Z]+) (\S+) HTTP/[^"]+" (\d{3}) (\d+)(?: |$)')


def load_requests(log, prefix, first_line=1, last_line=None):
    requests = []
    with log.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if line_number < first_line or not line.strip():
                continue
            if last_line is not None and line_number > last_line:
                break
            if line.lstrip().startswith("{"):
                row = json.loads(line)
                method, uri, status, body = (row[k] for k in ("method", "uri", "status", "bytes"))
            else:
                match = COMBINED.search(line)
                if not match:
                    raise ValueError(f"{log}:{line_number} is not Nginx combined or QA JSON access log.")
                method, uri, status, body = match.groups()
            path = urlsplit(uri).path
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix):]
            if relative == "current.json" or relative.startswith(("catalog-", "visual-assets-", "assets/")):
                requests.append((relative, method, int(status), int(body)))
    return requests


def verify(requests, expected):
    expected_counts = {path: count for path, (_, count) in expected.items() if count}
    counts = Counter(path for path, _, _, _ in requests)
    if counts != expected_counts:
        raise ValueError(f"Unexpected request counts: recorded={dict(counts)}, expected={expected_counts}")
    for path, method, status, body in requests:
        expected_size = expected[path][0]
        if (method, status, body) != ("GET", 200, expected_size):
            raise ValueError(f"{path}: expected GET 200 with {expected_size} bytes, got {method} {status} with {body} bytes")
    return {path: {"requests": count, "bytes": expected[path][0] * count} for path, count in counts.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True, help="Fixture root containing qa-index.json")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--source", required=True, help="Isolated HTTPS current.json URL")
    parser.add_argument("--stage", choices=("V1", "V2", "V3"), required=True)
    parser.add_argument("--images", required=True, help="Exact A-D image labels expected, or - for none")
    parser.add_argument("--current-count", type=int, default=1)
    parser.add_argument("--catalog-count", type=int, default=1)
    parser.add_argument("--mapping-count", type=int, default=1)
    parser.add_argument("--first-line", type=int, default=1, help="Start of isolated log window, inclusive")
    parser.add_argument("--last-line", type=int, help="End of isolated log window, inclusive")
    args = parser.parse_args()
    url = urlsplit(args.source)
    if url.scheme != "https" or not url.hostname or not url.path.endswith("/current.json") or url.query or url.fragment:
        parser.error("--source must be an HTTPS /current.json URL with neither query nor fragment.")
    if min(args.current_count, args.catalog_count, args.mapping_count) < 0:
        parser.error("Expected request counts must be nonnegative.")
    if args.first_line < 1 or (args.last_line is not None and args.last_line < args.first_line):
        parser.error("Invalid access-log window.")
    index = json.loads((args.fixtures / "qa-index.json").read_bytes())
    stage = index["stages"][args.stage]
    labels = "" if args.images == "-" else args.images
    if len(labels) != len(set(labels)) or not set(labels) <= set(stage["labels"]):
        parser.error(f"Expected image labels must be unique and within {stage['labels']}.")
    expected = {"current.json": (stage["current"]["size"], args.current_count),
                stage["catalog"]["file"]: (stage["catalog"]["size"], args.catalog_count),
                stage["visual"]["file"]: (stage["visual"]["size"], args.mapping_count)}
    for label in labels:
        image = index["images"][label]
        expected[image["path"]] = (image["size"], 1)
    try:
        requests = load_requests(args.log, url.path.removesuffix("current.json"), args.first_line, args.last_line)
        summary = verify(requests, expected)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"FAIL {error}\n")
    print(json.dumps({"stage": args.stage, "images": labels, "paths": summary,
                      "bytes": sum(row["bytes"] for row in summary.values())}, indent=2))


if __name__ == "__main__":
    main()
