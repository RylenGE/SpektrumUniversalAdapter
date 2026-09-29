import importlib.util
import traceback
from pathlib import Path


class PluginLoader:
    def __init__(self, plugins_dir, api):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.api = api
        self.loaded = {}
        self.errors = {}

    def load_all(self):
        self.loaded.clear()
        self.errors.clear()

        for path in sorted(self.plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue

            name = path.stem
            try:
                spec = importlib.util.spec_from_file_location(f"spektrum_addon_{name}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                setup = getattr(module, "setup", None)
                if not callable(setup):
                    raise RuntimeError("Addon must define setup(api)")

                result = setup(self.api)
                self.loaded[name] = {
                    "module": module,
                    "result": result,
                    "path": str(path),
                }
            except Exception:
                self.errors[name] = traceback.format_exc()

        return {
            "loaded": list(self.loaded),
            "errors": dict(self.errors),
        }
