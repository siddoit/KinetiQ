"""Update the existing KINETIQ final-review deck with final project status.

This script intentionally reuses the established presentation, its theme and its
graphics.  It changes only text/table content so the final evidence lanes and
safety limits match docs/report/report.md.
"""

from __future__ import annotations

import argparse
from copy import copy
from pathlib import Path

from pptx import Presentation
from pptx.enum.dml import MSO_COLOR_TYPE
from pptx.util import Pt


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "docs" / "final review" / "KINETIQ_V1_final_review.pptx"


def _style(shape):
    """Capture the first-run style before a text frame is cleared."""
    paragraph = shape.text_frame.paragraphs[0]
    run = paragraph.runs[0] if paragraph.runs else None
    if run is None:
        return {}
    font = run.font
    data = {
        "name": font.name,
        "size": font.size,
        "bold": font.bold,
        "italic": font.italic,
    }
    try:
        if font.color.type == MSO_COLOR_TYPE.RGB:
            data["rgb"] = font.color.rgb
    except AttributeError:
        pass
    return data


def set_text(shape, text: str, size: float | None = None) -> None:
    """Replace text while preserving the template's base text style."""
    saved = _style(shape)
    frame = shape.text_frame
    frame.clear()
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    font = run.font
    if saved.get("name"):
        font.name = saved["name"]
    font.size = Pt(size) if size else saved.get("size")
    font.bold = saved.get("bold")
    font.italic = saved.get("italic")
    if saved.get("rgb") is not None:
        font.color.rgb = saved["rgb"]
    frame.word_wrap = True


def set_cell(cell, text: str, size: float | None = None) -> None:
    """Replace one table cell, retaining its template text colour/style."""
    saved = _style(cell)
    frame = cell.text_frame
    frame.clear()
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    font = run.font
    if saved.get("name"):
        font.name = saved["name"]
    font.size = Pt(size) if size else saved.get("size")
    font.bold = saved.get("bold")
    font.italic = saved.get("italic")
    if saved.get("rgb") is not None:
        font.color.rgb = saved["rgb"]
    frame.word_wrap = True


def table_values(table, rows: list[list[str]], size: float = 10.0) -> None:
    for row_index, values in enumerate(rows):
        for column_index, value in enumerate(values):
            # Keep the template's header weight; make body dense but readable.
            set_cell(table.cell(row_index, column_index), value,
                     None if row_index == 0 else size)


def update_deck(prs: Presentation) -> None:
    slides = prs.slides

    # 1. Cover
    set_text(slides[0].shapes[4], "B.TECH MINOR PROJECT\nFINAL STATUS UPDATE\n23/10/2026")

    # 2. Title
    set_text(slides[1].shapes[1],
             "Real-Time Wellness Mirror with FreeRTOS and Sensor-Grounded AI\n"
             "Two evidence lanes: genuine Huawei Band 6 biometrics + deterministic RTOS control",
             20)
    set_text(slides[1].shapes[6],
             "PRESENTED BY:\n\tSidharth Sajith\n\tTharun Oommen Jacob",
             16)

    # 3. Problem
    set_text(slides[2].shapes[3],
             "Live wearable data is difficult to carry into a custom application.\n\n"
             "Generic AI can give advice without current context.\n\n"
             "A cached value can look current unless source and freshness are visible.\n\n"
             "Our approach: one wellness mirror, two clearly separated evidence lanes, "
             "a strict telemetry contract and a visible grounding verdict.\n\n"
             "Scope: wellness assistance only — never diagnosis or clinical alerting.",
             18)
    set_text(slides[2].shapes[5],
             "The mirror never hides whether a value is genuine, synthetic, fresh or stale.",
             16)

    # 4. Objectives
    set_text(slides[3].shapes[3],
             "1. FreeRTOS control path\n"
             "Real v11 tasks, queue, mutex and timing diagnostics in an offline node.\n\n"
             "2. Genuine wearable lane\n"
             "Huawei Band 6 → Huawei Health → local authenticated receiver → mirror.\n\n"
             "3. Strict telemetry boundary\n"
             "Exactly eight validated fields; malformed frames never reach the UI or AI.\n\n"
             "4. Honest mirror assistance\n"
             "Source, link and freshness stay visible; responses carry a grounding PASS/FAIL.\n\n"
             "5. Reproducible demo\n"
             "Lane B remains available offline if the phone or wearable is unavailable.",
             16)

    # 5. Methodology flow and explanation
    for index, text in {
        3: "SENSE\nBand 6 / virtual IMU",
        5: "PROCESS\ntasks · queue · validate",
        7: "INFER\nrules · freshness",
        9: "COMMUNICATE\nlocal relay · stdin",
        11: "ASSIST\nmirror · STT · LLM",
    }.items():
        set_text(slides[4].shapes[index], text, 12)
    set_text(slides[4].shapes[13],
             "LANE A — Genuine Band 6 values\n"
             "Huawei Health remains untouched. The phone service reads visible Health tiles, "
             "the receiver authenticates POSTs, and the bridge emits only fresh, complete HR, SpO₂ and steps.\n\n"
             "LANE B — RTOS control\n"
             "A native FreeRTOS v11 node emits the same JSON contract. Keys force rest, walk, run or a fall event; diagnostics stay on stderr.\n\n"
             "Shared mirror\n"
             "The browser validates input, labels source and freshness, and grounds text/voice replies in the current packet.",
             13)

    # 6. Demo path
    set_text(slides[5].shapes[3],
             "LANE A · GENUINE BAND 6\n"
             "Huawei Health UI\n"
             "    ↓ Accessibility streamer + token POST\n"
             "Receiver :8000 → genuine-only bridge\n"
             "    ↓ fresh + complete HR / SpO₂ / steps only\n"
             "Mirror stdin\n\n"
             "LANE B · OFFLINE RTOS CONTROL\n"
             "FreeRTOS v11 node → schema JSON stdout\n"
             "keys: 1 rest · 2 walk · 3 run · b fall · 0 auto\n"
             "stderr: period, lateness, queue HWM, drops, missed deadlines",
             14)
    set_text(slides[5].shapes[4],
             "1. Browser mirror\nShows HR, SpO₂, activity, exertion, steps, link and freshness. OFFLINE blanks stale numbers.\n\n"
             "2. Evidence gate\nThe Band bridge refuses simulated, incomplete or non-live readings. The RTOS lane is explicitly labelled control/synthetic.\n\n"
             "3. Grounded assistant\nTyped or voice question → current telemetry context → reply with visible PASS/FAIL.\n\n"
             "4. Stage-safe fallback\nIf the Band path is unavailable, Lane B demonstrates the same validated interface without fabricated Band frames.",
             14)

    # 7. Results
    set_text(slides[6].shapes[0], "RESULTS & EVIDENCE")
    set_text(slides[6].shapes[4],
             "Genuine wearable evidence\n"
             "Documented 22 Oct relay: 60 BPM · 100% SpO₂ · stress 57 · 4,181 steps.\n\n"
             "Validation and transport\n"
             "Locked eight-field schema rejects missing/unknown values. Recorded dashboard mock: 75 frames / 15 s, zero drops. BLE mock: 30 routed, zero rejects.\n\n"
             "Voice + grounding\n"
             "faster-whisper real path: 0.48 s on a synthesized clip. Mirror shows grounding PASS/FAIL beside every answer.\n\n"
             "RTOS transparency\n"
             "Native FreeRTOS task/queue timing is visible on stderr; telemetry is synthetic and labelled RTOS CONTROL.",
             14)

    # 8. Hardware / scope
    set_text(slides[7].shapes[0], "HARDWARE & RUNTIME SCOPE")
    hardware = [
        ["#", "ITEM", "QTY", "₹", "ROLE / STATUS"],
        ["1", "Native FreeRTOS v11 node", "1", "—", "active offline control lane"],
        ["2", "Huawei Band 6 + Health relay", "1", "—", "active genuine-biometrics lane"],
        ["3", "ESP32 DevKit V1 WROOM-32", "2", "998", "BOM / board path"],
        ["4", "MPU6050 + HC-SR04 + IR", "1", "470", "planned sensing path"],
        ["5", "DHT22 + breadboard/jumpers", "1", "808", "planned sensing path"],
        ["6", "Wokwi ESP32 simulation", "1", "—", "browser RTOS exhibit"],
        ["", "Tier-A BOM retained", "", "≈1,950", "design evidence; not new field data"],
    ]
    table_values(slides[7].shapes[3].table, hardware, 9)
    set_text(slides[7].shapes[4],
             "Current scope\n"
             "The final demo does not claim new physical ESP32 sensor readings. The ESP32 BOM, pin map and Wokwi sketch remain embedded-design evidence.\n\n"
             "Why two runtime lanes?\n"
             "The Band path supplies genuine wearable values when Health refreshes. The native node provides a dependable FreeRTOS demonstration without pretending to be a person or a medical sensor.\n\n"
             "Safety\n"
             "All displayed values are wellness information, not diagnosis or treatment.",
             13)

    # 9. Final status
    set_text(slides[8].shapes[0], "FINAL STATUS & DEMO PLAN")
    status = [
        ["TRACK", "DEMO PATH", "STATUS"],
        ["Lane A", "Band 6 → Health → receiver → mirror", "genuine when fresh"],
        ["Lane B", "native FreeRTOS → mirror", "offline / deterministic"],
        ["Freshness", "LIVE · UNCHANGED · STALE · OFFLINE", "shown on screen"],
        ["AI", "current packet → reply → grounding verdict", "PASS required for evidence"],
        ["Fallback", "run Lane B; never invent Band frames", "operator-ready"],
        ["ESP32", "Wokwi + BOM + pin plan", "design / simulation evidence"],
        ["Operator", "lane scripts and pre-stage checks", "repeatable"],
    ]
    table_values(slides[8].shapes[3].table, status, 9)
    set_text(slides[8].shapes[4],
             "Run order\n"
             "1. Start Lane A and refresh Huawei Health; show source and freshness.\n"
             "2. Start Lane B; press 1 / 2 / 3 / b to show controlled RTOS changes.\n"
             "3. Ask the mirror a question and show the grounding verdict.\n\n"
             "Key limits to say aloud\n"
             "Band activity/motion are unknown, native physiology is synthetic, and the mirror is wellness assistance only.\n\n"
             "Scripts\n"
             "demo_band_lane.ps1 · demo_rtos_lane.ps1 · demo_fallback.ps1",
             13)

    # 10. Closing
    set_text(slides[9].shapes[2],
             "THANK YOU\n\n"
             "KINETIQ\n"
             "Live source · honest freshness · grounded wellness assistance",
             27)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    presentation = Presentation(args.input)
    if len(presentation.slides) != 10:
        raise SystemExit("expected the ten-slide KINETIQ final-review template")
    update_deck(presentation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(args.output)
    print(f"updated deck written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
