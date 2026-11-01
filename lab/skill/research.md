# Research: using what is known without being limited by it

Literature is how you avoid rediscovering things and how you find gaps worth
aiming at. It is also the main way an AI scientist loses novelty: a model that
has read the field gravitates to what the field already did. Use it on
purpose.

## Firecrawl tools

| Need | Tool |
|---|---|
| Papers on a question (arXiv, PubMed, bioRxiv and more) | `firecrawl_research_search_papers(query, from?, to?, k)`. Try several framings: they surface different papers. |
| A specific number or method inside a paper | `firecrawl_research_read_paper(paperId, question)` returns the passages that answer it |
| Paper metadata, or papers citing or cited by it | `firecrawl_research_inspect_paper`, `firecrawl_research_related_papers` |
| Web pages, reviews, databases, PDFs | `firecrawl_search(query, categories=["research"] or ["pdf"])`, then `firecrawl_scrape(url, parsers=["pdf"])` |
| Structured data providers (possible materials databases) | `firecrawl_find_tools(query=...)`. Check what exists before scraping the same fields from many pages. |

Cite papers by DOI or canonical paper id in the notebook. A number you use
from a paper needs the paper and, ideally, the passage it came from.

## The research brief

Write one into `log.md` before designing a batch of experiments:

```markdown
### Brief: Fe-Si-P, 2026-10-31
Known (measured, with sources):
- Fe2P: K1 ≈ 2.3 MJ/m³ at 4 K, easy axis c, Tc 217 K (doi:...)
- Fe2(P,Si) bulk samples raise Tc above 400 K at ~10% Si (doi:...)
Computed only:
- MP lists Fe2Si P-62m off hull by ... (mp-...)
Gaps and contradictions:
- No measured K1 for Si above 15% on the P site; DFT papers disagree on whether anisotropy survives (doi:..., doi:...)
- Nothing on Ge on the P site
Cautions:
- Literature Tc gains come with large K1 loss in Mn-doped samples (doi:...)
Implication for the next batch: test K1 vs Si fraction at 1/6 and 1/3; open a no-precedent hypothesis on Ge.
```

Keep "known", "computed only" and "gaps" separate. The gaps line is the
point of the brief.

## Novelty check

Before calling a candidate a discovery (and before spending expensive
runs on it), classify it in `candidates.md`:

1. **Databases.** Run the novelty route on the candidate's CIF:
   `mmoderwell/check-whether-a-crystal-structure-is-already-known`
   (id in routes.md, free). It matches the structure against MP,
   Alexandria, GNoME and COD and returns `known`, `new polymorph` or
   `new composition`, plus `experimentally_known` and any copies already on
   Ouro. A structural match means at least `computed-only`, and
   `experimentally_known: true` means `known`. For the neighboring phases,
   use the hull route with `include_phase_diagram: true`, or
   `mmoderwell/search-materials-project-materials`. Avoid the GGen system
   report for this: it always publishes a post.
2. **Literature.** Search the formula, the parent plus the substitution
   ("Fe2P Si substitution"), and the structure type in this chemical system.
   A report of synthesis means `known`.
3. **Neither** means `new`. Record what you searched, so "new" means
   "not found in X, Y, Z", not "not checked".

An ordered cell of a phase known as a solid solution (one Si ordering of
Fe2(P,Si), say) is `known`, even when the database calls the exact
composition new: the material has been made, and the ordering is a model of
it. Say which ordering in the notes. It becomes `new` only if the ordering
itself is the claim and nothing reports it.

Record it as a `kind: "novelty"` experiment.

## Staying novel

- **Recall is not reasoning.** A model recognizes famous materials from
  almost any description, so confident predictions on well-known compounds
  measure memory. When you judge whether your own predictions are any good,
  look at your record on `computed-only` and `new` candidates.
- **Aim at gaps.** Good targets: properties measured in one family but not
  its neighbor, disagreements between papers, compositions reported only as
  computed, substitutions nobody tried on a site that matters.
- **Hold the no-precedent slot.** Keep at least one open hypothesis that no
  source supports, chosen on physical reasoning. Give it real experiments,
  not leftovers.
- **Let measurements overrule papers.** If a route result disagrees with
  the literature, first check the route on a calibration material. If the
  route holds up, the disagreement is a finding: write it down.
- **Know when the literature helps.** `notebook.py calibration` splits
  prediction accuracy by `literature-backed` and `no-precedent`. Weigh the
  literature by how well it has predicted results in this campaign.

## Backtesting the scientist

To test whether the loop finds things rather than remembers them: pick a
result from our own measurements, cut the literature off before it with
`firecrawl_research_search_papers(to="YYYY-MM-DD")`, start a fresh campaign
in a scratch notebook (`SCIENTIST_NOTEBOOK=/tmp/...`), and count the
sessions and dollars it takes to reach it.
