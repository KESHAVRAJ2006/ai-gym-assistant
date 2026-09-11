/**
 * MediaPipe Pose Landmarker setup and the per-frame loop.
 *
 * Everything here runs in the browser. The only thing that leaves the device
 * is 33 (x, y, z, visibility) tuples per frame - never a video frame. That is
 * both the privacy claim in the report and the reason the backend fits on a
 * free 512 MB dyno.
 */
import { DrawingUtils, FilesetResolver, PoseLandmarker } from "@mediapipe/tasks-vision";

// Served from our own origin by scripts/fetch-mediapipe.mjs.
const LOCAL_WASM = "/mediapipe/wasm";
const LOCAL_MODEL = "/mediapipe/models/pose_landmarker_lite.task";

// Used only if the local copies are missing (e.g. the build-time download
// failed). Requires a live network.
const CDN_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm";
const CDN_MODEL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/" +
  "pose_landmarker_lite/float16/1/pose_landmarker_lite.task";

async function headOk(url) {
  try {
    const res = await fetch(url, { method: "HEAD" });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Create the landmarker. Tries local assets, falls back to the CDN.
 * Returns { landmarker, source } where source is "local" or "cdn".
 */
export async function createPoseLandmarker() {
  const useLocal = await headOk(LOCAL_MODEL);
  const wasmPath = useLocal ? LOCAL_WASM : CDN_WASM;
  const modelPath = useLocal ? LOCAL_MODEL : CDN_MODEL;

  const fileset = await FilesetResolver.forVisionTasks(wasmPath);
  const landmarker = await PoseLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: modelPath,
      // GPU is several times faster, but falls over on some Intel drivers.
      // The caller retries with CPU if this throws.
      delegate: "GPU",
    },
    runningMode: "VIDEO",
    numPoses: 1,
    minPoseDetectionConfidence: 0.5,
    minPosePresenceConfidence: 0.5,
    minTrackingConfidence: 0.5,
    outputSegmentationMasks: false,
  });
  return { landmarker, source: useLocal ? "local" : "cdn" };
}

export async function createPoseLandmarkerSafe() {
  try {
    return await createPoseLandmarker();
  } catch (err) {
    // Retry on CPU: "Failed to create WebGL context" and friends.
    console.warn("GPU delegate failed, retrying on CPU:", err);
    const useLocal = await headOk(LOCAL_MODEL);
    const fileset = await FilesetResolver.forVisionTasks(
      useLocal ? LOCAL_WASM : CDN_WASM
    );
    const landmarker = await PoseLandmarker.createFromOptions(fileset, {
      baseOptions: {
        modelAssetPath: useLocal ? LOCAL_MODEL : CDN_MODEL,
        delegate: "CPU",
      },
      runningMode: "VIDEO",
      numPoses: 1,
    });
    return { landmarker, source: useLocal ? "local (CPU)" : "cdn (CPU)" };
  }
}

/** Ask for the webcam, with error text a human can act on. */
export async function startCamera(videoEl) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(
      "This browser cannot access the camera. Note that browsers only allow " +
        "camera access on https:// or on http://localhost."
    );
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
      audio: false,
    });
  } catch (err) {
    const map = {
      NotAllowedError:
        "Camera permission was denied. Click the camera icon in the address " +
        "bar and allow access, then reload.",
      NotFoundError: "No camera was found on this device.",
      NotReadableError:
        "The camera is already in use by another app (Zoom, Teams, Meet). " +
        "Close it and reload.",
    };
    throw new Error(map[err.name] || `Could not start the camera: ${err.message}`);
  }
  videoEl.srcObject = stream;
  await videoEl.play();
  return stream;
}

export function stopCamera(stream) {
  stream?.getTracks?.().forEach((t) => t.stop());
}

/** Convert MediaPipe output into the compact arrays the backend expects. */
export function packLandmarks(result) {
  const lm = result.landmarks?.[0];
  const wlm = result.worldLandmarks?.[0];
  if (!lm) return null;
  const r4 = (v) => Math.round(v * 1e4) / 1e4;
  return {
    lm: lm.map((p) => [r4(p.x), r4(p.y), r4(p.z ?? 0), r4(p.visibility ?? 1)]),
    wlm: wlm ? wlm.map((p) => [r4(p.x), r4(p.y), r4(p.z ?? 0)]) : undefined,
  };
}

const SKELETON = "#6ee7b7";
const JOINTS = "#f0f6fc";

/** Draw the skeleton over the mirrored video. */
export function drawPose(canvas, video, result, { state = "", reps = 0 } = {}) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = video.videoWidth || canvas.width;
  const h = video.videoHeight || canvas.height;
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }

  ctx.save();
  ctx.clearRect(0, 0, w, h);
  // Mirror so the user sees themselves as in a mirror. The landmarks sent to
  // the server are NOT mirrored - joint angles are unaffected by reflection,
  // and flipping them would silently swap left and right in the report.
  ctx.translate(w, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(video, 0, 0, w, h);

  const lm = result?.landmarks?.[0];
  if (lm) {
    const du = new DrawingUtils(ctx);
    du.drawConnectors(lm, PoseLandmarker.POSE_CONNECTIONS, {
      color: SKELETON,
      lineWidth: 3,
    });
    du.drawLandmarks(lm, {
      color: JOINTS,
      fillColor: state === "peak" ? "#f59e0b" : SKELETON,
      radius: 3.5,
      lineWidth: 1,
    });
  }
  ctx.restore();
}
