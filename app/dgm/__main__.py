from .startup_admin import preflight
preflight()
from .runtime import forward_managed
forward_managed()
from .cli import main

main()

