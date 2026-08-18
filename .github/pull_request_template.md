## What this changes

<!-- One or two sentences. The why, not a restatement of the diff. -->

## How it was tested

<!-- Which tests you added and what they pin down. If the change touches device behavior,
     say whether it was run on real hardware and on what — unit tests prove logic, not
     topology, and the project does not claim device behavior it has not run. -->

```console
$ pytest -m "not device"
```

## Checklist

- [ ] The failing test came first, and it failed for the reason I expected
- [ ] `pytest -m "not device"` is green
- [ ] Actions still go through `runtime.run_action` — no new path around the funnel
- [ ] Only `adb_backend.py` calls `adb`; any new shell-out is behind an injected `runner=`
- [ ] State files go through `state.read_json` / `state.write_json`, not raw read/write
- [ ] Docs updated if behavior changed, and no unverified device claim was added
- [ ] Conventional-commit subject, one logical change
