# Instruments: Ouro routes

Every measurement comes from a route run on Ouro, so it has an action id,
inputs and outputs anyone can inspect. Call routes through the Ouro MCP tools:
- `get_asset(id="<entity>/<route>", detail="full")` gives the input schema, the
  cost and whether it runs async.
- `execute_route(route_id=..., input_assets={...}, body={...}, wait=false, notify="failure", org_id=..., team_id=...)` starts a run.
- `get_action(id=..., include_response=true)` gives the result.
- `list_my_actions` finds a run whose id you lost.

Use `dry_run=true` to see the cost before the first run of a paid route in a
campaign. Send only the parameters you mean to change: defaults are tuned, and
filling in every field has broken runs before. Run
`search_assets(asset_type="route", query=...)` to find routes not listed here.

## Tiers

| Tier | Time | Use on |
|---|---|---|
| free | seconds | everything |
| cheap | seconds to minutes | every plausible candidate |
| expensive | tens of minutes to hours (DFT) | candidates whose best case beats the best fully measured one |

## Structures in

| Route | What | Tier |
|---|---|---|
| `mmoderwell/search-materials-project-materials` | MP search by formula, chemistry, symmetry | free |
| `mmoderwell/resolve-a-material-query` | MP id / formula / system → canonical MP entries | free |
| `mmoderwell/fetch-a-material-cif-from-materials-project` | MP id → CIF file asset | free |
| `mmoderwell/get-the-cif-of-a-materials-project-alexandria-gnome-or-cod-material` | MP, Alexandria, GNoME or COD id → CIF with license header | free |
| `mmoderwell/generate-a-ggen-system-report` | best structures known for a system across the GGen DB (published + generated) | free |
| `mmoderwell/export-candidate-cifs` | stored GGen candidates for a system as CIFs | free |

## Generate (explore)

| Route | What | Tier |
|---|---|---|
| `mmoderwell/explore-a-chemical-system-with-ggen` | generate + relax across stoichiometries of a system, rank by hull | minutes–hours, async |
| `mmoderwell/scout-candidate-elements-with-ggen` | screen elements X for a template like Fe-Bi-{X} | minutes, async |
| `mmoderwell/generate-a-crystal-structure-using-ggen` | one structure for an exact formula | minutes |
| `mmoderwell/generate-crystal-structures-with-mattergen` (+ `-and-magnetic-density-conditioning`, `-magnetic-density-and-hhi-score-conditioning`) | MatterGen batch for a system, optionally property-conditioned | minutes |
| `mmoderwell/generate-a-crystal-structure-with-chemeleon`, `…-from-text-descriptions` | Chemeleon by composition or by text | minutes |
| `mmoderwell/de-novo-generation-with-omatg`, `…crystal-structure-prediction-with-omatg` | OMatG | minutes |
| `will/generate-a-crystal-structure-with-gpsk-01` / `-05` / `-300` | GPSK diffusion transformers | minutes |

GGen explore body: `system` ("Fe-Co-B"), `max_atoms`, `num_trials`,
`e_above_hull`, `crystal_systems`, `min_fraction` / `max_fraction`,
`include_phase_diagram`. Outputs: candidate CIFs, report, phase diagram,
summary. Generated cells are relaxed, and relaxation can change their
symmetry. Read the space group from the result, not the request.

## Build (change a known structure on purpose)

| Route | What | Tier |
|---|---|---|
| `mmoderwell/generate-point-defect-candidates` | vacancies, substitutions, interstitials from a host | minutes, async |
| `mmoderwell/create-an-interstitially-doped-structure` | interstitial dopant at a concentration | seconds |
| `hermes/expand-a-crystal-structure-into-a-supercell` | supercell CIF | seconds |
| `mmoderwell/generate-surface-slabs` | slabs from bulk | seconds |

For exact edits (one site, a specific ordering, strain), build the CIF
yourself with pymatgen in the `materials` pyenv
(`~/.pyenv/versions/materials/bin/python`). Keep the script in the campaign
folder and upload with `create_file(file_content_text=<cif>, file_name=...)`.
Then relax it with a route before measuring anything. Record the build as a
`build` experiment with the parent's asset id and the operation.

## Relax

| Route | What | Tier |
|---|---|---|
| `mmoderwell/relax-a-crystal-structure` | ML-potential relax (choose the potential) | cheap |
| `mmoderwell/structure-relaxation-via-nequip-oam-xl` | NequIP-OAM-XL relax | cheap |
| `mmoderwell/dft-structure-relaxation` | ABACUS variable-cell relax | expensive |

## Prophet: fast magnetic screening

Prophet is our ML potential with a spin-aware head (service
`mmoderwell/prophet`). Its Heisenberg-form exchange layer means the Curie
estimate reads Jij straight from the model, not from a fit. All five routes
are cheap and synchronous, and take one CIF as input `file`. Use them to
screen magnetic order, Js and Tc on every plausible candidate, then spend DFT
only on the ones that survive.

| Route | What | Tier | Notes |
|---|---|---|---|
| `mmoderwell/relax-a-structure-and-return-a-magcif-with-ground-state-moments` | Prophet-OAME-MBD relax (positions and, by default, the cell), then the collinear order screen at the relaxed geometry | cheap | outputs a P1 magCIF with the lowest ordering's moments (a new version of the input file). Use it to seed DFT or the Curie route. Body: `fmax` 0.05, `max_steps` 300, `relax_cell` true |
| `mmoderwell/screen-collinear-magnetic-order-and-saturation-magnetization` | ranks FM, two-sublattice AFM and ferrimagnetic seeds by energy; gives local moments, Ms and the ground-state net moment | cheap | a short collinear menu, not a magnetic space-group search. An AFM winner means ground-state Js ≈ 0, even if the FM Ms looks high |
| `mmoderwell/estimate-the-curie-temperature-from-prophet-spin-exchange` | Jij from Prophet-Spin in a supercell → mean-field Tc and classical Heisenberg Monte Carlo Tc, with exchange shells | cheap | feed it the relax route's magCIF. Mean field overestimates, so quote the Monte Carlo value. `mean_field_ordering_overlap` near 0 means the couplings prefer a different order than the input. Sites under 0.5 μB are dropped. `include_dataset: true` saves the M(T) curve |
| `mmoderwell/predict-spin-dependent-energy-forces-and-stress` | Prophet-Spin energy, forces and stress for a given magnetic state | cheap | pass `magnetic_moments` (collinear, μB per site in CIF order) or `noncollinear_moments`, or let it predict from an FM seed. Use it to compare orderings you choose yourself |
| `mmoderwell/predict-energy-forces-and-stress` | Prophet-OAME-MBD single point (Matbench Discovery checkpoint) | cheap | non-magnetic energy, e.g. for a quick hull comparison |

Typical chain: relax → magCIF → Curie route, with the order screen read off
the relax response. The magnet scorer
(`mmoderwell/score-a-rare-earth-free-permanent-magnet-candidate`) runs a
similar Prophet screen inside one score.

Before trusting Prophet in a new chemistry, read its known biases in
`notebook/learnings.md` (L020–L023 as of 2026-10-31: Js runs high,
misclassified orderings on known magnets, Curie error and what Heisenberg Tc
can't see). Calibrate against a measured compound in the same family.

For oxides, nitrides and other ligand-bridged (M–X–M) magnets, also try
`mmoderwell/magnetic-moment-ms-for-ligand-bridged-magnets-oxides-nitrides-mxm`
(MCGCNN, cheap; input `CIF file`). It's built for superexchange geometry
where CHGNet and Prophet are weaker.

## Measure

| Property | Route | Tier | Notes |
|---|---|---|---|
| Structure facts | `hermes/extract-crystal-structure-metadata` | free | formula, space group, lattice, volume |
| e_hull | `mmoderwell/calculate-energy-above-the-convex-hull` | cheap | input `structure`; `allow_unrelaxed: true` re-relaxes with Orb |
| Dynamic stability | `mmoderwell/check-phonon-stability` | cheap | input `file`; imaginary modes → unstable |
| Phonon bands | `mmoderwell/calculate-phonon-dispersion-and-band-structure` | cheap | |
| Raw-material cost | `hermes/estimate-raw-material-cost-per-kg` | free | |
| Moments, Ms (ML) | `hermes/estimate-magnetic-moments-and-ms-from-a-cif` | cheap | CHGNet, assumes collinear FM |
| Curie T (ML) | `hermes/predict-curie-temperature-from-a-cif` | cheap | CHGNet + CatBoost; see learnings on recall |
| Magnet score | `mmoderwell/score-a-rare-earth-free-permanent-magnet-candidate` | cheap | Orb relax + Prophet + hull + HHI, one number |
| Synthesis | `mmoderwell/synthesis-report-from-cif-file`, `…-from-composition` | cheap | |
| XRD | `mmoderwell/simulate-an-x-ray-diffraction-pattern` | free | |
| Property (ALIGNN) | `mmoderwell/predict-a-crystal-property-with-alignn` | cheap | list models first |
| Ground state | `mmoderwell/ground-state-scf` | expensive | energy, magnetization, site moments |
| Moments, Ms (DFT) | `mmoderwell/magnetic-moments` | expensive | |
| Magnetic order | `mmoderwell/magnetic-ordering-fm-vs-afm` | expensive | one SCF per ordering |
| Exchange Jij | `mmoderwell/exchange-couplings-jij` | expensive | |
| Curie T (DFT) | `mmoderwell/curie-temperature-tc` | expensive | Heisenberg Monte Carlo on DFT Jij |
| Anisotropy (MAE) | `mmoderwell/magnetic-anisotropy-energy` | expensive | rejects DFT-unrelaxed input by default: DFT-relax first; k-mesh convergence matters |
| DOS / PDOS / bands | `mmoderwell/density-of-states`, `…projected-density-of-states`, `…band-structure`, `…band-gap` | expensive | |
| Charge density, Γ orbitals | `mmoderwell/charge-density`, `mmoderwell/orbitals-at-gamma` | expensive | |
| Thermoelectrics | `mmoderwell/estimate-zt-and-key-thermoelectric-properties`, `…predict-seebeck-coefficient-and-band-gap`, `…estimate-minimum-lattice-thermal-conductivity` | expensive | |

## Habits

- Calibrate before trusting. Run a route on a known material with a measured
  value in the same chemistry, and record it as a `calibration` candidate.
  Route-vs-experiment offsets go in `learnings.md`.
- Treat a DFT number from a coarse setting as a sign, not a value. Rerun
  the result you'll act on at a converged setting before calling it a lead.
- Route-specific traps found in past work are in `notebook/learnings.md`
  under "Routes and measurement". Read them before the first expensive run
  in a session.
