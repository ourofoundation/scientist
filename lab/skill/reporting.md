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

## Shape

1. Two or three sentences: what the loop tested and what it found.
2. **What we measured**: one line on method (functional, basis, k-mesh,
   which geometry), the table, the targets in one line, then the preview(s).
3. **What the hypotheses did**: one bullet each, the claim in bold with its
   verdict, then prediction against result.
4. **What changed**: our belief, in a few bullets.
5. **Instruments**: only if something went wrong.
6. **Next**: the ranked proposal from the log, a line each. It is a
   proposal, so readers can argue with it before the next loop decides.
7. One line linking earlier loops.

Use `##` headings, no deeper. Keep units in column headers, not in cells.
Use subscripts in formulas (Fe₁₁Co₅B), and LaTeX (`\\(...\\)`) only for
real math. If the post is long, draft it in
`campaigns/<slug>/work/post_loop<n>.md` and post it with `content_path`
or an `upload_id`, so you can check it before it goes out.

## Before posting

- Every id in the post comes from the notebook or an action result.
- Every embedded structure is a CIF with `viewMode: "preview"`; no
  `assetComponent` points at a route.
- Every measured number in the table has an `action:` link.
- Read `get_asset(id=<post id>, detail="full")` after posting: each
  embed should show a `↳` line. A missing one means a wrong id or type.
