from pathlib import Path

from .native_command import NativeCommandProfile


PAPERAGENT_PROJECT_PROFILE = NativeCommandProfile(
    profile_id="paperagent-native-gradio-v1",
    command_template=(
        "{python}",
        "-c",
        (
            "from paper_agent.gui import demo; "
            "demo.launch(server_name='127.0.0.1', server_port=int('{port}'), "
            "inbrowser=False, share=False, show_error=True)"
        ),
    ),
    required_source_files=("paper_agent/gui.py", "pyproject.toml"),
    environment_templates={
        "USERPROFILE": "{state_path}/user-profile",
        "HOME": "{state_path}/user-profile",
        "GRADIO_ANALYTICS_ENABLED": "False",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
    },
    cleared_secret_environment=(
        "CODEX_API_KEY",
        "CODEX_BASE_URL",
        "CODEX_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DEEPSEEK_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "YDC_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ),
    timeout_seconds=90,
)

PAPERAGENT_READINESS_PATH = "/config"
PAPERAGENT_CLIENT_SCRIPT = Path(__file__).with_name("paperagent_client.py")
