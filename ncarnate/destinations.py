"""One collision policy shared by manifest and prepared library batches."""
import os

from ncarnate.errors import NcarnateError


class DestinationCollisionError(NcarnateError):
    """The entire batch is refused before any conversion writes."""


def validate_destinations(entries, *, allow_existing=False, problems=()):
    """Check (source, destination-or-None, label) descriptors, without writing.

    None represents an intentional in-place rewrite of that source. Paths are
    resolved and case-folded on all platforms, so a portable plan cannot depend
    on two case-equivalent outputs remaining separate.
    """
    entries = list(entries)
    problems = list(problems)
    sources, destinations = {}, {}
    for source, destination, label in entries:
        source = os.path.realpath(source)
        sources.setdefault(os.path.normcase(source), []).append(label)
        if destination is not None:
            resolved = os.path.realpath(destination)
            destinations.setdefault(resolved.casefold(), []).append((label, destination))
    for source, labels in sources.items():
        if len(labels) > 1:
            problems.append(f"duplicate records for source {source}: {', '.join(labels)}")
    folded_sources = {source.casefold() for source in sources}
    for destination, claims in destinations.items():
        if len(claims) > 1:
            problems.append(f"destination {destination} claimed by: "
                            + ", ".join(label for label, _ in claims))
        if destination in folded_sources:
            problems.append(f"destination {destination} aliases selected source")
        for source in folded_sources:
            if destination.startswith(source.rstrip(os.sep) + os.sep):
                problems.append(f"destination {destination} is below source file {source}")
        # Outputs are files, never ancestors of another source or output.
        for other in folded_sources | destinations.keys():
            if other.startswith(destination.rstrip(os.sep) + os.sep):
                problems.append(f"destination {destination} contains another planned path {other}")
        for label, original in claims:
            if os.path.lexists(original):
                if os.path.islink(original) or not os.path.isfile(original):
                    problems.append(f"destination {original} is not a regular unlinked file")
                elif not allow_existing:
                    problems.append(f"destination {original} already exists (source {label})")
                # realpath alone cannot detect hard links to an input.
                elif any(os.path.exists(s) and os.path.samefile(original, s) for s, _, _ in entries):
                    problems.append(f"destination {original} aliases selected source")
    if problems:
        raise DestinationCollisionError(
            "destination preflight refused the entire run (no outputs were written):\n  "
            + "\n  ".join(problems), code="DESTINATION_COLLISION",
        )
