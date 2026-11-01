# Report tooling patches

The five daily market reports are written outside this repo, in
`vps2:~/neuralstrike-reports`, which has no git. A change to them that has to land
with a change here is kept in this folder as a patch against those files.

## `report_tooling_volume.patch` (2026-11-01)

Adds traded volume to the reports. Design:
[`docs/plans/2026-11-01-volume-in-read-and-reports-design.md`](../../docs/plans/2026-11-01-volume-in-read-and-reports-design.md).

| File | Change |
|---|---|
| `tools/remote_collect.py` | The snapshot gains `volume`, from `cache:market:volume` |
| `tools/render.py` | A `volume` block. It has no text of its own: it prints the sentences in this slot's snapshot, word for word, or says they are not available |
| `tools/plain_english.py` | `volume_notes`: lists a report with no volume block, and a volume figure outside it. A listing only |
| `tools/REPORT_GUIDE.md` | The snapshot's new view, the reading rule (volume says how much, not which way), a Volume section in every report, the block |

It does not change the prompts (each tells the run to follow the guide) or `social.py` (it
finds its sections by eyebrow, and `Volume` is not one of them).

Back up and apply it on the box, from a checkout that has this folder:

```bash
ssh vps2 'cd ~/neuralstrike-reports && for f in REPORT_GUIDE.md remote_collect.py render.py plain_english.py; do cp tools/$f tools/$f.bak-20261101-volume; done && patch -p1 < /home/administrator/dev/deploy/report_tooling/report_tooling_volume.patch'
```

Undo it by copying the `.bak-20261101-volume` files back.

The order against the promote does not matter. Until the market service publishes the
view, the block says the figures are not available for this report. Apply it outside the
report times (07:44, 08:45, 09:48, 11:36 and 15:10 CT): a run reads the guide once, at
its start.
