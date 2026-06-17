# Xorics — a self-hosted local AI assistant for embedded / PCB engineering.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics. Xorics is free software: you can redistribute it
# and/or modify it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Xorics is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for details.
#
# You should have received a copy of the GNU Affero General Public License along
# with Xorics. If not, see <https://www.gnu.org/licenses/>.
#
# ADDITIONAL PERMISSION (AGPLv3 section 7): designs and files produced by RUNNING
# Xorics, and any fragments it embeds into that output, are NOT covered by the
# AGPL — you may license your generated designs as you wish. See LICENSE-EXCEPTION.

"""
voice.py - PLACEHOLDER (not recovered).

[The original voice.py was lost on 2026-06-17 and could NOT be recovered from our
 past sessions — I never had its source. This file is a deliberate stub so the repo
 tree is complete and import-clean. It does NOT implement the voice pipeline.

 xorics.py does not import this module at top level, so its absence does not stop the
 agent from running; the --voice path simply will not work until this is restored.

 To restore the real module:
   1) Best:  ext4 undelete on RIDGames (photorec/extundelete) while the blocks survive.
   2) Else:  rebuild the intended pipeline — local Whisper (STT) in, the agent loop in
             the middle, and Kokoro or Piper (TTS) out; wrap the REPL via a --voice flag.

 If you rebuild it, delete this banner.]
"""

from __future__ import annotations


def main():  # pragma: no cover - placeholder
    raise NotImplementedError(
        "voice.py was lost and not recovered. Restore it via undelete on RIDGames, "
        "or rebuild the Whisper-in / Kokoro|Piper-out pipeline. See module docstring."
    )


if __name__ == "__main__":
    main()
