# Modified by HomeMaster: the vendored runtime has no mem0ai distribution metadata.
import importlib.metadata

try:
    __version__ = importlib.metadata.version("mem0ai")
except importlib.metadata.PackageNotFoundError:
    __version__ = "2.0.13"

from mem0.client.main import AsyncMemoryClient, MemoryClient  # noqa
from mem0.memory.main import AsyncMemory, Memory  # noqa
