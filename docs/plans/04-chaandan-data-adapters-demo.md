# Chaandan: dataset adapters, QC, demo

You own the path from raw files to clean tables and masks. The old pipeline handled 20 UCSF patients with UCSF-specific paths. Yours must run unattended over three datasets on the GX10. "Train with more data" depends on this more than on anything else. You also own the demo script and submission, because you will know best what the data can and cannot support.

You do not need the GX10. Develop against the legacy files already in QBIHack, the clinical spreadsheets, and a handful of sample patients Praneeth sends you.

Read [the overview](00-overview.md) and [the data contract](../contracts/data-contract.md), section 1, first.

**Paths you own:** `src/oncovision/imaging/**`, `tests/imaging/**`, `docs/demo/**`, `docs/DATASETS.md`, `README.md`.

## Step 1. Migrate (by G0)

From QBIHack `4a77efc`, `brain-cancer-sim/simulation-vinesh-philip-chandan/`:

| Old | New |
| --- | --- |
| `philip-chandan/nifti_extractor.py`, `export_raw_extract.py`, `export_all_raw.py` | `imaging/nifti.py`, `imaging/ingest.py` |
| `philip-chandan/cohort/{datasets,cohort_discovery,cohort_io}.py` | `imaging/datasets.py`, `imaging/discovery.py` |
| `philip-chandan/wt_volume_report.py`, `generate_manifest.py` | `imaging/volumes.py` |
| `vinesh/prepare_pde_input.py` | `imaging/pde_input.py` |
| `batch_job_state.py` | `imaging/job_state.py` |
| `models-praneeth/clean_ucsf.py` | `imaging/adapters/ucsf_lptdg.py` (clinical part) |
| `philip-chandan/qc_slice_plot.py`, `qc_pde_plot.py` | `imaging/qc.py` |
| their tests and fixtures | `tests/imaging/` |

Leave behind: napari viewers, the PDF report generator, `spike_paths.py`, `handoff_contract.py`, and everything under `breast-cancer-sim/`. Replace `sys.path.insert` with package imports and hard-coded directories with arguments.

Also move `brain-cancer-sim/DATASETS.md` to `docs/DATASETS.md`.

## Step 2. One adapter interface (by G1)

```python
class DatasetAdapter(Protocol):
    key: str
    def discover(self, raw_dir) -> list[PatientRef]: ...          # patients and their timepoints, ordered by date
    def load_labels(self, ref, tp) -> tuple[np.ndarray, spacing]: ...  # harmonised labels 0-4, (Z,Y,X)
    def load_clinical(self, raw_dir) -> pd.DataFrame: ...          # contract columns
    LABEL_MAP: dict[int, int]                                      # dataset value -> harmonised value, with its source cited
```

`ingest.py` is dataset-agnostic: discover, load, harmonise, resample to 1 mm, crop, compute volumes, write masks and PDE cubes, append table rows.

Fix the two defects from the overview here:

1. **Cavity is not tumour.** `tumor_mm3` and the PDE seed use harmonised labels 1-3 only. Label 4 is kept in the mask and reported as `rc_mm3`, with `has_cavity` in `qc_flags`. This removes the reason 5 of the 20 legacy patients were excluded.
2. **One crop box per patient.** Compute the union bounding box of tumour labels over all of a patient's timepoints, pad it, and use it for every timepoint. First check that the dataset's timepoints are in the same space (same shape and affine); if they are not, flag `not_coregistered` and exclude that patient from Dice, but still report volumes.

Make it robust: skip finished patients, log a failed patient to `ingest_errors.jsonl` and continue, run patients in a process pool (`--workers`), never load more than one patient per worker.

Tests: legacy masks for the 14 patients without cavity reproduce `wt_volume_report.json` to within 1%; a patient with a cavity gets a smaller `tumor_mm3` than the old pipeline's burden; two timepoints of one patient share shape and `crop_origin`; a corrupt file produces an error row and does not stop the run.

## Step 3. UCSF-LPTDG adapter (by G1)

Port the existing logic. The clinical workbook cleaning is already written (`clean_ucsf.py`); map its columns to the contract (`idh` `WT`/`mut` → `wt`/`mut`, `mgmt` positive/negative → methylated/unmethylated, `1p19q` codel → bool, `eor_at_1st_sx`, `on_tx_at_1st_scan_...`, `deceased` from `days_from_death_to_1st_scan` being present). The old master table names labels 1 NCR, 2 SNFH, 3 ET, 4 RC, which already matches the harmonised values; confirm against a real file.

Validate against the committed `brain-cancer-sim/data/processed/ucsf_longitudinal_master.csv`: your volumes for all 298 patients should match its `*_t1` and `*_t2` columns. That table is a ready-made answer key.

## Step 4. MU-Glioma-Post adapter (by G2)

Start from the clinical spreadsheet, which downloads over plain HTTPS without the imaging (`download_mu_glioma_post.py --metadata-only` in the old repo; Praneeth will send it). You can write and test the whole clinical mapping before any imaging arrives.

- Find and record: patient count, timepoints per patient, how scan dates or intervals are given, which molecular fields exist, how survival and censoring are encoded.
- Label values: read them from the dataset documentation and confirm with `np.unique` on a real mask. Do not assume they match UCSF.
- Many patients have three or more timepoints. Make sure ordering and `day` are right; Praneeth's third-timepoint forecast test depends on it.

## Step 5. LUMIERE adapter (by G3, if the download lands)

Same procedure. Things to check rather than assume: it ships more than one automatic segmentation per scan with different label schemes, so pick one, cite it, and map it; time is given in weeks from surgery; some scans lack some sequences. Glioblastoma only, so IDH and grade vary little. If its masks have no oedema/enhancing split that maps cleanly, fill the regions you can, leave the others null, and set a flag.

If time is short, MU-Glioma-Post is worth more than LUMIERE: expert-validated masks and multiple timepoints.

## Step 6. QC for scale (by G2)

Nobody will look at 300+ patients by eye. Produce `data/processed/qc_report.json` and a one-page `qc_summary.md`:

- per dataset: patients found, ingested, failed (with reasons), timepoints, count with cavity, count not co-registered;
- distributions: baseline `tumor_mm3`, interval days, growth percentage, by IDH status;
- outliers: follow-up volume more than 10× or less than 0.1× baseline, empty tumour at baseline, interval ≤ 0;
- a contact sheet PNG of mid-slices with mask overlay for 24 random patients per dataset.

Send the summary to the team after each full run on the GX10.

## Step 7. Demo and submission (from G2 onward)

Files in `docs/demo/`:

1. **`script.md`**: three minutes, timed. Suggested arc: the problem (follow-up MRI is weeks apart; what will this tumour do?) → pick a patient from the full cohort → risk and drivers → play the 3D forecast, show predicted vs observed on a held-out patient → what-if on the GPU live → local narrative → model card with the before/after data counts → "everything you saw ran on this box, offline".
2. **`claims.md`**: every number said aloud or shown on a slide, with the file it came from. If a number has no file, it is not said. Get the measured timings from Vinesh and Praneeth, and the metrics from Praneeth.
3. **`limitations.md`**: postoperative scans confounded by treatment; mortality risk is not growth; two-timepoint calibration is a fit, not a prediction; what-if drug factors are illustrative; research demo only.
4. **Fallbacks**: what to do if the LLM is slow (baked narratives), if the API dies (static mode), if a patient fails to load. Rehearse each once.
5. Backup screen recording at G4.

Also yours: keep `README.md` and `docs/DATASETS.md` true (sources, licences, counts as actually ingested), the submission form, and the ASUS feedback survey. Do not post anything public or submit without the team's go-ahead.

## Gates

| Gate | Your evidence |
| --- | --- |
| G0 Sat 20:30 | Code migrated, old tests pass under the new layout. Label-map and clinical-column mapping for UCSF written down. |
| G1 Sat 23:30 | `ingest` produces contract-valid output for the 20 legacy patients, with the cavity and shared-crop fixes and their tests. |
| G2 Sun 03:00 | Full UCSF run on the GX10 matches the master table. MU-Glioma-Post clinical mapping done; imaging adapter tested on sample patients. QC report generated. |
| G3 Sun 07:00 | MU-Glioma-Post fully ingested; LUMIERE if available. Demo script draft with real numbers. |
| G4 Sun 10:00 | `claims.md` complete and checked against files. Backup recording made. |
| G5 Sun 12:30 | Two timed rehearsals. Survey and submission done. |

## Done means

- [ ] One `ingest` command per dataset, resumable, with an error log.
- [ ] `clinical.parquet` and `timepoints.parquet` match the contract for every dataset ingested.
- [ ] Label maps cited; anything unverified is flagged, not guessed silently.
- [ ] Volumes validated against the UCSF master table.
- [ ] QC summary for the final run.
- [ ] Demo script, claims list and limitations written; every claim traceable to a file.
