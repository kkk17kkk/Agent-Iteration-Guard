from .native_http import NativeHttpProjectProfile


LIGHTTABLE_PROJECT_PROFILE = NativeHttpProjectProfile(
    profile_id="lighttable-native-http-v1",
    application="backend.main:app",
    readiness_path="/api/v1/status",
    required_source_files=(
        "backend/main.py",
        "backend/services/orchestrator.py",
        "backend/data/recipes.json",
    ),
    environment_templates={
        "LIGHTTABLE_SQLITE_DB_PATH": "{state_path}",
        "LIGHTTABLE_RECIPES_JSON": "{source}/backend/data/recipes.json",
        "LIGHTTABLE_ENABLE_RATE_LIMIT": "0",
    },
    cleared_secret_environment=("OPENROUTER_API_KEY", "MEM0_API_KEY"),
)
