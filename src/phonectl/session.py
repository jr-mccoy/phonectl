class Session:
    def __init__(self):
        self.last = None

    def set_snapshot(self, snap: dict) -> None:
        self.last = snap

    def resolve(self, i: int) -> tuple[int, int]:
        if self.last is None:
            raise KeyError("no snapshot; call observe() first")
        for e in self.last["elements"]:
            if e["i"] == i:
                return (e["center"][0], e["center"][1])
        raise KeyError(f"no element with index {i}")
