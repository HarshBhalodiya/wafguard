# Problem Statement

## Background

In modern advanced semiconductor fabrication (at 3nm/5nm sub-micron nodes), manufacturing microchips requires hundreds of ultra-precise process steps involving thousands of equipment sensors, complex chemical recipes, spatial wafer die inspections, and physical etching/deposition steps. Even minor equipment drift or subtle multi-variable sensor deviations can cause catastrophic yield drops across entire wafer lots.

---

## The Problem

At 3nm/5nm chip nodes, root causes of low wafer yield hide across thousands of high-dimensional equipment sensors, process parameter streams, spatial wafer defect maps, and maintenance logs. Yield and process engineers currently spend weeks manually sifting through disconnected log tools, sensor databases, and defect inspection reports to diagnose a single yield failure incident — reacting long after low-yield wafers have already completed processing and become costly scrap.

---

## Who is Affected

This problem directly impacts **Semiconductor Process Engineers**, **Yield Enhancement Engineers**, and **Fab Operations Leads** in high-volume leading-edge semiconductor fabrication plants who are responsible for maintaining target wafer yields, optimizing tool uptime, and preventing scrap.

---

## Why It Matters

- **Massive Financial Loss:** At leading 3nm/5nm nodes, a mere 1% drop in wafer yield costs semiconductor foundries tens of millions of dollars per month in lost revenue.
- **Costly Delays & Downtime:** Every day spent manually tracking down root causes extends tool downtime, increases engineer burn-out, and compounds financial waste.
- **Unprevented Scrap Accumulation:** Without proactive pre-run risk scoring, upcoming batches continue running on drifting equipment, repeatedly incurring low yields.

---

## Why Existing Solutions Fall Short

- **Disconnected Data Silos:** Current fab diagnostics separate tabular sensor telemetry, spatial wafer die inspection images, and equipment maintenance logs into isolated software tools.
- **Manual Single-Parameter SPC:** Traditional Statistical Process Control (SPC) triggers alerts only when single parameters breach static boundaries, missing non-linear multi-variable sensor drift and interactions.
- **Post-Facto Reaction:** Conventional systems alert engineers only *after* post-fabrication electrical test failures occur, rather than predicting lot failure risks *before* batches enter process chambers.
