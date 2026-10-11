from heatshield_risk.config import RiskThresholds
from heatshield_risk.engine import assess_location
from heatshield_risk.models import FloodInputs, GlacialLakeInfo, HeatInputs, LocationInfo, RiskLevel


LOC = LocationInfo(id="test", name="Test", state="Test", lat=0.0, lng=0.0)


def test_normal_flood_low():
    inputs = FloodInputs(
        location_id="test",
        rainfall_intensity_mm_hr=2.0,
        cumulative_rainfall_mm_24h=10.0,
        river_level_m=1.0,
        river_threshold_m=5.0,
        glacial_lake=GlacialLakeInfo(available=False),
        historical_flood_events_5y=0,
        population_exposure_index=0.2,
        infrastructure_exposure_index=0.2,
    )
    result = assess_location(LOC, "flood_glof", inputs, None)
    assert result.risk_level == RiskLevel.LOW


def test_heavy_rain_high_river_high():
    inputs = FloodInputs(
        location_id="test",
        rainfall_intensity_mm_hr=45.0,
        cumulative_rainfall_mm_24h=180.0,
        river_level_m=5.2,
        river_threshold_m=4.5,
        glacial_lake=GlacialLakeInfo(available=True, status="watch", lake_id="demo-lake"),
        historical_flood_events_5y=2,
        population_exposure_index=0.7,
        infrastructure_exposure_index=0.6,
    )
    result = assess_location(LOC, "flood_glof", inputs, None)
    assert result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert any(f.id == "rainfall" for f in result.contributing_factors)
    assert any(f.id == "river" for f in result.contributing_factors)


def test_missing_glacier_no_glof_factor():
    inputs = FloodInputs(
        location_id="test",
        rainfall_intensity_mm_hr=45.0,
        cumulative_rainfall_mm_24h=180.0,
        river_level_m=5.2,
        river_threshold_m=4.5,
        glacial_lake=GlacialLakeInfo(available=False),
        historical_flood_events_5y=2,
        population_exposure_index=0.7,
        infrastructure_exposure_index=0.6,
    )
    result = assess_location(LOC, "flood_glof", inputs, None)
    assert not any(f.id == "glacial_lake" for f in result.contributing_factors)
    assert "glacial_lake_score" not in result.component_scores


def test_extreme_heat_high():
    inputs = HeatInputs(
        location_id="test",
        temperature_c=44.0,
        humidity_pct=55.0,
        heat_index_c=None,
        elevated_temp_duration_hours=14.0,
        population_exposure_index=0.75,
        infrastructure_exposure_index=0.5,
    )
    result = assess_location(LOC, "extreme_heat", None, inputs)
    assert result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_invalid_river_threshold_no_crash():
    inputs = FloodInputs(
        location_id="test",
        rainfall_intensity_mm_hr=5.0,
        cumulative_rainfall_mm_24h=20.0,
        river_level_m=3.0,
        river_threshold_m=0.0,
        glacial_lake=GlacialLakeInfo(available=False),
        historical_flood_events_5y=0,
        population_exposure_index=0.3,
        infrastructure_exposure_index=0.3,
    )
    result = assess_location(LOC, "flood_glof", inputs, None)
    assert result.risk_score >= 0


def test_all_risk_level_boundaries_transition_correctly():
    thresholds = RiskThresholds.load()
    scores = [0, 24, 25, 49, 50, 74, 75, 100]
    levels = [thresholds.score_to_level(score) for score in scores]
    assert levels == ["low", "low", "moderate", "moderate", "high", "high", "critical", "critical"]


def test_missing_flood_inputs_do_not_create_exposure_or_glof_scores():
    inputs = FloodInputs(
        location_id="test",
        rainfall_intensity_mm_hr=None,
        cumulative_rainfall_mm_24h=None,
        river_level_m=None,
        river_threshold_m=None,
        glacial_lake=GlacialLakeInfo(available=False),
        historical_flood_events_5y=None,
        population_exposure_index=None,
        infrastructure_exposure_index=None,
    )
    result = assess_location(LOC, "flood_glof", inputs, None)
    assert result.risk_score == 0
    assert result.component_scores == {
        "rainfall_score": 0.0,
        "river_score": 0.0,
        "exposure_score": 0.0,
        "historical_score": 0.0,
    }
    assert any(entry.kind == "unavailable" for entry in result.observations)


def test_missing_heat_inputs_are_unavailable_not_inferred():
    inputs = HeatInputs(
        location_id="test",
        temperature_c=None,
        humidity_pct=None,
        heat_index_c=None,
        elevated_temp_duration_hours=None,
        population_exposure_index=None,
        infrastructure_exposure_index=None,
    )
    result = assess_location(LOC, "extreme_heat", None, inputs)
    assert result.risk_score == 0
    assert all(score == 0 for score in result.component_scores.values())
    assert all(entry.kind == "unavailable" for entry in result.observations)
