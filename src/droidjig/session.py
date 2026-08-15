from droidjig import ui_parser, errors

class Session:
    def __init__(self):
        self.last = None
        # The primary element index of the most recent selector resolution. Threaded into the
        # daemon run-record as target["matched_i"] so the selector-library can learn
        # selector -> index (macro/memory.capture_selector). Sticky across observes (the
        # post-action re-observe does not resolve selectors), refreshed on every find().
        self.last_match = None

    def set_snapshot(self, snap: dict) -> None:
        self.last = snap

    def resolve(self, i: int) -> tuple[int, int]:
        if self.last is None:
            raise KeyError("no snapshot; call observe() first")
        for e in self.last["elements"]:
            if e["i"] == i:
                return (e["center"][0], e["center"][1])
        raise KeyError(f"no element with index {i}")

    def find(self, selector: dict) -> list[int]:
        if self.last is None:
            raise KeyError("no snapshot; call observe() first")
        matches = ui_parser.match_selector(self.last["elements"], selector,
                                           relations=self.last.get("relations"))
        self.last_match = matches[0] if matches else None
        return matches

    def resolve_selector(self, selector: dict) -> tuple[int, int]:
        matches = self.find(selector)
        if not matches:
            raise errors.StaleSnapshotError(
                f"selector {selector} matched nothing in the current snapshot")
        return self.resolve(matches[0])
