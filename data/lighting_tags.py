"""Vocabulary of Booru Lighting Tagger v2.0.0 (all labels are general tags)."""

MODEL_REPO = "MarioTheKnight/booru-lighting-tagger"
MODEL_VERSION = "2.0.0"
# Immutable revision of v2.0.0, including the reviewed upstream inference code.
MODEL_REVISION = "959c0b836a7d93209ecaad02ffa527c623783675"

LIGHTING_TAGS = frozenset("""
backlighting rim_light sidelighting overlighting underlighting spotlight crack_of_light
high_contrast low_contrast chiaroscuro silhouette dramatic_lighting volumetric_lighting
warm_lighting cool_lighting golden_hour red_light orange_light green_light purple_light
light_particles lens_flare diffraction_spikes caustics refraction subsurface_scattering
overexposure shadow window_shadow dappled_sunlight partially_shaded_face sunlight
moonlight candlelight fire_light screen_light glowing night sunset dark dim_lighting high_key
soft_lighting hard_lighting complex_lighting colored_lights yellow_light blue_light pink_light
light_rays bloom dispersion_(optics) vignetting shade colored_shadow shaded_face
""".split())
