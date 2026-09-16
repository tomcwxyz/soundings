"""Unit tests for GrantNav flat-CSV normalisation."""

from soundings.grants.import_grantnav_csv import _to_api_shape


def test_standard_grantnav_headers_map_to_api_shape() -> None:
    raw = _to_api_shape(
        {
            "Identifier": "360G-Test-1",
            "Title": "A useful grant",
            "Description": "Support for refugee housing",
            "Currency": "GBP",
            "Amount Awarded": "25000",
            "Award Date": "2026-01-15",
            "Funding Org:Identifier": "GB-CHC-999999",
            "Funding Org:Name": "Example Foundation",
            "Recipient Org:Identifier": "GB-CHC-123456",
            "Recipient Org:Name": "Example Charity",
            "Grant Programme:Title": "Communities",
        }
    )

    assert raw is not None
    data = raw["data"]
    assert data["id"] == "360G-Test-1"
    assert data["amountAwarded"] == "25000"
    assert data["fundingOrganization"] == [{"id": "GB-CHC-999999", "name": "Example Foundation"}]
    assert data["recipientOrganization"] == [{"id": "GB-CHC-123456", "name": "Example Charity"}]
    assert data["grantProgramme"] == "Communities"


def test_row_without_identifier_is_skipped() -> None:
    assert _to_api_shape({"Title": "No identifier"}) is None
