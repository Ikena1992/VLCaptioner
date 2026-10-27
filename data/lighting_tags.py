"""Vocabulary of Booru Lighting Tagger v1.0.2 (all labels are general tags)."""

MODEL_REPO = "MarioTheKnight/booru-lighting-tagger"
# Immutable revision of v1.0.2, including the reviewed upstream inference code.
MODEL_REVISION = "8dd39a788e82654ae3e591218d743ef5ea8ad4a1"

LIGHTING_TAGS = frozenset("""
backlighting rim_light sidelighting overlighting underlighting spotlight crack_of_light
high_contrast low_contrast chiaroscuro silhouette dramatic_lighting volumetric_lighting
warm_lighting cool_lighting golden_hour red_light orange_light green_light purple_light
light_particles lens_flare diffraction_spikes caustics refraction subsurface_scattering
overexposure shadow window_shadow dappled_sunlight partially_shaded_face sunlight
moonlight candlelight fire_light screen_light glowing night sunset dark dim_lighting high_key
""".split())
