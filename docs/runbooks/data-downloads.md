# Dataset downloads on the GX10

Raw data root: `~/data/oncovision/raw/<dataset>/` (never committed). Checked 2026-10-11.

| Dataset | Source | Licence | Size | How |
| --- | --- | --- | --- | --- |
| LUMIERE | figshare collection 5904905 | CC0 | 32.5 GB zip + 5 small CSVs | `curl -C -` against `ndownloader.figshare.com/files/38249697` (imaging) |
| MU-Glioma-Post | TCIA Faspex package 1030 | CC BY 4.0 | 11.6 GB, 2978 files | `fetch_tcia_faspex.py` (below) |
| UCSD-PTGBM | TCIA Faspex packages 1019 (33 GB) and 1221 (BraTS-2024 test part, 12 GB) | CC BY 4.0 | segmentations + T1post + FLAIR only from 1019 (about 2 GB); package 1221 in full | `fetch_tcia_faspex.py` with a file list |
| UCSF-LPTDG | imagingdatasets.ucsf.edu/dataset/2 | non-commercial + DUA | n/a | **Not downloaded**: needs a personal login and acceptance of the agreement |

Clinical sheets are plain HTTPS from `cancerimagingarchive.net/wp-content/uploads/` (MU: `MU-Glioma-Post_ClinicalData-July2025.xlsx`;
UCSD: `UCSD_PTGBM-clinical-information_v3_2026-12-Mar.xlsx`).

## TCIA Faspex without a browser plugin

TCIA serves its newer collections from a Faspex 5 server over Aspera FASP; the HTTP gateway is disabled, so the
`ascp` binary is required. The `ascli` route is unreliable (HTTP 500), so `fetch_tcia_faspex.py` talks to the
public-link API directly:

1. Decode the `context=` value from the Download link on the collection page (base64 JSON with the package id and passcode).
2. `GET /aspera/faspex/auth/authorize_public_link?...&state=<base64 context>` redirects with a one-time `code`.
3. `POST /aspera/faspex/auth/token` returns a bearer token; `POST /aspera/faspex/api/v5/packages/<id>/transfer_spec/download?transfer_type=connect&type=received` returns the FASP transfer spec (token, cookie, host).
4. Run `ascp` with `ASPERA_SCP_TOKEN` / `ASPERA_SCP_COOKIE` set.

aarch64 `ascp` (no root needed): IBM Aspera Transfer SDK, URL listed in
`github.com/IBM/aspera-cli/blob/main/docs/sdk_location.yaml` (we used `1.1.7/ibm-aspera-transfer-sdk-linux-aarch64-1.1.7.tar.gz`),
unpacked to `~/tools/aspera-sdk/`. The token-auth SSH key is the well-known bypass key shipped with `aspera-cli`
(`lib/aspera/data/2`, an RSA key in DER; convert with `openssl rsa -inform DER`) saved as `~/tools/aspera-sdk/bypass_rsa.pem`.

```bash
python scripts/gx10/fetch_tcia_faspex.py <context_b64> <dest_dir>                  # whole package, resumable
python scripts/gx10/fetch_tcia_faspex.py <context_b64> <dest_dir> wanted.txt       # only the listed files
```

Re-running resumes (`-k 2`). Files still in flight carry a `.aspx` sidecar; ignore those.
Missing names in a file list are reported as failed files and do not stop the transfer.
