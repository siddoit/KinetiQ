"""
KINETIQ Lightweight Emotion Detector.

Uses ONNX Runtime (HSEmotion enet_b0_8_best_vgaf) + OpenCV Haar Cascade face detection.
Zero PyTorch dependency, sub-30ms inference on CPU.

Extracts facial emotion classes and maps them to empathetic wellness descriptors
for direct injection into the KINETIQ AI Mirror Assistant.
"""

from __future__ import annotations

import base64
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore

# 5 Clean Emotions Mapping: (descriptor, label, tone)
EMOTION_DESCRIPTORS = {
    "Neutral":   ("calm and settled", "Calm", "balanced"),
    "Happiness": ("happy and upbeat", "Happy", "upbeat"),
    "Surprise":  ("surprised and wide-eyed", "Surprised", "playfully curious and reassuring"),
    "Sadness":   ("sad or a little low", "Sad", "extra gentle, warm, and comforting"),
    "Anger":     ("angry or frustrated", "Angry", "calm, steady, and de-escalating"),
    "Disgust":   ("angry or frustrated", "Angry", "calm, steady, and de-escalating"),
    "Fear":      ("surprised and wide-eyed", "Surprised", "calm and reassuring"),
    "Contempt":  ("calm and settled", "Calm", "balanced"),
}

EMOTION_WELLNESS_MAP = {
    k: {
        "descriptor": v[0],
        "label": v[1],
        "tone": v[2],
    }
    for k, v in EMOTION_DESCRIPTORS.items()
}

_NEGATIVE_EMOTIONS = frozenset({"Anger", "Fear", "Sadness", "Disgust", "Contempt"})


def _neutral_result(face_detected: bool = False) -> Dict[str, Any]:
    mapping = EMOTION_WELLNESS_MAP["Neutral"]
    return {
        "raw_emotion": "Neutral",
        "descriptor": mapping["descriptor"],
        "label": mapping["label"],
        "tone": mapping["tone"],
        "confidence": 1.0,
        "face_detected": face_detected,
        "face_box": None,
        "timestamp": time.time(),
    }


class EmotionDetector:
    """Singleton detector caching the HSEmotion ONNX session and Haar cascade."""

    _instance: Optional[EmotionDetector] = None
    _init_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._cascade = None
        self._load_error: Optional[str] = None
        self._prev_raw: str = "Neutral"
        self._last_result: Dict[str, Any] = _neutral_result(face_detected=False)
        self._init_models()

    @classmethod
    def get_instance(cls) -> EmotionDetector:
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = EmotionDetector()
            return cls._instance

    def _init_models(self) -> None:
        if cv2 is None or np is None:
            self._load_error = "opencv-python / numpy not installed"
            return

        try:
            from hsemotion_onnx.facial_emotions import HSEmotionRecognizer

            # Loads cached enet_b0_8_best_vgaf.onnx from ~/.hsemotion
            self._model = HSEmotionRecognizer(model_name="enet_b0_8_best_vgaf")
        except Exception as exc:
            self._load_error = f"HSEmotionRecognizer init failed: {exc}"
            print(f"[EmotionDetector] {self._load_error}", file=sys.stderr)

        try:
            cascade_path = os.path.join(
                cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
            )
            if os.path.exists(cascade_path):
                self._cascade = cv2.CascadeClassifier(cascade_path)
        except Exception as exc:
            print(f"[EmotionDetector] Cascade init warning: {exc}", file=sys.stderr)

    def detect_emotion_from_bgr(self, frame: Any) -> Dict[str, Any]:
        """Detect emotion from an OpenCV BGR frame in <30ms."""
        if (
            cv2 is None
            or np is None
            or frame is None
            or getattr(frame, "size", 0) == 0
            or self._model is None
        ):
            return dict(self._last_result)

        h, w = frame.shape[:2]
        face_roi = None
        face_detected = False
        face_box = None

        # 1. Try Haar face detection with histogram equalization for dim rooms
        if self._cascade is not None and not self._cascade.empty():
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray_eq = cv2.equalizeHist(gray)
                faces = self._cascade.detectMultiScale(
                    gray_eq, scaleFactor=1.25, minNeighbors=4, minSize=(36, 36)
                )
                if len(faces) == 0:
                    faces = self._cascade.detectMultiScale(
                        gray, scaleFactor=1.25, minNeighbors=4, minSize=(36, 36)
                    )
                if len(faces) > 0:
                    fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                    H, W = gray.shape[:2]
                    face_box = {
                        "cx": round((fx + fw * 0.5) / W, 4),
                        "cy": round((fy + fh * 0.5) / H, 4),
                        "w": round(fw / W, 4),
                        "h": round(fh / H, 4),
                    }
                    pad_x = int(fw * 0.1)
                    pad_y = int(fh * 0.1)
                    x1 = max(0, fx - pad_x)
                    y1 = max(0, fy - pad_y)
                    x2 = min(w, fx + fw + pad_x)
                    y2 = min(h, fy + fh + pad_y)
                    face_roi = frame[y1:y2, x1:x2]
                    face_detected = True
            except Exception:
                face_roi = None
                face_box = None

        # 2. If no face is detected by Haar Cascade, do NOT feed background wall
        # textures into EfficientNet as negative emotions (walls often classify
        # as Sadness/Fear/Anger). Return a calm baseline until a face is seen.
        if not face_detected or face_roi is None or face_roi.size == 0 or face_roi.shape[0] < 12 or face_roi.shape[1] < 12:
            with self._lock:
                self._prev_raw = "Neutral"
                self._last_result = _neutral_result(face_detected=False)
                return dict(self._last_result)

        # 3. ONNX HSEmotion prediction (EfficientNet expects RGB format)
        try:
            try:
                face_rgb = cv2.cvtColor(face_roi, cv2.COLOR_BGR2RGB)
            except Exception:
                face_rgb = face_roi

            with self._lock:
                raw_emo, scores = self._model.predict_emotions(
                    face_rgb, logits=False
                )
                conf = float(np.max(scores)) if scores is not None else 0.90
                raw_str = str(raw_emo)

                # Temporal & confidence gate: prevent single-frame talking/blinking
                # artifacts from triggering false Anger/Fear/Disgust alerts.
                if raw_str in _NEGATIVE_EMOTIONS and conf < 0.48 and self._prev_raw != raw_str:
                    self._prev_raw = raw_str
                    raw_str = "Neutral"
                else:
                    self._prev_raw = raw_str

                mapping = EMOTION_WELLNESS_MAP.get(
                    raw_str,
                    EMOTION_WELLNESS_MAP["Neutral"],
                )

                result = {
                    "raw_emotion": raw_str,
                    "descriptor": mapping["descriptor"],
                    "label": mapping["label"],
                    "tone": mapping.get("tone", "balanced"),
                    "confidence": round(conf, 3),
                    "face_detected": face_detected,
                    "face_box": face_box,
                    "timestamp": time.time(),
                }
                self._last_result = result
                return dict(result)
        except Exception as exc:
            print(f"[EmotionDetector] Prediction failed: {exc}", file=sys.stderr)
            return dict(self._last_result)

    def detect_from_base64(self, data_url_or_b64: str) -> Dict[str, Any]:
        """Decode base64 JPEG from browser canvas and classify emotion."""
        if not data_url_or_b64 or cv2 is None or np is None:
            return dict(self._last_result)

        try:
            # Strip data URL prefix if present: "data:image/jpeg;base64,..."
            if "," in data_url_or_b64:
                b64_str = data_url_or_b64.split(",", 1)[1]
            else:
                b64_str = data_url_or_b64

            raw_bytes = base64.b64decode(b64_str)
            arr = np.frombuffer(raw_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return dict(self._last_result)
            return self.detect_emotion_from_bgr(frame)
        except Exception as exc:
            print(f"[EmotionDetector] base64 decode failed: {exc}", file=sys.stderr)
            return dict(self._last_result)

    def get_latest(self) -> Dict[str, Any]:
        return dict(self._last_result)