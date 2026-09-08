"""Download links shown in the UI when something is missing."""

PSMOVESERVICE_URL = "https://github.com/Timocop/PSMoveServiceEx/releases/download/v0.27.8/PSMoveService.zip"
VIRTUAL_DEVICE_MANAGER_URL = (
    "https://github.com/Timocop/PSMoveServiceEx-Virtual-Device-Manager/releases/download/v13.14.8/PSMSVirtualDeviceManager.zip"
)
VIGEMBUS_URL = "https://github.com/nefarius/ViGEmBus/releases"
ANTHROPIC_CONSOLE_URL = "https://console.anthropic.com/"


def link(url: str, text: str) -> str:
    return f'<a href="{url}" style="color:#33e6ff">{text}</a>'


def controller_install_html() -> str:
    """How to get a PS Move paired, with direct download links (rich text)."""
    return (
        "No PS Move found. Pair it once with PSMoveServiceEx: download "
        + link(VIRTUAL_DEVICE_MANAGER_URL, "PSMS Virtual Device Manager")
        + " (installs drivers and pairs over USB) and "
        + link(PSMOVESERVICE_URL, "PSMoveService")
        + ", run the pairing, close PSMoveService, then press the PS button."
    )
