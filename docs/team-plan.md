# Team plan: next work

Who builds what, so we don't overlap. Setup and code rules: `README.md`, `CLAUDE.md`.
Branch from `dev`, small PRs into `dev`, tests with the fake model.

## First, together (Felix + Kevin)

Agree on the **run settings** shape before building; both features use it:

```
depth: quick | standard | deep      # maps to budgets in sources.yaml
sources: [web, grants, procurement, news]
org_sources: [<OrgSource ids>]      # Kevin
folders: [<KnowledgeFolder ids>]    # Felix
opportunity_types: [...]
```

Saved as the org's defaults (Settings) and copied onto each `DiscoveryRun.config`.

## Felix: knowledge folders and run configuration (backend + frontend)

- [x] **Folders:** `KnowledgeFolder` (name, purpose/label). Create, rename, delete, open a
  folder, upload files into it. Every document belongs to a folder.
- [x] **Run settings tab** in Settings: depth, pipeline, sources, folders, types (the
  shape above). Org defaults.
- [x] **Run pop-up** on "Search for opportunities": *Run with my settings* (one click) or
  *Configure this run*. `start_run` accepts the settings and stores them on the run.
- [x] **Agents use the folders:** knowledge search limited to the chosen folders;
  folder names and purposes go into the planner's context; recorded on the run.
- [ ] **New agent `folder_summarizer`:** a short summary per folder (what it contains and
  what it proves), refreshed when files change, so agents use the folders without
  reading every file.

## Kevin: run results, extra places to look, discovery agents (backend + frontend)

- [x] **Show what a run produced:** Runs and Agents list the opportunities each run added
  (score, link), with a link to Opportunities filtered by that run.
- [x] **New agent `opportunity_analyst`:** after a run saves an opportunity, writes a brief:
  what it is, why it fits, eligibility checklist, documents needed, deadline plan,
  effort, next step. Shown on the opportunity and in a "New finds" view.
- [x] **Extra places to look (org sources):** Settings › Sources, where an org adds the
  websites, portals and pages where it wants the AI to look *in addition to* the
  default sources. Model `OrgSource` (url, label, notes).
- [x] **New source + agent `source_reader`:** each run reads the org's sources (and the
  listing pages they link to), extracts open calls, and passes them to the verifier.
  The supervisor plans searches around them too.
- [x] **Fix the discovery agents** (see below).
- [ ] Stretch: email digest of new finds after each run.

## Agents with issues (Kevin)

| Agent | Issue | Fix |
|---|---|---|
| `researcher` | Too strict: picked 1 of 48 and 3 of 39 results; real runs find 0–3 | Prompt v2: pick more borderline results; let the verifier decide |
| `verifier` | Keeps old calls with no stated deadline (a "2024" call was saved) | Treat a past year in the title or URL with no deadline as closed |
| `supervisor` | Plans Grants.gov searches for non-US orgs, which rarely fit | Weight sources by the org's countries; include org sources |
| `lead_scout` | Never tested live; emits no run events | Live test; add events so the UI can show progress |

Check every prompt change with `python manage.py run_evals`, comparing old and new versions.

## Round 2 (after the above)

**Felix: proposals and documents**
- [ ] Proposal Writer uses folders: evidence from the folders chosen for the
  proposal; "Past proposals" folder used as style and structure examples.
- [ ] Organization template for the Word export (logo, cover page, fonts), plus PDF
  export.
- [ ] Scanned PDFs and images readable (OCR), so they stop showing "Not readable".
- [ ] Cost per run and per proposal (tokens → USD) on the run and proposal pages, and an
  org total in Settings.

**Kevin: more sources and follow-up**
- [ ] EU Funding & Tenders and TED (EU procurement) sources.
- [ ] Weekly scheduled runs on by default for new orgs, with the new-finds email digest.
- [ ] Deadline-driven follow-up: reminders link to the proposal and its open [TODO]s.

## MVP: done when

Tested with 3 real East African organizations (their profiles and documents):

1. Setup to first run in under 15 minutes: profile, documents in folders, own sources.
2. One click "Run with my settings" finishes in under 3 minutes and adds at least 3
   open, relevant opportunities per org. No closed calls are saved.
3. Every find has a brief; approving one gives a full proposal in under 5 minutes, which
   exports to Word and PDF.
4. Contacts are found for at least half of the approved opportunities; an approved email
   is sent through a real email provider.
5. Weekly runs and the digest arrive without anyone clicking anything.
6. Running on a staging server (API, worker, beat, pgvector) with the cost of each run
   shown.
7. Evals: judge agreement at least 0.8 on the golden set; the review set has no
   closed-call false positives.

 