---
description: Tell Home Assistant you're back at the desk (Claude Fleet)
---
The Claude Fleet hook answers this command itself and tells Home Assistant you are
back, so this text should never reach the model. If it does, the hook is not
installed or not running on this computer: reply in one line that Home Assistant
was not told, and suggest running scripts/diagnose.sh from the claude-fleet repo.
