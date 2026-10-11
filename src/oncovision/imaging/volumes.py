"""Region volumes per scan, with scan days and treatment schedules from the clinical sheets.

    python -m oncovision.imaging.volumes --dataset mu_glioma_post \\
        --raw ~/data/oncovision/raw/mu_glioma_post --out ~/data/oncovision/processed

Writes ``<out>/<dataset>/timepoints.parquet`` (one row per scan) and
``<out>/<dataset>/treatments.json`` (one schedule per patient, days relative to the
patient's first scan).  Label maps are listed once per dataset below with their
source; they were also confirmed with ``np.unique`` on real masks.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import nibabel as nib
import numpy as np
import pandas as pd

# harmonised region names for the integer labels in each dataset's tumour mask
LABEL_MAPS = {
    # MU-Glioma-Post: Label1 necrotic core, Label2 infiltration/oedema, Label3 enhancing, Label4 resection cavity
    # (TCIA collection page / Scientific Data 2025 paper; values 1-4 seen in real masks)
    "mu_glioma_post": {1: "ncr", 2: "edema", 3: "et", 4: "rc"},
    # UCSD-PTGBM BraTS 2024 post-treatment scheme: 1 NETC, 2 SNFH, 3 ET, 4 RC (values 1-4 seen in real masks)
    "ucsd_ptgbm": {1: "ncr", 2: "edema", 3: "et", 4: "rc"},
}
REGIONS = ["ncr", "edema", "et", "rc"]


@dataclass
class Scan:
    dataset: str
    patient_id: str
    tp_key: int                  # the dataset's own timepoint number
    mask_path: str
    extra: dict = field(default_factory=dict)

    @property
    def patient_uid(self) -> str:
        return f"{self.dataset}:{self.patient_id}"


def discover(dataset: str, raw: str | Path) -> list[Scan]:
    raw = str(raw)
    out: list[Scan] = []
    if dataset == "mu_glioma_post":
        for p in glob.glob(os.path.join(raw, "imaging", "PKG*", "MU-Glioma-Post", "PatientID_*", "Timepoint_*",
                                        "*_tumorMask.nii.gz")):
            m = re.search(r"(PatientID_\d+)_Timepoint_(\d+)_tumorMask", p)
            if m and not os.path.exists(p + ".aspx"):
                out.append(Scan(dataset, m.group(1), int(m.group(2)), p))
    elif dataset == "ucsd_ptgbm":
        for p in glob.glob(os.path.join(raw, "imaging", "UCSD-PTGBM", "UCSD-PTGBM-*", "*_BraTS_tumor_seg.nii.gz")):
            m = re.search(r"UCSD-PTGBM-(\d+)_(\d+)_BraTS_tumor_seg", p)
            if m and not os.path.exists(p + ".aspx"):
                d = os.path.dirname(p)
                cell = glob.glob(os.path.join(d, "*_total_cellular_tumor_seg.nii.gz"))
                ok = cell and not os.path.exists(cell[0] + ".aspx")
                out.append(Scan(dataset, m.group(1), int(m.group(2)), p,
                                {"cellular_path": cell[0] if ok else None}))
    else:
        raise ValueError(f"no adapter for {dataset}")
    return sorted(out, key=lambda s: (s.patient_id, s.tp_key))


def _bbox(mask: np.ndarray):
    idx = np.nonzero(mask)
    if len(idx[0]) == 0:
        return None
    return [int(i.min()) for i in idx] + [int(i.max()) for i in idx]


def scan_volumes(scan: Scan) -> dict:
    img = nib.load(scan.mask_path)
    a = np.asarray(img.dataobj)
    zooms = img.header.get_zooms()[:3]
    vox = float(np.prod(zooms))
    lm = LABEL_MAPS[scan.dataset]
    row = {"patient_uid": scan.patient_uid, "dataset": scan.dataset, "patient_id": scan.patient_id,
           "tp_key": scan.tp_key, "mask_path": scan.mask_path, "shape": list(a.shape),
           "spacing": [float(z) for z in zooms]}
    unknown = set(np.unique(a).astype(int).tolist()) - set(lm) - {0}
    for lab, name in lm.items():
        row[f"{name}_mm3"] = float((a == lab).sum() * vox)
    row["tumor_mm3"] = row["ncr_mm3"] + row["edema_mm3"] + row["et_mm3"]     # cavity excluded
    row["core_mm3"] = row["ncr_mm3"] + row["et_mm3"]
    bb = _bbox(np.isin(a, [1, 2, 3]))
    row["bbox"] = bb
    flags = []
    if row["tumor_mm3"] == 0:
        flags.append("empty_tumor")
    if row["rc_mm3"] > 0:
        flags.append("has_cavity")
    if unknown:
        flags.append("unknown_labels:" + ",".join(map(str, sorted(unknown))))
    row["cellular_mm3"] = None
    cp = scan.extra.get("cellular_path")
    if cp:
        c = np.asarray(nib.load(cp).dataobj)
        row["cellular_mm3"] = float((c > 0).sum() * vox)
    row["qc_flags"] = ",".join(flags) or "ok"
    return row


def build(dataset: str, raw: str | Path, clinical_xlsx: str | Path, workers: int = 16) -> tuple[pd.DataFrame, dict]:
    from ..treatment import clinical as C

    if dataset == "mu_glioma_post":
        pt = C.mu_patient_treatments(C.load_mu_clinical(clinical_xlsx))
    elif dataset == "ucsd_ptgbm":
        pt = C.ucsd_patient_treatments(C.load_ucsd_clinical(clinical_xlsx))
    else:
        raise ValueError(dataset)
    scans = discover(dataset, raw)
    with ProcessPoolExecutor(workers) as ex:
        rows = list(ex.map(scan_volumes, scans, chunksize=4))
    df = pd.DataFrame(rows)
    days, tp_index = [], []
    for _, r in df.iterrows():
        p = pt.get(r.patient_id)
        days.append(p.scan_days.get(int(r.tp_key)) if p else None)
    df["day"] = days
    df = df[df.day.notna()].copy()
    df = df.sort_values(["patient_uid", "day"])
    df["tp_index"] = df.groupby("patient_uid").cumcount()
    # re-zero days so tp_index 0 is day 0 for the scans we actually have
    df["day"] = df.groupby("patient_uid")["day"].transform(lambda s: s - s.min())
    shift = {}
    for uid, g in df.groupby("patient_uid"):
        pid = g.patient_id.iloc[0]
        first_key = int(g.sort_values("day").tp_key.iloc[0])
        shift[pid] = pt[pid].scan_days[first_key]
    sched = {}
    for pid, off in shift.items():
        p = pt[pid]
        s = p.schedule
        from ..treatment.schedule import DrugCourse, RadiationCourse, Schedule
        sh = Schedule(
            [DrugCourse(c.agent, c.start_day - off, c.end_day - off, c.cycle_days, c.on_days, c.intensity)
             for c in s.drugs],
            [RadiationCourse(r.start_day - off, r.end_day - off, r.total_dose_gy, r.fractions) for r in s.radiation])
        sched[f"{dataset}:{pid}"] = {"schedule": sh.to_dict(), "assumptions": p.assumptions,
                                     "surgery_days": [d - off for d in p.surgery_days],
                                     "multiple_surgeries": p.multiple_surgeries}
    return df.reset_index(drop=True), sched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--clinical", required=True, help="clinical xlsx")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args(argv)
    df, sched = build(a.dataset, a.raw, a.clinical, a.workers)
    out = Path(a.out) / a.dataset
    out.mkdir(parents=True, exist_ok=True)
    d2 = df.copy()
    for c in ("shape", "spacing", "bbox"):
        d2[c] = d2[c].apply(lambda v: json.dumps(v))
    d2.to_parquet(out / "timepoints.parquet", index=False)
    (out / "treatments.json").write_text(json.dumps(sched))
    n_pat = df.patient_uid.nunique()
    multi = (df.groupby("patient_uid").size() >= 2).sum()
    print(f"{a.dataset}: {len(df)} scans, {n_pat} patients, {multi} with >=2 scans -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
