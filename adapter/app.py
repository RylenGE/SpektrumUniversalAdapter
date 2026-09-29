import json
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from .api import AdapterAPI
from .core import AdapterCore
from .http_api import LocalAPIServer
from .plugin_loader import PluginLoader
from .ui import UniversalUI


def runtime_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def main():
    root_dir = runtime_root()

    core = AdapterCore(root_dir)
    api = AdapterAPI(core)

    plugin_loader = PluginLoader(root_dir / "plugins", api)
    plugin_loader.load_all()

    http_server = LocalAPIServer(api, port=8765)
    try:
        http_server.start()
    except OSError:
        # Another copy may already own the port. The desktop app should still work.
        pass

    root = tk.Tk()

    style = ttk.Style()
    try:
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass

    UniversalUI(root, core, api, plugin_loader, http_server)
    root.mainloop()
