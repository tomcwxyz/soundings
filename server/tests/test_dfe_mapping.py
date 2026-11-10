from soundings.adapters.dfe_explore.mapping import DEFAULT_MAPPING_PATH, load_dfe_mapping


def test_default_mapping_file_exists() -> None:
    assert DEFAULT_MAPPING_PATH.exists()


def test_mapping_loads_and_covers_catalogue_dfe_indicators() -> None:
    mappings = load_dfe_mapping()
    keys = {m.indicator_key for m in mappings}
    assert "education.fsm_eligibility_share" in keys
    assert "education.ks4_attainment_8" in keys
    assert "education.persistent_absence_share" in keys


def test_fsm_entry_carries_dataset_and_indicator_ids() -> None:
    mappings = load_dfe_mapping()
    by_key = {m.indicator_key: m for m in mappings}
    fsm = by_key["education.fsm_eligibility_share"]
    # Verified dataset UUID + indicator ID from DfE EES API (Jul 2026).
    assert fsm.data_set_id == "019e7404-df19-71ce-90a8-f2e3db7dd7fa"
    assert fsm.indicator_id == "empgu"  # Percentage of pupils
    assert fsm.filter_selection  # non-empty — post-filter selects FSM eligible + All pupils + Total
    assert fsm.location_level == "LA"
    assert fsm.time_period_code == "AY"
    assert fsm.place_type == "ltla24"
    assert fsm.unit == "proportion"


def test_percentage_indicators_are_scaled_to_the_proportion_unit() -> None:
    """DfE publishes these as percentages (Bradford FSM arrives as 32.x).

    The catalogue unit is "proportion", so without a scale the values break
    the indicator's measure contract; the 9 November 2026 evaluation found
    them served as proportions of 21-32.
    """
    by_key = {m.indicator_key: m for m in load_dfe_mapping()}
    assert by_key["education.fsm_eligibility_share"].value_scale == 0.01
    assert by_key["education.persistent_absence_share"].value_scale == 0.01
    assert by_key["education.ks4_attainment_8"].value_scale is None


def test_materialised_values_apply_the_scale() -> None:
    from soundings.adapters.dfe_explore.adapter import _materialise_points
    from soundings.adapters.dfe_explore.mapping import DfeMapping

    mapping = DfeMapping(
        indicator_key="education.fsm_eligibility_share",
        data_set_id="d",
        indicator_id="empgu",
        location_level="LA",
        place_type="ltla24",
        unit="proportion",
        value_scale=0.01,
    )
    payload = {"results": [{"timePeriod": {"period": "2024/2025"}, "values": {"empgu": "32.4"}}]}

    points = _materialise_points(payload, mapping)

    assert points == [{"period": "2024/2025", "value": 0.324}]
