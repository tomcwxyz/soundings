"""Unit tests for the system prompt builder."""

from soundings.ask.prompts import SystemPromptBuilder


def test_prompt_contains_general_guidance():
    prompt = SystemPromptBuilder().build()
    assert "Soundings" in prompt
    assert "tool" in prompt.lower()
    assert "compose_answer" in prompt


def test_prompt_has_intent_inference_guidance():
    """The prompt teaches the model to infer intent from the question text."""
    prompt = SystemPromptBuilder().build()
    assert "Infer the user's intent" in prompt
    assert "Summary questions" in prompt
    assert "Compare questions" in prompt
    assert "Insight questions" in prompt


def test_prompt_mentions_compare_guidance():
    prompt = SystemPromptBuilder().build()
    assert "compare" in prompt.lower()
    assert "percentile" in prompt.lower()


def test_prompt_mentions_insight_guidance():
    prompt = SystemPromptBuilder().build()
    assert "detect_insights" in prompt
    assert "insight-callout" in prompt


def test_pinned_place_included_in_prompt():
    builder = SystemPromptBuilder(place_name="Stockton-on-Tees", place_id="ltla24:E06000004")
    prompt = builder.build()
    assert "Stockton-on-Tees" in prompt
    assert "ltla24:E06000004" in prompt


def test_scope_guardrail_present():
    prompt = SystemPromptBuilder().build()
    assert "population" in prompt
    assert "health" in prompt
    assert "cannot help" in prompt.lower() or "out of scope" in prompt.lower()


def test_prompt_mentions_distribution_chart():
    prompt = SystemPromptBuilder().build()
    assert "distribution-chart" in prompt


def test_prompt_mentions_composition_chart():
    prompt = SystemPromptBuilder().build()
    assert "composition-chart" in prompt


def test_prompt_mentions_scatter_plot():
    prompt = SystemPromptBuilder().build()
    assert "scatter-plot" in prompt


def test_prompt_mentions_get_peer_distribution():
    prompt = SystemPromptBuilder().build()
    assert "get_peer_distribution" in prompt


def test_prompt_has_chart_selection_guidance():
    prompt = SystemPromptBuilder().build()
    assert "Use distribution-chart" in prompt
    assert "Use composition-chart" in prompt
    assert "Use scatter-plot" in prompt


def test_prompt_mentions_map_overlay():
    prompt = SystemPromptBuilder().build()
    assert "overlay" in prompt.lower()
    assert "air_quality" in prompt
    assert "organisations" in prompt
    assert "amenities" in prompt


def test_prompt_mentions_environment_domain():
    prompt = SystemPromptBuilder().build()
    assert "environment" in prompt.lower()


def test_prompt_mentions_air_quality():
    prompt = SystemPromptBuilder().build()
    assert "air quality" in prompt.lower()


def test_prompt_notes_air_quality_is_centroid_sampled_model_data():
    prompt = SystemPromptBuilder().build()
    assert "modelled" in prompt.lower()
    assert "centroid" in prompt.lower()
    assert "local exposure may vary" in prompt.lower()


def test_prompt_mentions_infrastructure_domain():
    prompt = SystemPromptBuilder().build()
    assert "infrastructure" in prompt.lower()


def test_prompt_mentions_osm_amenity_counts():
    prompt = SystemPromptBuilder().build()
    assert "amenity" in prompt.lower() or "amenities" in prompt.lower()
    assert "openstreetmap" in prompt.lower() or "osm" in prompt.lower()


def test_prompt_routes_facility_questions_to_amenity_counts():
    # A "food banks / schools" question must use the infrastructure.*_count
    # OSM amenity indicators, not a silent charity-register fallback.
    prompt = SystemPromptBuilder().build()
    assert "food bank" in prompt.lower()
    assert "infrastructure" in prompt.lower()
    # The model is told not to silently substitute a charity search.
    assert "find_organisations_in_place" in prompt
    assert "explicit" in prompt.lower() or "say so" in prompt.lower()


def test_prompt_teaches_three_map_modes():
    prompt = SystemPromptBuilder().build()
    assert "granularity" in prompt
    assert "sub_areas" in prompt
    # points overlay for facility locations
    assert "indicator_keys" in prompt
    assert "where are" in prompt.lower()


def test_prompt_teaches_neighbourhood_resolution():
    """Prompt should explain that 'neighbourhood' means LSOA level."""
    prompt = SystemPromptBuilder().build()
    assert "neighbourhood" in prompt.lower() or "neighborhood" in prompt.lower()
    assert "lsoa" in prompt.lower()
    assert "ward" in prompt.lower()


def test_prompt_explains_geography_levels():
    """Prompt should explain that indicators vary by geography level."""
    prompt = SystemPromptBuilder().build()
    assert "available_at" in prompt or "geography level" in prompt.lower()
    assert "ltla" in prompt.lower()
    assert "lsoa" in prompt.lower()


def test_prompt_teaches_geography_types_filter():
    """Prompt should explain that find_place accepts geography_types."""
    prompt = SystemPromptBuilder().build()
    assert "geography_types" in prompt
    assert "lsoa21" in prompt


def test_prompt_mentions_get_sub_areas():
    prompt = SystemPromptBuilder().build()
    assert "get_sub_areas" in prompt
    assert "neighbourhood" in prompt.lower()


def test_prompt_teaches_sub_areas_for_neighbourhood_questions():
    prompt = SystemPromptBuilder().build()
    assert "most deprived neighbourhoods" in prompt.lower()
    assert "sub-area" in prompt.lower() or "sub area" in prompt.lower()


def test_prompt_teaches_neighbourhood_comparison():
    prompt = SystemPromptBuilder().build()
    assert "context_place_ids" in prompt
    assert "neighbourhood" in prompt.lower()


def test_prompt_mentions_sub_area_table():
    prompt = SystemPromptBuilder().build()
    assert "sub-area-table" in prompt


def test_prompt_mentions_ward_data_availability():
    """Ward-level data should be mentioned as available for a subset."""
    prompt = SystemPromptBuilder().build()
    assert "ward" in prompt.lower()
    assert "subset" in prompt.lower() or "limited" in prompt.lower()
