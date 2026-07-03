# Massive Flat Files Environment Variables

MoneyBot should use environment variables for Massive flat-file access. Do **not** commit the Access Key ID or Secret Access Key to the repository, logs, docs, or test fixtures.

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

Then use an S3-compatible client with the custom endpoint, for example:

```bash
aws s3 sync s3://flatfiles/us_stocks_sip/day_aggs_v1 data/massive_flatfiles/us_stocks_sip/day_aggs_v1 \
  --endpoint-url https://files.massive.com
```

Keep downloaded vendor files out of git. Use the flat feature store materializer for derived offline datasets and manifests.
