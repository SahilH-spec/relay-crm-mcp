import pytest

from relay.config import ConfigError, normalize_domain


def test_adds_https():
    assert normalize_domain("acme-dev-ed.develop.my.salesforce.com") == (
        "https://acme-dev-ed.develop.my.salesforce.com"
    )


def test_strips_trailing_slash():
    assert normalize_domain("https://acme.my.salesforce.com/") == "https://acme.my.salesforce.com"


def test_rejects_lightning_url():
    with pytest.raises(ConfigError):
        normalize_domain("https://acme.lightning.force.com")


def test_rejects_other_domains():
    with pytest.raises(ConfigError):
        normalize_domain("https://example.com")
