from scripts.check_massive_flatfiles_env import (
    DEFAULT_MASSIVE_FLATFILES_BUCKET,
    DEFAULT_MASSIVE_FLATFILES_ENDPOINT,
    build_aws_cli_command,
    load_massive_flatfiles_env,
)


def test_load_massive_flatfiles_env_uses_safe_defaults_and_redacts_secret_state():
    config = load_massive_flatfiles_env(
        {
            "MASSIVE_FLATFILES_ACCESS_KEY_ID": "abc123",
            "MASSIVE_FLATFILES_SECRET_ACCESS_KEY": "super-secret",
        }
    )

    assert config.ready is True
    assert config.access_key_id_configured is True
    assert config.secret_access_key_configured is True
    assert config.endpoint == DEFAULT_MASSIVE_FLATFILES_ENDPOINT
    assert config.bucket == DEFAULT_MASSIVE_FLATFILES_BUCKET
    assert not hasattr(config, "secret_access_key")


def test_load_massive_flatfiles_env_reports_missing_credentials():
    config = load_massive_flatfiles_env({})

    assert config.ready is False
    assert "MASSIVE_FLATFILES_ACCESS_KEY_ID" in config.missing
    assert "MASSIVE_FLATFILES_SECRET_ACCESS_KEY" in config.missing
    assert config.endpoint == DEFAULT_MASSIVE_FLATFILES_ENDPOINT
    assert config.bucket == DEFAULT_MASSIVE_FLATFILES_BUCKET


def test_build_aws_cli_command_uses_endpoint_bucket_and_prefix():
    config = load_massive_flatfiles_env(
        {
            "MASSIVE_FLATFILES_ACCESS_KEY_ID": "abc123",
            "MASSIVE_FLATFILES_SECRET_ACCESS_KEY": "super-secret",
            "MASSIVE_FLATFILES_ENDPOINT": "https://files.massive.com",
            "MASSIVE_FLATFILES_BUCKET": "flatfiles",
        }
    )

    assert build_aws_cli_command(config, prefix="us_stocks_sip/day_aggs_v1", destination="data/raw") == [
        "aws",
        "s3",
        "sync",
        "s3://flatfiles/us_stocks_sip/day_aggs_v1",
        "data/raw",
        "--endpoint-url",
        "https://files.massive.com",
    ]
