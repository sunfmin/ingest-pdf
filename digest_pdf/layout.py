"""Layout Spec (CONTEXT: Layout Spec; ADR-0008) — the repo-owned, declarative
placement source of truth at ``<repo>/.digest/layout.yaml``.

An **ordered rule table**. Each rule maps a **filename pattern** (a regex searched
against the PDF stem, with named captures such as ``year``/``region``/``subject``) to
a **Segmentation Strategy** *and* a **destination path template** mixing those captures
with a structural token the segmenter emits (``qno``/``page``/``section``). First match
wins. The tool auto-discovers the spec by walking up from cwd; the invoking repo root is
the directory that contains ``.digest/``, and templates resolve from there.

This module only **reads, validates, and matches** the spec. Applying a match to actual
placement lives in ``placement.resolve_placement()`` (ADR-0008 / issue #14).

Example ``.digest/layout.yaml``::

    rules:
      - name: 浙江高考数学真题
        match: '(?P<year>\\d{4})年(?P<region>浙江)高考数学(?:【(?P<subject>[理文])】)?'
        strategy: question
        path: '真题/{region}/{year}/{subject}/q{qno}'
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

SPEC_DIRNAME = ".digest"
SPEC_FILENAME = "layout.yaml"

# The single structural token each Segmentation Strategy can supply for a template's
# leaf segment. A template's *terminal* segment must carry its strategy's token.
STRATEGY_TOKEN = {"question": "qno", "page": "page", "outline": "section"}
STRUCTURAL_TOKENS = set(STRATEGY_TOKEN.values())

_TOKEN_RE = re.compile(r"\{(\w+)\}")


class LayoutError(ValueError):
    """A malformed or invalid Layout Spec. Carries a pointed, actionable message."""


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    strategy: str
    path: str  # destination template, e.g. "真题/{region}/{year}/{subject}/q{qno}"


@dataclass(frozen=True)
class Match:
    rule: Rule
    captures: dict  # named captures from the stem (a value is None for an optional group that didn't fire)

    def resolve(self) -> str:
        """Fill the template's **capture** tokens from the stem match, leaving the
        **structural** token intact — e.g. ``真题/{region}/{year}/{subject}/q{qno}`` →
        ``真题/浙江/2016/理/q{qno}``. A capture that resolved to None/empty drops its whole
        path segment, so an absent optional (e.g. no 【理/文】) yields ``真题/浙江/2018/q{qno}``
        rather than an empty directory. (Custom defaults are an ADR-0008 follow-up.)"""
        out_segments: list[str] = []
        for seg in self.rule.path.split("/"):
            dropped = False

            def _sub(m: "re.Match") -> str:
                nonlocal dropped
                tok = m.group(1)
                if tok in STRUCTURAL_TOKENS:
                    return m.group(0)  # leave {qno}/{page}/{section} for run time
                val = self.captures.get(tok)
                if val in (None, ""):
                    dropped = True
                    return ""
                return str(val)

            filled = _TOKEN_RE.sub(_sub, seg)
            if dropped and filled == "":
                continue  # a bare {capture} segment whose capture was absent → drop it
            out_segments.append(filled)
        return "/".join(out_segments)


@dataclass(frozen=True)
class LayoutSpec:
    repo_root: Path  # directory containing .digest/ — templates resolve from here
    spec_path: Path  # the .digest/layout.yaml file itself
    rules: list[Rule]

    def match(self, stem: str) -> Optional[Match]:
        """First rule whose pattern is found in ``stem`` (first-match-wins), else None."""
        for rule in self.rules:
            m = rule.pattern.search(stem)
            if m:
                return Match(rule=rule, captures=m.groupdict())
        return None


def discover_spec_path(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (default cwd) looking for ``.digest/layout.yaml``."""
    start = (start or Path.cwd()).resolve()
    for d in [start, *start.parents]:
        cand = d / SPEC_DIRNAME / SPEC_FILENAME
        if cand.is_file():
            return cand
    return None


def _repo_root_for(spec_path: Path) -> Path:
    """The repo root = the dir containing ``.digest/``. For an explicit ``--layout`` path
    that is not under ``.digest/``, fall back to the file's own directory."""
    if spec_path.parent.name == SPEC_DIRNAME:
        return spec_path.parent.parent
    return spec_path.parent


def _validate_rule(raw: object, i: int) -> Rule:
    where = f"rule #{i + 1}"
    if not isinstance(raw, dict):
        raise LayoutError(f"{where}: each rule must be a mapping, got {type(raw).__name__}")
    name = raw.get("name") or f"rule-{i + 1}"
    for key in ("match", "strategy", "path"):
        if key not in raw:
            raise LayoutError(f"{where} ({name}): missing required key '{key}'")
    match_src, strategy, path = raw["match"], raw["strategy"], raw["path"]
    if not isinstance(match_src, str):
        raise LayoutError(f"{where} ({name}): 'match' must be a string regex")
    try:
        pattern = re.compile(match_src)
    except re.error as e:
        raise LayoutError(f"{where} ({name}): 'match' is not a valid regex: {e}") from e
    if strategy not in STRATEGY_TOKEN:
        raise LayoutError(
            f"{where} ({name}): unknown strategy {strategy!r} "
            f"(expected one of {', '.join(sorted(STRATEGY_TOKEN))})"
        )
    if not isinstance(path, str) or not path.strip():
        raise LayoutError(f"{where} ({name}): 'path' must be a non-empty template string")

    tokens = _TOKEN_RE.findall(path)
    named = set(pattern.groupindex)
    strat_token = STRATEGY_TOKEN[strategy]
    for tok in tokens:
        if tok in STRUCTURAL_TOKENS:
            if tok != strat_token:
                raise LayoutError(
                    f"{where} ({name}): structural token '{{{tok}}}' belongs to another "
                    f"strategy; strategy '{strategy}' supplies '{{{strat_token}}}'"
                )
        elif tok not in named:
            raise LayoutError(
                f"{where} ({name}): token '{{{tok}}}' is neither a named capture in 'match' "
                f"({', '.join(sorted(named)) or 'none'}) nor a structural token "
                f"({', '.join(sorted(STRUCTURAL_TOKENS))})"
            )
    if strat_token not in tokens:
        raise LayoutError(
            f"{where} ({name}): 'path' must contain the strategy's structural token "
            f"'{{{strat_token}}}' (the per-Unit leaf)"
        )
    last_seg_tokens = _TOKEN_RE.findall(path.rstrip("/").split("/")[-1])
    if strat_token not in last_seg_tokens:
        raise LayoutError(
            f"{where} ({name}): the structural token '{{{strat_token}}}' must be in the "
            f"template's final path segment (the leaf), not a parent directory"
        )
    return Rule(name=name, pattern=pattern, strategy=strategy, path=path)


def parse_spec(text: str, spec_path: Path) -> LayoutSpec:
    """Parse + validate spec YAML text. Raises LayoutError on any problem."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise LayoutError(f"{spec_path}: invalid YAML: {e}") from e
    if not isinstance(data, dict) or "rules" not in data:
        raise LayoutError(f"{spec_path}: spec must be a mapping with a top-level 'rules:' list")
    raw_rules = data["rules"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise LayoutError(f"{spec_path}: 'rules' must be a non-empty list")
    rules = [_validate_rule(r, i) for i, r in enumerate(raw_rules)]
    return LayoutSpec(repo_root=_repo_root_for(spec_path), spec_path=spec_path, rules=rules)


def load_spec(explicit: Optional[Path] = None, start: Optional[Path] = None) -> Optional[LayoutSpec]:
    """Load the Layout Spec: an explicit ``--layout`` path if given, else auto-discovered
    by walking up from ``start`` (cwd). Absence returns None (not an error); a present but
    malformed spec raises LayoutError."""
    if explicit is not None:
        path = Path(explicit).resolve()
        if not path.is_file():
            raise LayoutError(f"--layout: no such file: {path}")
    else:
        path = discover_spec_path(start)
        if path is None:
            return None
    return parse_spec(path.read_text("utf-8"), path)
