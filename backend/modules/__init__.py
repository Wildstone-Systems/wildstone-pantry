"""Feature modules.

Importing this package imports each module's manifest so it registers itself
with the kernel registry. The app factory then calls ``mount_modules``.

We import the ``manifest`` submodule (not the package) on purpose: the package
__init__ files are side-effect free, so importing a module's ``services`` never
drags in the API/security layer (avoids import cycles).
"""

from modules.agent import manifest as _agent_manifest  # noqa: F401
from modules.automation import manifest as _automation_manifest  # noqa: F401
from modules.catalog import manifest as _catalog_manifest  # noqa: F401
from modules.inventory import manifest as _inventory_manifest  # noqa: F401
from modules.planning import manifest as _planning_manifest  # noqa: F401
from modules.shopping import manifest as _shopping_manifest  # noqa: F401
from modules.wildstone import manifest as _wildstone_manifest  # noqa: F401
