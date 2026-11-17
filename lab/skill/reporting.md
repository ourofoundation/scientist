# Writing the loop post

A loop post is read on Ouro by people following the campaign. It should read
like a short lab report: prose and a table carry the argument, the
structures are shown, and every number is one click from the run behind it.

## Preview the structures, link everything else

Post markdown can reference an asset in two ways:

- **Block embed** (`assetComponent`), which takes the full width of the post.
  A CIF with `"viewMode": "preview"` renders the interactive crystal viewer.
- **Inline link**: `[label](file:<uuid>)`, `[label](dataset:<uuid>)`,
  `[label](post:<uuid>)`, `[label](route:<uuid>)`, and for a route run
  `[label](action:<uuid>)`. It reads as part of the sentence or table cell.

Use them like this:

| Thing | How |
|---|---|
| The structures the loop is about (the lead, the cell that tested the mechanism) | `preview` block embed, the crystal viewer |
| Every other structure in the table | inline `file:` link on its name |
| Route runs behind a number | inline `action:` link on the number itself |
| A route, the first time it's named | inline `route:` link |
| Earlier loop posts, campaign datasets | inline `post:` / `dataset:` link |
| A figure you made on purpose (a plot file, a saved dataset view) | `preview` block embed |

Never embed route runs as blocks, and never embed a CIF as a `card`. Route
run receipts are tall and look the same as each other: six in a row bury the
argument. A card hides the structure, which is the one thing worth showing.

Preview one to three structures per post: the ones a reader should look at.
Put each one right after the paragraph or table it illustrates, with one
line of caption above it saying what to look at (which site, which axis).
Pick the geometry the headline number was measured on, usually the
DFT-relaxed one.

```assetComponent
{"id": "<cif file uuid>", "assetType": "file", "viewMode": "preview"}
```

## The results table

The table is the center of the post. Link from it, so the reader goes from
a number straight to its evidence:

```markdown
| Cell | Geometry | Novelty | K₁ (MJ/m³) | Js (T) | Orb hull (meV/atom) |
| --- | --- | --- | --- | --- | --- |
| [Fe₁₁Co₅B](file:<uuid>) | [DFT-relaxed](action:<relax uuid>) | new composition | [**1.01**](action:<mae uuid>) | 2.19 | [124](action:<hull uuid>) |
```

- The cell name links to its CIF. The geometry links to the relaxation that
  made it. Each measured value links to the run that measured it.
- One link per cell. A value from several runs links the one that produced
  the number; the others go in the notebook.
- Bold the headline number(s), and only those.
- Values from earlier loops, used for comparison, go in a sentence under the
  table with a link to that loop's post, not in the table.

## Open with the verdict

The first thing in the post, before any heading, is a summary a person can
read alone and know what the loop was worth. Three parts, in this order:

1. **The question**, one sentence in plain words: what the loop set out to
   find or test.
2. **The verdict**, on its own line and in bold. It answers "did the loop
   find a new candidate?" with a yes or a no, never a maybe:
   - `**New candidate: <formula>.**` then its rung on the claim ladder in
     plain words ("an ML-only lead", "meets every target on measurement;
     stability not yet checked", "met: every check passed"), its headline
     numbers, and how it compares with the campaign's best so far (better,
     level, or behind, and on what). Never write "meets every target"
     without saying which checks are still open.
   - `**No new candidate.**` then the reason in one sentence: what was
     tried and what stopped it.
   A cell that was promoted, demoted or ruled out counts: say which.
3. **Why, and what the loop was worth**, two to four sentences: the
   evidence behind the verdict, what we know now that we did not before
   (a lever ruled out, a cell promoted, a route's error explained), and
   what is still unchecked on anything put forward.

Write it for someone who reads nothing else. No ids, no route names, no
site labels a newcomer would have to look up; those belong below. A loop
with no candidate still has a value, and the summary says what it is: a
"no" that closes a direction is a result. The title carries the same
verdict in a few words.

## Shape

1. The verdict summary above.
2. **What we measured**: one line on method (functional, basis, k-mesh,
   which geometry), the table, the targets in one line, then the preview(s).
3. **What the hypotheses did**: one bullet each, the claim in bold with its
   verdict, then prediction against result.
4. **What changed**: our belief, in a few bullets.
5. **What this is not**, when a candidate is put forward: the open checks
   from its row in the claims table, and what would overturn it.
6. One line linking earlier loops.

Instrument problems and the ranked `Next` proposal do not go in the post
(Matt, 2026-11-15). They stay in `log.md`, where the next session reads
them. The exception is an instrument fault that changes how a reader
should take a number in the post: say that in one sentence beside the
number, not in a section of its own.

Use `##` headings, no deeper. Keep units in column headers, not in cells.
Use subscripts in formulas (Fe₁₁Co₅B), and LaTeX (`\\(...\\)`) only for
real math. If the post is long, draft it in
`campaigns/<slug>/work/post/post_loop<n>.md` so you can check it before it
goes out. When editing a post from ouro-py, pass the markdown as `content`:
`posts.update(content_path=...)` is ignored without an error.

## Before posting

- Every id in the post comes from the notebook or an action result.
- Every embedded structure is a CIF with `viewMode: "preview"`; no
  `assetComponent` points at a route.
- Every measured number in the table has an `action:` link.
- The opening answers "new candidate or not, and why" without the rest of
  the post, and there is no Instruments or Next section.
- Read `get_asset(id=<post id>, detail="full")` after posting: each
  embed should show a `↳` line. A missing one means a wrong id or type.
