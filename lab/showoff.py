"""Render a rotating crystal structure as a 16:9 MP4 for posting on X.

    python lab/showoff.py structure.cif
    python lab/showoff.py structure.cif -o out.mp4 --caption "E_hull = 0 meV/atom" --repeat 2
    python lab/showoff.py structure.cif --hull hull.json

--hull shows the candidate on the convex hull of its system, beside the crystal.
hull.json gives formation energies in eV/atom (elements default to 0):

    {"surface": "Orb v3",
     "candidate_e_form": -0.312,
     "phases": [{"formula": "Fe2P", "e_form": -0.40, "id": "mp-778"}, {"formula": "FeP", "e_form": -0.38}]}

A phase's optional "id" (mp-..., agm..., a COD number) is printed under its label, so a
viewer can look the known phase up. "verdict" and "rivals" (a list of formulas) replace the
headline the script would compute, for when the candidate's margin comes from a check
against a different set of phases than the ones drawn.

Binary and ternary systems get the hull itself; larger systems get an energy ladder
against the phases the candidate would otherwise decompose into.

--frame 6.5 writes a PNG of that moment instead of the video, to check a layout.

--dos dos.json adds a second act: after the hull, the right panel turns into the electronic
density of states of the candidate against its parent, from the density-of-states route's
.dos files (paths relative to dos.json):

    {"headline": "Boron pulls the Pt d band down by 1 eV",
     "subline": "Pt d-band centre -2.9 -> -3.6 eV",
     "footnote": "PBE (ABACUS, DZP), no spin-orbit",
     "window": [-6, 2.5],
     "curves": [{"file": "Pt3In2.dos", "atoms": 20, "label": "Pt3In2 (parent)", "role": "parent"},
                {"file": "In4BPt6.dos", "atoms": 22, "label": "In4BPt6", "role": "candidate"}]}

The crystal is drawn by the Ouro frontend's own crystal viewer code (showoff_web/: the
frontend's scene and polyhedra builders, colours, ball sizes and lights, in three.js),
rendered in headless Chromium. It needs the frontend checkout (FRONTEND, default
../frontend beside this repo) with its node_modules, and node on PATH. --viewer mpl falls
back to the old matplotlib drawing. --bonds B-Pt,Pt-Pt keeps only those bonds (and so
those polyhedra); the default bonds every pair within 1.15 x the sum of covalent radii.

Needs ase, pymatgen, matplotlib and ffmpeg on PATH.
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from ase.data import atomic_numbers, covalent_radii
from ase.data.colors import jmol_colors
from ase import Atoms
from ase.io import read
from ase.neighborlist import natural_cutoffs, neighbor_list
from matplotlib.animation import FFMpegWriter
from matplotlib.patches import Circle

# Colours for each theme. render() reads these module globals, so main() swaps them in.
THEMES = {
    "dark": dict(BG="#0b0d12", FG="#e8eaf0", DIM="#8a90a0", ACCENT="#ffd166", RIVAL="#ff8fab",
                 LINE="#4a5060", NEW_LINE="#d0d4dc", BOX=(0.45, 0.5, 0.6), UNIT=(0.85, 0.88, 0.95)),
    "light": dict(BG="#ffffff", FG="#1c1f26", DIM="#6b7280", ACCENT="#c27c00", RIVAL="#d6336c",
                  LINE="#c4c8d0", NEW_LINE="#2a2e36", BOX=(0.55, 0.58, 0.65), UNIT=(0.15, 0.17, 0.22)),
}
globals().update(THEMES["dark"])


def expand_boundary(atoms, repeat):
    """Supercell plus the periodic images that sit on the far faces, so the box looks closed."""
    sc = atoms.repeat(repeat)
    frac = sc.get_scaled_positions(wrap=True)
    pos, nums = [frac], [sc.numbers]
    tol = 1e-3
    for shift in np.array(np.meshgrid([0, 1], [0, 1], [0, 1])).T.reshape(-1, 3)[1:]:
        mask = np.all((frac < tol) | (shift == 0), axis=1)
        if mask.any():
            pos.append(frac[mask] + shift)
            nums.append(sc.numbers[mask])
    frac = np.vstack(pos)
    return frac @ sc.cell.array, np.concatenate(nums), sc


# Everything below runs on a 7 s timeline, stretched to --seconds.
TIMELINE = 7.0
# The unit cell builds into the supercell one axis at a time: (start, duration) for a, b, c.
GROW = [(1.0, 0.6), (1.6, 0.6), (2.2, 0.7)]
# Camera shots: when the move starts, how long it takes, where it lands (yaw, pitch in degrees,
# zoom on top of the automatic framing) and how it gets there. A slow yaw drift runs underneath.
SHOTS = [
    (0.0, 0.0, (30, 15, 1.0), "hold"),
    (3.0, 1.4, (90, 88, 1.0), "soft"),    # look straight down c at the supercell
    (4.6, 1.6, (200, 12, 1.7), "soft"),   # swing back to the side and push in
    (6.2, 0.8, (215, 15, 1.35), "inout"), # ease back a little to end
]
DRIFT = 6.0  # degrees per second
DOS_START = 7.0  # where the density-of-states act takes over the right panel (timeline seconds)
PUSH = 1.0  # scales the push-in; main() sets it from --push


def soft(x, c1=0.5):
    """Ease out with a slight overshoot."""
    x = np.clip(x, 0, 1)
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_out(x):
    """Ease out, no overshoot."""
    x = np.clip(x, 0, 1)
    return 1 - (1 - x) ** 3


def inout(x):
    x = np.clip(x, 0, 1)
    return np.where(x < 0.5, 4 * x**3, 1 - (-2 * x + 2) ** 3 / 2)


EASE = {"soft": soft, "inout": lambda x: float(inout(x)), "hold": lambda x: 1.0}


def camera(u, t):
    """(yaw, pitch, zoom) at timeline position u (real time t drives the drift)."""
    view = np.array(SHOTS[0][2], float)
    for start, dur, target, ease in SHOTS[1:]:
        if u <= start:
            break
        x = min((u - start) / dur, 1.0)
        view = view + (np.array(target, float) - view) * EASE[ease](x)
    yaw, pitch, zoom = view
    zoom = 1 + (zoom - 1) * PUSH
    return np.radians(yaw + DRIFT * t), np.radians(pitch), zoom


def box(u, reps):
    """Size of the built-up box along a, b, c, in unit cells, at timeline position u."""
    return np.array([1 + (r - 1) * float(inout((u - s) / d)) for r, (s, d) in zip(reps, GROW)])


# ----------------------------------------------------------------------------------------
# The crystal, drawn by the frontend's viewer code (showoff_web/)
# ----------------------------------------------------------------------------------------

WEB = Path(__file__).with_name("showoff_web")
FRONTEND = Path(__import__("os").environ.get("FRONTEND", Path(__file__).resolve().parents[2] / "frontend"))


def crystal_view(cif, bonds=None, scale=1.15):
    """The structure as the platform hands it to the frontend viewer (CrystalView): lattice,
    sites, and bonds as [i, j, da, db, dc]. `bonds` like "B-Pt,Pt-Pt" keeps only those pairs."""
    from pymatgen.core import Structure
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    s = Structure.from_file(cif)
    pairs = None
    if bonds:
        pairs = {tuple(sorted(p.split("-"))) for p in bonds.split(",")}
    rc = {el.symbol: covalent_radii[atomic_numbers[el.symbol]] for el in s.composition.elements}
    reach = scale * 2 * max(rc.values())
    i_, j_, images, dist = s.get_neighbor_list(reach)
    out = []
    for i, j, img, d in zip(i_, j_, images, dist):
        a, b = s[int(i)].specie.symbol, s[int(j)].specie.symbol
        if pairs is not None and tuple(sorted((a, b))) not in pairs:
            continue
        if d > scale * (rc[a] + rc[b]):
            continue
        img = tuple(int(round(x)) for x in img)
        # One entry per bond: the viewer adds the reverse direction itself.
        if i < j or (i == j and img > (0, 0, 0)):
            out.append([int(i), int(j), *img])
    try:
        sga = SpacegroupAnalyzer(s, symprec=0.1)
        sg = dict(symbol=sga.get_space_group_symbol(), number=sga.get_space_group_number(),
                  crystal_system=sga.get_crystal_system())
    except Exception:
        sg = None
    lat = s.lattice
    return dict(
        version=2, kind="crystal", periodic=True, formula=s.composition.reduced_formula, space_group=sg,
        lattice=dict(matrix=lat.matrix.tolist(), a=lat.a, b=lat.b, c=lat.c, alpha=lat.alpha, beta=lat.beta,
                     gamma=lat.gamma, volume=lat.volume),
        sites=[dict(species=[[site.specie.symbol, 1.0]], abc=site.frac_coords.tolist()) for site in s],
        bonds=out, bonding="cutoff",
    )


def web_frames(view, reps, states, width, height, workdir, polyhedra=True):
    """Render the crystal for each (u, yaw, pitch, zoom) state; returns the PNG paths."""
    import json
    import os
    import subprocess

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    bundle = workdir / "scene.js"
    env = {**os.environ, "NODE_PATH": str(FRONTEND / "node_modules"), "FRONTEND": str(FRONTEND)}
    subprocess.run([str(FRONTEND / "node_modules/.bin/esbuild"), str(WEB / "scene.ts"), "--bundle", "--format=iife",
                    f"--alias:frontend-lib={FRONTEND / 'lib'}", f"--outfile={bundle}", "--log-level=warning"],
                   check=True, env=env)
    job = dict(
        bundle=str(bundle),
        setup=dict(view=view, tiling=[int(r) for r in reps], grow=[list(g) for g in GROW], polyhedra=polyhedra,
                   width=int(width), height=int(height), background=BG,
                   box=matplotlib.colors.to_hex(BOX), unit=matplotlib.colors.to_hex(UNIT)),
        frames=[dict(u=float(u), yaw=float(y), pitch=float(p), zoom=float(z)) for u, y, p, z in states],
    )
    (workdir / "job.json").write_text(json.dumps(job))
    frames = workdir / "frames"
    for old in frames.glob("*.png"):
        old.unlink()
    subprocess.run(["node", str(WEB / "render.mjs"), str(workdir / "job.json"), str(frames)], check=True, env=env)
    return sorted(frames.glob("*.png"))


# ----------------------------------------------------------------------------------------
# Second act: the electronic density of states
# ----------------------------------------------------------------------------------------

def dos_scene(spec, base):
    """Load the curves once; return draw(ax, tau) for the density-of-states panel."""
    import json

    lo, hi = spec.get("window", [-6, 2.5])
    smear = spec.get("smear", 0.08)
    curves = []
    for c in spec["curves"]:
        d = json.loads((Path(base) / c["file"]).read_text())
        e = np.array(d["energies"]) - d["fermi_energy"]
        y = np.array(d["total"][0]) / c["atoms"]
        # Gaussian broadening, so the curve reads at video size.
        de = e[1] - e[0]
        k = np.arange(-4 * smear, 4 * smear + de, de)
        g = np.exp(-0.5 * (k / smear) ** 2)
        y = np.convolve(y, g / g.sum(), mode="same")
        m = (e >= lo) & (e <= hi)
        curves.append(dict(e=e[m], y=y[m], label=c["label"], role=c.get("role", "parent")))
    ymax = max(c["y"].max() for c in curves) * 1.12

    def fade(tau, start, dur=0.4):
        return float(np.clip((tau - start) / dur, 0, 1))

    def draw(ax, tau):
        ax.clear()
        ax.set_facecolor(BG)
        a0 = fade(tau, 0.0, 0.3)
        for name, sp in ax.spines.items():
            sp.set_visible(name in ("bottom", "left"))
            sp.set_color(DIM)
            sp.set_alpha(a0)
        ax.tick_params(colors=DIM, labelsize=13)
        ax.set_xlim(lo, hi)
        ax.set_ylim(0, ymax)
        ax.set_xlabel("energy relative to the Fermi level (eV)", color=DIM, fontsize=14)
        ax.set_ylabel("states / eV / atom", color=DIM, fontsize=14)
        # The Fermi level.
        ax.axvline(0, color=FG, lw=1.2, ls=(0, (4, 3)), alpha=0.6 * fade(tau, 0.3))
        ax.text(-0.06, ymax * 0.98, "E$_F$", color=FG, fontsize=14, ha="right", va="top", alpha=fade(tau, 0.3))
        for n, c in enumerate(curves):
            # Each curve sweeps in from the left, the parent first.
            start = 0.2 + 0.7 * n
            x_end = lo + (hi - lo) * float(ease_out((tau - start) / 1.0))
            if tau <= start:
                continue
            m = c["e"] <= x_end
            cand = c["role"] == "candidate"
            col = ACCENT if cand else LINE
            ax.fill_between(c["e"][m], 0, c["y"][m], color=col, alpha=0.1 if cand else 0.28, lw=0, zorder=2 + n)
            ax.plot(c["e"][m], c["y"][m], color=ACCENT if cand else DIM, lw=2.6 if cand else 2.0, zorder=3 + n)
            ax.text(0.98, 0.84 - 0.08 * n, c["label"], transform=ax.transAxes, ha="right", va="top",
                    color=ACCENT if cand else DIM, fontsize=15, fontweight="bold" if cand else "normal",
                    alpha=fade(tau, start + 0.3))
        # Arrows marking what moved, e.g. a band edge: {"from": [x, y], "to": [x, y], "text": ...}.
        for k, arr in enumerate(spec.get("arrows", [])):
            g = float(ease_out((tau - 1.7 - 0.2 * k) / 0.6))
            if g <= 0:
                continue
            (x0, y0), (x1, y1) = arr["from"], arr["to"]
            ax.annotate("", xy=(x0 + (x1 - x0) * g, y0 + (y1 - y0) * g), xytext=(x0, y0),
                        arrowprops=dict(arrowstyle="-|>", color=FG, lw=2, mutation_scale=18), zorder=8)
            if arr.get("text"):
                ax.text((x0 + x1) / 2, max(y0, y1) + 0.04 * ymax, arr["text"], ha="center", va="bottom",
                        color=FG, fontsize=13, alpha=fade(tau, 1.9 + 0.2 * k), zorder=8,
                        bbox=dict(fc=BG, ec="none", alpha=0.7, pad=1))
        a = fade(tau, 1.9)
        if spec.get("headline"):
            ax.text(0.5, 1.12, spec["headline"], transform=ax.transAxes, ha="center", va="bottom",
                    color=ACCENT, fontsize=20, fontweight="bold", alpha=a)
        if spec.get("subline"):
            ax.text(0.5, 1.05, spec["subline"], transform=ax.transAxes, ha="center", va="bottom",
                    color=FG, fontsize=16, alpha=fade(tau, 2.2))
        if spec.get("footnote"):
            ax.text(0.5, -0.24, spec["footnote"], transform=ax.transAxes, ha="center", va="top",
                    color=DIM, fontsize=12, alpha=a0)

    return draw




def tex(formula):
    """Fe2P -> Fe$_2$P for matplotlib."""
    import re

    return re.sub(r"(\d+(?:\.\d+)?)", r"$_{\1}$", formula)


def hull_scene(spec, comp, name=None):
    """Build the hull data once; return draw(ax, tau) for the hull panel."""
    from pymatgen.analysis.phase_diagram import PDEntry, PhaseDiagram
    from pymatgen.core import Composition, Element

    comp = Composition(comp).reduced_composition
    name = name or comp.reduced_formula
    els = sorted(comp.elements, key=lambda e: e.X)
    others = [PDEntry(Composition(ph["formula"]), ph["e_form"] * Composition(ph["formula"]).num_atoms,
                      name=Composition(ph["formula"]).reduced_formula) for ph in spec.get("phases", [])]
    # Database id of each known phase, by reduced formula; the lowest-energy one wins a tie.
    ids = {}
    for ph in sorted(spec.get("phases", []), key=lambda ph: -ph["e_form"]):
        if ph.get("id"):
            ids[Composition(ph["formula"]).reduced_formula] = ph["id"]
    have = {e.composition.elements[0] for e in others if e.composition.is_element}
    others += [PDEntry(Composition(el.symbol), 0.0, name=el.symbol) for el in els if el not in have]
    cand = PDEntry(comp, spec["candidate_e_form"] * comp.num_atoms, name="__cand__")

    old = PhaseDiagram(others)
    decomp, delta = old.get_decomp_and_e_above_hull(cand, allow_negative=True)
    rivals = {e.name for e in decomp}
    if spec.get("rivals"):
        rivals = {Composition(f).reduced_formula for f in spec["rivals"]}
    pd = PhaseDiagram(others + [cand])
    on_hull = delta <= 1e-6
    verdict = spec.get("verdict") or (
        f"{abs(delta) * 1000:.0f} meV/atom below the known hull" if delta < -1e-6
        else "on the hull" if on_hull else f"{delta * 1000:.0f} meV/atom above the hull")
    surface = spec.get("surface", "")
    n = len(els)

    def frac(c):
        return np.array([Composition(c).get_atomic_fraction(e) for e in els])

    def xy(c):
        f = frac(c)
        if n == 2:
            return np.array([f[1], 0.0])
        return np.array([f[1] + f[2] / 2, f[2] * np.sqrt(3) / 2])

    def ef(e):
        return pd.get_form_energy_per_atom(e)

    def label(e):
        return "NEW" if e.name == "__cand__" else tex(e.name)

    def tag(ax, x, y, e, col, size, alpha, dx=0, dy=0, ha="center", va="center", z=5):
        """A known phase's formula at (x, y) shifted by (dx, dy) points, its database id
        in small type under it."""
        box = dict(fc=BG, ec="none", alpha=0.75 * alpha, pad=1)
        text = ax.annotate(label(e), (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va,
                           color=col, fontsize=size, alpha=alpha, zorder=z, bbox=box)
        pid = ids.get(e.name)
        if pid:
            # On the formula's line, on its far side from the point; under it when centred.
            where = {"right": ((0, 0.5), (-5, 0), "right", "center"),
                     "left": ((1, 0.5), (5, 0), "left", "center"),
                     "center": ((0.5, 0), (0, -1), "center", "top")}[ha]
            ax.annotate(pid, where[0], xycoords=text, xytext=where[1], textcoords="offset points",
                        ha=where[2], va=where[3], color=DIM, fontsize=size * 0.68, alpha=alpha, zorder=z,
                        family="monospace", bbox=box)

    def style(ax):
        ax.clear()
        ax.set_facecolor(BG)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(colors=DIM, labelsize=14)

    def pop(tau, start, dur=0.6):
        return float(ease_out((tau - start) / dur))

    def fade(tau, start, dur=0.4):
        return float(np.clip((tau - start) / dur, 0, 1))

    def headline(ax, tau):
        a = fade(tau, 2.3)
        if a > 0:
            ax.text(0.5, 1.12, verdict, transform=ax.transAxes, ha="center", va="bottom",
                    color=ACCENT, fontsize=22, fontweight="bold", alpha=a)
            if rivals:
                ax.text(0.5, 1.06, "vs  " + "  +  ".join(tex(r) for r in sorted(rivals)),
                        transform=ax.transAxes, ha="center", va="bottom", color=FG, fontsize=16, alpha=a)
        if surface:
            ax.text(0.5, -0.08, f"energies: {surface}", transform=ax.transAxes, ha="center", va="top",
                    color=DIM, fontsize=12, alpha=fade(tau, 0.3), linespacing=1.5)

    def draw_binary(ax, tau):
        style(ax)
        old_pts = sorted(old.stable_entries, key=lambda e: xy(e.composition)[0])
        xs = np.array([xy(e.composition)[0] for e in old_pts])
        ys = np.array([old.get_form_energy_per_atom(e) for e in old_pts])
        ymin = min(ys.min(), ef(cand)) * 1.3 - 0.02
        ax.set_xlim(-0.05, 1.05)
        ax.set_ylim(ymin, 0.1 - 0.2 * ymin)
        ax.axhline(0, color=DIM, lw=1, alpha=0.4)
        ax.set_xticks([0, 1], [els[0].symbol, els[1].symbol], fontsize=22, color=FG)
        ax.set_ylabel("formation energy (eV/atom)", color=DIM, fontsize=15)
        landed = fade(tau, 2.1, 0.5)
        # The known hull draws left to right, then dims once the candidate lands below it.
        xr = float(np.clip(tau / 1.1, 0, 1))
        lx, ly = [xs[0]], [ys[0]]
        for i in range(1, len(xs)):
            if xs[i] <= xr:
                lx.append(xs[i]); ly.append(ys[i])
            else:
                f = (xr - xs[i - 1]) / (xs[i] - xs[i - 1])
                lx.append(xr); ly.append(ys[i - 1] + f * (ys[i] - ys[i - 1]))
                break
        ax.plot(lx, ly, color=LINE, lw=3, alpha=1 - 0.6 * landed * on_hull,
                ls="--" if landed > 0.5 and on_hull else "-", zorder=2)
        # The new hull segments through the candidate.
        if on_hull and landed > 0:
            new_pts = sorted(pd.stable_entries, key=lambda e: xy(e.composition)[0])
            k = [e.name for e in new_pts].index("__cand__")
            cx, cy = xy(cand.composition)[0], ef(cand)
            for nb in (new_pts[k - 1], new_pts[k + 1]):
                nx, ny = xy(nb.composition)[0], ef(nb)
                ax.plot([cx, cx + (nx - cx) * landed], [cy, cy + (ny - cy) * landed],
                        color=NEW_LINE, lw=3, zorder=3)
        for e in old.unstable_entries:
            ax.scatter([xy(e.composition)[0]], [old.get_form_energy_per_atom(e)], s=60, color=DIM,
                       alpha=fade(tau, 0.6) * 0.5, zorder=3)
        for k, e in enumerate(old_pts):
            if e.composition.is_element:
                continue
            g = pop(tau, 0.3 + 0.12 * k)
            col = RIVAL if e.name in rivals else FG
            ax.scatter([xs[k]], [ys[k]], s=160 * g, color=col, zorder=4, edgecolor=BG, lw=1.5)
            tag(ax, xs[k], ys[k], e, col, 15, fade(tau, 0.6 + 0.12 * k), dy=-10, va="top")
        drop(ax, tau, xy(cand.composition)[0], ef(cand), 0.4 * abs(ymin))
        headline(ax, tau)

    def drop(ax, tau, x, y, height, side=-1):
        # Candidate star settles into place; its name hangs off the given side.
        u = np.clip((tau - 1.4) / 0.9, 0, 1)
        if u <= 0:
            return
        yy = y + height * (1 - float(soft(u)))
        ax.scatter([x], [yy], s=700, marker="*", color=ACCENT, edgecolor=BG, lw=1.5, zorder=6,
                   alpha=float(ease_out(u * 2)))
        ax.text(x + side * 0.03, yy + 0.05 * height / 0.4, tex(name), ha="right" if side < 0 else "left",
                va="bottom",
                color=ACCENT, fontsize=20, fontweight="bold", zorder=7, alpha=fade(tau, 1.9),
                bbox=dict(fc=BG, ec="none", alpha=0.75 * fade(tau, 1.9), pad=2))

    # Ternary labels: a phase on the bottom edge is labelled under its point; every other phase
    # gets a slot in a column that follows the nearer side edge, spread so no two overlap
    # (corner-rich systems put half a dozen phases within a few percent of each other).
    slots = {}
    if n == 3:
        H, GAP = 0.062, 0.1  # slot height and distance out from the edge
        side_of = {}
        for e in old.stable_entries:
            if e.composition.is_element:
                continue
            x, y = xy(e.composition)
            if y >= 0.01:
                side_of[e.name] = (-1 if x < 0.5 + 1e-6 else 1, y)
        for side in (-1, 1):
            names = sorted((nm for nm, (s, _) in side_of.items() if s == side), key=lambda nm: -side_of[nm][1])
            ys = [side_of[nm][1] for nm in names]
            for _ in range(200):  # push neighbours apart until each pair is a slot apart
                for i in range(len(ys) - 1):
                    over = H - (ys[i] - ys[i + 1])
                    if over > 0:
                        ys[i] += over / 2
                        ys[i + 1] -= over / 2
                ys = [min(max(v, 0.06), 0.84) for v in ys]
            for nm, ly in zip(names, ys):
                edge = ly / np.sqrt(3) if side < 0 else 1 - ly / np.sqrt(3)
                slots[nm] = (edge + side * GAP, ly, side)

    def draw_ternary(ax, tau):
        style(ax)
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlim(-0.32, 1.32); ax.set_ylim(-0.12, 1.0)
        ax.set_aspect("equal")
        tri = np.array([[0, 0], [1, 0], [0.5, np.sqrt(3) / 2], [0, 0]])
        a = fade(tau, 0)
        ax.plot(*tri.T, color=DIM, lw=1.5, alpha=a)
        for el, (x, y), va in zip(els, tri[:3], ("top", "top", "bottom")):
            ax.text(x, y + (-0.03 if va == "top" else 0.03), el.symbol, ha="center", va=va,
                    color=FG, fontsize=22, alpha=a)
        landed = fade(tau, 2.1, 0.5)

        def ties(diagram):
            out = set()
            for facet in diagram.facets:
                names = [diagram.qhull_entries[i] for i in facet]
                for p_ in names:
                    for q_ in names:
                        if p_.name < q_.name:
                            out.add((p_, q_))
            return sorted(out, key=lambda pq: (pq[0].name, pq[1].name))

        before = ties(old)
        after = [pq for pq in ties(pd) if "__cand__" in (pq[0].name, pq[1].name)] if on_hull else []
        gone = {(p_.name, q_.name) for p_, q_ in before} - {(p_.name, q_.name) for p_, q_ in ties(pd)}
        for m, (pe, qe) in enumerate(before):
            g = float(np.clip((tau - 0.3 - 0.05 * m) / 0.4, 0, 1))
            if g <= 0:
                continue
            p, q = xy(pe.composition), xy(qe.composition)
            r = p + (q - p) * g
            broken = (pe.name, qe.name) in gone and on_hull
            ax.plot([p[0], r[0]], [p[1], r[1]], color=LINE, lw=1.5,
                    alpha=0.9 * (1 - 0.8 * landed) if broken else 0.9, zorder=2)
        c0 = xy(cand.composition)
        for pe, qe in after:
            other = qe if pe.name == "__cand__" else pe
            q = xy(other.composition)
            r = c0 + (q - c0) * landed
            ax.plot([c0[0], r[0]], [c0[1], r[1]], color=NEW_LINE, lw=2, zorder=3)
        for k, e in enumerate(old.stable_entries):
            x, y = xy(e.composition)
            g = pop(tau, 0.2 + 0.08 * k)
            hot = e.name in rivals
            col = RIVAL if hot else FG
            ax.scatter([x], [y], s=130 * g, color=col, edgecolor=BG, lw=1.5, zorder=4)
            if e.composition.is_element:
                continue
            a = fade(tau, 0.6 + 0.08 * k)
            if e.name in slots:
                # Out to its slot beside the triangle, with a leader line back to the point.
                lx, ly, side = slots[e.name]
                ax.plot([x, lx - side * 0.012], [y, ly], color=DIM, lw=0.8, alpha=0.7 * a, zorder=3)
                tag(ax, lx, ly, e, col, 13, a, ha="right" if side < 0 else "left", va="center",
                    z=6 if hot else 5)
            else:
                tag(ax, x, y, e, col, 13, a, 0, -8, "center", "top", z=6 if hot else 5)
        x, y = xy(cand.composition)
        drop(ax, tau, x, y, 0.25, side=1)
        headline(ax, tau)

    def draw_ladder(ax, tau):
        style(ax)
        ax.set_xticks([]); ax.set_xlim(0, 1)
        e_old = ef(cand) - delta
        lo, hi = min(ef(cand), e_old), max(ef(cand), e_old)
        pad = max(hi - lo, 0.05) * 1.5
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_ylabel("formation energy (eV/atom)", color=DIM, fontsize=15)
        g = fade(tau, 0.2, 0.6)
        ax.plot([0.1, 0.1 + 0.3 * g], [e_old, e_old], color=LINE, lw=5)
        # Three or more phases do not fit on one line beside the axis.
        names = [tex(r) for r in sorted(rivals)]
        half = (len(names) + 1) // 2 if len(names) > 2 else len(names)
        lines = ["  +  ".join(names[:half]), "  +  ".join(names[half:])]
        ax.text(0.25, e_old + pad * 0.08, "  +\n".join(l for l in lines if l), ha="center",
                va="bottom", color=RIVAL, fontsize=16, alpha=fade(tau, 0.6), linespacing=1.4)
        u = np.clip((tau - 1.4) / 0.8, 0, 1)
        if u > 0:
            yy = ef(cand) + pad * (1 - float(soft(u)))
            ax.plot([0.6, 0.9], [yy, yy], color=NEW_LINE, lw=5)
            ax.text(0.75, yy - pad * 0.08, tex(name), ha="center", va="top",
                    color=ACCENT, fontsize=20, fontweight="bold")
        a = fade(tau, 2.2)
        if a > 0:
            ax.annotate("", xy=(0.5, ef(cand)), xytext=(0.5, e_old),
                        arrowprops=dict(arrowstyle="->", color=FG, lw=2, alpha=a))
        headline(ax, tau)

    return {2: draw_binary, 3: draw_ternary}.get(n, draw_ladder)


def render(cif, out, caption, handle, repeat, seconds, fps, size, hull=None, name=None, frame=None,
           viewer="three", bonds=None, dos=None, polyhedra=True):
    atoms = read(cif)
    reps = np.array(repeat, int)
    pos, nums, sc = expand_boundary(atoms, tuple(reps))
    cell = atoms.cell.array
    # Each atom's position in unit-cell units, so it can appear when the growing box reaches it.
    ucoord = np.linalg.solve(cell.T, pos.T).T

    # Bonds between drawn atoms, using covalent cutoffs.
    drawn = Atoms(numbers=nums, positions=pos)
    i, j = neighbor_list("ij", drawn, natural_cutoffs(drawn, mult=1.1), self_interaction=False)
    pairs = [(a, b) for a, b in zip(i, j) if a < b]

    cf = np.array(np.meshgrid([0, 1], [0, 1], [0, 1])).T.reshape(-1, 3)
    edges = [(a, b) for a in range(8) for b in range(a + 1, 8) if np.abs(cf[a] - cf[b]).sum() == 1]

    radii = 0.38 * covalent_radii[nums]
    colors = jmol_colors[nums]

    # When each atom appears: the unit cell grows in from its centre, the rest as the box
    # sweeps over them.
    grid = np.arange(0, TIMELINE, 0.01)
    sizes = np.array([box(u, reps) for u in grid])
    inside = np.all(ucoord[None, :, :] <= sizes[:, None, :] + 1e-3, axis=2)
    appear = grid[np.argmax(inside, axis=0)]
    first = appear == 0
    d = np.linalg.norm(ucoord - 0.5, axis=1)
    appear[first] = 0.1 + 0.7 * d[first] / max(d[first].max(), 1e-9)

    formula = name or atoms.get_chemical_formula(mode="reduce")
    try:
        from pymatgen.io.ase import AseAtomsAdaptor
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

        sga = SpacegroupAnalyzer(AseAtomsAdaptor.get_structure(atoms), symprec=0.1)
        import re as _re

        symbol = _re.sub(r"_(\d)", r"$_{\1}$", sga.get_space_group_symbol())
        spg = f"{symbol}  (#{sga.get_space_group_number()})"
    except Exception:
        spg = ""

    dpi = 100
    fig = plt.figure(figsize=(size * 16 / 9 / dpi, size / dpi), dpi=dpi, facecolor=BG)
    hax, hull_draw = None, None
    if hull:
        import json

        hull_draw = hull_scene(json.loads(Path(hull).read_text()), atoms.get_chemical_formula(), formula)
        ax = fig.add_axes([0.0, 0.07, 0.6, 0.76])
        hax = fig.add_axes([0.6, 0.2, 0.38, 0.52])
    else:
        ax = fig.add_axes([0.1, 0.06, 0.8, 0.8])
    dos_draw, dax = None, None
    if dos:
        import json

        dos_draw = dos_scene(json.loads(Path(dos).read_text()), Path(dos).parent)
        # Same place as the hull, a little lower: its axis labels sit under it.
        dax = fig.add_axes([0.66, 0.24, 0.3, 0.46])
        dax.set_visible(False)
    bg = np.array(matplotlib.colors.to_rgb(BG))
    aspect = ax.get_position().width * 16 / 9 / ax.get_position().height

    def project(p, yaw, pitch):
        cy, sy = np.cos(yaw), np.sin(yaw)
        cp, sp = np.cos(pitch), np.sin(pitch)
        spin = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        tilt_m = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
        # Put the c axis vertical: map (x, y, z) -> (x, z, y) before spinning.
        p = p[:, [0, 2, 1]]
        return p @ spin.T @ tilt_m.T  # columns: screen x, screen y, depth (toward viewer)

    def fog(rgb, depth, extent):
        t = np.clip((depth + extent) / (2 * extent), 0, 1)  # 0 far, 1 near
        k = 0.35 + 0.65 * t
        return rgb * k + bg * (1 - k)

    def outline(c, extent, color, lw, alpha, zbase):
        for a, b in edges:
            z = (c[a, 2] + c[b, 2]) / 2
            ax.plot(*c[[a, b], :2].T, color=fog(np.array(color), z, extent), lw=lw, alpha=alpha,
                    zorder=z + zbase)

    pos_ax = ax.get_position()
    cell_label = fig.text(pos_ax.x0 + pos_ax.width / 2, 0.045, "", ha="center", va="center", color=DIM,
                          fontsize=size / 60)

    def draw_mpl(t):
        u = t * TIMELINE / seconds
        yaw, pitch, zoom = camera(u, t)
        L = box(u, reps)
        center = (L / 2) @ cell
        p = project(pos - center, yaw, pitch)
        # Frame the current box: the camera pulls back as the supercell builds.
        bc = project((cf * L) @ cell - center, yaw, pitch)
        uc = project(cf @ cell - center, yaw, pitch)
        extent = np.linalg.norm((cf * L) @ cell - center, axis=1).max() * 0.9
        half = extent / zoom
        ax.clear()
        ax.set_facecolor(BG)
        ax.set_xlim(-half * aspect, half * aspect)
        ax.set_ylim(-half, half)
        ax.axis("off")
        grow = ease_out((u - appear) / 0.5)

        outline(bc, extent, list(BOX), 1.0, 1.0, -100)
        if np.any(reps > 1):
            # The unit cell stays marked inside the supercell.
            outline(uc, extent, list(UNIT), 2.0, float(ease_out((u - 1.0) / 0.5)), 50)
        for a, b in pairs:
            g = min(grow[a], grow[b], 1.0)
            if g < 0.05:
                continue
            mid = (p[a] + p[b]) / 2
            for end, col in ((a, colors[a]), (b, colors[b])):
                seg = np.array([p[end, :2], mid[:2]])
                ax.plot(*seg.T, color=fog(col * 0.8, mid[2], extent), lw=3 * g, solid_capstyle="round",
                        zorder=mid[2] - 0.01)
        for k in range(len(p)):
            r = radii[k] * grow[k]
            if r <= 0:
                continue
            x, y, z = p[k]
            col = fog(colors[k], z, extent)
            ax.add_patch(Circle((x, y), r, fc=col, ec=col * 0.7, lw=1, zorder=z))
        # What we are looking at.
        n = np.round(L).astype(int)
        label = "unit cell" if np.all(L < 1.05) else " × ".join(str(v) for v in n) + " supercell"
        cell_label.set_text(label)

    times = [frame] if frame is not None else [f / fps for f in range(int(seconds * fps))]
    images = {}
    if viewer == "three":
        # Render every crystal frame first, at twice the panel's pixel size, then downsample.
        bb = ax.get_position()
        w, h = 2 * bb.width * fig.get_figwidth() * dpi, 2 * bb.height * fig.get_figheight() * dpi
        states = [(t * TIMELINE / seconds, *camera(t * TIMELINE / seconds, t)) for t in times]
        view = crystal_view(cif, bonds)
        import tempfile

        scratch = tempfile.mkdtemp(prefix="showoff_")  # hundreds of MB of PNGs; removed at the end
        paths = web_frames(view, reps, states, w, h, scratch, polyhedra=polyhedra)
        images = dict(zip(times, paths))
        feather = None

    def draw(t):
        if viewer != "three":
            return draw_mpl(t)
        u = t * TIMELINE / seconds
        ax.clear()
        ax.set_facecolor(BG)
        img = plt.imread(images[t])
        nonlocal feather
        if feather is None:
            # Fade the crystal out over the panel's outer 7 %, so the push-in has no hard edge.
            def ramp(n):
                x = np.minimum(np.arange(n), np.arange(n)[::-1]) / (0.07 * n)
                x = np.clip(x, 0, 1)
                return x * x * (3 - 2 * x)
            feather = ramp(img.shape[0])[:, None] * ramp(img.shape[1])[None, :]
        img = img.copy()
        img[..., 3] *= feather
        ax.imshow(img, extent=(0, 1, 0, 1), interpolation="antialiased", aspect="auto")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        L = box(u, reps)
        n = np.round(L).astype(int)
        cell_label.set_text("unit cell" if np.all(L < 1.05) else " × ".join(str(v) for v in n) + " supercell")

    fig.text(0.03, 0.93, tex(formula), color=FG, fontsize=size / 22, fontweight="bold", va="center")
    fig.text(0.03, 0.87, spg, color=DIM, fontsize=size / 48, va="center")
    if caption:
        fig.text(0.97, 0.93, caption, color=FG, fontsize=size / 48, ha="right", va="center")
    if handle:
        fig.text(0.97, 0.03, handle, color=DIM, fontsize=size / 60, ha="right", va="bottom")
    # Element legend, bottom left.
    for n, el in enumerate(sorted(set(atoms.get_chemical_symbols()), key=lambda s: atomic_numbers[s])):
        x = 0.03 + n * 0.05
        fig.text(x, 0.045, "\u25cf", color=jmol_colors[atomic_numbers[el]], fontsize=size / 45,
                 ha="center", va="center")
        fig.text(x + 0.012, 0.045, el, color=FG, fontsize=size / 60, va="center")

    def hull_at(t):
        # Starts with the supercell build and finishes before the push-in ends. With a DOS act,
        # the hull hands over its place at DOS_START.
        u = t * TIMELINE / seconds
        if dos_draw and u >= DOS_START:
            hax.set_visible(False)
            dax.set_visible(True)
            dos_draw(dax, u - DOS_START)
            return
        hull_draw(hax, max(u - 1.0, 0) * 2.8 / 4.0)

    def cleanup():
        if images:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)

    if frame is not None:
        draw(frame)
        if hull_draw:
            hull_at(frame)
        fig.savefig(out, dpi=dpi, facecolor=BG)
        plt.close(fig)
        cleanup()
        print(out)
        return

    writer = FFMpegWriter(fps=fps, codec="libx264",
                          extra_args=["-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart"])
    with writer.saving(fig, out, dpi):
        # The hull plays alongside the crystal: it starts once the atoms are in and finishes
        # with time to spare, so the last stretch holds on both.
        for t in times:
            draw(t)
            if hull_draw:
                hull_at(t)
            writer.grab_frame(facecolor=BG)
    plt.close(fig)
    cleanup()
    print(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cif")
    ap.add_argument("-o", "--out")
    ap.add_argument("--name", help="formula as it should be written, e.g. Fe3Ge2Al (default: from the CIF)")
    ap.add_argument("--caption", default="", help='top-right text, e.g. "E_hull = 0 meV/atom"')
    ap.add_argument("--handle", default="ouro.foundation", help="bottom-right watermark")
    ap.add_argument("--repeat", default="2", help='supercell to build up to: "2" or "3,3,2" (a,b,c)')
    ap.add_argument("--seconds", type=float, default=7, help="video length; the animation stretches to fit")
    ap.add_argument("--hull", help="hull.json: show the convex hull beside the crystal (see top of file)")
    ap.add_argument("--push", type=float, default=1.0,
                    help="how far the camera pushes in, 1 = full; use about 0.3 for a cell of 100+ atoms")
    ap.add_argument("--light", action="store_true", help="white background")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--size", type=int, default=1080, help="video height in pixels; width is 16:9")
    ap.add_argument("--frame", type=float, help="write a PNG of the frame at this many seconds instead")
    ap.add_argument("--dos", help="dos.json: a second act with the density of states (see top of file)")
    ap.add_argument("--viewer", choices=["three", "mpl"], default="three",
                    help="three: the frontend's crystal viewer in headless Chromium (default); mpl: matplotlib")
    ap.add_argument("--bonds", help='bond only these element pairs, e.g. "B-Pt,Pt-Pt" (three viewer)')
    ap.add_argument("--no-polyhedra", action="store_true", help="leave out coordination polyhedra")
    a = ap.parse_args()
    if a.dos:
        # The DOS act needs room after the hull: lengthen the timeline and add a last camera move.
        globals()["TIMELINE"] = DOS_START + 5.5
        SHOTS.append((DOS_START + 0.6, 2.4, (320, 28, 1.25), "inout"))
        if a.seconds == 7:
            a.seconds = TIMELINE
    globals().update(THEMES["light" if a.light else "dark"])
    globals()["PUSH"] = a.push
    out = a.out or str(Path(a.cif).with_suffix(".png" if a.frame is not None else ".mp4"))
    rep = [int(x) for x in a.repeat.split(",")]
    rep = rep * 3 if len(rep) == 1 else rep
    render(a.cif, out, a.caption, a.handle, rep, a.seconds, a.fps, a.size, a.hull, a.name, a.frame,
           a.viewer, a.bonds, a.dos, not a.no_polyhedra)


if __name__ == "__main__":
    main()
