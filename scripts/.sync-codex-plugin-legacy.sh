#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# KNOWMADE_SYNC_OUT=<dir> redirects generated output under <dir> with the same
# repository-relative paths; sources (including the tracked Codex manifest
# this script rewrites) are always read from the checkout. See the header of
# scripts/sync-codex-plugin.sh.
if [ -n "${KNOWMADE_SYNC_OUT:-}" ]; then
  mkdir -p "$KNOWMADE_SYNC_OUT"
  OUT="$(cd "$KNOWMADE_SYNC_OUT" && pwd)"
else
  OUT="$ROOT"
fi
SOURCE_SKILL="$ROOT/claude-plugin/skills/wiki-manager"
QUERY_SOURCE="$SOURCE_SKILL/references/query-lite.md"
TARGET_PLUGIN="$OUT/plugins/llm-wiki"
TARGET_SKILL="$TARGET_PLUGIN/skills/wiki"
TARGET_QUERY="$TARGET_PLUGIN/skills/wiki-query"
CLAUDE_BIN="$OUT/claude-plugin/bin"
CLAUDE_MANIFEST="$ROOT/claude-plugin/.claude-plugin/plugin.json"
# The Codex manifest is read-modify-write: tracked fields such as keywords are
# preserved from the checkout copy and the result lands in the output tree.
CODEX_MANIFEST="$ROOT/plugins/llm-wiki/.codex-plugin/plugin.json"
CODEX_MANIFEST_OUT="$TARGET_PLUGIN/.codex-plugin/plugin.json"
SESSION_HELPER="$ROOT/scripts/llm-wiki-session"
MARKETPLACE_MANIFEST="$ROOT/.agents/plugins/marketplace.json"
LOCAL_HELPER="$ROOT/scripts/llm-wiki"
ADOPT_HELPERS="llm-wiki-adopt llm-wiki-adopt-batch"

if [ ! -d "$SOURCE_SKILL" ]; then
  echo "Missing source skill: $SOURCE_SKILL" >&2
  exit 1
fi

if [ ! -f "$QUERY_SOURCE" ]; then
  echo "Missing query-lite source: $QUERY_SOURCE" >&2
  exit 1
fi

if [ ! -f "$CLAUDE_MANIFEST" ]; then
  echo "Missing Claude manifest: $CLAUDE_MANIFEST" >&2
  exit 1
fi

if [ ! -f "$CODEX_MANIFEST" ]; then
  echo "Missing Codex manifest: $CODEX_MANIFEST" >&2
  exit 1
fi

if [ ! -f "$SESSION_HELPER" ]; then
  echo "Missing session helper: $SESSION_HELPER" >&2
  exit 1
fi

if [ ! -f "$MARKETPLACE_MANIFEST" ]; then
  echo "Missing Codex marketplace manifest: $MARKETPLACE_MANIFEST" >&2
  exit 1
fi

if [ ! -f "$LOCAL_HELPER" ]; then
  echo "Missing local helper: $LOCAL_HELPER" >&2
  exit 1
fi

# The deterministic helper is shared by every runtime package. The public
# script remains the source of truth; packaged copies make adapter management
# available even when the plugin is installed without the full repository.
mkdir -p "$CLAUDE_BIN" "$TARGET_PLUGIN/bin"
cp "$LOCAL_HELPER" "$CLAUDE_BIN/llm-wiki"
cp "$LOCAL_HELPER" "$TARGET_PLUGIN/bin/llm-wiki"
chmod 0755 "$CLAUDE_BIN/llm-wiki" "$TARGET_PLUGIN/bin/llm-wiki"

# The Claude plugin ships the session script as a plain command-driven helper,
# with no hooks (emercons/knowmade#50): /wiki:session, /wiki:ll and
# /wiki:feedback call it explicitly. The Codex mirror carries the same script
# as hooks/llm_wiki_session.py below, where it is wired to Codex hooks.
cp "$SESSION_HELPER" "$CLAUDE_BIN/llm-wiki-session"
chmod 0755 "$CLAUDE_BIN/llm-wiki-session"

# The brownfield adoption helpers ship alongside the deterministic CLI and
# must not drift from it; the mirror sync tests fail if they do.
for helper in $ADOPT_HELPERS; do
  if [ ! -f "$ROOT/scripts/$helper" ]; then
    echo "Missing local helper: $ROOT/scripts/$helper" >&2
    exit 1
  fi
  cp "$ROOT/scripts/$helper" "$CLAUDE_BIN/$helper"
  cp "$ROOT/scripts/$helper" "$TARGET_PLUGIN/bin/$helper"
  chmod 0755 "$CLAUDE_BIN/$helper" "$TARGET_PLUGIN/bin/$helper"
done

mkdir -p "$TARGET_PLUGIN/skills"
# The Codex marketplace caches plugin contents eagerly, so references/ must be
# copied into the generated tree rather than left as a symlink. agents/ is
# Codex-only metadata and is recreated below.
rm -rf "$TARGET_PLUGIN/skills/wiki-manager"
rm -rf "$TARGET_QUERY"
rm -rf "$TARGET_SKILL"
cp -R "$SOURCE_SKILL" "$TARGET_SKILL"
rm -rf "$TARGET_SKILL/agents"

mkdir -p "$TARGET_SKILL/agents"
mkdir -p "$TARGET_PLUGIN/hooks"
cp "$SESSION_HELPER" "$TARGET_PLUGIN/hooks/llm_wiki_session.py"
chmod 0755 "$TARGET_PLUGIN/hooks/llm_wiki_session.py"

cat > "$TARGET_PLUGIN/hooks/hooks.json" <<'EOF'
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}/hooks/llm_wiki_session.py\" hook --harness codex --if-enabled",
            "timeout": 5,
            "statusMessage": "Loading Knowmade session context"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}/hooks/llm_wiki_session.py\" hook --harness codex --if-enabled",
            "timeout": 5,
            "statusMessage": "Checking Knowmade session context"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}/hooks/llm_wiki_session.py\" hook --harness codex --if-enabled",
            "timeout": 5,
            "statusMessage": "Recording Knowmade session event"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}/hooks/llm_wiki_session.py\" hook --harness codex --if-enabled",
            "timeout": 5,
            "statusMessage": "Capturing Knowmade pre-compact session context"
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}/hooks/llm_wiki_session.py\" hook --harness codex --if-enabled",
            "timeout": 5,
            "statusMessage": "Capturing Knowmade post-compact session context"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}/hooks/llm_wiki_session.py\" hook --harness codex --if-enabled",
            "timeout": 5,
            "statusMessage": "Saving Knowmade session checkpoint"
          }
        ]
      }
    ]
  }
}
EOF

# Codex keeps the expanded hook command in a running session. During an update,
# that session's versioned cache directory can disappear before the process is
# restarted. Keep the command itself self-healing: use the loaded plugin helper
# when present, otherwise execute an installed helper from the Codex plugin
# cache. The cache lives under the marketplace name this repository registers
# (see .agents/plugins/marketplace.json and scripts/bootstrap-codex-plugin.sh),
# so the fallback globs only that marketplace. It never widens to another
# marketplace: a machine that also has upstream nvk/llm-wiki installed
# (wiki@llm-wiki) would otherwise let this hook run upstream's helper
# (emercons/knowmade#124). No Knowmade cache entry means no helper, and the
# hook exits 0 without capturing anything. Among candidates it prefers the version this hooks.json was generated for,
# then the highest version by numeric component comparison -- never mtime,
# which would pick whichever cache was touched last, including a foreign
# upstream llm-wiki install. The fallback contains no user content and
# prevents a missing old cache from blocking UserPromptSubmit.
python3 - "$TARGET_PLUGIN/hooks/hooks.json" "$CLAUDE_MANIFEST" "$MARKETPLACE_MANIFEST" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
# The Codex manifest version is copied from the Claude manifest later in this
# script, so the Claude manifest is the value the generated plugin will carry.
version = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["version"]
marketplace = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))["name"]
for label, token in (("plugin version", version), ("marketplace name", marketplace)):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", token):
        raise SystemExit(f"Refusing to embed unsafe {label} in hooks.json: {token!r}")

value = json.loads(path.read_text(encoding="utf-8"))
launcher = (
    "python3 -c 'import glob,os,runpy,sys; "
    f"V=\"{version}\"; "
    "p=os.path.join(os.environ.get(\"PLUGIN_ROOT\",\"\"),\"hooks\",\"llm_wiki_session.py\"); "
    "h=os.environ.get(\"HOME\") or os.path.expanduser(\"~\"); "
    "c=os.path.join(h,\".codex\",\"plugins\",\"cache\"); "
    "g=lambda m:glob.glob(os.path.join(c,m,\"wiki\",\"*\",\"hooks\",\"llm_wiki_session.py\")); "
    f"xs=[] if os.path.isfile(p) else g(\"{marketplace}\"); "
    "v=lambda x:os.path.basename(os.path.dirname(os.path.dirname(x))); "
    "k=lambda x:(v(x)==V,tuple(int(t) for t in v(x).split(\".\") if t.isdigit())); "
    "p=p if os.path.isfile(p) else (max(xs,key=k) if xs else \"\"); "
    "sys.exit(0) if not p else None; "
    "sys.argv=[p,*sys.argv[1:]]; runpy.run_path(p,run_name=\"__main__\")' "
    "hook --harness codex --if-enabled"
)
for entries in value["hooks"].values():
    for entry in entries:
        for hook in entry["hooks"]:
            hook["command"] = launcher
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
PY

cat > "$TARGET_SKILL/agents/openai.yaml" <<'EOF'
interface:
  display_name: "Knowmade"
  short_description: "Research practical goals, run private adapters, apply personal specialists, shape Ideas, review portfolios, dispatch intake, and maintain Knowmade wikis."
  brand_color: "#2F855A"
  default_prompt: "Capture and shape an Idea around my goals, run a registered private adapter, apply a personal specialist, research a topic, or compile evidence into a Knowmade wiki."

policy:
  allow_implicit_invocation: true
EOF

mkdir -p "$TARGET_QUERY/agents"
cat > "$TARGET_QUERY/SKILL.md" <<'EOF'
---
name: wiki-query
description: >
  Fast, explicit, read-only Knowmade queries for Codex. Use for factual
  lookups, evidence checks, lists, and inventory status without wiki writes.
---

EOF
cat "$QUERY_SOURCE" >> "$TARGET_QUERY/SKILL.md"

cat > "$TARGET_QUERY/agents/openai.yaml" <<'EOF'
interface:
  display_name: "Wiki Query"
  short_description: "Read-only, index-first answers from Knowmade with exact file citations."
  brand_color: "#2F855A"
  default_prompt: "Answer this question from the selected Knowmade wiki without changing any files."

policy:
  allow_implicit_invocation: false
EOF

mkdir -p "$(dirname "$CODEX_MANIFEST_OUT")"
python3 - "$TARGET_SKILL" "$CLAUDE_MANIFEST" "$CODEX_MANIFEST" "$CODEX_MANIFEST_OUT" <<'PY'
import json
import re
import sys
from pathlib import Path

target_skill = Path(sys.argv[1])
claude_manifest = Path(sys.argv[2])
codex_manifest = Path(sys.argv[3])
codex_manifest_out = Path(sys.argv[4])

skill_path = target_skill / "SKILL.md"
text = skill_path.read_text(encoding="utf-8")

frontmatter = """---
name: wiki
description: >
  Manage Knowmade practical knowledge bases in Codex: dispatch hub intake,
  ingest/import, shape/promote Ideas around private goals, review portfolios,
  track inventory/datasets, archive, compile/query/lint/audit, research/plan,
  manage public/private topic pairs, sessions, private adapters, and personal
  specialists, and generate outputs.
  Activates when the user mentions wiki workflows, knowledge-base management,
  ingestion, collection ingestion, import wiki, collect, catalog, curate,
  find all, idea, turn idea into project, portfolio, business ideas, projects,
  inventory, source queue, hub inbox, dispatch inbox, private goals,
  candidate list, watch list, backlog, dataset, large data, data registry,
  dataset manifest, compilation, querying, linting, audit, research, librarian,
  scan quality, article quality, content review, output drift, provenance,
  archive wiki, archive topic, restore wiki, private adapter, adapter registry, skill-factory,
  checkpoints,
  personal specialist, specialist skill, specialist reviewer, expert lens,
  adapter route, adapter doctor, adapter run, read, review, or edit a hosted external resource such as a document,
  session capture, capture context, rehydrate,
  resume from session, implementation plan, or uses
  /wiki-style shorthand in a repo with .wiki/, ~/wiki/, or a configured hub path.
---
"""

start = text.find("---\n")
end = text.find("\n---\n", start + 4)
if start != 0 or end == -1:
    raise SystemExit(f"Unexpected frontmatter in {skill_path}")
text = frontmatter + text[end + 5 :]

replacements = [
    (
        "You manage a Knowmade practical knowledge base. Source material is staged in a\ntopic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After\nextraction and provenance capture, processed immutable artifacts are written to\n`raw/` and incrementally compiled into interconnected markdown articles. The\nactive agent is both compiler and query engine; Obsidian is an optional viewer.\n",
        "You manage a Knowmade practical knowledge base. Source material is staged in a\ntopic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After\nextraction and provenance capture, processed immutable artifacts are written to\n`raw/` and incrementally compiled into interconnected markdown articles. Codex\nis both compiler and query engine; Obsidian is an optional viewer.\n\n## Codex Plugin Notes\n\nCodex plugins package skills, MCP servers, apps, and metadata. They do not register Claude-style custom `/wiki:*` commands. Treat any `/wiki`, `/wiki:*`, or command-flag examples in this skill and its references as shorthand for the same workflow expressed in natural language, or via explicit `@wiki` invocation.\n",
    ),
    # The Dual-linking principle moved to references/principles.md
    # (emercons/knowmade#81); references are shared verbatim and say "agent".
    (
        "When this skill activates outside an explicit wiki invocation:",
        "When this skill activates outside of an explicit `@wiki` invocation or `/wiki`-style shorthand:",
    ),
    (
        '4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki with `/wiki:ingest`"',
        '4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki; ask `@wiki` to ingest it."',
    ),
    # Anchors for the Compilation Nudge, Structural Guardian, Concurrency and
    # stale-research-session wording were dropped when those sections moved to
    # references/skill-workflows.md (emercons/knowmade#55). References are shared
    # verbatim, so those rewrites no longer apply to SKILL.md; the neutral wording
    # ("the agent", "/wiki-style shorthand") is covered by the runtime notes above.
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"Expected text not found in {skill_path}: {old[:80]!r}")
    text = text.replace(old, new)

def replace_section(src, heading, replacement, next_heading=None):
    if next_heading is None:
        pattern = re.compile(rf"(?ms)^## {re.escape(heading)}\n.*\Z")
    else:
        pattern = re.compile(
            rf"(?ms)^## {re.escape(heading)}\n.*?(?=^## {re.escape(next_heading)}\n)"
        )
    if not pattern.search(src):
        raise SystemExit(f"Missing section heading in {skill_path}: {heading}")
    return pattern.sub(replacement, src, count=1)

text = replace_section(
    text,
    "Workflows",
    """## Workflows

Route free-text requests with `references/router.md`, the single routing
table shared with the Claude `/wiki` router; do not route from memory. Then
choose the smallest matching workflow and load only the reference material you
need for it:

- `ingest` → `references/ingestion.md`; add `references/source-lifecycle.md` only when original bytes are retained
- `ingest-collection` → `references/ingestion-collections.md`
- hub inbox dispatch → `references/hub-dispatch.md` and `references/knowmade-profile.md`
- `adapter` and private-adapter execution → `references/adapters.md`
- `collect` → `references/inventory.md`
- `inventory` → `references/inventory.md`
- `idea` → `references/ideas.md`
- `dataset` → `references/datasets.md`
- `archive` → `references/archive.md`
- `compile` → `references/compilation.md` and `references/indexing.md`
- `query` → read the relevant `_index.md` files first, then only the articles
  needed to answer
- `lint` → `references/linting.md`
- `audit` → `references/audit.md`
- `research` → `references/research-protocol.md` and `references/research-modes.md`; they name the per-phase file (`research-agents`, `-credibility`, `-staging`, `-rounds`, `-sessions`, `-plan`) each step reads
- `plan` → `references/plan-protocol.md`
- `assess` → `references/assess-protocol.md`
- `output` → `references/output-protocol.md`
- `ll` (lessons learned) → `references/ll-protocol.md`
- `refresh` → `references/refresh-protocol.md`
- `retract` → `references/retract-protocol.md`
- `specialist` → `references/specialists.md`
- `portfolio` → `references/portfolio.md`
- `project` → `references/projects.md`
- `checkpoint` → `references/checkpoints.md`
- `librarian` → `references/librarian.md`
- directory layout, `wikis.json` registry, file placement, init → `references/wiki-structure.md`
- index, log, raw-source, naming and tag formats → `references/wiki-format.md`
- config, topic guide, article, source-reference, volatility, linking and output formats → `references/wiki-articles.md`
- hub lookup and path handling → `references/hub-resolution.md`
- `session` (capture, automated hooks, rehydration, promotion) → `references/sessions.md`
- `feedback` (curation, corrections, approvals, candidate promotion) → `references/feedback.md`
- workflow flow summaries, Structural Guardian, confidence scoring, concurrency, session management → `references/skill-workflows.md`

Private adapters are explicitly trusted local executables. Resolve the bundled
`bin/llm-wiki` from the installed plugin root containing this skill; in a source
checkout use `scripts/llm-wiki`. Never clone or update an adapter, store its
registration in the hub, pass unregistered paths, or import outputs
automatically. Verify the manifest/handshake, run a v1 JSON request, keep
`private` and `bulk` artifacts external, and review `wiki-safe` candidates
before any normal wiki write.

Collect requests create bounded catalogs of discoverable things: artifacts,
examples, resources, entities, tools, media, memes, or source candidates. Infer
scale and media policy, record aliases plus `found_in_context` provenance,
deduplicate candidates, write a `type: collection` output at
`output/collect-<slug>-YYYY-MM-DD.md`, then create inventory only when the list
is small and durable enough; otherwise create or suggest one corpus record.
Download and hash bounded public binary media into
`output/assets/collect-<slug>/` by default for media-bearing collections, never
put binaries in `raw/`, and use defensive download settings: timeouts,
file-size caps, content-type checks, and IPv4 retry (`curl -4`) when media
hosts hang on IPv6. Use kind-first topic slugs such as `memes-bitcoin`,
`memes-ethereum`, or `tools-bitcoin` for collection families that can grow
across subjects. Never present "all" as exhaustive beyond the stated strategy
and limit.

Inventory is first-class operational state, not a silo. Ingest, collection, and
collect workflows should suggest inventory when the user wants to track or
decide later.
Dataset manifests should link to inventory records when next actions or
acceptance state matter. Compile and query may surface inventory gaps, but
factual claims still need raw/wiki sources. Collect, research, audit,
librarian, refresh, plan, output, and assess may propose durable follow-ups as
inventory records, but larger pivots should start with a small sample preview.

Ideas live under `inventory/ideas/`. Route fuzzy capture, research, shaping,
and promotion through `references/ideas.md`; require approval, preserve lineage,
and keep delivery truth in the Project.

Keep the first response short and action-oriented. Read deeper references only
after the user intent is clear or a write action is needed.

""",
    next_heading="Links: File Paths and URLs",
)

text = replace_section(
    text,
    "Links: File Paths and URLs",
    """## Operational Rules

- Use absolute file paths in saved-output messages and markdown links for URLs.
- Append to `log.md` for every wiki write operation; never rewrite old log entries.
- Keep large writes chunked into multiple edits rather than one long generation.
- Read `_index.md` files before broader scans, and treat indexes as derived data.
- Use article `confidence` fields when answering and flag weak sourcing when seen.
- If structure or placement looks wrong, use the `lint --fix` workflow from
  `references/linting.md` instead of inventing a one-off repair path.

""",
)

skill_path.write_text(text, encoding="utf-8", newline="\n")

# references/ is a copied mirror of claude-plugin/skills/wiki-manager/references
# and is shared verbatim — no per-file replacements needed. Source references
# use runtime-neutral wording ("the agent") so they read correctly under both.

claude = json.loads(claude_manifest.read_text(encoding="utf-8"))
codex = json.loads(codex_manifest.read_text(encoding="utf-8"))
codex["version"] = claude["version"]
codex["name"] = "wiki"
codex["license"] = claude.get("license", codex.get("license"))
codex["homepage"] = "https://github.com/emercons/knowmade"
codex["repository"] = "https://github.com/emercons/knowmade"
codex["author"] = {
    "name": "emercons",
    "url": "https://github.com/emercons",
}
codex["description"] = (
    "Knowmade practical knowledge bases for Codex with goal-directed private "
    "work, hub inbox dispatch, public/private topic pairs, fuzzy Idea capture, "
    "Project promotion, explicitly trusted private adapters, personal specialist "
    "skills, Project Knowledge Checkpoints Export, inventory, datasets, ingestion, "
    "audits, sessions, and outputs."
)
keywords = [
    keyword
    for keyword in codex.get("keywords", [])
    if not (keyword.startswith("private") and keyword.endswith("-skills"))
]
for keyword in ["ideas", "projects", "specialists", "personal-skills", "skill-factory", "checkpoints", "knowledge-handoff"]:
    if keyword not in keywords:
        keywords.append(keyword)
codex["keywords"] = keywords
interface = codex.setdefault("interface", {})
interface["displayName"] = "Knowmade"
interface["developerName"] = "emercons"
interface["websiteURL"] = "https://github.com/emercons/knowmade"
interface["shortDescription"] = (
    "Research, shape Ideas, and export project knowledge checkpoints"
)
interface["longDescription"] = (
    "Bundle the Knowmade workflow for Codex: use private goals as planning context, "
    "dispatch mixed-topic hub intake, capture and shape Ideas, apply personal specialist "
    "methods, export privacy-sealed Project Knowledge Checkpoints, promote approved briefs "
    "into Projects, and maintain public/private topic-scoped sources, compiled knowledge, "
    "inventory, datasets, sessions, audits, and outputs."
)
prompts = list(interface.get("defaultPrompt", []))
old_checkpoint_prompt = "Create a privacy-sealed Project Knowledge Checkpoint from relevant research across my topic wikis."
prompts = [prompt for prompt in prompts if prompt != old_checkpoint_prompt]
checkpoint_prompt = "Export a Project Knowledge Checkpoint from relevant research across my topic wikis, with privacy checks."
if checkpoint_prompt not in prompts:
    prompts.insert(0, checkpoint_prompt)
idea_prompt = "Capture this rough Idea, research it, shape alternatives, and wait for approval before creating a Project."
if idea_prompt not in prompts:
    prompts.insert(0, idea_prompt)
dispatch_prompt = "Triage my hub inbox and route each item to the right public or private topic inbox."
if dispatch_prompt not in prompts:
    prompts.insert(1, dispatch_prompt)
adapter_prompt = "Run a registered private adapter and review only its wiki-safe evidence candidates."
if adapter_prompt not in prompts:
    prompts.insert(2, adapter_prompt)
interface["defaultPrompt"] = prompts
codex_manifest_out.write_text(json.dumps(codex, indent=2) + "\n", encoding="utf-8", newline="\n")
PY

echo "Synced Codex plugin skill from canonical Knowmade source."
echo "Source: $SOURCE_SKILL"
echo "Target: $TARGET_SKILL"
echo "Query preset: $TARGET_QUERY"
