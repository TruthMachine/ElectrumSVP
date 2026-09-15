import os
import shutil
import logging
from bitcoinx import Headers, Bitcoin, header_hash
from bitcoinx.errors import MissingHeader

from .constants import PRELOADED_HEADERS


logger = logging.getLogger(__name__)


class HeaderNetworkCheckFailed(Exception):
    """Raised when the network header check cannot be completed."""


class HeaderChainMismatch(Exception):
    """Raised when the local header does not match the network header."""

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


    def _validate_headers_file(self):
        with open(self.file_path, "rb") as f:
            raw = f.read()

        if len(raw) % 80 != 0:
            raise ValueError("Headers file size is not a multiple of 80")

        if len(raw) < 80:
            raise ValueError("Headers file contains no complete headers")

        # The first header must be the Bitcoin genesis header.
        genesis_header = raw[:80]

        if genesis_header != self.headers.network.genesis_header:
            raise ValueError(
                "Headers file does not begin with the expected genesis header"
            )

        seen = set()

        for height in range(len(raw) // 80):
            header = raw[height * 80:height * 80 + 80]
            header_hash_value = header_hash(header)

            if header_hash_value in seen:
                raise ValueError(
                    f"Duplicate header at height {height}: "
                    f"{header_hash_value.hex()}"
                )

            seen.add(header_hash_value)

            if height > 0:
                previous = raw[(height - 1) * 80:height * 80]
                expected_prev = header_hash(previous)
                actual_prev = header[4:36]

                if actual_prev != expected_prev:
                    raise ValueError(
                        f"Header chain break at height {height}"
                    )

    def _validate_tip_against_network(self):
        """
        Compare the local tip header against the canonical header
        reported by ElectrumX.

        Raises HeaderChainMismatch if the network confirms that the
        local tip is on a different chain.

        Raises HeaderNetworkCheckFailed if the network check cannot
        be completed.
        """
        # Local import avoids introducing a possible import cycle
        # during wallet startup.
        from .verification_utils import electrum_request

        file_size = os.path.getsize(self.file_path)

        if file_size < 80:
            raise ValueError("Headers file is empty or too small")

        tip_height = (file_size // 80) - 1

        with open(self.file_path, "rb") as f:
            f.seek(tip_height * 80)
            local_header = f.read(80)

        if len(local_header) != 80:
            raise ValueError(
                f"Could not read local tip header at height {tip_height}"
            )

        local_hash = header_hash(local_header)

        logger.info(
            "Checking local header tip %d against ElectrumX",
            tip_height,
        )

        # --------------------------------------------------------------
        # Network request
        #
        # Only failures communicating with ElectrumX belong in
        # HeaderNetworkCheckFailed.
        # --------------------------------------------------------------
        try:
            response = electrum_request(
                "blockchain.block.header",
                [tip_height],
            )
        except Exception as e:
            raise HeaderNetworkCheckFailed(
                f"Could not request header {tip_height} from ElectrumX: {e}"
            ) from e

        # --------------------------------------------------------------
        # Validate ElectrumX response
        # --------------------------------------------------------------
        server_header_hex = response.get("result")

        if not server_header_hex:
            raise HeaderNetworkCheckFailed(
                f"ElectrumX returned no header for height {tip_height}"
            )

        try:
            server_header = bytes.fromhex(server_header_hex)
        except ValueError as e:
            raise HeaderNetworkCheckFailed(
                f"ElectrumX returned invalid hexadecimal data for "
                f"height {tip_height}"
            ) from e

        if len(server_header) != 80:
            raise HeaderNetworkCheckFailed(
                f"ElectrumX returned an invalid header length for "
                f"height {tip_height}: {len(server_header)} bytes"
            )

        server_hash = header_hash(server_header)

        # --------------------------------------------------------------
        # Compare the two headers
        #
        # This is NOT a network failure.
        #
        # ElectrumX successfully answered us and told us that the
        # local chain is different.
        # --------------------------------------------------------------
        if local_hash != server_hash:
            raise HeaderChainMismatch(
                f"Local header chain diverges from network at "
                f"height {tip_height}. "
                f"Local: {local_hash.hex()} "
                f"Network: {server_hash.hex()}"
            )

        logger.info(
            "Local header tip %d matches ElectrumX: %s",
            tip_height,
            local_hash.hex(),
        )



    def _load_or_restore_headers(self, network):
        """Load headers from file, or restore from PRELOADED_HEADERS if missing/corrupt."""
        if not os.path.exists(self.file_path):
            logger.info(
                "No headers file found, copying preloaded headers to %s",
                self.file_path,
            )
            shutil.copyfile(PRELOADED_HEADERS, self.file_path)

        try:
            # ----------------------------------------------------------
            # Validate the persisted header file before giving it to
            # BitcoinX.
            #
            # This catches:
            #   - truncated headers
            #   - duplicate headers
            #   - headers inserted at the wrong height
            #   - broken prev_hash links
            # ----------------------------------------------------------
            self._validate_headers_file()

            logger.info("Headers file integrity check passed")

            try:
                self._validate_tip_against_network()

            except HeaderNetworkCheckFailed as e:
                # A temporary network/DNS failure must NOT cause us
                # to throw away an otherwise valid local headers file.
                logger.warning(
                    "Skipping network header validation: %s",
                    e,
                )

            except HeaderChainMismatch as e:
                # ElectrumX successfully confirmed that our local tip
                # does not belong to the canonical chain.
                logger.error(
                    "Header chain mismatch detected: %s",
                    e,
                )
                raise


            with open(self.file_path, "rb") as f:
                raw = f.read()

            self.cursor = self.headers.connect_many(raw)

        except MissingHeader as e:
            logger.warning(
                "Headers file missing blocks: %s",
                str(e),
            )
            self._restore_from_preloaded(network)

        except Exception as e:
            logger.exception(
                "Headers file failed integrity/load check: %s",
                e,
            )
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

        try:
            if os.path.exists(old_backup):
                i = 1
                while os.path.exists(f"{old_backup}.{i}"):
                    i += 1
                old_backup = f"{old_backup}.{i}"

            os.rename(self.file_path, old_backup)
            logger.info("Renamed old headers file to %s", old_backup)

        except Exception as e:
            logger.warning(
                "Could not backup corrupt headers file: %s",
                e,
            )

        # Always restore the known-good headers.
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

