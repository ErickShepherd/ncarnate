"""Produce the canonical gzip header required by publication archive inspection."""

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def finalize(self, version, build_data, artifact_path):
        # Hatchling has already finished the deterministic tar/gzip stream.
        # A zero gzip timestamp means unspecified; member timestamps retain
        # Hatchling's reproducible values. With no optional gzip header fields,
        # changing MTIME does not alter the payload or its CRC/length trailer.
        with open(artifact_path, "r+b") as archive:
            header = archive.read(10)
            if (len(header) != 10 or header[:4] != b"\x1f\x8b\x08\x00"
                    or header[8] not in (0, 2, 4)):
                raise ValueError("Unsupported source-archive gzip header")
            archive.seek(4)
            archive.write(b"\x00" * 4)
