# Add or change a signal

Which signals count changes from business to business. Paste this into Claude Code, inside this repo.

```
I want to add a signal to the client score: REPLACE_ME

1. Ask me what counts as firing, and how many points it takes off.
2. If it is a number, code works it out. If it is about the words, the AI reads it and copies a line that proves it. Code throws the line away if it is not in the messages.
3. Add it to rules.json, health.py and test_health.py, with a test that fires and a test that does not.
4. Tell me the column to add to my sheet and how to change the Score and Why formulas.
5. Run python3 -m unittest -q and show me it passes.
```
