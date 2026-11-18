/**
 * The crystal half of a showoff.py video, drawn the way the Ouro frontend's
 * crystal viewer draws it: the same scene and polyhedra code (frontend
 * lib/structure-view.ts), element colours, ball sizes, materials and lights
 * (components/visualizations/structure-scene.tsx, crystal-viewer.tsx).
 *
 * The page is driven frame by frame from render.mjs: window.setup(job) once,
 * then window.frame(state) for each frame. Nothing here keeps time.
 */
import * as THREE from "three"

import { COVALENT_RADII, ELEMENT_COLORS } from "frontend-lib/elements"
import {
  buildPolyhedra,
  buildScene,
  fracToCart,
  type CrystalView,
  type SceneAtom,
  type Vec3,
} from "frontend-lib/structure-view"

// As in structure-scene.tsx.
const BALL_SCALE = 0.4
const MIN_BALL_RADIUS = 0.2
const BOND_RADIUS = 0.1
const ATOM_RADII = COVALENT_RADII.map((c) => Math.max(MIN_BALL_RADIUS, BALL_SCALE * c))

interface Job {
  view: CrystalView
  /** Cells along a, b, c that the box grows to. */
  tiling: Vec3
  /** [start, duration] of the box growing along a, b, c, on the video's timeline. */
  grow: [number, number][]
  polyhedra: boolean
  width: number
  height: number
  /** Background colour; also what the depth fog fades toward. */
  background: string
  /** Colour of the outer box and of the unit cell kept marked inside it. */
  box: string
  unit: string
  /** Magnetic moments: a Cartesian vector (muB) per site of the view, drawn as arrows from `start`. */
  moments?: { vectors: Vec3[]; start: number; color: string } | null
}

interface State {
  /** Position on the video's timeline (seconds of the unstretched timeline). */
  u: number
  yaw: number
  pitch: number
  zoom: number
}

declare global {
  interface Window {
    setup: (job: Job) => void
    frame: (state: State) => void
  }
}

const easeOut = (x: number) => 1 - (1 - Math.min(Math.max(x, 0), 1)) ** 3
const inout = (x: number) => {
  x = Math.min(Math.max(x, 0), 1)
  return x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2
}

let job: Job
let renderer: THREE.WebGLRenderer
let scene: THREE.Scene
let camera: THREE.OrthographicCamera
let headlight: THREE.DirectionalLight
let atoms: SceneAtom[] = []
let atomMesh: THREE.InstancedMesh
let bondMesh: THREE.InstancedMesh
let bondPairs: { a: number; b: number }[] = []
let polyFaces: THREE.Mesh[] = []
let polyEdges: THREE.LineSegments[] = []
let polyCenters: number[] = []
let polyVertices: number[][] = []
let boxLines: THREE.LineSegments
let unitLines: THREE.LineSegments
let appear: number[] = []
let shaftMesh: THREE.InstancedMesh | null = null
let headMesh: THREE.InstancedMesh | null = null
let arrowAtoms: number[] = []
// Arrow length: the ball's diameter plus this much per Bohr magneton.
const ARROW_PER_MUB = 0.55
const SHAFT_RADIUS = 0.075
const HEAD_RADIUS = 0.2
const HEAD_LENGTH = 0.42
let matrix: [Vec3, Vec3, Vec3]

/** Size of the built-up box along a, b, c, in cells, at timeline position u. */
function boxAt(u: number): Vec3 {
  return job.tiling.map((r, axis) => {
    const [s, d] = job.grow[axis]
    return 1 + (r - 1) * inout((u - s) / d)
  }) as Vec3
}

function boxGeometry(L: Vec3): THREE.BufferGeometry {
  const c = (f: Vec3) => fracToCart(matrix, f)
  const pts: number[] = []
  for (let axis = 0; axis < 3; axis++) {
    const [p, q] = [(axis + 1) % 3, (axis + 2) % 3]
    for (const i of [0, L[p]])
      for (const j of [0, L[q]]) {
        const s: Vec3 = [0, 0, 0]
        s[p] = i
        s[q] = j
        const e = [...s] as Vec3
        e[axis] = L[axis]
        pts.push(...c(s), ...c(e))
      }
  }
  return new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(pts, 3))
}

window.setup = (j: Job) => {
  job = j
  matrix = j.view.lattice!.matrix
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })
  renderer.setSize(j.width, j.height, false)
  renderer.setClearColor(0x000000, 0)
  // React Three Fiber's defaults, which the frontend viewer renders with.
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.outputColorSpace = THREE.SRGBColorSpace
  document.body.appendChild(renderer.domElement)

  scene = new THREE.Scene()
  scene.fog = new THREE.Fog(new THREE.Color(j.background), 1, 2)
  camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000)
  camera.up.set(0, 0, 1)
  scene.add(camera)
  scene.add(new THREE.AmbientLight(0xffffff, 1.1))
  headlight = new THREE.DirectionalLight(0xffffff, 2)
  scene.add(headlight, headlight.target)

  const built = buildScene(j.view, {
    tiling: j.tiling,
    boundaryAtoms: true,
    bondedOutside: false,
    completeShells: j.polyhedra,
  })
  atoms = built.atoms
  bondPairs = built.bonds

  // When each atom appears: the unit cell grows from its centre, the rest as the box sweeps
  // over them. Atoms drawn outside the box to finish a polyhedron come with its centre.
  const frac = atoms.map((atom) => {
    const inv = new THREE.Matrix3()
      .set(...matrix[0], ...matrix[1], ...matrix[2])
      .transpose()
      .invert()
    return new THREE.Vector3(...atom.position).applyMatrix3(inv).toArray() as Vec3
  })
  const grid = Array.from({ length: 1400 }, (_, k) => k * 0.01)
  appear = frac.map((f) => {
    const first = f.every((x) => x <= 1 + 1e-3 && x >= -1e-3)
    if (first) {
      const d = Math.hypot(f[0] - 0.5, f[1] - 0.5, f[2] - 0.5)
      return 0.1 + 0.7 * (d / 0.87)
    }
    const hit = grid.find((u) => {
      const L = boxAt(u)
      return f.every((x, axis) => x <= L[axis] + 1e-3)
    })
    return hit ?? 99
  })
  const outsideAppear = atoms.map(() => Infinity)
  for (const { a, b } of bondPairs) {
    if (atoms[a].outside && !atoms[b].outside) outsideAppear[a] = Math.min(outsideAppear[a], appear[b])
    if (atoms[b].outside && !atoms[a].outside) outsideAppear[b] = Math.min(outsideAppear[b], appear[a])
  }
  atoms.forEach((atom, i) => {
    if (atom.outside) appear[i] = Number.isFinite(outsideAppear[i]) ? outsideAppear[i] : 99
  })

  const sphere = new THREE.SphereGeometry(1, 48, 32)
  atomMesh = new THREE.InstancedMesh(sphere, new THREE.MeshStandardMaterial({ roughness: 0.45 }), atoms.length)
  const color = new THREE.Color()
  atoms.forEach((atom, i) => atomMesh.setColorAt(i, color.set(ELEMENT_COLORS[atom.z] ?? ELEMENT_COLORS[0])))
  scene.add(atomMesh)

  const cyl = new THREE.CylinderGeometry(1, 1, 1, 16, 1, true)
  bondMesh = new THREE.InstancedMesh(cyl, new THREE.MeshStandardMaterial({ roughness: 0.6 }), Math.max(1, 2 * bondPairs.length))
  bondPairs.forEach(({ a, b }, i) => {
    bondMesh.setColorAt(2 * i, color.set(ELEMENT_COLORS[atoms[a].z]))
    bondMesh.setColorAt(2 * i + 1, color.set(ELEMENT_COLORS[atoms[b].z]))
  })
  scene.add(bondMesh)

  // Polyhedra as in crystal-viewer.tsx: translucent, tinted like the centre atom, with edges.
  if (j.polyhedra) {
    for (const { center, faces } of buildPolyhedra(j.view, atoms, bondPairs)) {
      const tint = new THREE.Color(ELEMENT_COLORS[atoms[center].z])
      const tri: number[] = []
      const lines: number[] = []
      const seen = new Set<string>()
      const verts = new Set<number>()
      for (const ring of faces) {
        ring.forEach((v) => verts.add(v))
        for (let i = 1; i < ring.length - 1; i++)
          for (const v of [ring[0], ring[i], ring[i + 1]]) tri.push(...atoms[v].position)
        ring.forEach((a, i) => {
          const b = ring[(i + 1) % ring.length]
          const key = a < b ? `${a}-${b}` : `${b}-${a}`
          if (seen.has(key)) return
          seen.add(key)
          lines.push(...atoms[a].position, ...atoms[b].position)
        })
      }
      const fg = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(tri, 3))
      fg.computeVertexNormals()
      const face = new THREE.Mesh(
        fg,
        new THREE.MeshStandardMaterial({
          color: tint, transparent: true, opacity: 0.35, roughness: 0.6,
          side: THREE.DoubleSide, depthWrite: false,
        })
      )
      const edge = new THREE.LineSegments(
        new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(lines, 3)),
        new THREE.LineBasicMaterial({ color: tint, transparent: true, opacity: 0.7 })
      )
      face.renderOrder = 2
      edge.renderOrder = 3
      scene.add(face, edge)
      polyFaces.push(face)
      polyEdges.push(edge)
      polyCenters.push(center)
      polyVertices.push([...verts])
    }
  }

  if (j.moments) {
    arrowAtoms = atoms
      .map((atom, i) => (Math.hypot(...j.moments!.vectors[atom.site]) > 0.05 && !atom.outside ? i : -1))
      .filter((i) => i >= 0)
    const mat = () =>
      new THREE.MeshStandardMaterial({
        color: j.moments!.color, roughness: 0.35, emissive: new THREE.Color(j.moments!.color), emissiveIntensity: 0.35,
      })
    shaftMesh = new THREE.InstancedMesh(new THREE.CylinderGeometry(1, 1, 1, 20), mat(), Math.max(1, arrowAtoms.length))
    headMesh = new THREE.InstancedMesh(new THREE.ConeGeometry(1, 1, 24), mat(), Math.max(1, arrowAtoms.length))
    scene.add(shaftMesh, headMesh)
  }

  boxLines = new THREE.LineSegments(boxGeometry([1, 1, 1]),
    new THREE.LineBasicMaterial({ color: j.box, transparent: true, opacity: 0.6 }))
  unitLines = new THREE.LineSegments(boxGeometry([1, 1, 1]),
    new THREE.LineBasicMaterial({ color: j.unit, transparent: true, opacity: 0 }))
  scene.add(boxLines, unitLines)
}

const Y = new THREE.Vector3(0, 1, 0)

window.frame = ({ u, yaw, pitch, zoom }: State) => {
  const L = boxAt(u)
  const center = new THREE.Vector3(...fracToCart(matrix, L.map((x) => x / 2) as Vec3))
  // Frame the current box: the camera pulls back as the supercell builds.
  let extent = 0
  for (let k = 0; k < 8; k++) {
    const corner = fracToCart(matrix, [k & 1 ? L[0] : 0, k & 2 ? L[1] : 0, k & 4 ? L[2] : 0])
    extent = Math.max(extent, center.distanceTo(new THREE.Vector3(...corner)))
  }
  extent *= 0.9
  const half = extent / zoom
  const aspect = job.width / job.height
  camera.left = -half * aspect
  camera.right = half * aspect
  camera.top = half
  camera.bottom = -half
  const dir = new THREE.Vector3(Math.cos(pitch) * Math.cos(yaw), Math.cos(pitch) * Math.sin(yaw), Math.sin(pitch))
  camera.position.copy(center).addScaledVector(dir, extent * 4)
  camera.lookAt(center)
  camera.near = extent * 1.5
  camera.far = extent * 7
  camera.updateProjectionMatrix()
  // Depth cue: the far side of the box fades a little toward the background.
  ;(scene.fog as THREE.Fog).near = extent * 4
  ;(scene.fog as THREE.Fog).far = extent * 8
  // Headlight: up and to the left of the line of sight, as in the frontend.
  headlight.position.set(-0.6, 0.8, 1).applyQuaternion(camera.quaternion).add(center)
  headlight.target.position.copy(center)

  const grow = appear.map((t) => easeOut((u - t) / 0.5))
  const m = new THREE.Matrix4()
  atoms.forEach((atom, i) => {
    const r = ATOM_RADII[atom.z] * grow[i]
    m.makeScale(r + 1e-6, r + 1e-6, r + 1e-6).setPosition(...atom.position)
    atomMesh.setMatrixAt(i, m)
  })
  atomMesh.instanceMatrix.needsUpdate = true

  const s = new THREE.Vector3(), e = new THREE.Vector3(), mid = new THREE.Vector3()
  const span = (from: THREE.Vector3, to: THREE.Vector3, r: number) => {
    const d = to.clone().sub(from)
    const q = new THREE.Quaternion().setFromUnitVectors(Y, d.clone().normalize())
    return m.compose(from.clone().add(to).multiplyScalar(0.5), q, new THREE.Vector3(r, d.length(), r))
  }
  bondPairs.forEach(({ a, b }, i) => {
    const g = Math.min(grow[a], grow[b])
    const r = g < 0.05 ? 1e-6 : BOND_RADIUS * g
    s.set(...atoms[a].position)
    e.set(...atoms[b].position)
    mid.addVectors(s, e).multiplyScalar(0.5)
    bondMesh.setMatrixAt(2 * i, span(s, mid, r))
    bondMesh.setMatrixAt(2 * i + 1, span(e, mid, r))
  })
  bondMesh.instanceMatrix.needsUpdate = true

  polyFaces.forEach((face, k) => {
    // A polyhedron fills in once its centre and every corner are on screen.
    const g = Math.min(grow[polyCenters[k]], ...polyVertices[k].map((v) => grow[v]))
    const a = easeOut((g - 0.6) / 0.4)
    ;(face.material as THREE.MeshStandardMaterial).opacity = 0.35 * a
    ;(polyEdges[k].material as THREE.LineBasicMaterial).opacity = 0.7 * a
    face.visible = polyEdges[k].visible = a > 0.01
  })

  if (shaftMesh && headMesh && job.moments) {
    const q = new THREE.Quaternion()
    arrowAtoms.forEach((i, k) => {
      const v = new THREE.Vector3(...job.moments!.vectors[atoms[i].site])
      const mu = v.length()
      // Arrows come in after the atom, a little later the further up the box it sits.
      const g = Math.min(grow[i], easeOut((u - Math.max(job.moments!.start, appear[i]) - 0.04 * (k % 12)) / 0.6))
      const length = (2 * ATOM_RADII[atoms[i].z] + ARROW_PER_MUB * mu) * g
      const dirv = v.normalize()
      q.setFromUnitVectors(Y, dirv)
      const c = new THREE.Vector3(...atoms[i].position)
      const head = Math.min(HEAD_LENGTH, length * 0.45)
      const shaft = Math.max(length - head, 1e-6)
      const tail = c.clone().addScaledVector(dirv, -length / 2)
      const w = g < 0.03 ? 1e-6 : 1
      m.compose(tail.clone().addScaledVector(dirv, shaft / 2), q, new THREE.Vector3(SHAFT_RADIUS * w, shaft, SHAFT_RADIUS * w))
      shaftMesh!.setMatrixAt(k, m)
      m.compose(tail.clone().addScaledVector(dirv, shaft + head / 2), q, new THREE.Vector3(HEAD_RADIUS * w, Math.max(head, 1e-6), HEAD_RADIUS * w))
      headMesh!.setMatrixAt(k, m)
    })
    shaftMesh.instanceMatrix.needsUpdate = true
    headMesh.instanceMatrix.needsUpdate = true
  }

  boxLines.geometry.dispose()
  boxLines.geometry = boxGeometry(L)
  const big = job.tiling.some((n) => n > 1)
  ;(unitLines.material as THREE.LineBasicMaterial).opacity = big ? 0.8 * easeOut((u - job.grow[0][0]) / 0.5) : 0

  renderer.render(scene, camera)
}
