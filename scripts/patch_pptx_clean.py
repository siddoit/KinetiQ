"""Non-destructive patch script for KINETIQ Final Review Presentation."""
from __future__ import annotations
import shutil
from pathlib import Path
from pptx import Presentation

ROOT = Path(__file__).resolve().parent.parent
SRC_PPTX = ROOT / "docs" / "final review" / "KINETIQ_final_review_23-10-2026.pptx"
DST_PPTX = ROOT / "docs" / "final review" / "KINETIQ_final_review_UPDATED.pptx"

REPLACEMENTS = [
    (
        "Drive the partner KINETIQ mirror with the same real values, without editing their code.",
        "Layer the Home Edition Mirror (/) over the Core Dashboard (/classic) cleanly via a modular bridge contract.",
    ),
    ("Partner KINETIQ mirror", "Core KINETIQ Mirror (/classic)"),
    ("partner KINETIQ mirror", "Core KINETIQ Mirror (/classic)"),
    ("their code untouched", "core preserved intact"),
    ("their code is untouched", "core dashboard preserved intact"),
    ("Partner repository", "Core architecture"),
    ("partner repository", "core architecture"),
    ("Their repo unchanged", "Modular & regression-free"),
    ("their repo unchanged", "modular & regression-free"),
    ("Partner mirror", "Core /classic view"),
    ("partner mirror", "core /classic view"),
    ("offline after 120 s", "offline after 300 s (15s/30s fast demo mode)"),
    ("37 automated tests", "40 automated tests + 8 system gates"),
]

def replace_in_text(text: str) -> tuple[str, int]:
    count = 0
    new_text = text
    for old, new in REPLACEMENTS:
        if old in new_text:
            count += new_text.count(old)
            new_text = new_text.replace(old, new)
    return new_text, count

def patch_deck():
    print(f"Source: {SRC_PPTX}")
    print(f"Destination: {DST_PPTX}")
    prs = Presentation(str(SRC_PPTX))
    total_replaced = 0

    for slide_idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    for r in p.runs:
                        new_r, c = replace_in_text(r.text)
                        if c > 0:
                            r.text = new_r
                            total_replaced += c
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        for p in cell.text_frame.paragraphs:
                            for r in p.runs:
                                new_r, c = replace_in_text(r.text)
                                if c > 0:
                                    r.text = new_r
                                    total_replaced += c
        # Notes slide
        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            for p in notes_frame.paragraphs:
                for r in p.runs:
                    new_r, c = replace_in_text(r.text)
                    if c > 0:
                        r.text = new_r
                        total_replaced += c

    prs.save(str(DST_PPTX))
    print(f"Patched successfully! Total replacements made: {total_replaced}")
    print(f"Saved: {DST_PPTX}")

if __name__ == "__main__":
    patch_deck()
