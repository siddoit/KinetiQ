# KINETIQ Minor Project Report

This folder contains the editable LaTeX source and final PDF for **KINETIQ — Real-Time Wellness Mirror with FreeRTOS and Sensor-Grounded AI**.

## Contents

- `main.tex` — document preamble, preliminary pages, and build entry point.
- `chapters/` — seven numbered chapters and appendices.
- `figures/` — logo, original project visuals, and in-document TikZ diagrams.
- `references.bib` — numbered bibliography database.
- `KINETIQ_Minor_Project_Report.pdf` — compiled submission PDF.
- `build/` — LaTeX intermediate build files.

## Build requirements

- TeX Live or MiKTeX with XeLaTeX, Biber, TikZ, `biblatex`, and TeX Gyre fonts.
- A shell capable of running the commands below.

## Rebuild the report

Run these commands from this folder:

```powershell
xelatex -interaction=nonstopmode -output-directory=build main.tex
biber build/main
xelatex -interaction=nonstopmode -output-directory=build main.tex
xelatex -interaction=nonstopmode -output-directory=build main.tex
Copy-Item build/main.pdf KINETIQ_Minor_Project_Report.pdf
```

The second XeLaTeX run resolves citations; the final run resolves the table of contents, figure list, table list, and cross-references.

## Evidence boundary

The report deliberately separates documented Huawei Health relay records, synthetic mock/RTOS inputs, Wokwi simulation, and planned physical ESP32 hardware. It does not claim direct Huawei Band API integration, clinical validity, physical sensor acquisition, or fail-closed AI safety where the repository evidence does not support those claims.

## Administrative placeholders

Register numbers, certificate approvals, signatures, academic-year entry, and submission date were not supplied. They remain visibly marked for authorised institutional completion.
