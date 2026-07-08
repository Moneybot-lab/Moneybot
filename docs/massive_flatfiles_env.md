# Massive Flat Files Environment Variables

MoneyBot uses environment variables for Massive flat-file access. Do **not** commit the Access Key ID or Secret Access Key to the repository, logs, docs, manifests, or test fixtures.

## Required variables

| Variable | Value |
| --- | --- |
| `MASSIVE_FLATFILES_ACCESS_KEY_ID` | Your Massive flat-files Access Key ID. |
| `MASSIVE_FLATFILES_SECRET_ACCESS_KEY` | Your Massive flat-files Secret Access Key. |
| `MASSIVE_FLATFILES_ENDPOINT` | `https://files.massive.com` |
| `MASSIVE_FLATFILES_BUCKET` | `flatfiles` |

`MASSIVE_FLATFILES_ENDPOINT` and `MASSIVE_FLATFILES_BUCKET` default to the Massive values above in local helper tooling, but production/deployment environments should still set them explicitly for auditability.

## Local validation

After setting the variables in your shell or deployment secret manager, run:

```bash
python scripts/check_massive_flatfiles_env.py
```

The command only reports whether credentials are configured; it does not print secrets. It also emits a sample AWS CLI sync command using the configured endpoint and bucket.

## Example local setup

```bash
export MASSIVE_FLATFILES_ACCESS_KEY_ID="..."
export MASSIVE_FLATFILES_SECRET_ACCESS_KEY="..."
export MASSIVE_FLATFILES_ENDPOINT="https://files.massive.com"
export MASSIVE_FLATFILES_BUCKET="flatfiles"
```

## Raw historical ingest

Use the ingest helper to copy Massive flat files into immutable dated folders under `data/raw/massive_flatfiles`:

```bash
python scripts/ingest_massive_flatfiles.py \
  --prefix us_stocks_sip/day_aggs_v1 \
  --dataset-date 2026-07-03
```

The helper:

- reads Massive credentials only from `MASSIVE_FLATFILES_*` environment variables;
- maps credentials into `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` only for the child `aws s3 sync` process;
- never writes secret values into commands or manifests;
- writes to `data/raw/massive_flatfiles/<YYYY-MM-DD>/<safe-prefix>/`;
- refuses to run if the destination folder already contains files, preserving raw vendor data as immutable historical snapshots;
- writes `_INGEST_MANIFEST.json` beside the raw files for lineage and auditability.

For a non-network safety check, add `--dry-run`; this creates the dated manifest without running `aws s3 sync`.

Keep downloaded vendor files out of git. Use the flat feature store materializer for derived offline datasets and manifests.
