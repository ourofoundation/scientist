# Instruments: Ouro routes

Every measurement comes from a route run on Ouro, so it has an action id,
inputs and outputs anyone can inspect. Call routes through the Ouro MCP tools,
using the **id** column below:
- `get_asset(id=<route id>, detail="full")` gives the input schema, price and
  execution mode. `get_asset` needs the UUID; an `entity/route` name returns
  404 there.
- `execute_route(route_id=<id>, input_assets={...}, body={...}, wait=false, notify="failure", org_id=..., team_id=...)`
  starts a run.
- `get_action(id=..., include_response=true)` gives the result.
- `list_my_actions` finds a run whose id you lost.

Send only the parameters you mean to change: defaults are tuned, and filling
in every field has broken runs before. To find a route not listed here,
`search_assets(asset_type="route", query=...)`. Ids and prices were checked
on 2026-10-31.

## Time and money

Two separate things:
- **Time.** Fast: seconds. Minutes. Hours: DFT. "Expensive" in the skill
  and in a budget means the DFT routes (the hours tier, marked **DFT**
  below).
- **Price.** `free` costs nothing. `$/s` is billed per second of compute
  at the rate shown. `paid` is pay-per-use at a price `get_asset` shows.
  A "cheap routes only" budget means no DFT routes, but free and paid
  short routes are fine within the dollar cap.

Runs paid by an organization don't report a cost back to the caller
(`get_action` and `list_usage` show nothing). Record
`cost_usd ≈ seconds × rate` in the ledger, with `"cost_estimated": true`.

## Licensing: non-commercial structures

GNoME records, and Materials Project records marked BY-NC, are CC BY-NC.
Any route may measure them, paid ones included (DFT, phonons, hull, relax):
that is research use (Matt, 2026-11-01). The license carries over to what you
derive from them, so record it in `candidates.md`, keep it in the files you
upload, and never put a price on a derived asset. Check `license` on anything
pulled with the materialize route, and on the matches the novelty route
returns.

## Structures in

| Route | id | What | Price |
|---|---|---|---|
| `mmoderwell/check-whether-a-crystal-structure-is-already-known` | `3dced825-cfea-4711-89a9-1d7998242001` | **novelty check**: match a CIF against MP, Alexandria, GNoME and COD. Returns known / new polymorph / new composition, plus `experimentally_known` (COD or MP's ICSD match) | free |
| `mmoderwell/search-materials-project-materials` | `c32019e9-92d8-4c95-ac3a-2e3dd029eb40` | MP search by formula, chemistry, symmetry | free |
| `mmoderwell/resolve-a-material-query` | `15b2a26b-7d93-4aef-9ccb-310f89d811df` | MP id / formula / system → canonical MP entries | free |
| `mmoderwell/fetch-a-material-cif-from-materials-project` | `11224ef4-70e9-45f3-9f98-dd18ab8e0d4b` | MP id → CIF file asset | free |
| `mmoderwell/get-the-cif-of-a-materials-project-alexandria-gnome-or-cod-material` | `e4812d4a-caae-4c11-a1a1-ff92af687d8d` | MP / Alexandria / GNoME / COD id → CIF with license header | free |
| `mmoderwell/export-candidate-cifs` | `fed98533-98c4-490c-b875-abbd118fb09c` | stored GGen candidates for a system as CIFs | free |
| `mmoderwell/generate-a-ggen-system-report` | `d10241ae-e66b-49a9-b8bf-ca1af66b8803` | state of a system across the GGen DB | free, but always publishes a post: prefer the novelty route and the hull route's phase diagram |

## Generate (explore)

| Route | id | What | Time | Price |
|---|---|---|---|---|
| `mmoderwell/explore-a-chemical-system-with-ggen` | `25945f7c-d83a-45ba-8c40-30b69d9a3df7` | generate + relax across stoichiometries, rank by hull | minutes–hours, async | $0.0012/s |
| `mmoderwell/scout-candidate-elements-with-ggen` | `4623c347-ec8b-4516-81eb-c989cbf5c940` | screen elements X for a template like Fe-Bi-{X} | minutes, async | $0.0012/s |
| `mmoderwell/generate-a-crystal-structure-using-ggen` | `454b4729-839e-494a-9ea5-ae3eab07570b` | one structure for an exact formula | minutes | $0.0012/s |
| `mmoderwell/generate-crystal-structures-with-mattergen` | `9f920d84-2a88-4072-9ba5-3b4ce9ec0fe7` | MatterGen batch for a system (property-conditioned variants exist; search "MatterGen") | minutes | paid |
| `mmoderwell/generate-a-crystal-structure-with-chemeleon` | `a3088687-0fce-45fd-8dbb-952be5fc4d1c` | Chemeleon by composition | minutes | paid |
| `mmoderwell/de-novo-generation-with-omatg` | `c76d61d5-e5bb-4e6c-b5cc-df176ece7892` | OMatG de novo | minutes | paid |
| `will/generate-a-crystal-structure-with-gpsk-01` | `48a995e2-79c6-491c-8c6a-bcf1a8a16cf5` | GPSK diffusion transformer (-05 and -300 variants exist) | minutes | paid |

GGen explore body: `system` ("Fe-Co-B"), `max_atoms`, `num_trials`,
`e_above_hull`, `crystal_systems`, `min_fraction` / `max_fraction`,
`include_phase_diagram`. Outputs: candidate CIFs, report, phase diagram,
summary. Generated cells are relaxed, and relaxation can change their
symmetry. Read the space group from the result, not the request.

## Build (change a known structure on purpose)

| Route | id | What | Price |
|---|---|---|---|
| `mmoderwell/generate-point-defect-candidates` | `a8420297-143c-4cd3-8cc0-9a23db6b9efd` | vacancies, substitutions, interstitials from a host (async) | free |
| `mmoderwell/create-an-interstitially-doped-structure` | `be1c9062-9c73-4c56-a91f-6fbbbcad6d4f` | interstitial dopant at a concentration | paid |
| `hermes/expand-a-crystal-structure-into-a-supercell` | `0bd98417-bc11-43e6-a67e-f301d57035cc` | supercell CIF | paid |
| `mmoderwell/generate-surface-slabs` | `6a5e6e41-5b06-4e10-84b0-d9e05b8d7713` | slabs from bulk | free |

For exact edits (one site, a specific ordering, strain), build the CIF
yourself with pymatgen in the `materials` pyenv
(`~/.pyenv/versions/materials/bin/python`). Keep the script in the campaign's
`work/` folder and upload with `create_file(file_content_text=<cif>, file_name=...)`.
Relax it with a route before measuring anything. Record the build as a
`build` experiment with the parent's asset id and the operation.

## Relax

| Route | id | What | Price |
|---|---|---|---|
| `mmoderwell/relax-a-structure-and-return-a-magcif-with-ground-state-moments` | `b9593f22-3520-4439-ac88-f0931de9fd19` | Prophet relax + magnetic order (see Prophet) | free |
| `mmoderwell/relax-a-crystal-structure` | `d040d3b6-faad-40cf-9d7c-999a5c769ed8` | ML-potential relax (choose the potential), async | paid |
| `mmoderwell/structure-relaxation-via-nequip-oam-xl` | `cac67dd7-4ca4-40bf-aa8a-51c51692681f` | NequIP-OAM-XL relax | $0.0009/s |
| `mmoderwell/dft-structure-relaxation` | `d180f520-fc88-48b7-9eca-3d123f5cf680` | **DFT** ABACUS variable-cell relax | $0.0006/s |

## Prophet: fast magnetic screening

Prophet is our ML potential with a spin-aware head (service
`mmoderwell/prophet`). Its Heisenberg-form exchange layer means the Curie
estimate reads Jij straight from the model, not from a fit. All five routes
are free and synchronous, and take one CIF as input `file`. Use them to
screen magnetic order, Js and Tc on every plausible candidate, then spend DFT
only on the ones that survive.

| Route | id | What | Notes |
|---|---|---|---|
| `mmoderwell/relax-a-structure-and-return-a-magcif-with-ground-state-moments` | `b9593f22-3520-4439-ac88-f0931de9fd19` | Prophet-OAME-MBD relax (positions and, by default, the cell), then the collinear order screen at the relaxed geometry | outputs a P1 magCIF with the lowest ordering's moments (a new version of the input file). Use it to seed DFT or the Curie route. Body: `fmax` 0.05, `max_steps` 300, `relax_cell` true |
| `mmoderwell/screen-collinear-magnetic-order-and-saturation-magnetization` | `a0f8fd1c-faab-4b08-aff1-6c2e79d4670e` | ranks FM, two-sublattice AFM and ferrimagnetic seeds by energy; gives local moments, Ms and the ground-state net moment | a short collinear menu, not a magnetic space-group search. An AFM winner means ground-state Js ≈ 0, even if the FM Ms looks high |
| `mmoderwell/estimate-the-curie-temperature-from-prophet-spin-exchange` | `fc0b4cd4-baaf-480d-8846-447ff172063f` | Jij from Prophet-Spin in a supercell → mean-field Tc and classical Heisenberg Monte Carlo Tc, with exchange shells | feed it the relax route's magCIF. Mean field overestimates, so quote the Monte Carlo value. `mean_field_ordering_overlap` near 0 means the couplings prefer a different order than the input. Sites under 0.5 μB are dropped. `include_dataset: true` saves the M(T) curve |
| `mmoderwell/predict-spin-dependent-energy-forces-and-stress` | `9e09a76f-05aa-4c88-9e59-1f2cb1fdde08` | Prophet-Spin energy, forces and stress for a given magnetic state | pass `magnetic_moments` (collinear, μB per site in CIF order) or `noncollinear_moments`, or let it predict from an FM seed. Use it to compare orderings you choose yourself |
| `mmoderwell/predict-energy-forces-and-stress` | `b575b503-a2f0-4e55-8279-037dcdb381d2` | Prophet-OAME-MBD single point (Matbench Discovery checkpoint) | non-magnetic energy, e.g. for a quick energy comparison |

Typical chain: relax → magCIF → Curie route, with the order screen read off
the relax response. The magnet scorer (below) runs a similar Prophet screen
inside one score.

Before trusting Prophet in a new chemistry, read its known biases in
`lab/notebook/learnings.md` (L020–L023 as of 2026-10-31: Js runs high,
misclassified orderings on known magnets, Curie error and what Heisenberg Tc
can't see). Calibrate against a measured compound in the same family.

## Measure

| Property | Route | id | Price | Notes |
|---|---|---|---|---|
| Structure facts | `hermes/extract-crystal-structure-metadata` | `056f8ca8-8e0b-4a51-b365-f4b59395b2aa` | free | formula, space group, lattice, volume |
| e_hull | `mmoderwell/calculate-energy-above-the-convex-hull` | `75fe7f4b-22f5-4883-aa1d-378fd42da47c` | paid, async | input `structure`, already relaxed (max force ≤ 0.05 eV/Å) unless `allow_unrelaxed: true`, which relaxes with Orb first. `energy_surface` defaults to Orb v3 (every reference scored by Orb). The PBE option uses published PBE references but still scores **your** cell with Orb, so it can differ from the Orb surface by ~20 meV/atom. Record which surface, and compare like with like. `include_phase_diagram: true` gives the neighbor phases without a post |
| Dynamic stability | `mmoderwell/check-phonon-stability` | `69fc4538-ff58-42fd-a881-9ede1801a6f3` | $0.0009/s | input `file`; imaginary modes → unstable |
| Phonon bands | `mmoderwell/calculate-phonon-dispersion-and-band-structure` | `c4d7aa14-f5a7-496e-93a5-542e09482eb5` | paid, async | |
| Raw-material cost | `hermes/estimate-raw-material-cost-per-kg` | `9028cd75-e8ee-4f0a-bf41-c360f64cb8ad` | free | |
| Magnet score | `mmoderwell/score-a-rare-earth-free-permanent-magnet-candidate` | `0c0b4692-5d3e-4369-be15-a9174511897c` | free, async | Orb relax + Prophet + hull + HHI, one number |
| Moments, Ms (ML) | `hermes/estimate-magnetic-moments-and-ms-from-a-cif` | `d1fdf6d1-2b35-47af-956f-1b83c2fca036` | paid | CHGNet, unsigned moments, assumes collinear FM |
| Moments, Ms (ligand-bridged) | `mmoderwell/magnetic-moment-ms-for-ligand-bridged-magnets-oxides-nitrides-mxm` | `091f9f57-f11d-40d0-9fe4-590c05a756e7` | free | MCGCNN for oxides, nitrides, M–X–M exchange; input `CIF file` |
| Curie T (ML) | `hermes/predict-curie-temperature-from-a-cif` | `daf42af4-a3e4-4f9e-af65-6ecaafc26334` | paid | CHGNet + CatBoost; see learnings on recall |
| Property (ALIGNN) | `mmoderwell/predict-a-crystal-property-with-alignn` | `de2d96c5-b638-46c2-82f5-cae72aa4383c` | free | list models first |
| Synthesis | `mmoderwell/synthesis-report-from-cif-file` | `0135694c-823f-4ec6-8623-29c76ac4d7b9` | free | |
| XRD | `mmoderwell/simulate-an-x-ray-diffraction-pattern` | `fcd0f1fe-d001-43a5-adf4-35d6d4563ae3` | free | |
| Ground state | `mmoderwell/ground-state-scf` | `e4ed9888-0c34-4a9d-a227-02fa9d7521e0` | **DFT** $0.0003/s | energy, magnetization, site moments |
| Moments, Ms (DFT) | `mmoderwell/magnetic-moments` | `0a23817e-af47-485a-9c56-5f2df0178b80` | **DFT** $0.0003/s | |
| Magnetic order | `mmoderwell/magnetic-ordering-fm-vs-afm` | `67393536-da30-4983-9c21-37c9fabbb296` | **DFT** $0.0012/s | one SCF per ordering |
| Exchange Jij | `mmoderwell/exchange-couplings-jij` | `def3b73b-b790-4716-bb37-60b565342d48` | **DFT** $0.0003/s | |
| Curie T (DFT) | `mmoderwell/curie-temperature-tc` | `29fd80eb-7cf5-4895-a349-4e9864407567` | **DFT** $0.0003/s | Heisenberg Monte Carlo on DFT Jij |
| Anisotropy (MAE) | `mmoderwell/magnetic-anisotropy-energy` | `331d9faf-4b44-4679-a958-5a9a816036e1` | **DFT** $0.0006/s | rejects DFT-unrelaxed input by default, so DFT-relax first; k-mesh convergence matters |
| DOS | `mmoderwell/density-of-states` | `3fa25076-f993-4a57-90dd-799f4d2b6e1b` | **DFT** $0.0003/s | PDOS, bands, band gap routes exist too (search) |
| Charge density | `mmoderwell/charge-density` | `2941faa7-deb2-48dd-9bc1-7efb7f28ebaf` | **DFT** $0.0003/s | |
| Γ orbitals | `mmoderwell/orbitals-at-gamma` | `d1e8f4a1-9adb-4c41-93dd-0f387211ae53` | **DFT** $0.0003/s | |
| Thermoelectrics | `mmoderwell/estimate-zt-and-key-thermoelectric-properties` | `a8e05903-b077-4e5f-8de5-bdbeac459db0` | **DFT** $0.0012/s | Seebeck and κ_min routes exist too (search) |

## Habits

- Calibrate before trusting. Run a route on a known material with a measured
  value in the same chemistry, and record it as a `calibration` candidate.
  Route-vs-experiment offsets go in `learnings.md`.
- Treat a DFT number from a coarse setting as a sign, not a value. Rerun
  the result you'll act on at a converged setting before calling it a lead.
- Route-specific traps found in past work are in `lab/notebook/learnings.md`
  under "Routes and measurement". Read them before the first DFT run in a
  session.
