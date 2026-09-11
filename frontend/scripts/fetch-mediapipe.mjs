/**
 * Copies the MediaPipe WASM runtime out of node_modules and downloads the
 * pose model into public/, so the app serves both from its own origin.
 *
 * Why not just load them from a CDN:
 *   1. A CDN version that drifts out of step with the installed npm package
 *      produces a blank screen and a console error nobody reads.
 *   2. Your viva will be on college wifi. The demo must run with the network
 *      unplugged, and this makes that true.
 *
 * Runs automatically before `npm run dev` and `npm run build`. Safe to re-run:
 * it skips anything already present.
 */
import { createWriteStream } from "node:fs";
import { cp, mkdir, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");

const WASM_SRC = join(ROOT, "node_modules", "@mediapipe", "tasks-vision", "wasm");
const WASM_DST = join(ROOT, "public", "mediapipe", "wasm");

const MODEL_DIR = join(ROOT, "public", "mediapipe", "models");
const MODEL_FILE = join(MODEL_DIR, "pose_landmarker_lite.task");
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/" +
  "pose_landmarker_lite/float16/1/pose_landmarker_lite.task";

async function exists(p) {
  try {
    await stat(p);
    return true;
  } catch {
    return false;
  }
}

async function copyWasm() {
  if (!(await exists(WASM_SRC))) {
    console.error(
      "[prepare-assets] node_modules/@mediapipe/tasks-vision/wasm is missing.\n" +
        "                Run `npm install` first."
    );
    process.exit(1);
  }
  await mkdir(dirname(WASM_DST), { recursive: true });
  await cp(WASM_SRC, WASM_DST, { recursive: true });
  console.log("[prepare-assets] wasm runtime -> public/mediapipe/wasm");
}

async function fetchModel() {
  if (await exists(MODEL_FILE)) {
    const { size } = await stat(MODEL_FILE);
    console.log(
      `[prepare-assets] model already present (${(size / 1e6).toFixed(1)} MB), skipping`
    );
    return;
  }
  await mkdir(MODEL_DIR, { recursive: true });
  console.log("[prepare-assets] downloading pose model (~5 MB) ...");
  const res = await fetch(MODEL_URL);
  if (!res.ok) {
    console.error(
      `[prepare-assets] download failed: HTTP ${res.status}.\n` +
        "                The app will fall back to loading the model from the\n" +
        "                Google CDN at runtime, which needs a live network."
    );
    return; // non-fatal: the app has a CDN fallback
  }
  await pipeline(Readable.fromWeb(res.body), createWriteStream(MODEL_FILE));
  const { size } = await stat(MODEL_FILE);
  console.log(`[prepare-assets] model -> public/mediapipe/models (${(size / 1e6).toFixed(1)} MB)`);
}

await copyWasm();
await fetchModel();
