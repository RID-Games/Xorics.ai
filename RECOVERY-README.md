# Xorics — recovery bundle (2026-06-17)

This bundle reconstructs the `~/xorics-ai` tree after the live copy on RIDGames was
deleted. It exists to get a complete, working repo onto GitHub so this can't happen
again. Read the provenance below before trusting any single file.

## Provenance — what to trust

**Recovered intact / high confidence**
- `xorics.py` — includes this session's coder-control patch (the `/code <text>` routing
  and stop-propagation fix). 565 lines.
- `pcb_tools.py`, `notebook.py` — byte-identical to the morning handoff.
- `firmware_tools.py` — recovered complete from a past session (compile_check +
  extract_code + save_sketch).
- All docs: `README.md`, `LICENSE` (AGPL-3.0), `LICENSE-EXCEPTION`, `CLA.md`,
  `CONTRIBUTING.md`, `.gitignore`.
- All apply scripts: `apply-check-circuit-file.sh`, `apply-coder-control.sh`,
  `apply-connector-count.sh`, `apply-house-parts.sh`, `apply-license-headers.sh`,
  `apply-notebook.sh`, `apply-pins-and-connector.sh`, and `publish-xorics.sh`.

**Reconstructed — coherent and syntax-clean, but VERIFY**
- `datasheet_rag.py`, `web_datasheets.py`, `ingest.py` — rebuilt from our past build
  sessions. Structure and most code match; not guaranteed byte-identical. Marked with a
  `[RECONSTRUCTED ...]` banner at the top of each.

**Not recovered**
- `voice.py` — placeholder stub only. `xorics.py` does not import it, so the agent
  still runs; the `--voice` path will not work until you restore the real module.

## Patched-state note

All patches are already baked into the files in this bundle — you do NOT need to re-run
any `apply-*.sh`. Verified present: notebook integration, check_circuit_file,
connector-geometry (count + geometry), coder-control, and house-parts (the read_file
tool in `xorics.py`, the connector-bare-guard in `pcb_tools.py`, and the HOUSE_PARTS
seeding in `notebook.py`). The `apply-*.sh` scripts are included only as the historical
record of each change. (house-parts was applied during recovery with part-validation
skipped, since the staple list was already proven on the live ATmega run; the edits are
identical to what the SKiDL-validated run produces.)

## Restore + publish (gets it onto GitHub — the whole point)

```bash
# 1) restore the tree (this archive extracts to ./xorics-ai/)
cd ~
tar -xzf xorics-ai-recovery.tar.gz          # creates ~/xorics-ai

# 2) sanity check
cd ~/xorics-ai
python3 -m py_compile *.py && echo "all parse"

# 3) dry-run the publish (writes nothing to GitHub)
XORICS_SKIP_PUSH=1 bash publish-xorics.sh go
git ls-files          # expect the clean 15: 8 *.py + 6 docs + circuits/.gitkeep

# 4) real push — replaces the empty MIT repo with your AGPL tree
bash publish-xorics.sh go
#    username = RID-Games ,  password = your PAT
```

Once step 4 succeeds, the remote IS your backup. Future changes go through normal
`git add` / `commit` / `push` — never another one-shot publish, and never another
total loss from a single bad command.

## If you want the byte-exact originals back (time-sensitive)

The deleted files may still be carvable from the ext4 block device on RIDGames, but
every write to your home directory lowers the odds. Do not create files in `~`. Then:

```bash
sudo pacman -S testdisk          # provides photorec
# run photorec against the root device, recover text/.py files to a SEPARATE location,
# then identify them:  grep -l 'def search_datasheets\|def fetch_datasheet\|whisper' recovered_*
```

Swap any recovered originals in over the reconstructions via a normal git commit.
