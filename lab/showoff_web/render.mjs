// Render the crystal frames of a showoff.py video in headless Chromium.
//
//   node render.mjs job.json out_dir/
//
// job.json: {"bundle": "scene.js", "setup": {...}, "frames": [{u, yaw, pitch, zoom}, ...]}
// Writes out_dir/00000.png ... with a transparent background. Playwright comes from the
// frontend's node_modules (FRONTEND, default ../../frontend from this folder).
import fs from "node:fs"
import path from "node:path"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = process.env.FRONTEND ?? path.resolve(here, "../../../frontend")
const require = createRequire(path.join(frontend, "package.json"))
const { chromium } = require("playwright")

const [jobPath, outDir] = process.argv.slice(2)
const job = JSON.parse(fs.readFileSync(jobPath, "utf8"))
fs.mkdirSync(outDir, { recursive: true })

const args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
let browser
try {
  browser = await chromium.launch({ args })
} catch {
  // The bundled browser may not be downloaded for this Playwright version; use Chrome.
  browser = await chromium.launch({ channel: "chrome", args })
}
const { width, height } = job.setup
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 })
page.on("pageerror", (err) => {
  console.error("page error:", err.message)
  process.exit(1)
})
await page.setContent(
  `<!doctype html><html><body style="margin:0;background:transparent;overflow:hidden">
   <script>${fs.readFileSync(job.bundle, "utf8")}</script></body></html>`
)
await page.evaluate((setup) => window.setup(setup), job.setup)

for (let i = 0; i < job.frames.length; i++) {
  const data = await page.evaluate((state) => {
    window.frame(state)
    return document.querySelector("canvas").toDataURL("image/png")
  }, job.frames[i])
  fs.writeFileSync(path.join(outDir, `${String(i).padStart(5, "0")}.png`), Buffer.from(data.split(",")[1], "base64"))
  if (i % 60 === 0) console.log(`frame ${i}/${job.frames.length}`)
}
await browser.close()
