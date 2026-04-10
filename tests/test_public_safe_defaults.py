from config import DEFAULT_CORS_ORIGINS, Settings


def test_settings_default_log_level_is_info():
    assert Settings.model_fields["LOG_LEVEL"].default == "INFO"


def test_settings_default_cors_origins_are_localhost_only():
    assert Settings.model_fields["CORS_ORIGINS"].default == DEFAULT_CORS_ORIGINS
    assert "*" not in DEFAULT_CORS_ORIGINS
