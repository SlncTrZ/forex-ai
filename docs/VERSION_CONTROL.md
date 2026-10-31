# Forex-AI Tag-First Release Version Policy

**Policy effective:** 2026-10-31. **Scope:** Forex-AI source and Command Center.
The product's only published version identifier is an **annotated Git tag**
matching `v0.0.N`, starting from the existing `v0.0.1` history.

## Rules

1. Bump the *last integer only*, by exactly **one**: `v0.0.3 -> v0.0.4`.
   Never skip numbers, move existing tags, retag a different commit, or create
   retroactive tags just to account for historical commits.
2. A normal release becomes eligible after **3 small, first-parent,
   non-merge commits** following the last release tag.
3. An owner-approved **major change** may release after a single commit.
   The annotated tag message must contain `Change-Type: major` and a
   human-readable `Summary:`. An ordinary release uses `Change-Type: small`.
4. Creating a commit does not change the displayed version. Before the next
   tag, such commits are development progress and **not production releases**.
5. Only the current `main` HEAD, in sync with `origin/main`, is eligible
   for a new release tag. The tag must exist on `origin` and resolve to the
   **exact** commit being deployed.
6. Deployments **fail closed** when HEAD is not tagged, tag is lightweight,
   tag is not sequential, tag metadata is absent, or origin does not match.
   No untagged source may be deployed.
7. `release_tag` is the product version and is shown in Command Center.
   `git_sha`, timestamp, release fingerprint, dependency/config fingerprints,
   frontend build hash, DB schema and D3 policy versions remain separate
   traceability fields. DB schema `13` is not application version `v0.0.13`.
8. Rollback must target a retained release artifact with known tag and
   full immutable manifest, preserve persistent ARM/KILL and trading journal,
   restart applicable services and pass health/safety checks. **The existing
   rollback shell action does not yet implement all of these gates**; do not
   advertise one-click tag rollback as completed functionality.

## Release runbook

```bash
python3 scripts/version_control.py --repo .
python3 -m pytest tests -q
# Build and verify frontend bundle; perform broker/readiness review.
# Commit and push source changes to main; confirm upstream is synchronized.
python3 scripts/tag_release.py --repo . --kind major --summary "Owner-approved D3/Command Center change"
git push origin v0.0.4
# Deployment script requires this exact pushed annotated tag.
bash scripts/deploy_local.sh deploy
```

For a normal release, choose `--kind small` only after 3 small commits. This
manual authorization workflow deliberately avoids a background process
creating trading releases without the operator's decision.

## First transition

Existing tags are `v0.0.1`, `v0.0.2`, `v0.0.3`. The major Command Center V3,
D3 routing/scoring and persistent ARM work between `v0.0.3` and the first
tagged release under this policy is treated as **one approved major change**.
The next tag is `v0.0.4`, without inventing intervening versions.
