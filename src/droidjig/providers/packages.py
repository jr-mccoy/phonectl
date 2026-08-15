"""PackageProvider — list, resolve, launch, stop, and clear apps via the best provider."""
from __future__ import annotations

from droidjig import errors, results, runtime as rt


class PackageProvider:
    def __init__(self, registry) -> None:
        self._registry = registry

    def _prov(self, cap: str):
        return self._registry.for_capability(cap)

    def list_packages(self, include_system: bool = False) -> dict:
        p = self._prov("packages_list")
        if p is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_list not available"),
                capability="packages.list",
            )
        try:
            pkgs = p.packages_list(include_system=include_system)
            return results.ok(
                capability="packages.list",
                provider=type(p).__name__,
                data={"packages": pkgs},
            )
        except Exception as e:
            return results.err((errors.ObserveError.code, str(e)), capability="packages.list")

    def resolve(self, package: str) -> dict:
        p = self._prov("packages_list")
        if p is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_list not available"),
                capability="packages.resolve",
            )
        try:
            info = p.packages_resolve(package)
            return results.ok(capability="packages.resolve", provider=type(p).__name__, data=info)
        except Exception as e:
            return results.err((errors.ObserveError.code, str(e)), capability="packages.resolve")

    def launch(self, package: str, *, build, yes=False, cfg=None) -> dict:
        return rt.run_action(
            "launch",
            lambda backend, session: (backend.launch(package), {})[1],
            package,
            build=build, yes=yes, cfg=cfg,
        )

    def stop(self, package: str, *, build, yes=False, cfg=None) -> dict:
        if self._prov("packages_stop") is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_stop not available"),
                capability="packages.stop",
            )
        return rt.run_action(
            "packages_stop",
            lambda backend, session: (backend.packages_stop(package), {})[1],
            package,
            build=build, yes=yes, cfg=cfg,
        )

    def clear(self, package: str, *, build, yes=False, cfg=None) -> dict:
        if self._prov("packages_clear") is None:
            return results.err(
                errors.CapabilityUnavailableError("packages_clear not available"),
                capability="packages.clear",
            )
        return rt.run_action(
            "packages_clear",
            lambda backend, session: (backend.packages_clear(package), {})[1],
            package,
            build=build, yes=yes, cfg=cfg,
        )
