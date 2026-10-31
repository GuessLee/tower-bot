# Planning link
Issue: #<n>. Use "Closes #<n>" only if merging this PR finishes the issue; otherwise "Refs #<n>" and say what is left.
Planning: <parent tracking issue>, or "none - bounded maintenance". Required. If this PR adds or changes scope, it updates that record too.

# Changes
<What changed and why, in a few lines.>

# Operational impact
Deploy or config changes, rollback, data, permissions, cost. "None" when nothing changes outside the code.

# Delivery boundary
Merging this PR makes it: implemented and tested at the head commit, in the environment named under Verification.
Still to verify at the target (the running bot): <checks>, recorded in <issue>. Or: "nothing; merging delivers it".
Follow-ups: <links> or none.

## Verification
Commands run and results, environment, what was not tested.

# Documentation
Updated: <files>, or "none needed because ...".
