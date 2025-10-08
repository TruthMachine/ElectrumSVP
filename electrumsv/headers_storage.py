import os
import shutil
import logging
from bitcoinx import Headers, Bitcoin
from bitcoinx.errors import MissingHeader

from .constants import PRELOADED_HEADERS


logger = logging.getLogger(__name__)


class PersistentHeaders:
    """
    Wrapper around bitcoinx.Headers with persistent storage and
    automatic fallback to PRELOADED_HEADERS on corruption or missing file.
    """

    def __init__(self, network=Bitcoin, file_path=None):
        self.headers = Headers(network)
        self.file_path = file_path
        self.cursor = {}

        if not file_path:
            # In-memory headers only
            self.headers.connect(network.genesis_header)
            self.cursor = self.headers.cursor()
            return

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Load headers file or restore from PRELOADED_HEADERS
        self._load_or_restore_headers(network)

    def _load_or_restore_headers(self, network):
        """Load headers from file, or restore from PRELOADED_HEADERS if missing/corrupt."""
        if not os.path.exists(self.file_path):
            logger.info("No headers file found, copying preloaded headers to %s", self.file_path)
            shutil.copyfile(PRELOADED_HEADERS, self.file_path)

        try:
            with open(self.file_path, "rb") as f:
                raw = f.read()
            self.cursor = self.headers.connect_many(raw)
        except MissingHeader as e:
            logger.warning("Headers file missing blocks: %s", str(e))
            self._restore_from_preloaded(network)
        except Exception as e:
            logger.exception(f"Failed to load headers from {self.file_path}: {e}")
            self._backup_and_restore(network)

    def _restore_from_preloaded(self, network):
        """Overwrite headers file with PRELOADED_HEADERS and reload."""
        shutil.copyfile(PRELOADED_HEADERS, self.file_path)
        with open(self.file_path, "rb") as f:
            raw = f.read()
        self.headers = Headers(network)
        self.cursor = self.headers.connect_many(raw)
        logger.info("Restored headers from preloaded file %s", PRELOADED_HEADERS)

    def _backup_and_restore(self, network):
        """Backup corrupt headers file and restore preloaded headers."""
        old_backup = self.file_path + ".old"
        if os.path.exists(self.file_path):
            os.rename(self.file_path, old_backup)
            logger.info("Renamed old headers file to %s", old_backup)
        self._restore_from_preloaded(network)

    def add_header(self, raw_header, auto_flush=True):
        """Add a single header, falling back to preloaded headers on failure."""
        try:
            self.headers.connect(raw_header)
            self.cursor = self.headers.cursor()
            if auto_flush:
                self.flush()
        except Exception as e:
            logger.error(f"Failed to connect single header: {e}")
            self._backup_and_restore(self.headers.network)

    def add_headers(self, raw_headers, auto_flush=True):
        """Add multiple headers, falling back to preloaded headers on failure."""
        try:
            self.headers.connect_many(raw_headers)
            self.cursor = self.headers.cursor()
            if auto_flush:
                self.flush()
        except Exception as e:
            logger.error(f"Failed to connect batch of headers: {e}")
            self._backup_and_restore(self.headers.network)

    def unpersisted_headers(self):
        """Return headers that have not yet been flushed to disk."""
        return self.headers.unpersisted_headers(self.cursor)

    def flush(self):
        """Append unpersisted headers to the file."""
        if not self.file_path:
            return
        try:
            unpersisted = self.unpersisted_headers()
            if not unpersisted:
                return
            with open(self.file_path, 'ab') as f:
                f.write(unpersisted)
            self.cursor = self.headers.cursor()
        except Exception as e:
            logger.exception(f"Failed to flush headers: {e}")

    @property
    def tip(self):
        chains = self.headers.chains()
        return max(chains, key=lambda c: c.height) if chains else None

    def __getattr__(self, name):
        """Delegate unknown attributes to the underlying Headers object."""
        return getattr(self.headers, name)

