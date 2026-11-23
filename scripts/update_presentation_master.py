"""Master update script for KINETIQ Final Review Presentation.

Reflects all recent architectural milestones:
- Local offline 478-pt MediaPipe FaceLandmarker + 5 clean emotions + posture coaching
- Local LLM upgrade to Qwen 3.5 (4B) with think:false (<3.5s warm inference)
- Fail-closed Grounding Receipt audit modal & multi-turn memory
- Band 6 live diagnostics (:8000 receiver health, missing field detection, partial HR)
- 8/8 automated morning check gates & full test suites (22/22 receiver, 13/13 web server)
- One-click launcher (LAUNCH_FINAL_DEMO.bat / scripts/launch_final_demo.ps1) with auto-healing ADB
"""
from __future__ import annotations

import sys
from pathlib import Path
from pptx import Presentation

ROOT = Path(__file__).resolve().parent.parent
SRC_PPTX = ROOT / "docs" / "final review" / "KINETIQ_final_review_BACKUP.pptx"
DST_PPTX = ROOT / "docs" / "final review" / "KINETIQ_final_review_UPDATED.pptx"


def set_shape_text(shape, text_or_lines):
    """Set text on shape while keeping paragraph font styling where possible."""
    if not shape.has_text_frame:
        return
    tf = shape.text_frame
    if isinstance(text_or_lines, str):
        lines = text_or_lines.split("\n")
    else:
        lines = list(text_or_lines)

    # If shape already has paragraphs, update in-place
    for idx, line in enumerate(lines):
        if idx < len(tf.paragraphs):
            p = tf.paragraphs[idx]
            if p.runs:
                p.runs[0].text = line
                for r in p.runs[1:]:
                    r.text = ""
            else:
                p.text = line
        else:
            p = tf.add_paragraph()
            p.text = line


def update_deck():
    print(f"Loading: {SRC_PPTX}")
    prs = Presentation(str(SRC_PPTX))

    # =========================================================================
    # SLIDE 2: KINETIQ (Subtitle & team names)
    # =========================================================================
    s2 = prs.slides[1]
    # Shape 1: Subtitle
    if len(s2.shapes[1].text_frame.paragraphs) >= 2:
        p1 = s2.shapes[1].text_frame.paragraphs[1]
        if p1.runs:
            p1.runs[0].text = (
                "Genuine Huawei Band 6 vitals, FreeRTOS control lane, "
                "offline MediaPipe vision and local AI assistant"
            )
            for r in p1.runs[1:]:
                r.text = ""

    # Shape 6: Presented by
    if len(s2.shapes) > 6 and s2.shapes[6].has_text_frame:
        set_shape_text(
            s2.shapes[6],
            [
                "PRESENTED BY:",
                "Sidharth Sajith",
                "Tharun Oommen Jacob",
                "Shahazad Abdulla",
            ],
        )

    if s2.has_notes_slide:
        s2.notes_slide.notes_text_frame.text = (
            "Good morning. We are presenting KINETIQ: a real-time wellness mirror "
            "that integrates genuine Huawei Band 6 biometrics, an authentic FreeRTOS v11 "
            "control lane, local offline 478-point MediaPipe vision, and a sensor-grounded "
            "local AI assistant with fail-closed safety interlocks."
        )

    # =========================================================================
    # SLIDE 3: PROBLEM & SCOPE
    # =========================================================================
    s3 = prs.slides[2]
    if s3.has_notes_slide:
        s3.notes_slide.notes_text_frame.text = (
            "Start with the three critical failures we identified: 1) vendor health data "
            "is trapped behind proprietary walled gardens; 2) generic AI chatbots hallucinate "
            "because they have zero biometric grounding; and 3) cached historical data "
            "frequently masquerades as live readings. Then state our boundaries clearly: "
            "wellness guidance only, never formal medical diagnosis, and 100% local operation."
        )

    # =========================================================================
    # SLIDE 4: OBJECTIVES
    # =========================================================================
    s4 = prs.slides[3]
    if s4.has_notes_slide:
        s4.notes_slide.notes_text_frame.text = (
            "Four engineering objectives: 1) Extract real retail Band 6 data with zero root "
            "and zero HMS modification; 2) Enforce complete provenance honesty with live, stale, "
            "and offline clocks; 3) Provide a unified dual-lane mirror UI switching between Band 6 "
            "and FreeRTOS; and 4) Maintain additive, regression-free modular architecture."
        )

    # =========================================================================
    # SLIDE 5: HOW IT WORKS (Architecture Diagram)
    # =========================================================================
    s5 = prs.slides[4]
    # Shape 23: Camera + local AI
    if len(s5.shapes) > 23 and s5.shapes[23].has_text_frame:
        tf = s5.shapes[23].text_frame
        if len(tf.paragraphs) >= 2:
            p1 = tf.paragraphs[1]
            if p1.runs:
                p1.runs[0].text = "MediaPipe (offline) · Qwen 3.5 (4B)"
                for r in p1.runs[1:]:
                    r.text = ""

    # Shape 27: 5 s presence · AI
    if len(s5.shapes) > 27 and s5.shapes[27].has_text_frame:
        p0 = s5.shapes[27].text_frame.paragraphs[0]
        if p0.runs:
            p0.runs[0].text = "5s presence · 5-emotion vision"
            for r in p0.runs[1:]:
                r.text = ""

    if s5.has_notes_slide:
        s5.notes_slide.notes_text_frame.text = (
            "Walk left to right across Lane A: retail Huawei Band 6 sends biometrics over BLE to "
            "unmodified Huawei Health on Android; our KinetiQ Streamer AccessibilityService relays "
            "readings over USB reverse tunnel to the local receiver on port 8000. Lane B runs the native "
            "FreeRTOS v11 kernel node. Both feed into the Home Edition Mirror on port 8777, powered by "
            "offline local MediaPipe WASM vision and a local Qwen 3.5 (4B) LLM running in Ollama."
        )

    # =========================================================================
    # SLIDE 6: WHAT WORKS TODAY
    # =========================================================================
    s6 = prs.slides[5]
    # Shape 13: Local AI + presence
    if len(s6.shapes) > 13 and s6.shapes[13].has_text_frame:
        tf = s6.shapes[13].text_frame
        if len(tf.paragraphs) >= 1:
            tf.paragraphs[0].text = "Local AI + 5-Emotion Vision"
        if len(tf.paragraphs) >= 2:
            p1 = tf.paragraphs[1]
            if p1.runs:
                p1.runs[0].text = (
                    "Offline 478-pt MediaPipe FaceLandmarker tracks 5 clean expressions & posture. "
                    "Qwen 3.5 (4B) grounds replies in live vitals."
                )
                for r in p1.runs[1:]:
                    r.text = ""

    # Shape 14: Honest by design
    if len(s6.shapes) > 14 and s6.shapes[14].has_text_frame:
        tf = s6.shapes[14].text_frame
        if len(tf.paragraphs) >= 2:
            p1 = tf.paragraphs[1]
            if p1.runs:
                p1.runs[0].text = (
                    "Simulated data is locked to its own lane. "
                    "Fail-closed grounding receipts, token auth, and 8 automated staging gates."
                )
                for r in p1.runs[1:]:
                    r.text = ""

    if s6.has_notes_slide:
        s6.notes_slide.notes_text_frame.text = (
            "Highlight the working deliverables: 1) Genuine Band 6 heart rate, SpO2, stress, and steps; "
            "2) The zero-scroll Home Edition mirror; 3) Seamless one-click lane switching; 4) Offline "
            "478-point MediaPipe face landmarker detecting 5 responsive emotions (Happy, Calm, Sad, Angry, "
            "Surprised) with head roll posture coaching; and 5) Local Qwen 3.5:4B assistant with verifiable "
            "grounding receipts passing 8 rigorous automated gates."
        )

    # =========================================================================
    # SLIDE 7: NEW MIRROR UI
    # =========================================================================
    s7 = prs.slides[6]
    # Update bullet shapes 10, 11, 12, 13
    bullet_updates = [
        (10, "One glance: live heart rate, SpO₂, stress score, and daily steps."),
        (11, "Offline 478-pt MediaPipe constellation & real-time posture coaching."),
        (12, "5-emotion reactive preview pill with subtle dot tints & tone adaptation."),
        (13, "Grounding Receipt modal: full reply audit, verified vitals & drift block."),
    ]
    for sh_idx, text in bullet_updates:
        if len(s7.shapes) > sh_idx and s7.shapes[sh_idx].has_text_frame:
            tf = s7.shapes[sh_idx].text_frame
            if tf.paragraphs and tf.paragraphs[0].runs:
                if len(tf.paragraphs[0].runs) >= 2:
                    tf.paragraphs[0].runs[0].text = "•  "
                    tf.paragraphs[0].runs[1].text = text
                else:
                    tf.paragraphs[0].runs[0].text = "•  " + text

    if s7.has_notes_slide:
        s7.notes_slide.notes_text_frame.text = (
            "This is our Home Edition interface: a zero-scroll, calm personal wellness mirror. "
            "It features an ethereal 478-point MediaPipe facial constellation, real-time posture "
            "guidance (detecting head tilt and slouching), responsive 5-emotion preview pills with "
            "subtle status tints, and an interactive Grounding Receipt modal that transparently audits "
            "every LLM response against live sensor data. The core /classic dashboard remains 1 click away."
        )

    # =========================================================================
    # SLIDE 8: RESULTS & EVIDENCE
    # =========================================================================
    s8 = prs.slides[7]
    # Table 11
    if len(s8.shapes) > 11 and s8.shapes[11].has_table:
        tbl = s8.shapes[11].table
        # Row 2: Receiver tests
        tbl.cell(2, 0).text = "Receiver test suite"
        tbl.cell(2, 1).text = "22 / 22 pass"
        # Row 3: Web server tests
        tbl.cell(3, 0).text = "Web server suite"
        tbl.cell(3, 1).text = "13 / 13 pass"
        # Row 4: Morning check gates
        tbl.cell(4, 0).text = "Morning check gates"
        tbl.cell(4, 1).text = "8 / 8 pass (0 fail, 0 skip)"
        # Row 5: LLM latency
        tbl.cell(5, 0).text = "Local LLM latency"
        tbl.cell(5, 1).text = "~3.5 s (Qwen 3.5:4B warm)"

    # Bullets (Shapes 13, 14, 15, 16)
    b_updates_s8 = [
        (13, "A retail band feeds a custom mirror with no root, no HMS, and no change to Health."),
        (14, "Stress and steps are genuine. Offline MediaPipe classifies 5 emotions & posture at 22 FPS on GPU."),
        (15, "Freshness is visible: live under 60 s, stale 60–120 s, offline after 300 s (15s/30s demo mode)."),
        (16, "Local Qwen 3.5 (4B) grounds replies in biometrics; ungrounded drift is intercepted fail-closed."),
    ]
    for sh_idx, text in b_updates_s8:
        if len(s8.shapes) > sh_idx and s8.shapes[sh_idx].has_text_frame:
            tf = s8.shapes[sh_idx].text_frame
            if tf.paragraphs and tf.paragraphs[0].runs:
                if len(tf.paragraphs[0].runs) >= 2:
                    tf.paragraphs[0].runs[0].text = "•  "
                    tf.paragraphs[0].runs[1].text = text
                else:
                    tf.paragraphs[0].runs[0].text = "•  " + text

    if s8.has_notes_slide:
        s8.notes_slide.notes_text_frame.text = (
            "Here is the empirical evidence: 60 BPM heart rate, 100% SpO2, 57/100 stress score, and "
            "4,181 steps verified directly from the Huawei Band 6 relay. All automated test suites "
            "pass 100%: 22/22 receiver unit tests, 13/13 web server route tests, and 8/8 comprehensive "
            "morning staging gates. Local Qwen 3.5:4B achieves sub-3.5s warm inference latency on device."
        )

    # =========================================================================
    # SLIDE 9: SCOPE, LIMITS & SAFETY
    # =========================================================================
    s9 = prs.slides[8]
    # Table 6
    if len(s9.shapes) > 6 and s9.shapes[6].has_table:
        tbl9 = s9.shapes[6].table
        tbl9.cell(3, 1).text = "Receiver, mirror, Qwen 3.5"
        tbl9.cell(4, 1).text = "478-pt MediaPipe & 5 emotions"

    # Shape 7: KNOWN LIMITS
    if len(s9.shapes) > 7 and s9.shapes[7].has_text_frame:
        tf9 = s9.shapes[7].text_frame
        limits_text = [
            "KNOWN LIMITS",
            "•  Health home screen must stay open; otherwise the relay ages out.",
            "•  SpO₂ is the last completed value, not a continuous trace.",
            "•  Activity and motion are unmeasured in band packet; camera provides visual motion.",
            "•  Physical ESP32 is a future path; native FreeRTOS v11 simulator provides verified control lane.",
            "•  100% offline & local: MediaPipe WASM and Ollama run without external internet.",
            "•  Wellness guidance only, never a medical diagnosis; fail-closed interlock blocks drift.",
        ]
        set_shape_text(s9.shapes[7], limits_text)

    if s9.has_notes_slide:
        s9.notes_slide.notes_text_frame.text = (
            "Safety and ethical boundaries: We emphasize that KINETIQ is designed strictly for "
            "wellness reflection and posture awareness, never clinical diagnostics or medical alarms. "
            "Our fail-closed interlock physically halts speech synthesis if an LLM hallucinates an "
            "unverified vital. The entire pipeline operates 100% locally with zero cloud dependencies."
        )

    # =========================================================================
    # SLIDE 10: FINAL DEMO PLAN
    # =========================================================================
    s10 = prs.slides[9]
    # Step 1 (Shape 7)
    if len(s10.shapes) > 7 and s10.shapes[7].has_text_frame:
        set_shape_text(
            s10.shapes[7],
            [
                "Start the stack",
                "Run LAUNCH_FINAL_DEMO.bat. Receiver starts on :8000 with auto-healing ADB reverse, and mirror opens on :8777.",
            ],
        )

    # Step 2 (Shape 9)
    if len(s10.shapes) > 9 and s10.shapes[9].has_text_frame:
        set_shape_text(
            s10.shapes[9],
            [
                "Show the real lane",
                "Health on phone, Streamer on, USB connected. Live HR, SpO₂, stress and steps appear.",
            ],
        )

    # Step 3 (Shape 11)
    if len(s10.shapes) > 11 and s10.shapes[11].has_text_frame:
        set_shape_text(
            s10.shapes[11],
            [
                "Show the RTOS lane",
                "Switch to FreeRTOS v11 lane. 1 Hz synthetic JSON and task timing diagnostics stream live.",
            ],
        )

    # Step 4 (Shape 13)
    if len(s10.shapes) > 13 and s10.shapes[13].has_text_frame:
        set_shape_text(
            s10.shapes[13],
            [
                "Show vision & mirror",
                "Demonstrate 478-pt MediaPipe facial constellation, live 5-emotion pill, and posture coaching.",
            ],
        )

    # Step 5 (Shape 15)
    if len(s10.shapes) > 15 and s10.shapes[15].has_text_frame:
        set_shape_text(
            s10.shapes[15],
            [
                "Ask the assistant",
                "Speak or tap orb. Qwen 3.5 answers grounded in live vitals & posture; audit via Grounding Receipt.",
            ],
        )

    # Fallback box (Shape 16)
    if len(s10.shapes) > 16 and s10.shapes[16].has_text_frame:
        set_shape_text(
            s10.shapes[16],
            [
                "IF SOMETHING FAILS",
                "•  Band lane: re-open Huawei Health on its home screen and check USB reverse tunnel.",
                "•  RTOS lane: run the native node alone; it needs no hardware.",
                "•  AI: fail-closed grounding intercepts ungrounded drift with safe verified fallback.",
                "•  Verification: scripts/morning_check.ps1 validates all 8 staging gates green.",
                "BEFORE YOU START",
                "Charge the band · warm up Ollama (qwen3.5:4b) · close other webcam apps.",
            ],
        )

    if s10.has_notes_slide:
        s10.notes_slide.notes_text_frame.text = (
            "Our 5-step live demonstration: 1) One-click launch with LAUNCH_FINAL_DEMO.bat; "
            "2) Showcase genuine Huawei Band 6 biometrics streaming live; 3) Switch to the FreeRTOS v11 "
            "synthetic control lane showing queue metrics and deadline diagnostics; 4) Demonstrate offline "
            "478-point MediaPipe face mesh, 5 clean emotions, and posture reminders; and 5) Query local "
            "Qwen 3.5:4B assistant and open the Grounding Receipt modal to prove zero hallucination."
        )

    # =========================================================================
    # SLIDE 11: CONCLUSION / THANK YOU
    # =========================================================================
    s11 = prs.slides[10]
    if s11.has_notes_slide:
        s11.notes_slide.notes_text_frame.text = (
            "To conclude: KINETIQ proves that retail wearables can be unlocked without root, "
            "firmware timing can be transparently audited with FreeRTOS, and local vision and AI "
            "can deliver responsive wellness guidance with absolute sensor-grounded honesty. "
            "Thank you, and we welcome your questions."
        )

    prs.save(str(DST_PPTX))
    print(f"Presentation successfully updated and saved to: {DST_PPTX}")


if __name__ == "__main__":
    update_deck()
