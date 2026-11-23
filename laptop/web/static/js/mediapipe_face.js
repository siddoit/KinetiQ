/* KinetiQ — Local Offline MediaPipe 478-Point FaceLandmarker + Posture/Mood Engine */
import { FaceLandmarker, FilesetResolver } from '/static/vendor/mediapipe/vision_bundle.mjs';

const state = {
  ready: false,
  failed: false,
  faceDetected: false,
  landmarks: null,       // Array of 478 {x, y, z}
  smile: 0,              // 0..1
  browTension: 0,        // 0..1
  eyeBlink: 0,           // 0..1
  jawOpen: 0,            // 0..1
  frown: 0,              // 0..1
  rollDeg: 0,            // Head tilt angle in degrees
  posture: 'upright',    // 'upright' | 'tilted' | 'slouched'
  postureNote: '',
  mpMood: '',            // Live reactive expression hint from blendshapes
  lastDetectTs: 0,
  badPostureSince: 0
};

window.KinetiQFace = state;

let landmarker = null;
let lastVideoTime = -1;
let smooth = {
  jawOpen: 0,
  smile: 0,
  browDown: 0,
  browInnerUp: 0,
  browOuterUp: 0,
  frown: 0,
  mouthPress: 0,
  eyeWide: 0
};
let candidateMood = 'Calm';
let candidateSince = 0;

async function initFaceLandmarker() {
  try {
    const vision = await FilesetResolver.forVisionTasks('/static/vendor/mediapipe/wasm');
    try {
      landmarker = await FaceLandmarker.createFromOptions(vision, {
        baseOptions: {
          modelAssetPath: '/static/vendor/mediapipe/face_landmarker.task',
          delegate: 'GPU'
        },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFaceBlendshapes: true,
        minFaceDetectionConfidence: 0.45,
        minFacePresenceConfidence: 0.45,
        minTrackingConfidence: 0.45
      });
    } catch (gpuErr) {
      // Graceful CPU WASM fallback if GPU delegate unavailable
      landmarker = await FaceLandmarker.createFromOptions(vision, {
        baseOptions: {
          modelAssetPath: '/static/vendor/mediapipe/face_landmarker.task',
          delegate: 'CPU'
        },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFaceBlendshapes: true
      });
    }
    state.ready = true;
    requestAnimationFrame(detectLoop);
  } catch (err) {
    console.warn('[KinetiQFace] MediaPipe init fallback:', err);
    state.failed = true;
  }
}

function getBlend(cats, name) {
  for (let i = 0; i < cats.length; i++) {
    if (cats[i].categoryName === name) return cats[i].score || 0;
  }
  return 0;
}

function detectLoop(ts) {
  requestAnimationFrame(detectLoop);
  if (!landmarker || document.hidden) return;
  // Cap detection cleanly to ~22 FPS (45ms) for silky zero-stutter performance
  if (ts - state.lastDetectTs < 45) return;

  const video = document.getElementById('camVideo');
  const stage = document.getElementById('mirrorStage');
  if (!video || !stage || !stage.classList.contains('cam-live')) return;
  if (video.readyState < 2 || !video.videoWidth || video.currentTime === lastVideoTime) return;

  state.lastDetectTs = ts;
  lastVideoTime = video.currentTime;

  try {
    const res = landmarker.detectForVideo(video, ts);
    if (res && res.faceLandmarks && res.faceLandmarks.length > 0) {
      const lm = res.faceLandmarks[0];
      state.faceDetected = true;
      state.landmarks = lm;

      // 1. Live Blendshapes -> 5 Responsive Emotions with EMA + 220ms Anti-Flicker Hold
      if (res.faceBlendshapes && res.faceBlendshapes.length > 0) {
        const cats = res.faceBlendshapes[0].categories || [];
        const rawJawOpen = getBlend(cats, 'jawOpen');
        const rawSmile = (getBlend(cats, 'mouthSmileLeft') + getBlend(cats, 'mouthSmileRight')) * 0.5;
        const rawBrowDown = (getBlend(cats, 'browDownLeft') + getBlend(cats, 'browDownRight')) * 0.5;
        const rawBrowInnerUp = getBlend(cats, 'browInnerUp');
        const rawBrowOuterUp = (getBlend(cats, 'browOuterUpLeft') + getBlend(cats, 'browOuterUpRight')) * 0.5;
        const rawFrown = (getBlend(cats, 'mouthFrownLeft') + getBlend(cats, 'mouthFrownRight')) * 0.5;
        const rawMouthPress = (getBlend(cats, 'mouthPressLeft') + getBlend(cats, 'mouthPressRight')) * 0.5;
        const rawEyeWide = (getBlend(cats, 'eyeWideLeft') + getBlend(cats, 'eyeWideRight')) * 0.5;
        const rawBlink = (getBlend(cats, 'eyeBlinkLeft') + getBlend(cats, 'eyeBlinkRight')) * 0.5;

        // Smooth each metric with fast EMA (prev * 0.55 + cur * 0.45) for snappy <250ms feel
        smooth.jawOpen = smooth.jawOpen * 0.55 + rawJawOpen * 0.45;
        smooth.smile = smooth.smile * 0.55 + rawSmile * 0.45;
        smooth.browDown = smooth.browDown * 0.55 + rawBrowDown * 0.45;
        smooth.browInnerUp = smooth.browInnerUp * 0.55 + rawBrowInnerUp * 0.45;
        smooth.browOuterUp = smooth.browOuterUp * 0.55 + rawBrowOuterUp * 0.45;
        smooth.frown = smooth.frown * 0.55 + rawFrown * 0.45;
        smooth.mouthPress = smooth.mouthPress * 0.55 + rawMouthPress * 0.45;
        smooth.eyeWide = smooth.eyeWide * 0.55 + rawEyeWide * 0.45;

        state.smile = smooth.smile;
        state.jawOpen = smooth.jawOpen;
        state.frown = smooth.frown;
        state.browTension = smooth.browDown;
        state.eyeBlink = state.eyeBlink * 0.55 + rawBlink * 0.45;

        // Classify candidate emotion strictly in priority order:
        // 1. Surprised: jawOpen > 0.32 || (jawOpen > 0.20 && (browInnerUp > 0.28 || eyeWide > 0.28))
        // 2. Happy:     smile > 0.32
        // 3. Angry:     browDown > 0.30 || (browDown > 0.20 && mouthPress > 0.24)
        // 4. Sad:       frown > 0.22 || (browInnerUp > 0.32 && smile < 0.14 && jawOpen < 0.18)
        // 5. Calm:      default baseline
        let detected = 'Calm';
        if (smooth.jawOpen > 0.32 || (smooth.jawOpen > 0.20 && (smooth.browInnerUp > 0.28 || smooth.eyeWide > 0.28))) {
          detected = 'Surprised';
        } else if (smooth.smile > 0.32) {
          detected = 'Happy';
        } else if (smooth.browDown > 0.30 || (smooth.browDown > 0.20 && smooth.mouthPress > 0.24)) {
          detected = 'Angry';
        } else if (smooth.frown > 0.22 || (smooth.browInnerUp > 0.32 && smooth.smile < 0.14 && smooth.jawOpen < 0.18)) {
          detected = 'Sad';
        } else {
          detected = 'Calm';
        }

        if (detected !== candidateMood) {
          candidateMood = detected;
          candidateSince = ts;
        }

        // Apply 220ms hold debounce before committing state.mpMood
        if (ts - candidateSince >= 220 || !state.mpMood) {
          state.mpMood = candidateMood;
        }
      }

      // 2. Live 3D Head Roll & Posture Geometry
      const leftEye = lm[33];
      const rightEye = lm[263];
      const nose = lm[4];
      const leftCheek = lm[234];
      const rightCheek = lm[454];

      if (leftEye && rightEye && nose && leftCheek && rightCheek) {
        const dx = rightEye.x - leftEye.x;
        const dy = rightEye.y - leftEye.y;
        const rollDeg = Math.atan2(dy, dx) * (180 / Math.PI);
        state.rollDeg = state.rollDeg * 0.75 + rollDeg * 0.25;

        const faceW = Math.hypot(rightCheek.x - leftCheek.x, rightCheek.y - leftCheek.y);
        const isTilted = Math.abs(state.rollDeg) > 11.5;
        const isSlouched = nose.y > 0.65 || faceW > 0.46;

        if (isTilted || isSlouched) {
          if (!state.badPostureSince) state.badPostureSince = ts;
          if (ts - state.badPostureSince > 3500) {
            state.posture = isTilted ? 'tilted' : 'slouched';
            state.postureNote = isTilted
              ? 'Your head is tilted slightly — try leveling your gaze and relaxing your neck.'
              : 'Try relaxing your shoulders, lifting your chin, and sitting comfortably upright.';
          }
        } else {
          state.badPostureSince = 0;
          state.posture = 'upright';
          state.postureNote = '';
        }
      }
    } else {
      state.faceDetected = false;
      state.landmarks = null;
      state.badPostureSince = 0;
      state.posture = 'upright';
      state.mpMood = '';
      candidateMood = 'Calm';
      candidateSince = 0;
    }
  } catch (e) {
    // Ignore transient video frame decode hiccups
  }
}

initFaceLandmarker();
