"""Generated build identity of this Knowmade control-plane copy.

GENERATED FILE: do not edit by hand. scripts/sync-codex-plugin.sh and
scripts/sync-opencode-plugin.sh rewrite it, through
scripts/.sync-build-identity.sh, into scripts/knowmade_control/ and into every
packaged copy under claude-plugin/bin/ and plugins/*/bin/.

engine.py prefers VERSION and RULE_SET from this module and falls back to the
upward plugin-manifest search only when the module is missing. COMMIT is used
by packaged runtimes, which have no git checkout of their own; in a source
checkout git still reports the live commit. COMMIT is informational: it is the
short hash of HEAD when the sync ran, so in a release commit it names the
commit before the release commit. The authoritative release identity is
VERSION plus the release tag.

The mirror-sync tests compare this file with the COMMIT line filtered out, so
everything except COMMIT is deterministic for a given checkout.
"""

VERSION = "0.13.0.25.1"
COMMIT = "312996d"
RULE_SET = 3
GENERATED_BY = "scripts/sync-codex-plugin.sh, scripts/sync-opencode-plugin.sh"
