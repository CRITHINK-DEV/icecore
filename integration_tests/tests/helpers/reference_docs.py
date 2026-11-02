"""Macro and generic-test reference: one source of truth, the `.yml` files next to the macros and tests.

- `problems(root)` is the docs check: every public macro (no leading underscore) and every generic test must have a
  description in its .yml, and every argument of its signature must have one too (and a type). It parses the files
  only, so it runs offline and needs no dbt.
- `render(root)` builds docs/reference/macros.md and docs/reference/tests.md from the same files. Regenerate with
  `python integration_tests/tests/helpers/reference_docs.py` from the repository root (it needs PyYAML, which dbt installs).
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]

DEFINITION = re.compile(r"{%-?\s*(macro|test)\s+(\w+)\s*\(")

# Folder of macros/ -> (heading, one line of what the macros in it are for). Order is the order of the page.
GROUPS = {
    "setup": ("Setup and checks", "Run `setup`, `doctor`, `estimate_telemetry_cost` and `set_telemetry_levels` with `dbt run-operation icecore.<name>`. The others are steps they call."),
    "telemetry": ("Run telemetry", "The hooks and the emitters behind them."),
    "alerts": ("Alerts", "Acknowledging incidents."),
    "control": ("Alerts, SLAs and tasks", "Built by `setup`; rarely called by hand."),
    "monitors": ("Monitor building blocks", "Readers and computers the generic tests use."),
    "engines": ("Anomaly engines", "Scoring a metric against its history."),
    "root_cause": ("Root cause", "Why a test failed."),
    "cost": ("Cost", "Attaching warehouse cost to a node."),
    "views": ("Views", "The SQL behind the [views](views.md)."),
    "compat": ("Elementary-compatible views", "Created when `setup` runs with `compat: elementary`."),
}


def split_args(text: str) -> list[tuple[str, str | None]]:
    """'a, b=none, c="x,y"' -> [('a', None), ('b', 'none'), ('c', '"x,y"')]. Brackets and quotes do not split."""
    out, depth, quote, cur = [], 0, None, ""
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    result = []
    for item in out:
        item = item.strip()
        if item:
            name, _, default = item.partition("=")
            result.append((name.strip(), default.strip() if _ else None))
    return result


def definitions(root: Path) -> dict:
    """Doc name -> {kind, name, file, args}, for every macro in macros/ and every generic test in tests/generic/."""
    found = {}
    files = sorted((root / "macros").rglob("*.sql")) + sorted((root / "tests" / "generic").glob("*.sql"))
    for path in files:
        text = path.read_text()
        for m in DEFINITION.finditer(text):
            depth, i = 1, m.end()
            while depth and i < len(text):
                depth += (text[i] == "(") - (text[i] == ")")
                i += 1
            kind, name = m.group(1), m.group(2)
            doc_name = "test_" + name if kind == "test" else name
            found[doc_name] = {"kind": kind, "name": name, "file": path.relative_to(root).as_posix(),
                               "args": split_args(text[m.end():i - 1])}
    return found


def documented(root: Path) -> dict:
    """Doc name -> yml entry, from every .yml under macros/. The docs of a generic test live in macros/tests/<name>.yml
    under the name test_<name>: dbt reads them only from macro-paths, so `dbt docs generate` shows nothing for a yml in tests/generic/."""
    found = {}
    for path in sorted((root / "macros").rglob("*.yml")):
        for entry in (yaml.safe_load(path.read_text()) or {}).get("macros") or []:
            found[entry["name"]] = {**entry, "_file": path.relative_to(root).as_posix()}
    return found


def public(defs: dict) -> dict:
    return {k: v for k, v in defs.items() if not v["name"].startswith("_")}


def problems(root: Path = REPO) -> list[str]:
    defs, docs = public(definitions(root)), documented(root)
    out = []
    for key, d in defs.items():
        what = "generic test" if d["kind"] == "test" else "public macro"
        entry = docs.get(key)
        if entry is None:
            out.append(f"{what} {d['name']} ({d['file']}) has no entry in a .yml file")
            continue
        if not str(entry.get("description") or "").strip():
            out.append(f"{what} {d['name']} has no description")
        by_name = {a["name"]: a for a in entry.get("arguments") or []}
        for arg, _default in d["args"]:
            a = by_name.get(arg)
            if a is None or not str(a.get("description") or "").strip():
                out.append(f"{what} {d['name']}: argument {arg} has no description")
            elif not str(a.get("type") or "").strip():
                out.append(f"{what} {d['name']}: argument {arg} has no type")
        extra = set(by_name) - {a for a, _ in d["args"]}
        for arg in sorted(extra):
            out.append(f"{what} {d['name']}: documented argument {arg} is not in its signature")
    for key, entry in docs.items():
        if not key.startswith("_") and key not in public(definitions(root)) and key not in definitions(root):
            out.append(f"{key} ({entry['_file']}) documents a macro that does not exist")
    return out


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _table(d: dict, entry: dict) -> list[str]:
    by_name = {a["name"]: a for a in entry.get("arguments") or []}
    if not d["args"]:
        return ["This macro takes no arguments.", ""]
    rows = ["| Argument | Type | Default | What it does |", "| --- | --- | --- | --- |"]
    for arg, default in d["args"]:
        a = by_name[arg]
        rows.append(f"| `{arg}` | {_cell(a['type'])} | {'required' if default is None else '`' + default + '`'} | {_cell(a['description'])} |")
    return rows + [""]


def _prose(entry: dict) -> str:
    return " ".join(str(entry["description"]).split())


def _call(d: dict) -> str:
    required = [a for a, default in d["args"] if default is None]
    return "{{ icecore." + d["name"] + "(" + ", ".join(required) + ") }}"


def render_macros(root: Path = REPO) -> str:
    defs, docs = public(definitions(root)), documented(root)
    lines = [
        "# Macro reference",
        "",
        "<!-- Generated from the .yml files next to the macros by integration_tests/tests/helpers/reference_docs.py. "
        "Edit the .yml, then regenerate. -->",
        "",
        "Every public macro of icecore, that is every macro without a leading underscore. `dbt docs generate` shows the same "
        "descriptions. Macros that start with `_icecore_` are internal and not listed. The generic tests are on the "
        "[tests page](tests.md), the views on the [views page](views.md).",
        "",
        "Call a macro in a hook or a model as `{{ icecore.<name>(...) }}`, or from the command line as "
        "`dbt run-operation icecore.<name> --args '{arg: value}'`. Each entry shows the call with its required arguments.",
        "",
    ]
    for folder, (title, blurb) in GROUPS.items():
        names = sorted(k for k, d in defs.items() if d["kind"] == "macro" and d["file"].split("/")[1] == folder)
        if not names:
            continue
        lines += [f"## {title}", "", blurb, ""]
        for key in names:
            d = defs[key]
            lines += [f"### `{d['name']}`", "", _prose(docs[key]), "", "```sql", _call(d), "```", ""]
            lines += _table(d, docs[key])
    unlisted = [k for k, d in defs.items() if d["kind"] == "macro" and d["file"].split("/")[1] not in GROUPS]
    assert not unlisted, f"add the folder of {unlisted} to GROUPS"
    return "\n".join(lines).rstrip() + "\n"


def render_tests(root: Path = REPO) -> str:
    defs, docs = public(definitions(root)), documented(root)
    lines = [
        "# Generic test reference",
        "",
        "<!-- Generated from macros/tests/*.yml by integration_tests/tests/helpers/reference_docs.py. "
        "Edit the .yml, then regenerate. -->",
        "",
        "The generic tests of icecore. Add them under `data_tests:` of a model, or of a column for the tests that take a "
        "column. `model` and `column_name` are filled in by dbt: you never write them. Every test accepts dbt's own "
        "`config:`, such as `severity: warn`. How the tests fit together is on the [configuration page](../configuration.md#generic-tests).",
        "",
    ]
    for key in sorted(k for k, d in defs.items() if d["kind"] == "test"):
        d = defs[key]
        needs = [a for a, default in d["args"] if default is None and a not in ("model", "column_name")]
        snippet = ["- icecore." + d["name"] + (":" if needs else "")] + [f"    {a}: <{a}>" for a in needs]
        indent = "      " if "column_name" in [a for a, _ in d["args"]] else "  "
        head = ["columns:", "  - name: <column>", "    data_tests:"] if indent == "      " else ["data_tests:"]
        body = [("    " if indent == "      " else "") + "  " + s for s in snippet]
        lines += [f"## `icecore.{d['name']}`", "", _prose(docs[key]), "", "```yaml", *head, *body, "```", ""]
        lines += _table(d, docs[key])
    return "\n".join(lines).rstrip() + "\n"


def write(root: Path = REPO) -> None:
    out = root / "docs" / "reference"
    (out / "macros.md").write_text(render_macros(root))
    (out / "tests.md").write_text(render_tests(root))


if __name__ == "__main__":
    found = problems()
    if found:
        sys.exit("\n".join(found))
    write()
