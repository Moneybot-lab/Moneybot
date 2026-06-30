from __future__ import annotations

import subprocess
import sys


def test_importing_service_module_does_not_import_app_factory():
    code = "import sys; import moneybot.services.market_data_providers; print('moneybot.app_factory' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)

    assert result.stdout.strip() == "False"


def test_package_create_app_lazy_export_still_works():
    code = "from moneybot import create_app; print(callable(create_app))"
    result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)

    assert result.stdout.strip() == "True"
